import sys
from oslo_config import cfg
from blazar.db import api as db_api
from datetime import datetime

CONF = cfg.CONF


def list_leases(args):
    print("start,end,created_at,deleted_at,hours_before_start,number_of_hosts,numer_of_networks,id,user_id,project_id,host_id,hypervisor_hostname,node_name,node_type")
    since_datetime = None
    if args.since:
        since_datetime = datetime.strptime(args.since, "%Y-%m-%d %H:%M")
    hosts_by_id = {}
    for lease in db_api.lease_list(
        args.project_id,
        status=None,
        lease_id=None,
        lease_name=None,
        marker=None,
        limit=None,
        sort_dir="desc",
        sort_key="end_date"
    ):
        try:
            if since_datetime and lease["start_date"] < since_datetime:
                continue

            created_at = datetime.strptime(lease["created_at"], "%Y-%m-%d %H:%M:%S")
            td = max(0, (lease["start_date"] - created_at).total_seconds())/3600
            all_allocations = []
            for allocations in [
                db_api.host_allocation_get_all_by_values(reservation_id=x['id'])
                for x in lease["reservations"]
                if x["resource_type"] in ["physical:host", "flavor:instance"]
            ]:
                all_allocations.extend(
                    hosts_by_id.setdefault(
                        a["compute_host_id"],
                        db_api.host_get(a["compute_host_id"])
                    )
                    for a in allocations
                )
            for host in all_allocations:
                if not host.get("extras"):
                    host["extras"] = {
                        k: v.capability_value
                        for v, k in db_api.host_extra_capability_get_all_per_host(host["id"])
                    }
                print(
                    lease["start_date"],
                    lease["end_date"],
                    lease["created_at"],
                    lease["deleted_at"],
                    td,
                    len(db_api.hosts_in_lease(lease["id"])),
                    len(db_api.networks_in_lease(lease["id"])),
                    lease["id"],
                    lease["user_id"],
                    lease["project_id"],
                    host["id"],
                    host["hypervisor_hostname"],
                    host["extras"]["node_name"],
                    host["extras"]["node_type"],
                    sep=",",
                )
        except Exception as e:
            print(f"Error processing lease {lease['id']}: {e}", file=sys.stderr)
            continue


def add_command_parsers(subparsers):
    parser = subparsers.add_parser('list_leases')
    parser.add_argument(
        '--since',
        type=str,
        help='Start date for filtering leases (YYYY-MM-DD HH:MM format)'
    )
    parser.add_argument(
        '--project-id',
        type=str,
        default=None,
        help='Project ID to filter by'
    )
    parser.set_defaults(func=list_leases)


command_opts = [
    cfg.SubCommandOpt('command',
                      title='Command',
                      help='Available commands',
                      handler=add_command_parsers)
]
CONF.register_cli_opts(command_opts)


def main():
    CONF(project='blazar', prog='blazar-manager')
    CONF.set_override("include_deleted", True)

    if hasattr(CONF.command, 'func'):
        CONF.command.func(CONF.command)
    else:
        print("No valid command specified.")
        CONF.print_help()


if __name__ == "__main__":
    main()
