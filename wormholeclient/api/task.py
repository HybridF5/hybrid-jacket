class TaskApiMixin(object):

    def query_task(self, task, timeout=10):
        """ Query a task status, return an integer.
            constants.TASK_DOING ==> Task started, but not completed
            constants.TASK_SUCCESS ==> Task complete, and successfully
            constants.TASK_ERROR ==> Task complete, but error
        """
        params = {'t': timeout}
        url = self._url("/tasks/%s" % task.get('task_id'))
        status =  self._result(self._get(url, params=params), True)
        return status['status']

