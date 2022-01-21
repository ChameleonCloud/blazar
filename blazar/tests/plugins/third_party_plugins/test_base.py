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
import ddt
from novaclient import client as nova_client
from oslo_config import cfg
import random
from unittest import mock

from blazar import context
from blazar.db import api as db_api
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_exceptions
from blazar.plugins.third_party_plugins.base import BasePlugin
import blazar.plugins.third_party_plugins.exceptions as tpp_ex
from blazar import status
from blazar import tests
from blazar.utils.openstack import nova
from blazar.utils import trusts

from blazar import policy

CONF = cfg.CONF


class TestPluginImpl(BasePlugin):
    def resource_type(self):
        return "test_resource"

    def get_query_options(self, params, index_type):
        return {"lease_id": "my_lease_id"}


@ddt.ddt
class BasePluginTest(tests.TestCase):

    def setUp(self):
        super(BasePluginTest, self).setUp()
        self.cfg = cfg
        self.context = context
        self.patch(self.context, 'BlazarContext')

        self.nova_client = nova_client
        self.nova_client = self.patch(self.nova_client, 'Client').return_value

        self.trusts = trusts
        self.trust_ctx = self.patch(self.trusts, 'create_ctx_from_trust')
        self.trust_create = self.patch(self.trusts, 'create_trust')

        self.fake_resource = {
            'id': 1,
            'data': {"id": "hypvsr1"},
        }

        self.db_api = db_api
        self.db_resource_list = self.patch(self.db_api, 'resource_list')
        self.db_resource_list.return_value = [self.fake_resource]
        self.db_resource_get = self.patch(self.db_api, 'resource_get')
        self.db_resource_get.return_value = self.fake_resource
        self.db_resource_create = self.patch(self.db_api, 'resource_create')
        self.db_resource_create.return_value = self.fake_resource
        self.db_resource_resource_property_get_all_per_resource = self.patch(
            self.db_api, "resource_resource_property_get_all_per_resource")
        self.db_resource_resource_property_get_all_per_resource.return_value =\
            []
        self.db_resource_update = self.patch(self.db_api, 'resource_update')
        self.db_resource_resource_property_create = self.patch(
            self.db_api, 'resource_resource_property_create')
        self.db_resource_allocation_get_all_by_values = self.patch(
            self.db_api, 'resource_allocation_get_all_by_values')
        self.db_resource_allocation_get_all_by_values.return_value = []
        self.db_resource_destroy = self.patch(self.db_api, 'resource_destroy')
        self.db_resource_reservation_create = self.patch(
            self.db_api, 'resource_reservation_create')
        self.db_resource_reservation_get = self.patch(
            self.db_api, 'resource_reservation_get')
        self.db_resource_allocation_create = self.patch(
            self.db_api, 'resource_allocation_create')

        self.db_utils = db_utils
        self.db_utils_get_reservation_allocations_by_resource_ids = self.patch(
            self.db_utils, "get_reservation_allocations_by_resource_ids")

        self.enforce = self.patch(policy, "check_enforcement")

        self.base_date = datetime.datetime.strptime(
            '2012-12-13 13:13', '%Y-%m-%d %H:%M')

        self.ServerManager = nova.ServerManager

        self.plugin = TestPluginImpl()

    def test_validate_data(self):
        # Extra key
        self.assertRaises(
            tpp_ex.InvalidCreateResourceData,
            self.plugin.validate_data,
            {"a": 1}, [], []
        )
        # Missing required key
        self.assertRaises(
            tpp_ex.InvalidCreateResourceData,
            self.plugin.validate_data,
            {"a": 1}, ["b"], ["a"]
        )
        # Valid
        ret = self.plugin.validate_data({"a": 1}, ["a"], ["b"])
        self.assertDictEqual({"a": 1}, ret)

    def test_on_start(self):
        self.patch(self.plugin, '_get_resources')
        plugin_allocate = self.patch(self.plugin, 'allocate')

        self.plugin.on_start("1")
        plugin_allocate.assert_called()
        self.db_resource_reservation_get.assert_called()

    def test_on_end(self):
        self.patch(self.plugin, '_get_resources')
        plugin_deallocate = self.patch(self.plugin, 'deallocate')

        self.db_resource_reservation_get.return_value = {
            "id": 1,
            "reservation_id": 2
        }
        db_resource_reservation_update = self.patch(
            self.db_api, 'resource_reservation_update')
        db_resource_allocation_get_all_by_values = self.patch(
            self.db_api, "resource_allocation_get_all_by_values")
        db_resource_allocation_get_all_by_values.return_value = [
            {"id": 100}, {"id": 101}
        ]
        db_resource_allocation_destroy = self.patch(
            self.db_api, "resource_allocation_destroy")

        self.plugin.on_end(1)

        plugin_deallocate.assert_called()
        self.db_resource_reservation_get.assert_called()
        # Reservation status is updated
        db_resource_reservation_update.assert_called_once_with(
            1, {'status': 'completed'})
        # All allocations are destroyed
        db_resource_allocation_get_all_by_values.assert_called()
        db_resource_allocation_destroy.assert_has_calls([
            mock.call(100),
            mock.call(101),
        ])

    def test_matching_resources_not_allocated(self):
        def resource_allocation_get_all_by_values(**kwargs):
            return kwargs['resource_id'] == 'resource1'

        resource_get_all = self.patch(
            self.db_api, "resource_get_all_by_queries")
        resource_get_all.return_value = [
            {"id": "resource1"},
            {"id": "resource2"},
            {"id": "resource3"},
        ]

        resource_allocation_get_all = self.patch(
            self.db_api, "resource_allocation_get_all_by_values")
        resource_allocation_get_all.side_effect = \
            resource_allocation_get_all_by_values

        get_free_periods = self.patch(self.db_utils, 'get_free_periods')
        get_free_periods.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 21, 00)),
        ]

        random.seed(1)
        result = self.plugin.matching_resources(
            '[]', datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00), '2', '2')
        self.assertEqual(['resource2', 'resource3'], result)

    def test_matching_resources_cleaning_times(self):
        def resource_allocation_get_all_by_values(**kwargs):
            return kwargs['resource_id'] == 'resource1'
        self.cfg.CONF.set_override('cleaning_time', '5')
        resource_get_all = self.patch(
            self.db_api, "resource_get_all_by_queries")
        resource_get_all.return_value = [
            {"id": "resource1"},
            {"id": "resource2"},
            {"id": "resource3"},
        ]

        resource_allocation_get_all = self.patch(
            self.db_api, "resource_allocation_get_all_by_values")
        resource_allocation_get_all.side_effect = \
            resource_allocation_get_all_by_values

        get_free_periods = self.patch(self.db_utils, 'get_free_periods')
        get_free_periods.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00)
             - datetime.timedelta(minutes=5),
             datetime.datetime(2013, 12, 19, 21, 00)
             + datetime.timedelta(minutes=5))
        ]

        random.seed(1)
        result = self.plugin.matching_resources(
            '[]', datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00), '2', '2')
        self.assertEqual(['resource3', 'resource2'], result)

    def test_matching_resources_min_max(self):
        def resource_allocation_get_all_by_values(**kwargs):
            return kwargs['resource_id'] == 'resource1'
        resource_get_all = self.patch(
            self.db_api, "resource_get_all_by_queries")
        resource_get_all.return_value = [
            {"id": "resource1"},
            {"id": "resource2"},
            {"id": "resource3"},
        ]

        resource_allocation_get_all = self.patch(
            self.db_api, "resource_allocation_get_all_by_values")
        resource_allocation_get_all.side_effect = \
            resource_allocation_get_all_by_values

        get_free_periods = self.patch(self.db_utils, 'get_free_periods')
        get_free_periods.return_value = [
            (datetime.datetime(2013, 12, 19, 20, 00),
             datetime.datetime(2013, 12, 19, 22, 00)),
        ]

        self.assertRaises(
            tpp_ex.NotEnoughResourcesAvailable,
            self.plugin.matching_resources,
            '[]',
            datetime.datetime(2013, 12, 19, 20, 00),
            datetime.datetime(2013, 12, 19, 21, 00), '3', '3')

    def test_api_create(self):
        data = {
            "data": {"id": "hypvsr1"},
        }
        host = self.plugin.api_create(data)
        self.db_resource_create.assert_called_once_with(
            "test_resource", data["data"])
        self.enforce.assert_called_once_with("test_resource", "post")
        self.assertEqual(self.fake_resource, host)

    def test_api_list(self):
        self.plugin.api_list()
        self.db_resource_list.assert_called_once_with("test_resource")
        self.enforce.assert_called_once_with("test_resource", "get")

    def test_api_get(self):
        self.plugin.api_get(self.fake_resource["id"])
        self.enforce.assert_called_once_with("test_resource", "get")
        self.db_resource_get.assert_called_once_with(
            "test_resource", self.fake_resource["id"])
        self.db_resource_resource_property_get_all_per_resource\
            .assert_called_once_with(self.fake_resource["id"])

    def test_api_update_with_no_update(self):
        actual = self.plugin.api_update(
            self.fake_resource["id"], {"data": {}, "extras": {}})
        self.assertEqual(None, actual)

    def test_api_update(self):
        data = {"name": "test"}
        extras = {"key1": "val1"}
        self.plugin.api_update(
            self.fake_resource["id"], {"data": data, "extras": extras})
        self.enforce.assert_called_once_with("test_resource", "put")
        self.db_resource_update.assert_called_once_with(
            "test_resource", self.fake_resource["id"], data)
        self.db_resource_resource_property_create.assert_called_once_with(
            "test_resource",
            {
                "resource_id": self.fake_resource["id"],
                "capability_name": "key1",
                "capability_value": "val1",
            })

    def test_api_delete_with_allocations(self):
        self.db_resource_allocation_get_all_by_values.return_value = ["alloc"]
        self.assertRaises(
            tpp_ex.CantDeleteResource,
            self.plugin.api_delete,
            self.fake_resource["id"]
        )
        self.enforce.assert_called_once_with("test_resource", "delete")

    def test_api_delete(self):
        self.db_resource_allocation_get_all_by_values.return_value = []
        self.plugin.api_delete(self.fake_resource["id"])
        self.db_resource_destroy.assert_called_once_with(
            "test_resource", self.fake_resource["id"])
        self.enforce.assert_called_once_with("test_resource", "delete")

    def test_list_allocations(self):
        self.db_resource_list.return_value = [
            {"id": "resource-1"},
            {"id": 'resource-2'},
            {"id": 'resource-3'}
        ]
        self.db_utils_get_reservation_allocations_by_resource_ids\
            .return_value = [
                {
                    'id': 'reservation-1',
                    'lease_id': 'lease-1',
                    'start_date': datetime.datetime(2021, 8, 20, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 30, 20, 0),
                    'resource_ids': ['resource-1', 'resource-2']
                },
                {
                    'id': 'reservation-2',
                    'lease_id': 'lease-2',
                    'start_date': datetime.datetime(2021, 8, 20, 16, 34),
                    'end_date': datetime.datetime(2021, 8, 21, 16, 34),
                    'resource_ids': ['resource-3']
                },
                {
                    'id': 'reservation-3',
                    'lease_id': 'lease-3',
                    'start_date': datetime.datetime(2021, 8, 19, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 27, 20, 18),
                    'resource_ids': ['resource-1']
                },
            ]
        expected = [
            {
                'resource_id': 'resource-1',
                'reservations': [
                    {
                        'id': 'reservation-1',
                        'lease_id': 'lease-1',
                        'start_date': datetime.datetime(2021, 8, 20, 20, 18),
                        'end_date': datetime.datetime(2021, 8, 30, 20, 0),
                    },
                    {
                        'id': 'reservation-3',
                        'lease_id': 'lease-3',
                        'start_date': datetime.datetime(2021, 8, 19, 20, 18),
                        'end_date': datetime.datetime(2021, 8, 27, 20, 18),
                    },
                ]
            },
            {
                'resource_id': 'resource-2',
                'reservations': [
                    {
                        'id': 'reservation-1',
                        'lease_id': 'lease-1',
                        'start_date': datetime.datetime(2021, 8, 20, 20, 18),
                        'end_date': datetime.datetime(2021, 8, 30, 20, 0),
                    },
                ]
            },
            {
                'resource_id': 'resource-3',
                'reservations': [
                    {
                        'id': 'reservation-2',
                        'lease_id': 'lease-2',
                        'start_date': datetime.datetime(2021, 8, 20, 16, 34),
                        'end_date': datetime.datetime(2021, 8, 21, 16, 34),
                    },
                ]
            }
        ]

        with mock.patch.object(datetime, 'datetime',
                               mock.Mock(wraps=datetime.datetime)) as patched:
            patched.utcnow.return_value = self.base_date

            res = self.plugin.list_allocations({})

            self.db_utils_get_reservation_allocations_by_resource_ids\
                .assert_called_once_with(
                    ["resource-1", "resource-2", "resource-3"],
                    self.base_date,
                    datetime.date.max,
                    "my_lease_id",
                    None
                )
            self.assertListEqual(expected, res)

    def test_api_list_allocations(self):
        self.patch(self.plugin, "list_allocations")
        self.plugin.api_list_allocations({})
        self.enforce.assert_called_once_with(
            "test_resource", "get_allocations")

    def test_api_get_allocations(self):
        self.db_resource_list.return_value = [
            {"id": "resource-1"},
            {"id": 'resource-2'},
            {"id": 'resource-3'}
        ]
        self.db_utils_get_reservation_allocations_by_resource_ids\
            .return_value = [
                {
                    'id': 'reservation-1',
                    'lease_id': 'lease-1',
                    'start_date': datetime.datetime(2021, 8, 20, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 30, 20, 0),
                    'resource_ids': ['resource-1', 'resource-2']
                },
                {
                    'id': 'reservation-2',
                    'lease_id': 'lease-2',
                    'start_date': datetime.datetime(2021, 8, 20, 16, 34),
                    'end_date': datetime.datetime(2021, 8, 21, 16, 34),
                    'resource_ids': ['resource-3']
                },
                {
                    'id': 'reservation-3',
                    'lease_id': 'lease-3',
                    'start_date': datetime.datetime(2021, 8, 19, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 27, 20, 18),
                    'resource_ids': ['resource-1']
                },
            ]
        expected = {
            'resource_id': 'resource-1',
            'reservations': [
                {
                    'id': 'reservation-1',
                    'lease_id': 'lease-1',
                    'start_date': datetime.datetime(2021, 8, 20, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 30, 20, 0),
                },
                {
                    'id': 'reservation-3',
                    'lease_id': 'lease-3',
                    'start_date': datetime.datetime(2021, 8, 19, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 27, 20, 18),
                },
            ]
        }

        with mock.patch.object(datetime, 'datetime',
                               mock.Mock(wraps=datetime.datetime)) as patched:
            patched.utcnow.return_value = self.base_date

            res = self.plugin.api_get_allocations("resource-1", {})

            self.db_utils_get_reservation_allocations_by_resource_ids\
                .assert_called_once_with(
                    ["resource-1"],
                    self.base_date,
                    datetime.date.max,
                    "my_lease_id",
                    None
                )
            self.enforce.assert_called_once_with(
                "test_resource", "get_allocations")
            self.assertDictEqual(expected, res)

    def test_api_reallocate(self):
        self.patch(self.plugin, "get_allocations").return_value = {
            'resource_id': 'resource-1',
            'reservations': [
                {
                    'id': 'reservation-1',
                    'lease_id': 'lease-1',
                    'start_date': datetime.datetime(2021, 8, 20, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 30, 20, 0),
                    "status": status.reservation.PENDING
                },
                {
                    'id': 'reservation-2',
                    'lease_id': 'lease-2',
                    'start_date': datetime.datetime(2021, 8, 20, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 30, 20, 0),
                    "status": status.reservation.ACTIVE
                },
                {
                    'id': 'reservation-3',
                    'lease_id': 'lease-3',
                    'start_date': datetime.datetime(2021, 8, 19, 20, 18),
                    'end_date': datetime.datetime(2021, 8, 27, 20, 18),
                    "status": status.reservation.PENDING
                },
            ]
        }
        self.patch(self.db_api, "resource_allocation_get_all_by_values")
        responses = iter([True, True, False])

        def reallocate_side_effect(*args, **kwargs):
            return next(responses)
        self.patch(
            self.plugin, "reallocate").side_effect = reallocate_side_effect
        lease_update = self.patch(self.db_api, 'lease_update')
        reservation_update = self.patch(self.db_api, 'reservation_update')

        self.plugin.api_reallocate(self.fake_resource["id"], {})

        self.enforce.assert_called_once_with("test_resource", "reallocate")
        lease_update.assert_has_calls([
            mock.call("lease-2", dict(degraded=True)),
            mock.call("lease-3", dict(degraded=True)),
        ])
        reservation_update.assert_has_calls([
            mock.call("reservation-1", {}),
            mock.call("reservation-2", dict(resources_changed=True)),
            mock.call("reservation-3", dict(missing_resources=True)),
        ])

    def test_api_list_resource_properties(self):
        self.db_list_resource_properties = self.patch(
            self.db_api, 'resource_properties_list')

        self.db_list_resource_properties.return_value = [
            ('prop1', False, 'aaa'),
            ('prop1', False, 'bbb'),
            ('prop2', False, 'aaa'),
            ('prop2', False, 'aaa'),
            ('prop3', True, 'aaa')
        ]

        expected = [
            {'property': 'prop1'},
            {'property': 'prop2'}
        ]

        ret = self.plugin.api_list_resource_properties(
            query={'detail': False})

        # Sort returned value to use assertListEqual
        ret.sort(key=lambda x: x['property'])

        self.assertListEqual(expected, ret)
        self.enforce.assert_called_once_with(
            "test_resource", "get_resource_properties")
        self.db_list_resource_properties.assert_called_once_with(
            'test_resource')

    def test_api_list_resource_properties_with_detail(self):
        self.db_list_resource_properties = self.patch(
            self.db_api, 'resource_properties_list')

        self.db_list_resource_properties.return_value = [
            ('prop1', False, 'aaa'),
            ('prop1', False, 'bbb'),
            ('prop2', False, 'ccc'),
        ]

        expected = [
            {'property': 'prop1', 'private': False, 'values': ['aaa', 'bbb']},
            {'property': 'prop2', 'private': False, 'values': ['ccc']}
        ]

        ret = self.plugin.api_list_resource_properties(
            query={'detail': True})

        # Sort returned value to use assertListEqual
        ret.sort(key=lambda x: x['property'])

        self.assertListEqual(expected, ret)
        self.enforce.assert_called_once_with(
            "test_resource", "get_resource_properties")
        self.db_list_resource_properties.assert_called_once_with(
            'test_resource')

    def test_api_update_resource_property(self):
        resource_property_values = {
            'resource_type': 'test_resource',
            'private': False}

        db_resource_property_update = self.patch(
            self.db_api, 'resource_property_update')

        self.plugin.api_update_resource_property(
            'foo', resource_property_values)
        db_resource_property_update.assert_called_once_with(
            'test_resource', 'foo', resource_property_values)
        self.enforce.assert_called_once_with(
            "test_resource", "patch_resource_properties")

    def test_reserve_resources_no_resources(self):
        now = datetime.datetime.utcnow()
        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 1,
            'max': 1,
            'resource_properties': '',
            'start_date': now,
            'end_date': now + datetime.timedelta(hours=1),
            'resource_type': "test_resource",
        }
        plugin_allocation_candidates = self.patch(
            self.plugin, "allocation_candidates")
        plugin_allocation_candidates.return_value = []

        self.assertRaises(tpp_ex.NotEnoughResourcesAvailable,
                          self.plugin.reserve_resource,
                          'f9894fcf-e2ed-41e9-8a4c-92fac332608e',
                          values)
        self.db_resource_reservation_create.assert_not_called()

    def test_reserve_resources(self):
        now = datetime.datetime.utcnow()
        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 2,
            'max': 2,
            'resource_properties': '',
            'start_date': now,
            'end_date': now + datetime.timedelta(hours=1),
            'resource_type': "test_resource",
        }
        plugin_allocation_candidates = self.patch(
            self.plugin, "allocation_candidates")
        plugin_allocation_candidates.return_value = [1, 2]

        reservation_id = 'f9894fcf-e2ed-41e9-8a4c-92fac332608e'
        self.db_resource_reservation_create.return_value = {
            "id": "cd9a9d9d-d0b9-419f-b686-3cd001847288",
            "reservation_id": reservation_id}

        self.plugin.reserve_resource(
            reservation_id, values)
        self.db_resource_reservation_create.assert_called()
        calls = [
            mock.call({"resource_id": 1, "reservation_id": reservation_id}),
            mock.call({"resource_id": 2, "reservation_id": reservation_id}),
        ]
        self.db_resource_allocation_create.assert_has_calls(calls)

    def test_update_reservation_with_invalid_param(self):
        self.patch(self.db_api, 'reservation_get')
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
        }

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': -1,
            'max': 2,
            'before_end': 'default',
            'resource_properties': '',
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}

        self.assertRaises(
            manager_exceptions.MalformedParameter,
            self.plugin.update_reservation,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': "a",
            'max': 2,
            'before_end': 'default',
            'resource_properties': '',
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}

        self.assertRaises(
            manager_exceptions.MalformedParameter,
            self.plugin.update_reservation,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 3,
            'max': 2,
            'before_end': 'default',
            'resource_properties': '',
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}

        self.assertRaises(
            manager_exceptions.InvalidRange,
            self.plugin.update_reservation,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'min': 3,
            'max': 2,
            'before_end': 'default',
            'resource_properties': '',
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}

        self.assertRaises(
            manager_exceptions.InvalidRange,
            self.plugin.update_reservation,
            '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            values)

    def test_update_reservation_no_update(self):
        self.patch(self.db_api, 'reservation_get')
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
        }

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}

        self.plugin.update_reservation(
            '441c1476-9f8f-4700-9f30-cd9b6fef3509', values)
        # Never needed to get reservation
        self.db_resource_reservation_get.assert_not_called()

    def test_get(self):
        plugin_extra_cap = self.patch(self.plugin, "_get_extra_capabilities")
        plugin_extra_cap.return_value = {"hello": "world"}
        ret = self.plugin.get(self.fake_resource["id"])
        expected = {
            'id': 1,
            'data': {"id": "hypvsr1"},
            "hello": "world",
        }
        self.assertDictEqual(expected, ret)

    def test__update_allocations_increase(self):
        self.db_resource_allocation_get_all_by_values.return_value = [
            {"id": 100}
        ]
        plugin_allocs_to_remove = self.patch(
            self.plugin, "_allocations_to_remove")
        plugin_allocs_to_remove.return_value = []

        self.patch(self.plugin, "matching_resources").return_value = [
            'resource1', "resource2"
        ]
        plugin_reserve_new_resources = self.patch(
            self.plugin, "reserve_new_resources")
        plugin_allocate = self.patch(self.plugin, 'allocate')
        plugin_deallocate = self.patch(self.plugin, 'deallocate')
        db_resource_allocation_destroy = self.patch(
            self.db_api, "resource_allocation_destroy")

        lease = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
        }
        resource_reservation = {
            "reservation_id": "reservation1",
            "count_range": "1-1",
            "resource_properties": "[]"
        }

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            "min": 2,
            "max": 2,
            "resource_properties": "[]",
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}
        start_date = datetime.datetime(2017, 3, 1, 20, 00)
        end_date = datetime.datetime(2017, 3, 2, 20, 00)

        dates_before = {'start_date': start_date, 'end_date': end_date}
        dates_after = {'start_date': start_date, 'end_date': end_date}

        self.plugin._update_allocations(
            dates_before, dates_after, '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            status.reservation.PENDING, resource_reservation, values, lease)
        db_resource_allocation_destroy.assert_not_called()
        plugin_deallocate.assert_not_called()
        plugin_allocate.assert_not_called()
        plugin_reserve_new_resources.assert_called_once_with(
            "reservation1", ["resource1", "resource2"]
        )

    def test__update_allocations_increase_while_active(self):
        self.db_resource_allocation_get_all_by_values.return_value = [
            {"id": 100}
        ]
        plugin_allocs_to_remove = self.patch(
            self.plugin, "_allocations_to_remove")
        plugin_allocs_to_remove.return_value = []

        self.patch(self.plugin, "matching_resources").return_value = [
            'resource1', "resource2"
        ]
        plugin_reserve_new_resources = self.patch(
            self.plugin, "reserve_new_resources")
        plugin_allocate = self.patch(self.plugin, 'allocate')
        self.patch(self.plugin, 'deallocate')
        db_resource_allocation_destroy = self.patch(
            self.db_api, "resource_allocation_destroy")

        lease = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
        }
        resource_reservation = {
            "reservation_id": "reservation1",
            "count_range": "1-1",
            "resource_properties": "[]"
        }

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            "min": 2,
            "max": 2,
            "resource_properties": "[]",
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}
        start_date = datetime.datetime(2017, 3, 1, 20, 00)
        end_date = datetime.datetime(2017, 3, 2, 20, 00)

        dates_before = {'start_date': start_date, 'end_date': end_date}
        dates_after = {'start_date': start_date, 'end_date': end_date}

        self.plugin._update_allocations(
            dates_before, dates_after, '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            status.reservation.ACTIVE, resource_reservation, values, lease)
        db_resource_allocation_destroy.assert_not_called()
        plugin_allocate.assert_called_once_with(
            resource_reservation, [self.fake_resource, self.fake_resource]
        )
        plugin_reserve_new_resources.assert_called_once_with(
            "reservation1", ["resource1", "resource2"]
        )

    def test__update_allocations_decrease(self):
        self.db_resource_allocation_get_all_by_values.return_value = [
            {"id": 100},
            {"id": 101}
        ]
        plugin_allocs_to_remove = self.patch(
            self.plugin, "_allocations_to_remove")
        plugin_allocs_to_remove.return_value = [{"id": "101"}]

        self.patch(self.plugin, "matching_resources").return_value = [
            'resource1'
        ]
        plugin_reserve_new_resources = self.patch(
            self.plugin, "reserve_new_resources")
        plugin_allocate = self.patch(self.plugin, 'allocate')
        plugin_deallocate = self.patch(self.plugin, 'deallocate')
        db_resource_allocation_destroy = self.patch(
            self.db_api, "resource_allocation_destroy")

        lease = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
        }
        resource_reservation = {
            "reservation_id": "reservation1",
            "count_range": "2-2",
            "resource_properties": "[]"
        }

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            "min": 1,
            "max": 1,
            "resource_properties": "[]",
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}
        start_date = datetime.datetime(2017, 3, 1, 20, 00)
        end_date = datetime.datetime(2017, 3, 2, 20, 00)

        dates_before = {'start_date': start_date, 'end_date': end_date}
        dates_after = {'start_date': start_date, 'end_date': end_date}

        self.plugin._update_allocations(
            dates_before, dates_after, '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            status.reservation.PENDING, resource_reservation, values, lease)
        db_resource_allocation_destroy.assert_called_once_with('101')
        plugin_deallocate.assert_not_called()
        plugin_allocate.assert_not_called()
        plugin_reserve_new_resources.assert_not_called()

    def test__update_allocations_decrease_while_active(self):
        self.db_resource_allocation_get_all_by_values.return_value = [
            {"id": 100},
            {"id": 101}
        ]
        plugin_allocs_to_remove = self.patch(
            self.plugin, "_allocations_to_remove")
        plugin_allocs_to_remove.return_value = [{"id": "101"}]

        self.patch(self.plugin, "matching_resources").return_value = [
            'resource1'
        ]
        self.patch(
            self.plugin, "reserve_new_resources")
        self.patch(self.plugin, 'allocate')
        self.patch(self.plugin, 'deallocate')
        self.patch(
            self.db_api, "resource_allocation_destroy")

        lease = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
        }
        resource_reservation = {
            "reservation_id": "reservation1",
            "count_range": "2-2",
            "resource_properties": "[]"
        }

        values = {
            'lease_id': '018c1b43-e69e-4aef-a543-09681539cf4c',
            "min": 1,
            "max": 1,
            "resource_properties": "[]",
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            'resource_type': "test_resource"}
        start_date = datetime.datetime(2017, 3, 1, 20, 00)
        end_date = datetime.datetime(2017, 3, 2, 20, 00)

        dates_before = {'start_date': start_date, 'end_date': end_date}
        dates_after = {'start_date': start_date, 'end_date': end_date}

        self.assertRaises(
            tpp_ex.NotEnoughResourcesAvailable,
            self.plugin._update_allocations,
            dates_before, dates_after, '441c1476-9f8f-4700-9f30-cd9b6fef3509',
            status.reservation.ACTIVE, resource_reservation, values, lease)

    def test_reallocate_no_candidates(self):
        self.patch(self.db_api, "reservation_get").return_value = {
            "id": "reservation-1",
            "resource_id": "resource_reservation-1",
            "lease_id": "lease-1",
            "status": status.reservation.PENDING,
        }
        self.db_resource_reservation_get.return_value = {
            "values": {"resource_properties": []},
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            "name": "lease-1",
        }
        db_resource_allocation_destroy = self.patch(
            self.db_api, "resource_allocation_destroy")
        self.patch(self.plugin, "matching_resources").return_value = []

        allocation = {
            "id": "allocation-1",
            "reservation_id": "reservation-1"
        }
        ret = self.plugin.reallocate(allocation)
        self.assertEqual(False, ret)
        db_resource_allocation_destroy.assert_called_once_with(
            allocation["id"])

    def test_reallocate_pending(self):
        self.patch(self.db_api, "reservation_get").return_value = {
            "id": "reservation-1",
            "resource_id": "resource_reservation-1",
            "lease_id": "lease-1",
            "status": status.reservation.PENDING,
        }
        self.db_resource_reservation_get.return_value = {
            "values": {"resource_properties": []},
        }
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            "name": "lease-1",
        }
        db_resource_allocation_update = self.patch(
            self.db_api, "resource_allocation_update")
        self.patch(self.plugin, "matching_resources").return_value = [
            "resource-1"
        ]

        allocation = {
            "id": "allocation-1",
            "reservation_id": "reservation-1"
        }
        ret = self.plugin.reallocate(allocation)
        self.assertEqual(True, ret)
        db_resource_allocation_update.assert_called_once_with(
            allocation["id"], {'resource_id': "resource-1"}
        )

    def test_reallocate_active(self):
        self.patch(self.db_api, "reservation_get").return_value = {
            "id": "reservation-1",
            "resource_id": "resource_reservation-1",
            "lease_id": "lease-1",
            "status": status.reservation.ACTIVE,
        }
        resource_reservation = {
            "id": "resource_reservation-1",
            "values": {"resource_properties": []},
        }
        self.db_resource_reservation_get.return_value = resource_reservation
        lease_get = self.patch(self.db_api, 'lease_get')
        lease_get.return_value = {
            'start_date': datetime.datetime(2017, 3, 1, 20, 00),
            'end_date': datetime.datetime(2017, 3, 2, 20, 00),
            "name": "lease-1",
        }
        db_resource_allocation_update = self.patch(
            self.db_api, "resource_allocation_update")
        self.patch(self.plugin, "matching_resources").return_value = [
            "resource-1"
        ]
        plugin_allocate = self.patch(self.plugin, 'allocate')

        allocation = {
            "id": "allocation-1",
            "reservation_id": "reservation-1"
        }
        ret = self.plugin.reallocate(allocation)
        self.assertEqual(True, ret)
        db_resource_allocation_update.assert_called_once_with(
            allocation["id"], {'resource_id': "resource-1"}
        )
        plugin_allocate.assert_called_once_with(
            resource_reservation, [self.fake_resource])
