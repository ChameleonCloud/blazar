# Copyright (c) 2020 University of Chicago.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from blazar import context
from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.manager import exceptions as manager_ex
from blazar.plugins.third_party_plugins import base
from blazar import policy
from blazar.utils.openstack import nova
from blazar.utils.openstack import placement
from blazar.utils import trusts

from novaclient import exceptions as nova_exceptions

from oslo_config import cfg
from oslo_log import log as logging


plugin_opts = [
    cfg.StrOpt('blazar_az_prefix',
               default='blazar_',
               help='Prefix for Availability Zones created by Blazar'),
    cfg.StrOpt('before_end',
               default='',
               help='Actions which we will be taken before the end of '
                    'the lease'),
]

CONF = cfg.CONF
LOG = logging.getLogger(__name__)

before_end_options = ['', 'snapshot', 'default']


class HostPlugin(base.BasePlugin, nova.NovaClientWrapper):
    freepool_name = CONF.nova.aggregate_freepool_name

    def __init__(self):
        super(HostPlugin, self).__init__()
        self.placement_client = placement.BlazarPlacementClient()
        CONF.register_opts(plugin_opts, group=self.resource_type())

    def resource_type(self):
        return "compute_host"

    def validate_create_params(self, data):
        host_id = data.pop('id', None)
        host_name = data.pop('name', None)
        try:
            trust_id = data.pop('trust_id')
        except KeyError:
            raise manager_ex.MissingTrustId()

        host_ref = host_id or host_name
        if host_ref is None:
            raise manager_ex.InvalidHost(host=data)

        inventory = nova.NovaInventory()
        servers = inventory.get_servers_per_host(host_ref)
        if servers:
            raise manager_ex.HostHavingServers(host=host_ref,
                                               servers=servers)
        host_details = inventory.get_host_details(host_ref)
        # NOTE(sbauza): Only last duplicate name for same extra capability
        # will be stored
        to_store = set(host_details.keys()) - set(host_details.keys())
        extra_capabilities_keys = to_store
        extra_capabilities = dict(
            (key, host_details[key]) for key in extra_capabilities_keys
        )

        if any([len(key) > 64 for key in extra_capabilities_keys]):
            raise manager_ex.ExtraCapabilityTooLong()

        self.placement_client.create_reservation_provider(
            host_details['hypervisor_hostname'])

        pool = nova.ReservationPool()
        pool.add_computehost(self.freepool_name,
                             host_details['hypervisor_hostname'])

        host = None
        cantaddextracapability = []
        try:
            if trust_id:
                host_details.update({'trust_id': trust_id})
            host = db_api.host_create(host_details)
        except db_ex.BlazarDBException as e:
            # We need to rollback
            # TODO(sbauza): Investigate use of Taskflow for atomic
            # transactions
            pool.remove_computehost(self.freepool_name,
                                    host_details['hypervisor_hostname'])
            self.placement_client.delete_reservation_provider(
                host_details['hypervisor_hostname'])
            raise e
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

        return host_details

    def rollback_create(self, data):
        pool = nova.ReservationPool()
        pool.remove_computehost(self.freepool_name,
                                data['hypervisor_hostname'])
        self.placement_client.delete_reservation_provider(
            data['hypervisor_hostname'])

    def validate_update_params(self, resource_id, data):
        return data

    def validate_delete(self, resource_id):
        host = db_api.resource_get(self.resource_type(), resource_id)
        if not host:
            raise manager_ex.HostNotFound(host=resource_id)
        with trusts.create_ctx_from_trust(host["data"]['trust_id']):
            inventory = nova.NovaInventory()
            servers = inventory.get_servers_per_host(
                host["data"]['hypervisor_hostname'])
            if servers:
                raise manager_ex.HostHavingServers(
                    host=host["data"]['hypervisor_hostname'], servers=servers)

            try:
                pool = nova.ReservationPool()
                pool.remove_computehost(self.freepool_name,
                                        host["data"]['hypervisor_hostname'])
                self.placement_client.delete_reservation_provider(
                    host["data"]['hypervisor_hostname'])
            except db_ex.BlazarDBException as e:
                raise manager_ex.CantDeleteHost(host=resource_id, msg=str(e))

    def allocation_candidates(self, values):
        if 'resource_properties' not in values:
            raise manager_ex.MissingParameter(param='resource_properties')

        if 'before_end' not in values:
            values['before_end'] = 'default'
        if values['before_end'] not in before_end_options:
            raise manager_ex.MalformedParameter(param='before_end')
        return super(HostPlugin, self).allocation_candidates(values)

    def reservation_values(self, reservation_id, values):
        pool = nova.ReservationPool()
        pool_name = reservation_id
        az_name = "%s%s" % (CONF[self.resource_type()].blazar_az_prefix,
                            pool_name)
        ctx = context.current()
        pool_instance = pool.create(
            name=pool_name, project_id=ctx.project_id, az=az_name)
        return {
            "resource_properties": values["resource_properties"],
            "before_end": values['before_end'],
            "aggregate_id": pool_instance.id,
        }

    @trusts.use_trust_auth()
    def api_create(self, data):
        policy.check_enforcement(self.resource_type(), "post")
        create_data = data["data"]
        trust_id = data["trust_id"]
        create_data["trust_id"] = trust_id
        data = self.validate_create_params(create_data)
        try:
            resource = db_api.resource_create(self.resource_type(), data)
        except db_ex.BlazarDBException as e:
            self.rollback_create(data["data"])
            raise e
        return resource

    def allocate(self, resource_reservation, resources):
        """Take action after an allocation is made"""
        hosts = [resource["data"]["hypervisor_hostname"] for resource in resources]
        pool = nova.ReservationPool()
        pool.add_computehost(
            resource_reservation["values"]['aggregate_id'], hosts)

    def deallocate(self, resource_reservation, resources):
        """Take action after an allocation is deleted"""

        pool = nova.ReservationPool()
        for host in pool.get_computehosts(
                resource_reservation["values"]['aggregate_id']):
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
            pool.delete(resource_reservation["values"]['aggregate_id'])
        except manager_ex.AggregateNotFound:
            pass

    def before_end(self, reservation_id):
        """Take an action before the end of a lease."""
        host_reservation = db_api.resource_reservation_get(reservation_id)
        action = host_reservation["values"]['before_end']
        if action == 'default':
            action = CONF[self.resource_type()].before_end
        if action == 'snapshot':
            pool = nova.ReservationPool()
            client = nova.BlazarNovaClient()
            for host in pool.get_computehosts(
                    host_reservation['aggregate_id']):
                for server in client.servers.list(
                        search_opts={"host": host, "all_tenants": 1}):
                    client.servers.create_image(server=server)

    def notification_callback(self, event_type, payload):
        LOG.trace('Handling a notification...')
        reservation_flags = {}

        data = payload.get('nova_object.data', None)
        if data:
            if data['disabled'] or data['forced_down']:
                failed_hosts = db_api.reservable_resource_get_all_by_queries(
                    ['hypervisor_hostname == ' + data['host']])
                if failed_hosts:
                    LOG.warn('%s failed.',
                             failed_hosts[0]['hypervisor_hostname'])
                    reservation_flags = self._handle_failures(failed_hosts)
            else:
                recovered_hosts = db_api.resource_get_all_by_queries(
                    ['reservable == 0',
                     'hypervisor_hostname == ' + data['host']])
                if recovered_hosts:
                    db_api.resource_update(self.resource_type(),
                                           recovered_hosts[0]['id'],
                                           {'reservable': True})
                    LOG.warn('%s recovered.',
                             recovered_hosts[0]["data"]['hypervisor_hostname'])

        return reservation_flags

    def get_notification_event_types(self):
        return ['service.update']

    def poll_resource_failures(self):
        """Check health of hosts by calling Nova Hypervisors API.

        :return: a list of failed hosts, a list of recovered hosts.
        """
        hosts = db_api.resource_get_all_by_filters(self.resource_type(), {})
        reservable_hosts = [h for h in hosts if h['reservable'] is True]
        unreservable_hosts = [h for h in hosts if h['reservable'] is False]

        try:
            hvs = self.nova.hypervisors.list()

            failed_hv_ids = [str(hv.id) for hv in hvs
                             if hv.state == 'down' or hv.status == 'disabled']
            failed_hosts = [host for host in reservable_hosts
                            if host['id'] in failed_hv_ids]

            active_hv_ids = [str(hv.id) for hv in hvs
                             if hv.state == 'up' and hv.status == 'enabled']
            recovered_hosts = [host for host in unreservable_hosts
                               if host['id'] in active_hv_ids]
        except Exception as e:
            LOG.exception('Skipping health check. %s', str(e))

        return failed_hosts, recovered_hosts
