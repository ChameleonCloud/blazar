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
from oslo_config import cfg

from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.manager import exceptions as manager_ex
from kubernetes import client
from kubernetes import config
from oslo_log import log as logging

opts = [
    cfg.StrOpt(
        'kubeconfig_file', help='Kubeconfig file to use for calls to k8s'),
]

CONF = cfg.CONF
CONF.register_opts(opts, group="k8s")
LOG = logging.getLogger(__name__)

LABEL_NAMESPACE = "blazar.openstack.org"
LABELS = {
    "reservation_id": f"{LABEL_NAMESPACE}/reservation_id",
    "project_id": f"{LABEL_NAMESPACE}/project_id",
    "device": f"{LABEL_NAMESPACE}/device",
}


class K8sPlugin():
    device_driver = 'k8s'

    def __init__(self):
        config.load_kube_config(config_file=CONF.k8s.kubeconfig_file)
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    def has_label(self, node, label, value):
        '''Get if the node has label=value, or if value is none, any value'''
        return (label in node.metadata.labels and
                (value is None or node.metadata.labels.get(label) == value))

    def set_label(self, name, label, value):
        body = {
            "metadata": {
                "labels": {
                    label: value,
                },
            },
        }
        return self.core_v1.patch_node(name, body)

    def set_res_id_label(self, name, reservation_id):
        return self.set_label(
            name, LABELS["reservation_id"], reservation_id)

    def set_project_id_label(self, name, project_id):
        return self.set_label(
            name, LABELS["project_id"], project_id)

    def set_device(self, name, value):
        return self.set_label(name, LABELS["device"], value)

    def create_device(self, device_values):
        device_name = device_values.get('name')

        if device_name is None:
            raise manager_ex.InvalidHost(host=device_values)

        try:
            self.core_v1.read_node(device_name)
        # TODO(jason): Future versions of the kubernetes client have this import
        # available just from client.ApiException.
        except client.api_client.ApiException as exc:
            if exc.status != 404:
                LOG.exception("Error fetching node from k8s")
            raise manager_ex.DeviceNotFound(device=device_name)

        device_properties = {
            'name': device_name,
            'device_type': 'container',
            'device_driver': K8sPlugin.device_driver
        }

        to_store = set(device_values.keys()) - set(device_properties.keys())
        extra_capabilities_keys = to_store
        extra_capabilities = dict(
            (key, device_values[key]) for key in extra_capabilities_keys
        )

        if any([len(key) > 64 for key in extra_capabilities_keys]):
            raise manager_ex.ExtraCapabilityTooLong()

        device = None
        cantaddextracapability = []
        try:
            device = db_api.device_create(device_properties)
        except db_ex.BlazarDBException:
            raise
        for key in extra_capabilities:
            values = {'device_id': device['id'],
                      'capability_name': key,
                      'capability_value': extra_capabilities[key],
                      }
            try:
                db_api.device_extra_capability_create(values)
            except db_ex.BlazarDBException:
                cantaddextracapability.append(key)
        if cantaddextracapability:
            raise manager_ex.CantAddExtraCapability(
                keys=cantaddextracapability,
                host=device['id'])
        self.set_device(device_name, device['id'])
        return device['id']

    def is_active(self, node):
        conditions = node.status.conditions
        for i in conditions:
            if i.type == "Ready":
                return i.status == "True"
        return False

    def poll_resource_failures(self, devices):
        failed_devices = []
        recovered_devices = []

        all_nodes = self.core_v1.list_node().items
        for device in devices:
            for node in all_nodes:
                if node.metadata.name == device["name"]:
                    if not self.is_active(node) and device["reservable"]:
                        failed_devices.append(device)
                    elif self.is_active(node) and not device["reservable"]:
                        recovered_devices.append(device)
                        # Handle case when node is rebuilt; it will not have the
                        # "device" label anymore.
                        if node.metadata.labels.get(LABELS["device"]) != device["id"]:
                            self.set_device(device["name"], device["id"])
                    break
            else:
                # Node is completely missing from k8s
                failed_devices.append(device)

        return failed_devices, recovered_devices

    def allocate(self, device_reservation, lease, devices):
        project_id = lease["project_id"]
        for device in devices:
            self.set_res_id_label(
                device["name"], device_reservation["reservation_id"])
            self.set_project_id_label(device["name"], project_id)

    def deallocate(self, device_reservation, lease, devices):
        namespace = lease["project_id"]
        for device in devices:
            self.set_res_id_label(device["name"], None)
            self.set_project_id_label(device["name"], None)

        for deployment in self.apps_v1.list_namespaced_deployment(
                namespace).items:
            if self.has_label(
                    deployment,
                    LABELS["reservation_id"],
                    device_reservation["reservation_id"]):
                self.apps_v1.delete_namespaced_deployment(
                    deployment.metadata.name, namespace)

    def after_destroy(self, device):
        self.set_device(device["name"], None)

    def remove_active_device(self, device, device_reservation, lease):
        self.set_res_id_label(device["name"], None)
        self.set_project_id_label(device["name"], None)

    def add_active_device(self, device, device_reservation, lease):
        project_id = lease["project_id"]
        self.set_res_id_label(
            device["name"], device_reservation["reservation_id"])
        self.set_project_id_label(device["name"], project_id)
