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

from keystoneauth1.identity import v3
from keystoneauth1 import session
import netaddr
from oslo_config import cfg

from blazar import context
from blazar.manager import exceptions

CONF = cfg.CONF


def get_os_auth_host(conf):
    """Description

    Returns os_auth_host from conf, surrounded by brackets if IPv6.
    """
    os_auth_host = conf.os_auth_host
    if netaddr.valid_ipv6(os_auth_host, netaddr.core.INET_PTON):
        os_auth_host = "[%s]" % os_auth_host
    return os_auth_host


def client_kwargs(**_kwargs):
    kwargs = _kwargs.copy()

    ctx = kwargs.pop('ctx', None)
    username = kwargs.pop('username',
                          CONF.os_admin_username)
    password = kwargs.pop('password',
                          CONF.os_admin_password)
    project_name = kwargs.pop('project_name',
                              CONF.os_admin_project_name)
    user_domain_name = kwargs.pop('user_domain_name',
                                  CONF.os_admin_user_domain_name)
    project_domain_name = kwargs.pop('project_domain_name',
                                     CONF.os_admin_project_domain_name)
    trust_id = kwargs.pop('trust_id', None)
    auth_url = kwargs.pop('auth_url', None)
    region_name = kwargs.pop('region_name', CONF.os_region_name)
    if ctx is None:
        try:
            ctx = context.current()
        except RuntimeError:
            pass
    if ctx is not None:
        kwargs.setdefault('global_request_id', ctx.global_request_id)

    if auth_url is None:
        auth_url = "%s://%s:%s/%s/%s" % (CONF.os_auth_protocol,
                                         get_os_auth_host(CONF),
                                         CONF.os_auth_port,
                                         CONF.os_auth_prefix,
                                         CONF.os_auth_version)

    auth_kwargs = dict(
        auth_url=auth_url,
        username=username,
        password=password,
        user_domain_name=user_domain_name,
        project_domain_name=project_domain_name
    )

    if trust_id is not None:
        auth_kwargs.update(trust_id=trust_id)
    else:
        auth_kwargs.update(project_name=project_name)

    auth = v3.Password(**auth_kwargs)
    sess = session.Session(auth=auth)

    kwargs.setdefault('session', sess)
    kwargs.setdefault('region_name', region_name)
    return kwargs


def client_user_kwargs(**_kwargs):
    kwargs = _kwargs.copy()

    auth_url = kwargs.pop('auth_url', None)
    region_name = kwargs.pop('region_name', CONF.os_region_name)

    if auth_url is None:
        auth_url = "%s://%s:%s/%s/%s" % (CONF.os_auth_protocol,
                                         get_os_auth_host(CONF),
                                         CONF.os_auth_port,
                                         CONF.os_auth_prefix,
                                         CONF.os_auth_version)

    ctx = context.current()
    auth_kwargs = {
        'auth_url': auth_url,
        'token': ctx.auth_token,
        'project_name': ctx.project_name,
        'project_domain_name': ctx.project_domain_name,
    }

    auth = v3.Token(**auth_kwargs)
    sess = session.Session(auth=auth)

    kwargs.setdefault('session', sess)
    kwargs.setdefault('region_name', region_name)
    return kwargs


def url_for(service_catalog, service_type, admin=False,
            endpoint_interface=None,
            os_region_name=None):
    """Description

    Gets url of the service to communicate through.
    service_catalog - dict contains info about specific OpenStack service
    service_type - OpenStack service type specification
    """
    if not endpoint_interface:
        endpoint_interface = 'public'
    if admin:
        endpoint_interface = 'admin'

    service = None
    for srv in service_catalog:
        if srv['type'] == service_type:
            service = srv

    if service:
        try:
            endpoints = service['endpoints']
        except KeyError:
            raise exceptions.EndpointsNotFound(
                "No endpoints for %s" % service['type'])
        if os_region_name:
            endpoints = [e for e in endpoints if e['region'] == os_region_name]
            if not endpoints:
                raise exceptions.EndpointsNotFound("No endpoints for %s in "
                                                   "region %s" %
                                                   (service['type'],
                                                    os_region_name))
        try:
            # if Keystone API v3 endpoints returned
            endpoint = [e for e in endpoints
                        if e['interface'] == endpoint_interface][0]
            return endpoint['url']
        except KeyError:
            # otherwise
            return endpoints[0]['%sURL' % endpoint_interface]
    else:
        raise exceptions.ServiceNotFound(
            'Service "%s" not found' % service_type)
