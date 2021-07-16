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

from blazar.plugins import base
from blazar import tests


class BasePluginDummy(base.BasePlugin):
    def get(self, resource_id):
        pass

    def reserve_resource(self, reservation_id, values):
        pass

    def list_allocations(self, query, detail=False):
        pass

    def query_allocations(self, resource_id_list, lease_id=None,
                          reservation_id=None):
        pass

    def allocation_candidates(self, lease_values):
        pass

    def update_reservation(self, reservation_id, values):
        pass

    def on_end(self, resource_id, lease=None):
        pass

    def on_start(self, resource_id, lease=None):
        pass


class BasePluginTestCase(tests.TestCase):
    def setUp(self):
        super(BasePluginTestCase, self).setUp()
        self.plugin = BasePluginDummy()

    def test__is_project_allowed(self):
        # No project restrictions
        project_id = "0ac67a48-e65c-11eb-ba80-0242ac130004"
        resource = {}
        self.assertTrue(self.plugin.is_project_allowed(project_id, resource))

        # Single project restriction
        resource = {
            "authorized_projects": "0ac67a48-e65c-11eb-ba80-0242ac130004"
        }
        project_id = "0ac67a48-e65c-11eb-ba80-0242ac130004"
        self.assertTrue(self.plugin.is_project_allowed(project_id, resource))
        project_id = "6bd9356e-e65c-11eb-ba80-0242ac130004"
        self.assertFalse(self.plugin.is_project_allowed(project_id, resource))

        resource = {
            "authorized_projects": "0ac67a48-e65c-11eb-ba80-0242ac130004,"
                                   "6bd9356e-e65c-11eb-ba80-0242ac130004"
        }
        project_id = "0ac67a48-e65c-11eb-ba80-0242ac130004"
        self.assertTrue(self.plugin.is_project_allowed(project_id, resource))
        project_id = "6bd9356e-e65c-11eb-ba80-0242ac130004"
        self.assertTrue(self.plugin.is_project_allowed(project_id, resource))
        project_id = "923cf8d0-e65c-11eb-ba80-0242ac130004"
        self.assertFalse(self.plugin.is_project_allowed(project_id, resource))
