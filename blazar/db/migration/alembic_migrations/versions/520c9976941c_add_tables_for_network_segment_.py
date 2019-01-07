# Copyright 2018 OpenStack Foundation.
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

"""Add tables for network segment reservation

Revision ID: 520c9976941c
Revises: 6ef879d2080d
Create Date: 2019-01-07 13:47:08.073385

"""

# revision identifiers, used by Alembic.
revision = '520c9976941c'
down_revision = '6ef879d2080d'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT


def MediumText():
    return sa.Text().with_variant(MEDIUMTEXT(), 'mysql')


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        'network_segments',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('network_type', sa.String(length=255), nullable=False),
        sa.Column('physical_network', sa.String(length=255), nullable=True),
        sa.Column('segment_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('network_type', 'physical_network', 'segment_id')
    )
    op.create_table(
        'networksegment_extra_capabilities',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('network_id', sa.String(length=36), nullable=False),
        sa.Column('capability_name', sa.String(length=64), nullable=False),
        sa.Column('capability_value', MediumText(), nullable=False),
        sa.ForeignKeyConstraint(['network_id'], ['network_segments.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'network_allocations',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted', sa.String(length=36), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('network_id', sa.String(length=36), nullable=True),
        sa.Column('reservation_id', sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(['network_id'], ['network_segments.id'], ),
        sa.ForeignKeyConstraint(['reservation_id'], ['reservations.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'network_reservations',
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted', sa.String(length=36), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('reservation_id', sa.String(length=36), nullable=True),
        sa.Column('resource_properties', MediumText(), nullable=True),
        sa.Column('network_properties', MediumText(), nullable=True),
        sa.Column('before_end', sa.String(length=36), nullable=True),
        sa.Column('network_name', sa.String(length=255), nullable=True),
        sa.Column('network_description', sa.String(length=255), nullable=True),
        sa.Column('network_id', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['reservation_id'], ['reservations.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('network_reservations')
    op.drop_table('network_allocations')
    op.drop_table('networksegment_extra_capabilities')
    op.drop_table('network_segments')
    # ### end Alembic commands ###
