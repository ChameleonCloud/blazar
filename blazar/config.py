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


from oslo_config import cfg
from oslo_log import log as logging


cli_opts = [
    cfg.HostAddressOpt('host', default='0.0.0.0',
                       help='Name of this node. This can be an opaque '
                            'identifier. It is not necessarily a hostname, '
                            'FQDN, or IP address. However, the node name must '
                            'be valid within an AMQP key, and if using '
                            'ZeroMQ, a valid hostname, FQDN, or IP address'),
    cfg.BoolOpt('log_exchange', default=False,
                help='Log request/response exchange details: environ, '
                     'headers and bodies'),
]

os_opts = [
    cfg.StrOpt('os_auth_protocol',
               default='http',
               help='Protocol used to access OpenStack Identity service'),
    cfg.HostAddressOpt('os_auth_host',
                       default='127.0.0.1',
                       help='IP or hostname of machine on which OpenStack '
                            'Identity service is located'),
    cfg.StrOpt('os_auth_port',
               default='35357',
               help='Port of OpenStack Identity service.'),
    cfg.StrOpt('os_auth_prefix',
               default='',
               help='Prefix of URL to access OpenStack Identity service.'),
    cfg.StrOpt('os_admin_username',
               default='admin',
               help='This OpenStack user is used to treat trusts. '
                    'The user must have admin role in <os_admin_project_name> '
                    'project.'),
    cfg.StrOpt('os_admin_password',
               default='blazar',
               help='Password of the admin user to treat trusts.'),
    cfg.StrOpt('os_admin_project_name',
               default='admin',
               help='Name of project where the user is admin.'),
    cfg.StrOpt('os_auth_version',
               default='v2.0',
               help='Blazar uses API v3 to allow trusts using.'),
]

CONF = cfg.CONF
CONF.register_cli_opts(cli_opts)
CONF.register_opts(os_opts)
logging.register_options(cfg.CONF)
