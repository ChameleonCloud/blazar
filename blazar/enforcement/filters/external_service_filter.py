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

import datetime
import json
import requests

from blazar import context
from blazar.enforcement.filters import base_filter
from blazar import exceptions
from blazar.i18n import _
from blazar.utils.openstack import base
from blazar.utils.openstack.keystone import BlazarKeystoneClient

from oslo_config import cfg
from oslo_log import log as logging

LOG = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime.datetime):
            return str(o)

        return json.JSONEncoder.default(self, o)


class ExternalServiceUnsupportedHTTPResponse(exceptions.BlazarException):
    code = 400
    msg_fmt = _('External Service Filter returned a %(status)s http response. '
                'Only 204 and 403 responses are supported.')


class ExternalServiceFilterException(exceptions.NotAuthorized):
    code = 400
    msg_fmt = _('%(message)s')


class ExternalServiceFilter(base_filter.BaseFilter):

    enforcement_opts = [
        cfg.StrOpt(
            'external_service_endpoint',
            default=False,
            help='The url of the external service API. A value of -1 will '
                 'disabled the service.'),
        cfg.StrOpt(
            'external_service_token',
            default="",
            help='Authentication token for token based authentication.')
    ]

    def __init__(self, conf=None):
        super(ExternalServiceFilter, self).__init__(conf=conf)

    def get_headers(self):
        headers = {'Content-Type': 'application/json'}

        if self.external_service_token:
            headers['X-Auth-Token'] = (self.external_service_token)
        else:
            auth_url = "%s://%s:%s/%s" % (self.conf.os_auth_protocol,
                                          base.get_os_auth_host(self.conf),
                                          self.conf.os_auth_port,
                                          self.conf.os_auth_prefix)
            client = BlazarKeystoneClient(
                password=self.conf.os_admin_password,
                auth_url=auth_url,
                ctx=context.admin())

            headers['X-Auth-Token'] = client.auth_token

        return headers

    def post(self, path, body):
        url = self.external_service_endpoint

        if url[-1] == '/':
            url += path[1:]
        else:
            url += path

        body = json.dumps(body, cls=DateTimeEncoder)
        req = requests.post(url, headers=self.get_headers(), data=body)

        if req.status_code == 204:
            return True
        elif req.status_code == 403:
            raise ExternalServiceFilterException(
                message=req.json().get('message'))
        else:
            raise ExternalServiceUnsupportedHTTPResponse(
                status=req.status_code)

    def check_create(self, context, lease_values):
        if self.external_service_endpoint:
            path = '/v1/check-create'
            body = dict(context=context, lease=lease_values)

            self.post(path, body)

    def check_update(self, context, current_lease_values, new_lease_values):
        if self.external_service_endpoint:
            path = '/v1/check-update'
            body = dict(context=context, current_lease=current_lease_values,
                        lease=new_lease_values)

            self.post(path, body)

    def on_end(self, context, lease_values):
        if self.external_service_endpoint:
            path = '/v1/on-end'
            body = dict(context=context, lease=lease_values)

            self.post(path, body)
