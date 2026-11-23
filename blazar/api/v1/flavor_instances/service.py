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
from blazar.manager.flavor_instances import rpcapi as manager_rpcapi
from blazar import policy


class API(object):
    def __init__(self):
        self.manager_rpcapi = manager_rpcapi.ManagerRPCAPI()

    @policy.authorize('flavor_instances', 'get_availability')
    def get_flavor_availability(self, flavor_id, start_date, end_date):
        """Availability timelines for a flavor, or all flavors.

        Dates are strings in the plugin's AVAILABILITY_DATE_FORMAT.
        """
        ctx = context.current()
        return self.manager_rpcapi.get_availability(
            flavor_id, start_date, end_date, ctx.project_id)
