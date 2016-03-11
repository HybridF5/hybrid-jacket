from hyperservice import wsgi
from hyperservice import container
from hyperservice import host


class Router(wsgi.ComposableRouter):
    def add_routes(self, mapper):
        for r in [container, host]:
            r.create_router(mapper)
