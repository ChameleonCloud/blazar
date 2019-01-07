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
from oslo_config import cfg

from blazar.utils.openstack import base

ironic_opts = [
    cfg.StrOpt(
        'ironic_api_version',
        default='1',
        help='Ironic API version'),
    cfg.StrOpt(
        'ironic_api_microversion',
        default='1.31',
        help='Ironic API microversion')
]

CONF = cfg.CONF
CONF.register_opts(ironic_opts, group='ironic')


class BlazarIronicClient(object):
    """Client class for Ironic service."""

    def __init__(self, **kwargs):
        client_kwargs = base.client_kwargs(**kwargs)
        client_kwargs.setdefault('os_ironic_api_version',
                                 CONF.ironic.ironic_api_microversion)
        self.ironic = ironic_client.Client(
            CONF.ironic.ironic_api_version, **client_kwargs)

    def __getattr__(self, attr):
        return getattr(self.ironic, attr)
