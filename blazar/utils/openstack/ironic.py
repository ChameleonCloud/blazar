# Copyright (c) 2020 University of Chicago
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

from ironicclient import client as ironic_client
from keystoneauth1 import identity
from keystoneauth1 import session
from oslo_config import cfg
from oslo_log import log as logging

ironic_opts = [
    cfg.StrOpt(
        'ironic_api_version',
        default='1',
        help='Ironic API version')
]

CONF = cfg.CONF
CONF.register_opts(ironic_opts, group='ironic')
LOG = logging.getLogger(__name__)


class BlazarIronicClient(object):

    def __init__(self):

        auth_url = "%s://%s:%s/%s" % (CONF.os_auth_protocol,
                                      CONF.os_auth_host,
                                      CONF.os_auth_port,
                                      CONF.os_auth_prefix)

        auth = identity.Password(
            auth_url=auth_url,
            username=CONF.os_admin_username,
            password=CONF.os_admin_password,
            project_name=CONF.os_admin_project_name,
            project_domain_name=CONF.os_admin_project_domain_name,
            user_domain_name=CONF.os_admin_user_domain_name)
        sess = session.Session(auth=auth)

        self.ironic = ironic_client.Client(
            CONF.ironic.ironic_api_version,
            session=sess,
            region_name=CONF.os_region_name
            )
