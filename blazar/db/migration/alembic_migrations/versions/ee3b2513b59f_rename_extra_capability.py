# Copyright 2024 OpenStack Foundation.
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

"""rename extra capability to resource property

Revision ID: ee3b2513b59f
Revises: d5a379ff6ba3
Create Date: 2024-01-06 00:22:00.128435

"""

# revision identifiers, used by Alembic.
revision = 'ee3b2513b59f'
down_revision = '9eec70cbc562'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    # Drop existing foreign key constraints
    op.drop_constraint('computehost_extra_capability_id_fk',
                       'computehost_extra_capabilities',
                       type_='foreignkey')
    op.drop_constraint('networksegment_extra_capability_id_fk',
                       'networksegment_extra_capabilities',
                       type_='foreignkey')
    op.drop_constraint('device_extra_capabilities_ibfk_2',
                       'device_extra_capabilities',
                       type_='foreignkey')
    # Rename the ExtraCapability model to ResourceProperty
    op.rename_table('extra_capabilities', 'resource_properties')
    # Update the column names and types
    op.alter_column('resource_properties', 'capability_name',
                    new_column_name='property_name', existing_type=sa.String(255))
    op.alter_column('computehost_extra_capabilities', 'capability_id',
                    new_column_name='property_id', existing_type=sa.String(255))
    op.alter_column('networksegment_extra_capabilities', 'capability_id',
                    new_column_name='property_id', existing_type=sa.String(255))
    op.alter_column('device_extra_capabilities', 'capability_id',
                    new_column_name='property_id', existing_type=sa.String(255))
    # Recreate foreign key constraints
    op.create_foreign_key('computehost_extra_capability_id_fk',
                          'computehost_extra_capabilities',
                          'resource_properties', ['property_id'], ['id'])
    op.create_foreign_key('networksegment_extra_capability_id_fk',
                          'networksegment_extra_capabilities',
                          'resource_properties', ['property_id'], ['id'])
    op.create_foreign_key('device_extra_capabilities_ibfk_2',
                          'device_extra_capabilities',
                          'resource_properties', ['property_id'], ['id'])

def downgrade():
    # Drop foreign key constraints
    op.drop_constraint('computehost_extra_capability_id_fk',
                       'computehost_extra_capabilities',
                       type_='foreignkey')
    op.drop_constraint('networksegment_extra_capability_id_fk',
                       'networksegment_extra_capabilities',
                       type_='foreignkey')
    op.drop_constraint('device_extra_capabilities_ibfk_2',
                       'device_extra_capabilities',
                       type_='foreignkey')

    # Rename the ResourceProperty model back to ExtraCapability
    op.rename_table('resource_properties', 'extra_capabilities')

    # Update the column names and types
    op.alter_column('extra_capabilities', 'property_name',
                    new_column_name='capability_name', existing_type=sa.String(255))
    op.alter_column('computehost_extra_capabilities', 'property_id',
                    new_column_name='capability_id', existing_type=sa.String(255))
    op.alter_column('networksegment_extra_capabilities', 'property_id',
                    new_column_name='capability_id', existing_type=sa.String(255))
    op.alter_column('device_extra_capabilities', 'property_id',
                    new_column_name='capability_id', existing_type=sa.String(255))

    # Recreate foreign key constraints
    op.create_foreign_key('computehost_extra_capability_id_fk',
                          'computehost_extra_capabilities',
                          'extra_capabilities', ['capability_id'], ['id'])
    op.create_foreign_key('networksegment_extra_capability_id_fk',
                          'networksegment_extra_capabilities',
                          'extra_capabilities', ['capability_id'], ['id'])
    op.create_foreign_key('device_extra_capabilities_ibfk_2',
                          'device_extra_capabilities',
                          'extra_capabilities', ['capability_id'], ['id'])
