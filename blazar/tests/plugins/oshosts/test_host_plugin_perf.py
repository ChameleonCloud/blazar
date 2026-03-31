"""Performance regression tests for host plugin DB queries.

These tests seed a real (SQLite) database and assert on query counts
to catch N+1 regressions in list_computehosts and list_allocations.
"""

import datetime
import uuid

from sqlalchemy import event

from blazar.db import api as db_api
from blazar.db.sqlalchemy import api as sql_api
from blazar.db.sqlalchemy import facade_wrapper
from blazar.db.sqlalchemy import utils as db_utils
from blazar import tests

NUM_HOSTS = 50
EXTRAS_PER_HOST = 3
NUM_LEASES = 20


class QueryCounter:
    def __init__(self, engine):
        self.engine = engine
        self.count = 0

    def __enter__(self):
        self.count = 0
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, conn, cursor, statement, parameters, context,
                    executemany):
        self.count += 1


class TestHostPluginQueryCount(tests.DBTestCase):

    def setUp(self):
        super().setUp()
        self.engine = facade_wrapper.get_engine()
        self.counter = QueryCounter(self.engine)
        self._seed()

    def _seed(self):
        self.host_ids = []
        for i in range(NUM_HOSTS):
            hid = str(uuid.uuid4())
            sql_api.host_create({
                'id': hid,
                'vcpus': 64,
                'cpu_info': '{}',
                'hypervisor_type': 'QEMU',
                'hypervisor_version': 6001000,
                'hypervisor_hostname': f'host-{i:04d}',
                'service_name': f'host-{i:04d}',
                'memory_mb': 256000,
                'local_gb': 1000,
                'status': 'free',
                'availability_zone': 'nova',
                'trust_id': str(uuid.uuid4()),
            })
            self.host_ids.append(hid)

            for j in range(EXTRAS_PER_HOST):
                sql_api.host_extra_capability_create({
                    'computehost_id': hid,
                    'property_name': f'cap_{j}',
                    'capability_value': f'val_{j}',
                })

        now = datetime.datetime.utcnow()
        self.reservation_ids = []
        for i in range(NUM_LEASES):
            lid = str(uuid.uuid4())
            sql_api.lease_create({
                'id': lid,
                'name': f'lease-{i:04d}',
                'user_id': str(uuid.uuid4()),
                'project_id': str(uuid.uuid4()),
                'start_date': now + datetime.timedelta(hours=i),
                'end_date': now + datetime.timedelta(hours=i + 4),
                'trust_id': str(uuid.uuid4()),
                'status': 'active',
            })

            rid = str(uuid.uuid4())
            sql_api.reservation_create({
                'id': rid,
                'lease_id': lid,
                'resource_type': 'physical:host',
                'status': 'active',
            })
            self.reservation_ids.append(rid)

        for i, hid in enumerate(self.host_ids):
            rid = self.reservation_ids[i % NUM_LEASES]
            sql_api.host_allocation_create({
                'compute_host_id': hid,
                'reservation_id': rid,
            })

    def test_list_hosts_query_count(self):
        """list_computehosts should not scale queries with host count."""
        with self.counter:
            raw_host_list = db_api.host_list()
            for host in raw_host_list:
                db_api.host_get(host['id'])
                db_api.host_extra_capability_get_all_per_host(host['id'])

        # Current behavior: 1 + 2*N queries (N+1 pattern)
        # After fix this should be a small constant.
        # For now, record the status quo so we can see the improvement.
        n_plus_1_count = 1 + 2 * NUM_HOSTS
        self.assertGreater(self.counter.count, 0)
        # TODO(perf): after fixing N+1, change this to:
        # self.assertLess(self.counter.count, 10)
        self.assertLessEqual(self.counter.count, n_plus_1_count * 2,
                             "Query count exploded beyond expected N+1")

    def test_list_allocations_query_count(self):
        """list_allocations DB queries should be constant, not per-host."""
        with self.counter:
            start = datetime.datetime.utcnow()
            end = datetime.date.max
            db_utils.get_reservation_allocations_by_host_ids(
                self.host_ids, start, end, None, None)

        # Should be a small constant (currently 4: host list + reservations
        # query + allocations query + user_ids query)
        self.assertLess(self.counter.count, 10,
                        f"Allocation queries should be constant, "
                        f"got {self.counter.count}")
