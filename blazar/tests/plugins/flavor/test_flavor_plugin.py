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
from unittest import mock

from novaclient.v2 import flavors
from oslo_config import cfg

from blazar import context
from blazar.db.sqlalchemy import api as db_api
from blazar.db import utils as db_utils
from blazar.manager import exceptions as mgr_exceptions
from blazar.plugins.flavor import flavor_plugin
from blazar.plugins.oshosts import host_plugin
from blazar import tests
from blazar.tests.db.sqlalchemy import test_sqlalchemy_api as fake
from blazar.utils.openstack import nova
from blazar.utils.openstack import placement

CONF = cfg.CONF


class TestFlavorPlugin(tests.DBTestCase):
    def _create_fake_host(self, id=123, hypervisor_hostname=None, **kwargs):
        host_values = fake._get_fake_host_values(id=id)
        if hypervisor_hostname:
            host_values['hypervisor_hostname'] = hypervisor_hostname
        host_values.update(kwargs)
        host_values["reservable"] = 1
        db_api.host_create(host_values)

    def _create_lease_and_reservation(self, lease_id, start_date, end_date,
                                      host_id, reservation_id, flavor_id='flavor1'):
        db_api.lease_create({
            'id': lease_id,
            'name': lease_id,
            'project_id': 'proj1',
            'start_date': start_date,
            'end_date': end_date,
            'user_id': 'user1',
            'trust_id': 'trust1',
        })
        for event_type, event_time in [('start_lease', start_date),
                                       ('end_lease', end_date)]:
            db_api.event_create({
                'lease_id': lease_id,
                'event_type': event_type,
                'time': event_time,
                'status': 'pending' if event_type == 'end_lease' else 'done',
            })

        inst_res = {
            'id': reservation_id,
            'lease_id': lease_id,
            'resource_id': 'inst-' + reservation_id,
            'resource_type': 'virtual:instance',
            'status': 'active',
            'start_date': start_date,
            'end_date': end_date,
            'project_id': 'proj1',
        }
        db_api.reservation_create(inst_res)
        inst_res_details = {
            'id': 'inst-' + reservation_id,
            'reservation_id': reservation_id,
            'flavor_id': flavor_id,
            'amount': 1,
            'vcpus': 1,
            'memory_mb': 1024,
            'disk_gb': 10,
            'affinity': False,
            'resource_properties': '{"OS-FLV-EXT-DATA:ephemeral": 0, "disk": 10, "ram": 1024, "vcpus": 1, "extra_specs": {}}',
        }
        db_api.instance_reservation_create(inst_res_details)
        db_api.host_allocation_create({
            'compute_host_id': host_id,
            'reservation_id': reservation_id
        })

    def test_get(self):
        plugin = flavor_plugin.FlavorPlugin()
        resource_id = '123'
        self._create_fake_host()

        result = plugin.get(resource_id)

        self.assertEqual(resource_id, result['id'])

    def test_list_allocations(self):
        plugin = flavor_plugin.FlavorPlugin()

        result = plugin.list_allocations({'lease_id': '2001'})

        self.assertEqual(0, len(result))

    @mock.patch.object(host_plugin.PhysicalHostPlugin, 'query_allocations')
    def test_query_allocations(self, mock_query):
        plugin = flavor_plugin.FlavorPlugin()
        mock_query.return_value = "fake"

        result = plugin.query_allocations(['123'], lease_id='2001')

        self.assertEqual("fake", result)
        mock_query.assert_called_once_with(['123'], '2001', None)

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_allocation_candidates(self, mock_get_flavor):
        self._create_fake_host()
        fake_inventory_values = {
            'computehost_id': 123,
            'resource_class': 'PCPU',
            'total': 10,
            'reserved': 2,
            'min_unit': 1,
            'max_unit': 10,
            'step_size': 1,
            'allocation_ratio': 1.0
        }
        db_api.host_resource_inventory_create(fake_inventory_values)
        plugin = flavor_plugin.FlavorPlugin()
        reservation = {
            'flavor_id': "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            'amount': 4,
            'affinity': None,
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00),
            "project_id": "fake",
        }
        mock_get_flavor.return_value = ({"PCPU": 2}, {},
                                        {"flavor_id": "fake"})

        result = plugin.allocation_candidates(reservation)

        self.assertEqual(4, len(result))
        mock_get_flavor.assert_called_once_with(
            "34eb7166-0e9b-432c-96fd-dff37f22e36e")

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_allocation_candidates_fails_no_space(self, mock_get_flavor):
        self._create_fake_host()
        fake_inventory_values = {
            'computehost_id': 123,
            'resource_class': 'PCPU',
            'total': 10,
            'reserved': 2,
            'min_unit': 1,
            'max_unit': 10,
            'step_size': 1,
            'allocation_ratio': 1.0
        }
        db_api.host_resource_inventory_create(fake_inventory_values)
        plugin = flavor_plugin.FlavorPlugin()
        reservation = {
            'flavor_id': "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            'amount': 5,
            'affinity': None,
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00),
            "project_id": "fake",
        }
        mock_get_flavor.return_value = ({"PCPU": 2}, {},
                                        {"flavor_id": "fake"})

        self.assertRaises(mgr_exceptions.NotEnoughHostsAvailable,
                          plugin.allocation_candidates,
                          reservation)

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_create_resources')
    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_allocation_candidates_avoids_reservations(self, mock_get_flavor,
                                                       mock_create):
        self._create_fake_host()
        fake_inventory_values = {
            'computehost_id': 123,
            'resource_class': 'PCPU',
            'total': 10,
            'reserved': 2,
            'min_unit': 1,
            'max_unit': 10,
            'step_size': 1,
            'allocation_ratio': 1.0
        }
        db_api.host_resource_inventory_create(fake_inventory_values)
        plugin = flavor_plugin.FlavorPlugin()
        new_reservation = {
            'flavor_id': "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            'amount': 3,
            'affinity': None,
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00),
            "project_id": "fake",
        }
        fake_flavor = {
            "disk": 0,  # GiB
            "OS-FLV-EXT-DATA:ephemeral": 0,  # GiB
            "id": "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            "name": "test1",
            "ram": 0,  # MB
            "swap": 0,
            "vcpus": 2,
            "extra_specs": {'hw:cpu_policy': 'dedicated'}
        }
        mock_get_flavor.return_value = ({"PCPU": 2}, {},
                                        fake_flavor)
        old_reservation = new_reservation.copy()
        old_reservation['amount'] = 2
        fake_phys_reservation = new_reservation.copy()
        fake_phys_reservation['id'] = 345
        fake_start_event = {
            'id': 123,
            'lease_id': 1234,
            'event_type': "start_lease",
            'time': datetime.datetime(2030, 1, 1, 8, 00),
            'status': "fake",
        }
        fake_lease = {
            'id': 1234,
            'name': "fakelease",
            'user_id': 'fake',
            'project_id': 'fake',
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00),
            'trust': 'trust',
            'reservations': [fake_phys_reservation],
            'events': [fake_start_event]
        }
        db_api.lease_create(fake_lease)
        mock_create.return_value = ("flavor_id", "aggregate_id")
        # create a reservation to avoid
        plugin.reserve_resource("345", old_reservation)

        # Host as 10 PCPUs, 2 are reserved, leaving 8 PCPUs available
        # Old reservation is for 2 flavors needing 2 each, so 4 left
        # So there should be space for 2 lots of 2 PCPUs
        new_reservation['amount'] = 2
        result = plugin.allocation_candidates(new_reservation)
        self.assertEqual(2, len(result))

        # there should not be space for 3 lots of 2 PCPUs
        new_reservation['amount'] = 3
        self.assertRaises(mgr_exceptions.NotEnoughHostsAvailable,
                          plugin.allocation_candidates,
                          new_reservation)

    @mock.patch.object(flavors.FlavorManager, 'get')
    def test__get_flavor_details(self, mock_get):
        plugin = flavor_plugin.FlavorPlugin()
        mock_flavor = mock.Mock()
        mock_get.return_value = mock_flavor
        fake_flavor = {
            "disk": 10,  # GiB
            "OS-FLV-EXT-DATA:ephemeral": 0,  # GiB
            "id": "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            "name": "test1",
            "ram": 1024,  # MB
            "swap": 0,
            "vcpus": 1,
        }
        mock_flavor.to_dict.return_value = fake_flavor
        fake_extra_specs = {
            'hw:cpu_policy': 'dedicated',
            'trait:HW_CPU_X86_AVX': 'required',
            'resources:VGPU': '1',
        }
        mock_flavor.get_keys.return_value = fake_extra_specs

        resource_request, resource_traits, source_flavor = \
            plugin._get_flavor_details("34eb7166-0e9b-432c-96fd-dff37f22e36e")

        self.assertDictEqual({
            'DISK_GB': 10, 'MEMORY_MB': 1024, 'PCPU': 1, 'VCPU': 0, 'VGPU': 1
        }, resource_request)
        self.assertDictEqual({'HW_CPU_X86_AVX': 'required'}, resource_traits)
        expected = fake_flavor.copy()
        expected["extra_specs"] = fake_extra_specs
        self.assertDictEqual(expected, source_flavor)

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_create_resources')
    @mock.patch.object(flavor_plugin.FlavorPlugin, '_pick_hosts')
    def test_reserve_resource(self, mock_pick, mock_create):
        plugin = flavor_plugin.FlavorPlugin()
        reservation = {
            'flavor_id': "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            'amount': 5,
            'affinity': None,
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00)
        }
        fake_flavor = {
            "disk": 10,  # GiB
            "OS-FLV-EXT-DATA:ephemeral": 0,  # GiB
            "id": "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            "name": "test1",
            "ram": 1024,  # MB
            "swap": 0,
            "vcpus": 1,
        }
        mock_pick.return_value = (['123', '124'], fake_flavor)
        mock_create.return_value = ("flavor_id", "aggregate_id")

        id = plugin.reserve_resource("345", reservation)

        allocations = db_api.host_allocation_get_all_by_values(
            reservation_id="345")
        self.assertEqual(2, len(allocations))
        reservation = db_api.instance_reservation_get(id)
        self.assertEqual("345", reservation["reservation_id"])
        self.assertEqual("flavor_id", reservation["flavor_id"])
        self.assertEqual("aggregate_id", reservation["aggregate_id"])

    @mock.patch.object(nova.ReservationPool, 'create')
    @mock.patch.object(context.BlazarContext, 'current')
    @mock.patch.object(placement.BlazarPlacementClient,
                       'create_reservation_class')
    @mock.patch.object(flavor_plugin.FlavorPlugin, '_create_flavor')
    def test_create_resources(self, mock_create_flavor,
                              mock_reservation_create,
                              mock_current, mock_pool_create):
        plugin = flavor_plugin.FlavorPlugin()
        fake_flavor = {
            "disk": 10,  # GiB
            "OS-FLV-EXT-DATA:ephemeral": 100,  # GiB
            "id": "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            "name": "test1",
            "ram": 1024,  # MB
            "swap": 0,
            "vcpus": 2,
            "extra_specs": {'hw:cpu_policy': 'dedicated'}
        }
        fake_reservation = {
            'reservation_id': "12345",
            'vcpus': fake_flavor["vcpus"],
            'memory_mb': fake_flavor["ram"],
            'disk_gb': fake_flavor["disk"],
            'amount': 2,
            'affinity': None,
            'resource_properties': json.dumps(fake_flavor)
        }
        mock_context = mock.Mock()
        mock_context.project_id = "fake-project-id"
        mock_current.return_value = mock_context
        mock_aggregate = mock.Mock()
        mock_aggregate.id = "aggregate_id"
        mock_pool_create.return_value = mock_aggregate
        mock_flavor = mock.Mock()
        mock_flavor.id = "flavor_id"
        mock_create_flavor.return_value = mock_flavor

        fid, aid = plugin._create_resources(fake_reservation)

        self.assertEqual(fid, "flavor_id")
        self.assertEqual(aid, "aggregate_id")
        mock_create_flavor.assert_called_once_with(fake_reservation)
        mock_reservation_create.assert_called_once_with("12345")
        mock_current.assert_called_once_with()
        mock_pool_create.assert_called_once_with(
            name="12345",
            metadata={'reservation': '12345',
                      'filter_tenant_id': 'fake-project-id'},
            project_id='fake-project-id',
        ),

    @mock.patch.object(flavors.FlavorManager, 'create')
    @mock.patch.object(db_api, "reservation_get")
    @mock.patch.object(db_api, "lease_get")
    def test_create_flavor(self, mock_lease_get, mock_reservation_get, mock_create):
        plugin = flavor_plugin.FlavorPlugin()
        fake_flavor = {
            "disk": 10,  # GiB
            "OS-FLV-EXT-DATA:ephemeral": 100,  # GiB
            "id": "34eb7166-0e9b-432c-96fd-dff37f22e36e",
            "name": "test1",
            "ram": 1024,  # MB
            "swap": 0,
            "vcpus": 2,
            "extra_specs": {'hw:cpu_policy': 'dedicated'}
        }
        fake_reservation = {
            'reservation_id': "12345",
            'vcpus': fake_flavor["vcpus"],
            'memory_mb': fake_flavor["ram"],
            'disk_gb': fake_flavor["disk"],
            'amount': 2,
            'affinity': None,
            'resource_properties': json.dumps(fake_flavor)
        }
        mock_flavor = mock.Mock()
        mock_create.return_value = mock_flavor
        class FakeRes:
            def to_dict(self):
                return {"lease_id": 1}
        mock_reservation_get.return_value = FakeRes()
        class FakeLease:
            def to_dict(self):
                return {"name": "my_lease"}
        mock_lease_get.return_value = FakeLease()

        plugin._create_flavor(fake_reservation)

        mock_flavor.set_keys.assert_called_once_with({
            'hw:cpu_policy': 'dedicated',
            'aggregate_instance_extra_specs:reservation': '12345',
            'resources:CUSTOM_RESERVATION_12345': '1',
        })
        mock_create.assert_called_once_with(
            flavorid='12345', name='reservation:12345', vcpus=2, ram=1024,
            disk=10, is_public=False, description="my_lease")

    @mock.patch.object(placement.BlazarPlacementClient,
                       'list_resource_providers')
    def test__query_available_hosts(self, mock_list_resource_providers):
        mock_list_resource_providers.return_value = {}
        get_reservations = self.patch(db_utils,
                                      'get_reservations_by_host_id')
        get_reservations.return_value = []
        plugin = flavor_plugin.FlavorPlugin()

        # Check that we can fit 4 VCPU resource requests
        self._create_fake_host(hypervisor_hostname="abc")
        fake_inventory_values = {
            'computehost_id': 123,
            'resource_class': 'VCPU',
            'total': 4,
            'reserved': 0,
            'min_unit': 1,
            'max_unit': 4,
            'step_size': 1,
            'allocation_ratio': 1.0
        }
        db_api.host_resource_inventory_create(fake_inventory_values)

        query_params = {
            'start_date': datetime.datetime(2020, 7, 7, 18, 0),
            'end_date': datetime.datetime(2020, 7, 7, 19, 0),
            'resource_request': {
                'VCPU': 1,
            },
            "project_id": "fake",
            'resource_traits': {}
        }
        ret = plugin._query_available_hosts(**query_params)
        self.assertEqual(4, len(ret))

        # Only 2 * 1024 MB requests fit when we add a MEMORY_MB inventory
        fake_inventory_values = {
            'computehost_id': 123,
            'resource_class': 'MEMORY_MB',
            'total': 2048,
            'reserved': 0,
            'min_unit': 1,
            'max_unit': 2048,
            'step_size': 1,
            'allocation_ratio': 1.0
        }
        db_api.host_resource_inventory_create(fake_inventory_values)

        query_params = {
            'start_date': datetime.datetime(2020, 7, 7, 18, 0),
            'end_date': datetime.datetime(2020, 7, 7, 19, 0),
            'resource_request': {
                'VCPU': 1,
                'MEMORY_MB': 1024
            },
            "project_id": "fake",
            'resource_traits': {}
        }
        ret = plugin._query_available_hosts(**query_params)
        self.assertEqual(2, len(ret))

        # No instances fit when traits do not match request
        query_params = {
            'start_date': datetime.datetime(2020, 7, 7, 18, 0),
            'end_date': datetime.datetime(2020, 7, 7, 19, 0),
            'resource_request': {
                'VCPU': 1,
                'MEMORY_MB': 1024
            },
            "project_id": "fake",
            'resource_traits': {
                "CUSTOM_1": "required"
            }
        }
        ret = plugin._query_available_hosts(**query_params)
        self.assertEqual(0, len(ret))

        # Only hosts with the required trait are returned
        self._create_fake_host(id=456, hypervisor_hostname="def")
        fake_inventory_values = {
            'computehost_id': 456,
            'resource_class': 'VCPU',
            'total': 3,
            'reserved': 0,
            'min_unit': 1,
            'max_unit': 4,
            'step_size': 1,
            'allocation_ratio': 1.0
        }
        db_api.host_resource_inventory_create(fake_inventory_values)
        self._create_fake_host(id=789, hypervisor_hostname="ghi")
        fake_inventory_values = {
            'computehost_id': 789,
            'resource_class': 'VCPU',
            'total': 3,
            'reserved': 0,
            'min_unit': 1,
            'max_unit': 4,
            'step_size': 1,
            'allocation_ratio': 1.0
        }
        db_api.host_resource_inventory_create(fake_inventory_values)
        mock_list_resource_providers.return_value = [{"name": "def"}]
        query_params = {
            'start_date': datetime.datetime(2020, 7, 7, 18, 0),
            'end_date': datetime.datetime(2020, 7, 7, 19, 0),
            'resource_request': {
                'VCPU': 1,
            },
            'resource_traits': {
                "CUSTOM_1": "forbidden",
                "CUSTOM_2": "required",
            },
            "project_id": "fake",
        }
        ret = plugin._query_available_hosts(**query_params)
        # 3 available slots on the second host
        self.assertEqual(3, len(ret))
        for host in ret:
            self.assertEqual(host["id"], '456')

    def test_update_reservation_extension_should_pass_with_full_host(self):
        # Scenario: Host has 1 VCPU.
        # Reservation consumes 1 VCPU.
        # User extends Reservation.
        # Should succeed because the reservation's own usage should be excluded.

        self.plugin = flavor_plugin.FlavorPlugin()

        # 1. Setup Host
        self._create_fake_host(id='host1', hypervisor_hostname='host1', vcpus=1)

        # Setup Inventory to make sure the plugin sees the resource
        db_api.host_resource_inventory_create({
            'computehost_id': 'host1',
            'resource_class': 'VCPU',
            'total': 1,
            'reserved': 0,
            'min_unit': 1,
            'max_unit': 1,
            'step_size': 1,
            'allocation_ratio': 1.0
        })

        # 2. Setup Existing Reservation
        self._create_lease_and_reservation(
            lease_id='lease-1',
            start_date=datetime.datetime(2030, 1, 1, 10, 0),
            end_date=datetime.datetime(2030, 1, 1, 11, 0),
            host_id='host1',
            reservation_id='res-1'
        )

        # Mock Nova/Flavor/Placement calls to avoid external calls
        # We need _get_flavor_details to return our VCPU requirement
        with mock.patch.object(self.plugin, '_get_flavor_details') as mock_get_flavor:
            mock_get_flavor.return_value = ({'VCPU': 1}, {}, {'vcpus': 1, 'ram': 1024, 'disk': 10, 'OS-FLV-EXT-DATA:ephemeral': 0})

            # 3. Call update_reservation to extend
            new_values = {
                'start_date': datetime.datetime(2030, 1, 1, 10, 0),
                'end_date': datetime.datetime(2030, 1, 1, 12, 0), # Extend by 1 hour
                'project_id': 'proj1',
                'amount': 1,
                'flavor_id': 'flavor1'
            }

            try:
                self.plugin.update_reservation('res-1', new_values)
            except mgr_exceptions.NotEnoughHostsAvailable:
                self.fail("Raised NotEnoughHostsAvailable. The existing reservation was likely not excluded from capacity check.")

    def test_update_reservation_extension_should_fail_if_blocked(self):
        # Scenario: Host has 1 VCPU.
        # Lease 1: 10:00-11:00 (1 VCPU).
        # Lease 2: 11:00-12:00 (1 VCPU).
        # User extends Lease 1 to 12:00.
        # Should FAIL because Lease 2 consumes the resource 11:00-12:00.

        self.plugin = flavor_plugin.FlavorPlugin()

        # 1. Setup Host
        self._create_fake_host(id='host1', hypervisor_hostname='host1', vcpus=1)

        db_api.host_resource_inventory_create({
            'computehost_id': 'host1',
            'resource_class': 'VCPU',
            'total': 1,
            'reserved': 0,
            'min_unit': 1,
            'max_unit': 1,
            'step_size': 1,
            'allocation_ratio': 1.0
        })

        # 2. Setup Lease 1 (The one we update)
        self._create_lease_and_reservation(
            lease_id='lease-1',
            start_date=datetime.datetime(2030, 1, 1, 10, 0),
            end_date=datetime.datetime(2030, 1, 1, 11, 0),
            host_id='host1',
            reservation_id='res-1'
        )

        # 3. Setup Lease 2 (Blocking lease)
        self._create_lease_and_reservation(
            lease_id='lease-2',
            start_date=datetime.datetime(2030, 1, 1, 11, 0),
            end_date=datetime.datetime(2030, 1, 1, 12, 0),
            host_id='host1',
            reservation_id='res-2'
        )

        # Mock external calls
        with mock.patch.object(self.plugin, '_get_flavor_details') as mock_get_flavor:
            mock_get_flavor.return_value = ({'VCPU': 1}, {}, {'vcpus': 1, 'ram': 1024, 'disk': 10, 'OS-FLV-EXT-DATA:ephemeral': 0})

            # 4. Try to extend Lease 1 to 12:00
            new_values = {
                'start_date': datetime.datetime(2030, 1, 1, 10, 0),
                'end_date': datetime.datetime(2030, 1, 1, 12, 0), # Extension overlapping Lease 2
                'project_id': 'proj1',
                'amount': 1,
                'flavor_id': 'flavor1'
            }

            # Should FAIL
            self.assertRaises(mgr_exceptions.NotEnoughHostsAvailable,
                              self.plugin.update_reservation,
                              'res-1', new_values)

    def test_update_reservation_extension_mixed_hosts(self):
        # Scenario:
        # Host 1 (1 VCPU). Lease 1 (1 VCPU) 10:00-11:00.
        # Host 2 (1 VCPU). Lease 2 (1 VCPU) 10:00-12:00.
        # User extends Lease 1 to 12:00.
        # Host 1 should be available (exclude self).
        # Host 2 should be unavailable (Lease 2).

        self.plugin = flavor_plugin.FlavorPlugin()

        # 1. Setup Hosts
        for i in range(1, 3):
            self._create_fake_host(id='host%d' % i, hypervisor_hostname='host%d' % i, vcpus=1)
            db_api.host_resource_inventory_create({
                'computehost_id': 'host%d' % i,
                'resource_class': 'VCPU',
                'total': 1,
                'reserved': 0,
                'min_unit': 1,
                'max_unit': 1,
                'step_size': 1,
                'allocation_ratio': 1.0
            })

        # 2. Setup Lease 1 on Host 1
        self._create_lease_and_reservation(
            lease_id='lease-1',
            start_date=datetime.datetime(2030, 1, 1, 10, 0),
            end_date=datetime.datetime(2030, 1, 1, 11, 0),
            host_id='host1',
            reservation_id='res-1'
        )

        # 3. Setup Lease 2 on Host 2 (Blocking)
        self._create_lease_and_reservation(
            lease_id='lease-2',
            start_date=datetime.datetime(2030, 1, 1, 10, 0),
            end_date=datetime.datetime(2030, 1, 1, 12, 0),
            host_id='host2',
            reservation_id='res-2'
        )

        with mock.patch.object(self.plugin, '_get_flavor_details') as mock_get_flavor:
            mock_get_flavor.return_value = ({'VCPU': 1}, {}, {'vcpus': 1, 'ram': 1024, 'disk': 10, 'OS-FLV-EXT-DATA:ephemeral': 0})

            # 4. Try to extend Lease 1 to 12:00
            new_values = {
                'start_date': datetime.datetime(2030, 1, 1, 10, 0),
                'end_date': datetime.datetime(2030, 1, 1, 12, 0),
                'project_id': 'proj1',
                'amount': 1,
                'flavor_id': 'flavor1'
            }

            # This should SUCCEED
            self.plugin.update_reservation('res-1', new_values)

    def test__max_usages_spread_hosts(self):
        # Scenario:
        # Reservation R (amount=2)
        # Allocation 1: Host A
        # Allocation 2: Host B
        # _max_usages(Host A, [R]) should return 1 instance worth of resources.

        plugin = flavor_plugin.FlavorPlugin()

        # 1. Setup Hosts
        self._create_fake_host(id='host1', hypervisor_hostname='host1')
        self._create_fake_host(id='host2', hypervisor_hostname='host2')

        # 2. Setup Lease and Reservation
        start_date = datetime.datetime(2030, 1, 1, 10, 0)
        end_date = datetime.datetime(2030, 1, 1, 11, 0)

        lease_id = 'lease-1'
        reservation_id = 'res-1'

        self._create_lease_and_reservation(
            lease_id=lease_id,
            start_date=start_date,
            end_date=end_date,
            host_id='host1',
            reservation_id=reservation_id,
            flavor_id='flavor1'
        )

        # 3. Add second allocation on host2
        db_api.host_allocation_create({
            'compute_host_id': 'host2',
            'reservation_id': reservation_id
        })

        # 4. Update reservation amount to 2
        instance_res = db_api.instance_reservation_get('inst-' + reservation_id)
        db_api.instance_reservation_update(instance_res['id'], {'amount': 2})

        host1 = db_api.host_get('host1')
        reservations = db_utils.get_reservations_by_host_id(
            'host1', start_date, end_date)

        # 5. Call _max_usages
        result = plugin._max_usages(host1, reservations)

        # Expected: 1 instance on host1 (even if total amount is 2)
        self.assertEqual(1, result['VCPU'])
        self.assertEqual(1024, result['MEMORY_MB'])
        self.assertEqual(10, result['DISK_GB'])

        # 6. Verify for host2 as well
        host2 = db_api.host_get('host2')
        reservations2 = db_utils.get_reservations_by_host_id(
            'host2', start_date, end_date)
        result2 = plugin._max_usages(host2, reservations2)

        self.assertEqual(1, result2['VCPU'])
        self.assertEqual(1024, result2['MEMORY_MB'])
        self.assertEqual(10, result2['DISK_GB'])

    _FAKE_SOURCE_FLAVOR = {
        'vcpus': 1,
        'ram': 1024,
        'disk': 10,
        'OS-FLV-EXT-DATA:ephemeral': 0,
        'extra_specs': {},
    }

    def _create_host_with_inventory(self, host_id, vcpus, vcpu_max=None):
        if vcpu_max is None:
            vcpu_max = vcpus
        self._create_fake_host(id=host_id, hypervisor_hostname=str(host_id))
        db_api.host_resource_inventory_create({
            'computehost_id': host_id,
            'resource_class': 'VCPU',
            'total': vcpus,
            'reserved': 0,
            'min_unit': 1,
            'max_unit': vcpu_max,
            'step_size': 1,
            'allocation_ratio': 1.0,
        })

    def _create_phys_host_reservation(self, lease_id, start_date, end_date,
                                      host_id, reservation_id):
        db_api.lease_create({
            'id': lease_id,
            'name': lease_id,
            'project_id': 'proj1',
            'start_date': start_date,
            'end_date': end_date,
            'user_id': 'user1',
            'trust_id': 'trust1',
        })
        for event_type, event_time in [('start_lease', start_date),
                                       ('end_lease', end_date)]:
            db_api.event_create({
                'lease_id': lease_id,
                'event_type': event_type,
                'time': event_time,
                'status': 'pending',
            })
        db_api.reservation_create({
            'id': reservation_id,
            'lease_id': lease_id,
            'resource_id': 'host-' + reservation_id,
            'resource_type': 'physical:host',
            'status': 'active',
            'start_date': start_date,
            'end_date': end_date,
            'project_id': 'proj1',
        })
        db_api.host_allocation_create({
            'compute_host_id': host_id,
            'reservation_id': reservation_id,
        })

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_compute_availability_no_eligible_hosts(self, mock_get_flavor):
        mock_get_flavor.return_value = ({'VCPU': 1}, {}, self._FAKE_SOURCE_FLAVOR)
        plugin = flavor_plugin.FlavorPlugin()
        start = datetime.datetime(2030, 1, 1, 8, 0)
        end = datetime.datetime(2030, 1, 1, 12, 0)

        result = plugin.compute_availability('flavor-1', start, end, 'proj1')

        self.assertEqual('flavor-1', result['flavor_id'])
        self.assertEqual({'vcpus': 1, 'memory_mb': 1024, 'disk_gb': 10},
                         result['resource_spec'])
        self.assertEqual(
            [{'start': start, 'end': end, 'available': 0, 'total': 0}],
            result['availability'])

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_compute_availability_no_reservations(self, mock_get_flavor):
        mock_get_flavor.return_value = ({'VCPU': 1}, {}, self._FAKE_SOURCE_FLAVOR)
        self._create_host_with_inventory('host1', vcpus=4)
        plugin = flavor_plugin.FlavorPlugin()
        start = datetime.datetime(2030, 1, 1, 8, 0)
        end = datetime.datetime(2030, 1, 1, 12, 0)

        result = plugin.compute_availability('flavor-1', start, end, 'proj1')

        self.assertEqual({'vcpus': 1, 'memory_mb': 1024, 'disk_gb': 10},
                         result['resource_spec'])
        self.assertEqual(
            [{'start': start, 'end': end, 'available': 4, 'total': 4}],
            result['availability'])

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_compute_availability_with_instance_reservation(self, mock_get_flavor):
        mock_get_flavor.return_value = ({'VCPU': 1}, {}, self._FAKE_SOURCE_FLAVOR)
        self._create_host_with_inventory('host1', vcpus=4)
        start = datetime.datetime(2030, 1, 1, 8, 0)
        mid1 = datetime.datetime(2030, 1, 1, 9, 0)
        mid2 = datetime.datetime(2030, 1, 1, 11, 0)
        end = datetime.datetime(2030, 1, 1, 12, 0)
        self._create_lease_and_reservation(
            lease_id='lease-1', start_date=mid1, end_date=mid2,
            host_id='host1', reservation_id='res-1')
        plugin = flavor_plugin.FlavorPlugin()

        result = plugin.compute_availability('flavor-1', start, end, 'proj1')

        # One VCPU consumed during [mid1, mid2]; 4-1=3 available in that window
        self.assertEqual([
            {'start': start, 'end': mid1, 'available': 4, 'total': 4},
            {'start': mid1, 'end': mid2, 'available': 3, 'total': 4},
            {'start': mid2, 'end': end, 'available': 4, 'total': 4},
        ], result['availability'])

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_compute_availability_reservation_fills_host(self, mock_get_flavor):
        mock_get_flavor.return_value = ({'VCPU': 1}, {}, self._FAKE_SOURCE_FLAVOR)
        self._create_host_with_inventory('host1', vcpus=1)
        start = datetime.datetime(2030, 1, 1, 8, 0)
        mid1 = datetime.datetime(2030, 1, 1, 9, 0)
        mid2 = datetime.datetime(2030, 1, 1, 11, 0)
        end = datetime.datetime(2030, 1, 1, 12, 0)
        self._create_lease_and_reservation(
            lease_id='lease-1', start_date=mid1, end_date=mid2,
            host_id='host1', reservation_id='res-1')
        plugin = flavor_plugin.FlavorPlugin()

        result = plugin.compute_availability('flavor-1', start, end, 'proj1')

        self.assertEqual([
            {'start': start, 'end': mid1, 'available': 1, 'total': 1},
            {'start': mid1, 'end': mid2, 'available': 0, 'total': 1},
            {'start': mid2, 'end': end, 'available': 1, 'total': 1},
        ], result['availability'])

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_compute_availability_physical_host_blocks_host(self, mock_get_flavor):
        mock_get_flavor.return_value = ({'VCPU': 1}, {}, self._FAKE_SOURCE_FLAVOR)
        self._create_host_with_inventory('host1', vcpus=4)
        start = datetime.datetime(2030, 1, 1, 8, 0)
        mid1 = datetime.datetime(2030, 1, 1, 9, 0)
        mid2 = datetime.datetime(2030, 1, 1, 11, 0)
        end = datetime.datetime(2030, 1, 1, 12, 0)
        self._create_phys_host_reservation(
            lease_id='lease-1', start_date=mid1, end_date=mid2,
            host_id='host1', reservation_id='res-1')
        plugin = flavor_plugin.FlavorPlugin()

        result = plugin.compute_availability('flavor-1', start, end, 'proj1')

        # Physical host reservation zeros out the entire host
        self.assertEqual([
            {'start': start, 'end': mid1, 'available': 4, 'total': 4},
            {'start': mid1, 'end': mid2, 'available': 0, 'total': 4},
            {'start': mid2, 'end': end, 'available': 4, 'total': 4},
        ], result['availability'])

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_compute_availability_merges_adjacent_segments(self, mock_get_flavor):
        mock_get_flavor.return_value = ({'VCPU': 1}, {}, self._FAKE_SOURCE_FLAVOR)
        self._create_host_with_inventory('host1', vcpus=2)
        T0 = datetime.datetime(2030, 1, 1, 8, 0)
        T1 = datetime.datetime(2030, 1, 1, 9, 0)
        T2 = datetime.datetime(2030, 1, 1, 10, 0)
        T3 = datetime.datetime(2030, 1, 1, 11, 0)
        T4 = datetime.datetime(2030, 1, 1, 12, 0)
        # Two back-to-back reservations each consuming 1 VCPU: [T1,T2] and [T2,T3]
        self._create_lease_and_reservation(
            lease_id='lease-1', start_date=T1, end_date=T2,
            host_id='host1', reservation_id='res-1')
        self._create_lease_and_reservation(
            lease_id='lease-2', start_date=T2, end_date=T3,
            host_id='host1', reservation_id='res-2')
        plugin = flavor_plugin.FlavorPlugin()

        result = plugin.compute_availability('flavor-1', T0, T4, 'proj1')

        # Both segments have available=1; they should be merged into [T1, T3]
        self.assertEqual([
            {'start': T0, 'end': T1, 'available': 2, 'total': 2},
            {'start': T1, 'end': T3, 'available': 1, 'total': 2},
            {'start': T3, 'end': T4, 'available': 2, 'total': 2},
        ], result['availability'])

    @mock.patch.object(flavor_plugin.FlavorPlugin, '_get_flavor_details')
    def test_compute_availability_cleaning_time_extends_block(self, mock_get_flavor):
        CONF.set_override('cleaning_time', 60)
        self.addCleanup(CONF.clear_override, 'cleaning_time')
        mock_get_flavor.return_value = ({'VCPU': 1}, {}, self._FAKE_SOURCE_FLAVOR)
        self._create_host_with_inventory('host1', vcpus=1)
        start = datetime.datetime(2030, 1, 1, 8, 0)
        mid1 = datetime.datetime(2030, 1, 1, 9, 0)
        mid2 = datetime.datetime(2030, 1, 1, 10, 0)
        mid3 = datetime.datetime(2030, 1, 1, 11, 0)  # mid2 + 60 min cleaning
        end = datetime.datetime(2030, 1, 1, 12, 0)
        self._create_lease_and_reservation(
            lease_id='lease-1', start_date=mid1, end_date=mid2,
            host_id='host1', reservation_id='res-1')
        plugin = flavor_plugin.FlavorPlugin()

        result = plugin.compute_availability('flavor-1', start, end, 'proj1')

        # Cleaning time extends the blocked window from mid2 to mid3
        self.assertEqual([
            {'start': start, 'end': mid1, 'available': 1, 'total': 1},
            {'start': mid1, 'end': mid3, 'available': 0, 'total': 1},
            {'start': mid3, 'end': end, 'available': 1, 'total': 1},
        ], result['availability'])

    @mock.patch.object(flavor_plugin.FlavorPlugin, 'compute_availability')
    @mock.patch.object(nova.NovaClientWrapper, 'nova',
                       new_callable=mock.PropertyMock)
    def test_compute_all_availability_calls_each_flavor(
            self, mock_nova_prop, mock_compute_av):
        mock_client = mock.Mock()
        mock_nova_prop.return_value = mock_client
        flavor1 = mock.Mock()
        flavor1.id = 'flavor-1'
        flavor2 = mock.Mock()
        flavor2.id = 'flavor-2'
        mock_client.nova.flavors.list.return_value = [flavor1, flavor2]
        fake_result = {'flavor_id': 'x', 'resource_spec': {}, 'availability': []}
        mock_compute_av.return_value = fake_result
        plugin = flavor_plugin.FlavorPlugin()
        start = datetime.datetime(2030, 1, 1, 8, 0)
        end = datetime.datetime(2030, 1, 1, 12, 0)

        results = plugin.compute_all_availability(start, end, 'proj1')

        self.assertEqual(2, len(results))
        mock_compute_av.assert_any_call('flavor-1', start, end, 'proj1')
        mock_compute_av.assert_any_call('flavor-2', start, end, 'proj1')

    @mock.patch.object(flavor_plugin.FlavorPlugin, 'compute_availability')
    @mock.patch.object(nova.NovaClientWrapper, 'nova',
                       new_callable=mock.PropertyMock)
    def test_compute_all_availability_skips_failed_flavor(
            self, mock_nova_prop, mock_compute_av):
        mock_client = mock.Mock()
        mock_nova_prop.return_value = mock_client
        flavor1 = mock.Mock()
        flavor1.id = 'flavor-1'
        flavor2 = mock.Mock()
        flavor2.id = 'flavor-2'
        mock_client.nova.flavors.list.return_value = [flavor1, flavor2]
        good_result = {'flavor_id': 'flavor-2', 'resource_spec': {},
                       'availability': []}
        mock_compute_av.side_effect = [Exception("not found"), good_result]
        plugin = flavor_plugin.FlavorPlugin()
        start = datetime.datetime(2030, 1, 1, 8, 0)
        end = datetime.datetime(2030, 1, 1, 12, 0)

        results = plugin.compute_all_availability(start, end, 'proj1')

        self.assertEqual(1, len(results))
        self.assertEqual('flavor-2', results[0]['flavor_id'])

    def test_get_eligible_hosts_with_capacity(self):
        self._create_host_with_inventory('host1', vcpus=4)
        self._create_host_with_inventory('host2', vcpus=2)
        plugin = flavor_plugin.FlavorPlugin()

        result = plugin._get_eligible_hosts_with_capacity(
            {'VCPU': 1}, {}, 'proj1')

        self.assertEqual({'host1': 4, 'host2': 2}, result)

    def test_get_eligible_hosts_with_capacity_skips_no_inventory(self):
        self._create_host_with_inventory('host1', vcpus=4)
        # host2 has no inventory
        self._create_fake_host(id='host2', hypervisor_hostname='host2')
        plugin = flavor_plugin.FlavorPlugin()

        result = plugin._get_eligible_hosts_with_capacity(
            {'VCPU': 1}, {}, 'proj1')

        self.assertIn('host1', result)
        self.assertNotIn('host2', result)
