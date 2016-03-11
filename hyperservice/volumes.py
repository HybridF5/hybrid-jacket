import webob
from hyperservice import exception
from hyperservice import wsgi
from hyperservice import db

from oslo.config import cfg
from oslo.log import log

import functools
import uuid
import os

CONF = cfg.CONF
LOG = log.getLogger(__name__)

volume_opts = [
    cfg.StrOpt('device_symbolic_directory',
               default="/home/.by-volume-id",
               help='Path to use as the volume mapping.'),
]

CONF.register_opts(host_opts)

def create_device_symbolic_directory():
    utils.trycmd("mkdir", CONF.device_symbolic_directory, attempts = 3)

def create_symbolic(dev_path, volume_id):
    
    utils.execute('ln', '-s', dev_path, CONF.device_symbolic_directory + os.sep + volume_id)

LINK_DIR = "/home/.by-volume-id"
DOCKER_LINK_NAME = "docker-data-device-link"

volume_to_dev_mapping = {}

def setup_volume_mapping():
    if not os.path.exists(LINK_DIR):
        os.mkdir(LINK_DIR)
        return
    for link in os.listdir(LINK_DIR):
        link_path = LINK_DIR + os.path + link
        if os.path.islink(link_path):
            realpath = os.path.realpath(link_path)
            if realpath.startswith("/dev/"):
                volume_to_dev_mapping[link] = realpath[len("/dev/"):]
                LOG.info("found volume mapping %s ==> /dev/%s", 
                        link, volume_to_dev_mapping[link])



class VolumeController(wsgi.Application):
    def __init__(self):

        super(VolumeController, self).__init__()
        
    def list(self, request):
        return {}

    def attach_volume(self, request, volume):
        """ add volume. """
        return {}

    def detach_volume(self, request, volume_id):
        return webob.Response(status_int=204)


def create_router(mapper):
    setup_volume_mapping()
    controller = VolumeController()
    mapper.connect('/volumes',
                   controller=controller,
                   action='list',
                   conditions=dict(method=['GET']))
    mapper.connect('/volumes/detach',
                   controller=controller,
                   action='dettach_volume',
                   conditions=dict(method=['POST']))
    mapper.connect('/volumes/attach',
                   controller=controller,
                   action='attach_volume',
                   conditions=dict(method=['POST']))
