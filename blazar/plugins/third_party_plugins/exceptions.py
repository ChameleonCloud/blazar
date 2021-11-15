from blazar import exceptions
from blazar.i18n import _


class ResourceNotFound(exceptions.NotFound):
    msg_fmt = _("The %(resource_type)s '%(resource)s' not found!")


class CantDeleteResource(exceptions.BlazarException):
    code = 409
    msg_fmt = _("Can't delete %(resource_type)s %(resource)s. %(msg)s")


class OperationNotSupported(exceptions.BlazarException):
    msg_fmt = _("Operation not supported for resource: %s")


class NotEnoughResourcesAvailable(exceptions.BlazarException):
    msg_fmt = _("Not enough resources available")


class InvalidCreateResourceData(exceptions.BlazarException):
    msg_fmt = _("Cannot create resource: %s")


class InvalidUpdateResourceData(exceptions.BlazarException):
    msg_fmt = _("Cannot update resource: %s")
