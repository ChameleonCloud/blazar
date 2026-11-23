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

"""Check that each plugin RPC client matches the plugin it calls.

The manager dispatches "<resource_type>:<method>" to the plugin and passes
the client's kwargs straight through, so a renamed method or argument only
fails at runtime. These tests call every client method and check that the
target exists on the plugin class and accepts the kwargs.
"""

import inspect
from unittest import mock

import ddt

from blazar.manager.devices import rpcapi as devices_rpcapi
from blazar.manager.flavor_instances import rpcapi as flavor_rpcapi
from blazar.manager.floatingips import rpcapi as floatingips_rpcapi
from blazar.manager.networks import rpcapi as networks_rpcapi
from blazar.manager.oshosts import rpcapi as oshosts_rpcapi
from blazar.plugins.devices import device_plugin
from blazar.plugins.flavor import flavor_plugin
from blazar.plugins.floatingips import floatingip_plugin
from blazar.plugins.networks import network_plugin
from blazar.plugins.oshosts import host_plugin
from blazar import tests

CLIENTS = [
    (oshosts_rpcapi, host_plugin.PhysicalHostPlugin),
    (floatingips_rpcapi, floatingip_plugin.FloatingIpPlugin),
    (devices_rpcapi, device_plugin.DevicePlugin),
    (networks_rpcapi, network_plugin.NetworkPlugin),
    (flavor_rpcapi, flavor_plugin.FlavorPlugin),
]


@ddt.ddt
class PluginRPCAPIContractTestCase(tests.TestCase):

    @ddt.data(*CLIENTS)
    @ddt.unpack
    def test_client_methods_match_plugin(self, rpcapi_module, plugin_cls):
        with mock.patch('blazar.rpc.get_client'), \
                mock.patch('blazar.rpc.init'):
            client = rpcapi_module.ManagerRPCAPI()
        call = self.patch(client, 'call')

        methods = [
            name for name, _ in inspect.getmembers(
                rpcapi_module.ManagerRPCAPI, inspect.isfunction)
            if not name.startswith('_')
            and name not in ('call', 'cast')]
        self.assertTrue(methods)

        for name in methods:
            call.reset_mock()
            method = getattr(client, name)
            params = inspect.signature(method).parameters
            getattr(client, name)(*[mock.sentinel.arg] * len(params))

            target, kwargs = call.call_args[0][0], call.call_args[1]
            resource_type, plugin_method = target.rsplit(':', 1)
            self.assertEqual(plugin_cls.resource_type, resource_type, name)
            self.assertTrue(hasattr(plugin_cls, plugin_method),
                            '%s calls missing %s.%s' % (
                                name, plugin_cls.__name__, plugin_method))
            inspect.signature(getattr(plugin_cls, plugin_method)).bind(
                None, **kwargs)


class FlavorGetAvailabilityTestCase(tests.TestCase):
    """Dates cross RPC as strings, so get_availability converts both ways."""

    def setUp(self):
        super(FlavorGetAvailabilityTestCase, self).setUp()
        self.plugin = flavor_plugin.FlavorPlugin.__new__(
            flavor_plugin.FlavorPlugin)
        self.start = flavor_plugin.datetime.datetime(2026, 1, 1, 0, 0)
        self.end = flavor_plugin.datetime.datetime(2026, 1, 2, 0, 0)
        self.result = {'flavor_id': 'f1', 'availability': [
            {'start': self.start, 'end': self.end,
             'available': 1, 'total': 2}]}

    def test_single_flavor(self):
        compute = self.patch(self.plugin, 'compute_availability')
        compute.return_value = self.result
        results = self.plugin.get_availability(
            'f1', '2026-01-01 00:00', '2026-01-02 00:00', 'proj')
        compute.assert_called_once_with('f1', self.start, self.end, 'proj')
        self.assertEqual(
            [{'flavor_id': 'f1', 'availability': [
                {'start': '2026-01-01 00:00', 'end': '2026-01-02 00:00',
                 'available': 1, 'total': 2}]}],
            results)

    def test_all_flavors(self):
        compute_all = self.patch(self.plugin, 'compute_all_availability')
        compute_all.return_value = [self.result]
        results = self.plugin.get_availability(
            None, '2026-01-01 00:00', '2026-01-02 00:00', 'proj')
        compute_all.assert_called_once_with(self.start, self.end, 'proj')
        self.assertEqual('2026-01-01 00:00',
                         results[0]['availability'][0]['start'])
