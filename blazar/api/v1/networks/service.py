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
from blazar.manager.networks import rpcapi as manager_rpcapi
from blazar import policy
from blazar.utils import trusts


class API(object):
    def __init__(self):
        self.manager_rpcapi = manager_rpcapi.ManagerRPCAPI()

    @policy.authorize('networks', 'get')
    def get_networks(self):
        """List all existing networks."""
        return self.manager_rpcapi.list_networks()

    @policy.authorize('networks', 'post')
    @trusts.use_trust_auth()
    def create_network(self, data):
        """Create new network.

        :param data: New network characteristics.
        :type data: dict
        """

        return self.manager_rpcapi.create_network(data)

    @policy.authorize('networks', 'get')
    def get_network(self, network_id):
        """Get network by its ID.

        :param network_id: ID of the network in Blazar DB.
        :type network_id: str
        """
        return self.manager_rpcapi.get_network(network_id)

    @policy.authorize('networks', 'put')
    def update_network(self, network_id, data):
        """Update network. Only name changing may be proceeded.

        :param network_id: ID of the network in Blazar DB.
        :type network_id: str
        :param data: New network characteristics.
        :type data: dict
        """
        return self.manager_rpcapi.update_network(network_id, data)

    @policy.authorize('networks', 'delete')
    def delete_network(self, network_id):
        """Delete specified network.

        :param network_id: ID of the network in Blazar DB.
        :type network_id: str
        """
        self.manager_rpcapi.delete_network(network_id)

    @policy.authorize('networks', 'get_allocations')
    def list_allocations(self, query):
        """List all allocations on all network segments.

        :param query: parameter to query allocations
        :type query: dict
        """
        ctx = context.current()
        detail = False

        if policy.enforce(ctx, 'admin', {}, do_raise=False):
            detail = True

        return self.manager_rpcapi.list_allocations(query, detail=detail)

    @policy.authorize('networks', 'get_allocations')
    def get_allocations(self, network_id, query):
        """List all allocations on a specificied network segment.

        :param network_id: ID of the network segment in Blazar BDself.
        :type network_id: str
        :param query: parameters to query allocation
        :type query: dict
        """
        return self.manager_rpcapi.get_allocations(network_id, query)

    @policy.authorize('networks', 'get_resource_properties')
    def list_resource_properties(self, query):
        """List resource properties for networks."""
        return self.manager_rpcapi.list_resource_properties(query)

    @policy.authorize('networks', 'patch_resource_properties')
    def update_resource_property(self, property_name, data):
        """Update a network resource property."""
        return self.manager_rpcapi.update_resource_property(property_name,
                                                            data)
