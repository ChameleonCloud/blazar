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
import collections
import concurrent.futures
import datetime
import json
import random

from novaclient import exceptions as nova_exceptions
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import strutils

from blazar import context, status
from blazar.db import api as db_api
from blazar.db import utils as db_utils
from blazar.manager import exceptions as mgr_exceptions
from blazar.plugins import base
from blazar.plugins import flavor as plugin
from blazar.plugins import instances as instances_plugin
from blazar.plugins import oshosts as oshosts_plugin
from blazar.plugins.instances import instance_plugin
from blazar.plugins.oshosts import host_plugin
from blazar.utils.openstack import nova
from blazar.utils import plugins as plugins_utils
from blazar.utils.openstack import placement

plugin_opts = [
    cfg.StrOpt('before_end',
               default='',
               help='Actions which we will be taken before the end of '
                    'the lease'),
    cfg.BoolOpt('randomize_host_selection',
            default=False,
            help='Allocate hosts for reservations randomly.'),
    cfg.BoolOpt('filter_ironic_hosts',
            default=True,
            help='Filter out ironic (baremetal) hosts from flavor '
                 'reservation candidates.'),
]

CONF = cfg.CONF
CONF.register_opts(plugin_opts, group=plugin.RESOURCE_TYPE)

LOG = logging.getLogger(__name__)


before_end_options = ['', 'snapshot', 'default', 'email']

QUERY_TYPE_ALLOCATION = 'allocation'


class FlavorPlugin(base.BasePlugin):
    """Plugin for Nova flavor-based server reservations."""

    resource_type = plugin.RESOURCE_TYPE
    title = 'Plugin for Nova flavor-based server reservations'
    description = 'Reserve compute resources modeled by Nova flavors.'

    query_options = {
        QUERY_TYPE_ALLOCATION: ['lease_id', 'reservation_id']
    }

    def __init__(self):
        super().__init__()
        self._placement_client = placement.BlazarPlacementClient()
        self._host_plugin = host_plugin.PhysicalHostPlugin()
        self._instance_plugin = instance_plugin.VirtualInstancePlugin()

    def get(self, host_id):
        return self._host_plugin.get(host_id)

    def list_allocations(self, query):
        return self._host_plugin.list_allocations(query)

    def query_allocations(self, hosts, lease_id=None, reservation_id=None):
        return self._host_plugin.query_allocations(
            hosts, lease_id, reservation_id)

    def allocation_candidates(self, reservation):
        """Return a list of candidate host_ids."""
        host_ids, _ = self._pick_hosts(reservation)
        return host_ids

    def _pick_hosts(self, reservation):
        self._validate_reservation_params(reservation)

        flavor_id = reservation['flavor_id']
        resource_request, resource_traits, source_flavor = \
            self._get_flavor_details(flavor_id)

        affinity = strutils.bool_from_string(
            reservation['affinity'], default=None)
        start_date = reservation['start_date']
        end_date = reservation['end_date']
        candidates = self._query_available_hosts(
            start_date, end_date, resource_request, resource_traits, reservation['project_id'])

        # Fail if we have fewer candidates than amount requested
        req_amount = reservation['amount']
        if len(candidates) < req_amount:
            raise mgr_exceptions.NotEnoughHostsAvailable()

        if affinity is not None:
            raise mgr_exceptions.NotImplemented(
                error="Affinity not supported yet")

        if CONF[self.resource_type].randomize_host_selection:
            random.shuffle(candidates)

        # return just enough hosts to satisfy the request
        while len(candidates) > req_amount:
            candidates.pop()
        host_ids = [host['id'] for host in candidates]
        return host_ids, source_flavor

    def _validate_reservation_params(self, values):
        marshall_attributes = set([
            'amount', 'flavor_id', 'affinity', 'start_date', 'end_date',
        ])
        missing_attr = marshall_attributes - set(values.keys())
        if missing_attr:
            raise mgr_exceptions.MissingParameter(param=','.join(missing_attr))

        try:
            values['amount'] = strutils.validate_integer(
                values['amount'], "amount", 1, db_api.DB_MAX_INT)
        except ValueError as e:
            raise mgr_exceptions.MalformedParameter(str(e))

    def _host_passes(self, host, project_id):
        full_host = self._host_plugin.get_computehost(host["id"])
        return (
            self.is_project_allowed(project_id, full_host) and
            (
                not CONF[self.resource_type].filter_ironic_hosts or
                full_host.get('hypervisor_type') != 'ironic'
            )
        )

    def _get_eligible_hosts(self, resource_traits, project_id):
        hosts = db_api.reservable_host_get_all_by_queries([])

        hosts = [
            host for host in hosts if self._host_passes(host, project_id)
        ]

        placement_rps_matching_traits = None
        # Only query placement if we have traits to match
        if resource_traits:
            traits_list = []
            for trait, value in resource_traits.items():
                # We've already validated resource_traits at this point
                if value == "required":
                    traits_list.append(trait)
                elif value == "forbidden":
                    # prefix forbidden traits with `!`
                    traits_list.append(f"!{trait}")
            required_string = ",".join(traits_list)
            placement_rps_matching_traits = \
                self._placement_client.list_resource_providers(
                    query=f"required={required_string}",
                    microversion="1.22",
                )

        placment_rps_matching_traits_hostnames = {
            rp['name'] for rp in placement_rps_matching_traits
        } if placement_rps_matching_traits else set()

        eligible_hosts = []
        for host in hosts:
            hypervisor_hostname = host['hypervisor_hostname']
            if (
                resource_traits and
                hypervisor_hostname not in placment_rps_matching_traits_hostnames
            ):
                LOG.debug(
                    "Placement filtered out host %s based on traits",
                    hypervisor_hostname
                )
                continue
            eligible_hosts.append(host)
        return eligible_hosts

    def _query_available_hosts(self, start_date, end_date,
                               resource_request, resource_traits, project_id, excludes=[]):
        # TODO(johngarbutt): offload more of this to the db
        # we should be able to exclude hosts that don't match the
        # resource requests, e.g. baremetal vs virtual
        # or missing traits
        hosts = self._get_eligible_hosts(resource_traits, project_id)

        # find reservations for each host in our time period
        free_hosts, reserved_hosts = \
            self._instance_plugin.filter_hosts_by_reservation(
                hosts,
                start_date - datetime.timedelta(minutes=CONF.cleaning_time),
                end_date + datetime.timedelta(minutes=CONF.cleaning_time),
                excludes)

        available_hosts = []
        for host_info in (reserved_hosts + free_hosts):
            # check how many instances can fit on this host
            hosts_list = self._get_hosts_list(host_info, resource_request, excludes)
            available_hosts.extend(hosts_list)
        return available_hosts

    def _get_hosts_list(self, host_info, resource_request, excludes=[]):
        """For given host, work out how many instances can fit on it."""

        # For each host, look how many slots are available,
        # given the current list of reservations within the
        # target time window for this host

        # get high water mark of usage during all reservations
        max_usage = self._max_usages(host_info["host"], host_info['reservations'])
        LOG.debug(f"Max usage {host_info['host']['hypervisor_hostname']} "
                  f"is {max_usage}")

        host = host_info['host']
        host_crs = db_api.host_resource_inventory_get_all_per_host(host['id'])
        host_inventory = {cr['resource_class']: cr for cr in host_crs}
        if not host_inventory:
            LOG.warning("host added before inventory set in DB!")
            return []
        LOG.debug(f"Inventory for {host_info['host']['hypervisor_hostname']} "
                  f"is {host_inventory}")

        # see how much room for slots we have
        hosts_list = []
        current_usage = max_usage.copy()

        def has_free_slot():
            for rc, requested in resource_request.items():
                if not requested:
                    # skip things like requests for 0 vcpus
                    continue

                host_details = host_inventory.get(rc)
                if not host_details:
                    # host doesn't have this sort of resource
                    LOG.debug(f"Resource {rc} not found for "
                              f"{host_info['host']['hypervisor_hostname']}")
                    return False
                usage = current_usage[rc]

                if requested > host_details["max_unit"]:
                    # requested more than the max allowed by this host
                    LOG.debug(f"Requested {requested} {rc} for "
                              f"{host_info['host']['hypervisor_hostname']} "
                              f"but maximum is {host_details['max_unit']}")
                    return False

                capacity = ((host_details["total"] - host_details["reserved"])
                            * host_details["allocation_ratio"])
                LOG.debug(f"Capacity is {capacity} for {rc} for "
                          f"{host_info['host']['hypervisor_hostname']}")
                if (usage + requested) > capacity:
                    LOG.debug("Current usage is %d, requested %d",
                              usage, requested)
                    return False

            # We have enough resources for all resource requests
            return True

        while (has_free_slot()):
            hosts_list.append(host)
            for rc, requested in resource_request.items():
                current_usage[rc] += requested

        LOG.debug(f"For host {host_info['host']['hypervisor_hostname']} "
                  f"we have {len(hosts_list)} slots.")
        return hosts_list

    def _get_cached_flavor(self, instance_reservation):
        source_flavor = instance_reservation["resource_properties"]
        if source_flavor and "OS-FLV-EXT-DATA:ephemeral" in source_flavor:
            return json.loads(source_flavor)

    def _max_usages(self, host, reservations):
        """For reservation list for a host, find resource high watermark."""
        def resource_usage_by_event(event):
            instance_reservation = event['reservation']['instance_reservation']
            # Fetch how many instances of this reservation are on this host speifically
            request_count = len([
                c for c in event['reservation'].computehost_allocations
                if c.compute_host_id == host['id']
            ])
            source_flavor = self._get_cached_flavor(instance_reservation)
            if source_flavor:
                flavor_resource_inventory, _ = \
                    self._estimate_flavor_resources(source_flavor)
                return {
                    rc: amount * request_count
                    for rc, amount in flavor_resource_inventory.items()
                    if amount > 0
                }
            raise mgr_exceptions.ReservationTypeConflict()

        # Get sorted list of events for all reservations
        # that exist in the target time window
        events_list = []
        for r in reservations:
            fetched_events = db_api.event_get_all_sorted_by_filters(
                sort_key='time', sort_dir='asc',
                filters={'lease_id': r['lease_id']})
            events_list.extend([{'event': e, 'reservation': r}
                                for e in fetched_events])
        events_list.sort(key=lambda x: x['event']['time'])

        current_usage = collections.defaultdict(int)
        max_usage = collections.defaultdict(int)
        for event in events_list:
            usage = resource_usage_by_event(event)

            if event['event']['event_type'] == 'start_lease':
                LOG.debug(f"found start{event} with {usage}")
                for rc, usage_amount in usage.items():
                    current_usage[rc] += usage_amount
                    # TODO(johngarbutt) what if the max usage is
                    # actually outside the target time window?
                    if max_usage[rc] < current_usage[rc]:
                        max_usage[rc] = current_usage[rc]

            elif event['event']['event_type'] == 'end_lease':
                for rc, usage_amount in usage.items():
                    current_usage[rc] -= usage_amount

            LOG.debug(f"after {event}\nusage is: {current_usage}\n"
                      f"max is: {max_usage}")
        return max_usage

    def _get_flavor_details(self, flavor_id):
        # access nova using the user token,
        # to ensure we can only see flavors they can see
        user_client = nova.NovaClientWrapper()
        flavor = user_client.nova.nova.flavors.get(flavor_id)
        source_flavor = flavor.to_dict()
        # TODO(johngarbutt): use newer api to get this above
        source_flavor["extra_specs"] = flavor.get_keys()
        if 'links' in source_flavor.keys():
            del source_flavor['links']

        # NOTE(johngarbutt): we are only partially reproducing all the
        # options that are available in a flavor.
        resource_request, resource_traits = \
            self._estimate_flavor_resources(source_flavor)

        return (resource_request, resource_traits, source_flavor)

    def _estimate_flavor_resources(self, source_flavor):
        resource_request = {}
        resource_traits = {}

        # add default resource requests
        resource_request["VCPU"] = int(source_flavor['vcpus'])
        resource_request["MEMORY_MB"] = int(source_flavor['ram'])
        # NOTE(priteau): This reserves resources for the root disk even if the
        # instance will boot from volume.
        resource_request["DISK_GB"] = (
            int(source_flavor['disk']) +
            int(source_flavor['OS-FLV-EXT-DATA:ephemeral']))

        # Check for PCPUs
        hw_cpu_policy = source_flavor['extra_specs'].get("hw:cpu_policy")
        if hw_cpu_policy == "dedicated":
            resource_request["PCPU"] = source_flavor['vcpus']
            resource_request["VCPU"] = 0

        # Check for traits and extra resources
        for key, value in source_flavor['extra_specs'].items():
            if key.startswith("trait:"):
                trait = key.split(":")[1]
                if value == "required":
                    resource_traits[trait] = "required"
                elif value == "forbidden":
                    resource_traits[trait] = "forbidden"
                else:
                    raise mgr_exceptions.MalformedParameter(
                        "Invalid value for trait %s" % trait)

            if key.startswith("resources:"):
                rc = key.split(":")[1]
                resource_request[rc] = int(value)

        # TODO(johngarbutt): look for other extra specs that we
        # don't support and error out the reservation if we find any

        return (resource_request, resource_traits)

    def reserve_resource(self, reservation_id, values):
        host_ids, source_flavor = self._pick_hosts(values)

        if 'before_end' not in values:
            values['before_end'] = 'default'
        if values['before_end'] not in before_end_options:
            raise mgr_exceptions.MalformedParameter(param='before_end')

        instance_reservation_val = {
            'reservation_id': reservation_id,
            # use flavor display values,
            # even though we probably reserve different resources
            'vcpus': source_flavor["vcpus"],
            'memory_mb': source_flavor["ram"],
            'disk_gb': source_flavor["disk"],
            'amount': values['amount'],
            'affinity': None,
            'resource_properties': json.dumps(source_flavor),
            'before_end': values['before_end'],
        }
        instance_reservation = db_api.instance_reservation_create(
            instance_reservation_val)

        for host_id in host_ids:
            db_api.host_allocation_create({'compute_host_id': host_id,
                                          'reservation_id': reservation_id})

        try:
            flavor_id, aggregate_id = \
                self._create_resources(instance_reservation)
        except nova_exceptions.ClientException:
            LOG.exception("Failed to create Nova resources "
                          "for reservation %s", reservation_id)
            self._cleanup_resources(instance_reservation)
            raise mgr_exceptions.NovaClientError()

        db_api.instance_reservation_update(instance_reservation['id'],
                                           {'flavor_id': flavor_id,
                                            'aggregate_id': aggregate_id})

        return instance_reservation['id']

    def _create_resources(self, instance_reservation):
        reservation_id = instance_reservation['reservation_id']
        # TODO(johngarbutt) we ignore affinity for now
        # user_client = nova.NovaClientWrapper()
        # reserved_group = user_client.nova.server_groups.create(
        #    instance_plugin.RESERVATION_PREFIX + ':' + reservation_id,
        #    'affinity' if inst_reservation['affinity'] else 'anti-affinity'
        #    )

        reserved_flavor = self._create_flavor(instance_reservation)

        ctx = context.current()
        pool = nova.ReservationPool()
        pool_metadata = {
            instance_plugin.RESERVATION_PREFIX: reservation_id,
            # this is added to work with Nova configuration options
            # [scheduler]limit_tenants_to_placement_aggregate=True and
            # [scheduler]placement_aggregate_required_for_tenants=True
            'filter_tenant_id': ctx.project_id,
        }
        agg = pool.create(name=reservation_id, metadata=pool_metadata, project_id=ctx.project_id)

        # TODO(johngarbutt) maybe add inventory here, but mark
        # then inventory as reserved to start with?
        self._placement_client.create_reservation_class(reservation_id)

        return reserved_flavor.id, agg.id

    def _create_flavor(self, instance_reservation):
        source_flavor = self._get_cached_flavor(instance_reservation)
        if not source_flavor:
            raise mgr_exceptions.ReservationTypeConflict()

        res = db_api.reservation_get(instance_reservation['reservation_id'])
        lease = db_api.lease_get(res["lease_id"])
        reservation_id = instance_reservation['reservation_id']
        flavor_details = {
            'flavorid': reservation_id,
            'name': instance_plugin.RESERVATION_PREFIX + ":" + reservation_id,
            'vcpus': source_flavor['vcpus'],
            'ram': source_flavor['ram'],
            'disk': source_flavor['disk'],
            'is_public': False,
            'description': f'{lease["name"]} (ID: {lease["id"]})',
        }
        # create flavor using admin access
        reserved_flavor = self._instance_plugin.nova.nova.flavors.create(
            **flavor_details)

        # Set extra specs to the flavor
        rsv_id_rc_format = reservation_id.upper().replace("-", "_")
        reservation_rc = "resources:CUSTOM_RESERVATION_" + rsv_id_rc_format
        extra_specs = source_flavor["extra_specs"].copy()
        extra_specs[instance_plugin.FLAVOR_EXTRA_SPEC] = reservation_id
        extra_specs[reservation_rc] = "1"
        reserved_flavor.set_keys(extra_specs)

        return reserved_flavor

    def _cleanup_resources(self, instance_reservation):
        self._instance_plugin.cleanup_resources(instance_reservation)

    def update_reservation(self, reservation_id, values):
        """Only supports updating lease start and end date.
        """
        reservation = db_api.reservation_get(reservation_id)
        instance_reservation = db_api.instance_reservation_get(
            reservation['resource_id'])

        if ('flavor_id' in values and values.get('flavor_id') != instance_reservation['flavor_id']):
            raise mgr_exceptions.CantUpdateParameter(param="flavor_id")
        if ('amount' in values and values.get('amount') != instance_reservation['amount']):
            raise mgr_exceptions.CantUpdateParameter(param="amount")

        flavor_id = instance_reservation['flavor_id']
        resource_request, resource_traits, _ = self._get_flavor_details(flavor_id)

        # The flavor has this reservation as a resource for it. This
        # information isn't in the host inventory DB, and so we need
        # to remove it in order to query available hosts, or else none
        # match our request.
        rsv_id_rc_format = reservation_id.upper().replace("-", "_")
        reservation_rc = "CUSTOM_RESERVATION_" + rsv_id_rc_format
        if reservation_rc in resource_request:
            resource_request.pop(reservation_rc)

        existing_allocations = db_api.host_allocation_get_all_by_values(reservation_id=reservation_id)
        lease = db_api.lease_get(reservation['lease_id'])
        candidates = self._query_available_hosts(
            values['start_date'],
            values['end_date'],
            resource_request, resource_traits,
            lease['project_id'],
            [reservation_id]
        )

        # Ensure for every allocation in this reservation, there is a candidate
        alloc_count = collections.Counter([
            alloc["compute_host_id"] for alloc in existing_allocations
        ])
        candidate_count = collections.Counter([
            can["id"] for can in candidates
        ])
        # TODO We could support changing "amount" pretty easy here.
        if not all(alloc_count[key] <= candidate_count.get(key, 0) for key in alloc_count):
            raise mgr_exceptions.NotEnoughHostsAvailable()

    def on_start(self, resource_id, lease=None):
        self._instance_plugin.on_start(resource_id, lease)

    def on_end(self, resource_id, lease=None):
        self._instance_plugin.on_end(resource_id, lease)

    def before_end(self, resource_id, lease=None):
        """Take an action before the end of a lease."""
        instance_reservation = db_api.instance_reservation_get(resource_id)

        action = instance_reservation['before_end']
        if action == 'default':
            action = CONF[plugin.RESOURCE_TYPE].before_end

        if action == 'email':
            plugins_utils.send_lease_extension_reminder(
                lease, CONF.os_region_name)

    def compute_all_availability(self, start_date, end_date, project_id):
        """Return compute_availability results for every public Nova flavor."""
        user_client = nova.NovaClientWrapper()
        flavors = user_client.nova.nova.flavors.list()
        results = []
        for flavor in flavors:
            try:
                results.append(self.compute_availability(
                    flavor.id, start_date, end_date, project_id))
            except Exception:
                LOG.warning("Skipping flavor %s during availability scan",
                            flavor.id, exc_info=True)
        return results

    def compute_availability(self, flavor_id, start_date, end_date,
                             project_id):
        """Return a change-point timeline of available slot counts for flavor_id.

        Returns a dict with keys:
          'flavor_id': the Nova flavor UUID
          'resource_spec': basic flavor dimensions
          'availability': list of {'start', 'end', 'available', 'total'}
                          segments covering [start_date, end_date] with no gaps.

        Uses the same resource-accounting logic as _query_available_hosts /
        _max_usages: tracks per-host per-resource-class usage so that
        reservations with different resource footprints are handled correctly.
        Cleaning time is applied to match the window expansion done by
        _query_available_hosts.
        """
        resource_request, resource_traits, source_flavor = \
            self._get_flavor_details(flavor_id)

        eligible_hosts = self._get_eligible_hosts(resource_traits, project_id)

        # Per-host: inventory (for resource-class capacity) and max slot count
        # (for capping available() output).  Mirrors the data _get_hosts_list
        # uses so that slots_for_host() gives results identical to it.
        host_inventory = {}   # host_id -> {rc: inventory_row}
        host_max_slots = {}   # host_id -> max slots when the host is empty
        for host in eligible_hosts:
            host_crs = db_api.host_resource_inventory_get_all_per_host(
                host['id'])
            inv = {cr['resource_class']: cr for cr in host_crs}
            if not inv:
                continue
            slots = len(self._get_hosts_list(
                {'host': host, 'reservations': []}, resource_request))
            if not slots:
                continue
            host_inventory[host['id']] = inv
            host_max_slots[host['id']] = slots

        total_slots = sum(host_max_slots.values())
        host_ids = list(host_max_slots.keys())

        # Expand query window by cleaning_time on both sides, matching
        # _query_available_hosts which passes padded dates to
        # filter_hosts_by_reservation.
        cleaning = datetime.timedelta(minutes=CONF.cleaning_time)
        all_reservations = db_utils.get_reservations_by_host_ids(
            host_ids, start_date - cleaning, end_date + cleaning)

        instance_types = frozenset([
            instances_plugin.RESOURCE_TYPE, plugin.RESOURCE_TYPE])

        # Build a time-bucketed event map.
        # Instance/flavor reservations: track per-(host, rc) resource deltas
        #   so that slots_for_host() can compute remaining capacity correctly
        #   regardless of whether the existing reservations use the same flavor.
        # Physical:host reservations: set a block flag that zeroes the host.
        # Cleaning time extends the "occupied" period past lease.end_date.
        event_map = collections.defaultdict(list)
        for reservation in all_reservations:
            lease = reservation.lease
            blk_start = max(lease.start_date, start_date)
            blk_end = min(lease.end_date + cleaning, end_date)
            if blk_start >= blk_end:
                continue

            if reservation.resource_type in instance_types:
                ir = reservation.instance_reservation
                cached = self._get_cached_flavor(ir)
                if cached:
                    slot_resources, _ = self._estimate_flavor_resources(cached)
                else:
                    slot_resources = {
                        'VCPU': ir.vcpus,
                        'MEMORY_MB': ir.memory_mb,
                        'DISK_GB': ir.disk_gb,
                    }
                slots_by_host = collections.Counter(
                    a.compute_host_id
                    for a in reservation.computehost_allocations
                    if a.deleted is None
                    and a.compute_host_id in host_max_slots
                )
                for h_id, slot_count in slots_by_host.items():
                    rc_delta = {
                        rc: slot_count * amt
                        for rc, amt in slot_resources.items()
                        if amt > 0
                    }
                    event_map[blk_start].append((h_id, rc_delta, 0))
                    event_map[blk_end].append(
                        (h_id, {rc: -v for rc, v in rc_delta.items()}, 0))

            elif reservation.resource_type == oshosts_plugin.RESOURCE_TYPE:
                for alloc in reservation.computehost_allocations:
                    if (alloc.deleted is None
                            and alloc.compute_host_id in host_max_slots):
                        h = alloc.compute_host_id
                        event_map[blk_start].append((h, {}, +1))
                        event_map[blk_end].append((h, {}, -1))

        host_rc_used = collections.defaultdict(
            lambda: collections.defaultdict(int))
        host_block_count = collections.defaultdict(int)

        def slots_for_host(h_id):
            """Available slots for h_id given current resource usage."""
            if host_block_count[h_id] > 0:
                return 0
            inv = host_inventory[h_id]
            min_slots = host_max_slots[h_id]
            for rc, requested in resource_request.items():
                if not requested:
                    continue
                host_inv = inv.get(rc)
                if not host_inv:
                    return 0
                capacity = ((host_inv['total'] - host_inv['reserved'])
                            * host_inv['allocation_ratio'])
                used = host_rc_used[h_id][rc]
                slots = int(max(0.0, capacity - used) / requested)
                min_slots = min(min_slots, slots)
            return max(0, min_slots)

        def available_now():
            return sum(slots_for_host(h_id) for h_id in host_max_slots)

        segments = []
        prev_time = start_date

        for time in sorted(event_map.keys()):
            if time >= end_date:
                break
            if time > prev_time:
                segments.append({
                    'start': prev_time,
                    'end': time,
                    'available': available_now(),
                    'total': total_slots,
                })
                prev_time = time
            for h_id, rc_delta, delta_block in event_map[time]:
                for rc, delta in rc_delta.items():
                    host_rc_used[h_id][rc] += delta
                host_block_count[h_id] += delta_block

        segments.append({
            'start': prev_time,
            'end': end_date,
            'available': available_now(),
            'total': total_slots,
        })

        # Merge adjacent segments with the same available count
        merged = []
        for seg in segments:
            if merged and merged[-1]['available'] == seg['available']:
                merged[-1]['end'] = seg['end']
            else:
                merged.append(dict(seg))

        return {
            'flavor_id': flavor_id,
            'resource_spec': {
                'vcpus': source_flavor.get('vcpus'),
                'memory_mb': source_flavor.get('ram'),
                'disk_gb': source_flavor.get('disk'),
            },
            'availability': merged,
        }
