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

"""Add is_unique field in extra capabilities

Revision ID: 9eec70cbc562
Revises: d5a379ff6ba3
Create Date: 2024-03-15 17:36:26.552008

"""

# revision identifiers, used by Alembic.
revision = '9eec70cbc562'
down_revision = 'd5a379ff6ba3'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

def upgrade():
    op.create_unique_constraint(None, 'devices', ['name'])
    op.add_column('extra_capabilities', sa.Column('is_unique', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    # ### end Alembic commands ###


def downgrade():
    op.drop_column('extra_capabilities', 'is_unique')
    op.drop_constraint(None, 'devices', type_='unique')
    # ### end Alembic commands ###
