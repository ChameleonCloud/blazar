# Copyright (c) 2020 University of Chicago.
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

from . import base


class DummyPlugin(base.BasePlugin):
    def resource_type(self):
        return "dummy"

    def validate_create_params(self, data):
        return self.validate_data(data, ["name", "score"], [], "create")

    def validate_update_params(self, resource_id, data):
        return self.validate_data(data, ["name", "score"], [], "update")

    def poll_resource_failures(self):
        return [], []

    def notification_callback(self, event_type, payload):
        return {}

    def get_notification_event_types(self):
        return []
