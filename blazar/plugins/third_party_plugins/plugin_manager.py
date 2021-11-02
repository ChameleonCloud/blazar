from blazar.api.v1 import utils as api_utils
from blazar.api.v1 import validation
from blazar.db import exceptions as db_ex
from . import exceptions as manager_ex
from blazar.db import api as db_api
from blazar.db import utils as db_utils
from blazar import manager
from blazar.utils import service

from oslo_log import log as logging
from oslo_serialization import jsonutils

import datetime
import collections

LOG = logging.getLogger(__name__)
QUERY_TYPE_ALLOCATION = 'allocation'


class PluginManager():
    _instance = None

    def __init__(self):
        self.plugins = []

    def instance():
        if PluginManager._instance is None:
            PluginManager._instance = PluginManager()
        return PluginManager._instance

    def supports(self, resource_type):
        return any(iter(
            [resource_type == p.resource_type() for p in self.plugins]))

    def get(self, resource_type):
        return next(iter(
            [p for p in self.plugins if resource_type == p.resource_type()]))

    def add_plugin(self, plugin_module, app, add_endpoints=True):
        new_plugin = plugin_module()
        self.plugins.append(new_plugin)

    def create_api(self, app):
        for plugin in self.plugins:
            resource_type = new_plugin.resource_type()
            rest = api_utils.Rest(f'{resource_type}_v1_0',
                                  __name__,
                                  url_prefix=f'/v1/{resource_type}')
            self._create_rest_endpoints(rest, new_plugin, resource_type)
            app.register_blueprint(rest)

    def _create_rest_endpoints(self, rest, plugin, resource_type):
        @rest.get('', query=True)
        def resource_list(req, query=None):
            resource_list = db_api.resource_list(resource_type)
            return api_utils.render(resources=resource_list)

        @rest.post('')
        def resource_create(req, data):
            data = data["data"]
            plugin.validate_create_params(data)
            resource = db_api.resource_create(resource_type, data)
            return api_utils.render(resource=resource)

        @rest.get('/<resource_id>')
        def resource_get(req, resource_id):
            resource = plugin.get(resource_id)
            if resource is None:
                raise manager_ex.ResourceNotFound(
                    resource=resource_id, resource_type=resource_type)
            return api_utils.render(resource=resource)

        @rest.put('/<resource_id>')
        def resource_update(req, resource_id, data):
            extras = data["extras"]
            data = data["data"]
            if not data and not extras:
                return api_utils.internal_error(status_code=400,
                                                descr="No data to update")
            else:
                if data:
                    plugin.validate_update_params(data)
                    db_api.resource_update(resource_type,
                                           resource_id,
                                           jsonutils.loads(data["data"]))
                if extras:
                    plugin.update_extra_capabilities(resource_id, extras)
                return api_utils.render(
                    resource=db_api.resource_get(resource_type, resource_id))

        @rest.delete('/<resource_id>')
        def resource_delete(req, resource_id):
            # TODO check return value
            resource = db_api.resource_get(resource_type, resource_id)
            if resource is None:
                raise manager_ex.ResourceNotFound(
                    resource=resource_id, resource_type=resource_type)
            """
            allocations = db_api.resource_allocation_get_all_by_values(
                resource_id=resource_id)
            if allocations:
                msg = 'Resource id %s is allocated by reservations.' % resource_id
                LOG.info(msg)
                raise manager_ex.CantDeleteResource(resource=resource_id, msg=msg, resource_type=resource_type)
            """
            try:
                db_api.resource_destroy(resource_type, resource_id)
            except db_ex.BlazarDBException as e:
                raise manager_ex.CantDeleteResource(
                    resource=resource_id,
                    resource_type=resource_type,
                    msg=str(e)
                )
            return api_utils.render(status=200)

        @rest.get('/allocations', query=True)
        def allocations_list(req, query):
            resource_id_list = [
                r['id'] for r in db_api.resource_list(resource_type)]
            options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
            options['detail'] = False  # TODO use conf
            resource_allocations = self.query_resource_allocations(
                resource_id_list, **options)

            allocations = [
                {"resource_id": resource, "reservations": allocs}
                for resource, allocs in resource_allocations.items()
            ]
            return api_utils.render(allocations=allocations)

        @rest.get('/<resource_id>/allocation', query=True)
        #@validation.check_exists(_api.get_resource, resource_id='resource_id')
        def allocations_get(req, resource_id, query):
            options = self.get_query_options(query, QUERY_TYPE_ALLOCATION)
            options['detail'] = False  # self.query_device_allocations TODO use conf
            resource_allocations = self.query_resource_allocations(
                [resource_id], **options)
            allocs = resource_allocations.get(resource_id, [])
            allocation = {"resource_id": resource_id, "reservations": allocs}
            return api_utils.render(allocation=allocation)

        @rest.put('/<resource_id>/allocation')
        #@validation.check_exists(_api.get_resource, resource_id='resource_id')
        def reallocate(req, resource_id, data):

            return api_utils.render(
                allocation=_api.reallocate(resource_id, data))

        @rest.get('/properties', query=True)
        def resource_properties_list(req, query=None):
            detail = False if not query else query.get('detail', False)
            resource_properties = collections.defaultdict(list)

            for name, private, value in db_api.resource_properties_list(
                    resource_type):

                if not private:
                    resource_properties[name].append(value)

            if detail:
                resource_properties = [
                    dict(property=k, private=False, values=v)
                    for k, v in resource_properties.items()]
            else:
                resource_properties = [
                    dict(property=k) for k, v in resource_properties.items()]
            return api_utils.render(
                resource_properties=resource_properties)

        @rest.patch('/properties/<property_name>')
        def resource_property_update(req, property_name, data):
            resource_property = db_api.resource_property_update(
                resource_type, property_name, data)
            return api_utils.render(resource_property=resource_property)

    def get_query_options(self, params, index_type):
        query_options = {
            QUERY_TYPE_ALLOCATION: ['lease_id', 'reservation_id']
        }
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
            # TODO detail config?
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

class ManagerRPCAPI(service.RPCClient):
    """Client side for the Manager RPC API.

    Used from other services to communicate with blazar-manager service.
    """
    def __init__(self):
        """Initiate RPC API client with needed topic and RPC version."""
        super(ManagerRPCAPI, self).__init__(manager.get_target())

    def create_api(self, app):
        return self.call_without_context('create_api', app=app)

    def list_plugins(self):
        return self.call_without_context('list_plugins')
