import logging
from .. import utils
log = logging.getLogger(__name__)


class PersonalityApiMixin(object):
    def inject_file(self, dst_path, src_path, timeout=10):
        params = {'t': timeout}
        url = self._url("/service/personality")
        inject_file_config = utils.inject_file_config(dst_path, src_path)
        res = self._post(url, params=params, data=inject_file_config)
        self._raise_for_status(res)