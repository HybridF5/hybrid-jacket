from .. import utils


class ContainerApiMixin(object):

    def create_container(self, image_name, volume_id=None, timeout=10):
        params = {'t': timeout}
        url = self._url("/container/create")
        create_config = utils.create_container_config(image_name, volume_id=volume_id)
        res = self._post_json(url, params=params, data=create_config)
        self._raise_for_status(res)
        return res.raw

    def restart_container(self, timeout=10, network_info=None, block_device_info=None):
        params = {'t': timeout}
        url = self._url("/container/restart")
        restart_config = utils.restart_container_config(network_info, block_device_info)
        res = self._post_json(url, params=params, data=restart_config)
        self._raise_for_status(res)
        return res.raw

    def stop_container(self, timeout=10):
        params = {'t': timeout}
        url = self._url("/container/stop")
        res = self._post_json(url, params=params, data=None)
        self._raise_for_status(res)
        return res.raw

    def start_container(self, timeout=10, network_info=None, block_device_info=None):
        params = {'t': timeout}
        url = self._url("/container/start")
        start_config = utils.start_container_config(network_info, block_device_info)
        res = self._post_json(url, params=params, data=start_config)
        self._raise_for_status(res)
        return res.raw
