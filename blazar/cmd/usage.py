import sys
from oslo_config import cfg
from blazar.db import api as db_api
from datetime import datetime

CONF = cfg.CONF


def list_leases(args):
    print("hours_before_start,number_of_hosts,numer_of_networks,id")
    for lease in db_api.lease_list():
        created_at = datetime.strptime(lease["created_at"], "%Y-%m-%d %H:%M:%S")
        td = max(0, (lease["start_date"] - created_at).total_seconds())/3600
        print(
            td,
            len(db_api.hosts_in_lease(lease["id"])),
            len(db_api.networks_in_lease(lease["id"])),
            lease["id"], 
            sep=",",
        )
    

def add_command_parsers(subparsers):
    parser = subparsers.add_parser('list_leases')
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
