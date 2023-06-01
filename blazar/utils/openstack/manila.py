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

from blazar.utils.openstack import base
from oslo_log import log as logging
from manilaclient import client as manila_client


manila_opts = [
    cfg.StrOpt(
        'manila_api_version',
        default='2',
        help='Manila API version'),
    cfg.StrOpt(
        'manila_api_microversion',
        default='2.69',
        help='Manila API microversion')
]

CONF = cfg.CONF
CONF.register_opts(manila_opts, group='manila')

LOG = logging.getLogger(__name__)


class BlazarManilaClient(object):
    """Client class for Manila service."""

    def __init__(self, **kwargs):
        client_kwargs = base.client_kwargs(**kwargs)
        client_kwargs.setdefault('os_manila_api_version',
                                 CONF.manila.manila_api_microversion)
        self.manila = manila_client.Client(
            CONF.manila.manila_api_version, **client_kwargs)

    def __getattr__(self, attr):
        return getattr(self.manila, attr)
