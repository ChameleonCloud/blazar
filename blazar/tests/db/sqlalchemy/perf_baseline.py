# Copyright (c) 2026 OpenStack Foundation.
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

"""Manual harness counting SQL statements and rows fetched by DB-layer calls.

Each case seeds a fixed dataset and runs a dbapi call, then outputs:
- # of sql statements sent from python->DB
- # of rows returned from DB->python

The use-case is to sanity check methods that hit the DB, a query that returns
two hosts or leases shouldn't return 1000s of rows, or run dozens of queries.

As this is a tool, not pass/fail tests (currently), it deliberately doesn't 
match the `test*.py` unittest discovery pattern, and won't run automatically.

Run via: `python -m unittest -v blazar.tests.db.sqlalchemy.perf_baseline`
"""

import contextlib

from sqlalchemy import event

# registers the [database] config group at import time; without it the
# DBTestCase fixture fails under plain `python -m unittest`
import blazar.db.api  # noqa: F401

from blazar.db.sqlalchemy import api as db_api
from blazar.db.sqlalchemy import facade_wrapper
from blazar.tests.db.sqlalchemy.test_sqlalchemy_api import (
    _get_fake_event_values,
    _get_fake_host_extra_capabilities,
    _get_fake_host_values,
    _get_fake_phys_lease_values,
    _get_fake_phys_reservation_values,
)
from blazar import tests


@contextlib.contextmanager
def db_counters():
    """Count SQL statements and rows handed from the DB to Python.

    Rows are counted via a passthrough sqlite row_factory -- it sees every row
    materialized into Python, which is the metric that exposes a whole-table
    fetch (one statement, but the entire table crosses into Python). dispose()
    forces connections to (re)open so they pick up / drop the factory; safe
    because the test DB is a temp file, not :memory:.
    """
    engine = facade_wrapper.get_engine()
    c = {"queries": 0, "rows": 0}

    def before(*args, **kwargs):
        c["queries"] += 1

    def on_connect(dbapi_conn, rec):
        base = dbapi_conn.row_factory

        def factory(cursor, row):
            c["rows"] += 1
            return base(cursor, row) if base else row

        dbapi_conn.row_factory = factory

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "connect", on_connect)
    engine.dispose()
    try:
        yield c
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "connect", on_connect)
        engine.dispose()


class QueryCounts(tests.DBTestCase):

    def _count(self, label, fn):
        with db_counters() as c:
            fn()
        print("\n%-56s queries=%-4d rows=%d" % (label, c["queries"], c["rows"]))

    def _seed_hosts(self, n):
        return [db_api.host_create(_get_fake_host_values())["id"]
                for _ in range(n)]

    def _seed_caps(self, host_ids, cap, value):
        for host_id in host_ids:
            db_api.host_extra_capability_create(
                _get_fake_host_extra_capabilities(
                    computehost_id=host_id,
                    name=cap,
                    value=value,
                )
            )

    def test_host_get_all_by_queries(self):
        # Filters hosts by column and extra-capability predicates, returning
        # matching hosts with their capabilities joined.

        # create 100 hosts, 4 capabilities each.
        # hosts 0-10 get value 0, 11-100 get value 1
        hosts = self._seed_hosts(100)
        self._seed_caps(hosts[:10], cap="cap0", value="0")
        self._seed_caps(hosts[:10], cap="cap1", value="0")
        self._seed_caps(hosts[:10], cap="cap2", value="0")
        self._seed_caps(hosts[:10], cap="cap3", value="0")

        self._seed_caps(hosts[10:], cap="cap0", value="1")
        self._seed_caps(hosts[10:], cap="cap1", value="1")
        self._seed_caps(hosts[10:], cap="cap2", value="1")
        self._seed_caps(hosts[10:], cap="cap3", value="1")

        self._count(
            ("host_get_all_by_queries 100 hosts x 4 caps, 1 predicate.\n"
             + "Should be 10 hosts x 4 caps = 40 rows\n"),
            lambda: db_api.host_get_all_by_queries(["cap0 == 0"]),
        )

        # each host's caps share one value, so the same 10 hosts match all
        # four predicates -- the ideal result doesn't grow with predicates
        self._count(
            ("host_get_all_by_queries 100 hosts x 4 caps, 4 predicates.\n"
             + "Should be 10 hosts x 4 caps = 40 rows\n"),
            lambda: db_api.host_get_all_by_queries(
                [
                    "cap0 == 0",
                    "cap1 == 0",
                    "cap2 == 0",
                    "cap3 == 0",
                 ]
            ),
        )

    def test_lease_list(self):
        # Lease list fetches leases, reservation, and events, so it can return
        # leases with context.

        # create 200 leases
        # x 4 reservations per lease, 800 total
        # x 6 events per lease, 1200 total
        for _ in range(200):
            values = _get_fake_phys_lease_values()
            values["reservations"] = [
                _get_fake_phys_reservation_values(lease_id=values["id"])
                for _ in range(4)
            ]
            values["events"] = [
                _get_fake_event_values(lease_id=values["id"])
                for _ in range(6)
            ]
            db_api.lease_create(values)

        self._count(
            ("lease_list 200 leases x 4 res x 6 events.\n"
             +"Should be 200 + 800 + 1200 = 2200 rows\n"),
            lambda: [
                (lease.reservations, lease.events)
                for lease in db_api.lease_list()
            ],
        )

        self._count(
            ("lease_list 200 leases x 4 res x 6 events, with limit=50\n"
             +"Should be 50 + 200 + 300 = 550 rows\n"),
            lambda: [
                (lease.reservations, lease.events)
                for lease in db_api.lease_list(limit=50)
            ],
        )
