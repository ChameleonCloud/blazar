# Copyright (c) 2018 StackHPC
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

from oslo_config import cfg

from blazar import manager
from blazar.utils import service

CONF = cfg.CONF
CONF.import_opt('rpc_topic', 'blazar.manager.service', 'manager')


class ManagerRPCAPI(service.RPCClient):
    """Client side for the Manager RPC API.

    Used from other services to communicate with blazar-manager service.
    """
    BASE_RPC_API_VERSION = '1.0'

    def __init__(self):
        """Initiate RPC API client with needed topic and RPC version."""
        super(ManagerRPCAPI, self).__init__(manager.get_target())

    def get_device(self, device_id):
        """Get detailed info about some device."""
        return self.call('device:get_device', device_id=device_id)

    def list_devices(self):
        """List all devices."""
        return self.call('device:list_devices')

    def create_device(self, values):
        """Create device with specified parameters."""
        return self.call('device:create_device',
                         values=values)

    def update_device(self, device_id, values):
        """Update device with passes values dictionary."""
        return self.call('device:update_device', device_id=device_id,
                         values=values)

    def delete_device(self, device_id):
        """Delete specified device."""
        return self.call('device:delete_device',
                         device_id=device_id)

    def list_allocations(self, query, detail=False):
        """List all allocations on all devices."""
        return self.call('device:list_allocations',
                         query=query, detail=detail)

    def get_allocations(self, device_id, query):
        """List all allocations on a specified device."""
        return self.call('device:get_allocations',
                         device_id=device_id, query=query)

    def list_resource_properties(self, query):
        """List resource properties and possible values for devices."""
        return self.call('device:list_resource_properties', query=query)

    def update_resource_property(self, property_name, values):
        """Update resource property for device."""
        return self.call('device:update_resource_property',
                         property_name=property_name, values=values)

    def reallocate(self, device_id, data):
        """Exchange device from current allocations."""
        return self.call('device:reallocate_device',
                         device_id=device_id, data=data)
