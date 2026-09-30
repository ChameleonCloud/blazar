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

import datetime

from blazar.api.v1.flavor_instances import service
from blazar.api.v1 import utils as api_utils
from blazar import exceptions as ex
from blazar import utils

DATE_FORMAT = "%Y-%m-%d %H:%M"


def get_rest():
    """Return Rest app"""
    return rest


rest = api_utils.Rest('flavor_instances_v1_0', __name__,
                      url_prefix='/v1/flavor-instances')
_api = utils.LazyProxy(service.API)


def _parse_date(value, param_name):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, DATE_FORMAT)
    except ValueError:
        raise ex.BlazarException(
            "Invalid %(param)s: expected format '%(fmt)s'"
            % {'param': param_name, 'fmt': DATE_FORMAT})


def _format_date(dt):
    if dt is None:
        return None
    return datetime.datetime.strftime(dt, DATE_FORMAT)


@rest.get('/availability', query=True)
def flavor_availability(req, query):
    """Return available slot count timeline for a Nova flavor."""
    flavor_id = query.get('flavor_id')
    raw_start = query.get('start_date')
    raw_end = query.get('end_date')

    start_date = (_parse_date(raw_start, 'start_date')
                  if raw_start else datetime.datetime.utcnow())
    end_date = (_parse_date(raw_end, 'end_date')
                if raw_end else start_date + datetime.timedelta(days=30))

    results = _api.get_flavor_availability(flavor_id, start_date, end_date)

    for flavor_result in results:
        for seg in flavor_result.get('availability', []):
            seg['start'] = _format_date(seg['start'])
            seg['end'] = _format_date(seg['end'])

    return api_utils.render(flavor_instances=results)
