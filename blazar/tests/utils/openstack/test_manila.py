# Copyright (c) 2026 University of Chicago
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

from manilaclient import client as manila_client
from oslo_config import cfg
from oslo_config import fixture

from blazar import tests
from blazar.utils.openstack import base
from blazar.utils.openstack import manila


class TestBlazarManilaClient(tests.TestCase):
    def setUp(self):
        super(TestBlazarManilaClient, self).setUp()
        self.cfg = self.useFixture(fixture.Config(cfg.CONF))
        self.patch(base, 'client_kwargs').return_value = {}
        self.client = self.patch(manila_client, 'Client')

    def test_client_endpoint_type_default(self):
        manila.BlazarManilaClient()

        self.assertEqual('internalURL',
                         self.client.call_args.kwargs['endpoint_type'])

    def test_client_endpoint_type(self):
        self.cfg.config(endpoint_type='public', group='manila')

        manila.BlazarManilaClient()

        self.assertEqual('publicURL',
                         self.client.call_args.kwargs['endpoint_type'])
