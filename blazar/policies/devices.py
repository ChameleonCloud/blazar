#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from oslo_policy import policy

from blazar.policies import base

POLICY_ROOT = 'blazar:devices:%s'

devices_policies = [
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'get',
        check_str=base.RULE_ADMIN,
        description='Policy rule for List/Show Device(s) API.',
        operations=[
            {
                'path': '/{api_version}/devices',
                'method': 'GET'
            },
            {
                'path': '/{api_version}/devices/{device_id}',
                'method': 'GET'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'post',
        check_str=base.RULE_ADMIN,
        description='Policy rule for Create Device API.',
        operations=[
            {
                'path': '/{api_version}/devices',
                'method': 'POST'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'put',
        check_str=base.RULE_ADMIN,
        description='Policy rule for Update Host API.',
        operations=[
            {
                'path': '/{api_version}/devices/{device_id}',
                'method': 'PUT'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'delete',
        check_str=base.RULE_ADMIN,
        description='Policy rule for Delete Device API.',
        operations=[
            {
                'path': '/{api_version}/devices/{device_id}',
                'method': 'DELETE'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'get_allocations',
        check_str=base.RULE_ADMIN,
        description='Policy rule for List/Get Device(s) Allocations API.',
        operations=[
            {
                'path': '/{api_version}/devices/allocations',
                'method': 'GET'
            },
            {
                'path': '/{api_version}/devices/{device_id}/allocation',
                'method': 'GET'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'reallocate',
        check_str=base.RULE_ADMIN,
        description='Policy rule for Reallocate Host API.',
        operations=[
            {
                'path': '/{api_version}/devices/{device_id}/allocation',
                'method': 'PUT'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'get_resource_properties',
        check_str=base.RULE_ADMIN,
        description='Policy rule for Resource Properties API.',
        operations=[
            {
                'path': '/{api_version}/devices/resource_properties',
                'method': 'GET'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=POLICY_ROOT % 'patch_resource_properties',
        check_str=base.RULE_ADMIN,
        description='Policy rule for Resource Properties API.',
        operations=[
            {
                'path': ('/{api_version}/devices/resource_properties/'
                         '{property_name}'),
                'method': 'PATCH'
            }
        ]
    ),
]


def list_rules():
    return devices_policies
