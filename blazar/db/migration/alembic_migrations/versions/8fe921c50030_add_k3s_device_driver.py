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

"""add k8s device driver

Revision ID: 8fe921c50030
Revises: 42c7fd6e792e
Create Date: 2022-01-27 20:48:00.443041

"""

# revision identifiers, used by Alembic.
revision = '8fe921c50030'
down_revision = '42c7fd6e792e'

from alembic import op


def upgrade():
    connection = op.get_bind()
    connection.execute(
        "ALTER TABLE devices MODIFY COLUMN device_driver ENUM('zun', 'k8s');")


def downgrade():
    connection = op.get_bind()
    connection.execute("UPDATE devices SET device_driver = 'zun';")
    connection.execute(
        "ALTER TABLE devices MODIFY COLUMN device_driver ENUM('zun')")
