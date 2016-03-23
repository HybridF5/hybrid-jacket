class TaskApiMixin(object):
    def query_task(self, task, timeout=10):
        params = {'t': timeout}
        url = self._url("/tasks/%s" % task.get('task_id'))
        status =  self._result(self._get(url, params=params), True)
        return status['status']

