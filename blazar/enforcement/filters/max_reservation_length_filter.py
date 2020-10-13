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

from datetime import datetime
from datetime import timedelta

from blazar.enforcement.filters import base_filter
from blazar import exceptions
from blazar.i18n import _

from oslo_config import cfg
from oslo_log import log as logging


DEFAULT_MAX_RESERVATION_LENGTH = -1
DEFAULT_RESERVATION_EXTENTSION_WINDOW = -1


LOG = logging.getLogger(__name__)


class MaxReservationLengthException(exceptions.NotAuthorized):
    code = 400
    msg_fmt = _('Lease length of %(lease_length)s seconds be less than or '
                'equal the maximum lease length of %(max_length)s seconds.')


class MaxReservationUpdateWindowException(exceptions.NotAuthorized):
    code = 400
    msg_fmt = _('Lease can only be extended within %(extension_window)s '
                'seconds of the leases current end time.')


class MaxReservationLengthFilter(base_filter.BaseFilter):

    enforcement_opts = [
        cfg.IntOpt(
            'max_reservation_length',
            default=DEFAULT_MAX_RESERVATION_LENGTH,
            help='Maximum lease duration in seconds. If this is set to -1, '
                 'there is not limit. For active leases being updated, '
                 'the limit applies between now and the new end date.'),
        cfg.IntOpt(
            'reservation_extension_window',
            default=DEFAULT_RESERVATION_EXTENTSION_WINDOW,
            help='Gives users a window towards the end of a reservation to '
                 'extend their lease again for the max lease length. A '
                 'value of -1 will not allow users to extend leases beyond '
                 'the maximum lease length'),
        cfg.ListOpt(
            'max_reservation_length_exempt_project_ids',
            default=[],
            help='White list of project ids exempt from filter constraints.'),
    ]

    def __init__(self, conf=None):
        super(MaxReservationLengthFilter, self).__init__(conf=conf)
        self.exempt_projects = self.max_reservation_length_exempt_project_ids

    def check_for_length_violation(self, start_date, end_date):
        if not self.max_reservation_length:
            return

        lease_length = (end_date - start_date).total_seconds()

        if lease_length > self.max_reservation_length:
            raise MaxReservationLengthException(
                lease_length=lease_length,
                max_length=self.max_reservation_length)

    def check_create(self, context, lease_values):
        if context['project_id'] in self.exempt_projects:
            return

        start_date = lease_values['start_date']
        end_date = lease_values['end_date']

        self.check_for_length_violation(start_date, end_date)

    def check_update(self, context, current_lease_values, new_lease_values):
        if context['project_id'] in self.exempt_projects:
            return

        start_date = current_lease_values['start_date']
        end_date = new_lease_values['end_date']

        # Check if lease is being extended
        if (current_lease_values['end_date'] >= end_date and
                start_date >= new_lease_values['start_date']):
            return

        if self.reservation_extension_window:
            min_window = current_lease_values['end_date'] - timedelta(
                seconds=self.reservation_extension_window)
            update_at = datetime.utcnow()

            if update_at < min_window:
                raise MaxReservationUpdateWindowException(
                    extension_window=(self.reservation_extension_window))

            start_date = current_lease_values['end_date']

        self.check_for_length_violation(start_date, end_date)

    def on_end(self, context, lease_values):
        pass
