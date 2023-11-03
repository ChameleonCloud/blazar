# Copyright (c) 2019 NTT.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import datetime
import concurrent.futures

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils.excutils import save_and_reraise_exception
from oslo_utils import netutils
from oslo_utils import strutils

from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from blazar import exceptions
from blazar.manager import exceptions as manager_ex
from blazar.plugins import base, monitor
from blazar.plugins import floatingips as plugin
from blazar import status
from blazar.utils.openstack import neutron
from blazar.utils import plugins as plugins_utils
from blazar.utils.openstack import exceptions

plugin_opts = [
    cfg.BoolOpt('retry_allocation_without_defaults',
                default=False,
                help='Whether an allocation should be retried on failure '
                     'without the default properties'),
]

CONF = cfg.CONF
CONF.register_opts(plugin_opts, group=plugin.RESOURCE_TYPE)
LOG = logging.getLogger(__name__)

QUERY_TYPE_ALLOCATION = 'allocation'
MONITOR_ARGS = {"resource_type": plugin.RESOURCE_TYPE}

class FloatingIpPlugin(base.BasePlugin):
    """Plugin for floating IP resource."""

    resource_type = plugin.RESOURCE_TYPE
    title = 'Floating IP Plugin'
    description = 'This plugin creates and assigns floating IPs.'
    query_options = {
        QUERY_TYPE_ALLOCATION: ['lease_id', 'reservation_id']
    }

    def __init__(self):
        super(FloatingIpPlugin, self).__init__()
        self.monitor = FloatingIpMonitorPlugin(**MONITOR_ARGS)

    def check_params(self, values):
        if 'network_id' not in values:
            raise manager_ex.MissingParameter(param='network_id')

        if 'amount' not in values:
            raise manager_ex.MissingParameter(param='amount')

        if not strutils.is_int_like(values['amount']):
            raise manager_ex.MalformedParameter(param='amount')

        # required_floatingips param is an optional parameter
        fips = values.get('required_floatingips', [])
        if not isinstance(fips, list):
            manager_ex.MalformedParameter(param='required_floatingips')

        for ip in fips:
            if not (netutils.is_valid_ipv4(ip) or netutils.is_valid_ipv6(ip)):
                raise manager_ex.InvalidIPFormat(ip=ip)

    def _update_allocations(self, dates_before, dates_after, reservation_id,
                            reservation_status, fip_reservation, lease,
                            values):
        amount = int(values.get('amount', fip_reservation['amount']))
        fip_allocations = db_api.fip_allocation_get_all_by_values(
            reservation_id=reservation_id)
        allocs_to_remove = self._allocations_to_remove(
            dates_before, dates_after, fip_allocations, amount)

        kept_fips = len(fip_allocations) - len(allocs_to_remove)
        fip_ids_to_add = []

        if kept_fips < amount:
            needed_fips = amount - kept_fips
            required_fips = values.get(
                'required_floatingips',
                fip_reservation['required_floatingips'])
            fip_ids_to_add = self._matching_fips(
                fip_reservation['network_id'], required_fips, needed_fips,
                dates_after['start_date'], dates_after['end_date'])

            if len(fip_ids_to_add) < needed_fips:
                raise manager_ex.NotEnoughFloatingIPAvailable()

        # Create new floating IPs if reservation is active
        created_fips = []
        if reservation_status == status.reservation.ACTIVE:
            fip_pool = neutron.FloatingIPPool(fip_reservation['network_id'])
            for fip_id in fip_ids_to_add:
                try:
                    fip = db_api.floatingip_get(fip_id)
                    LOG.debug(
                        'Creating floating IP {} for reservation {}'.format(
                            fip['floating_ip_address'], reservation_id))
                    fip_pool.create_reserved_floatingip(
                        fip['subnet_id'], fip['floating_ip_address'],
                        lease['project_id'], reservation_id)
                    created_fips.append(fip['floating_ip_address'])
                except Exception as e:
                    for fip_address in created_fips:
                        fip_pool.delete_reserved_floatingip(fip_address)
                    err_msg = 'Failed to create floating IP: {}'.format(str(e))
                    raise manager_ex.NeutronClientError(err_msg)

        for fip_id in fip_ids_to_add:
            LOG.debug('Adding floating IP {} to reservation {}'.format(
                fip_id, reservation_id))
            db_api.fip_allocation_create({
                'floatingip_id': fip_id,
                'reservation_id': reservation_id})
        if allocs_to_remove:
            self.deallocate(fip_reservation, allocs_to_remove)

    def _allocations_to_remove(self, dates_before, dates_after, allocs,
                               amount):
        """Find candidate floating IP allocations to remove."""
        allocs_to_remove = []

        for alloc in allocs:
            is_extension = (
                dates_before['start_date'] > dates_after['start_date'] or
                dates_before['end_date'] < dates_after['end_date'])

            if is_extension:
                reserved_periods = db_utils.get_reserved_periods(
                    alloc['floatingip_id'],
                    dates_after['start_date'],
                    dates_after['end_date'],
                    datetime.timedelta(seconds=1),
                    resource_type='floatingip')

                max_start = max(dates_before['start_date'],
                                dates_after['start_date'])
                min_end = min(dates_before['end_date'],
                              dates_after['end_date'])

                if not (len(reserved_periods) == 0 or
                        (len(reserved_periods) == 1 and
                         reserved_periods[0][0] == max_start and
                         reserved_periods[0][1] == min_end)):
                    allocs_to_remove.append(alloc)
                    continue

        allocs_to_keep = [a for a in allocs if a not in allocs_to_remove]

        if len(allocs_to_keep) > amount:
            allocs_to_remove.extend(
                allocs_to_keep[:(len(allocs_to_keep) - amount)])

        return allocs_to_remove

    def reserve_resource(self, reservation_id, values):
        """Create floating IP reservation."""
        self.check_params(values)

        required_fips = values.get('required_floatingips', [])
        amount = int(values['amount'])

        if len(required_fips) > amount:
            raise manager_ex.TooLongFloatingIPs()

        floatingip_ids = self._matching_fips(values['network_id'],
                                             required_fips,
                                             amount,
                                             values['start_date'],
                                             values['end_date'])

        floatingip_rsrv_values = {
            'reservation_id': reservation_id,
            'network_id': values['network_id'],
            'amount': amount
        }

        fip_reservation = db_api.fip_reservation_create(floatingip_rsrv_values)
        for fip_address in required_fips:
            fip_address_values = {
                'address': fip_address,
                'floatingip_reservation_id': fip_reservation['id']
            }
            db_api.required_fip_create(fip_address_values)
        for fip_id in floatingip_ids:
            db_api.fip_allocation_create({'floatingip_id': fip_id,
                                          'reservation_id': reservation_id})
        return fip_reservation['id']

    def update_reservation(self, reservation_id, values):
        """Update reservation."""
        reservation = db_api.reservation_get(reservation_id)
        lease = db_api.lease_get(reservation['lease_id'])
        dates_before = {'start_date': lease['start_date'],
                        'end_date': lease['end_date']}
        dates_after = {'start_date': values['start_date'],
                       'end_date': values['end_date']}
        fip_reservation = db_api.fip_reservation_get(
            reservation['resource_id'])

        if ('network_id' in values and
                values.get('network_id') != fip_reservation['network_id']):
            raise manager_ex.CantUpdateFloatingIPReservation(
                msg="Updating network_id is not supported")

        required_fips = fip_reservation['required_floatingips']
        if ('required_floatingips' in values and
                values['required_floatingips'] != required_fips and
                values['required_floatingips'] != []):
            raise manager_ex.CantUpdateFloatingIPReservation(
                msg="Updating required_floatingips is not supported except "
                    "with an empty list")

        self._update_allocations(dates_before, dates_after, reservation_id,
                                 reservation['status'], fip_reservation, lease,
                                 values)
        updates = {}
        if 'amount' in values:
            updates['amount'] = values.get('amount')
        if updates:
            db_api.fip_reservation_update(fip_reservation['id'], updates)

        if ('required_floatingips' in values and
                values['required_floatingips'] != required_fips):
            db_api.required_fip_destroy_by_fip_reservation_id(
                fip_reservation['id'])
            for fip_address in values.get('required_floatingips'):
                fip_address_values = {
                    'address': fip_address,
                    'floatingip_reservation_id': fip_reservation['id']
                }
                db_api.required_fip_create(fip_address_values)

    def on_start(self, resource_id, lease=None):
        fip_reservation = db_api.fip_reservation_get(resource_id)
        allocations = db_api.fip_allocation_get_all_by_values(
            reservation_id=fip_reservation['reservation_id'])

        fip_pool = neutron.FloatingIPPool(fip_reservation['network_id'])
        for alloc in allocations:
            fip = db_api.floatingip_get(alloc['floatingip_id'])
            fip_pool.create_reserved_floatingip(
                fip['subnet_id'], fip['floating_ip_address'],
                lease['project_id'], fip_reservation['reservation_id'])

    def on_end(self, resource_id, lease=None):
        fip_reservation = db_api.fip_reservation_get(resource_id)
        allocations = db_api.fip_allocation_get_all_by_values(
            reservation_id=fip_reservation['reservation_id'])

        self.deallocate(fip_reservation, allocations)

    def deallocate(self, fip_reservation, allocations):
        reservation = db_api.reservation_get(fip_reservation["reservation_id"])
        if reservation["status"] == status.reservation.ACTIVE:
            fip_pool = neutron.FloatingIPPool(fip_reservation['network_id'])
            for alloc in allocations:
                fip = db_api.floatingip_get(alloc['floatingip_id'])
                fip_pool.delete_reserved_floatingip(fip['floating_ip_address'])
        for alloc in allocations:
            db_api.fip_allocation_destroy(alloc['id'])

    def allocation_candidates(self, values):
        self.check_params(values)

        required_fips = values.get('required_floatingips', [])
        amount = int(values['amount'])

        if len(required_fips) > amount:
            raise manager_ex.TooLongFloatingIPs()

        return self._matching_fips(values['network_id'], required_fips,
                                   amount, values['start_date'],
                                   values['end_date'])

    def _matching_fips(self, network_id, fip_addresses, amount,
                       start_date, end_date):
        filter_array = []
        start_date_with_margin = start_date - datetime.timedelta(
            minutes=CONF.cleaning_time)
        end_date_with_margin = end_date + datetime.timedelta(
            minutes=CONF.cleaning_time)

        fip_query = ["==", "$floating_network_id", network_id]
        filter_array = plugins_utils.convert_requirements(fip_query)

        fip_ids = []
        not_allocated_fip_ids = []
        allocated_fip_ids = []
        for fip in db_api.reservable_fip_get_all_by_queries(filter_array):
            if not db_api.fip_allocation_get_all_by_values(
                    floatingip_id=fip['id']):
                if fip['floating_ip_address'] in fip_addresses:
                    fip_ids.append(fip['id'])
                else:
                    not_allocated_fip_ids.append(fip['id'])
            elif db_utils.get_free_periods(
                    fip['id'],
                    start_date_with_margin,
                    end_date_with_margin,
                    end_date_with_margin - start_date_with_margin,
                    resource_type='floatingip'
            ) == [
                (start_date_with_margin, end_date_with_margin),
            ]:
                if fip['floating_ip_address'] in fip_addresses:
                    fip_ids.append(fip['id'])
                else:
                    allocated_fip_ids.append(fip['id'])

        if len(fip_ids) != len(fip_addresses):
            raise manager_ex.NotEnoughFloatingIPAvailable()

        fip_ids += not_allocated_fip_ids
        if len(fip_ids) >= amount:
            return fip_ids[:amount]

        fip_ids += allocated_fip_ids
        if len(fip_ids) >= amount:
            return fip_ids[:amount]

        raise manager_ex.NotEnoughFloatingIPAvailable()

    def validate_floatingip_params(self, values):
        marshall_attributes = set(['floating_network_id',
                                   'floating_ip_address'])
        missing_attr = marshall_attributes - set(values.keys())
        if missing_attr:
            raise manager_ex.MissingParameter(param=','.join(missing_attr))

    def create_floatingip(self, values):

        self.validate_floatingip_params(values)

        network_id = values.pop('floating_network_id')
        floatingip_address = values.pop('floating_ip_address')

        pool = neutron.FloatingIPPool(network_id)
        # validate the floating ip address is out of allocation_pools and
        # within its subnet cidr.
        try:
            subnet = pool.fetch_subnet(floatingip_address)
        except exceptions.BlazarException:
            with save_and_reraise_exception():
                LOG.info("Floating IP %s in network %s can't be used "
                         "for Blazar's resource.", floatingip_address,
                         network_id)

        floatingip_values = {
            'floating_network_id': network_id,
            'subnet_id': subnet['id'],
            'floating_ip_address': floatingip_address
        }

        floatingip = db_api.floatingip_create(floatingip_values)

        return floatingip

    def get(self, fip_id):
        return self.get_floatingip(fip_id)

    def get_floatingip(self, fip_id):
        fip = db_api.floatingip_get(fip_id)
        if fip is None:
            raise manager_ex.FloatingIPNotFound(floatingip=fip_id)
        return fip

    def list_floatingip(self):
        fips = db_api.floatingip_list()
        return fips

    def delete_floatingip(self, fip_id):
        fip = db_api.floatingip_get(fip_id)
        if fip is None:
            raise manager_ex.FloatingIPNotFound(floatingip=fip_id)

        allocations = db_api.fip_allocation_get_all_by_values(
            floatingip_id=fip_id)
        if allocations:
            msg = 'Floating IP id %s is allocated by reservations.' % fip_id
            LOG.info(msg)
            raise manager_ex.CantDeleteFloatingIP(floatingip=fip_id, msg=msg)
        try:
            db_api.floatingip_destroy(fip_id)
        except db_ex.BlazarDBException as e:
            raise manager_ex.CantDeleteFloatingIP(floatingip=fip_id,
                                                  msg=str(e))

    def list_allocations(self, query, detail=False):
        fip_id_list = [f['id'] for f in db_api.floatingip_list()]
        options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
        options['detail'] = detail
        fip_allocations = self.query_allocations(fip_id_list, **options)

        return [{"resource_id": fip, "reservations": allocs}
                for fip, allocs in fip_allocations.items()]

    def query_allocations(self, resource_id_list, detail=None, lease_id=None,
                          reservation_id=None):
        return self.query_fip_allocations(resource_id_list, detail=detail,
                                          lease_id=lease_id,
                                          reservation_id=reservation_id)

    def query_fip_allocations(self, fips, detail=None, lease_id=None,
                              reservation_id=None):
        """Return dict of host and its allocations.

        The list element forms
        {
          '-id': [
                       {
                         'lease_id': lease_id,
                         'id': reservation_id
                       },
                     ]
        }.
        """
        start = datetime.datetime.utcnow()
        end = datetime.date.max

        reservations = db_utils.get_reservation_allocations_by_fip_ids(
            fips, start, end, lease_id, reservation_id)
        fip_allocations = {fip: [] for fip in fips}

        for reservation in reservations:
            if not detail:
                del reservation['project_id']
                del reservation['lease_name']
                del reservation['status']

            for fip_id in reservation['floatingip_ids']:
                if fip_id in fip_allocations.keys():
                    fip_allocations[fip_id].append({
                        k: v for k, v in reservation.items()
                        if k != 'floatingip_ids'})

        return fip_allocations


class FloatingIpMonitorPlugin(monitor.GeneralMonitorPlugin, neutron.NeutronClientWrapper):
    """
    Monitors reserved floating IPs which fail to clean up.
    Deletes any floating IPs without an associated active reservation.
    """

    def __new__(cls, *args, **kwargs):
        return super(FloatingIpMonitorPlugin, cls).__new__(cls, *args, **kwargs)

    def filter_allocations(self, reservation, resource_ids):
        return [alloc for alloc
                in reservation["floatingip_allocations"]
                if alloc["floating_ip_id"] in resource_ids]

    def get_reservations_by_resource_ids(self, resource_ids,
                                         interval_begin, interval_end):
        return db_utils.get_reservations_by_floatingip_id(resource_ids,
                                                          interval_begin,
                                                          interval_end)

    def get_unreservable_resourses(self):
        return db_api.unreservable_fip_get_all_by_queries([])

    def get_notification_event_types(self):
        """Get event types of notification messages to handle."""
        return ['service.update']

    def notification_callback(self, event_type, payload):
        return {}

    def set_reservable(self, resource_id, is_reservable):
        fip = db_api.floatingip_get(resource_id)
        if fip is None:
            raise manager_ex.FloatingIPNotFound(floatingip=resource_id)

        try:
            db_api.floatingip_destroy(resource_id)
        except db_ex.BlazarDBException as e:
            raise manager_ex.CantDeleteFloatingIP(floatingip=resource_id,
                                                  msg=str(e))

    def poll_resource_failures(self):
        failed = []
        recovered = []
        fips = db_api.floatingip_list()

        def process_fip(fip):
            fip_address = fip["floating_ip_address"]
            fip_pool = neutron.FloatingIPPool(fip['floating_network_id'])
            # check if the FIP is in neutron subnet allocation pools
            try:
                subnet = fip_pool.fetch_subnet(fip_address)
            except exceptions.NeutronUsesFloatingIP as e:
                LOG.warn(f"Floating ip {fip_address} is in use by subnet pools", exc_info=True)
                raise e
            # if the FIP is not found in any subnet, no need to clean the FIP
            except exceptions.FloatingIPSubnetNotFound as e:
                LOG.warn(f"Floating ip {fip_address} is not found in any subnet", exc_info=True)
                raise e
            # get the floating IP reservation ID from neutron
            try:
                fip_info_from_neutron = fip_pool.show_floatingip(fip["floating_ip_address"])
            except Exception as e:
                LOG.error("Error getting Floating IP from neutron", exc_info=e)
                failed.append(fip["id"])
                raise e
            # get the reservation ID from neutron tags
            if 'tags' in fip_info_from_neutron:
                tags = fip_info_from_neutron['tags']
                try:
                    reservation_tag = next((tags[i + 1] for i, tag in enumerate(tags) if tag == 'blazar'), None)
                    reservation_id_from_neutron = reservation_tag.replace("reservation:")
                except Exception as e:
                    LOG.error("Floating IP does not have 'blazar' tag in neutron", exc_info=e)
                    raise e
                reservation = db_api.reservation_get(reservation_id_from_neutron)
                if reservation["status"] in [status.reservation.DELETED, status.reservation.ERROR]:
                    LOG.warning(
                        f"Found floating IP {fip['id']} stuck in deleted lease. Recovering..."
                    )
                    fip_pool.delete_reserved_floatingip(fip_address)
                    recovered.append(fip["id"])
            else:
                LOG.info(f"{fip_address} does not have reservation tags in neutron")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.map(process_fip, fips)
        return failed, recovered
