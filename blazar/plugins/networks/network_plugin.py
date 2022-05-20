# -*- coding: utf-8 -*-
#
# Author: Pierre Riteau <pierre@stackhpc.com>
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
from random import shuffle

from neutronclient.common import exceptions as neutron_ex
from oslo_config import cfg
from oslo_log import log as logging

from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_ex
from blazar.plugins import base
from blazar.plugins import networks as plugin
from blazar import status
from blazar.utils.openstack import ironic
from blazar.utils.openstack import neutron
from blazar.utils import plugins as plugins_utils


plugin_opts = [
    cfg.StrOpt('default_resource_properties',
               default='',
               help='Default resource_properties when creating a lease of '
                    'this type.'),
    cfg.BoolOpt('display_default_resource_properties',
                default=True,
                help='Display default resource_properties if allocation fails '
                     'due to not enough resources'),
    cfg.BoolOpt('retry_allocation_without_defaults',
                default=True,
                help='Whether an allocation should be retried on failure '
                     'without the default properties'),

]


CONF = cfg.CONF
CONF.register_opts(plugin_opts, group=plugin.RESOURCE_TYPE)
LOG = logging.getLogger(__name__)

before_end_options = ['', 'snapshot', 'default', 'email']

QUERY_TYPE_ALLOCATION = 'allocation'


class NetworkPlugin(base.BasePlugin):
    """Plugin for network resource."""
    resource_type = plugin.RESOURCE_TYPE
    title = 'Network Plugin'
    description = 'This plugin creates and deletes networks.'
    query_options = {
        QUERY_TYPE_ALLOCATION: ['lease_id', 'reservation_id']
    }

    def __init__(self):
        super(NetworkPlugin, self).__init__()

    def filter_networks_by_reservation(self, networks, start_date, end_date):
        free = []
        non_free = []

        for network in networks:
            reservations = db_utils.get_reservations_by_network_id(
                network['id'], start_date, end_date)

            if reservations == []:
                free.append({'network': network, 'reservations': None})
            elif [r for r in reservations
                  if r['resource_type'] == self.resource_type]:
                non_free.append(
                    {'network': network, 'reservations': reservations})

        return free, non_free

    def reserve_resource(self, reservation_id, values):
        """Create reservation."""
        network_ids = self.allocation_candidates(values)

        network_id = next(iter(
            self._matching_networks(
                values['network_properties'],
                values['resource_properties'],
                values['start_date'],
                values['end_date'],
            )
        ), None)

        if not network_id:
            raise manager_ex.NotEnoughNetworksAvailable()

        network_id = network_ids[0]
        network_rsrv_values = {
            'reservation_id': reservation_id,
            'network_properties': values['network_properties'],
            'resource_properties': values['resource_properties'],
            'status': 'pending',
            'before_end': values['before_end'],
            'network_name': values['network_name'],
            'network_description': values.get('network_description'),
        }
        network_reservation = db_api.network_reservation_create(
            network_rsrv_values)
        db_api.network_allocation_create({
            'network_id': network_id, 'reservation_id': reservation_id})
        return network_reservation['id']

    def update_reservation(self, reservation_id, values):
        """Update reservation."""
        reservation = db_api.reservation_get(reservation_id)
        lease = db_api.lease_get(reservation['lease_id'])

        if (not [x for x in values.keys() if x in ['network_properties',
                                                   'resource_properties']]
                and values['start_date'] >= lease['start_date']
                and values['end_date'] <= lease['end_date']):
            # Nothing to update
            return

        dates_before = {'start_date': lease['start_date'],
                        'end_date': lease['end_date']}
        dates_after = {'start_date': values['start_date'],
                       'end_date': values['end_date']}
        network_reservation = db_api.network_reservation_get(
            reservation['resource_id'])
        self._update_allocations(dates_before, dates_after, reservation_id,
                                 reservation['status'], network_reservation,
                                 values)

        updates = {}
        if 'network_properties' in values:
            updates['network_properties'] = values.get(
                'network_properties')
        if 'resource_properties' in values:
            updates['resource_properties'] = values.get(
                'resource_properties')
        if updates:
            db_api.network_reservation_update(
                network_reservation['id'], updates)

    def on_start(self, resource_id, lease=None):
        """Creates a Neutron network using the allocated segment."""
        network_reservation = db_api.network_reservation_get(resource_id)
        network_name = network_reservation['network_name']
        network_description = network_reservation['network_description']
        reservation_id = network_reservation['reservation_id']

        # We need the lease to get to the project_id
        reservation = db_api.reservation_get(reservation_id)
        lease = db_api.lease_get(reservation['lease_id'])

        for allocation in db_api.network_allocation_get_all_by_values(
                reservation_id=reservation_id):
            network_segment = db_api.network_get(allocation['network_id'])
            network_type = network_segment['network_type']
            physical_network = network_segment['physical_network']
            segment_id = network_segment['segment_id']
            neutron_client = neutron.BlazarNeutronClient()
            network_body = {
                "network": {
                    "name": network_name,
                    "provider:network_type": network_type,
                    "provider:segmentation_id": segment_id,
                    "project_id": lease['project_id']
                }
            }

            if physical_network:
                network_body['network']['provider:physical_network'] = (
                    physical_network)

            if network_description:
                network_body['network']['description'] = network_description

            try:
                network = neutron_client.create_network(body=network_body)
                network_dict = network['network']
                network_id = network_dict['id']
                db_api.network_reservation_update(network_reservation['id'],
                                                  {'network_id': network_id})
            except Exception as e:
                LOG.error("create_network failed: %s", e)
                raise manager_ex.NetworkCreationFailed(name=network_name,
                                                       id=reservation_id,
                                                       msg=str(e))

    def delete_port(self, neutron_client, ironic_client, port):
        if port['binding:vnic_type'] == 'baremetal':
            node = port.get('binding:host_id')
            node_info = ironic_client.node.get(node)

            if node and node_info.instance_uuid:
                ironic_client.node.vif_detach(node, port['id'])
            else:
                raise Exception("Expected to find attribute binding:host_id "
                                "on port %s" % port['id'])

        neutron_client.delete_port(port['id'])

    def delete_subnet(self, neutron_client, subnet_id):
        neutron_client.delete_subnet(subnet_id)

    def delete_router(self, neutron_client, router_id):
        neutron_client.remove_gateway_router(router_id)
        neutron_client.delete_router(router_id)

    def delete_neutron_network(self, network_id, reservation_id,
                               trust_id=None):
        if network_id is None:
            LOG.info("Not deleting network for reservation %s as no network "
                     "ID was recorded",
                     reservation_id)
            return

        neutron_client = neutron.BlazarNeutronClient(trust_id=trust_id)
        ironic_client = ironic.BlazarIronicClient()

        try:
            neutron_client.show_network(network_id)
        except neutron_ex.NetworkNotFoundClient:
            LOG.info("Not deleting network %s as it could not be found",
                     network_id)
            return

        try:
            ports = neutron_client.list_ports(network_id=network_id)
            instance_ports = neutron_client.list_ports(
                device_owner='compute:nova', network_id=network_id)
            for instance_port in instance_ports['ports']:
                self.delete_port(neutron_client, ironic_client, instance_port)

            router_ids = [port['device_id'] for port in ports['ports'] if
                          port['device_owner'] == 'network:router_interface']
            for router_id in router_ids:
                router_ports = neutron_client.list_ports(device_id=router_id)

                # Remove static routes
                neutron_client.update_router(
                    router_id, body={'router': {'routes': []}})

                # Remove subnets
                subnets = set()
                for router_port in router_ports['ports']:
                    if router_port['device_owner'] != 'network:router_gateway':
                        for fixed_ip in router_port['fixed_ips']:
                            subnets.update([fixed_ip['subnet_id']])
                for subnet_id in subnets:
                    body = {}
                    body['subnet_id'] = subnet_id
                    neutron_client.remove_interface_router(router_id,
                                                           body=body)

                # Delete external gateway and router
                self.delete_router(neutron_client, router_id)

            subnets = neutron_client.list_subnets(network_id=network_id)
            for subnet in subnets['subnets']:
                self.delete_subnet(neutron_client, subnet['id'])

            neutron_client.delete_network(network_id)
        except Exception:
            LOG.exception("Failed to delete network %s", network_id)
            raise manager_ex.NetworkDeletionFailed(
                network_id=network_id, reservation_id=reservation_id)

    def on_end(self, resource_id, lease=None):
        """Delete the Neutron network created when the lease started.

        We first need to delete associated Neutron resources.
        """

        network_reservation = db_api.network_reservation_get(resource_id)
        reservation_id = network_reservation['reservation_id']

        db_api.network_reservation_update(network_reservation['id'],
                                          {'status': 'completed'})
        allocations = db_api.network_allocation_get_all_by_values(
            reservation_id=reservation_id)
        for allocation in allocations:
            db_api.network_allocation_destroy(allocation['id'])
        network_id = network_reservation['network_id']

        # The call to delete must be done without trust_id so the admin role is
        # used
        self.delete_neutron_network(network_id, reservation_id)

    def _get_extra_capabilities(self, network_id):
        extra_capabilities = {}
        raw_extra_capabilities = (
            db_api.network_extra_capability_get_all_per_network(network_id))
        for capability, capability_name in raw_extra_capabilities:
            key = capability_name
            extra_capabilities[key] = capability.capability_value
        return extra_capabilities

    def get(self, network_id):
        return self.get_network(network_id)

    def get_network(self, network_id):
        network = db_api.network_get(network_id)
        extra_capabilities = self._get_extra_capabilities(network_id)
        if network is not None and extra_capabilities:
            res = network.copy()
            res.update(extra_capabilities)
            return res
        else:
            return network

    def list_networks(self):
        raw_network_list = db_api.network_list()
        network_list = []
        for network in raw_network_list:
            network_list.append(self.get_network(network['id']))
        return network_list

    def validate_network_param(self, values):
        marshall_attributes = set(['network_type', 'physical_network',
                                   'segment_id'])
        missing_attr = marshall_attributes - set(values.keys())
        if missing_attr:
            raise manager_ex.MissingParameter(param=','.join(missing_attr))

    def create_network(self, values):
        if 'trust_id' in values:
            del values['trust_id']

        # TODO(priteau): check that no network is using this segmentation_id
        self.validate_network_param(values)
        network_type = values.get('network_type')
        physical_network = values.get('physical_network')
        segment_id = values.get('segment_id')
        if network_type != 'vlan' and network_type != 'vxlan':
            raise manager_ex.MalformedParameter(param=network_type)

        # Check that VLAN segmentation ID is valid
        try:
            segment_id = int(segment_id)
        except ValueError:
            raise manager_ex.MalformedParameter(param=segment_id)
        if segment_id < 1 or segment_id > 4094:
            raise manager_ex.MalformedParameter(param=segment_id)

        network_values = {
            'network_type': network_type,
            'physical_network': physical_network,
            'segment_id': segment_id
        }
        network = db_api.network_create(network_values)

        to_store = set(values.keys()) - set(network.keys())
        extra_capabilities_keys = to_store
        extra_capabilities = dict(
            (key, values[key]) for key in extra_capabilities_keys
        )
        if any([len(key) > 64 for key in extra_capabilities_keys]):
            raise manager_ex.ExtraCapabilityTooLong()

        cantaddextracapability = []
        for key in extra_capabilities:
            values = {'network_id': network['id'],
                      'capability_name': key,
                      'capability_value': extra_capabilities[key],
                      }
            try:
                db_api.network_extra_capability_create(values)
            except db_ex.BlazarDBException:
                cantaddextracapability.append(key)
        if cantaddextracapability:
            raise manager_ex.CantAddExtraCapability(
                keys=cantaddextracapability,
                host=network['id'])
        return self.get_network(network['id'])

    def is_updatable_extra_capability(self, capability, capability_name):
        reservations = db_utils.get_reservations_by_network_id(
            capability['network_id'], datetime.datetime.utcnow(),
            datetime.date.max)

        for r in reservations:
            plugin_reservation = db_utils.get_plugin_reservation(
                r['resource_type'], r['resource_id'])

            requirements_queries = plugins_utils.convert_requirements(
                plugin_reservation['resource_properties'])

            # TODO(masahito): If all the reservations using the
            # extra_capability can be re-allocated it's okay to update
            # the extra_capability.
            for requirement in requirements_queries:
                # A requirement is of the form "key op value" as string
                if requirement.split(" ")[0] == capability_name:
                    return False
        return True

    def update_network(self, network_id, values):
        # nothing to update
        if not values:
            return self.get_network(network_id)

        network = db_api.network_get(network_id)
        if not network:
            raise manager_ex.NetworkNotFound(network=network_id)

        updatable = ['network_type', 'physical_network', 'segment_id']

        network_type = values.get('network_type')
        if network_type == 'vlan':
            segment_id = values.get('segment_id')
            if segment_id is not None:
                try:
                    segment_id = int(segment_id)
                except ValueError:
                    raise manager_ex.MalformedParameter(param=segment_id)
                if segment_id < 1 or segment_id > 4094:
                    raise manager_ex.MalformedParameter(param=segment_id)

        new_values = {}
        for key in updatable:
            if key in values and values[key] is not None:
                new_values[key] = values[key]
        db_api.network_update(network_id, new_values)

        cant_update_extra_capability = []
        cant_delete_extra_capability = []
        previous_capabilities = self._get_extra_capabilities(network_id)
        updated_keys = set(values.keys()) & set(previous_capabilities.keys())
        new_keys = set(values.keys()) - set(previous_capabilities.keys())

        for key in updated_keys:
            raw_capability, cap_name = next(iter(
                db_api.network_extra_capability_get_all_per_name(
                    network_id, key)))
            capability = {
                'capability_name': key,
                'capability_value': values[key],
            }
            if self.is_updatable_extra_capability(raw_capability, cap_name):
                if values[key] is not None:
                    try:
                        db_api.network_extra_capability_update(
                            raw_capability['id'], capability)
                    except (db_ex.BlazarDBException, RuntimeError):
                        cant_update_extra_capability.append(cap_name)
                else:
                    try:
                        db_api.network_extra_capability_destroy(
                            raw_capability['id'])
                    except db_ex.BlazarDBException:
                        cant_delete_extra_capability.append(cap_name)
            else:
                LOG.info("Capability %s can't be updated because "
                         "existing reservations require it.",
                         cap_name)
                cant_update_extra_capability.append(cap_name)

        for key in new_keys:
            new_capability = {
                'network_id': network_id,
                'capability_name': key,
                'capability_value': values[key],
            }
            try:
                db_api.network_extra_capability_create(new_capability)
            except (db_ex.BlazarDBException, RuntimeError):
                cant_update_extra_capability.append(key)

        if cant_update_extra_capability:
            raise manager_ex.CantAddExtraCapability(
                network=network_id, keys=cant_update_extra_capability)

        if cant_delete_extra_capability:
            raise manager_ex.ExtraCapabilityNotFound(
                resource=network_id, keys=cant_delete_extra_capability)

        LOG.info('Extra capabilities on network %s updated with %s',
                 network_id, values)

    def delete_network(self, network_id):
        network = db_api.network_get(network_id)
        if not network:
            raise manager_ex.NetworkNotFound(network=network_id)

        if db_api.network_allocation_get_all_by_values(
                network_id=network_id):
            raise manager_ex.CantDeleteNetwork(
                network=network_id,
                msg='The network is reserved.'
            )

        try:
            db_api.network_destroy(network_id)
        except db_ex.BlazarDBException as e:
            # Nothing so bad, but we need to alert admins
            # they have to rerun
            raise manager_ex.CantDeleteNetwork(network=network_id, msg=str(e))

    def list_allocations(self, query, detail=False):
        network_id_list = [n['id'] for n in db_api.network_list()]
        options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
        options['detail'] = detail

        network_allocations = self.query_network_allocations(network_id_list,
                                                             **options)
        self.add_extra_allocation_info(network_allocations)
        return [{"resource_id": network, "reservations": allocs}
                for network, allocs in network_allocations.items()]

    def get_allocations(self, network_id, query):
        options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
        network_allocations = self.query_network_allocations([network_id],
                                                             **options)
        allocs = network_allocations.get(network_id, [])
        return {"resource_id": network_id, "reservations": allocs}

    def query_allocations(self, networks, lease_id=None, reservation_id=None):
        return self.query_network_allocations(networks, lease_id=lease_id,
                                              reservation_id=reservation_id)

    def query_network_allocations(self, networks, lease_id=None,
                                  reservation_id=None, detail=False):
        """Return dict of network and its allocations

        The list element forms
        {
            'network-id': [
                            {
                              'lease_id': lease_id,
                              'id': reservation_id,
                              'start_date': lease_start_date,
                              'end_date': lease_end_date
                            }
                          ]
        }.
        """
        start = datetime.datetime.utcnow()
        end = datetime.date.max

        reservations = db_utils.get_reservation_allocations_by_network_ids(
            networks, start, end, lease_id, reservation_id)
        network_allocations = {n: [] for n in networks}

        for reservation in reservations:
            if not detail:
                del reservation['project_id']
                del reservation['lease_name']
                del reservation['status']

            for network_id in reservation['network_ids']:
                if network_id in network_allocations.keys():
                    network_allocations[network_id].append({
                        k: v for k, v in reservation.items()
                        if k != 'network_ids'})

        return network_allocations

    def update_default_parameters(self, values):
        self.add_default_resource_properties(values)

    def allocation_candidates(self, values):
        self._check_params(values)

        network_ids = self._matching_networks(
            values['network_properties'],
            values['resource_properties'],
            values['start_date'],
            values['end_date']
        )

        if len(network_ids) < 1:
            raise manager_ex.NotEnoughNetworksAvailable()

        return network_ids[:1]

    def _matching_networks(self, network_properties, resource_properties,
                           start_date, end_date):
        """Return the matching networks (preferably not allocated)"""
        allocated_network_ids = []
        not_allocated_network_ids = []
        filter_array = []
        start_date_with_margin = start_date - datetime.timedelta(
            minutes=CONF.cleaning_time)
        end_date_with_margin = end_date + datetime.timedelta(
            minutes=CONF.cleaning_time)

        # TODO(frossigneux) support "or" operator
        if network_properties:
            filter_array = plugins_utils.convert_requirements(
                network_properties)
        if resource_properties:
            filter_array += plugins_utils.convert_requirements(
                resource_properties)
        for network in db_api.network_get_all_by_queries(
                filter_array):
            if not db_api.network_allocation_get_all_by_values(
                    network_id=network['id']):
                not_allocated_network_ids.append(network['id'])
            elif db_utils.get_free_periods(
                network['id'],
                start_date_with_margin,
                end_date_with_margin,
                end_date_with_margin - start_date_with_margin,
                resource_type='network'
            ) == [
                (start_date_with_margin, end_date_with_margin),
            ]:
                allocated_network_ids.append(network['id'])

        if len(not_allocated_network_ids):
            shuffle(not_allocated_network_ids)
            return not_allocated_network_ids

        all_network_ids = allocated_network_ids + not_allocated_network_ids
        if len(all_network_ids):
            shuffle(all_network_ids)
            return all_network_ids
        else:
            return []

    def _check_params(self, values):
        required_values = ['network_name', 'network_properties',
                           'resource_properties']
        for value in required_values:
            if value not in values:
                raise manager_ex.MissingParameter(param=value)

        if 'network_description' in values:
            values['network_description'] = str(values['network_description'])

        if 'before_end' not in values:
            values['before_end'] = 'default'
        if values['before_end'] not in before_end_options:
            raise manager_ex.MalformedParameter(param='before_end')

    def _update_allocations(self, dates_before, dates_after, reservation_id,
                            reservation_status, network_reservation, values):
        network_properties = values.get(
            'network_properties',
            network_reservation['network_properties'])
        resource_properties = values.get(
            'resource_properties',
            network_reservation['resource_properties'])
        alloc = next(iter(db_api.network_allocation_get_all_by_values(
            reservation_id=reservation_id)))
        change_allocation = self._allocation_needs_change(
            dates_before, dates_after, network_properties,
            resource_properties, alloc)

        if not change_allocation:
            return

        if (reservation_status == status.reservation.ACTIVE):
            # To support removing a reserved network from an active reservation
            # we need support for deallocating a network, which requires
            # deleting the network and its subnets, removing ports etc, which
            # is not currenty implemented.
            # TODO(jasonanderson): should have a more descriptive error here.
            raise manager_ex.NotEnoughNetworksAvailable((
                "Operation requires removing a network from its reservation, "
                "which is not supported."))

        new_network_id = next(iter(
            self._matching_networks(
                network_properties, resource_properties,
                dates_after['start_date'], dates_after['end_date'])
        ), None)

        if not new_network_id:
            raise manager_ex.NotEnoughNetworksAvailable()

        LOG.debug('Adding network %s to reservation %s',
                  new_network_id, reservation_id)
        db_api.network_allocation_create({
            'network_id': new_network_id,
            'reservation_id': reservation_id,
        })

        LOG.debug('Removing network %s from reservation %s',
                  alloc['network_id'], reservation_id)
        db_api.network_allocation_destroy(alloc['id'])

    def _allocation_needs_change(self, dates_before, dates_after,
                                 network_properties, resource_properties,
                                 alloc):
        """Determines if a network allocation needs to be exchanged."""
        requested_network_ids = [network['id'] for network in
                                 self._filter_networks_by_properties(
                                 network_properties, resource_properties)]

        if alloc['network_id'] not in requested_network_ids:
            return True

        starting_earlier = (
            dates_after['start_date'] < dates_before['start_date'])
        ending_later = dates_after['end_date'] > dates_before['end_date']

        if (starting_earlier or ending_later):
            max_start = max(dates_before['start_date'],
                            dates_after['start_date'])
            min_end = min(dates_before['end_date'],
                          dates_after['end_date'])

            reserved_periods = db_utils.get_reserved_periods(
                alloc['network_id'],
                dates_after['start_date'],
                dates_after['end_date'],
                datetime.timedelta(minutes=CONF.cleaning_time),
                resource_type='network')
            reserved_by_others = [
                p for p in reserved_periods
                if not (p[0] == max_start and p[1] == min_end)
            ]
            return len(reserved_by_others) > 0

        return False

    def _filter_networks_by_properties(self, network_properties,
                                       resource_properties):
        filter = []
        if network_properties:
            filter += plugins_utils.convert_requirements(network_properties)
        if resource_properties:
            filter += plugins_utils.convert_requirements(resource_properties)
        if filter:
            return db_api.network_get_all_by_queries(filter)
        else:
            return db_api.network_list()
