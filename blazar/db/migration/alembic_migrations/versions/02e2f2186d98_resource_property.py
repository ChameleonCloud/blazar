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

"""resource property

Revision ID: 02e2f2186d98
Revises: f4084140f608
Create Date: 2020-04-17 15:51:40.542459

"""

# revision identifiers, used by Alembic.
# This upstream migration's schema changes are superseded in this fork by
# 02e2f2186d98_extra_capability.py + ee3b2513b59f_rename_extra_capability.py,
# which cover the same schema end-state. Kept as a no-op to preserve position
# in the chain (upstream intended this immediately after f4084140f608).
revision = '1a2b3c4d5e6f'
down_revision = 'f4084140f608'


def upgrade():
    pass


def downgrade():
    pass
