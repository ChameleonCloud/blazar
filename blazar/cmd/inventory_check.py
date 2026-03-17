# Copyright (c) 2024 University of Chicago
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import sys
from oslo_config import cfg
from blazar.db import api as db_api
from blazar.utils.openstack import placement
# Import keystone to register required config options like os_region_name
from blazar.utils.openstack import keystone

try:
    import openstack
except ImportError:
    openstack = None

CONF = cfg.CONF


class PlacementClientWrapper:
    """Wrapper to inject endpoint_override or cloud session into placement calls."""
    
    def __init__(self, os_cloud=None):
        self.os_cloud = os_cloud
        self.client = placement.BlazarPlacementClient()
        self._session = None
        
        if self.os_cloud:
            if not openstack:
                raise ImportError("openstacksdk is required for --os-cloud support")
            conn = openstack.connect(cloud=self.os_cloud)
            self._session = conn.session

    def _patch_client(self):
        """Patch the internal placement client with custom session/endpoint."""
        original_method = self.client._create_client
        
        def _create_client_with_override(**kwargs):
            # If we have a cloud session, we use it to create the adapter
            if self._session:
                from keystoneauth1 import adapter
                # Replicate some of the logic from BlazarPlacementClient._create_client
                headers = {'accept': 'application/json'}
                kwargs.setdefault('service_type', 'placement')
                kwargs.setdefault('interface', CONF.placement.endpoint_type)
                kwargs.setdefault('additional_headers', headers)
                return adapter.Adapter(self._session, **kwargs)
                
            return original_method(**kwargs)
        
        self.client._create_client = _create_client_with_override
        return original_method

    def get_resource_provider(self, rp_name):
        original_method = self._patch_client()
        try:
            return self.client.get_resource_provider(rp_name)
        finally:
            self.client._create_client = original_method
    
    def get_inventory(self, rp_uuid):
        original_method = self._patch_client()
        try:
            return self.client.get_inventory(rp_uuid)
        finally:
            self.client._create_client = original_method


def check_inventory(args):
    """Check that resource inventory in DB matches Placement."""
    # Override database connection if provided
    if hasattr(args, 'database_connection') and args.database_connection:
        CONF.set_override('connection', args.database_connection, 
                         group='database')
        print(f"Using custom database: {args.database_connection}")
    
    # Create placement client with custom settings if provided
    os_cloud = getattr(args, 'os_cloud', None)
    
    if os_cloud:
        print(f"Using cloud for Placement: {os_cloud}")
    
    placement_client = PlacementClientWrapper(
        os_cloud=os_cloud
    )
    
    # Get all hosts from the database
    hosts = db_api.host_get_all_by_queries([])
    
    print("Checking resource inventory for all hosts...")
    print("=" * 80)
    
    mismatches = []
    errors = []
    
    for host in hosts:
        host_id = host['id']
        hostname = host['hypervisor_hostname']
        
        print(f"\nHost: {hostname} (ID: {host_id})")
        print("-" * 80)
        
        try:
            # Get resource provider from Placement
            rp = placement_client.get_resource_provider(hostname)
            if rp is None:
                error_msg = f"  ERROR: Resource provider not found in Placement"
                print(error_msg)
                errors.append({
                    'host_id': host_id,
                    'hostname': hostname,
                    'error': 'Resource provider not found in Placement'
                })
                continue
            
            # Get inventory from Placement
            placement_inventories = placement_client.get_inventory(rp['uuid'])
            placement_inv_dict = placement_inventories.get('inventories', {})
            
            # Get inventory from database
            db_inventories = db_api.host_resource_inventory_get_all_per_host(host_id)
            
            # Convert DB inventories to dict keyed by resource_class
            db_inv_dict = {inv['resource_class']: inv for inv in db_inventories}
            
            # Check for mismatches
            all_resource_classes = set(placement_inv_dict.keys()) | set(db_inv_dict.keys())
            
            host_has_mismatch = False
            
            for rc in sorted(all_resource_classes):
                placement_inv = placement_inv_dict.get(rc)
                db_inv = db_inv_dict.get(rc)
                
                if placement_inv is None:
                    mismatch_msg = f"  MISMATCH: {rc} exists in DB but not in Placement"
                    print(mismatch_msg)
                    mismatches.append({
                        'host_id': host_id,
                        'hostname': hostname,
                        'resource_class': rc,
                        'issue': 'exists in DB but not in Placement',
                        'db_inventory': db_inv,
                        'placement_inventory': None
                    })
                    host_has_mismatch = True
                elif db_inv is None:
                    mismatch_msg = f"  MISMATCH: {rc} exists in Placement but not in DB"
                    print(mismatch_msg)
                    mismatches.append({
                        'host_id': host_id,
                        'hostname': hostname,
                        'resource_class': rc,
                        'issue': 'exists in Placement but not in DB',
                        'db_inventory': None,
                        'placement_inventory': placement_inv
                    })
                    host_has_mismatch = True
                else:
                    # Compare inventory values
                    fields_to_check = ['total', 'reserved', 'min_unit', 'max_unit', 
                                       'step_size', 'allocation_ratio']
                    
                    differences = []
                    for field in fields_to_check:
                        db_value = db_inv.get(field)
                        placement_value = placement_inv.get(field)
                        
                        # Handle float comparison for allocation_ratio
                        if field == 'allocation_ratio':
                            if abs(float(db_value) - float(placement_value)) > 0.001:
                                differences.append(
                                    f"{field}: DB={db_value}, Placement={placement_value}"
                                )
                        else:
                            if db_value != placement_value:
                                differences.append(
                                    f"{field}: DB={db_value}, Placement={placement_value}"
                                )
                    
                    if differences:
                        mismatch_msg = f"  MISMATCH: {rc} has different values:"
                        print(mismatch_msg)
                        for diff in differences:
                            print(f"    - {diff}")
                        mismatches.append({
                            'host_id': host_id,
                            'hostname': hostname,
                            'resource_class': rc,
                            'issue': 'different values',
                            'differences': differences,
                            'db_inventory': db_inv,
                            'placement_inventory': placement_inv
                        })
                        host_has_mismatch = True
                    else:
                        print(f"  OK: {rc}")
            
            if not host_has_mismatch:
                print("  All resource inventories match!")
                
        except Exception as e:
            error_msg = f"  ERROR: {str(e)}"
            print(error_msg)
            errors.append({
                'host_id': host_id,
                'hostname': hostname,
                'error': str(e)
            })
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total hosts checked: {len(hosts)}")
    print(f"Hosts with mismatches: {len(set(m['host_id'] for m in mismatches))}")
    print(f"Total mismatches found: {len(mismatches)}")
    print(f"Hosts with errors: {len(errors)}")
    
    if mismatches:
        print("\nMismatches found:")
        for mismatch in mismatches:
            print(f"  - {mismatch['hostname']}: {mismatch['resource_class']} - {mismatch['issue']}")
    
    if errors:
        print("\nErrors encountered:")
        for error in errors:
            print(f"  - {error['hostname']}: {error['error']}")
    
    # Return exit code
    if errors or mismatches:
        return 1
    else:
        print("\nAll hosts have matching resource inventories!")
        return 0


def add_command_parsers(subparsers):
    parser = subparsers.add_parser(
        'check_inventory',
        help='Check that resource inventory in DB matches Placement'
    )
    parser.add_argument(
        '--database-connection',
        type=str,
        help='Custom database connection string (overrides config file). '
             'Format: mysql+pymysql://user:pass@host:port/dbname'
    )
    parser.add_argument(
        '--placement-url',
        type=str,
        help='Custom Placement API endpoint URL (overrides service catalog lookup). '
             'Format: http://host:port'
    )
    parser.add_argument(
        '--os-cloud',
        type=str,
        help='Name of the cloud in clouds.yaml to use for Placement authentication.'
    )
    parser.set_defaults(func=check_inventory)


command_opts = [
    cfg.SubCommandOpt('command',
                      title='Command',
                      help='Available commands',
                      handler=add_command_parsers)
]
CONF.register_cli_opts(command_opts)


def main():
    CONF(project='blazar', prog='blazar-inventory-check')
    
    if hasattr(CONF.command, 'func'):
        exit_code = CONF.command.func(CONF.command)
        sys.exit(exit_code or 0)
    else:
        print("No valid command specified.")
        CONF.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
