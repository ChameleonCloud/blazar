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

import concurrent.futures as futures
import threading
from unittest import mock

from keystoneauth1.identity.v3 import base as ks_v3_base
from oslo_config import cfg
from oslo_config import fixture

from blazar import context
from blazar import tests
from blazar.utils.openstack import base
from blazar.utils.openstack import exceptions as os_exceptions

CONF = cfg.CONF


class TestLegacyAuthUrl(tests.TestCase):
    """The [DEFAULT] os_auth_* options must assemble a well-formed URL."""

    def setUp(self):
        super(TestLegacyAuthUrl, self).setUp()
        self.cfg = self.useFixture(fixture.Config(CONF))
        self.cfg.config(os_auth_protocol='http')
        self.cfg.config(os_auth_host='127.0.0.1')
        self.cfg.config(os_auth_port='5000')

    def test_empty_prefix_does_not_double_the_separator(self):
        self.cfg.config(os_auth_prefix='')
        self.cfg.config(os_auth_version='v3')

        self.assertEqual('http://127.0.0.1:5000/v3', base.legacy_auth_url())

    def test_prefix_is_appended_when_set(self):
        self.cfg.config(os_auth_prefix='identity')
        self.cfg.config(os_auth_version='v3')

        self.assertEqual('http://127.0.0.1:5000/identity/v3',
                         base.legacy_auth_url())

    def test_surrounding_slashes_are_not_doubled(self):
        self.cfg.config(os_auth_prefix='/identity/')
        self.cfg.config(os_auth_version='/v3')

        self.assertEqual('http://127.0.0.1:5000/identity/v3',
                         base.legacy_auth_url())

    def test_empty_version_is_omitted(self):
        self.cfg.config(os_auth_prefix='identity')
        self.cfg.config(os_auth_version='')

        self.assertEqual('http://127.0.0.1:5000/identity',
                         base.legacy_auth_url())

    def test_ipv6_host_is_bracketed(self):
        self.cfg.config(os_auth_host='::1')
        self.cfg.config(os_auth_prefix='')
        self.cfg.config(os_auth_version='v3')

        self.assertEqual('http://[::1]:5000/v3', base.legacy_auth_url())

    def test_https_protocol_is_honoured(self):
        self.cfg.config(os_auth_protocol='https')
        self.cfg.config(os_auth_prefix='')
        self.cfg.config(os_auth_version='v3')

        self.assertEqual('https://127.0.0.1:5000/v3', base.legacy_auth_url())


def _username_of(auth):
    """Username, for either plugin shape.

    Options loaded from config produce a generic plugin that performs
    version discovery; the deprecated [DEFAULT] options build a v3
    plugin directly. They expose their parameters differently.
    """
    if hasattr(auth, '_username'):
        return auth._username
    return auth.auth_methods[0].username


def _scope_of(auth):
    """Scope targets, for either plugin shape. See _username_of().

    project_domain_* are not listed: they qualify a project name rather
    than being a scope target of their own, so keystone's mutual-exclusion
    check ignores them.
    """
    names = ('trust_id', 'project_name', 'project_id',
             'domain_name', 'domain_id')
    generic = hasattr(auth, '_trust_id')
    return dict(
        (name, getattr(auth, ('_' + name) if generic else name))
        for name in names)


class TestServiceAuth(tests.TestCase):
    """Credentials come from the service's own keystoneauth section."""

    AUTH_URL = 'https://keystone.example.org:5000/v3'

    def setUp(self):
        super(TestServiceAuth, self).setUp()
        self.cfg = self.useFixture(fixture.Config(CONF))

    def _configure(self, group, **overrides):
        self.register_auth_opts(group)
        params = dict(auth_type='password',
                      auth_url=self.AUTH_URL,
                      username='blazar',
                      password='secret',
                      project_name='services',
                      user_domain_name='Default',
                      project_domain_name='Default')
        params.update(overrides)
        self.cfg.config(group=group, **params)

    def _use_legacy_options(self):
        self.cfg.config(os_auth_protocol='https')
        self.cfg.config(os_auth_host='legacy.example.org')
        self.cfg.config(os_auth_port='5000')
        self.cfg.config(os_auth_prefix='')
        self.cfg.config(os_auth_version='v3')
        self.cfg.config(os_admin_username='legacy-user')

    def test_configured_section_is_used(self):
        self._configure('nova')

        auth = base.service_auth('nova')

        self.assertEqual(self.AUTH_URL, auth.auth_url)
        self.assertEqual('blazar', _username_of(auth))

    def test_configured_section_wins_over_legacy_options(self):
        self._use_legacy_options()
        self._configure('nova')

        auth = base.service_auth('nova')

        self.assertEqual(self.AUTH_URL, auth.auth_url)
        self.assertEqual('blazar', _username_of(auth))

    def test_falls_back_to_legacy_options(self):
        self._use_legacy_options()

        auth = base.service_auth('nova')

        self.assertEqual('https://legacy.example.org:5000/v3', auth.auth_url)
        self.assertEqual('legacy-user', _username_of(auth))

    def test_legacy_fallback_warns_once(self):
        self._use_legacy_options()

        with mock.patch.object(base.LOG, 'warning') as warning:
            base.service_auth('nova')
            base.service_auth('neutron')

        self.assertEqual(1, warning.call_count)

    def test_configured_section_does_not_warn(self):
        self._configure('nova')

        with mock.patch.object(base.LOG, 'warning') as warning:
            base.service_auth('nova')

        self.assertFalse(warning.called)

    def test_caller_overrides_beat_configuration(self):
        self._configure('nova')

        auth = base.service_auth('nova', username='someone-else')

        self.assertEqual('someone-else', _username_of(auth))


class TestTrustScoping(tests.TestCase):
    """Trust scope must replace project scope, never accompany it."""

    def setUp(self):
        super(TestTrustScoping, self).setUp()
        self.cfg = self.useFixture(fixture.Config(CONF))

    def _assert_trust_scoped_only(self, auth):
        scope = _scope_of(auth)
        self.assertEqual('trust-id', scope.pop('trust_id'))
        self.assertEqual(
            dict((name, None) for name in scope), scope,
            'keystone rejects a request scoped to more than one target, so '
            'the configured project scope must be cleared')

    def test_configured_section_scopes_to_trust_only(self):
        self.register_auth_opts('nova')
        self.cfg.config(group='nova',
                        auth_type='password',
                        auth_url='https://keystone.example.org:5000/v3',
                        username='blazar',
                        password='secret',
                        project_name='services',
                        user_domain_name='Default',
                        project_domain_name='Default')

        auth = base.service_auth('nova', trust_id='trust-id')

        self._assert_trust_scoped_only(auth)

    def test_legacy_options_scope_to_trust_only(self):
        self.cfg.config(os_admin_project_name='services')

        auth = base.service_auth('nova', trust_id='trust-id')

        self._assert_trust_scoped_only(auth)

    def test_plugin_without_trust_support_is_rejected(self):
        # admin_token has no trust_id option, so silently dropping the
        # trust would authenticate with the wrong scope.
        self.register_auth_opts('nova', plugin='admin_token')
        self.cfg.config(group='nova', auth_type='admin_token')

        self.assertRaises(os_exceptions.TrustScopeUnsupported,
                          base.service_auth, 'nova', trust_id='trust-id')


class TestSessionCaching(tests.TestCase):
    """One token per service, not one per client."""

    def setUp(self):
        super(TestSessionCaching, self).setUp()
        self.cfg = self.useFixture(fixture.Config(CONF))

    def test_session_is_shared_between_clients(self):
        first = base.client_kwargs('nova')['session']
        second = base.client_kwargs('nova')['session']

        self.assertIs(first, second)

    def test_each_service_gets_its_own_session(self):
        self.assertIsNot(base.client_kwargs('nova')['session'],
                         base.client_kwargs('neutron')['session'])

    def test_one_token_is_fetched_for_many_clients(self):
        # Patch the v3 plugin the deprecated options build, not the base
        # class: v3.Auth overrides get_auth_ref, so patching the base
        # would let a real token request through.
        with mock.patch.object(ks_v3_base.Auth, 'get_auth_ref') as get_ref:
            get_ref.return_value = mock.Mock(
                auth_token='token',
                **{'will_expire_soon.return_value': False})
            session = base.client_kwargs('nova')['session']
            for _ in range(10):
                base.client_kwargs('nova')
                session.get_token()

        self.assertEqual(1, get_ref.call_count)

    def _trust_session(self):
        return base.client_kwargs(
            'nova', base.Identity.TRUST, trust_id='trust-id')['session']

    def test_trust_scoped_session_is_not_shared(self):
        shared = base.client_kwargs('nova')['session']
        scoped = self._trust_session()

        self.assertIsNot(shared, scoped)
        self.assertIsNot(scoped, self._trust_session())

    def test_caller_supplied_credentials_are_not_shared(self):
        shared = base.client_kwargs('nova')['session']
        scoped = base.client_kwargs('nova',
                                    username='someone-else')['session']

        self.assertIsNot(shared, scoped)

    def test_reset_discards_sessions(self):
        first = base.client_kwargs('nova')['session']
        base.reset()

        self.assertIsNot(first, base.client_kwargs('nova')['session'])

    def test_concurrent_callers_share_one_session(self):
        # ReservationPool.add_computehost() builds clients from a thread
        # pool, so the cache must not hand out one session per thread.
        started = threading.Barrier(8)

        def build(_):
            started.wait()
            return base.client_kwargs('nova')['session']

        with futures.ThreadPoolExecutor(max_workers=8) as pool:
            sessions = list(pool.map(build, range(8)))

        self.assertEqual(1, len(set(id(s) for s in sessions)))


class TestIdentityIsExplicit(tests.TestCase):
    """A client is never built with the wrong principal, quietly."""

    def setUp(self):
        super(TestIdentityIsExplicit, self).setUp()
        self.cfg = self.useFixture(fixture.Config(CONF))

    def test_trust_id_without_trust_identity_is_rejected(self):
        # Accepting a trust and then authenticating as the service user
        # would act on the wrong principal's behalf.
        self.assertRaises(os_exceptions.IdentityConflict,
                          base.client_kwargs, 'neutron',
                          base.Identity.SERVICE, trust_id='trust-id')

    def test_trust_identity_without_trust_id_is_rejected(self):
        self.assertRaises(os_exceptions.IdentityConflict,
                          base.client_kwargs, 'nova', base.Identity.TRUST)

    def test_user_identity_without_a_context_is_rejected(self):
        self.assertRaises(os_exceptions.NoUserToken,
                          base.client_kwargs, 'nova', base.Identity.USER)

    def test_user_identity_without_a_token_is_rejected(self):
        self.set_context(context.BlazarContext(user_id='u', project_id='p'))

        self.assertRaises(os_exceptions.NoUserToken,
                          base.client_kwargs, 'nova', base.Identity.USER)

    def test_user_identity_reuses_the_callers_token(self):
        self.set_context(context.BlazarContext(
            user_id='u', project_id='p', auth_token='caller-token'))

        session = base.client_kwargs('nova', base.Identity.USER)['session']

        self.assertEqual('caller-token', session.get_token())

    def test_user_identity_takes_endpoints_from_the_context_catalog(self):
        # The caller's own catalog is reused, so acting as the requesting
        # user costs no extra round trip to keystone.
        self.set_context(context.BlazarContext(
            user_id='u', project_id='p', auth_token='caller-token',
            service_catalog=[{
                'type': 'compute',
                'name': 'nova',
                'endpoints': [{'interface': 'internal',
                               'region': 'RegionOne',
                               'url': 'https://nova.example.org/v2.1'}]}]))

        session = base.client_kwargs('nova', base.Identity.USER)['session']

        self.assertEqual(
            'https://nova.example.org/v2.1',
            session.get_endpoint(service_type='compute',
                                 interface='internal'))

    def test_user_identity_needs_no_keystone_call(self):
        self.set_context(context.BlazarContext(
            user_id='u', project_id='p', auth_token='caller-token'))

        with mock.patch.object(ks_v3_base.Auth, 'get_auth_ref') as get_ref:
            base.client_kwargs('nova', base.Identity.USER)

        self.assertFalse(get_ref.called)
