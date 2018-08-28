# -*- coding: utf-8 -*-
#
# Author: François Rossigneux <francois.rossigneux@inria.fr>
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
import shlex
import subprocess
from random import shuffle

from novaclient import exceptions as nova_exceptions
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import strutils
import redis

from blazar import exceptions as common_ex
from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_ex
from blazar.plugins import base
from blazar.plugins import oshosts as plugin
from blazar.plugins.oshosts import billrate
from blazar.utils.openstack import nova
from blazar.utils import plugins as plugins_utils
from blazar.utils import trusts
from blazar.utils.openstack import keystone


plugin_opts = [
    cfg.StrOpt('on_end',
               default='on_end',
               deprecated_for_removal=True,
               deprecated_since='0.3.0',
               help='Actions which we will use in the end of the lease'),
    cfg.StrOpt('on_start',
               default='on_start',
               deprecated_for_removal=True,
               deprecated_since='0.3.0',
               help='Actions which we will use at the start of the lease'),
    cfg.StrOpt('blazar_az_prefix',
               default='blazar_',
               deprecated_name='climate_az_prefix',
               help='Prefix for Availability Zones created by Blazar'),
    cfg.StrOpt('before_end',
               default='',
               help='Actions which we will be taken before the end of '
                    'the lease')
]

CONF = cfg.CONF
CONF.register_opts(plugin_opts, group=plugin.RESOURCE_TYPE)
LOG = logging.getLogger(__name__)


before_end_options = ['', 'snapshot', 'default', 'email']
BillingError = common_ex.NotAuthorized


def dt_hours(dt):
    return dt.total_seconds() / 3600.0


def isclose(a, b, rel_tol=1e-09, abs_tol=0.0):
    # math.isclose in Python 3.5+
    return abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)


class PhysicalHostPlugin(base.BasePlugin, nova.NovaClientWrapper):
    """Plugin for physical host resource."""
    resource_type = plugin.RESOURCE_TYPE
    title = 'Physical Host Plugin'
    description = 'This plugin starts and shutdowns the hosts.'
    freepool_name = CONF.nova.aggregate_freepool_name
    pool = None

    def _setup_redis(self, usage_db_host):
        if not usage_db_host:
            raise common_ex.ConfigurationError("usage_db_host must be set")
        return redis.StrictRedis(host=CONF.manager.usage_db_host, port=6379, db=0)

    def _init_usage_values(self, r, project_name):
        try:
            balance = r.hget('balance', project_name)
            if balance is None:
                r.hset('balance', project_name, CONF.manager.usage_default_allocated)

            used = r.hget('used', project_name)
            if used is None:
                used = 0.0
                r.hset('used', project_name, 0.0)

            encumbered = r.hget('encumbered', project_name)
            if encumbered is None:
                r.hset('encumbered', project_name, 0.0)
        except redis.exceptions.ConnectionError:
            LOG.exception("cannot connect to redis host %s", CONF.manager.usage_db_host)

    def __init__(self):
        super(PhysicalHostPlugin, self).__init__(
            username=CONF.os_admin_username,
            password=CONF.os_admin_password,
            user_domain_name=CONF.os_admin_user_domain_name,
            project_name=CONF.os_admin_project_name,
            project_domain_name=CONF.os_admin_user_domain_name)

    def reserve_resource(self, reservation_id, values, usage_enforcement=False, usage_db_host=None, user_name=None, project_name=None):
        """Create reservation."""
        self._check_params(values)

        if usage_enforcement:
            r = self._setup_redis(usage_db_host)
            self._init_usage_values(r, project_name)

            # Check if we have enough available SUs for this reservation
            try:
                balance = float(r.hget('balance', project_name))
                encumbered = float(r.hget('encumbered', project_name))
                start_date = values['start_date']
                end_date = values['end_date']
                duration = end_date - start_date
                hours = (duration.days * 86400 + duration.seconds) / 3600.0
                requested = hours * float(values['max'])
                left = balance - encumbered
                if left - requested < 0:
                    raise common_ex.NotAuthorized(
                        'Reservation for project %s would spend %f SUs, only %f left' % (project_name, requested, left))
            except redis.exceptions.ConnectionError:
                LOG.exception("cannot connect to redis host %s", CONF.manager.usage_db_host)

        host_ids = self._matching_hosts(
            values['hypervisor_properties'],
            values['resource_properties'],
            values['count_range'],
            values['start_date'],
            values['end_date'],
        )
        if not host_ids:
            raise manager_ex.NotEnoughHostsAvailable()

        # Check if we have enough available SUs for this reservation
        if usage_enforcement:
            total_su_factor = sum(billrate.computehost_billrate(host_id) for host_id in host_ids)
            try:
                balance = float(r.hget('balance', project_name))
                encumbered = float(r.hget('encumbered', project_name))
                start_date = values['start_date']
                end_date = values['end_date']
                duration = end_date - start_date
                hours = duration.total_seconds() / 3600.0
                requested = hours * total_su_factor
                left = balance - encumbered
                if left - requested < 0:
                    raise BillingError(
                        'Reservation for project {} would spend {:.2f} SUs, only {:.2f} left'.format(project_name, requested, left))
                LOG.info("Increasing encumbered for project {} by {:.2f} ({:.2f} hours @ {:.2f} SU/hr)"
                    .format(project_name, requested, hours, total_su_factor))
                r.hincrbyfloat('encumbered', project_name, str(requested))
                LOG.info("Usage encumbered for project %s now %s", project_name, r.hget('encumbered', project_name))
                LOG.info("Removing lease exception for user %s", user_name)
                r.hdel('user_exceptions', user_name)
            except redis.exceptions.ConnectionError:
                LOG.exception("cannot connect to redis host %s", CONF.manager.usage_db_host)
        else:
            LOG.info("Usage enforcement not in effect")

        pool = nova.ReservationPool()
        pool_name = reservation_id
        az_name = "%s%s" % (CONF[self.resource_type].blazar_az_prefix,
                            pool_name)
        pool_instance = pool.create(name=pool_name, az=az_name)
        host_rsrv_values = {
            'reservation_id': reservation_id,
            'aggregate_id': pool_instance.id,
            'resource_properties': values['resource_properties'],
            'hypervisor_properties': values['hypervisor_properties'],
            'count_range': values['count_range'],
            'status': 'pending',
            'before_end': values['before_end']
        }
        host_reservation = db_api.host_reservation_create(host_rsrv_values)
        for host_id in host_ids:
            db_api.host_allocation_create({'compute_host_id': host_id,
                                          'reservation_id': reservation_id})
        return host_reservation['id']

    def update_reservation(self, reservation_id, values, usage_enforcement=False, usage_db_host=None, project_name=None):
        """Update reservation."""
        if usage_enforcement:
            r = self._setup_redis(usage_db_host)
            self._init_usage_values(r, project_name)

        reservation = db_api.reservation_get(reservation_id)
        lease = db_api.lease_get(reservation['lease_id'])

        host_allocations = db_api.host_allocation_get_all_by_values(reservation_id=reservation_id)

        # Check if we have enough available SUs for update
        if usage_enforcement:
            old_su_factor = sum(
                billrate.computehost_billrate(h['compute_host_id'])
                for h
                in host_allocations
            )
            new_su_factor = old_su_factor # may be altered later
            try:
                balance = float(r.hget('balance', project_name))
                encumbered = float(r.hget('encumbered', project_name))

                old_duration = lease['end_date'] - lease['start_date']
                new_duration = values['end_date'] - values['start_date']
                change = new_duration - old_duration
                hours = change.total_seconds() / 3600.0
                estimated_requested = hours * old_su_factor
                left = balance - encumbered
                if left - estimated_requested < 0:
                    raise BillingError(
                        'Update reservation would spend {:.2f} more SUs, only {:.2f} left'.format(estimated_requested, left))
            except redis.exceptions.ConnectionError:
                left = None
                LOG.exception("cannot connect to redis host %s", CONF.manager.usage_db_host)
        else:
            old_su_factor = new_su_factor = None

        # if the time period is growing
        if (values['start_date'] < lease['start_date'] or
                values['end_date'] > lease['end_date']):
            allocations = [] # allocations to destroy
            hosts_in_pool = []
            for allocation in host_allocations:
                full_periods = db_utils.get_full_periods(
                    allocation['compute_host_id'],
                    values['start_date'],
                    values['end_date'],
                    datetime.timedelta(seconds=1))
                max_start = max(lease['start_date'], values['start_date'])
                min_end = min(lease['end_date'], values['end_date'])
                if not (len(full_periods) == 0 or
                        (len(full_periods) == 1 and
                         full_periods[0][0] == max_start and
                         full_periods[0][1] == min_end)):
                    allocations.append(allocation)
            if allocations:
                if reservation['status'] == 'active':
                    raise manager_ex.NotEnoughHostsAvailable()
                host_reservation = db_api.host_reservation_get(
                    reservation['resource_id'])
                pool = nova.ReservationPool()
                hosts_in_pool.extend(pool.get_computehosts(
                    host_reservation['aggregate_id']))
                host_ids = self._matching_hosts(
                    host_reservation['hypervisor_properties'],
                    host_reservation['resource_properties'],
                    str(len(allocations)) + '-' + str(len(allocations)),
                    values['start_date'],
                    values['end_date'])
                if not host_ids:
                    raise manager_ex.NotEnoughHostsAvailable()

                if usage_enforcement:
                    for allocation in allocations:
                        new_su_factor -= billrate.computehost_billrate(allocation['compute_host_id'])
                    for host_id in host_ids:
                        new_su_factor += billrate.computehost_billrate(host_id)

                    if not isclose(new_su_factor, old_su_factor, rel_tol=1e-5):
                        LOG.warning("SU factor changing from {} to {}"
                                    .format(old_su_factor, new_su_factor))
                        LOG.warning("Refusing factor change!")
                        # XXX easier for usage-reporting, but could probably allow
                        # not-yet-started reservations to be modified without much trouble.
                        raise BillingError("Modifying a reservation that changes the SU cost is prohibited")

                # XXX computehost changes below are not undone on error,
                # must verify policy beforehand.
                if hosts_in_pool:
                    old_hosts = [db_api.host_get(allocation['compute_host_id'])
                                 for allocation in allocations]
                    old_hostnames = [old_host['hypervisor_hostname']
                                     for old_host in old_hosts]
                    pool.remove_computehost(host_reservation['aggregate_id'],
                                            old_hostnames)
                for allocation in allocations:
                    LOG.debug("Dropping host {} from reservation {}".format(allocation['compute_host_id'], reservation_id))
                    db_api.host_allocation_destroy(allocation['id'], soft_delete=False)

                for host_id in host_ids:
                    LOG.debug("Adding host {} to reservation {}".format(host_id, reservation_id))
                    db_api.host_allocation_create(
                        {'compute_host_id': host_id,
                         'reservation_id': reservation_id})
                    if hosts_in_pool:
                        host = db_api.host_get(host_id)
                        pool.add_computehost(host_reservation['aggregate_id'],
                                             host['hypervisor_hostname'])

        if usage_enforcement:
            old_hours = dt_hours(lease['end_date'] - lease['start_date'])
            new_hours = dt_hours(values['end_date'] - values['start_date'])
            change_hours = new_hours - old_hours
            change_su_factor = new_su_factor - old_su_factor
            change_encumbered = new_hours * new_su_factor - old_hours * old_su_factor
            if left is not None: # maybe computed above, if redis was available
                if change_encumbered > left:
                    raise BillingError('Update reservation would spend {:.2f} more '
                                       'SUs, only {:.2f} left'.format(requested, left))

            LOG.info("Increasing encumbered for project {} by {:.2f} ({:.2f} hours @ {:.2f} SU/hr)"
                .format(project_name, change_encumbered, change_hours, new_su_factor))

            if not isclose(new_su_factor, old_su_factor, rel_tol=1e-5):
                LOG.warning('Violated assertion: new/old su_factor expected to be identical')

            try:
                r.hincrbyfloat('encumbered', project_name, str(change_encumbered))
                new_encumbered = float(r.hget('encumbered', project_name))
            except redis.exceptions.ConnectionError:
                LOG.exception("cannot connect to redis host %s", CONF.manager.usage_db_host)
            LOG.info("Usage encumbered for project {} now {:.2f}"
                     .format(project_name, new_encumbered))

    def on_start(self, resource_id):
        """Add the hosts in the pool."""
        host_reservation = db_api.host_reservation_get(resource_id)
        pool = nova.ReservationPool()
        added_hosts = []
        for allocation in db_api.host_allocation_get_all_by_values(
                reservation_id=host_reservation['reservation_id']):
            try:
                host = db_api.host_get(allocation['compute_host_id'])
                pool.add_computehost(host_reservation['aggregate_id'],
                                     host['hypervisor_hostname'])
                added_hosts.append(host['hypervisor_hostname'])
            except Exception:
                if added_hosts:
                    LOG.warn('Removing hosts added to aggregate %s: %s',
                             host_reservation['aggregate_id'], added_hosts)
                    for host in added_hosts:
                        pool.remove_computehost(
                            host_reservation['aggregate_id'], host)
                raise

    def before_end(self, resource_id):
        """Take an action before the end of a lease."""
        host_reservation = db_api.host_reservation_get(resource_id)
        action = host_reservation['before_end']
        if action == 'default':
            action = CONF[plugin.RESOURCE_TYPE].before_end
        if action == 'snapshot':
            pool = nova.ReservationPool()
            client = nova.BlazarNovaClient()
            for host in pool.get_computehosts(
                    host_reservation['aggregate_id']):
                for server in client.servers.list(
                        search_opts={"node": host, "all_tenants": 1}):
                        client.servers.create_image(server=server)
        elif action == 'email':
            reservation_id = host_reservation['reservation_id']
            reservation = db_api.reservation_get(reservation_id)
            lease = db_api.lease_get(reservation['lease_id'])
            project_id = lease['project_id']
            user_id = lease['user_id']
            keystoneclient = keystone.BlazarKeystoneClient(username=CONF.os_admin_username,
                                               password=CONF.os_admin_password,
                                               tenant_name=CONF.os_admin_project_name)
            project = keystoneclient.projects.get(project_id)
            user = keystoneclient.users.get(user_id)
            params_tmp = ('--to "{recipient}" '
                          '--username "{username}" '
                          '--project-name "{project_name}" '
                          '--lease-name "{lease_name}" '
                          '--lease-id "{lease_id}" '
                          '--end-datetime "{end_datetime}" '
                          '--site "{site}"')
            params = params_tmp.format(recipient=user.email, 
                                username=user.name, 
                                project_name=project.name, 
                                lease_name=lease['name'], 
                                lease_id=lease['id'], 
                                end_datetime=lease['end_date'],
                                site=CONF.os_region_name)
            try:
                subprocess.check_call(shlex.split('/usr/local/bin/blazar_before_end_action_email {params}'.format(params=params)))
            except Exception as e:
                LOG.exception(str(e))

    def on_end(self, resource_id, usage_enforcement=False, usage_db_host=None, project_name=None):
        """Remove the hosts from the pool."""
        if usage_enforcement:
            r = self._setup_redis(usage_db_host)
            self._init_usage_values(r, project_name)

        host_reservation = db_api.host_reservation_get(resource_id)
        db_api.host_reservation_update(host_reservation['id'],
                                       {'status': 'completed'})
        allocations = db_api.host_allocation_get_all_by_values(
            reservation_id=host_reservation['reservation_id'])
        for allocation in allocations:
            db_api.host_allocation_destroy(allocation['id'])
        pool = nova.ReservationPool()
        for host in pool.get_computehosts(host_reservation['aggregate_id']):
            for server in self.nova.servers.list(
                    search_opts={"node": host, "all_tenants": 1}):
                try:
                    self.nova.servers.delete(server=server)
                except nova_exceptions.NotFound:
                    LOG.info('Could not find server %s, may have been deleted '
                             'concurrently.', server)
                except Exception as e:
                    LOG.exception('Failed to delete %s: %s.', server, str(e))
        try:
            pool.delete(host_reservation['aggregate_id'])
        except manager_ex.AggregateNotFound:
            pass

        if usage_enforcement:
            total_su_factor = sum(
                billrate.computehost_billrate(h['compute_host_id'])
                for h
                in allocations
            )
            try:
                reservation = db_api.reservation_get(host_reservation['reservation_id'])
                lease = db_api.lease_get(reservation['lease_id'])
                status = reservation['status']
                if status in ['pending', 'active']:
                    old_duration = lease['end_date'] - lease['start_date']
                    if status == 'pending':
                        new_duration = datetime.timedelta(seconds=0)
                    elif reservation['status'] == 'active':
                        new_duration = datetime.datetime.utcnow() - lease['start_date']
                    change = new_duration - old_duration
                    hours = change.total_seconds() / 3600.0
                    change_encumbered = hours * total_su_factor
                    LOG.info("Decreasing encumbered for project {} by {:.2f} ({:.2f} hours @ {:.2f} SU/hr)"
                        .format(project_name, -change_encumbered, hours, total_su_factor))
                    r.hincrbyfloat('encumbered', project_name, str(change_encumbered))
                    LOG.info("Usage encumbered for project %s now %s", project_name, r.hget('encumbered', project_name))
            except redis.exceptions.ConnectionError:
                LOG.exception("cannot connect to redis host %s", CONF.manager.usage_db_host)

    def _get_extra_capabilities(self, host_id):
        extra_capabilities = {}
        raw_extra_capabilities = (
            db_api.host_extra_capability_get_all_per_host(host_id))
        for capability in raw_extra_capabilities:
            key = capability['capability_name']
            extra_capabilities[key] = capability['capability_value']
        return extra_capabilities

    def get_computehost(self, host_id):
        host = db_api.host_get(host_id)
        extra_capabilities = self._get_extra_capabilities(host_id)
        if host is not None and extra_capabilities:
            res = host.copy()
            res.update(extra_capabilities)
            return res
        else:
            return host

    def list_computehosts(self):
        raw_host_list = db_api.host_list()
        host_list = []
        for host in raw_host_list:
            host_list.append(self.get_computehost(host['id']))
        return host_list

    def create_computehost(self, host_values):
        # TODO(sbauza):
        #  - Exception handling for HostNotFound
        host_id = host_values.pop('id', None)
        host_name = host_values.pop('name', None)
        try:
            trust_id = host_values.pop('trust_id')
        except KeyError:
            raise manager_ex.MissingTrustId()

        host_ref = host_id or host_name
        if host_ref is None:
            raise manager_ex.InvalidHost(host=host_values)

        with trusts.create_ctx_from_trust(trust_id):
            inventory = nova.NovaInventory()
            servers = inventory.get_servers_per_host(host_ref)
            if servers:
                raise manager_ex.HostHavingServers(host=host_ref,
                                                   servers=servers)
            host_details = inventory.get_host_details(host_ref)
            # NOTE(sbauza): Only last duplicate name for same extra capability
            # will be stored
            to_store = set(host_values.keys()) - set(host_details.keys())
            extra_capabilities_keys = to_store
            extra_capabilities = dict(
                (key, host_values[key]) for key in extra_capabilities_keys
            )
            pool = nova.ReservationPool()
            pool.add_computehost(self.freepool_name,
                                 host_details['hypervisor_hostname'])

            host = None
            cantaddextracapability = []
            try:
                if trust_id:
                    host_details.update({'trust_id': trust_id})
                host = db_api.host_create(host_details)
            except db_ex.BlazarDBException:
                # We need to rollback
                # TODO(sbauza): Investigate use of Taskflow for atomic
                # transactions
                pool.remove_computehost(self.freepool_name,
                                        host_details['hypervisor_hostname'])
            if host:
                for key in extra_capabilities:
                    values = {'computehost_id': host['id'],
                              'capability_name': key,
                              'capability_value': extra_capabilities[key],
                              }
                    try:
                        db_api.host_extra_capability_create(values)
                    except db_ex.BlazarDBException:
                        cantaddextracapability.append(key)
            if cantaddextracapability:
                raise manager_ex.CantAddExtraCapability(
                    keys=cantaddextracapability,
                    host=host['id'])
            if host:
                return self.get_computehost(host['id'])
            else:
                return None

    def update_computehost(self, host_id, values):
        if values:
            cant_update_extra_capability = []
            for value in values:
                capabilities = db_api.host_extra_capability_get_all_per_name(
                    host_id,
                    value,
                )
                if capabilities:
                    for raw_capability in capabilities:
                        capability = {
                            'capability_name': value,
                            'capability_value': values[value],
                        }
                        try:
                            db_api.host_extra_capability_update(
                                raw_capability['id'], capability)
                        except (db_ex.BlazarDBException, RuntimeError):
                            cant_update_extra_capability.append(
                                raw_capability['capability_name'])
                else:
                    new_capability = {
                        'computehost_id': host_id,
                        'capability_name': value,
                        'capability_value': values[value],
                    }
                    try:
                        db_api.host_extra_capability_create(new_capability)
                    except (db_ex.BlazarDBException, RuntimeError):
                        cant_update_extra_capability.append(
                            new_capability['capability_name'])
            if cant_update_extra_capability:
                raise manager_ex.CantAddExtraCapability(
                    host=host_id,
                    keys=cant_update_extra_capability)
        return self.get_computehost(host_id)

    def delete_computehost(self, host_id):
        host = db_api.host_get(host_id)
        if not host:
            raise manager_ex.HostNotFound(host=host_id)

        with trusts.create_ctx_from_trust(host['trust_id']):
            # TODO(sbauza):
            #  - Check if no leases having this host scheduled
            inventory = nova.NovaInventory()
            servers = inventory.get_servers_per_host(
                host['hypervisor_hostname'])
            if servers:
                raise manager_ex.HostHavingServers(
                    host=host['hypervisor_hostname'], servers=servers)

            try:
                pool = nova.ReservationPool()
                pool.remove_computehost(self.freepool_name,
                                        host['hypervisor_hostname'])
                # NOTE(sbauza): Extracapabilities will be destroyed thanks to
                #  the DB FK.
                db_api.host_destroy(host_id)
            except db_ex.BlazarDBException:
                # Nothing so bad, but we need to advert the admin
                # he has to rerun
                raise manager_ex.CantRemoveHost(host=host_id,
                                                pool=self.freepool_name)

    def _matching_hosts(self, hypervisor_properties, resource_properties,
                        count_range, start_date, end_date):
        """Return the matching hosts (preferably not allocated)

        """
        count_range = count_range.split('-')
        min_host = count_range[0]
        max_host = count_range[1]
        allocated_host_ids = []
        not_allocated_host_ids = []
        filter_array = []
        # TODO(frossigneux) support "or" operator
        if hypervisor_properties:
            filter_array = plugins_utils.convert_requirements(
                hypervisor_properties)
        if resource_properties:
            filter_array += plugins_utils.convert_requirements(
                resource_properties)
        for host in db_api.host_get_all_by_queries(filter_array):
            if not db_api.host_allocation_get_all_by_values(
                    compute_host_id=host['id']):
                not_allocated_host_ids.append(host['id'])
            elif db_utils.get_free_periods(
                host['id'],
                start_date,
                end_date,
                end_date - start_date,
            ) == [
                (start_date, end_date),
            ]:
                allocated_host_ids.append(host['id'])
        if len(not_allocated_host_ids) >= int(min_host):
            shuffle(not_allocated_host_ids)
            return not_allocated_host_ids[:int(max_host)]
        all_host_ids = allocated_host_ids + not_allocated_host_ids
        if len(all_host_ids) >= int(min_host):
            shuffle(all_host_ids)
            return all_host_ids[:int(max_host)]
        else:
            return []

    def _convert_int_param(self, param, name):
        """Checks that the parameter is present and can be converted to int."""
        if param is None:
            raise manager_ex.MissingParameter(param=name)
        if strutils.is_int_like(param):
            param = int(param)
        else:
            raise manager_ex.MalformedParameter(param=name)
        return param

    def _check_params(self, values):
        min_hosts = self._convert_int_param(values.get('min'), 'min')
        max_hosts = self._convert_int_param(values.get('max'), 'max')

        if 0 <= min_hosts and min_hosts <= max_hosts:
            values['count_range'] = str(min_hosts) + '-' + str(max_hosts)
        else:
            raise manager_ex.InvalidRange()

        if 'hypervisor_properties' not in values:
            raise manager_ex.MissingParameter(param='hypervisor_properties')
        if 'resource_properties' not in values:
            raise manager_ex.MissingParameter(param='resource_properties')

        if 'before_end' not in values:
            values['before_end'] = 'default'
        if values['before_end'] not in before_end_options:
            raise manager_ex.MalformedParameter(param='before_end')
