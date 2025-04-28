# Copyright (c) 2013 Bull.
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
from unittest import mock

from blazar.plugins.devices import k8s_plugin
import ddt
from unittest import skip
from oslo_config import cfg
from oslo_config import fixture as conf_fixture
import random
import testtools

from blazar import context, policy
from blazar import status
from blazar.db import api as db_api
from blazar.db import exceptions as db_exceptions
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_exceptions
from blazar.manager import service
from blazar.plugins import devices as plugin
from blazar.plugins.devices import device_plugin
from blazar import tests
from blazar.utils.openstack import base
from blazar.utils import trusts

from kubernetes import client
from kubernetes import config

CONF = cfg.CONF


@ddt.ddt
class DevicePluginTestCase(tests.TestCase):

    def setUp(self):
        super(DevicePluginTestCase, self).setUp()
        self.cfg = cfg
        self.context = context
        self.patch(self.context, 'BlazarContext')

        self.service = service
        self.manager = self.service.ManagerService()

        self.fake_device_id = '1'
        self.fake_device = {
            'id': self.fake_device_id,
            "name": "fake-rpi-1",
            "device_type": "container",
            "device_driver": "k8s",
        }

        self.patch(base, 'url_for').return_value = 'http://foo.bar'
        self.device_plugin = device_plugin
        self._get_plugins = self.patch(device_plugin, "_get_plugins")

        self.patch(config, 'load_kube_config').return_value = mock.MagicMock()
        self.core_v1 = self.patch(client, 'CoreV1Api')
        self.core_v1.return_value = mock.MagicMock()
        self.apps_v1 = self.patch(client, 'AppsV1Api')
        self.apps_v1.return_value = mock.MagicMock()

        self.fake_k8s_plugin = k8s_plugin.K8sPlugin()

        self._get_plugins.return_value = {
            "k8s": self.fake_k8s_plugin,
        }

        self.fake_dev_plugin = self.device_plugin.DevicePlugin()
        self.db_api = db_api
        self.db_utils = db_utils

        self.db_device_get = self.patch(self.db_api, 'device_get')
        self.db_device_get.return_value = self.fake_device
        self.db_device_list = self.patch(self.db_api, 'device_list')
        self.db_device_create = self.patch(self.db_api, 'device_create')
        self.db_device_update = self.patch(self.db_api, 'device_update')
        self.db_device_destroy = self.patch(self.db_api, 'device_destroy')

        self.db_device_extra_capability_get_all_per_device = self.patch(
            self.db_api, 'device_extra_capability_get_all_per_device')

        self.db_device_extra_capability_get_all_per_name = self.patch(
            self.db_api, 'device_extra_capability_get_all_per_name')

        self.db_device_extra_capability_create = self.patch(
            self.db_api, 'device_extra_capability_create')

        self.db_device_extra_capability_update = self.patch(
            self.db_api, 'device_extra_capability_update')

        self.db_device_extra_capability_destroy = self.patch(
            self.db_api, 'device_extra_capability_destroy')

        self.get_extra_capabilities = self.patch(
            self.fake_dev_plugin, '_get_extra_capabilities')
        self.get_extra_capabilities.return_value = {
            'foo': 'bar',
            'buzz': 'word',
        }

        self.fake_dev_plugin.setup(None)

        self.trusts = trusts
        self.trust_ctx = self.patch(self.trusts, 'create_ctx_from_trust')
        self.trust_create = self.patch(self.trusts, 'create_trust')

        self.cfg = cfg
        self.cfg.CONF.set_override('plugins', ["k8s.plugin"], group=plugin.RESOURCE_TYPE)


    def reservation_allocation_dict(self, r_id, l_id, p_id, h_ids):
        return {
            'id': r_id, 'status': 'active', 'lease_id': l_id,
            'start_date': datetime.datetime(2015, 1, 1, 0, 0),
            'end_date': datetime.datetime(2015, 1, 2, 0, 0),
            'lease_name': l_id, 'project_id': p_id,
            'device_ids': h_ids}

    def test_get_device(self):
        device = self.fake_dev_plugin.get_device(self.fake_device_id)
        self.db_device_get.assert_called_once_with('1')
        expected = self.fake_device.copy()
        expected.update({'foo': 'bar', 'buzz': 'word'})
        self.assertEqual(expected, device)

    def test_get_device_without_extracapabilities(self):
        self.get_extra_capabilities.return_value = {}
        device = self.fake_dev_plugin.get_device(self.fake_device_id)
        self.db_device_get.assert_called_once_with('1')
        self.assertEqual(self.fake_device, device)

    def test_list_devices(self):
        self.fake_dev_plugin.list_devices()
        self.db_device_list.assert_called_once_with()

    def test_create_device_without_extra_capabilities(self):
        self.get_extra_capabilities.return_value = {}
        device = self.fake_dev_plugin.create_device(self.fake_device)
        expected_device = self.fake_device.copy()
        del expected_device["id"]
        self.db_device_create.assert_called_once_with(expected_device)
        self.assertEqual(self.fake_device, device)

    def test_create_device_with_extra_capabilities(self):
        fake_device = self.fake_device.copy()
        fake_device.update({'foo': 'bar'})
        fake_request = fake_device.copy()
        del fake_request["id"]
        fake_capa = {'device_id': '1',
                     'capability_name': 'foo',
                     'capability_value': 'bar',
                     }
        self.get_extra_capabilities.return_value = {'foo': 'bar'}
        self.db_device_create.return_value = fake_device
        device = self.fake_dev_plugin.create_device(fake_request)
        self.db_device_create.assert_called_once_with(
            {
                'name': self.fake_device["name"],
                'device_type': 'container',
                'device_driver': "k8s",
            }
        )
        self.db_device_extra_capability_create.assert_called_once_with(fake_capa)
        self.assertEqual(fake_device, device)


    def test_create_device_issuing_rollback(self):
        def fake_db_device_create(*args, **kwargs):
            raise db_exceptions.BlazarDBException
        self.patch(self.fake_k8s_plugin, "create_device").side_effect = fake_db_device_create
        self.assertRaises(db_exceptions.BlazarDBException,
                          self.fake_dev_plugin.create_device,
                          self.fake_device)

    def test_create_duplicate_device(self):
        def fake_db_device_create(*args, **kwargs):
            raise db_exceptions.BlazarDBDuplicateEntry
        self.db_device_create.side_effect = fake_db_device_create
        self.assertRaises(db_exceptions.BlazarDBDuplicateEntry,
                          self.fake_dev_plugin.create_device,
                          self.fake_device)

    def test_create_device_having_issue_when_storing_extra_capability(self):
        def fake_db_device_extra_capability_create(*args, **kwargs):
            raise db_exceptions.BlazarDBException
        fake_device = self.fake_device.copy()
        fake_device.update({'foo': 'bar'})
        fake_request = fake_device.copy()
        self.get_extra_capabilities.return_value = {'foo': 'bar'}
        self.db_device_create.return_value = fake_device
        fake = self.db_device_extra_capability_create
        fake.side_effect = fake_db_device_extra_capability_create
        self.assertRaises(manager_exceptions.CantAddExtraCapability,
                          self.fake_dev_plugin.create_device,
                          fake_request)

    def test_update_device(self):
        device_values = {'foo': 'baz'}

        self.db_device_extra_capability_get_all_per_name.return_value = [
            ({'id': 'extra_id1',
              'device_id': self.fake_device_id,
              'capability_value': 'bar'},
             'foo'),
        ]

        self.get_reservations_by_device = self.patch(
            self.db_utils, 'get_reservations_by_device_id')
        self.get_reservations_by_device.return_value = []

        self.fake_dev_plugin.update_device(self.fake_device_id,
                                                 device_values)
        self.db_device_extra_capability_update.assert_called_once_with(
            'extra_id1', {'capability_value': 'baz'})

    def test_update_device_having_issue_when_storing_extra_capability(self):
        def fake_db_device_extra_capability_update(*args, **kwargs):
            raise RuntimeError
        device_values = {'foo': 'baz'}
        self.get_reservations_by_device = self.patch(
            self.db_utils, 'get_reservations_by_device_id')
        self.get_reservations_by_device.return_value = []
        self.db_device_extra_capability_get_all_per_name.return_value = [
            ({'id': 'extra_id1',
              'device_id': self.fake_device_id,
              'capability_value': 'bar'},
             'foo'),
        ]
        fake = self.db_device_extra_capability_update
        fake.side_effect = fake_db_device_extra_capability_update
        self.assertRaises(manager_exceptions.CantAddExtraCapability,
                          self.fake_dev_plugin.update_device,
                          self.fake_device_id, device_values)

    def test_update_device_with_new_extra_capability(self):
        device_values = {'qux': 'word'}

        self.db_device_extra_capability_get_all_per_device.return_value = []
        self.fake_dev_plugin.update_device(self.fake_device_id,
                                                 device_values)
        self.db_device_extra_capability_create.assert_called_once_with({
            'device_id': '1',
            'capability_name': 'qux',
            'capability_value': 'word'
        })

    def test_update_device_with_removed_capability(self):
        device_values = {'foo': None}

        self.db_device_extra_capability_get_all_per_name.return_value = [
            ({'id': 'extra_id1',
              'device_id': self.fake_device_id,
              'capability_value': 'bar'},
             'foo'),
        ]

        self.get_reservations_by_device = self.patch(
            self.db_utils, 'get_reservations_by_device_id')
        self.get_reservations_by_device.return_value = []

        self.fake_dev_plugin.update_device(self.fake_device_id,
                                                 device_values)
        self.db_device_extra_capability_destroy.assert_called_once_with(
            'extra_id1')

    def test_update_device_with_used_capability(self):
        device_values = {'foo': 'buzz'}

        self.db_device_extra_capability_get_all_per_name.return_value = [
            ({'id': 'extra_id1',
              'device_id': self.fake_device_id,
              'capability_value': 'bar'},
             'foo'),
        ]
        fake_phys_reservation = {
            'resource_type': plugin.RESOURCE_TYPE,
            'resource_id': 'resource-1',
        }

        fake_get_reservations = self.patch(self.db_utils,
                                           'get_reservations_by_device_id')
        fake_get_reservations.return_value = [fake_phys_reservation]

        fake_get_plugin_reservation = self.patch(self.db_utils,
                                                 'get_plugin_reservation')
        fake_get_plugin_reservation.return_value = {
            'resource_properties': '["==", "$foo", "bar"]'
        }
        self.assertRaises(manager_exceptions.CantAddExtraCapability,
                          self.fake_dev_plugin.update_device,
                          self.fake_device_id, device_values)
        fake_get_plugin_reservation.assert_called_once_with(
            plugin.RESOURCE_TYPE, 'resource-1')

    def test_delete_device(self):
        device_allocation_get_all = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = []
        self.fake_dev_plugin.delete_device(self.fake_device_id)

        self.db_device_destroy.assert_called_once_with(self.fake_device_id)

    def test_delete_device_reserved(self):
        device_allocation_get_all = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': self.fake_device_id
            }
        ]

        self.assertRaises(manager_exceptions.CantDeleteDevice,
                          self.fake_dev_plugin.delete_device,
                          self.fake_device_id)

    def test_delete_device_having_vms(self):
        device_allocation_get_all = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = []

    def test_delete_device_not_existing_in_db(self):
        self.db_device_get.return_value = None
        self.assertRaises(manager_exceptions.DeviceNotFound,
                          self.fake_dev_plugin.delete_device,
                          self.fake_device_id)

    def test_delete_device_issuing_rollback(self):
        def fake_db_device_destroy(*args, **kwargs):
            raise db_exceptions.BlazarDBException
        device_allocation_get_all = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = []
        self.db_device_destroy.side_effect = fake_db_device_destroy
        self.assertRaises(manager_exceptions.CantDeleteDevice,
                          self.fake_dev_plugin.delete_device,
                          self.fake_device_id)

    def test_list_allocations(self):
        self.db_get_reserv_allocs = self.patch(
            self.db_utils, 'get_reservation_allocations_by_device_ids')

        # Expecting a list of (Reservation, Allocation)
        self.db_get_reserv_allocs.return_value = [
            self.reservation_allocation_dict(*r) for r
            in [
                ('reservation-1', 'lease-1',
                 'project-1', ['device-1', 'device-2']),
                ('reservation-2', 'lease-1',
                 'project-1', ['device-2', 'device-3']),
                ('reservation-3', 'lease-2', 'project-2', ['device-1'])]]

        self.db_device_list = self.patch(self.db_api, 'device_list')
        self.db_device_list.return_value = [
            {'id': 'device-1'}, {'id': 'device-2'}, {'id': 'device-3'}]

        expected = [
            {
                'resource_id': 'device-1',
                'reservations': [
                    {'id': 'reservation-1',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                    {'id': 'reservation-3',
                        'lease_id': 'lease-2', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                ]
            },
            {
                'resource_id': 'device-2',
                'reservations': [
                    {'id': 'reservation-1',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                    {'id': 'reservation-2',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                ]
            },
            {
                'resource_id': 'device-3',
                'reservations': [
                    {'id': 'reservation-2',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                ]
            }
        ]
        ret = self.fake_dev_plugin.list_allocations({})

        # Sort returned value to use assertListEqual
        for r in ret:
            r['reservations'].sort(key=lambda x: x['id'])
        ret.sort(key=lambda x: x['resource_id'])

        self.assertListEqual(expected, ret)

    def test_list_allocations_with_lease_id(self):
        self.db_get_reserv_allocs = self.patch(
            self.db_utils, 'get_reservation_allocations_by_device_ids')

        # Expecting a list of (Reservation, Allocation)
        self.db_get_reserv_allocs.return_value = [
            self.reservation_allocation_dict(*r) for r
            in [
                ('reservation-1', 'lease-1',
                 'project-1', ['device-1', 'device-2']),
                ('reservation-2', 'lease-1',
                 'project-1', ['device-2', 'device-3'])]]

        self.db_device_list = self.patch(self.db_api, 'device_list')
        self.db_device_list.return_value = [
            {'id': 'device-1'}, {'id': 'device-2'}, {'id': 'device-3'}]

        expected = [
            {
                'resource_id': 'device-1',
                'reservations': [
                    {'id': 'reservation-1',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                ]
            },
            {
                'resource_id': 'device-2',
                'reservations': [
                    {'id': 'reservation-1',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                    {'id': 'reservation-2',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                ]
            },
            {
                'resource_id': 'device-3',
                'reservations': [
                    {'id': 'reservation-2',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                ]
            }
        ]
        ret = self.fake_dev_plugin.list_allocations({'lease_id': 'lease-1'})

        # Sort returned value to use assertListEqual
        for r in ret:
            r['reservations'].sort(key=lambda x: x['id'])
        ret.sort(key=lambda x: x['resource_id'])

        self.assertListEqual(expected, ret)

    def test_list_allocations_with_reservation_id(self):
        self.db_get_reserv_allocs = self.patch(
            self.db_utils, 'get_reservation_allocations_by_device_ids')

        # Expecting a list of (Reservation, Allocation)
        self.db_get_reserv_allocs.return_value = [
            self.reservation_allocation_dict(*r) for r
            in [
                ('reservation-1', 'lease-1',
                 'project-1', ['device-1', 'device-2'])]]

        self.db_device_list = self.patch(self.db_api, 'device_list')
        self.db_device_list.return_value = [{'id': 'device-1'}, {'id': 'device-2'}]

        expected = [
            {
                'resource_id': 'device-1',
                'reservations': [
                    {'id': 'reservation-1',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                ]
            },
            {
                'resource_id': 'device-2',
                'reservations': [
                    {'id': 'reservation-1',
                        'lease_id': 'lease-1', 'extras': {},
                        'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                ]
            },
        ]
        ret = self.fake_dev_plugin.list_allocations(
            {'reservation_id': 'reservation-1'})

        # Sort returned value to use assertListEqual
        for r in ret:
            r['reservations'].sort(key=lambda x: x['id'])
        ret.sort(key=lambda x: x['resource_id'])

        self.assertListEqual(expected, ret)

    def test_get_allocations(self):
        self.db_get_reserv_allocs = self.patch(
            self.db_utils, 'get_reservation_allocations_by_device_ids')

        # Expecting a list of (Reservation, Allocation)
        self.db_get_reserv_allocs.return_value = [
            self.reservation_allocation_dict(*r) for r
            in [
                ('reservation-1', 'lease-1',
                 'project-1', ['device-1', 'device-2']),
                ('reservation-2', 'lease-1',
                 'project-1', ['device-2', 'device-3']),
                ('reservation-3', 'lease-2', 'project-2', ['device-1'])]]

        self.db_device_list = self.patch(self.db_api, 'device_list')
        self.db_device_list.return_value = [
            {'id': 'device-1'}, {'id': 'device-2'}, {'id': 'device-3'}]

        expected = {
            'resource_id': 'device-1',
            'reservations': [
                {'id': 'reservation-1', 'lease_id': 'lease-1',
                    'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
                {'id': 'reservation-3', 'lease_id': 'lease-2',
                    'start_date': datetime.datetime(2015, 1, 1), 'end_date': datetime.datetime(2015, 1, 2)},
            ]
        }
        ret = self.fake_dev_plugin.get_allocations('device-1', {})

        # sort returned value to use assertListEqual
        ret['reservations'].sort(key=lambda x: x['id'])

        self.assertDictEqual(expected, ret)

    def test_get_allocations_with_lease_id(self):
        self.db_get_reserv_allocs = self.patch(
            self.db_utils, 'get_reservation_allocations_by_device_ids')

        # Expecting a list of (Reservation, Allocation)
        self.db_get_reserv_allocs.return_value = [
            self.reservation_allocation_dict(
                'reservation-1', 'lease-1', 'project-1', ['device-1']),
        ]

        self.db_device_list = self.patch(self.db_api, 'device_list')
        self.db_device_list.return_value = [{'id': 'device-1'}]

        expected = {
            'resource_id': 'device-1',
            'reservations': [
                {'id': 'reservation-1', 'lease_id': 'lease-1',
                    'start_date': datetime.datetime(2015, 1, 1, 0, 0), 'end_date': datetime.datetime(2015, 1, 2, 0, 0)}]}

        ret = self.fake_dev_plugin.get_allocations('device-1',
                                                    {'lease_id': 'lease-1'})

        # sort returned value to use assertListEqual
        ret['reservations'].sort(key=lambda x: x['id'])

        self.assertDictEqual(expected, ret)

    def test_get_allocations_with_reservation_id(self):
        self.db_get_reserv_allocs = self.patch(
            self.db_utils, 'get_reservation_allocations_by_device_ids')

        # Expecting a list of (Reservation, Allocation)
        self.db_get_reserv_allocs.return_value = [
            self.reservation_allocation_dict(
                'reservation-1', 'lease-1', 'project-1', ['device-1'])]

        self.db_device_list = self.patch(self.db_api, 'device_list')
        self.db_device_list.return_value = [{'id': 'device-1'}]

        expected = {
            'resource_id': 'device-1',
            'reservations': [
                {'id': 'reservation-1', 'lease_id': 'lease-1',
                    'start_date': datetime.datetime(2015, 1, 1, 0, 0), 'end_date': datetime.datetime(2015, 1, 2, 0, 0)}]}

        ret = self.fake_dev_plugin.get_allocations(
            'device-1', {'reservation_id': 'reservation-1'})

        # sort returned value to use assertListEqual
        ret['reservations'].sort(key=lambda x: x['id'])

        self.assertDictEqual(expected, ret)

    def test_get_allocations_with_invalid_device(self):
        self.db_get_reserv_allocs = self.patch(
            self.db_utils, 'get_reservation_allocations_by_device_ids')

        # Expecting a list of (Reservation, Allocation)
        self.db_get_reserv_allocs.return_value = [
            self.reservation_allocation_dict(*r) for r
            in [
                ('reservation-1', 'lease-1',
                 'project-1', ['device-1', 'device-2']),
                ('reservation-2', 'lease-1',
                 'project-1', ['device-2', 'device-3']),
                ('reservation-3', 'lease-2', 'project-2', ['device-1'])]]

        self.db_device_list = self.patch(self.db_api, 'device_list')
        self.db_device_list.return_value = [
            {'id': 'device-1'}, {'id': 'device-2'}, {'id': 'device-3'}]

        expected = {'resource_id': 'no-reserved-device', 'reservations': []}
        ret = self.fake_dev_plugin.get_allocations('no-reserved-device', {})

        self.assertDictEqual(expected, ret)

    def test_create_reservation_no_devices_available(self):
        now = datetime.datetime.utcnow()
        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 1,
            'max': 1,
            'resource_properties': '["=", "$memory_mb", "256"]',
            'start_date': now,
            'end_date': now + datetime.timedelta(hours=1),
            'resource_type': plugin.RESOURCE_TYPE,
            'project_id': 'fake-project'
        }
        device_reservation_create = self.patch(self.db_api,
                                             'device_reservation_create')
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = []
        self.assertRaises(manager_exceptions.NotEnoughDevicesAvailable,
                          self.fake_dev_plugin.reserve_resource,
                          'f9894fcf-e2ed-41e9-8a4c-92fac332608e',
                          values)
        device_reservation_create.assert_not_called()

    def test_create_reservation_devices_available(self):
        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 1,
            'max': 1,
            'resource_properties': '["=", "$memory_mb", "256"]',
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00),
            'resource_type': plugin.RESOURCE_TYPE,
            'project_id': 'fake-project'
        }
        device_reservation_create = self.patch(self.db_api,
                                             'device_reservation_create')
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = ['device1', 'device2']
        device_allocation_create = self.patch(
            self.db_api,
            'device_allocation_create')
        self.fake_dev_plugin.reserve_resource(
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)
        device_values = {
            'reservation_id': '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            'resource_properties': '["=", "$memory_mb", "256"]',
            'count_range': '1-1',
            'status': 'pending',
            'before_end': 'default',
        }
        device_reservation_create.assert_called_once_with(device_values)
        calls = [
            mock.call(
                {'device_id': 'device1',
                 'reservation_id': '441c1476-9f8f-4700-9f30-cd9b6fef3509',
                 }),
            mock.call(
                {'device_id': 'device2',
                 'reservation_id': '441c1476-9f8f-4700-9f30-cd9b6fef3509',
                 }),
        ]
        device_allocation_create.assert_has_calls(calls)

    def test_create_reservation_devices_non_reservable(self):
        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 1,
            'max': 1,
            'resource_properties': '["=", "$memory_mb", "256"]',
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00),
            'resource_type': plugin.RESOURCE_TYPE,
            'project_id': 'fake-project'
        }
        device_reservation_create = self.patch(self.db_api,
                                             'device_reservation_create')
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = ['device1', 'device2']
        device_allocation_create = self.patch(
            self.db_api,
            'device_allocation_create')
        is_admin = self.patch(
            policy, 'enforce'
        )
        is_admin.return_value = False
        self.fake_dev_plugin.reserve_resource(
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)
        device_values = {
            'reservation_id': '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            'resource_properties': '["=", "$memory_mb", "256"]',
            'count_range': '1-1',
            'status': 'pending',
            'before_end': 'default',
        }
        device_reservation_create.assert_called_once_with(device_values)
        calls = [
            mock.call(
                {'device_id': 'device1',
                 'reservation_id': '441c1476-9f8f-4700-9f30-cd9b6fef3509',
                 }),
            mock.call(
                {'device_id': 'device2',
                 'reservation_id': '441c1476-9f8f-4700-9f30-cd9b6fef3509',
                 }),
        ]
        device_allocation_create.assert_has_calls(calls)

    @ddt.data({"params": {'max': 0}},
              {"params": {'max': -1}},
              {"params": {'max': 'one'}},
              {"params": {'min': 0}},
              {"params": {'min': -1}},
              {"params": {'min': 'one'}},
              {"params": {'before_end': 'invalid'}})
    @ddt.unpack
    def test_create_reservation_with_invalid_param(self, params):
        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 1,
            'max': 2,
            'before_end': 'default',
            'resource_properties': '["=", "$memory_mb", "256"]',
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': plugin.RESOURCE_TYPE}
        for key, value in params.items():
            values[key] = value
        self.patch(db_api, 'device_allocation_get_all_by_values')
        self.assertRaises(
            manager_exceptions.MalformedParameter,
            self.fake_dev_plugin.reserve_resource,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)

    @ddt.data({"params": {'max': 0}},
              {"params": {'max': -1}},
              {"params": {'max': 'one'}},
              {"params": {'min': 0}},
              {"params": {'min': -1}},
              {"params": {'min': 'one'}})
    @ddt.unpack
    def test_update_reservation_with_invalid_param(self, params):
        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 1,
            'max': 2,
            'before_end': 'default',
            'resource_properties': '["=", "$memory_mb", "256"]',
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': plugin.RESOURCE_TYPE}
        self.patch(self.db_api, 'reservation_get')
        self.patch(self.db_api, 'lease_get')
        device_reservation_get = self.patch(self.db_api,
                                          'device_reservation_get')
        device_reservation_get.return_value = {
            'count_range': '1-1',
            'resource_properties': '["=", "$memory_mb", "256"]',
        }
        for key, value in params.items():
            values[key] = value
        self.patch(db_api, 'device_allocation_get_all_by_values')
        self.assertRaises(
            manager_exceptions.MalformedParameter,
            self.fake_dev_plugin.update_reservation,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)

    def test_create_update_reservation_with_invalid_range(self):
        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 2,
            'max': 1,
            'resource_properties': '["=", "$memory_mb", "256"]',
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': plugin.RESOURCE_TYPE,
        }
        self.patch(self.db_api, 'reservation_get')
        self.patch(self.db_api, 'lease_get')
        self.patch(db_api, 'device_allocation_get_all_by_values')
        device_reservation_get = self.patch(self.db_api,
                                          'device_reservation_get')
        device_reservation_get.return_value = {
            'count_range': '1-1',
            'resource_properties': '["=", "$memory_mb", "256"]',
        }
        self.assertRaises(
            manager_exceptions.InvalidRange,
            self.fake_dev_plugin.reserve_resource,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)
        self.assertRaises(
            manager_exceptions.InvalidRange,
            self.fake_dev_plugin.update_reservation,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)

    def test_update_reservation_shorten(self):
        values = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 30),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00)
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00)
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_reservation_get.assert_not_called()

    def test_update_reservation_extend(self):
        values = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 30)
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'count_range': '1-1',
            'resource_properties': '["=", "$memory_mb", "256"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [{'id': 'device1'}]
        get_reserved_periods = self.patch(self.db_utils,
                                          'get_reserved_periods')
        get_reserved_periods.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00))
        ]
        device_allocation_create = self.patch(
            self.db_api,
            'device_allocation_create')
        device_allocation_destroy = self.patch(
            self.db_api,
            'device_allocation_destroy')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_allocation_create.assert_not_called()
        device_allocation_destroy.assert_not_called()

    def test_update_reservation_move_failure(self):
        values = {
            'start_date': datetime.datetime(2013, 12, 20, 20, 00),
            'end_date': datetime.datetime(2013, 12, 20, 21, 30),
            'project_id': 'fake-project'
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'active'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(
            self.db_api,
            'device_reservation_get')
        device_reservation_get.return_value = {
            'count_range': '1-1',
            'resource_properties': '["=", "$memory_mb", "256"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [{'id': 'device1'}]
        get_reserved_periods = self.patch(self.db_utils,
                                          'get_reserved_periods')
        get_reserved_periods.return_value = [
            (datetime.datetime(2013, 12, 20, 20, 30),
             datetime.datetime(2013, 12, 20, 21, 00))
        ]
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = []
        self.assertRaises(
            manager_exceptions.NotEnoughDevicesAvailable,
            self.fake_dev_plugin.update_reservation,
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        reservation_get.assert_called()

    def test_update_reservation_move_overlap(self):
        values = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 30),
            'end_date': datetime.datetime(2013, 12, 19, 21, 30)
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(
            self.db_api,
            'device_reservation_get')
        device_reservation_get.return_value = {
            'count_range': '1-1',
            'resource_properties': '["=", "$memory_mb", "256"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [{'id': 'device1'}]
        get_reserved_periods = self.patch(self.db_utils,
                                          'get_reserved_periods')
        get_reserved_periods.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 30),
             datetime.datetime(2013, 12, 19, 21, 00))
        ]
        device_allocation_create = self.patch(
            self.db_api,
            'device_allocation_create')
        device_allocation_destroy = self.patch(
            self.db_api,
            'device_allocation_destroy')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_allocation_create.assert_not_called()
        device_allocation_destroy.assert_not_called()

    def test_update_reservation_move_realloc(self):
        values = {
            'start_date': datetime.datetime(2013, 12, 20, 20, 00),
            'end_date': datetime.datetime(2013, 12, 20, 21, 30)
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(
            self.db_api,
            'device_reservation_get')
        device_reservation_get.return_value = {
            'count_range': '1-1',
            'resource_properties': '["=", "$memory_mb", "256"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [{'id': 'device1'},
                                                {'id': 'device2'}]
        device_allocation_create = self.patch(
            self.db_api,
            'device_allocation_create')
        device_allocation_destroy = self.patch(
            self.db_api,
            'device_allocation_destroy')
        get_reserved_periods = self.patch(self.db_utils,
                                          'get_reserved_periods')
        get_reserved_periods.return_value = [
            (datetime.datetime(2013, 12, 20, 20, 30),
             datetime.datetime(2013, 12, 20, 21, 00))
        ]
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = ['device2']
        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_reservation_get.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b')
        device_allocation_destroy.assert_called_with(
            'dd305477-4df8-4547-87f6-69069ee546a6')
        device_allocation_create.assert_called_with(
            {
                'device_id': 'device2',
                'reservation_id': '706eb3bc-07ed-4383-be93-b32845ece672'
            }
        )

    def test_update_reservation_min_increase_success(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'min': 3
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '2-3',
            'resource_properties': '["=", "$memory_mb", "16384"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            },
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a7',
                'device_id': 'device2'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'}
        ]
        device_allocation_destroy = self.patch(self.db_api,
                                             'device_allocation_destroy')
        device_allocation_create = self.patch(self.db_api,
                                            'device_allocation_create')
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = ['device3']
        device_reservation_update = self.patch(self.db_api,
                                             'device_reservation_update')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_reservation_get.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b')
        matching_devices.assert_called_with(
            '["=", "$memory_mb", "16384"]',
            '1-1',
            datetime.datetime(2017, 7, 12, 20, 00),
            datetime.datetime(2017, 7, 12, 21, 00),
            'fake-project'
        )
        device_allocation_destroy.assert_not_called()
        device_allocation_create.assert_called_with(
            {
                'device_id': 'device3',
                'reservation_id': '706eb3bc-07ed-4383-be93-b32845ece672'
            }
        )
        device_reservation_update.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            {'count_range': '3-3'}
        )

    def test_update_reservation_min_increase_fail(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'min': 3
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '2-3',
            'resource_properties': '["=", "$memory_mb", "16384"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            },
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a7',
                'device_id': 'device2'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [
            {'id': 'device1'},
            {'id': 'device2'}
        ]
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = []

        self.assertRaises(
            manager_exceptions.NotEnoughDevicesAvailable,
            self.fake_dev_plugin.update_reservation,
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        matching_devices.assert_called_with(
            '["=", "$memory_mb", "16384"]',
            '1-1',
            datetime.datetime(2017, 7, 12, 20, 00),
            datetime.datetime(2017, 7, 12, 21, 00),
            'fake-project'
        )

    def test_update_reservation_min_decrease(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'min': 1
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '2-2',
            'resource_properties': '["=", "$memory_mb", "16384"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            },
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a7',
                'device_id': 'device2'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [
            {'id': 'device1'},
            {'id': 'device2'}
        ]
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        device_allocation_destroy = self.patch(self.db_api,
                                             'device_allocation_destroy')
        device_allocation_create = self.patch(self.db_api,
                                            'device_allocation_create')
        device_reservation_update = self.patch(self.db_api,
                                             'device_reservation_update')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        matching_devices.assert_not_called()
        device_allocation_destroy.assert_not_called()
        device_allocation_create.assert_not_called()
        device_reservation_update.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            {'count_range': '1-2'}
        )

    def test_update_reservation_max_increase_alloc(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'max': 3
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '1-2',
            'resource_properties': '["=", "$memory_mb", "16384"]'
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            },
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a7',
                'device_id': 'device2'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'}
        ]
        device_allocation_destroy = self.patch(self.db_api,
                                             'device_allocation_destroy')
        device_allocation_create = self.patch(self.db_api,
                                            'device_allocation_create')
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = ['device3']
        device_reservation_update = self.patch(self.db_api,
                                             'device_reservation_update')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_reservation_get.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b')
        matching_devices.assert_called_with(
            '["=", "$memory_mb", "16384"]',
            '0-1',
            datetime.datetime(2017, 7, 12, 20, 00),
            datetime.datetime(2017, 7, 12, 21, 00),
            'fake-project'
        )
        device_allocation_destroy.assert_not_called()
        device_allocation_create.assert_called_with(
            {
                'device_id': 'device3',
                'reservation_id': '706eb3bc-07ed-4383-be93-b32845ece672'
            }
        )
        device_reservation_update.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            {'count_range': '1-3'}
        )

    def test_update_active_reservation_max_increase_alloc(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'max': 3
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'active'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '1-2',
            'resource_properties': '["=", "$memory_mb", "16384"]',
            'reservation_id': '706eb3bc-07ed-4383-be93-b32845ece672',
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1',
                "name": "rpi1",
            },
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a7',
                'device_id': 'device2',
                "name": "rpi1",
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'}
        ]
        device_allocation_destroy = self.patch(self.db_api,
                                             'device_allocation_destroy')
        device_allocation_create = self.patch(self.db_api,
                                            'device_allocation_create')
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = ['device3']
        device_get = self.patch(self.db_api, 'device_get')
        device_get.return_value = {
            'service_name': 'service1',
            "device_driver": "k8s",
            "name": "rpi1",
        }
        add_device = self.patch(
            self.fake_k8s_plugin, 'add_active_device')
        device_reservation_update = self.patch(self.db_api,
                                             'device_reservation_update')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_reservation_get.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b')
        matching_devices.assert_called_with(
            '["=", "$memory_mb", "16384"]',
            '0-1',
            datetime.datetime(2017, 7, 12, 20, 00),
            datetime.datetime(2017, 7, 12, 21, 00),
            'fake-project'
        )
        device_allocation_destroy.assert_not_called()
        device_allocation_create.assert_called_with(
            {
                'device_id': 'device3',
                'reservation_id': '706eb3bc-07ed-4383-be93-b32845ece672'
            }
        )
        add_device.assert_called_with(
            device_get.return_value,
            device_reservation_get.return_value,
            lease_get.return_value,
        )
        device_reservation_update.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            {'count_range': '1-3'}
        )

    def test_update_reservation_max_increase_noalloc(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'max': 3
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '1-2',
            'resource_properties': '["=", "$memory_mb", "16384"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            },
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a7',
                'device_id': 'device2'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [
            {'id': 'device1'},
            {'id': 'device2'}
        ]
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = []
        device_reservation_update = self.patch(self.db_api,
                                             'device_reservation_update')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_reservation_get.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b')
        matching_devices.assert_called_with(
            '["=", "$memory_mb", "16384"]',
            '0-1',
            datetime.datetime(2017, 7, 12, 20, 00),
            datetime.datetime(2017, 7, 12, 21, 00),
            'fake-project'
        )
        device_reservation_update.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            {'count_range': '1-3'}
        )

    def test_update_reservation_max_decrease(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'max': 1
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '1-2',
            'resource_properties': '["=", "$memory_mb", "16384"]',
            'project_id': 'fake-project'
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            },
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a7',
                'device_id': 'device2'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [
            {'id': 'device1'},
            {'id': 'device2'}
        ]
        device_allocation_destroy = self.patch(self.db_api,
                                             'device_allocation_destroy')
        device_reservation_update = self.patch(self.db_api,
                                             'device_reservation_update')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_reservation_get.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b')
        device_allocation_destroy.assert_called_with(
            'dd305477-4df8-4547-87f6-69069ee546a6')
        device_reservation_update.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            {'count_range': '1-1'}
        )

    def test_update_reservation_realloc_with_properties_change(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'resource_properties': '["=", "$memory_mb", "32768"]',
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '1-1',
            'resource_properties': '["=", "$memory_mb", "16384"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = [{'id': 'device2'}]
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = ['device2']
        device_allocation_create = self.patch(self.db_api,
                                            'device_allocation_create')
        device_allocation_destroy = self.patch(self.db_api,
                                             'device_allocation_destroy')
        device_reservation_update = self.patch(self.db_api,
                                             'device_reservation_update')

        self.fake_dev_plugin.update_reservation(
            '706eb3bc-07ed-4383-be93-b32845ece672',
            values)
        device_reservation_get.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b')
        matching_devices.assert_called_with(
            '["=", "$memory_mb", "32768"]',
            '1-1',
            datetime.datetime(2017, 7, 12, 20, 00),
            datetime.datetime(2017, 7, 12, 21, 00),
            'fake-project'
        )
        device_allocation_create.assert_called_with(
            {
                'device_id': 'device2',
                'reservation_id': '706eb3bc-07ed-4383-be93-b32845ece672'
            }
        )
        device_allocation_destroy.assert_called_with(
            'dd305477-4df8-4547-87f6-69069ee546a6'
        )
        device_reservation_update.assert_called_with(
            '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            {'resource_properties': '["=", "$memory_mb", "32768"]'}
        )

    def test_update_reservation_no_requested_devices_available(self):
        values = {
            'start_date': datetime.datetime(2017, 7, 12, 20, 00),
            'end_date': datetime.datetime(2017, 7, 12, 21, 00),
            'resource_properties': '[">=", "$vcpus", "32768"]'
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = {
            'lease_id': '10870923-6d56-45c9-b592-f788053f5baa',
            'resource_id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'status': 'pending'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2013, 12, 19, 20, 00),
            'end_date': datetime.datetime(2013, 12, 19, 21, 00),
            'project_id': 'fake-project'
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '91253650-cc34-4c4f-bbe8-c943aa7d0c9b',
            'count_range': '1-1',
            'resource_properties': '["=", "$memory_mb", "16384"]',
        }
        device_allocation_get_all = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all.return_value = [
            {
                'id': 'dd305477-4df8-4547-87f6-69069ee546a6',
                'device_id': 'device1'
            }
        ]
        device_get_all_by_queries = self.patch(self.db_api,
                                             'device_get_all_by_queries')
        device_get_all_by_queries.return_value = []
        matching_devices = self.patch(self.fake_dev_plugin, '_matching_devices')
        matching_devices.return_value = []

        self.assertRaises(
            manager_exceptions.NotEnoughDevicesAvailable,
            self.fake_dev_plugin.update_reservation,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)

    def test_on_start(self):
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'reservation_id': '593e7028-c0d1-4d76-8642-2ffd890b324c',
            "device_driver": "k8s",
        }
        device_allocation_get_all_by_values = self.patch(
            self.db_api, 'device_allocation_get_all_by_values')
        device_allocation_get_all_by_values.return_value = [
            {
                'device_id': 'device1',
                "device_driver": "k8s",
            },
        ]
        device_get = self.patch(self.db_api, 'device_get')
        device_get.return_value = {
            "device_driver": "k8s",
            "name": "rpi1",
        }

        self.fake_dev_plugin.on_start('04de74e8-193a-49d2-9ab8-cba7b49e45e8', lease={
            "project_id": "fake-project",
        })

    def test_before_end_with_no_action(self):
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {'before_end': ''}
        fake_lease = {'project_id': 'fake-project'}
        self.fake_dev_plugin.before_end(
            '04de74e8-193a-49d2-9ab8-cba7b49e45e8', lease=fake_lease)

    def test_before_end_with_email(self):
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'before_end': ''
        }
        fake_lease = {'project_id': 'fake-project', "user_id": "fake-user"}
        self.fake_dev_plugin.before_end(
            '04de74e8-193a-49d2-9ab8-cba7b49e45e8', lease=fake_lease)

    def test_on_end_with_instances(self):
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '04de74e8-193a-49d2-9ab8-cba7b49e45e8',
            'reservation_id': '593e7028-c0d1-4d76-8642-2ffd890b324c',
        }
        device_reservation_update = self.patch(
            self.db_api,
            'device_reservation_update')
        device_allocation_get_all_by_values = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all_by_values.return_value = [
            {'id': 'bfa9aa0b-8042-43eb-a4e6-4555838bf64f',
             'device_id': 'cdae2a65-236f-475a-977d-f6ad82f828b7',
             },
        ]
        device_allocation_destroy = self.patch(
            self.db_api,
            'device_allocation_destroy')
        
        self.fake_dev_plugin.on_end(
            '04de74e8-193a-49d2-9ab8-cba7b49e45e8',
            lease={
                "project_id": "fake-project",
            },
        )
        device_reservation_update.assert_called_with(
            '04de74e8-193a-49d2-9ab8-cba7b49e45e8', {'status': 'completed'})
        device_allocation_destroy.assert_called_with(
            'bfa9aa0b-8042-43eb-a4e6-4555838bf64f')

    def test_on_end_without_instances(self):
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = {
            'id': '04de74e8-193a-49d2-9ab8-cba7b49e45e8',
            'reservation_id': '593e7028-c0d1-4d76-8642-2ffd890b324c',
        }
        device_reservation_update = self.patch(
            self.db_api,
            'device_reservation_update')
        device_allocation_get_all_by_values = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_allocation_get_all_by_values.return_value = [
            {'id': 'bfa9aa0b-8042-43eb-a4e6-4555838bf64f',
             'device_id': 'cdae2a65-236f-475a-977d-f6ad82f828b7',
             },
        ]
        device_allocation_destroy = self.patch(
            self.db_api,
            'device_allocation_destroy')
        self.fake_dev_plugin.on_end(
            '04de74e8-193a-49d2-9ab8-cba7b49e45e8',
            lease={
                "project_id": "fake-project",
            },

        )
        device_reservation_update.assert_called_with(
            '04de74e8-193a-49d2-9ab8-cba7b49e45e8', {'status': 'completed'})
        device_allocation_destroy.assert_called_with(
            'bfa9aa0b-8042-43eb-a4e6-4555838bf64f')

    @skip # these tests pass when ran individually
    def test_heal_reservations_before_start_and_resources_changed(self):
        failed_device = {'id': '1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': plugin.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'resource_properties': [],
            'resource_id': 'resource-1',
            'device_allocations': [{
                'id': 'alloc-1', 'device_id': failed_device['id'],
                'reservation_id': 'rsrv-1'
            }]
        }
        get_reservations = self.patch(self.db_utils,
                                      'get_reservations_by_device_ids')
        get_reservations.return_value = [dummy_reservation]
        reallocate = self.patch(self.fake_dev_plugin.monitor, '_reallocate')
        reallocate.return_value = True
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation

        dummy_device_reservation = {
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = dummy_device_reservation

        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]

        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')

        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        self.patch(self.db_api, 'device_allocation_update')

        result = self.fake_dev_plugin.monitor.heal_reservations(
            [failed_device],
            datetime.datetime(2020, 1, 1, 12, 00),
            datetime.datetime(2020, 1, 1, 13, 00))
        reallocate.assert_called_once_with(
            dummy_reservation['device_allocations'][0])
        self.assertEqual({}, result)

    @skip
    def test_heal_reservations_before_start_and_missing_resources(self):
        failed_device = {'id': '1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': plugin.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'resource_properties': [],
            'resource_id': 'resource-1',
            'device_allocations': [{
                'id': 'alloc-1', 'device_id': failed_device['id'],
                'reservation_id': 'rsrv-1'
            }]
        }
        get_reservations = self.patch(self.db_utils,
                                      'get_reservations_by_device_ids')
        get_reservations.return_value = [dummy_reservation]
        reallocate = self.patch(self.fake_dev_plugin.monitor, '_reallocate')
        reallocate.return_value = False
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation

        dummy_device_reservation = {
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = dummy_device_reservation

        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]

        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')

        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        self.patch(self.db_api, 'device_allocation_update')

        result = self.fake_dev_plugin.monitor.heal_reservations(
            [failed_device],
            datetime.datetime(2020, 1, 1, 12, 00),
            datetime.datetime(2020, 1, 1, 13, 00))
        reallocate.assert_called_once_with(
            dummy_reservation['device_allocations'][0])
        self.assertEqual(
            {dummy_reservation['id']: {'missing_resources': True}},
            result)

    def test_heal_active_reservations_and_resources_changed(self):
        failed_device = {'id': '1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': plugin.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'active',
            'resource_properties': [],
            'resource_id': 'resource-1',
            'device_allocations': [{
                'id': 'alloc-1', 'device_id': failed_device['id'],
                'reservation_id': 'rsrv-1'
            }]
        }
        get_reservations = self.patch(self.db_utils,
                                      'get_reservations_by_device_ids')
        get_reservations.return_value = [dummy_reservation]

        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation

        dummy_device_reservation = {
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = dummy_device_reservation

        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]

        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')

        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]

        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = dummy_lease

        reallocate = self.patch(self.fake_dev_plugin.monitor, '_reallocate')
        reallocate.return_value = True

        self.patch(self.db_api, 'device_allocation_update')

        result = self.fake_dev_plugin.monitor.heal_reservations(
            [failed_device],
            datetime.datetime(2020, 1, 1, 12, 00),
            datetime.datetime(2020, 1, 1, 13, 00))
        # skipped for leases which are active d285bc9
        reallocate.assert_not_called()
        self.assertEqual({}, result)

    def test_heal_active_reservations_and_missing_resources(self):
        failed_device = {'id': '1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': plugin.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'active',
            'resource_properties': [],
            'resource_id': 'resource-1',
            'device_allocations': [{
                'id': 'alloc-1', 'device_id': failed_device['id'],
                'reservation_id': 'rsrv-1'
            }]
        }
        get_reservations = self.patch(self.db_utils,
                                      'get_reservations_by_device_ids')
        get_reservations.return_value = [dummy_reservation]
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation
        reallocate = self.patch(self.fake_dev_plugin.monitor, '_reallocate')
        reallocate.return_value = False
        dummy_device_reservation = {
        }
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = dummy_device_reservation
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]
        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')

        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]
        self.patch(self.db_api, 'device_allocation_update')

        result = self.fake_dev_plugin.monitor.heal_reservations(
            [failed_device],
            datetime.datetime(2020, 1, 1, 12, 00),
            datetime.datetime(2020, 1, 1, 13, 00))
        # skipped for leases which are active d285bc9
        reallocate.assert_not_called()
        self.assertEqual({}, result)

    def test_reallocate_before_start(self):
        failed_device = {'id': '1'}
        new_device = {'id': '2'}
        dummy_allocation = {
            'id': 'alloc-1',
            'device_id': failed_device['id'],
            'reservation_id': 'rsrv-1',
        }
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': plugin.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'resource_properties': [],
            'resource_id': 'resource-1'
        }
        dummy_device_reservation = {
            "resource_properties": [],
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1',
            'project_id': 'fake-project'
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = dummy_device_reservation
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        matching_devices = self.patch(device_plugin.DevicePlugin,
                                    '_matching_devices')
        matching_devices.return_value = [new_device['id']]
        alloc_update = self.patch(self.db_api, 'device_allocation_update')

        with mock.patch.object(datetime, 'datetime',
                               mock.Mock(wraps=datetime.datetime)) as patched:
            patched.utcnow.return_value = datetime.datetime(
                2020, 1, 1, 11, 00)
            result = self.fake_dev_plugin._reallocate(dummy_allocation)

        matching_devices.assert_called_once_with(
            dummy_reservation['resource_properties'],
            '1-1', dummy_lease['start_date'], dummy_lease['end_date'],
            dummy_lease['project_id'],
        )
        alloc_update.assert_called_once_with(
            dummy_allocation['id'],
            {'device_id': new_device['id']})
        self.assertEqual(True, result)

    def test_reallocate_active(self):
        failed_device = {'id': '1',
                       "device_driver": "k8s",
                       "name": "rpi1",
                       }
        new_device = {'id': '2',
                    "device_driver": "k8s",
                    "name": "rpi1"
                    }
        dummy_allocation = {
            'id': 'alloc-1',
            'device_id': failed_device['id'],
            'reservation_id': 'rsrv-1'
        }
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': plugin.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'active',
            'resource_properties': [],
            'resource_id': 'resource-1',
        }
        dummy_device_reservation = {
            "resource_properties": [],
            "reservation_id": 'rsrv-1',
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1',
            'project_id': 'fake-project'
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = dummy_device_reservation
        device_get = self.patch(self.db_api, 'device_get')
        device_get.side_effect = [failed_device, new_device]
        matching_devices = self.patch(device_plugin.DevicePlugin,
                                    '_matching_devices')
        matching_devices.return_value = [new_device['id']]
        alloc_update = self.patch(self.db_api, 'device_allocation_update')

        with mock.patch.object(datetime, 'datetime',
                               mock.Mock(wraps=datetime.datetime)) as patched:
            patched.utcnow.return_value = datetime.datetime(
                2020, 1, 1, 13, 00)
            result = self.fake_dev_plugin._reallocate(dummy_allocation)

        matching_devices.assert_called_once_with(
            dummy_reservation['resource_properties'],
            '1-1', datetime.datetime(2020, 1, 1, 13, 00),
            dummy_lease['end_date'],
            'fake-project',
        )
        alloc_update.assert_called_once_with(
            dummy_allocation['id'],
            {'device_id': new_device['id']})
        self.assertEqual(True, result)

    def test_reallocate_missing_resources(self):
        failed_device = {'id': '1'}
        dummy_allocation = {
            'id': 'alloc-1',
            'device_id': failed_device['id'],
            'reservation_id': 'rsrv-1'
        }
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': plugin.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'resource_properties': [],
            'resource_id': 'resource-1'
        }
        dummy_device_reservation = {
            'resource_properties': [],
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1',
            'project_id': 'fake-project'
        }
        reservation_get = self.patch(self.db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation
        device_reservation_get = self.patch(self.db_api, 'device_reservation_get')
        device_reservation_get.return_value = dummy_device_reservation
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        matching_devices = self.patch(device_plugin.DevicePlugin,
                                    '_matching_devices')
        matching_devices.return_value = []
        alloc_destroy = self.patch(self.db_api, 'device_allocation_destroy')

        with mock.patch.object(datetime, 'datetime',
                               mock.Mock(wraps=datetime.datetime)) as patched:
            patched.utcnow.return_value = datetime.datetime(
                2020, 1, 1, 11, 00)
            result = self.fake_dev_plugin._reallocate(dummy_allocation)

        matching_devices.assert_called_once_with(
            dummy_reservation['resource_properties'],
            '1-1', dummy_lease['start_date'], dummy_lease['end_date'],
            dummy_lease['project_id'],
        )
        alloc_destroy.assert_called_once_with(dummy_allocation['id'])
        self.assertEqual(False, result)

    def test_matching_devices_not_allocated_devices(self):
        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]
        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]
        is_admin = self.patch(
            policy, 'enforce'
        )
        is_admin.return_value = False
        result = self.fake_dev_plugin._matching_devices(
            '[]', '1-3',
            datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00),
            'fake-project'
        )
        self.assertEqual(set(['device2', 'device3']), set(result))

    def test_matching_devices_allocated_devices(self):
        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]
        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]
        is_admin = self.patch(
            policy, 'enforce'
        )
        is_admin.return_value = False
        result = self.fake_dev_plugin._matching_devices(
            '[]', '3-3',
            datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00),
            None
        )
        self.assertEqual(set(['device1', 'device2', 'device3']), set(result))

    @mock.patch.object(random, "shuffle")
    def test_matching_devices_allocated_devices_with_cleaning_time(self, mock_shuffle):
        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        self.cfg.CONF.set_override('cleaning_time', '5')
        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]
        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_get.side_effect = device_allocation_get_all_by_values
        get_free_periods = self.patch(
            self.db_utils,
            'get_free_periods')
        get_free_periods.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00)
             - datetime.timedelta(minutes=5),
             datetime.datetime(2013, 12, 19, 21, 00)
             + datetime.timedelta(minutes=5))
        ]
        is_admin = self.patch(
            policy, 'enforce'
        )
        is_admin.return_value = False
        result = self.fake_dev_plugin._matching_devices(
            '[]', '3-3',
            datetime.datetime(2013, 12, 19, 20, 00) - datetime.timedelta(minutes=5),
            datetime.datetime(2013, 12, 19, 21, 00) + datetime.timedelta(minutes=5),
            None)
        mock_shuffle.assert_called_once_with(['device1', 'device2', 'device3'])
        self.addCleanup(CONF.clear_override, 'cleaning_time')


    @mock.patch.object(random, "shuffle")
    def test_random_matching_devices_not_allocated_devices(self, mock_shuffle):
        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]
        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]
        is_admin = self.patch(
            policy, 'enforce'
        )
        is_admin.return_value = False
        self.fake_dev_plugin._matching_devices(
            '[]', '1-3',
            datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00),
            None)
        mock_shuffle.assert_called_once_with(['device2', 'device3'])

    @mock.patch.object(random, "shuffle")
    def test_random_matching_devices_allocated_devices(self, mock_shuffle):
        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]
        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]
        is_admin = self.patch(
            policy, 'enforce'
        )
        is_admin.return_value = False
        self.fake_dev_plugin._matching_devices(
            '[]', '3-3',
            datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00),
            None)
        mock_shuffle.assert_called_once_with(['device1', 'device2', 'device3'])

    @mock.patch.object(random, "shuffle")
    def test_random_matching_devices_allocated_cleaning_time(self, mock_shuffle):
        def device_allocation_get_all_by_values(**kwargs):
            if kwargs['device_id'] == 'device1':
                return True
        self.cfg.CONF.set_override('cleaning_time', '5')
        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = [
            {'id': 'device1'},
            {'id': 'device2'},
            {'id': 'device3'},
        ]
        device_get = self.patch(
            self.db_api,
            'device_allocation_get_all_by_values')
        device_get.side_effect = device_allocation_get_all_by_values
        device_get = self.patch(
            self.db_utils,
            'get_free_periods')
        device_get.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00)
             - datetime.timedelta(minutes=5),
             datetime.datetime(2013, 12, 19, 21, 00)
             + datetime.timedelta(minutes=5))
        ]
        is_admin = self.patch(
            policy, 'enforce'
        )
        is_admin.return_value = False
        self.fake_dev_plugin._matching_devices(
            '[]', '2-3',
            datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00),
            None)
        self.addCleanup(CONF.clear_override, 'cleaning_time')
        mock_shuffle.assert_called_once_with(['device2', 'device3'])

    def test_matching_devices_not_matching(self):
        device_get = self.patch(
            self.db_api,
            'reservable_device_get_all_by_queries')
        device_get.return_value = []
        is_admin = self.patch(
            policy, 'enforce'
        )
        is_admin.return_value = False
        result = self.fake_dev_plugin._matching_devices(
            '["=", "$memory_mb", "2048"]', '1-1',
            datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00),
            None
        )
        self.assertEqual([], result)

    def test_check_params_with_valid_before_end(self):
        values = {
            'min': 1,
            'max': 2,
            'resource_properties': '',
            'before_end': 'email'
        }
        self.fake_dev_plugin._check_params(values)
        self.assertEqual(values['before_end'], 'email')

    def test_check_params_with_invalid_before_end(self):
        values = {
            'min': 1,
            'max': 2,
            'resource_properties': '',
            'before_end': 'invalid'
        }
        self.assertRaises(manager_exceptions.MalformedParameter,
                          self.fake_dev_plugin._check_params,
                          values)

    def test_check_params_without_before_end(self):
        self.cfg.CONF.set_override('before_end', '',
                                   group='device')
        values = {
            'min': 1,
            'max': 2,
            'resource_properties': '',
        }
        self.fake_dev_plugin._check_params(values)
        self.assertEqual(values['before_end'], 'default')

    def test_list_resource_properties(self):
        self.db_list_resource_properties = self.patch(
            self.db_api, 'resource_properties_list')

        # Expecting a list of (Reservation, Allocation)
        self.db_list_resource_properties.return_value = [
            ('prop1', False, 'aaa', False),
            ('prop1', False, 'bbb', False),
            ('prop2', False, 'aaa', False),
            ('prop2', False, 'aaa', False),
            ('prop3', True, 'aaa', False)
        ]

        expected = [
            {'property': 'prop1',},
            {'property': 'prop2',}
        ]

        ret = self.fake_dev_plugin.list_resource_properties(
            query={'detail': False})

        # Sort returned value to use assertListEqual
        ret.sort(key=lambda x: x['property'])

        self.assertListEqual(expected, ret)
        self.db_list_resource_properties.assert_called_once_with(
            'device')

    def test_list_resource_properties_with_detail(self):
        self.db_list_resource_properties = self.patch(
            self.db_api, 'resource_properties_list')

        # Expecting a list of (Reservation, Allocation)
        self.db_list_resource_properties.return_value = [
            ('prop1', False, 'aaa', False),
            ('prop1', False, 'bbb', False),
            ('prop2', False, 'ccc', False),
            ('prop3', True, 'aaa', False)
        ]

        expected = [
            {'property': 'prop1', 'private': False, 'values': ['aaa', 'bbb'], 'is_unique': False},
            {'property': 'prop2', 'private': False, 'values': ['ccc'], 'is_unique': False}
        ]

        ret = self.fake_dev_plugin.list_resource_properties(
            query={'detail': True})

        # Sort returned value to use assertListEqual
        ret.sort(key=lambda x: x['property'])

        self.assertListEqual(expected, ret)
        self.db_list_resource_properties.assert_called_once_with(
            'device')

    def test_update_resource_property(self):
        resource_property_values = {
            'resource_type': 'device',
            'private': False}

        db_resource_property_update = self.patch(
            self.db_api, 'resource_property_update')

        self.fake_dev_plugin.update_resource_property(
            'foo', resource_property_values)
        db_resource_property_update.assert_called_once_with(
            'device', 'foo', resource_property_values)
