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

from oslo_config import cfg

from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.manager import exceptions as manager_ex
from blazar.utils.openstack import placement
from blazar.utils.openstack import zun
from oslo_log import log as logging
from random import shuffle
from zunclient import exceptions as zun_ex


CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class ZunPlugin(zun.ZunClientWrapper):
    """Plugin for zun device driver."""
    device_driver = 'zun'

    def create_device(self, device_values):
        device_id = device_values.get('id')
        device_name = device_values.get('name')
        device_ref = device_id or device_name

        if device_ref is None:
            raise manager_ex.InvalidHost(host=device_values)

        inventory = zun.ZunInventory()
        zun_compute_node = inventory.get_host_details(device_ref)
        if len(zun_compute_node['containers']) > 0:
            raise manager_ex.HostHavingContainers(host=device_ref)

        device_properties = {'id': zun_compute_node['id'],
                             'name': zun_compute_node['name'],
                             'device_type': 'container',
                             'device_driver': 'zun'}

        to_store = set(device_values.keys()) - set(device_properties.keys())
        extra_capabilities_keys = to_store
        extra_capabilities = dict(
            (key, device_values[key]) for key in extra_capabilities_keys
        )

        if any([len(key) > 64 for key in extra_capabilities_keys]):
            raise manager_ex.ExtraCapabilityTooLong()

        placement_client = placement.BlazarPlacementClient()
        placement_client.create_reservation_provider(
            host_name=zun_compute_node['name'])

        device = None
        cantaddextracapability = []
        try:
            device = db_api.device_create(device_properties)
        except db_ex.BlazarDBException as e:
            placement_client.delete_reservation_provider(
                host_name=zun_compute_node['name'])
            raise
        for key in extra_capabilities:
            values = {'device_id': device['id'],
                      'capability_name': key,
                      'capability_value': extra_capabilities[key],
                      }
            try:
                db_api.device_extra_capability_create(values)
            except db_ex.BlazarDBException:
                cantaddextracapability.append(key)
        if cantaddextracapability:
            raise manager_ex.CantAddExtraCapability(
                keys=cantaddextracapability,
                host=device['id'])
        return device['id']

    def cleanup_device(self, device):
        for container in self.zun.containers.list(host=device['name']):
            try:
                self.zun.containers.delete(container['uuid'])
            except zun_ex.NotFound:
                LOG.info('Could not find container %s, may have been deleted '
                         'concurrently.', container['container_id'])
            except Exception as e:
                LOG.exception('Failed to delete %s: %s.',
                              container['container_id'], str(e))
