# Copyright (c) 2020 University of Chicago.
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

from blazar.db import api as db_api
from blazar.manager import exceptions as manager_ex
from blazar.plugins.third_party_plugins import base
from blazar.plugins.third_party_plugins import exceptions as plugin_ex
from blazar.utils.openstack import neutron

from neutronclient.common import exceptions as neutron_ex

from oslo_config import cfg
from oslo_log import log as logging


plugin_opts = [
    cfg.StrOpt('network_id', default='',
               help='Network ID to create shadow ports under'),
]

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class StitchportPlugin(base.BasePlugin):

    def __init__(self):
        super(StitchportPlugin, self).__init__()
        CONF.register_opts(plugin_opts, group=self.resource_type())
        self.neutron_client = neutron.BlazarNeutronClient()

    def resource_type(self):
        return "stitchport"

    def validate_create_params(self, data):
        self.validate_data(data, ["provider", "name"], [], "create")
        existing = db_api.resource_get_all_by_queries(
            self.resource_type(), [f"name == {data['name']}"])
        if existing:
            msg = "A stitchport with that name already exists"
            raise plugin_ex.InvalidCreateResourceData(msg)
        port = self.neutron_client.create_port({
            "port": {
                "network_id": CONF[self.resource_type()].network_id,
                "admin_state_up": True,
                "name": data["name"],
            }
        })["port"]
        self.neutron_client.add_tag(
            "ports", port["id"], f"shadow")
        data["port_id"] = port["id"]
        return data

    def validate_update_params(self, resource_id, data):
        self.validate_data(data, [], ["name", "provider"], "update")
        stitchport = db_api.resource_get(self.resource_type(), resource_id)
        data_copy = stitchport["data"].copy()
        data_copy.update(data)
        if "name" in data:
            self.neutron_client.update_port(
                stitchport["data"]["port_id"],
                {"port": {"name": data["name"]}}
            )
        return data_copy

    def allocation_candidates(self, values):
        if not values.get("resource_properties", None):
            raise manager_ex.MissingParameter(param='resource_properties')
        return super(StitchportPlugin, self).allocation_candidates(values)

    def reservation_values(self, reservation_id, values):
        return {
            "resource_properties": values["resource_properties"],
        }

    def allocate(self, resource_reservation, resources):
        res_id = resource_reservation["reservation_id"]
        reservation = db_api.reservation_get(res_id)
        lease = db_api.lease_get(reservation["lease_id"])
        project_id = lease["project_id"]
        for stitchport in resources:
            try:
                self.neutron_client.add_tag(
                    "ports",
                    stitchport["data"]["port_id"],
                    f"reservation_id={res_id}"
                )
                self.neutron_client.add_tag(
                    "ports",
                    stitchport["data"]["port_id"],
                    f"project_id={project_id}"
                )
            except neutron_ex.NotFound:
                LOG.info("Could not find resource to deallocate")

    def deallocate(self, resource_reservation, resources):
        res_id = resource_reservation["reservation_id"]
        reservation = db_api.reservation_get(res_id)
        lease = db_api.lease_get(reservation["lease_id"])
        project_id = lease["project_id"]
        for stitchport in resources:
            try:
                LOG.info(
                    "Removing reservation %s from port %s",
                    res_id,
                    stitchport["data"]["port_id"]
                )
                self.neutron_client.remove_tag(
                    "ports",
                    stitchport["data"]["port_id"],
                    f"reservation_id={res_id}"
                )
                self.neutron_client.remove_tag(
                    "ports",
                    stitchport["data"]["port_id"],
                    f"project_id={project_id}"
                )
            except neutron_ex.NotFound:
                LOG.info("Could not find resource to deallocate")
        tags = [f"reservation_id={res_id}", "stitchport"]
        ports_to_del = self.neutron_client.list_ports(
            retrieve_all=True, tags=tags)
        if not ports_to_del:
            LOG.info("No ports to clean up")
        for port in ports_to_del["ports"]:
            try:
                LOG.info("Cleaning up port %s", port["id"])
                self.neutron_client.delete_port(port["id"])
            except neutron_ex.NotFound:
                LOG.info("Could not delete port %s", port["id"])

    def poll_resource_failures(self):
        stitchports = db_api.resource_get_all_by_filters(
            self.resource_type(), {})
        reservable_stitchports = [
            s for s in stitchports if s['reservable'] is True]
        unreservable_stitchports = [
            s for s in stitchports if s['reservable'] is False]

        try:
            neutron_ports = self.neutron_client.list_ports(
                retrieve_all=True, tags=["shadow"])
            failed_port_ids = [
                str(p.id) for p in neutron_ports if p.admin_state_up == "DOWN"]
            active_port_ids = [
                str(p.id) for p in neutron_ports if p.admin_state_up == "UP"]

            failed_ports = [
                port for port in reservable_stitchports
                if port["data"]["port_id"] in failed_port_ids
            ]
            recovered_ports = [
                port for port in unreservable_stitchports
                if port["data"]["port_id"] in active_port_ids
            ]
        except Exception as e:
            LOG.exception('Skipping health check. %s', str(e))

        return failed_ports, recovered_ports
