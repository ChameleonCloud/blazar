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

from keystoneclient import client as keystone_client
from oslo_config import cfg
from oslo_config import fixture

from blazar import context
from blazar import tests
from blazar.utils.openstack import base
from blazar.utils.openstack import keystone


class TestCKClient(tests.TestCase):
    """TODO: Update test class.

    This test originally tested functionality implemented in the
    third-party keystoneclient library, which is redundant. It should test
    primarily the branching b/w user and non-user authentication params, as
    that is the main function this wrapper serves.
    """

    def setUp(self):
        super(TestCKClient, self).setUp()
        self.cfg = self.useFixture(fixture.Config(cfg.CONF))
        self.patch(base, 'client_kwargs').return_value = {}
        self.client = self.patch(keystone_client, 'Client')

    def test_client_endpoint_type_default(self):
        keystone.BlazarKeystoneClient()

        self.assertEqual('internal',
                         self.client.call_args.kwargs['interface'])

    def test_client_endpoint_type(self):
        self.cfg.config(endpoint_type='public')

        keystone.BlazarKeystoneClient()

        self.assertEqual('public', self.client.call_args.kwargs['interface'])

    def test_client_as_user_endpoint_type(self):
        self.patch(context, 'current')
        self.patch(base, 'create_access_info')
        self.patch(base.access, 'AccessInfoPlugin')
        self.patch(base.session, 'Session')

        keystone.BlazarKeystoneClient(as_user=True)

        admin_client, user_client = self.client.call_args_list
        self.assertEqual('internal', admin_client.kwargs['interface'])
        self.assertEqual('internal', user_client.kwargs['interface'])
