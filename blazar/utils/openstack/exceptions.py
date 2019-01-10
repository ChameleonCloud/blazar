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

from blazar import exceptions
from blazar.i18n import _


class ResourceProviderRetrievalFailed(exceptions.BlazarException):
    msg_fmt = _("Failed to get resource provider %(name)s")


class ResourceProviderCreationFailed(exceptions.BlazarException):
    msg_fmt = _("Failed to create resource provider %(name)s")


class ResourceProviderDeletionFailed(exceptions.BlazarException):
    msg_fmt = _("Failed to delete resource provider %(uuid)s")


class FloatingIPNetworkNotFound(exceptions.InvalidInput):
    msg_fmt = _("Failed to find network %(network)s")


class FloatingIPSubnetNotFound(exceptions.NotFound):
    msg_fmt = _("Valid subnet for the floating IP %(fip)s is not found.")


class NeutronUsesFloatingIP(exceptions.InvalidInput):
    msg_fmt = _("The floating IP %(floatingip)s is used in allocation_pools "
                "or gateway_ip in subnet %(subnet)s .")
