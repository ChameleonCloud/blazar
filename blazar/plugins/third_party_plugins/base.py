from oslo_log import log as logging
from oslo_utils import strutils
from oslo_config import cfg

from blazar.utils import plugins as plugins_utils
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_ex
from blazar.plugins import monitor
import datetime
from random import shuffle

from blazar.db import api as db_api

from .exceptions import NotEnoughResourcesAvailable

import collections

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

plugin_opts = [
    cfg.StrOpt('before_end',
               default='',
               help='Actions which we will be taken before the end of '
                    'the lease'),
    cfg.IntOpt('cleaning_time',
               default=0,
               min=0,
               help='The minimum interval [minutes] between the end of a '
               'lease and the start of the next lease for the same '
               'device. This interval is used for cleanup.'),
]

plugin_opts.extend(monitor.monitor_opts)


class BasePlugin():
    def __init__(self):
        CONF.register_opts(plugin_opts, group=self.resource_type())
        self.monitor = ResourceMonitorPlugin(self)

    def resource_type(self):
        pass

    def allocate(self, reservation_id, values):
        pass

    def deallocate(self, resources, lease):
        pass

    def on_start(self, resources, lease):
        LOG.info("ON START")
        pass

    def before_end(self, resources, lease):
        LOG.info("BEFORE END")
        pass

    def on_end(self, resource_id, lease=None):
        LOG.info("ON END")
        resource_reservation = db_api.resource_reservation_get(resource_id)
        db_api.resource_reservation_update(resource_reservation['id'],
                                           {'status': 'completed'})
        allocations = db_api.resource_allocation_get_all_by_values(
            reservation_id=resource_reservation['reservation_id'])
        for allocation in allocations:
            db_api.resource_allocation_destroy(allocation['id'])

    def validate_create_params(self, data):
        pass

    def validate_update_params(self, data):
        pass

    def matching_resources(
        self, resource_properties,
        start_date, end_date, min_resources, max_resources
    ):
        cleaning_time_delta = datetime.timedelta(
            minutes=getattr(CONF, self.resource_type()).cleaning_time)
        start_date_with_margin = start_date - cleaning_time_delta
        end_date_with_margin = end_date + cleaning_time_delta
        filter_array = plugins_utils.convert_requirements(resource_properties)

        not_allocated_resource_ids = []
        for resource in db_api.resource_get_all_by_queries(
                self.resource_type(), filter_array):
            if not db_api.resource_allocation_get_all_by_values(
                    resource_id=resource['id']):
                not_allocated_resource_ids.append(resource['id'])
            elif db_utils.get_free_periods(
                resource['id'],
                start_date_with_margin,
                end_date_with_margin,
                end_date_with_margin - start_date_with_margin,
                resource_type=self.resource_type()
            ) == [
                (start_date_with_margin, end_date_with_margin),
            ]:
                not_allocated_resource_ids.append(resource['id'])
        if len(not_allocated_resource_ids) >= int(min_resources):
            shuffle(not_allocated_resource_ids)
            return not_allocated_resource_ids[:int(min_resources)]
        else:
            raise NotEnoughResourcesAvailable()

    def allocation_candidates(self, values):
        return self.matching_resources(
            values["resource_properties"],
            values["start_date"],
            values["end_date"],
            values["min"],
            values["max"],
        )

    def get(self, resource_id):
        resource = db_api.resource_get(self.resource_type(), resource_id)
        extra_capabilities = self._get_extra_capabilities(resource_id)
        if extra_capabilities:
            res = resource.copy()
            res.update(extra_capabilities)
            return res
        return resource

    def list_allocations(self, query, detail=False):
        resource_id_list = [
            r['id']
            for r in db_api.resource_list(
                self.resource_type(), self.resource_type()
            )
        ]
        options = self.get_query_options(query, "allocation")
        options['detail'] = detail
        resource_allocations = self.query_resource_allocations(
            resource_id_list, **options)
        self.add_extra_allocation_info(resource_allocations)
        return [{"resource_id": resource, "reservations": allocs}
                for resource, allocs in resource_allocations.items()]

    def get_query_options(self, params, index_type):
        options = {k: params[k] for k in params
                   if k in self.query_options[index_type]}
        unsupported = set(params) - set(options)
        if unsupported:
            LOG.debug('Unsupported query key is specified in API request: %s',
                      unsupported)
        return options

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
        resource_reservation = db_api.resource_reservation_get(
            reservation['resource_id'])
        self._update_allocations(dates_before, dates_after, reservation_id,
                                 reservation['status'], resource_reservation,
                                 values, lease)

        updates = {}
        if 'min' in values or 'max' in values:
            count_range = str(values.get(
                'min', resource_reservation['count_range'].split('-')[0])
            ) + '-' + str(values.get(
                'max', resource_reservation['count_range'].split('-')[1])
            )
            updates['count_range'] = count_range
        if 'resource_properties' in values:
            updates['resource_properties'] = values.get(
                'resource_properties')
        if updates:
            db_api.resource_reservation_update(
                resource_reservation['id'], updates)

    def _update_allocations(self, dates_before, dates_after, reservation_id,
                            reservation_status, resource_reservation, values,
                            lease):
        min_resources = values.get('min', int(
            resource_reservation['count_range'].split('-')[0]))
        max_resources = values.get(
            'max', int(resource_reservation['count_range'].split('-')[1]))
        self._validate_min_max_range(values, min_resources, max_resources)
        resource_properties = values.get(
            'resource_properties',
            resource_reservation['resource_properties'])
        allocs = db_api.resource_allocation_get_all_by_values(
            reservation_id=reservation_id)

        allocs_to_remove = self._allocations_to_remove(
            dates_before, dates_after, max_resources,
            resource_properties, allocs)

        if (allocs_to_remove and
                reservation_status == status.reservation.ACTIVE):
            raise manager_ex.NotEnoughResourcesAvailable()

        kept_resources = len(allocs) - len(allocs_to_remove)
        if kept_resources < max_resources:
            min_resources = min_resources - kept_resources \
                if (min_resources - kept_resources) > 0 else 0
            max_resources = max_resources - kept_resources
            resource_ids = self._matching_resources(
                resource_properties,
                str(min_resources) + '-' + str(max_resources),
                dates_after['start_date'], dates_after['end_date'],
                lease['project_id'])
            if len(resource_ids) >= min_resources:
                for resource_id in resource_ids:
                    db_api.resource_allocation_create(
                        {'resource_id': resource_id,
                         'reservation_id': reservation_id})
                    # TODO call allocate() here?
            else:
                raise manager_ex.NotEnoughResourcesAvailable()

        for allocation in allocs_to_remove:
            db_api.resource_allocation_destroy(allocation['id'])

    def _convert_int_param(self, param, name):
        """
        Checks that the parameter is present and can be converted to int.
        """
        if param is None:
            raise manager_ex.MissingParameter(param=name)
        if strutils.is_int_like(param):
            param = int(param)
        else:
            raise manager_ex.MalformedParameter(param=name)
        return param

    def _validate_min_max_range(self, values, min_resources, max_resources):
        min_resources = self._convert_int_param(min_resources, 'min')
        max_resources = self._convert_int_param(max_resources, 'max')
        if min_resources <= 0 or max_resources <= 0:
            raise manager_ex.MalformedParameter(
                param='min and max (must be greater than or equal to 1)')
        if max_resources < min_resources:
            raise manager_ex.InvalidRange()
        values['count_range'] = str(min_resources) + '-' + str(max_resources)

    def _allocations_to_remove(self, dates_before, dates_after, max_resources,
                               resource_properties, allocs):
        allocs_to_remove = []
        requested_resource_ids = [resource['id'] for resource in
                                  self._filter_resources_by_properties(
                                    resource_properties
        )]

        for alloc in allocs:
            if alloc['resource_id'] not in requested_resource_ids:
                allocs_to_remove.append(alloc)
                continue
            if (dates_before['start_date'] > dates_after['start_date'] or
                    dates_before['end_date'] < dates_after['end_date']):
                reserved_periods = db_utils.get_reserved_periods(
                    alloc['resource_id'],
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

        kept_resources = len(allocs) - len(allocs_to_remove)
        if kept_resources > max_resources:
            allocs_to_remove.extend(
                [allocation for allocation in allocs
                 if allocation not in allocs_to_remove
                 ][:(kept_resources - max_resources)]
            )

        return allocs_to_remove

    def _filter_resources_by_properties(self, resource_properties):
        filter = []
        if resource_properties:
            filter += plugins_utils.convert_requirements(resource_properties)
        if filter:
            return db_api.resource_get_all_by_queries(
                    self.resource_type(), filter)
        else:
            return db_api.resource_list(self.resource_type())

    def is_updatable_extra_capability(self, capability, capability_name):
        reservations = db_utils.get_reservations_by_resource_id(
            capability['resource_id'], datetime.datetime.utcnow(),
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

    def update_extra_capabilities(self, resource_id, extras):
        cant_update_extra_capability = []
        previous_capabilities = self._get_extra_capabilities(resource_id)
        updated_keys = set(extras.keys()) & set(previous_capabilities.keys())
        new_keys = set(extras.keys()) - set(previous_capabilities.keys())
        for key in updated_keys:
            raw_capability, cap_name = next(iter(
                db_api.resource_extra_capability_get_all_per_name(
                    resource_id, key)))
            capability = {'capability_value': extras[key]}

            if self.is_updatable_extra_capability(raw_capability, cap_name):
                try:
                    if extras[key] is not None:
                        capability = {'capability_value': extras[key]}
                        db_api.resource_extra_capability_update(
                            raw_capability['id'], capability)
                    else:
                        db_api.resource_extra_capability_destroy(
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
                'resource_id': resource_id,
                'capability_name': key,
                'capability_value': extras[key],
            }
            try:
                db_api.resource_extra_capability_create(
                    self.resource_type(), new_capability)
            except (db_ex.BlazarDBException, RuntimeError):
                cant_update_extra_capability.append(key)

        if cant_update_extra_capability:
            raise manager_ex.CantAddExtraCapability(
                resource=resource_id, keys=cant_update_extra_capability)

        LOG.info('Extra capabilities on resource %s updated with %s',
                 resource_id, extras)
        return self.get(resource_id)

    def _get_extra_capabilities(self, resource_id):
        extra_capabilities = {}
        raw_extra_capabilities = (
            db_api.resource_extra_capability_get_all_per_resource(resource_id))
        for capability, capability_name in raw_extra_capabilities:
            key = capability_name
            extra_capabilities[key] = capability.capability_value
        return extra_capabilities

    def poll_resource_failures(self):
        return [], []

    def notification_callback(self, event_type, payload):
        return {}

    def get_notification_event_types(self):
        return []


class ResourceMonitorPlugin(monitor.GeneralMonitorPlugin):
    def __new__(cls, plugin, *args, **kwargs):
        if not cls._instance:
            cls._instance = \
                super(ResourceMonitorPlugin, cls).__new__(cls, *args, **kwargs)
            cls._instance.plugin = plugin
            cls._instance.resource_type = plugin.resource_type()
        return cls._instance

    def filter_allocations(self, reservation, resource_ids):
        return [alloc for alloc
                in reservation['resource_allocations']
                if alloc['resource_id'] in resource_ids]

    def get_reservations_by_resource_ids(
            self, resource_ids, resource_type, interval_begin, interval_end):
        return db_utils.get_reservations_by_resource_ids(resource_ids,
                                                         resource_type,
                                                         interval_begin,
                                                         interval_end)

    def get_unreservable_resourses(self):
        return db_api.unreservable_resource_get_all_by_queries(
            self.plugin.resource_type(), [])

    def get_notification_event_types(self):
        return self.plugin.get_notification_event_types()

    def notification_callback(self, event_type, payload):
        return self.plugin.notification_callback(event_type, payload)

    def set_reservable(self, resource, is_reservable):
        db_api.resource_update(
            self.plugin.resource_type(),
            resource["id"],
            {"reservable": is_reservable}
        )
        LOG.warn('%s %s.', resource["name"],
                 "recovered" if is_reservable else "failed")

    def poll_resource_failures(self):
        return self.plugin.poll_resource_failures()
