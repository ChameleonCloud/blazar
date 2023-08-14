# Copyright 2022 OpenStack Foundation.
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

"""add third party plugins

Revision ID: 4c95dbdbcd23
Revises: 4b7bdec3ae61
Create Date: 2022-01-07 20:47:31.416670

"""

# revision identifiers, used by Alembic.
revision = '4c95dbdbcd23'
down_revision = '4b7bdec3ae61'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT


def MediumText():
    return sa.Text().with_variant(MEDIUMTEXT(), 'mysql')


def upgrade():
    op.create_table(
        'resources',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('resource_type', sa.String(length=255), nullable=False),
        sa.Column('reservable', sa.Boolean(),
                  server_default=sa.text('true'), nullable=False),
        sa.Column('data', sa.JSON, nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'resource_resource_properties',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('resource_id', sa.String(length=36), nullable=False),
        sa.Column('property_value', MediumText(), nullable=False),
        sa.Column('property_id', sa.String(length=36), nullable=False),
        sa.Column('deleted', sa.String(length=36), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], ),
        sa.ForeignKeyConstraint(['property_id'], ['resource_properties.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'resource_allocations',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('resource_id', sa.String(length=36), nullable=True),
        sa.Column('reservation_id', sa.String(length=36), nullable=True),
        sa.Column('deleted', sa.String(length=36), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], ),
        sa.ForeignKeyConstraint(['reservation_id'], ['reservations.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'resource_reservations',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('reservation_id', sa.String(length=36), nullable=True),
        sa.Column('resource_properties', MediumText(), nullable=True),
        sa.Column('count_range', sa.String(length=36), nullable=True),
        sa.Column('before_end', sa.String(length=36), nullable=True),
        sa.Column('values', sa.JSON, nullable=False),
        sa.Column('deleted', sa.String(length=36), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['reservation_id'], ['reservations.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('resource_reservations')
    op.drop_table('resource_allocations')
    op.drop_table('resource_resource_properties')
    op.drop_table('resources')
