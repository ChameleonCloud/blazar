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

from oslo_config import cfg

from blazar.manager import exceptions as manager_exceptions
from blazar.utils.openstack import base
from oslo_log import log as logging
from zunclient import client as zun_client
from zunclient import exceptions as zun_exception


zun_opts = [
    cfg.StrOpt(
        'zun_api_version',
        default='1',
        help='Zun API version'),
    cfg.StrOpt(
        'zun_api_microversion',
        default='1.22',
        help='Zun API microversion'),
    cfg.StrOpt(
        'endpoint_override',
        help='Zun endpoint URL to use')
]

CONF = cfg.CONF
CONF.register_opts(zun_opts, group='zun')

LOG = logging.getLogger(__name__)


class BlazarZunClient(object):
    """Client class for Zun service."""

    def __init__(self, **kwargs):
        client_kwargs = base.client_kwargs(**kwargs)
        client_kwargs.setdefault('os_zun_api_version',
                                 CONF.zun.zun_api_microversion)
        self.zun = zun_client.Client(
            CONF.zun.zun_api_version, **client_kwargs)

    def __getattr__(self, attr):
        return getattr(self.zun, attr)


class ZunClientWrapper(object):
    @property
    def zun(self):
        zun = BlazarZunClient(endpoint_override=CONF.zun.endpoint_override)
        return zun


class ZunInventory(BlazarZunClient):
    def get_host_details(self, host):
        """Get Zun capabilities of a single host

        :param host: UUID or name of zun compute node
        :return: Dict of capabilities or raise HostNotFound
        """
        try:
            host = self.zun.hosts.get(host)
        except (zun_exception.NotFound, zun_exception.BadRequest):
            host_ids = []
            for h in self.zun.hosts.list():
                if h.hostname == host:
                    host_ids.append(h.uuid)
            if len(host_ids) == 0:
                raise manager_exceptions.HostNotFound(host=host)
            elif len(host_ids) > 1:
                raise manager_exceptions.MultipleHostsFound(host=host)
            else:
                host = self.zun.hosts.get(host_ids[0])

        return {'id': host.uuid,
                'name': host.hostname,
                'containers': self.zun.containers.list(host=host.name)
                }
