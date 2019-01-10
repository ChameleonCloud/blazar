# Copyright (c) 2013 Bull.
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

from blazar import exceptions
from blazar.i18n import _


class NoFreePool(exceptions.NotFound):
    msg_fmt = _("No Freepool found")


class HostNotInFreePool(exceptions.NotFound):
    msg_fmt = _("Host %(host)s not in freepool '%(freepool_name)s'")


class CantRemoveHost(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Can't remove host(s) %(host)s from Aggregate %(pool)s")


class CantDeleteHost(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Can't delete host %(host)s. %(msg)s")


class CantAddHost(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Can't add host(s) %(host)s to Aggregate %(pool)s")


class AggregateHaveHost(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Can't delete Aggregate '%(name)s', "
                "host(s) attached to it : %(hosts)s")


class AggregateAlreadyHasHost(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Conflict while adding host %(host)s to aggregate %(pool)s: "
                "%(nova_exception)s")


class AggregateNotFound(exceptions.NotFound):
    msg_fmt = _("Aggregate '%(pool)s' not found!")


class HostNotFound(exceptions.NotFound):
    msg_fmt = _("Host '%(host)s' not found!")


class InvalidHost(exceptions.NotAuthorized):
    msg_fmt = _("Invalid values for host %(host)s")


class MultipleHostsFound(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Multiple Hosts found for pattern '%(host)s'")


class HostHavingServers(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Servers [%(servers)s] found for host %(host)s")


class PluginConfigurationError(exceptions.BlazarException):
    msg_fmt = _("Plugin Configuration error : %(error)s")


class EventError(exceptions.BlazarException):
    msg_fmt = '%(error)s'


class InvalidDate(exceptions.BlazarException):
    code = 400
    msg_fmt = _(
        '%(date)s is an invalid date. Required format: %(date_format)s')


class UnsupportedResourceType(exceptions.BlazarException):
    msg_fmt = _("The %(resource_type)s resource type is not supported")


class LeaseNameAlreadyExists(exceptions.BlazarException):
    code = 409
    msg_fmt = _("The lease with name: %(name)s already exists")


class MissingTrustId(exceptions.BlazarException):
    msg_fmt = _("A trust id is required")


class NovaClientError(exceptions.BlazarException):
    msg_fmt = _("Failed to create Nova resources for the reservation")


# oshost plugin related exceptions

class CantAddExtraCapability(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Can't add extracapabilities %(keys)s to Host %(host)s")


class EndpointsNotFound(exceptions.NotFound):
    code = 404
    msg_fmt = _("No endpoints for %(service)s")


class ServiceNotFound(exceptions.NotFound):
    code = 404
    msg_fmt = _("Service %(service)s not found")


class WrongClientVersion(exceptions.BlazarException):
    code = 400
    msg_fmt = _("Unfortunately you use wrong client version")


class NoManagementUrl(exceptions.NotFound):
    code = 404
    msg_fmt = _("You haven't management url for service")


class HypervisorNotFound(exceptions.BlazarException):
    msg_fmt = _("Aggregate '%(pool)s' not found!")


class NotEnoughHostsAvailable(exceptions.BlazarException):
    restore_lease_status = True
    msg_fmt = _("Not enough hosts available")


class MalformedParameter(exceptions.BlazarException):
    code = 400
    msg_fmt = _("Malformed parameter %(param)s")


class MalformedRequirements(exceptions.BlazarException):
    code = 400
    msg_fmt = _("Malformed requirements %(rqrms)s")


class MissingParameter(exceptions.BlazarException):
    code = 400
    msg_fmt = _("Missing parameter %(param)s")


class ExtraCapabilityTooLong(exceptions.BlazarException):
    code = 400
    msg_fmt = _("Extra capability key too long")


class InvalidState(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Invalid State %(state)s for %(id)s")


class InvalidStateUpdate(InvalidState):
    msg_fmt = _("Unable to update ID %(id)s state with %(action)s:%(status)s")


class ProjectIdNotFound(exceptions.BlazarException):
    msg_fmt = _("No project_id found in current context")


class InvalidRange(exceptions.BlazarException):
    code = 400
    msg_fmt = _('Invalid values for min/max of hosts. '
                'Max must be equal to or larger than min.')


class CantUpdateParameter(exceptions.BlazarException):
    code = 409
    msg_fmt = _("%(param)s cannot be updated")


class InvalidPeriod(exceptions.BlazarException):
    code = 400
    msg_fmt = _('The end_date must be later than the start_date.')


class ConfigurationError(exceptions.BlazarException):
    msg_fmt = _("Configuration error : %(error)s")


class RedisConnectionError(exceptions.BlazarException):
    msg_fmt = _("Cannot connect to Redis host %(host)s")


class CantDeleteStartingLease(exceptions.BlazarException):
    code = 409
    restore_lease_status = True
    msg_fmt = _("Can't delete lease %(id)s while it is being started")


class CantDeleteNetwork(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Can't delete network %(network)s. %(msg)s")


class NetworkNotFound(exceptions.NotFound):
    msg_fmt = _("Network '%(network)s' not found!")


class InvalidNetwork(exceptions.NotAuthorized):
    msg_fmt = _("Invalid values for network %(network)s")


class NotEnoughNetworksAvailable(exceptions.BlazarException):
    restore_lease_status = True
    msg_fmt = _("Not enough networks available")


class NetworkCreationFailed(exceptions.BlazarException):
    msg_fmt = _("Failed to create network %(name)s for reservation %(id)s. "
                "%(msg)s")


class NetworkDeletionFailed(exceptions.BlazarException):
    msg_fmt = _("Failed to delete network %(network_id)s for reservation "
                "%(reservation_id)s")


# floating ip plugin related exceptions

class FloatingIPNotFound(exceptions.NotFound):
    msg_fmt = _("Floating IP %(floatingip)s not found.")


class CantDeleteFloatingIP(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Can't delete floating IP %(floatingip)s. %(msg)s")
