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

import enum
import threading

from keystoneauth1.access import service_catalog as ks_service_catalog
from keystoneauth1.identity import v3
from keystoneauth1 import loading as ks_loading
from keystoneauth1 import plugin as ks_plugin
import netaddr
from oslo_config import cfg
from oslo_log import log as logging

from blazar import context
from blazar.utils.openstack import exceptions


CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class Identity(enum.Enum):
    """Whose credential a client authenticates with.

    Each client names its own default rather than sharing one constant,
    because the existing defaults differ: the nova client falls through
    to the requesting user, while the keystone, neutron and placement
    clients have only ever used Blazar's own credential.
    """

    #: Blazar's own credential, from the service's keystoneauth section.
    SERVICE = 'service'
    #: The token the caller authenticated to Blazar with, reused verbatim.
    USER = 'user'
    #: Blazar's own credential, scoped to a trust.
    TRUST = 'trust'


#: Service type each config group's endpoint is registered under in the
#: keystone catalog.
SERVICE_TYPES = {
    'identity': 'identity',
    'neutron': 'network',
    'nova': 'compute',
    'placement': 'placement',
}

#: Scope options that must be cleared when scoping to a trust; keystone
#: rejects a request scoped to more than one target.
_SCOPE_DESTS = (
    'project_id',
    'project_name',
    'project_domain_id',
    'project_domain_name',
    'domain_id',
    'domain_name',
    'system_scope',
)

_AUTH_OVERRIDES = (
    'auth_url',
    'username',
    'password',
    'project_name',
    'user_domain_name',
    'project_domain_name',
)

#: Interfaces tried, in order, when a group does not name its own.
DEFAULT_INTERFACES = ('internal',)

_SESSION_DEPRECATIONS = {
    'cafile': [cfg.DeprecatedOpt('cafile', group='DEFAULT')],
}

#: endpoint_type named one interface; valid_interfaces supersedes it with
#: a list, so oslo.config reparses the old value as the new type.
_ADAPTER_DEPRECATIONS = {
    'identity': {
        'valid_interfaces': [cfg.DeprecatedOpt('endpoint_type',
                                               group='DEFAULT')],
        'service_type': [cfg.DeprecatedOpt('identity_service',
                                           group='DEFAULT')],
    },
    'neutron': {
        'valid_interfaces': [cfg.DeprecatedOpt('endpoint_type',
                                               group='neutron')],
    },
    'nova': {
        'valid_interfaces': [cfg.DeprecatedOpt('endpoint_type',
                                               group='nova')],
        'service_type': [cfg.DeprecatedOpt('compute_service', group='nova')],
    },
    'placement': {
        'valid_interfaces': [cfg.DeprecatedOpt('endpoint_type',
                                               group='placement')],
    },
}

_legacy_warned = False

#: Sessions for Blazar's own credential, one per service group. Caching
#: stops at the session deliberately: global_request_id is an adapter
#: argument rather than a session one, so caching a whole client would
#: pin the first request's id onto every later request.
_sessions = {}
_sessions_lock = threading.Lock()


def _adapter_opts(group):
    """Adapter options for `group`, carrying Blazar's defaults.

    Defaults go on the options themselves rather than through
    conf.set_default(), so that they survive ConfigOpts.reset() and reach
    the generated sample config.
    """
    options = [
        opt for opt in ks_loading.get_adapter_conf_options(
            # The adapter's own `interface` option is mutually exclusive
            # with valid_interfaces, which endpoint_type now maps onto;
            # registering only one of them makes the clash unreachable.
            include_deprecated=False,
            deprecated_opts=_ADAPTER_DEPRECATIONS[group])
        # Blazar pins client and microversions itself, so negotiating a
        # version through the adapter would have no effect.
        if opt.dest not in ('version', 'min_version', 'max_version')]
    cfg.set_defaults(options,
                     service_type=SERVICE_TYPES[group],
                     valid_interfaces=list(DEFAULT_INTERFACES))
    return options


#: Registered so that blazar.opts can hand the generator a described
#: group rather than a bare name.
OPT_GROUPS = {
    group: cfg.OptGroup(
        group,
        title='%s service options' % service_type.capitalize(),
        help='How Blazar authenticates to, and locates, the %s service.'
             % service_type)
    for group, service_type in SERVICE_TYPES.items()
}


def register_keystoneauth_opts():
    """Register keystoneauth's auth, session and adapter options."""
    for group in SERVICE_TYPES:
        CONF.register_group(OPT_GROUPS[group])
        ks_loading.register_auth_conf_options(CONF, group)
        ks_loading.register_session_conf_options(
            CONF, group, deprecated_opts=_SESSION_DEPRECATIONS)
        CONF.register_opts(_adapter_opts(group), group=group)


register_keystoneauth_opts()


def adapter_kwargs(group):
    """Endpoint-selection kwargs for `group`, for a keystoneauth Adapter."""
    return dict(
        service_type=CONF[group].service_type,
        interface=CONF[group].valid_interfaces,
        endpoint_override=CONF[group].endpoint_override,
        region_name=CONF[group].region_name or CONF.os_region_name)


def endpoint_kwargs(group):
    """Endpoint-selection kwargs for `group`, for clients predating adapters.

    novaclient and neutronclient take a single 'internalURL'-style string
    rather than keystoneauth's ordered list of interfaces, so only the
    first acceptable interface reaches them.
    """
    return dict(
        endpoint_type=CONF[group].valid_interfaces[0] + 'URL',
        endpoint_override=CONF[group].endpoint_override)


def keystoneauth_conf_options(group):
    """The keystoneauth options `group` accepts.

    blazar.opts exports these so they reach blazar.conf.sample. Only the
    password plugin's options are listed: a plugin's own options are
    registered lazily, when load_auth_from_conf_options() reads auth_type,
    so the generator cannot enumerate every installed plugin.
    """
    return (
        ks_loading.get_auth_common_conf_options()
        + ks_loading.get_session_conf_options(
            deprecated_opts=_SESSION_DEPRECATIONS)
        + _adapter_opts(group)
        + ks_loading.get_auth_plugin_conf_options('password'))


def get_os_auth_host(conf):
    """Description

    Returns os_auth_host from conf, surrounded by brackets if IPv6.
    """
    os_auth_host = conf.os_auth_host
    if netaddr.valid_ipv6(os_auth_host, netaddr.core.INET_PTON):
        os_auth_host = "[%s]" % os_auth_host
    return os_auth_host


def legacy_auth_url():
    """Assemble the identity URL from the [DEFAULT] os_auth_* options.

    os_auth_prefix defaults to the empty string, so the prefix and version
    components are only appended when they are actually set. Interpolating
    them unconditionally produces a doubled separator, e.g.
    "http://127.0.0.1:5000//v3".
    """
    auth_url = "%s://%s:%s" % (CONF.os_auth_protocol,
                               get_os_auth_host(CONF),
                               CONF.os_auth_port)
    for part in (CONF.os_auth_prefix, CONF.os_auth_version):
        if part:
            auth_url = "%s/%s" % (auth_url, part.strip('/'))
    return auth_url


def _warn_legacy_once():
    global _legacy_warned
    if _legacy_warned:
        return
    _legacy_warned = True
    LOG.warning(
        'Authenticating with the deprecated [DEFAULT] os_auth_* and '
        'os_admin_* options. Configure a keystoneauth section per service '
        'instead - see the auth_type option in the '
        '[identity], [nova], [neutron] and [placement] groups. The legacy '
        'options will be removed in a future release.')


def _legacy_auth(trust_id=None, **overrides):
    """Build a password plugin from the deprecated [DEFAULT] options."""
    _warn_legacy_once()
    auth_kwargs = dict(
        auth_url=legacy_auth_url(),
        username=CONF.os_admin_username,
        password=CONF.os_admin_password,
        user_domain_name=CONF.os_admin_user_domain_name,
        project_domain_name=CONF.os_admin_project_domain_name,
    )
    if trust_id is not None:
        auth_kwargs['trust_id'] = trust_id
    else:
        auth_kwargs['project_name'] = CONF.os_admin_project_name
    auth_kwargs.update(overrides)
    return v3.Password(**auth_kwargs)


def _loaded_auth(group, trust_id=None, **overrides):
    """Build an auth plugin from the keystoneauth options of `group`."""
    loader = ks_loading.get_plugin_loader(CONF[group].auth_type)
    known = {opt.dest for opt in loader.get_options()}

    if trust_id is not None:
        if 'trust_id' not in known:
            raise exceptions.TrustScopeUnsupported(
                auth_type=CONF[group].auth_type, group=group)
        # None suppresses the configured value; omitting it lets it win.
        for dest in _SCOPE_DESTS:
            if dest in known:
                overrides[dest] = None
        overrides['trust_id'] = trust_id

    overrides = {k: v for k, v in overrides.items() if k in known}
    auth = ks_loading.load_auth_from_conf_options(CONF, group, **overrides)
    if auth is None:
        # load_auth_from_conf_options() returns None for an unset
        # auth_type; a Session must never be given it.
        raise exceptions.ServiceAuthNotConfigured(group=group)
    return auth


def service_auth(group, trust_id=None, **overrides):
    """Return the auth plugin for `group`'s own service credential."""
    if CONF[group].auth_type:
        return _loaded_auth(group, trust_id=trust_id, **overrides)
    return _legacy_auth(trust_id=trust_id, **overrides)


def identity_auth_url():
    """The identity endpoint, as configured.

    Blazar hands this to external services, which cannot resolve it from
    Blazar's own view of the catalog, so it comes from configuration
    rather than from a request context.
    """
    return getattr(service_auth('identity'), 'auth_url', None)


def _new_session(group, auth):
    """A session for `group`, honouring its TLS and timeout options."""
    return ks_loading.load_session_from_conf_options(CONF, group, auth=auth)


def service_session(group):
    """The session shared by everything using Blazar's own credential.

    Reusing one session means one token per service rather than one per
    client. keystoneauth refreshes an expiring token itself, under a lock
    held by the auth plugin, so the result is safe to share across threads.
    """
    try:
        return _sessions[group]
    except KeyError:
        pass
    with _sessions_lock:
        if group not in _sessions:
            _sessions[group] = _new_session(group, service_auth(group))
        return _sessions[group]


def reset():
    """Discard process-wide state. Configuration changes invalidate it."""
    global _legacy_warned
    with _sessions_lock:
        _sessions.clear()
        _legacy_warned = False


def _context_for(identity, trust_id, ctx):
    """Check `identity` is usable and return the context it will use.

    Raises rather than quietly falling back. A client built with no
    usable credential, or one that accepts a trust and then ignores it,
    authenticates as the wrong principal and fails far from its cause.
    """
    if trust_id is not None and identity is not Identity.TRUST:
        raise exceptions.IdentityConflict(
            requested=identity.value,
            reason='a trust_id was supplied, which only identity=trust uses')
    if identity is Identity.TRUST and trust_id is None:
        raise exceptions.IdentityConflict(
            requested=identity.value, reason='no trust_id was supplied')

    if ctx is None:
        try:
            ctx = context.current()
        except RuntimeError:
            ctx = None

    if identity is Identity.USER and (ctx is None or not ctx.auth_token):
        raise exceptions.NoUserToken()

    return ctx


class _ContextAuth(ks_plugin.BaseAuthPlugin):
    """Reuses the caller's token and the catalog already on the context.

    The token is passed straight on to the next service rather than
    exchanged for a new one, which is what lets application credentials
    and other non-password auth types work. The endpoint comes from the
    service catalog the caller's own token was issued with, so acting as
    the requesting user costs no extra call to keystone.
    """

    def __init__(self, auth_token, service_catalog):
        super(_ContextAuth, self).__init__()
        self._auth_token = auth_token
        self._catalog = ks_service_catalog.ServiceCatalogV3(
            service_catalog or [])

    def get_token(self, session, **kwargs):
        return self._auth_token

    def get_endpoint(self, session, service_type=None, interface=None,
                     region_name=None, service_name=None, **kwargs):
        if interface is ks_plugin.AUTH_INTERFACE:
            return None
        return self._catalog.url_for(service_type=service_type,
                                     interface=interface,
                                     region_name=region_name,
                                     service_name=service_name)


def _user_auth(ctx):
    """An auth plugin acting as the caller. See _ContextAuth."""
    catalog = ctx.service_catalog
    if hasattr(catalog, 'normalize_catalog'):
        # An API request puts a plain list on the context, but
        # trusts.create_ctx_from_trust() puts a keystoneauth catalog.
        catalog = catalog.normalize_catalog()
    return _ContextAuth(ctx.auth_token, catalog)


def client_kwargs(group, identity=Identity.SERVICE, **kwargs):
    """Session and adapter kwargs for a client contacting `group`.

    :param group: config group holding the keystoneauth options for the
        service being contacted, e.g. 'nova'.
    :param identity: whose credential to authenticate with; see Identity.
    """
    ctx = kwargs.pop('ctx', None)
    trust_id = kwargs.pop('trust_id', None)
    region_name = kwargs.pop(
        'region_name',
        CONF[group].region_name or CONF.os_region_name)
    overrides = {name: kwargs.pop(name)
                 for name in _AUTH_OVERRIDES if name in kwargs}

    ctx = _context_for(identity, trust_id, ctx)
    if ctx is not None:
        kwargs.setdefault('global_request_id', ctx.global_request_id)

    # Only the plain service credential is shared. A trust, a caller's
    # token, or credentials this caller chose each belong to one request.
    if identity is Identity.USER:
        session = _new_session(group, _user_auth(ctx))
    elif identity is Identity.TRUST:
        session = _new_session(group, service_auth(group, trust_id=trust_id))
    elif overrides:
        session = _new_session(group, service_auth(group, **overrides))
    else:
        session = service_session(group)

    kwargs.setdefault('session', session)
    kwargs.setdefault('region_name', region_name)
    return kwargs
