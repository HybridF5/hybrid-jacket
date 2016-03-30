class VolumeApiMixin(object):
    def list_volume(self, timeout=10):
        params = {'t': timeout}
        url = self._url("/volumes")
        return self._result(self._get(url, params=params), True)

    def clone_volume(self, volume, src_vref, timeout=10):
        params = {'t': timeout}
        url = self._url("/volumes/clone")
        res = self._post_json(url, data={ 'volume' : { 'id' : volume['id'], 'size' : volume['size']}, 
            'src_vref': { 'id': src_vref['id'], 'size': src_vref['size'] } })
        return self._result(res, True)
