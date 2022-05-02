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
from blazar.db import api as db_api
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_ex
from blazar.utils.openstack import manila
from blazar.utils.openstack import neutron
import datetime
from oslo_config import cfg
from oslo_context import context
from oslo_log import log as logging
from oslo_service import periodic_task

opts = [
    cfg.StrOpt('external_ganesha_network',
               default='filesystem-ganesha',
               help='External network name for NFS-Ganesha'),
    cfg.IntOpt('set_manila_share_access_rules_interval',
               default=5*60,
               help='Set access rules for all manila shares every N seconds.'
                    'If this number is negative the periodic task will be '
                    'disabled.'),
    cfg.StrOpt('ceph_nfs_share_type',
               default='default_share_type',
               help='The Ceph NFS share type'),
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
        external_ganesha_network = self.neutron_client.list_networks(
            name=CONF.network_storage.external_ganesha_network
        ).get("networks")
        self.external_ganesha_network = next(
            iter(external_ganesha_network), None
        )
        if not self.external_ganesha_network:
            raise manager_ex.NetworkNotFound(
                network=CONF.network_storage.external_ganesha_network
            )
        self.periodic_tasks = [self._set_manila_share_access_rules]

    def perform_extra_on_start_steps(self, network_segment, neutron_network):
        neutron_network = neutron_network["network"]
        router = None
        try:
            # create a router with the owner of service project
            router_body = {
                    "router": {
                        "name": STORAGE_ROUTER_NAME.format(
                            network_segment_id=network_segment["id"]
                        ),
                        "admin_state_up": True,
                        "external_gateway_info": {
                            "network_id": self.external_ganesha_network["id"],
                            "enable_snat": False,
                        },
                    }
            }
            router = self.neutron_client.create_router(body=router_body)
            # create a subnet (predefined CIDR) with the reserved network
            subnet_body = {
                    "subnet": {
                        "name": f"{neutron_network['name']}-subnet",
                        "cidr": network_segment["subnet_cidr"],
                        "network_id": neutron_network["id"],
                        "ip_version": 4,
                        "project_id": neutron_network["project_id"],
                    }
            }
            subet = self.neutron_client.create_subnet(body=subnet_body)
            # share the network with serivce project
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
            # add the subnet to the router
            interface_body = {
                'subnet_id': subet["subnet"]["id"],
            }
            self.neutron_client.add_interface_router(
                router=router["router"]["id"], body=interface_body
            )
        except Exception as e:
            self.neutron_client.delete_network(neutron_network["id"])
            if router:
                self.neutron_client.delete_router(router["router"]["id"])
            raise e

    def _get_storage_networks(self):
        networks = db_api.network_list()
        storage_networks = []
        for network_segment in networks:
            network_segment_id = network_segment["id"]
            network = db_api.network_get(network_segment_id)
            raw_extra_capabilities = (
                db_api.network_extra_capability_get_all_per_network(
                    network_segment_id
                ))
            extra_capabilities = {}
            for capability, capability_name in raw_extra_capabilities:
                key = capability_name
                extra_capabilities[key] = capability.capability_value
            if ("usage_type" in extra_capabilities and
                    extra_capabilities["usage_type"] == self.usage_type):
                network.update(extra_capabilities)
                storage_networks.append(network)
        return storage_networks

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
                # "status": "available",
            }
        )
        # get all storage network reservations
        storage_networks = self._get_storage_networks()
        storage_vlan_cidr = {n["id"]: n["subnet_cidr"]
                             for n in storage_networks}
        start = datetime.datetime.utcnow()
        end = start
        reservations = db_utils.get_reservation_allocations_by_network_ids(
            list(storage_vlan_cidr.keys()), start, end
        )
        project_reservations = {}
        for res in reservations:
            pid = res["project_id"]
            if pid not in project_reservations:
                project_reservations[pid] = []
            project_reservations[pid].extend(res["network_ids"])

        for share in shares:
            try:
                proj = share.project_id
                access_rules = self.manila_client.shares.access_list(share.id)
                existing_cidr_to_id = {
                    rule.access_to: rule.id for rule in access_rules
                    if rule.access_level == "rw"
                }
                existing_cidrs = list(existing_cidr_to_id.keys())
                new_cidrs = []
                if proj in project_reservations:
                    for network_id in project_reservations[proj]:
                        new_cidrs.append(storage_vlan_cidr[network_id])
                cidrs_to_add = set(new_cidrs).difference(existing_cidrs)
                cidrs_to_delete = set(existing_cidrs).difference(new_cidrs)
                for cidr in cidrs_to_add:
                    self.manila_client.shares.allow(
                        share.id, "ip", cidr, "rw"
                    )
                for cidr in cidrs_to_delete:
                    self.manila_client.share_access_rules.delete(
                        existing_cidr_to_id[cidr]
                    )
                # all users should have ro access to a public share
                existing_ro_cidrs = {
                    rule.access_to: rule.id for rule in access_rules
                    if rule.access_level == "ro"
                }
                if share.is_public and not existing_ro_cidrs:
                    self.manila_client.shares.allow(
                        share.id, "ip", "0.0.0.0/0", "ro"
                    )
                if not share.is_public and existing_ro_cidrs:
                    for cidr, rule_id in existing_ro_cidrs:
                        self.manila_client.share_access_rules.delete(
                            rule_id
                        )
            except Exception as e:
                LOG.exception(
                    f"Failed to manage access rules for share {share.id}"
                )
