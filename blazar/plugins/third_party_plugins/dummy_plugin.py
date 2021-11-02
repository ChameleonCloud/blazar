from . import base
from blazar.plugins.third_party_plugins import exceptions
from blazar.db import api as db_api
from blazar.db import exceptions as db_ex
from blazar.db import utils as db_utils
from oslo_log import log as logging
from oslo_config import cfg

LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class DummyPlugin(base.BasePlugin):
    def resource_type(self):
        return "dummy"

    def validate_data(self, data, action_type):
        LOG.info("validate")
        LOG.info(type(data))
        LOG.info(type(action_type))
        if action_type == "create":
            ex_fn = exceptions.InvalidCreateResourceData
        elif action_type == "update":
            ex_fn = exceptions.InvalidUpdateResourceData

        valid_params = set(["name", "score"])
        data_params = set(data.keys())
        # Check required params
        for item in valid_params:
            if item not in data_params:
                raise ex_fn(f"Dummy plugin requires '{item}'")
        # Check no extra params
        extra_params = data_params - valid_params
        if extra_params:
            raise ex_fn(f"Invalid keys in data '{extra_params}'")

    def validate_create_params(self, data):
        self.validate_data(data, "create")

    def validate_update_params(self, data):
        self.validate_data(data, "update")

    def allocate(self, reservation_id, values):
        LOG.info("allocate()")
        LOG.info(values)
        self._validate_min_max_range(values, values["min"], values["max"])
        resource_ids = self.allocation_candidates(values)
        resource_rsrv_values = {
            'reservation_id': reservation_id,
            'values': values['resource_properties'],
            'status': 'pending',
            "resource_type": self.resource_type(),
            'count_range': values['count_range'],
        }
        resource_reservation = db_api.resource_reservation_create(
            resource_rsrv_values)
        for resource_id in resource_ids:
            db_api.resource_allocation_create(
                {'resource_id': resource_id, 'reservation_id': reservation_id})
        return resource_reservation['id']

    def deallocate(self, resources, lease):
        LOG.info("allocating dummy")
