from . import base
from blazar import context
from blazar import status
from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_ex
from blazar.plugins.third_party_plugins import exceptions
from blazar.utils.openstack import heat
from blazar.utils.openstack import placement
from blazar.utils.openstack import nova
from blazar.utils.openstack import ironic
from blazar.utils import plugins as plugins_utils
from oslo_log import log as logging
from oslo_config import cfg

import datetime

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

plugin_opts = [
    cfg.StrOpt('blazar_az_prefix',
               default='blazar_',
               help='Prefix for Availability Zones created by Blazar'),
    cfg.StrOpt('before_end',
               default='',
               help='Actions which we will be taken before the end of '
                    'the lease'),
    cfg.StrOpt('default_resource_properties',
               default='',
               help='Default resource_properties when creating a lease of '
                    'this type.'),
]

before_end_options = ['', 'snapshot', 'default', 'email']
on_start_options = ['', 'default', 'orchestration']


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
        to_store = set(data.keys()) - set(host_details.keys())
        extra_capabilities_keys = to_store
        extra_capabilities = dict(
            (key, data[key]) for key in extra_capabilities_keys
        )

        if any([len(key) > 64 for key in extra_capabilities_keys]):
            raise manager_ex.ExtraCapabilityTooLong()

        self.placement_client.create_reservation_provider(
            host_details['hypervisor_hostname'])

        pool = nova.ReservationPool()
        # NOTE(jason): CHAMELEON-ONLY
        # changed from 'service_name' to 'hypervisor_hostname'
        pool.add_computehost(self.freepool_name,
                             host_details['hypervisor_hostname'])

        host = None
        cantaddextracapability = []
        if trust_id:
            host_details.update({'trust_id': trust_id})

        return host_details

    def reallocate(self, allocation):
        reservation = db_api.reservation_get(allocation['reservation_id'])
        h_reservation = db_api.resource_reservation_get(
            reservation['resource_id'])
        lease = db_api.lease_get(reservation['lease_id'])
        pool = nova.ReservationPool()

        # Remove the old host from the aggregate.
        if reservation['status'] == status.reservation.ACTIVE:
            host = db_api.resource_get(self.resource_type(), allocation['resource_id'])
            pool.remove_computehost(h_reservation['aggregate_id'],
                                    host["data"]['hypervisor_hostname'])

        # Allocate an alternative host.
        start_date = max(datetime.datetime.utcnow(), lease['start_date'])
        new_hostids = self.matching_resources(
            h_reservation["values"]['resource_properties'],
            start_date, lease['end_date'], 1, 1
        )
        if not new_hostids:
            db_api.resource_allocation_destroy(allocation['id'])
            LOG.warn('Could not find alternative host for reservation %s '
                     '(lease: %s).', reservation['id'], lease['name'])
            return False
        else:
            new_hostid = new_hostids.pop()
            db_api.resource_allocation_update(allocation['id'],
                                          {'resource_id': new_hostid})
            LOG.warn('Resource changed for reservation %s (lease: %s).',
                     reservation['id'], lease['name'])
            if reservation['status'] == status.reservation.ACTIVE:
                # Add the alternative host into the aggregate.
                new_host = db_api.resource_get(self.resource_type(), new_hostid)
                pool.add_computehost(h_reservation['aggregate_id'],
                                     new_host['hypervisor_hostname'])
            return True

    def rollback_create(self, data):
        pool = nova.ReservationPool()
        pool.remove_computehost(self.freepool_name,
                                data['hypervisor_hostname'])
        self.placement_client.delete_reservation_provider(
            data['hypervisor_hostname'])

    def validate_update_params(self, data):
        return data

    def validate_delete(self, resource_id):
        inventory = nova.NovaInventory()
        servers = inventory.get_servers_per_host(
            host['hypervisor_hostname'])
        if servers:
            raise manager_ex.HostHavingServers(
                host=host['hypervisor_hostname'], servers=servers)
        pool = nova.ReservationPool()
        # NOTE(jason): CHAMELEON-ONLY
        # changed from 'service_name' to 'hypervisor_hostname'
        pool.remove_computehost(self.freepool_name,
                                host['hypervisor_hostname'])
        self.placement_client.delete_reservation_provider(
            host['hypervisor_hostname'])

    def _is_valid_on_start_option(self, value):

        if 'orchestration' in value:
            stack = value.split(':')[-1]
            try:
                UUID(stack)
                return True
            except Exception:
                return False
        else:
            return value in on_start_options

    def allocation_candidates(self, values):
        if 'before_end' not in values:
            values['before_end'] = 'default'
        if values['before_end'] not in before_end_options:
            raise manager_ex.MalformedParameter(param='before_end')

        if 'on_start' not in values:
            values['on_start'] = 'default'
        if not self._is_valid_on_start_option(values['on_start']):
            raise manager_ex.MalformedParameter(param='on_start')

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
            "on_start": values['on_start'],
            "aggregate_id": pool_instance.id,
        }

    def reserve_new_resources(self, reservation_id, reservation_status, resource_ids):
        super(HostPlugin, self).reserve_new_resources(
            reservation_id, reservation_status, resource_ids)
        new_hosts = []
        for resource_id in resource_ids:
            new_host = db_api.resource_get(self.resource_type(), resource_id)
            new_hosts.append(new_host["data"]['hypervisor_hostname'])
        if reservation_status == status.reservation.ACTIVE:
            pool = nova.ReservationPool()
            pool.add_computehost(resource_reservation["values"]['aggregate_id'], new_hosts)

    def on_start(self, resource_id, lease=None):
        """Add the hosts in the pool."""
        host_reservation = db_api.resource_reservation_get(resource_id)
        pool = nova.ReservationPool()
        hosts = []
        for allocation in db_api.resource_allocation_get_all_by_values(
                reservation_id=host_reservation['reservation_id']):
            host = db_api.resource_get(
                self.resource_type(), allocation['resource_id'])
            hosts.append(host['data']['hypervisor_hostname'])
        pool.add_computehost(host_reservation["values"]['aggregate_id'], hosts)

        action = host_reservation["values"].get('on_start', 'default')

        if 'orchestration' in action:
            stack_id = action.split(':')[-1]
            heat_client = heat.BlazarHeatClient()
            heat_client.heat.stacks.update(
                stack_id=stack_id,
                existing=True,
                converge=True,
                parameters=dict(
                    reservation_id=host_reservation['reservation_id']))

    def before_end(self, resource_id, lease=None):
        """Take an action before the end of a lease."""
        host_reservation = db_api.resource_reservation_get(resource_id)

        action = host_reservation["values"]['before_end']
        if action == 'default':
            action = CONF[self.resource_type()].before_end

        if action == 'snapshot':
            pool = nova.ReservationPool()
            client = nova.BlazarNovaClient()
            for host in pool.get_computehosts(
                    host_reservation["values"]['aggregate_id']):
                for server in client.servers.list(
                    search_opts={"node": host, "all_tenants": 1,
                                 "project_id": lease['project_id']}):
                    # TODO(jason): Unclear if this even works! What happens
                    # when you try to createImage on a server not owned by the
                    # authentication context (admin context in this case.) Is
                    # the snapshot owned by the admin, or the original
                    client.servers.create_image(server=server)
        elif action == 'email':
            plugins_utils.send_lease_extension_reminder(
                lease, CONF.os_region_name)

    def on_end(self, resource_id, lease=None):
        """Remove the hosts from the pool."""
        super(HostPlugin, self).on_end(resource_id, lease)
        resource_reservation = db_api.resource_reservation_get(resource_id)
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
        hosts = db_api.resource_get_all_by_filters({})

        ironic_hosts = []
        nova_hosts = []
        for h in hosts:
            if 'hypervisor_type' in h and h['hypervisor_type'] == 'ironic':
                ironic_hosts.append(h)
            else:
                nova_hosts.append(h)

        failed_hosts = []
        recovered_hosts = []
        try:
            if ironic_hosts:
                invalid_power_states = ['error']
                invalid_provision_states = ['error', 'clean failed',
                                            'manageable', 'deploy failed']
                reservable_hosts = [h for h in ironic_hosts
                                    if h['reservable'] is True]
                unreservable_hosts = [h for h in ironic_hosts
                                      if h['reservable'] is False]

                ironic_client = ironic.BlazarIronicClient()
                nodes = ironic_client.ironic.node.list()
                failed_bm_ids = [n.uuid for n in nodes
                                 if n.maintenance
                                 or n.power_state in invalid_power_states
                                 or n.provision_state
                                 in invalid_provision_states]
                failed_hosts.extend([host for host in reservable_hosts
                                     if host['hypervisor_hostname']
                                     in failed_bm_ids])
                active_bm_ids = [n.uuid for n in nodes
                                 if not n.maintenance
                                 and n.provision_state in ['available']]
                recovered_hosts.extend([host for host in unreservable_hosts
                                        if host['hypervisor_hostname']
                                        in active_bm_ids])

            if nova_hosts:
                reservable_hosts = [h for h in nova_hosts
                                    if h['reservable'] is True]
                unreservable_hosts = [h for h in nova_hosts
                                      if h['reservable'] is False]

                hvs = self.nova.hypervisors.list()

                failed_hv_ids = [str(hv.id) for hv in hvs
                                 if hv.state == 'down'
                                 or hv.status == 'disabled']
                failed_hosts.extend([host for host in reservable_hosts
                                     if host['id'] in failed_hv_ids])

                active_hv_ids = [str(hv.id) for hv in hvs
                                 if hv.state == 'up'
                                 and hv.status == 'enabled']
                recovered_hosts.extend([host for host in unreservable_hosts
                                        if host['id'] in active_hv_ids])

        except Exception as e:
            LOG.exception('Skipping health check. %s', str(e))

        return failed_hosts, recovered_hosts
