import logging
from .. import utils
log = logging.getLogger(__name__)


class ServiceConfigApiMixin(object):
    def config_network_service(self, rabbit_user_id, rabbit_passwd, rabbit_host, rabbit_port=9696, timeout=10):
        params = {'t': timeout}
        url = self._url("/service/config")
        network_config = utils.network_service_config(rabbit_user_id, rabbit_passwd, rabbit_host, rabbit_port)
        res = self._post(url, params=params, data=network_config)
        self._raise_for_status(res)