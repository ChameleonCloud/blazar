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
from blazar.utils.openstack import exceptions as os_exceptions
from blazar.utils.openstack import keystone

CONF = cfg.CONF


class TestCKClient(tests.TestCase):
    """Which credential the wrapper authenticates with.

    Choosing between the service user, the requesting user and a trust is
    the main thing this wrapper does, and blazar.utils.trusts depends on
    all three.
    """

    def setUp(self):
        super(TestCKClient, self).setUp()
        self.cfg = self.useFixture(fixture.Config(CONF))
        self.cfg.config(os_admin_username='blazar-service')
        self.ks_client = self.patch(keystone_client, 'Client')

    def _session(self):
        return self.ks_client.call_args.kwargs['session']

    def test_defaults_to_the_service_user(self):
        keystone.BlazarKeystoneClient()

        auth = self._session().auth
        self.assertEqual('blazar-service', auth.auth_methods[0].username)

    def test_user_identity_reuses_the_callers_token(self):
        self.set_context(context.BlazarContext(
            user_id='u', project_id='p', auth_token='caller-token'))

        keystone.BlazarKeystoneClient(identity=base.Identity.USER)

        self.assertEqual('caller-token', self._session().get_token())

    def test_user_identity_without_a_token_is_rejected(self):
        self.set_context(context.BlazarContext(user_id='u', project_id='p'))

        self.assertRaises(os_exceptions.NoUserToken,
                          keystone.BlazarKeystoneClient,
                          identity=base.Identity.USER)

    def test_trust_id_scopes_to_the_trust(self):
        # trusts.delete_trust() and create_ctx_from_trust() rely on this.
        keystone.BlazarKeystoneClient(
            identity=base.Identity.TRUST, trust_id='trust-id')

        auth = self._session().auth
        self.assertEqual('trust-id', auth.trust_id)
        self.assertIsNone(auth.project_name)

    def test_version_defaults_to_the_configured_one(self):
        keystone.BlazarKeystoneClient()

        self.assertEqual(CONF.keystone_client_version,
                         self.ks_client.call_args.kwargs['version'])
