# Copyright (c) 2013 Bull.
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
from blazar.manager.service import get_plugins
from blazar import policy
from blazar.utils import trusts


class API(object):
    def __init__(self):
        self.plugin = get_plugins()["physical:host"]

    @policy.authorize('oshosts', 'get')
    def get_computehosts(self, query):
        """List all existing computehosts."""
        return self.plugin.list_computehosts(query=query)

    @policy.authorize('oshosts', 'post')
    @trusts.use_trust_auth()
    def create_computehost(self, data):
        """Create new computehost.

        :param data: New computehost characteristics.
        :type data: dict
        """

        return self.plugin.create_computehost(data)

    @policy.authorize('oshosts', 'get')
    def get_computehost(self, host_id):
        """Get computehost by its ID.

        :param host_id: ID of the computehost in Blazar DB.
        :type host_id: str
        """
        return self.plugin.get_computehost(host_id)

    @policy.authorize('oshosts', 'put')
    def update_computehost(self, host_id, data):
        """Update computehost. Only name changing may be proceeded.

        :param host_id: ID of the computehost in Blazar DB.
        :type host_id: str
        :param data: New computehost characteristics.
        :type data: dict
        """
        return self.plugin.update_computehost(host_id, data)

    @policy.authorize('oshosts', 'delete')
    def delete_computehost(self, host_id):
        """Delete specified computehost.

        :param host_id: ID of the computehost in Blazar DB.
        :type host_id: str
        """
        self.plugin.delete_computehost(host_id)

    @policy.authorize('oshosts', 'get_allocations')
    def list_allocations(self, query):
        """List all allocations on all computehosts.

        :param query: parameters to query allocations
        :type query: dict
        """
        ctx = context.current()
        detail = False

        if policy.enforce(ctx, 'admin', {}, do_raise=False):
            detail = True

        return self.plugin.list_allocations(query, detail=detail)

    @policy.authorize('oshosts', 'get_allocations')
    def get_allocations(self, host_id, query):
        """List all allocations on a specified computehost.

        :param host_id: ID of the computehost in Blazar DB.
        :type host_id: str
        :param query: parameters to query allocations
        :type query: dict
        """
        return self.plugin.get_allocations(host_id, query)

    def reallocate(self, host_id, data):
        """Exchange host from allocations."""
        return self.plugin.reallocate_computehost(host_id, data)

    @policy.authorize('oshosts', 'get_resource_properties')
    def list_resource_properties(self, query):
        """List resource properties for hosts."""
        return self.plugin.list_resource_properties(query)

    @policy.authorize('oshosts', 'patch_resource_properties')
    def update_resource_property(self, property_name, data):
        """Update a host resource property."""
        return self.plugin.update_resource_property(property_name, data)
