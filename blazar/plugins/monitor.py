# -*- coding: utf-8 -*-
#
# Author: François Rossigneux <francois.rossigneux@inria.fr>
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

import abc
from blazar.manager import exceptions as manager_ex
from blazar.plugins import base
from blazar import status
from oslo_log import log as logging


monitor_opts = [
    cfg.BoolOpt('enable_notification_monitor',
                default=False,
                help='Enable notification-based resource monitoring. '
                     'If it is enabled, the blazar-manager monitors states of '
                     'resource by subscribing to notifications of '
                     'the corresponding service.'),
    cfg.ListOpt('notification_topics',
                default=['notifications', 'versioned_notifications'],
                help='Notification topics to subscribe to.'),
    cfg.BoolOpt('enable_polling_monitor',
                default=False,
                help='Enable polling-based resource monitoring. '
                     'If it is enabled, the blazar-manager monitors states '
                     'of resource by polling the service API.'),
    cfg.IntOpt('polling_interval',
               default=60,
               min=1,
               help='Interval (seconds) of polling for health checking.'),
    cfg.IntOpt('healing_interval',
               default=60,
               min=0,
               help='Interval (minutes) of reservation healing. '
                    'If 0 is specified, the interval is infinite and all the '
                    'reservations in the future is healed at one time.'),
]

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class GeneralMonitorPlugin(base.BaseMonitorPlugin, metaclass=abc.ABCMeta):
    """Monitor plugin for resource."""

    # Singleton design pattern
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = \
                super(GeneralMonitorPlugin, cls).__new__(cls)
            cls._instance.resource_type = kwargs.get("resource_type")
            super(GeneralMonitorPlugin, cls._instance).__init__()
        return cls._instance

    def __init__(self, *args, **kwargs):
        """Do nothing.

        This class uses the Singleton design pattern and an instance of this
        class is generated and initialized in __new__().
        """
        pass

    def register_healing_handler(self, handler):
        self.heal_reservations = handler

    def register_reallocater(self, reallocator):
        self._reallocate = reallocator

    @abc.abstractmethod
    def filter_allocations(self, reservation, resource_ids):
        """Filter allocations of a reservation by resource ids

        :param reservation: a reservation dict
        :param resource_ids: a list of resource ids
        :return: a list of allocations that contain resources
        """
        pass

    @abc.abstractmethod
    def get_reservations_by_resource_ids(self, resource_ids,
                                         interval_begin, interval_end):
        """Get reservations by resource ids.

        :param resource ids: a list of resource ids.
        :param interval_begin: start date of the searching period.
        :param interval_end: end date of the searching period.
        :return: a list of reservation dict
        """
        pass

    @abc.abstractmethod
    def get_unreservable_resourses(self):
        """Get all unreservable resources

        """
        pass

    @abc.abstractmethod
    def poll_resource_failures(self):
        """Get a list of failed resources and recovered resources

        """
        pass

    @abc.abstractmethod
    def set_reservable(self, resource_id, is_reservable):
        """Set resource as reservable or not reservable

        """
        pass

    def heal_reservations(self, failed_resources, interval_begin,
                          interval_end):
        """Heal reservations which suffer from resource failures.

        :param failed_resources: a list of failed resources.
        :param interval_begin: start date of the period to heal.
        :param interval_end: end date of the period to heal.
        :return: a dictionary of {reservation id: flags to update}
                 e.g. {'de27786d-bd96-46bb-8363-19c13b2c6657':
                       {'missing_resources': True}}
        """
        reservation_flags = {}

        resource_ids = [h['id'] for h in failed_resources]
        reservations = self.get_reservations_by_resource_ids(resource_ids,
                                                             interval_begin,
                                                             interval_end)

        for reservation in reservations:
            if reservation['resource_type'] != self.resource_type:
                continue

            reservation_id = reservation["id"]

            for allocation in self.filter_allocations(reservation,
                                                      resource_ids):
                try:
                    if self._reallocate(allocation):
                        if reservation['status'] == status.reservation.ACTIVE:
                            if reservation_id not in reservation_flags:
                                reservation_flags[reservation_id] = {}
                            reservation_flags[reservation_id].update(
                                {'resources_changed': True})
                    else:
                        if reservation_id not in reservation_flags:
                            reservation_flags[reservation_id] = {}
                        reservation_flags[reservation_id].update(
                            {'missing_resources': True})
                except manager_ex.ResourceBusy:
                    LOG.info(
                        "Cannot heal reservation %s, found servers",
                        reservation["id"]
                    )

        return reservation_flags

    def is_notification_enabled(self):
        """Check if the notification monitor is enabled."""
        return CONF[self.resource_type].enable_notification_monitor

    def get_notification_topics(self):
        """Get topics of notification to subscribe to."""
        return CONF[self.resource_type].notification_topics

    def is_polling_enabled(self):
        """Check if the polling monitor is enabled."""
        return CONF[self.resource_type].enable_polling_monitor

    def get_polling_interval(self):
        """Get interval of polling."""
        return CONF[self.resource_type].polling_interval

    def poll(self):
        """Detect and handle resource failures.

        :return: a dictionary of {reservation id: flags to update}
                 e.g. {'de27786d-bd96-46bb-8363-19c13b2c6657':
                 {'missing_resources': True}}
        """
        LOG.trace('Poll...')

        failed_resources, recovered_resources = self.poll_resource_failures()
        if failed_resources:
            for resource in failed_resources:
                self.set_reservable(resource, False)
        if recovered_resources:
            for resource in recovered_resources:
                self.set_reservable(resource, True)

        return self.heal()

    def get_healing_interval(self):
        """Get interval of reservation healing in minutes."""
        return CONF[self.resource_type].healing_interval

    def heal(self):
        """Heal suffering reservations in the next healing interval.

        :return: a dictionary of {reservation id: flags to update}
        """
        reservation_flags = {}
        resources = self.get_unreservable_resourses()

        interval_begin = datetime.datetime.utcnow()
        interval = self.get_healing_interval()
        if interval == 0:
            interval_end = datetime.date.max
        else:
            interval_end = interval_begin + datetime.timedelta(
                minutes=interval)

        reservation_flags.update(self.heal_reservations(resources,
                                                        interval_begin,
                                                        interval_end))

        return reservation_flags
