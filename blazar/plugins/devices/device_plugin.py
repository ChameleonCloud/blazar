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

import datetime

from oslo_config import cfg
from oslo_utils import strutils
from stevedore import named

from blazar import status
from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_ex
from blazar.plugins import base
from blazar.plugins import devices as plugin
from blazar.plugins import monitor
from blazar.utils import plugins as plugins_utils
from blazar.utils.openstack import placement
from oslo_log import log as logging
from random import shuffle


plugin_opts = [
    cfg.StrOpt('before_end',
               default='',
               help='Actions which we will be taken before the end of '
                    'the lease'),
    cfg.ListOpt('plugins',
                default=['zun.plugin'],
                help='All plugins to use (one for every device driver to '
                     'support.)'),
    cfg.IntOpt('cleaning_time',
               default=0,
               min=0,
               help='The minimum interval [minutes] between the end of a '
               'lease and the start of the next lease for the same '
               'device. This interval is used for cleanup.'),
]

plugin_opts.extend(monitor.monitor_opts)

CONF = cfg.CONF
CONF.register_opts(plugin_opts, group=plugin.RESOURCE_TYPE)
LOG = logging.getLogger(__name__)

before_end_options = ['', 'default', 'email']

QUERY_TYPE_ALLOCATION = 'allocation'

MONITOR_ARGS = {"resource_type": plugin.RESOURCE_TYPE}


def _get_plugins():
    """Return dict of resource-plugin class pairs."""
    plugins = {}

    extension_manager = named.NamedExtensionManager(
        namespace='blazar.device.driver.plugins',
        names=CONF.device.plugins,
        invoke_on_load=False
    )

    for ext in extension_manager.extensions:
        try:
            plugin_obj = ext.plugin()
        except Exception as e:
            LOG.warning("Could not load {0} plugin "
                        "for resource type {1} '{2}'".format(
                            ext.name, ext.plugin.resource_type, e))
        else:
            if plugin_obj.device_driver in plugins:
                msg = ("You have provided several plugins for "
                       "one device driver in configuration file. "
                       "Please set one plugin per device driver.")
                raise manager_ex.PluginConfigurationError(error=msg)

        plugins[plugin_obj.device_driver] = plugin_obj
    return plugins


class DevicePlugin(base.BasePlugin):
    """Plugin for device resource."""
    resource_type = plugin.RESOURCE_TYPE
    title = 'Device Plugin'
    description = 'This plugin creates and deletes devices.'
    query_options = {
        QUERY_TYPE_ALLOCATION: ['lease_id', 'reservation_id']
    }

    def __init__(self):
        super(DevicePlugin, self).__init__()
        self.plugins = _get_plugins()
        self.monitor = DeviceMonitorPlugin(**MONITOR_ARGS)
        self.monitor.register_reallocater(self._reallocate)
        self.placement_client = placement.BlazarPlacementClient()
        self._check_resource_providers()

    def _check_resource_providers(self):
        """
        Check if there is a reservation provider for
        all enrolled devices. Lazy create if not.
        """
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

    def reserve_resource(self, reservation_id, values):
        """Create reservation."""
        device_ids = self.allocation_candidates(values)

        if not device_ids:
            raise manager_ex.NotEnoughDevicesAvailable()

        device_rsrv_values = {
            'reservation_id': reservation_id,
            'resource_properties': values['resource_properties'],
            'count_range': values['count_range'],
            'status': 'pending',
            'before_end': values['before_end'],
        }
        device_reservation = db_api.device_reservation_create(
            device_rsrv_values)
        for device_id in device_ids:
            db_api.device_allocation_create({'device_id': device_id,
                                             'reservation_id': reservation_id})
        return device_reservation['id']

    def update_reservation(self, reservation_id, values):
        """Update reservation."""
        reservation = db_api.reservation_get(reservation_id)
        lease = db_api.lease_get(reservation['lease_id'])

        if (not [x for x in values.keys() if x in ['min', 'max',
                                                   'resource_properties']]
                and values['start_date'] >= lease['start_date']
                and values['end_date'] <= lease['end_date']):
            # Nothing to update
            return

        dates_before = {'start_date': lease['start_date'],
                        'end_date': lease['end_date']}
        dates_after = {'start_date': values['start_date'],
                       'end_date': values['end_date']}
        device_reservation = db_api.device_reservation_get(
            reservation['resource_id'])
        self._update_allocations(dates_before, dates_after, reservation_id,
                                 reservation['status'], device_reservation,
                                 values, lease)

        updates = {}
        if 'min' in values or 'max' in values:
            count_range = str(values.get(
                'min', device_reservation['count_range'].split('-')[0])
            ) + '-' + str(values.get(
                'max', device_reservation['count_range'].split('-')[1])
            )
            updates['count_range'] = count_range
        if 'resource_properties' in values:
            updates['resource_properties'] = values.get(
                'resource_properties')
        if updates:
            db_api.device_reservation_update(device_reservation['id'], updates)

    def on_start(self, resource_id, lease=None):
        """Add the devices in the custom Placement trait."""
        device_reservation = db_api.device_reservation_get(resource_id)
        self.placement_client.create_reservation_trait(
            device_reservation['reservation_id'], lease['project_id'])

        for allocation in db_api.device_allocation_get_all_by_values(
                reservation_id=device_reservation['reservation_id']):
            device = db_api.device_get(allocation['device_id'])
            rp = self.placement_client.get_reservation_provider(device['name'])
            self.placement_client. \
                associate_reservation_trait_with_resource_provider(
                    rp['uuid'],
                    device_reservation['reservation_id'],
                    lease['project_id'])

    def before_end(self, resource_id, lease=None):
        """Take an action before the end of a lease."""
        device_reservation = db_api.device_reservation_get(resource_id)

        action = device_reservation['before_end']
        if action == 'default':
            action = CONF[plugin.RESOURCE_TYPE].before_end

        if action == 'email':
            plugins_utils.send_lease_extension_reminder(
                lease, CONF.os_region_name)

    def on_end(self, resource_id, lease=None):
        """Remove the devices from the custom Placement trait."""
        device_reservation = db_api.device_reservation_get(resource_id)
        db_api.device_reservation_update(device_reservation['id'],
                                         {'status': 'completed'})
        allocations = db_api.device_allocation_get_all_by_values(
            reservation_id=device_reservation['reservation_id'])
        for allocation in allocations:
            db_api.device_allocation_destroy(allocation['id'])

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
            device = self.get_device(rp['parent_provider_uuid'])
            if device:
                self.plugins[device['device_driver']].cleanup_device(device)
            else:
                LOG.warning(
                    'Failed to retrieve device from resource provider %s',
                    rp['parent_provider_uuid']
                )
        self.placement_client.delete_reservation_trait(
            reservation_id, project_id)

    def _get_extra_capabilities(self, device_id):
        extra_capabilities = {}
        raw_extra_capabilities = (
            db_api.device_extra_capability_get_all_per_device(device_id))
        for capability, capability_name in raw_extra_capabilities:
            key = capability_name
            extra_capabilities[key] = capability.capability_value
        return extra_capabilities

    def get(self, device_id):
        return self.get_device(device_id)

    def get_device(self, device_id):
        device = db_api.device_get(device_id)
        if device is None:
            return device
        return self.get_device_with_extra_capabilities(device)

    def get_device_with_extra_capabilities(self, device):
        extra_capabilities = self._get_extra_capabilities(device["id"])
        if extra_capabilities:
            res = device.copy()
            res.update(extra_capabilities)
            return res
        else:
            return device

    def list_devices(self):
        raw_device_list = db_api.device_list()
        device_list = []
        for device in raw_device_list:
            device_list.append(self.get_device(device['id']))
        return device_list

    def create_device(self, values):
        if 'trust_id' in values:
            del values['trust_id']
        device_id = self.plugins[values.get(
            'device_driver')].create_device(values)
        return self.get_device(device_id)

    def is_updatable_extra_capability(self, capability, capability_name):
        reservations = db_utils.get_reservations_by_device_id(
            capability['device_id'], datetime.datetime.utcnow(),
            datetime.date.max)

        for r in reservations:
            plugin_reservation = db_utils.get_plugin_reservation(
                r['resource_type'], r['resource_id'])

            requirements_queries = plugins_utils.convert_requirements(
                plugin_reservation['resource_properties'])

            for requirement in requirements_queries:
                if requirement.split(" ")[0] == capability_name:
                    return False
        return True

    def update_device(self, device_id, values):
        # nothing to update
        if not values:
            return self.get_device(device_id)

        device_property_names = ['device_type', 'device_driver']
        device_properties = {}
        for prop_key in list(values.keys()):
            if prop_key in device_property_names:
                device_properties[prop_key] = values.pop(prop_key)
        if device_properties:
            db_api.device_update(device_id, device_properties)

        cant_update_extra_capability = []
        previous_capabilities = self._get_extra_capabilities(device_id)
        updated_keys = set(values.keys()) & set(previous_capabilities.keys())
        new_keys = set(values.keys()) - set(previous_capabilities.keys())

        for key in updated_keys:
            raw_capability, cap_name = next(iter(
                db_api.device_extra_capability_get_all_per_name(
                    device_id, key)))
            capability = {'capability_value': values[key]}

            if self.is_updatable_extra_capability(raw_capability, cap_name):
                try:
                    if values[key] is not None:
                        capability = {'capability_value': values[key]}
                        db_api.device_extra_capability_update(
                            raw_capability['id'], capability)
                    else:
                        db_api.device_extra_capability_destroy(
                            raw_capability['id'])
                except (db_ex.BlazarDBException, RuntimeError):
                    cant_update_extra_capability.append(cap_name)
            else:
                LOG.info("Capability %s can't be updated because "
                         "existing reservations require it.",
                         cap_name)
                cant_update_extra_capability.append(cap_name)

        for key in new_keys:
            new_capability = {
                'device_id': device_id,
                'capability_name': key,
                'capability_value': values[key],
            }
            try:
                db_api.device_extra_capability_create(new_capability)
            except (db_ex.BlazarDBException, RuntimeError):
                cant_update_extra_capability.append(key)

        if cant_update_extra_capability:
            raise manager_ex.CantAddExtraCapability(
                host=device_id, keys=cant_update_extra_capability)

        LOG.info('Extra capabilities on device %s updated with %s',
                 device_id, values)
        return self.get_device(device_id)

    def delete_device(self, device_id):
        device = db_api.device_get(device_id)
        if not device:
            raise manager_ex.DeviceNotFound(device=device_id)

        if db_api.device_allocation_get_all_by_values(
                device_id=device_id):
            raise manager_ex.CantDeleteDevice(
                device=device_id,
                msg='The device is reserved.'
            )

        try:
            db_api.device_destroy(device_id)
            self.placement_client.delete_reservation_provider(device['name'])
        except db_ex.BlazarDBException as e:
            raise manager_ex.CantDeleteDevice(device=device_id, msg=str(e))

    def reallocate_device(self, device_id, data):
        allocations = self.get_allocations(device_id, data, detail=True)

        for alloc in allocations['reservations']:
            reservation_flags = {}
            device_allocation = db_api.device_allocation_get_all_by_values(
                device_id=device_id,
                reservation_id=alloc['id'])[0]

            if self._reallocate(device_allocation):
                if alloc['status'] == status.reservation.ACTIVE:
                    reservation_flags.update(dict(resources_changed=True))
                    db_api.lease_update(alloc['lease_id'], dict(degraded=True))
            else:
                reservation_flags.update(dict(missing_resources=True))
                db_api.lease_update(alloc['lease_id'], dict(degraded=True))

            db_api.reservation_update(alloc['id'], reservation_flags)

        return self.get_allocations(device_id, data)

    def _reallocate(self, allocation):
        """Allocate an alternative device.

        :param allocation: allocation to change.
        :return: True if an alternative device was successfully allocated.
        """
        reservation = db_api.reservation_get(allocation['reservation_id'])
        device_reservation = db_api.device_reservation_get(
            reservation['resource_id'])
        lease = db_api.lease_get(reservation['lease_id'])

        # Remove the old device from the trait.
        if reservation['status'] == status.reservation.ACTIVE:
            device = db_api.device_get(allocation['device_id'])
            rp = self.placement_client.get_reservation_provider(device['name'])
            self.placement_client. \
                dissociate_reservation_trait_with_resource_provider(
                    rp['uuid'],
                    device_reservation['reservation_id'],
                    lease['project_id'])

        # Allocate an alternative device.
        start_date = max(datetime.datetime.utcnow(), lease['start_date'])
        new_deviceids = self._matching_devices(
            device_reservation['resource_properties'],
            '1-1', start_date, lease['end_date'], lease['project_id']
        )
        if not new_deviceids:
            db_api.device_allocation_destroy(allocation['id'])
            LOG.warn('Could not find alternative device for reservation %s '
                     '(lease: %s).', reservation['id'], lease['name'])
            return False
        else:
            new_deviceid = new_deviceids.pop()
            db_api.device_allocation_update(allocation['id'],
                                            {'device_id': new_deviceid})
            LOG.warn('Resource changed for reservation %s (lease: %s).',
                     reservation['id'], lease['name'])
            if reservation['status'] == status.reservation.ACTIVE:
                # Add the alternative device into the trait.
                new_device = db_api.device_get(new_deviceid)
                rp = self.placement_client.get_reservation_provider(
                    new_device['name'])
                self.placement_client. \
                    associate_reservation_trait_with_resource_provider(
                        rp['uuid'],
                        device_reservation['reservation_id'],
                        lease['project_id'])

            return True

    def list_allocations(self, query, detail=False):
        devices_id_list = [d['id'] for d in db_api.device_list()]
        options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
        options['detail'] = detail
        devices_allocations = self.query_device_allocations(devices_id_list,
                                                            **options)
        self.add_extra_allocation_info(devices_allocations)
        return [{"resource_id": device, "reservations": allocs}
                for device, allocs in devices_allocations.items()]

    def get_allocations(self, device_id, query, detail=False):
        options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
        options['detail'] = detail
        device_allocations = self.query_device_allocations(
            [device_id], **options)
        allocs = device_allocations.get(device_id, [])
        return {"resource_id": device_id, "reservations": allocs}

    def query_allocations(self, devices, lease_id=None, reservation_id=None):
        return self.query_device_allocations(devices, lease_id=lease_id,
                                             reservation_id=reservation_id)

    def query_device_allocations(self, devices, lease_id=None,
                                 reservation_id=None, detail=False):
        """Return dict of device and its allocations.

        The list element forms
        {
          'device-id': [
                       {
                         'lease_id': lease_id,
                         'id': reservation_id,
                         'start_date': lease_start_date,
                         'end_date': lease_end_date
                       },
                     ]
        }.
        """
        start = datetime.datetime.utcnow()
        end = datetime.date.max

        reservations = db_utils.get_reservation_allocations_by_device_ids(
            devices, start, end, lease_id, reservation_id)
        device_allocations = {d: [] for d in devices}

        for reservation in reservations:
            if not detail:
                del reservation['project_id']
                del reservation['lease_name']
                del reservation['status']

            for device_id in reservation['device_ids']:
                if device_id in device_allocations.keys():
                    device_allocations[device_id].append({
                        k: v for k, v in reservation.items()
                        if k != 'device_ids'})

        return device_allocations

    def allocation_candidates(self, values):
        self._check_params(values)

        device_ids = self._matching_devices(
            values['resource_properties'],
            values['count_range'],
            values['start_date'],
            values['end_date'],
            values['project_id']
        )

        min_devices, _ = [int(n) for n in values['count_range'].split('-')]

        if len(device_ids) < min_devices:
            raise manager_ex.NotEnoughHostsAvailable()

        return device_ids

    def _convert_int_param(self, param, name):
        """Checks that the parameter is present and can be converted to int."""
        if param is None:
            raise manager_ex.MissingParameter(param=name)
        if strutils.is_int_like(param):
            param = int(param)
        else:
            raise manager_ex.MalformedParameter(param=name)
        return param

    def _validate_min_max_range(self, values, min_devices, max_devices):
        min_devices = self._convert_int_param(min_devices, 'min')
        max_devices = self._convert_int_param(max_devices, 'max')
        if min_devices <= 0 or max_devices <= 0:
            raise manager_ex.MalformedParameter(
                param='min and max (must be greater than or equal to 1)')
        if max_devices < min_devices:
            raise manager_ex.InvalidRange()
        values['count_range'] = str(min_devices) + '-' + str(max_devices)

    def _check_params(self, values):
        self._validate_min_max_range(values, values.get('min'),
                                     values.get('max'))

        if 'resource_properties' not in values:
            raise manager_ex.MissingParameter(param='resource_properties')

        if 'before_end' not in values:
            values['before_end'] = 'default'
        if values['before_end'] not in before_end_options:
            raise manager_ex.MalformedParameter(param='before_end')

        if 'on_start' not in values:
            values['on_start'] = 'default'

    def _matching_devices(self, resource_properties, count_range,
                          start_date, end_date, project_id):
        """Return the matching devices (preferably not allocated)"""
        count_range = count_range.split('-')
        min_device = count_range[0]
        max_device = count_range[1]
        allocated_device_ids = []
        not_allocated_device_ids = []
        filter_array = []
        start_date_with_margin = start_date - datetime.timedelta(
            minutes=CONF.device.cleaning_time)
        end_date_with_margin = end_date + datetime.timedelta(
            minutes=CONF.device.cleaning_time)

        if resource_properties:
            filter_array += plugins_utils.convert_requirements(
                resource_properties)
        for device in db_api.device_get_all_by_queries(
                filter_array):
            device = self.get_device_with_extra_capabilities(device)
            if not self.is_project_allowed(project_id, device):
                continue
            if not db_api.device_allocation_get_all_by_values(
                    device_id=device['id']):
                not_allocated_device_ids.append(device['id'])
            elif db_utils.get_free_periods(
                device['id'],
                start_date_with_margin,
                end_date_with_margin,
                end_date_with_margin - start_date_with_margin,
                resource_type='device'
            ) == [
                (start_date_with_margin, end_date_with_margin),
            ]:
                allocated_device_ids.append(device['id'])
        if len(not_allocated_device_ids) >= int(min_device):
            shuffle(not_allocated_device_ids)
            return not_allocated_device_ids[:int(max_device)]
        all_device_ids = allocated_device_ids + not_allocated_device_ids
        if len(all_device_ids) >= int(min_device):
            shuffle(all_device_ids)
            return all_device_ids[:int(max_device)]
        else:
            return []

    def _update_allocations(self, dates_before, dates_after, reservation_id,
                            reservation_status, device_reservation, values,
                            lease):
        min_devices = values.get('min', int(
            device_reservation['count_range'].split('-')[0]))
        max_devices = values.get(
            'max', int(device_reservation['count_range'].split('-')[1]))
        self._validate_min_max_range(values, min_devices, max_devices)
        resource_properties = values.get(
            'resource_properties',
            device_reservation['resource_properties'])
        allocs = db_api.device_allocation_get_all_by_values(
            reservation_id=reservation_id)

        allocs_to_remove = self._allocations_to_remove(
            dates_before, dates_after, max_devices,
            resource_properties, allocs)

        if (allocs_to_remove and
                reservation_status == status.reservation.ACTIVE):
            raise manager_ex.NotEnoughHostsAvailable()

        kept_devices = len(allocs) - len(allocs_to_remove)
        if kept_devices < max_devices:
            min_devices = min_devices - kept_devices \
                if (min_devices - kept_devices) > 0 else 0
            max_devices = max_devices - kept_devices
            device_ids = self._matching_devices(
                resource_properties,
                str(min_devices) + '-' + str(max_devices),
                dates_after['start_date'], dates_after['end_date'],
                lease['project_id'])
            if len(device_ids) >= min_devices:
                for device_id in device_ids:
                    db_api.device_allocation_create(
                        {'device_id': device_id,
                         'reservation_id': reservation_id})
                    new_device = db_api.device_get(device_id)
                    if reservation_status == status.reservation.ACTIVE:
                        # Add new device into the trait.
                        rp = self.placement_client.get_reservation_provider(
                            new_device['name'])
                        self.placement_client. \
                            associate_reservation_trait_with_resource_provider(
                                rp['uuid'],
                                device_reservation['reservation_id'],
                                lease['project_id'])
            else:
                raise manager_ex.NotEnoughHostsAvailable()

        for allocation in allocs_to_remove:
            db_api.device_allocation_destroy(allocation['id'])

    def _allocations_to_remove(self, dates_before, dates_after, max_devices,
                               resource_properties, allocs):
        allocs_to_remove = []
        requested_device_ids = [device['id'] for device in
                                self._filter_devices_by_properties(
                                    resource_properties
        )]

        for alloc in allocs:
            if alloc['device_id'] not in requested_device_ids:
                allocs_to_remove.append(alloc)
                continue
            if (dates_before['start_date'] > dates_after['start_date'] or
                    dates_before['end_date'] < dates_after['end_date']):
                reserved_periods = db_utils.get_reserved_periods(
                    alloc['device_id'],
                    dates_after['start_date'],
                    dates_after['end_date'],
                    datetime.timedelta(seconds=1))

                max_start = max(dates_before['start_date'],
                                dates_after['start_date'])
                min_end = min(dates_before['end_date'],
                              dates_after['end_date'])

                if not (len(reserved_periods) == 0 or
                        (len(reserved_periods) == 1 and
                         reserved_periods[0][0] == max_start and
                         reserved_periods[0][1] == min_end)):
                    allocs_to_remove.append(alloc)

        kept_devices = len(allocs) - len(allocs_to_remove)
        if kept_devices > max_devices:
            allocs_to_remove.extend(
                [allocation for allocation in allocs
                 if allocation not in allocs_to_remove
                 ][:(kept_devices - max_devices)]
            )

        return allocs_to_remove

    def _filter_devices_by_properties(self, resource_properties):
        filter = []
        if resource_properties:
            filter += plugins_utils.convert_requirements(resource_properties)
        if filter:
            return db_api.device_get_all_by_queries(filter)
        else:
            return db_api.device_list()


class DeviceMonitorPlugin(monitor.GeneralMonitorPlugin):
    """Monitor plugin for device resource."""

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = \
                super(DeviceMonitorPlugin, cls).__new__(cls, *args, **kwargs)
            cls._instance.plugins = _get_plugins()
        return cls._instance

    def filter_allocations(self, reservation, device_ids):
        return [alloc for alloc
                in reservation['device_allocations']
                if alloc['device_id'] in device_ids]

    def get_reservations_by_resource_ids(self, device_ids,
                                         interval_begin, interval_end):
        return db_utils.get_reservations_by_device_ids(device_ids,
                                                       interval_begin,
                                                       interval_end)

    def get_unreservable_resourses(self):
        return db_api.unreservable_device_get_all_by_queries([])

    def get_notification_event_types(self):
        """Get event types of notification messages to handle."""
        return []

    def notification_callback(self, event_type, payload):
        """Handle a notification message.
        It is used as a callback of a notification-based resource monitor.
        :param event_type: an event type of a notification.
        :param payload: a payload of a notification.
        :return: a dictionary of {reservation id: flags to update}
                 e.g. {'de27786d-bd96-46bb-8363-19c13b2c6657':
                       {'missing_resources': True}}
        """
        return {}

    def set_reservable(self, resource, is_reservable):
        db_api.device_update(resource["id"], {"reservable": is_reservable})
        LOG.warn('%s %s.', resource["name"],
                 "recovered" if is_reservable else "failed")

    def poll_resource_failures(self):
        """Check health of devices by calling driver service API.

        :return: a list of failed devices, a list of recovered devices.
        """
        devices = db_api.device_get_all_by_filters({})

        failed_devices = []
        recovered_devices = []

        for device_driver in self.plugins.keys():
            try:
                driver_failed_devices, driver_recovered_devices = \
                    self.plugins[device_driver].poll_resource_failures(devices)
                failed_devices.extend(driver_failed_devices)
                recovered_devices.extend(driver_recovered_devices)
            except AttributeError:
                LOG.warning('poll_resource_failures is not implemented for {}'
                            .format(device_driver))

        return failed_devices, recovered_devices
