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

import json
import subprocess
import sys

from oslotest import base

# Groups registered by libraries Blazar consumes rather than by Blazar
# itself. Each reaches blazar.conf.sample through its own namespace in
# etc/blazar/blazar-config-generator.conf, so blazar.opts must not export
# them.
EXTERNAL_GROUPS = [
    'cache',
    'database',
    'keystone_authtoken',
    'oslo_concurrency',
    'oslo_messaging_metrics',
    'oslo_messaging_tracing',
    'oslo_middleware',
]

# Run out of process, the way oslo-config-generator loads the entry point.
# Importing blazar.opts in-process is not viable here: it pulls in
# blazar.db.migration.cli, whose module-level register_cli_opts() adds a
# required subcommand that then breaks CONF(args=[]) for every other test
# sharing the worker.
_PROBE = """
import json
from oslo_config import cfg
from blazar import opts

# an entry's group is either a name or a described OptGroup
exported = [getattr(group, 'name', group) for group, _ in opts.list_opts()]
print(json.dumps({
    'exported': exported,
    'registered': list(cfg.CONF._groups),
}))
"""


class TestListOpts(base.BaseTestCase):
    """Guard against option groups missing from the generated sample."""

    def setUp(self):
        super(TestListOpts, self).setUp()
        output = subprocess.check_output([sys.executable, '-c', _PROBE])
        self.result = json.loads(output.decode().strip().splitlines()[-1])

    def test_every_blazar_group_is_exported(self):
        exported = set(self.result['exported'])
        registered = set(self.result['registered']) - set(EXTERNAL_GROUPS)

        self.assertEqual(
            set(), registered - exported,
            "option group(s) registered by Blazar but absent from "
            "blazar.opts.list_opts(); they will not appear in "
            "blazar.conf.sample")

    def test_no_group_is_exported_twice(self):
        exported = self.result['exported']
        self.assertEqual(sorted(set(exported)), sorted(exported))
