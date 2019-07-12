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

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import netutils
from oslo_utils import strutils

from blazar import context
from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from blazar import exceptions
from blazar.manager import exceptions as manager_ex
from blazar.plugins import base
from blazar.plugins import floatingips as plugin
from blazar import status
from blazar.utils.openstack import neutron
from blazar.utils import plugins as plugins_utils


plugin_opts = [
    cfg.FloatOpt('billrate',
                 default=1.0,
                 help='Bill rate for all floating IPs'),
]

CONF = cfg.CONF
CONF.register_opts(plugin_opts, group=plugin.RESOURCE_TYPE)
LOG = logging.getLogger(__name__)


class FloatingIpPlugin(base.BasePlugin):
    """Plugin for floating IP resource."""

    resource_type = plugin.RESOURCE_TYPE
    title = 'Floating IP Plugin'
    description = 'This plugin creates and assigns floating IPs.'

    def __init__(self):
        super(FloatingIpPlugin, self).__init__()
        self.usage_enforcer = None

    def set_usage_enforcer(self, usage_enforcer):
        self.usage_enforcer = usage_enforcer

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
                            reservation_status, fip_reservation, values,
                            lease, fip_allocations):
        allocs_to_remove = self._allocations_to_remove(
            dates_before, dates_after, fip_allocations, values('amount', 1))

        if (allocs_to_remove and
                reservation_status == status.reservation.ACTIVE):
            raise manager_ex.CantUpdateFloatingIPReservation()

        kept_fips = len(fip_allocations) - len(allocs_to_remove)
        fip_ids_to_add = []
        amount = int(values.get('amount', 1))

        if kept_fips < amount:
            needed_fips = amount - kept_fips
            required_fips = values.get('required_fips', [])
            fip_ids_to_add = self._matching_fips(
                values['floatingip_id'], required_fips, amount,
                dates_after['start_date'], dates_after['end_date'])

            if len(fip_ids_to_add) < needed_fips:
                raise manager_ex.NotEnoughFloatingIPAvailable()

        allocs_to_keep = [
            a for a in fip_allocations if a not in allocs_to_remove]
        allocs_to_add = [{'floatingip_id': f} for f in fip_ids_to_add]
        new_allocations = allocs_to_keep + allocs_to_add

        try:
            self.usage_enforcer.check_usage_against_allocation_post_update(
                values, lease, fip_allocations, new_allocations)
        except manager_ex.RedisConnectionError:
            pass

        for fip_id in fip_ids_to_add:
            LOG.debug('Adding floating ip {} to reservation {}'.format(
                fip_id, reservation_id))
            db_api.fip_allocation_create({
                'floatingip_id': fip_id,
                'reservation_id': reservation_id})

        for allocation in allocs_to_remove:
            LOG.debug('Removing floating ip {} from reservation {}'.format(
                allocation['floatingip_id'], reservation_id))
            db_api.fip_allocation_destroy(allocation['id'])

    def _allocations_to_remove(self, dates_before, dates_after, allocs,
                               amount):
        """Find candidate floating ip allocations to remove."""
        allocs_to_remove = []
        all_fip_ids = [fip['id'] for fip in db_api.floatingip_list()]

        for alloc in allocs:
            if alloc['floatingip_id'] not in all_fip_ids:
                allocs_to_remove.append(alloc)
                continue

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

        # NOTE(priteau): Check if we have enough available SUs for this
        # reservation. This takes into account the su_factor of each allocated
        # network, if present.
        lease = db_api.lease_get(values['lease_id'])
        try:
            self.usage_enforcer.check_usage_against_allocation(
                lease, allocated_floatingip_ids=floatingip_ids)
        except manager_ex.RedisConnectionError:
            pass

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
        fip_allocations = db_api.fip_allocation_get_all_by_values(
            reservation_id=reservation_id)

        if (values['start_date'] >= lease['start_date'] and
                values['end_date'] <= lease['end_date']):
            # Nothing to update
            try:
                self.usage_enforcer.check_usage_against_allocation_post_update(
                    values, lease,
                    fip_allocations,
                    fip_allocations)
            except manager_ex.RedisConnectionError:
                pass

        # Check if we have enough available SUs for update
        try:
            self.usage_enforcer.check_usage_against_allocation_pre_update(
                values, lease, fip_allocations)
        except manager_ex.RedisConnectionError:
            pass

        dates_before = {'start_date': lease['start_date'],
                        'end_date': lease['end_date']}
        dates_after = {'start_date': values['start_date'],
                       'end_date': values['end_date']}
        fip_reservation = db_api.fip_reservation_get(
            reservation['resource_id'])

        self._update_allocations(dates_before, dates_after, reservation_id,
                                 reservation['status'], fip_reservation,
                                 values, lease, fip_allocations)

    def on_start(self, resource_id):
        fip_reservation = db_api.fip_reservation_get(resource_id)
        allocations = db_api.fip_allocation_get_all_by_values(
            reservation_id=fip_reservation['reservation_id'])

        ctx = context.current()
        fip_pool = neutron.FloatingIPPool(fip_reservation['network_id'])
        for alloc in allocations:
            fip = db_api.floatingip_get(alloc['floatingip_id'])
            fip_pool.create_reserved_floatingip(
                fip['subnet_id'], fip['floating_ip_address'],
                ctx.project_id, fip_reservation['reservation_id'])

    def on_end(self, resource_id):
        fip_reservation = db_api.fip_reservation_get(resource_id)
        allocations = db_api.fip_allocation_get_all_by_values(
            reservation_id=fip_reservation['reservation_id'])

        fip_pool = neutron.FloatingIPPool(fip_reservation['network_id'])
        for alloc in allocations:
            fip = db_api.floatingip_get(alloc['floatingip_id'])
            fip_pool.delete_reserved_floatingip(fip['floating_ip_address'])
            db_api.fip_allocation_destroy(alloc['id'])

        reservation = db_api.reservation_get(
            fip_reservation['reservation_id'])
        lease = db_api.lease_get(reservation['lease_id'])
        try:
            self.usage_enforcer.release_encumbered(
                lease, reservation, allocations)
        except manager_ex.RedisConnectionError:
            pass

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
            LOG.info("Floating IP %s in network %s can't be used "
                     "for Blazar's resource.", floatingip_address, network_id)
            raise

        floatingip_values = {
            'floating_network_id': network_id,
            'subnet_id': subnet['id'],
            'floating_ip_address': floatingip_address
        }

        floatingip = db_api.floatingip_create(floatingip_values)

        return floatingip

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
