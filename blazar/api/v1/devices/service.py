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

from blazar import context
from blazar import policy
from blazar.manager.service import get_plugins
from blazar.utils import trusts


class API(object):
    def __init__(self):
        self.plugin = get_plugins()["device"]

    @policy.authorize('devices', 'get')
    def get_devices(self):
        """List all existing devices."""
        return self.plugin.list_devices()

    @policy.authorize('devices', 'post')
    @trusts.use_trust_auth()
    def create_device(self, data):
        """Create new device.

        :param data: New device characteristics.
        :type data: dict
        """

        return self.plugin.create_device(data)

    @policy.authorize('devices', 'get')
    def get_device(self, device_id):
        """Get device by its ID.

        :param device_id: ID of the device in Blazar DB.
        :type device_id: str
        """
        return self.plugin.get_device(device_id)

    @policy.authorize('devices', 'put')
    def update_device(self, device_id, data):
        """Update device.

        :param device_id: ID of the device in Blazar DB.
        :type device_id: str
        :param data: New device characteristics.
        :type data: dict
        """
        return self.plugin.update_device(device_id, data)

    @policy.authorize('devices', 'delete')
    def delete_device(self, device_id):
        """Delete specified device.

        :param device_id: ID of the device in Blazar DB.
        :type device_id: str
        """
        self.plugin.delete_device(device_id)

    @policy.authorize('devices', 'reallocate')
    def reallocate(self, device_id, data):
        """Exchange device from allocations."""
        return self.plugin.reallocate(device_id, data)

    @policy.authorize('devices', 'get_allocations')
    def list_allocations(self, query):
        """List all allocations on all devices.

        :param query: parameter to query allocations
        :type query: dict
        """
        ctx = context.current()
        detail = False

        if policy.enforce(ctx, 'admin', {}, do_raise=False):
            detail = True

        return self.plugin.list_allocations(query, detail=detail)

    @policy.authorize('devices', 'get_allocations')
    def get_allocations(self, device_id, query):
        """List all allocations on a specificied device.

        :param device_id: ID of the device in Blazar BDself.
        :type device_id: str
        :param query: parameters to query allocation
        :type query: dict
        """
        return self.plugin.get_allocations(device_id, query)

    @policy.authorize('devices', 'get_resource_properties')
    def list_resource_properties(self, query):
        """List resource properties for devices."""
        return self.plugin.list_resource_properties(query)

    @policy.authorize('devices', 'patch_resource_properties')
    def update_resource_property(self, property_name, data):
        """Update a device resource property."""
        return self.plugin.update_resource_property(property_name, data)
