from hyperservice import wsgi
from hyperservice import container
from hyperservice import host
from hyperservice import volumes


class Router(wsgi.ComposableRouter):
    def add_routes(self, mapper):
        for r in [container, host, volumes]:
            r.create_router(mapper)
