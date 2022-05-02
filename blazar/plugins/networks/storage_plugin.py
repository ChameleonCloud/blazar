# -*- coding: utf-8 -*-
#
# Author: Chameleon Cloud
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
from blazar.manager import exceptions as manager_ex
from blazar.utils.openstack import manila
from blazar.utils.openstack import neutron
import collections
from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import periodic_task

opts = [
    cfg.IntOpt('set_manila_share_access_rules_interval',
               default=5*60,
               help='Set access rules for all manila shares every N seconds.'
                    'If this number is negative the periodic task will be '
                    'disabled.'),
    cfg.StrOpt('ceph_nfs_share_type',
               default='default_share_type',
               help='The Ceph NFS share type'),
    cfg.StrOpt('storage_subnetpool',
               default='filesystem-subnet-pool',
               help='The storage subnet pool name'),
    cfg.StrOpt('storage_router',
               default='filesystem-ganesha-router',
               help='The storage router name'),
]

CONF = cfg.CONF
CONF.register_opts(opts, group="network_storage")
LOG = logging.getLogger(__name__)

STORAGE_ROUTER_NAME = "storage_router_{network_segment_id}"


class StoragePlugin():
    """Plugin for storage usage type."""
    usage_type = "storage"

    def __init__(self):
        super(StoragePlugin, self).__init__()
        self.neutron_client = neutron.BlazarNeutronClient()
        self.manila_client = manila.BlazarManilaClient()
        # get ganesha subnetpool by name
        ganesha_subnetpool = self.neutron_client.list_subnetpools(
            name=CONF.network_storage.storage_subnetpool
        ).get("subnetpools")
        self.ganesha_subnetpool = next(
            iter(ganesha_subnetpool), None
        )
        if not self.ganesha_subnetpool:
            raise manager_ex.SubnetpoolNotFound(
                network=CONF.network_storage.storage_subnetpool
            )
        # get ganesha router by name
        ganesha_router = self.neutron_client.list_routers(
            name=CONF.network_storage.storage_router
        ).get("routers")
        self.ganesha_router = next(
            iter(ganesha_router), None
        )
        if not self.ganesha_router:
            raise manager_ex.RouterNotFound(
                network=CONF.network_storage.storage_router
            )
        # collect periodic tasks
        self.periodic_tasks = [self._set_manila_share_access_rules]

    def perform_extra_on_start_steps(self, network_segment, neutron_network):
        neutron_network = neutron_network["network"]
        try:
            # create a subnet with the reserved network and subnetpool
            subnet_body = {
                    "subnet": {
                        "name": f"{neutron_network['name']}-subnet",
                        "subnetpool_id": self.ganesha_subnetpool["id"],
                        "network_id": neutron_network["id"],
                        "ip_version": 4,
                        "project_id": neutron_network["project_id"],
                    }
            }
            subet = self.neutron_client.create_subnet(body=subnet_body)
            # share the network with service project
            rbac_policy_body = {
                "rbac_policy": {
                    "object_type": "network",
                    "action": "access_as_shared",
                    "target_tenant": CONF.os_admin_project_name,
                    "object_id": neutron_network["id"],
                }
            }
            self.neutron_client.create_rbac_policy(
                rbac_policy_body
            )
            # add the subnet to ganesha router
            interface_body = {
                'subnet_id': subet["subnet"]["id"],
            }
            self.neutron_client.add_interface_router(
                router=self.ganesha_router["id"], body=interface_body
            )
        except Exception as e:
            self.neutron_client.delete_network(neutron_network["id"])
            raise e

    def _get_ganesha_router_interfaces(self):
        ports = self.neutron_client.list_ports(
            device_id=self.ganesha_router["id"]
        )["ports"]
        result = collections.defaultdict(list)
        for p in ports:
            for fixed_ip in p["fixed_ips"]:
                subnet = self.neutron_client.show_subnet(
                    fixed_ip["subnet_id"]
                )["subnet"]
                result[subnet["tenant_id"]].append(subnet["cidr"])

        return result

    @periodic_task.periodic_task(
        spacing=CONF.network_storage.set_manila_share_access_rules_interval,
        run_immediately=True
    )
    def _set_manila_share_access_rules(self, manager_obj, context):
        # get all available shares
        shares = self.manila_client.shares.list(
            search_opts={
                "all_tenants": 1,
                "share_type": CONF.network_storage.ceph_nfs_share_type,
                "status": "available",
            }
        )

        ganesha_router_project_cidrs = self._get_ganesha_router_interfaces()

        for share in shares:
            try:
                proj = share.project_id
                access_rules = self.manila_client.shares.access_list(share.id)
                existing_ip_to_rule_id = {
                    rule.access_to: rule.id for rule in access_rules
                    if rule.access_level == "rw"
                }
                existing_ips = list(existing_ip_to_rule_id.keys())
                new_ips = ganesha_router_project_cidrs.get(proj, [])
                ips_to_add = set(new_ips).difference(existing_ips)
                ips_to_delete = set(existing_ips).difference(new_ips)
                for ip in ips_to_add:
                    self.manila_client.shares.allow(
                        share.id, "ip", ip, "rw"
                    )
                for ip in ips_to_delete:
                    self.manila_client.shares.deny(
                        share.id, existing_ip_to_rule_id[ip]
                    )
                # all users should have ro access to a public share
                existing_ro_rule_ids = [
                    rule.id for rule in access_rules
                    if rule.access_level == "ro"
                ]
                if share.is_public and not existing_ro_rule_ids:
                    for prefix in self.ganesha_subnetpool["prefixes"]:
                        self.manila_client.shares.allow(
                            share.id, "ip", prefix, "ro"
                        )
                if not share.is_public and existing_ro_rule_ids:
                    for rule_id in existing_ro_rule_ids:
                        self.manila_client.shares.deny(
                            share.id, rule_id
                        )
            except Exception as e:
                LOG.exception(
                    f"Failed to manage access rules for share {share.id}"
                )
