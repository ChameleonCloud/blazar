from oslo_log import log as logging
from oslo_utils import strutils
from oslo_config import cfg
from oslo_serialization import jsonutils
from oslo_policy.policy import DocumentedRuleDefault

from blazar import status
from blazar import policy
from blazar.api.v1 import validation
from blazar.api.v1 import utils as api_utils
from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from blazar.manager import exceptions as manager_ex
from blazar.plugins import monitor
from blazar.policies import base
from blazar.utils import plugins as plugins_utils
from blazar.utils import trusts
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


class BasePlugin(metaclass=abc.ABCMeta):
    query_options = {
        QUERY_TYPE_ALLOCATION: ['lease_id', 'reservation_id']
    }

    def __init__(self):
        CONF.register_opts(plugin_opts, group=self.resource_type())
        self.monitor = ResourceMonitorPlugin(self)

    @abc.abstractmethod
    def resource_type(self):
        """Gets the resource type for this plugin"""
        pass

    def validate_data(
            self, data, required_keys, optional_keys, action_type="create"):
        """A helper function to validate data has the proper structure"""
        if action_type == "create":
            ex_fn = exceptions.InvalidCreateResourceData
        elif action_type == "update":
            ex_fn = exceptions.InvalidUpdateResourceData
        data_keys = set(data.keys())
        required_keys = set(required_keys)
        missing_required_keys = required_keys - data_keys
        if missing_required_keys:
            msg = f"{self.resource_type()} plugin requires " \
                   "'{missing_required_keys}'"
            raise ex_fn(msg)
        optional_keys = set(optional_keys)
        extra_keys = (data_keys - optional_keys) - required_keys
        if extra_keys:
            raise ex_fn(f"Invalid keys in data '{extra_params}'")

    # TODO what information should they have here?
    def allocate(self, reservation_id, resource_id):
        """Take action after an allocation is made"""
        pass

    def deallocate(self, resource_id):
        """Take action after an allocation is deleted"""
        pass

    def on_start(self, resource_id, lease):
        """Wake up resource"""
        pass

    def before_end(self, resource_id, lease):
        """Take actions before the end of a lease"""
        pass

    def on_end(self, resource_id, lease=None):
        """Delete resource."""
        resource_reservation = db_api.resource_reservation_get(resource_id)
        db_api.resource_reservation_update(resource_reservation['id'],
                                           {'status': 'completed'})
        allocations = db_api.resource_allocation_get_all_by_values(
            reservation_id=resource_reservation['reservation_id'])
        for allocation in allocations:
            db_api.resource_allocation_destroy(allocation['id'])
            self.deallocate(resource_id)

    def validate_create_params(self, data):
        """Modify and check the create resource params are valid"""
        return data

    def rollback_create(self, data):
        """Rollback after an exception while creating the resource"""
        pass

    def validate_update_params(self, data):
        """Modify and check the update resource params are valid"""
        return data

    def validate_delete(self, resource_id):
        """Validate that the resource can be deleted"""
        pass

    def matching_resources(
        self, resource_properties,
        start_date, end_date, min_resources, max_resources
    ):
        """Returns a list of all resources matching the parameters"""
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
            raise manager_ex.NotEnoughResourcesAvailable()

    def allocation_candidates(self, values):
        """Returns a list of all resources matching the parameters"""
        return self.matching_resources(
            values["resource_properties"],
            values["start_date"],
            values["end_date"],
            values["min"],
            values["max"],
        )

    def get(self, resource_id):
        """Gets the resource with the ID, and extra capabilities"""
        resource = db_api.resource_get(self.resource_type(), resource_id)
        extra_capabilities = self._get_extra_capabilities(resource_id)
        if extra_capabilities:
            res = resource.copy()
            res.update(extra_capabilities)
            return res
        return resource

    def list_allocations(self, query, detail=False):
        """List all allocations"""
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
            # TODO func does not exist
            resource_ids = self._matching_resources(
                resource_properties,
                str(min_resources) + '-' + str(max_resources),
                dates_after['start_date'], dates_after['end_date'],
                lease['project_id'])
            if len(resource_ids) >= min_resources:
                self.reserve_new_resources(reservation_id, reservation_status)
            else:
                raise manager_ex.NotEnoughResourcesAvailable()

        for allocation in allocs_to_remove:
            db_api.resource_allocation_destroy(allocation['id'])

    def reservation_values(self, reservation_id, values):
        """Get the values to be stored with the reservation"""
        return values["resource_properties"]

    def reserve_resource(self, reservation_id, values):
        """Reserve the resources"""
        self._validate_min_max_range(values, values["min"], values["max"])
        resource_ids = self.allocation_candidates(values)
        if not resource_ids:
            raise manager_ex.NotEnoughResourcesAvailable()
        rsrv_values = {
            "reservation_id": reservation_id,
            "values": self.reservation_values(reservation_id, values),
            "status": "pending",
            "count_range": values["count_range"],
            "resource_type": self.resource_type(),
        }
        resource_reservation = db_api.resource_reservation_create(
            rsrv_values)
        self.reserve_new_resources(reservation_id, None, resource_ids)
        return resource_reservation["id"]

    def reserve_new_resources(self, reservation_id, reservation_status, resource_ids):
        """Reserve and create allocations for these new resources"""
        for resource_id in resource_ids:
            db_api.resource_allocation_create(
                {'resource_id': resource_id, 'reservation_id': reservation_id})
            self.allocate(reservation_id, resource_id)

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

    def reallocate(self, allocation):
        """
        Reallocate this allocation to a different resource, return if
        successful.
        """
        return True

    def api_list(self):
        policy.check_enforcement(self.resource_type(), "get")
        raw_resource_list = db_api.resource_list(self.resource_type())
        resource_list = []
        for resource in raw_resource_list:
            resource_list.append(self.get(resource['id']))
        return resource_list

    @trusts.use_trust_auth()
    def api_create(self, data):
        policy.check_enforcement(self.resource_type(), "post")
        create_data = data["data"]
        trust_id = data["trust_id"]
        create_data["trust_id"] = trust_id
        data = self.validate_create_params(create_data)
        try:
            resource = db_api.resource_create(self.resource_type(), data)
        except db_ex.BlazarDBException as e:
            self.rollback_create(data["data"])
            raise e
        return resource

    def api_get(self, resource_id):
        policy.check_enforcement(self.resource_type(), "get")
        resource = self.get(resource_id)
        if resource is None:
            raise manager_ex.ResourceNotFound(
                resource=resource_id, resource_type=self.resource_type())
        return resource

    def api_update(self, resource_id, data):
        policy.check_enforcement(self.resource_type(), "put")
        extras = data["extras"]
        data = data["data"]
        if not data and not extras:
            return None
        else:
            if data:
                data = self.validate_update_params(data)
                db_api.resource_update(self.resource_type(),
                                       resource_id,
                                       jsonutils.loads(data["data"]))
            if extras:
                self.update_extra_capabilities(resource_id, extras)
            return db_api.resource_get(self.resource_type(), resource_id)

    def api_delete(self, resource_id):
        policy.check_enforcement(self.resource_type(), "delete")
        resource = db_api.resource_get(self.resource_type(), resource_id)
        if resource is None:
            raise manager_ex.ResourceNotFound(
                resource=resource_id, resource_type=self.resource_type())
        allocations = db_api.resource_allocation_get_all_by_values(
            resource_id=resource_id)
        if allocations:
            msg = 'Resource id %s is allocated by reservations.' % resource_id
            LOG.info(msg)
            raise manager_ex.CantDeleteResource(
                resource=resource_id, msg=msg, resource_type=resource_type)
        try:
            self.validate_delete(resource_id)
            db_api.resource_destroy(self.resource_type(), resource_id)
        except db_ex.BlazarDBException as e:
            raise manager_ex.CantDeleteResource(
                resource=resource_id,
                resource_type=self.resource_type(),
                msg=str(e)
            )

    def api_list_allocations(self, query):
        policy.check_enforcement(self.resource_type(), "get_allocations")
        resource_id_list = [
            r['id'] for r in db_api.resource_list(self.resource_type())]
        options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
        options['detail'] = False
        resource_allocations = self.query_resource_allocations(
            resource_id_list, **options)

        return [
            {"resource_id": resource, "reservations": allocs}
            for resource, allocs in resource_allocations.items()
        ]

    def get_allocations(self, resource_id, query, detail=False):
        options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
        options['detail'] = False
        resource_allocations = self.query_resource_allocations(
            [resource_id], **options)
        allocs = resource_allocations.get(resource_id, [])
        return {"resource_id": resource_id, "reservations": allocs}

    def api_get_allocations(self, resource_id, query):
        policy.check_enforcement(self.resource_type(), "get_allocations")
        return self.get_allocations(resource_id, query)

    def api_reallocate(self, resource_id, data):
        policy.check_enforcement(self.resource_type(), "reallocate")
        allocations = self.get_allocations(resource_id, data, detail=True)

        for alloc in allocations['reservations']:
            reservation_flags = {}
            resource_allocation = db_api.resource_allocation_get_all_by_values(
                resource_id=resource_id,
                reservation_id=alloc['id'])[0]

            if self.reallocate(resource_allocation):
                if alloc['status'] == status.reservation.ACTIVE:
                    reservation_flags.update(dict(resources_changed=True))
                    db_api.lease_update(alloc['lease_id'], dict(degraded=True))
            else:
                reservation_flags.update(dict(missing_resources=True))
                db_api.lease_update(alloc['lease_id'], dict(degraded=True))

            db_api.reservation_update(alloc['id'], reservation_flags)

        return self.get_allocations(resource_id, data)

    def api_list_resource_properties(self, query):
        policy.check_enforcement(self.resource_type(), "get_resource_properties")
        detail = False if not query else query.get('detail', False)
        resource_properties = collections.defaultdict(list)

        for name, private, value in db_api.resource_properties_list(
                self.resource_type()):

            if not private:
                resource_properties[name].append(value)

        if detail:
            resource_properties = [
                dict(property=k, private=False, values=v)
                for k, v in resource_properties.items()]
        else:
            resource_properties = [
                dict(property=k) for k, v in resource_properties.items()]
        return resource_properties

    def api_update_resource_property(self, property_name, data):
        policy.check_enforcement(self.resource_type(), "patch_resource_properties")
        return db_api.resource_property_update(
            self.resource_type(), property_name, data)

    def create_API(self):
        """Create the API endpoints for this resource type"""
        rest = api_utils.Rest(f'{self.resource_type()}_v1_0',
                              __name__,
                              url_prefix=f'/v1/{self.resource_type()}')

        @rest.get('', query=True)
        def resource_list(req, query=None):
            return api_utils.render(resources=self.api_list())

        @rest.post('')
        def resource_create(req, data):
            return api_utils.render(resource=self.api_create(data))

        @rest.get('/<resource_id>')
        @validation.check_exists(self.get, resource_id='resource_id')
        def resource_get(req, resource_id):
            return api_utils.render(resource=self.api_get(resource_id))

        @rest.put('/<resource_id>')
        @validation.check_exists(self.get, resource_id='resource_id')
        def resource_update(req, resource_id, data):
            resource = self.api_update(resource_id, data)
            if resource:
                return api_utils.render(resource=resource)
            else:
                return api_utils.internal_error(status_code=400,
                                                descr="No data to update")

        @rest.delete('/<resource_id>')
        @validation.check_exists(self.get, resource_id='resource_id')
        def resource_delete(req, resource_id):
            self.api_delete(resource_id)
            return api_utils.render(status=200)

        @rest.get('/allocations', query=True)
        def allocations_list(req, query):
            return api_utils.render(
                    allocations=self.api_list_allocations(query))

        @rest.get('/<resource_id>/allocation', query=True)
        @validation.check_exists(self.get, resource_id='resource_id')
        def allocations_get(req, resource_id, query):
            return api_utils.render(
                    allocation=self.api_get_allocations(resource_id, query))

        @rest.put('/<resource_id>/allocation')
        @validation.check_exists(self.get, resource_id='resource_id')
        def reallocate(req, resource_id, data):
            return api_utils.render(
                    allocation=self.api_reallocate(resource_id, data))

        @rest.get('/properties', query=True)
        def resource_properties_list(req, query=None):
            return api_utils.render(
                resource_properties=self.api_list_resource_properties(query))

        @rest.patch('/properties/<property_name>')
        def resource_property_update(req, property_name, data):
            return api_utils.render(
                    resource_property=self.api_update_resource_property(
                        property_name, data))

        return rest

    def get_query_options(self, params, index_type):
        options = {k: params[k] for k in params
                   if k in self.query_options[index_type]}
        unsupported = set(params) - set(options)
        if unsupported:
            LOG.debug('Unsupported query key is specified in API request: %s',
                      unsupported)
        return options

    def query_resource_allocations(self, resources, lease_id=None,
                                   reservation_id=None, detail=False):
        start = datetime.datetime.utcnow()
        end = datetime.date.max

        reservations = db_utils.get_reservation_allocations_by_resource_ids(
            resources, start, end, lease_id, reservation_id)
        resource_allocations = {d: [] for d in resources}

        for reservation in reservations:
            if detail:
                del reservation['project_id']
                del reservation['lease_name']
                del reservation['status']

            for resource_id in reservation['resource_ids']:
                if resource_id in resource_allocations.keys():
                    resource_allocations[resource_id].append({
                        k: v for k, v in reservation.items()
                        if k != 'resource_ids'})

        return resource_allocations

    def get_policy(self):
        """Get the policy for this resource"""
        policy_root = f'blazar:{self.resource_type()}:%s'
        resource_policy = [
            DocumentedRuleDefault(
                name=policy_root % 'get',
                check_str=base.RULE_ADMIN,
                description='Policy rule for List/Show Resource(s) API.',
                operations=[
                    {
                        'path': '/{api_version}/'+self.resource_type(),
                        'method': 'GET'
                    },
                    {
                        'path': '/{api_version}/'+self.resource_type(),
                        'method': 'GET'
                    }
                ]
            ),
            DocumentedRuleDefault(
                name=policy_root % 'post',
                check_str=base.RULE_ADMIN,
                description='Policy rule for Create Resource API.',
                operations=[
                    {
                        'path': '/{api_version}/'+self.resource_type(),
                        'method': 'POST'
                    }
                ]
            ),
            DocumentedRuleDefault(
                name=policy_root % 'put',
                check_str=base.RULE_ADMIN,
                description='Policy rule for Update Resource API.',
                operations=[
                    {
                        'path': '/{api_version}/'+self.resource_type()+'/{resource_id}',
                        'method': 'PUT'
                    }
                ]
            ),
            DocumentedRuleDefault(
                name=policy_root % 'delete',
                check_str=base.RULE_ADMIN,
                description='Policy rule for Delete Resource API.',
                operations=[
                    {
                        'path': '/{api_version}/'+self.resource_type()+'/{resource_id}',
                        'method': 'DELETE'
                    }
                ]
            ),
            DocumentedRuleDefault(
                name=policy_root % 'get_allocations',
                check_str=base.RULE_ADMIN,
                description='Policy rule for List/Get Resource(s) Allocations API.',
                operations=[
                    {
                        'path': '/{api_version}/'+self.resource_type()+'/allocations',
                        'method': 'GET'
                    },
                    {
                        'path': '/{api_version}/'+self.resource_type()+'/{resource_id}/allocation',
                        'method': 'GET'
                    }
                ]
            ),
            DocumentedRuleDefault(
                name=policy_root % 'reallocate',
                check_str=base.RULE_ADMIN,
                description='Policy rule for Reallocate Resource API.',
                operations=[
                    {
                        'path': '/{api_version}/'+self.resource_type()+'/{resource_id}/allocation',
                        'method': 'PUT'
                    }
                ]
            ),
            DocumentedRuleDefault(
                name=policy_root % 'get_resource_properties',
                check_str=base.RULE_ADMIN,
                description='Policy rule for Resource Properties API.',
                operations=[
                    {
                        'path': '/{api_version}/'+self.resource_type()+'/resource_properties',
                        'method': 'GET'
                    }
                ]
            ),
            DocumentedRuleDefault(
                name=policy_root % 'patch_resource_properties',
                check_str=base.RULE_ADMIN,
                description='Policy rule for Resource Properties API.',
                operations=[
                    {
                        'path': ('/{api_version}/'+self.resource_type()+'/resource_properties/'
                                 '{property_name}'),
                        'method': 'PATCH'
                    }
                ]
            ),
        ]
        return resource_policy


class ResourceMonitorPlugin(monitor.GeneralMonitorPlugin):
    """Monitor for a resource plugin"""
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
            self, resource_ids, interval_begin, interval_end):
        return db_utils.get_reservations_by_resource_ids(
            resource_ids, self.plugin.resource_type(),
            interval_begin, interval_end
        )

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
