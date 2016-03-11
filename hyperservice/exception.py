import sys

from oslo.config import cfg
import webob.exc

from .i18n import _
from .common import excutils
from .common import log as logging

LOG = logging.getLogger(__name__)

exc_log_opts = [
    cfg.BoolOpt('fatal_exception_format_errors',
                default=False,
                help='Make exception message format errors fatal'),
]

CONF = cfg.CONF
CONF.register_opts(exc_log_opts)

class ConvertedException(webob.exc.WSGIHTTPException):
    def __init__(self, code=0, title="", explanation=""):
        self.code = code
        self.title = title
        self.explanation = explanation
        super(ConvertedException, self).__init__()


def _cleanse_dict(original):
    """Strip all admin_password, new_pass, rescue_pass keys from a dict."""
    return dict((k, v) for k, v in original.iteritems() if "_pass" not in k)

class HyperserviceException(Exception):
    """Base Hyperservice Exception

    To correctly use this class, inherit from it and define
    a 'msg_fmt' property. That msg_fmt will get printf'd
    with the keyword arguments provided to the constructor.

    """
    msg_fmt = _("An unknown exception occurred.")
    code = 500
    headers = {}
    safe = False
    title = ''

    def __init__(self, message=None, code=500, title='', **kwargs):
        self.kwargs = kwargs
        self.code = code
        self.title = title

        if 'code' not in self.kwargs:
            try:
                self.kwargs['code'] = self.code
            except AttributeError:
                pass

        if not message:
            try:
                message = self.msg_fmt % kwargs

            except Exception:
                exc_info = sys.exc_info()
                # kwargs doesn't match a variable in the message
                # log the issue and the kwargs
                LOG.exception(_('Exception in string format operation'))
                for name, value in kwargs.iteritems():
                    LOG.error("%s: %s" % (name, value))    # noqa

                if CONF.fatal_exception_format_errors:
                    raise exc_info[0], exc_info[1], exc_info[2]
                else:
                    # at least get the core message out if something happened
                    message = self.msg_fmt

        super(HyperserviceException, self).__init__(message)

    def format_message(self):
        # NOTE(mrodden): use the first argument to the python Exception object
        # which should be our full HyperserviceException message, (see __init__)
        return self.args[0]

        
class ValidationError(HyperserviceException):
    msg_fmt = _("Expecting to find %(attribute)s in %(target)s -"
                       " the server could not comply with the request"
                       " since it is either malformed or otherwise"
                       " incorrect. The client is assumed to be in error.")
    code = 400
    title = 'Bad Request'

class Invalid(HyperserviceException):
    msg_fmt = _("Unacceptable parameters.")
    code = 400

class Forbidden(HyperserviceException):
    msg_fmt = _("Not authorized.")
    code = 403

class UnexpectedError(HyperserviceException):
    msg_fmt = _("Unexpected Error.")
    code = 500

class AdminRequired(Forbidden):
    msg_fmt = _("Container does not have admin privileges")

class InvalidInput(Invalid):
    msg_fmt = _("Invalid input received: %(reason)s")

class InvalidContentType(Invalid):
    msg_fmt = _("Invalid content type %(content_type)s.")

class InvalidID(Invalid):
    title = "Invalid Id"
    msg_fmt = _("Invalid ID received %(id)s.")

class NotFound(HyperserviceException):
    title = "Not Found"
    msg_fmt = _("Resource could not be found.")
    code = 404

class ConfigNotFound(HyperserviceException):
    msg_fmt = _("Could not find config at %(path)s")

class PasteAppNotFound(HyperserviceException):
    msg_fmt = _("Could not load paste app '%(name)s' from %(path)s")

class MalformedRequestBody(HyperserviceException):
    msg_fmt = _("Malformed message body: %(reason)s")

class ImageNotFound(NotFound):
    title = "Image Not Found"
    msg_fmt = _("Image %(id) Not Found.")

class ContainerNotFound(NotFound):
    title = "Container Not Found"
    msg_fmt = _("No Container Found.")

class ContainerCreateFailed(HyperserviceException):
    msg_fmt = _("Unable to create Container")

class ContainerStartFailed(HyperserviceException):
    msg_fmt = _("Unable to start Container")

class InjectFailed(HyperserviceException):
    msg_fmt = _("Inject file %(path)s failed")
