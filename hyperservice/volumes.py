import webob
from hyperservice import exception
from hyperservice import wsgi
from hyperservice import utils

from oslo.config import cfg
from hyperservice.common import log

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

CONF.register_opts(volume_opts)

LINK_DIR = "/home/.by-volume-id"
DOCKER_LINK_NAME = "docker-data-device-link"
DEV_DIRECTORY = "/dev/"

def create_symbolic(dev_path, volume_id):
    utils.trycmd('ln', '-sf', dev_path, LINK_DIR + os.path.sep + volume_id)

def remove_symbolic(volume_id):
    link_file = LINK_DIR + os.path.sep + volume_id
    if os.path.islink(link_file):
        os.remove(link_file)

class VolumeController(wsgi.Application):

    def __init__(self):
        super(VolumeController, self).__init__()
        self.volume_device_mapping = {}
        self.setup_volume_mapping()
        
    def setup_volume_mapping(self):
        if self.volume_device_mapping:
            return

        if not os.path.exists(LINK_DIR):
            os.makedirs(LINK_DIR)
            return

        for link in os.listdir(LINK_DIR):
            link_path = LINK_DIR + os.path.sep + link
            if os.path.islink(link_path):
                realpath = os.path.realpath(link_path)
                if realpath.startswith(DEV_DIRECTORY):
                    self.volume_device_mapping[link] = realpath
                    LOG.info("found volume mapping %s ==> %s", 
                            link, self.volume_device_mapping[link])

    def list_host_device(self):
        dev_out, _err = utils.trycmd('lsblk', '-dn', '-o', 'NAME,TYPE')
        dev_list = []
        for dev in dev_out.strip().split('\n'):
            name, type = dev.split()
            if type == 'disk' and not name.endswith('da'):
                dev_list.append(DEV_DIRECTORY + name)

        LOG.debug("scan host devices: %s", dev_list)
        return { "devices" : dev_list }

    def list(self, request, scan=True):
        if scan:
            LOG.debug("scaning host scsi devices")
            utils.trycmd("bash", "-c", "for f in /sys/class/scsi_host/host*/scan; do echo '- - -' > $f; done")
        return self.list_host_device()

    def add_mapping(self, volume, mountpoint, device=''):
        if not device:
            link_file = LINK_DIR + os.path.sep + volume
            if os.path.islink(link_file):
                device = os.path.realpath(link_file)
            else:
                LOG.warn("can't find the device of volume %s when attaching volume", volume)
                return
        else:
            if not device.startswith(DEV_DIRECTORY):
                device = DEV_DIRECTORY + device
            create_symbolic(device, volume)
        self.volume_device_mapping[volume] = device

    def add_root_mapping(self, volume_id):
        root_dev_path = os.path.realpath(LINK_DIR + os.path.sep + DOCKER_LINK_NAME)
        self.add_mapping(volume_id, "/docker", root_dev_path)

    def remove_mapping(self, volume):
        if volume in self.volume_device_mapping:
            remove_symbolic(volume)
            del self.volume_device_mapping[volume]

    def attach_volume(self, request, volume, device, mount_device):
        """ attach volume. """
        LOG.debug("attach volume %s : device %s, mountpoint %s", volume, device, mount_device)
        self.add_mapping(volume, mount_device, device)
        return None

    def detach_volume(self, request, volume):
        LOG.debug("dettach volume %s, current volume mapping: %s", volume, self.volume_device_mapping)
        self.remove_mapping(volume)
        return webob.Response(status_int=200)

def create_router(mapper):
    global controller
    
    mapper.connect('/volumes',
                   controller=controller,
                   action='list',
                   conditions=dict(method=['GET']))
    mapper.connect('/volumes/detach',
                   controller=controller,
                   action='detach_volume',
                   conditions=dict(method=['POST']))
    mapper.connect('/volumes/attach',
                   controller=controller,
                   action='attach_volume',
                   conditions=dict(method=['POST']))
    
controller = VolumeController()
