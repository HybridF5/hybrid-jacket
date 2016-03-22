class VolumeApiMixin(object):
    def attach_volume(self, volume_id, device, mount_device, timeout=10):
        params = {'t': timeout}
        url = self._url("/volumes/attach")
        res = self._post_json(url, data={ 'volume' : volume_id, 'device': device, 'mount_device' : mount_device })
        self._raise_for_status(res)
        return res.raw

    def detach_volume(self, volume_id, timeout=10):
        params = {'t': timeout}
        url = self._url("/volumes/detach")
        res = self._post_json(url, data={ 'volume' : volume_id })
        self._raise_for_status(res)
        return res.raw

    def list_volume(self, timeout=10):
        params = {'t': timeout}
        url = self._url("/volumes")
        return self._result(self._get(url, params=params), True)
