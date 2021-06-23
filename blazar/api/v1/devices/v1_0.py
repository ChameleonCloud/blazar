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

from blazar.api.v1.devices import service
from blazar.api.v1 import utils as api_utils
from blazar.api.v1 import validation
from blazar import utils


def get_rest():
    """Return Rest app"""
    return rest


rest = api_utils.Rest('device_v1_0', __name__, url_prefix='/v1/devices')
_api = utils.LazyProxy(service.API)


# devices operations

@rest.get('')
def devices_list(req):
    """List all existing devices."""
    return api_utils.render(devices=_api.get_devices())


@rest.post('')
def devices_create(req, data):
    """Create new device."""
    return api_utils.render(device=_api.create_device(data))


@rest.get('/<device_id>')
@validation.check_exists(_api.get_device, device_id='device_id')
def devices_get(req, device_id):
    """Get device by its ID."""
    return api_utils.render(device=_api.get_device(device_id))


@rest.put('/<device_id>')
@validation.check_exists(_api.get_device, device_id='device_id')
def devices_update(req, device_id, data):
    """Update device. Only name changing may be proceeded."""
    if len(data) == 0:
        return api_utils.internal_error(status_code=400,
                                        descr="No data to update")
    else:
        return api_utils.render(device=_api.update_device(device_id, data))


@rest.delete('/<device_id>')
@validation.check_exists(_api.get_device, device_id='device_id')
def devices_delete(req, device_id):
    """Delete specified device."""
    _api.delete_device(device_id)
    return api_utils.render(status=200)


@rest.put('/<device_id>/allocation')
@validation.check_exists(_api.get_device, device_id='device_id')
def reallocate(req, device_id, data):
    """Exhange device in a lease."""
    return api_utils.render(allocation=_api.reallocate(device_id, data))

@rest.get('/allocations', query=True)
def allocations_list(req, query, detail=False):
    """List all allocations on all device segments."""
    return api_utils.render(allocations=_api.list_allocations(query))


@rest.get('/<device_id>/allocation')
@validation.check_exists(_api.get_device, device_id='device_id')
def allocations_get(req, device_id, query):
    """List all allocations on a specific device segment."""
    return api_utils.render(allocation=_api.get_allocations(device_id,
                                                            query))


@rest.get('/properties', query=True)
def resource_properties_list(req, query=None):
    """List device resource properties."""
    return api_utils.render(
        resource_properties=_api.list_resource_properties(query))


@rest.patch('/properties/<property_name>')
def resource_property_update(req, property_name, data):
    """Update a device resource property."""
    return api_utils.render(
        resource_property=_api.update_resource_property(property_name, data))
