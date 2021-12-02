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

    def validate_create_params(self, data):
        return self.validate_data(data, ["name", "score"], [], "create")

    def validate_update_params(self, data):
        return self.validate_data(data, ["name", "score"], [], "update")
