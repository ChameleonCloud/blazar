# -*- coding: utf-8 -*-
#
# Author: Pierre Riteau <pierre@stackhpc.com>
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
from blazar.utils.openstack import placement
from blazar.utils.openstack import zun
from oslo_log import log as logging
from zunclient import exceptions as zun_ex

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class ZunPlugin(zun.ZunClientWrapper):
    """Plugin for zun device driver."""
    device_driver = 'zun'

    def __init__(self):
        self.placement_client = placement.BlazarPlacementClient()
        for blazar_device in db_api.device_list():
            if not blazar_device['reservable']:
                continue
            name = blazar_device['name']
            parent_rp = self.placement_client.get_resource_provider(
                name)
            reservation_rp = self.placement_client.get_reservation_provider(
                name)
            if not parent_rp:
                LOG.warning("No resource provider found "
                            "for blazar device {}".format(name))
            elif not reservation_rp:
                LOG.warning("No reservation provider found for blazar "
                            "device {}. Auto-creating one. ".format(name))
                rrp = self.placement_client.create_reservation_provider(name)
                LOG.info(
                    "Reservation provider {} has created.".format(rrp['name']))

    def create_device(self, device_values):
        device_id = device_values.get('id')
        device_name = device_values.get('name')
        device_ref = device_id or device_name

        if device_ref is None:
            raise manager_ex.InvalidHost(host=device_values)

        inventory = zun.ZunInventory()
        zun_compute_node = inventory.get_host_details(device_ref)
        if len(zun_compute_node['containers']) > 0:
            raise manager_ex.HostHavingContainers(host=device_ref)

        device_properties = {'id': zun_compute_node['id'],
                             'name': zun_compute_node['name'],
                             'device_type': 'container',
                             'device_driver': 'zun'}

        to_store = set(device_values.keys()) - set(device_properties.keys())
        extra_capabilities_keys = to_store
        extra_capabilities = dict(
            (key, device_values[key]) for key in extra_capabilities_keys
        )

        if any([len(key) > 64 for key in extra_capabilities_keys]):
            raise manager_ex.ExtraCapabilityTooLong()

        self.placement_client.create_reservation_provider(
            host_name=zun_compute_node['name'])

        device = None
        cantaddextracapability = []
        try:
            device = db_api.device_create(device_properties)
        except db_ex.BlazarDBException:
            self.placement_client.delete_reservation_provider(
                host_name=zun_compute_node['name'])
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
        return device['id']

    def cleanup_device(self, device):
        try:
            # TODO(jason): zunclient is broken when passing both all_projects
            # and 'host' as a keyword argument; the parameters are encoded
            # like /v1/containers/?all_projects=1?host=..., which is malformed.
            # Passing in 'host' to the list() function would however probably
            # be more efficient.
            host_containers = [
                container for container in
                self.zun.containers.list(all_projects=True)
                if container.host == device['name']
            ]
        except zun_ex.ClientException as exc:
            LOG.error((
                'During lease teardown, failed to enumerate containers. '
                'Containers may need to be manually cleaned up on %s.'
                'Error: %s'
            ), device['name'], exc)
            host_containers = []

        for container in host_containers:
            try:
                self.zun.containers.delete(
                    container.uuid, force=True, stop=True)
            except zun_ex.NotFound:
                LOG.info('Could not find container %s, may have been deleted '
                         'concurrently.', container.name)
            except Exception as e:
                LOG.exception('Failed to delete %s: %s.',
                              container.name, str(e))

    def poll_resource_failures(self, devices):
        failed_devices = []
        recovered_devices = []

        zun_compute_services = {s.host: s for s in self.zun.services.list()}
        zun_devices = {d["name"]: d for d in devices
                       if d.get("device_driver") == self.device_driver}

        for device_name, device in zun_devices.items():
            is_reservable = device.get("reservable")
            cs = zun_compute_services.get(device_name)
            if is_reservable and cs and \
                    cs.state == 'down' or cs.disabled:
                failed_devices.append(device)
            if not is_reservable and cs and \
                    cs.state == 'up' and not cs.disabled:
                recovered_devices.append(device)

        return failed_devices, recovered_devices

    def allocate(self, device_reservation, lease, devices):
        self.placement_client.create_reservation_trait(
            device_reservation['reservation_id'], lease['project_id'])
        for device in devices:
            rp = self.placement_client.get_reservation_provider(device['name'])
            self.placement_client. \
                associate_reservation_trait_with_resource_provider(
                    rp['uuid'],
                    device_reservation['reservation_id'],
                    lease['project_id'])

    def remove_active_device(self, device, device_reservation, lease):
        rp = self.placement_client.get_reservation_provider(device['name'])
        self.placement_client. \
            dissociate_reservation_trait_with_resource_provider(
                rp['uuid'],
                device_reservation['reservation_id'],
                lease['project_id'])

    def add_active_device(self, device, device_reservation, lease):
        rp = self.placement_client.get_reservation_provider(
            device['name'])
        self.placement_client. \
            associate_reservation_trait_with_resource_provider(
                rp['uuid'],
                device_reservation['reservation_id'],
                lease['project_id'])

    def deallocate(self, device_reservation, lease, devices):
        # If a device lease fails to start, the reservation trait is never
        # added to the parent resource provider. If that lease is deleted,
        # deleting the trait fails because it does not exist. This case
        # will be handled by logging a message rather than failing.
        reservation_id = device_reservation['reservation_id']
        project_id = lease['project_id']
        if not self.placement_client.reservation_trait_exists(
                reservation_id, project_id):
            LOG.warning("Reservation trait doesn't exist for reservation {0} "
                        "and project {1}".format(reservation_id, project_id))
            return
        resource_providers = self.placement_client. \
            get_reservation_trait_resource_providers(reservation_id,
                                                     project_id)
        for rp in resource_providers:
            self.placement_client. \
                dissociate_reservation_trait_with_resource_provider(
                    rp['uuid'],
                    reservation_id,
                    project_id)
            device = None
            for d in devices:
                if d["id"] == rp['parent_provider_uuid']:
                    device = d
                    break
            if device:
                self.cleanup_device(device)
            else:
                LOG.warning(
                    'Failed to retrieve device from resource provider %s',
                    rp['parent_provider_uuid']
                )
        self.placement_client.delete_reservation_trait(
            reservation_id, project_id)

    def after_destroy(self, device):
        self.placement_client.delete_reservation_provider(device['name'])
