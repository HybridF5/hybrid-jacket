class VolumeApiMixin(object):
    def attach_volume(self, volume, device, mountpoint, timeout=10):
        params = {'t': timeout}
        url = self._url("/volumes/attach")
        res = self._post_json(url, data={ 'volume' : volume, 'device': device, 'mountpoint' : mountpoint })
        self._raise_for_status(res)
        return res.raw

    def detach_volume(self, volume, timeout=10):
        params = {'t': timeout}
        url = self._url("/volumes/detach")
        res = self._post_json(url, data={ 'volume' : volume })
        self._raise_for_status(res)
        return res.raw

    def list_volume(self, timeout=10):
        params = {'t': timeout}
        url = self._url("/volumes")
        return self._result(self._get(url, params=params), True)
