# Copyright (c) 2013 Mirantis Inc.
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

from blazar import manager
from blazar.utils import service


class ManagerRPCAPI(service.RPCClient):
    """Client side for the Manager RPC API.

    Used from other services to communicate with blazar-manager service.
    """
    def __init__(self):
        """Initiate RPC API client with needed topic and RPC version."""
        super(ManagerRPCAPI, self).__init__(manager.get_target())

    def create_api(self, app):
        return self.call_without_context('create_api', app=app)
