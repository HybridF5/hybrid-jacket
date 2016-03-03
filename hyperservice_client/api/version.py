class VersionApiMixin(object):
    def get_version(self, timeout=10):
        params = {'t': timeout}
        url = self._url("/", versioned_api=False)
        return self._result(self._get(url, params=params), True)
