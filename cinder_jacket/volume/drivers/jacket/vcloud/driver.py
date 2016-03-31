import os
import time
import shutil
import urllib2
import traceback
import subprocess
from oslo.config import cfg
from oslo.utils import excutils

from cinder import utils
from cinder.i18n import _
from cinder import exception
import cinder.compute.nova as nova
from cinder.image import image_utils
from cinder.backup.driver import BackupDriver
from cinder.openstack.common import fileutils
from cinder.openstack.common import log as logging

from cinder.volume import driver
from cinder.volume import volume_types
from cinder.volume.drivers.jacket.vcloud import util
from cinder.volume.drivers.jacket.vcloud import constants
from cinder.volume.drivers.jacket.vcloud.vcloud import RetryDecorator
from cinder.volume.drivers.jacket.vcloud.vcloud_client import VCloudClient
from cinder.volume.drivers.jacket.vcloud import sshclient

from wormholeclient import errors
from wormholeclient.client import Client
from wormholeclient import constants as client_constants

from keystoneclient.v2_0 import client as kc


vcloudapi_opts = [

    cfg.StrOpt('vcloud_node_name',
               default='vcloud_node_01',
               help='node name,which a node is a vcloud vcd '
               'host.'),
    cfg.StrOpt('vcloud_host_ip',
               default='162.3.110.103',
               help='Hostname or IP address for connection to VMware VCD '
               'host.'),
    cfg.IntOpt('vcloud_host_port',
               default=443,
               help='Host port for cnnection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_host_username',
               default='nkapotoxin',
               help='Host username for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_host_password',
               default='Galax0088',
               help='Host password for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_org',
               default='nkapotoxin-org',
               help='User org for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_vdc',
               default='nkapotoxin-hybrid-org',
               help='Vdc for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_version',
               default='5.5',
               help='Version for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_service',
               default='85-719',
               help='Service for connection to VMware VCD '
               'host.'),
    cfg.BoolOpt('vcloud_verify',
                default=False,
                help='Verify for connection to VMware VCD '
                'host.'),
    cfg.StrOpt('vcloud_service_type',
               default='vcd',
               help='Service type for connection to VMware VCD '
               'host.'),
    cfg.IntOpt('vcloud_api_retry_count',
               default=12,
               help='Api retry count for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_ovs_ethport',
               default='eth1',
               help='The eth port of ovs-vm use '
               'to connect vm openstack create '),
    cfg.StrOpt('vcloud_conversion_dir',
               default='/vcloud/convert_tmp',
               help='the directory where images are converted in '),
    cfg.StrOpt('vcloud_volumes_dir',
               default='/vcloud/volumes',
               help='the directory of volume files'),

    cfg.StrOpt('base_image_id',
               help='The base image id which hybrid vm use.'),
    cfg.StrOpt('base_image_name',
               default = 'ubuntu-upstart',
               help='The base image name which hybrid vm use.'),
    cfg.StrOpt('hybrid_service_port',
               default = '7127',
               help='The port of the hybrid service.'),
    cfg.StrOpt('provider_base_network_name',
               help='The provider network name which base provider network use.'),
    cfg.StrOpt('provider_tunnel_network_name',
               help='The provider network name which tunnel provider network use.'),
]

vcloudvgw_opts = [
    cfg.StrOpt('vcloud_vgw_host',
               default='',
               help='the ip or host of vcloud vgw host.'),
    cfg.StrOpt('vcloud_vgw_name',
               default='vcloud_vgw',
               help='the name of vcloud vgw host.'),
    cfg.StrOpt('vcloud_vgw_username',
               default='root',
               help='user name of vcloud vgw host.'),
    cfg.StrOpt('vcloud_vgw_password',
               default='',
               help='password of vcloud vgw host.'),
    cfg.StrOpt('store_file_dir',
               default='/home/upload',
               help='Directory used for temporary storage '
                    'during migrate volume'),
    # cfg.DictOpt('vcloud_vgw_url',
    #             default={
    #                 'fs_vgw_url': 'http://162.3.114.62:8090/',
    #                 'vcloud_vgw_url': 'http://162.3.114.108:8090/',
    #                 'aws_vgw_url': 'http://172.27.12.245:8090/'
    #             },
    #             help="These values will be used for upload/download image "
    #                  "from vgw host."),
    ]

keystone_opts =[
    cfg.StrOpt('tenant_name',
               default='admin',
               help='tenant name for connecting to keystone in admin context'),
    cfg.StrOpt('user_name',
               default='cloud_admin',
               help='username for connecting to cinder in admin context'),
    cfg.StrOpt('keystone_auth_url',
               default='https://identity.cascading.hybrid.huawei.com:443/identity-admin/v2.0',
               help='value of keystone url'),
]

keystone_auth_group = cfg.OptGroup(name='keystone_authtoken',
                               title='keystone_auth_group')

CONF = cfg.CONF
CONF.register_opts(vcloudapi_opts, 'vcloud')
CONF.register_opts(vcloudvgw_opts, 'vgw')

CONF.register_group(keystone_auth_group)
CONF.register_opts(keystone_opts,'keystone_authtoken')

LOG = logging.getLogger(__name__)
# VOLUME_FILE_DIR = '/hc_volumes'
# CONVERT_DIR = '/hctemp'
IMAGE_TRANSFER_TIMEOUT_SECS = 300
VGW_URLS = ['vgw_url']


def _retry_decorator(max_retry_count=-1, inc_sleep_time=10, max_sleep_time=10, exceptions=()):
    def handle_func(func):
        def handle_args(*args, **kwargs):
            retry_count = 0
            sleep_time = 0
            def _sleep(retry_count, sleep_time):
                retry_count += 1
                if max_retry_count == -1 or retry_count < max_retry_count:
                    sleep_time += inc_sleep_time
                    if sleep_time > max_sleep_time:
                        sleep_time = max_sleep_time

                    LOG.debug('_retry_decorator func %s times %s sleep time %s', func, retry_count, sleep_time)
                    time.sleep(sleep_time)
                    return retry_count, sleep_time
                else:
                    return retry_count, sleep_time
            while (max_retry_count == -1 or retry_count < max_retry_count):
                try:
                    result = func(*args, **kwargs)
                    if not result:
                        retry_count, sleep_time = _sleep(retry_count, sleep_time)
                    else:
                        return result
                except exceptions:
                    with excutils.save_and_reraise_exception() as ctxt:
                        retry_count, sleep_time = _sleep(retry_count, sleep_time)
                        if max_retry_count == -1 or retry_count < max_retry_count:
                            ctxt.reraise = False

            if max_retry_count != -1 and retry_count >= max_retry_count:
                LOG.error(_("func (%(name)s) exec failed since retry count (%(retry_count)d) reached max retry count (%(max_retry_count)d)."),
                                  {'name': func, 'retry_count': retry_count, 'max_retry_count': max_retry_count})
        return handle_args
    return handle_func

def make_step_decorator(context, instance, update_instance_progress,
                        total_offset=0):
    """Factory to create a decorator that records instance progress as a series
    of discrete steps.

    Each time the decorator is invoked we bump the total-step-count, so after::

        @step
        def step1():
            ...

        @step
        def step2():
            ...

    we have a total-step-count of 2.

    Each time the step-function (not the step-decorator!) is invoked, we bump
    the current-step-count by 1, so after::

        step1()

    the current-step-count would be 1 giving a progress of ``1 / 2 *
    100`` or 50%.
    """
    step_info = dict(total=total_offset, current=0)

    def bump_progress():
        step_info['current'] += 1
        update_instance_progress(context, instance,
                                 step_info['current'], step_info['total'])

    def step_decorator(f):
        step_info['total'] += 1

        @functools.wraps(f)
        def inner(*args, **kwargs):
            rv = f(*args, **kwargs)
            bump_progress()
            return rv

        return inner

    return step_decorator

class VCloudVolumeDriver(driver.VolumeDriver):
    VERSION = "1.0"

    def __init__(self, scheme="https", *args, **kwargs):
        super(VCloudVolumeDriver, self).__init__( *args, **kwargs)
        self._stats = None
        self._nova_api = nova.API()
        self._node_name = CONF.vcloud.vcloud_node_name
        self._vcloud_client = VCloudClient(scheme=scheme)

        self._vgw_host = CONF.vgw.vcloud_vgw_host
        self._vgw_name = CONF.vgw.vcloud_vgw_name
        self._vgw_username = CONF.vgw.vcloud_vgw_username
        self._vgw_password = CONF.vgw.vcloud_vgw_password
        #self._vgw_url = CONF.vgw.vcloud_vgw_url
        self._vgw_store_file_dir = CONF.vgw.store_file_dir

        self.db = kwargs.get('db')

    def _get_vcloud_volume_name(self, volume_id, volume_name):
        volume_prefix = 'volume@'
        snapshot_prefix = 'snapshot@'
        backup_prefix = 'backup@'

        if volume_name.startswith(volume_prefix):
            vcloud_volume_name = volume_name[len(volume_prefix):]
        elif volume_name.startswith(snapshot_prefix):
            vcloud_volume_name = volume_name[len(snapshot_prefix):]
        elif volume_name.startswith(backup_prefix):
            vcloud_volume_name = volume_name[len(backup_prefix):]
        else:
            vcloud_volume_name = volume_id

        return vcloud_volume_name

    def do_setup(self, context):
        """Instantiate common class and log in storage system."""
        pass

    def check_for_setup_error(self):
        """Check configuration file."""
        pass

    def create_volume(self, volume):
        """Create a volume."""

        # use volume_name as vcloud disk name, remove prefix str `volume@`
        # if volume_name does not start with volume@, then use volume id instead
        volume_name = volume['display_name']
        vcloud_volume_name = self._get_vcloud_volume_name(volume['id'],volume_name)

        LOG.debug('Creating volume %(name)s of size %(size)s Gb',
                  {'name': vcloud_volume_name, 'size': volume['size']})

        self._vcloud_client.create_volume(vcloud_volume_name, volume['size'])

    def delete_volume(self, volume):
        """Delete a volume."""
        volume_name = volume['display_name']
        vcloud_volume_name = self._get_vcloud_volume_name(volume['id'], volume_name)
        LOG.debug('Deleting volume %s', vcloud_volume_name)

        self._vcloud_client.delete_volume(vcloud_volume_name)

    def create_volume_from_snapshot(self, volume, snapshot):
        """Create a volume from a snapshot."""

        def _delete_volumes():
            for created_volume in created_volumes:
                self._vcloud_client.delete_volume(created_volume)

        def _delete_vapp():
            self._vcloud_client.delete_vapp(clone_vapp_name)

        def _detach_disks_to_vm():
            for attached_disk_name in attached_disk_names:
                result, disk_ref = self._vcloud_client.get_disk_ref(attached_disk_name)
                if result:
                    self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)

        def _power_off_vapp():
            self._vcloud_client.power_off(clone_vapp_name)

        LOG.debug('create volume from snapshot volume: %s snapshot: %s', vars(volume), vars(snapshot))

        undo_mgr = util.UndoManager()
        created_volumes = []
        attached_disk_names = []

        try:
            volume_name = volume['display_name']
            vcloud_volume_name = self._get_vcloud_volume_name(volume['id'], volume_name)
            self._vcloud_client.create_volume(vcloud_volume_name, volume['size'])
            result, disk_ref = self._vcloud_client.get_disk_ref(vcloud_volume_name)
            created_volumes.append(vcloud_volume_name)
            undo_mgr.undo_with(_delete_volumes)
            LOG.debug("Create volume %s in vcloud successful" % vcloud_volume_name)

            snapshot_name = snapshot['display_name']
            vcloud_snapshot_volume_name = self._get_vcloud_volume_name(snapshot['id'], snapshot_name)
            result, snapshot_disk_ref = self._vcloud_client.get_disk_ref(vcloud_snapshot_volume_name)
            if not result:
                msg = _('the volume %s for snapshot in vcloud cannot found', vcloud_snapshot_volume_name)
                LOG.error(msg)
                raise exception.CinderException(msg)

            #NOTE(nkapotoxin): create vapp with vapptemplate
            network_names = [CONF.vcloud.provider_tunnel_network_name, CONF.vcloud.provider_base_network_name]
            network_configs = self._vcloud_client.get_network_configs(network_names)

            # create vapp
            clone_vapp_name = 'server@%s' % vcloud_volume_name
            clone_vapp = self._vcloud_client.create_vapp(clone_vapp_name, CONF.vcloud.base_image_id, network_configs)
            undo_mgr.undo_with(_delete_vapp)
            LOG.debug("Create clone vapp %s successful" % clone_vapp_name)

            # generate the network_connection
            network_connections = self._vcloud_client.get_network_connections(clone_vapp, network_names)

            # update network
            self._vcloud_client.update_vms_connections(clone_vapp, network_connections)
            
            # update vm specification
            #self._vcloud_client.modify_vm_cpu(clone_vapp, instance.get_flavor().vcpus)
            #self._vcloud_client.modify_vm_memory(clone_vapp, instance.get_flavor().memory_mb)
            LOG.debug("Config vapp %s successful" % clone_vapp_name)

            if clone_vapp_name.startswith('server@'):
                local_disk_name = 'Local@%s' % clone_vapp_name[len('server@'):]
            else:
                local_disk_name = 'Local@%s' % clone_vapp_name

            self._vcloud_client.create_volume(local_disk_name, 1)
            created_volumes.append(local_disk_name)
            LOG.debug("Create Local disk %s for vapp %s successful", local_disk_name, clone_vapp_name)

            result, local_disk_ref = self._vcloud_client.get_disk_ref(local_disk_name)
            self._vcloud_client.attach_disk_to_vm(clone_vapp_name, local_disk_ref)
            attached_disk_names.append(local_disk_name)
            undo_mgr.undo_with(_detach_disks_to_vm)
            LOG.debug("attach local disk %s to vapp %s successful", local_disk_name, clone_vapp_name)

            # power on it
            self._vcloud_client.power_on_vapp(clone_vapp_name)
            undo_mgr.undo_with(_power_off_vapp)

            vapp_ip = self.get_vapp_ip(clone_vapp_name)
            client = Client(vapp_ip, port=CONF.vcloud.hybrid_service_port)
            self._wait_hybrid_service_up(vapp_ip, CONF.vcloud.hybrid_service_port)
            LOG.debug("vapp %s hybrid service has been up", clone_vapp_name)

            odevs = set(client.list_volume()['devices'])
            if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, snapshot_disk_ref):
                attached_disk_names.append(vcloud_snapshot_volume_name)
                LOG.debug("Volume %(volume_name)s attached to: %(instance_name)s",
                        {'volume_name': vcloud_snapshot_volume_name, 'instance_name': clone_vapp_name})

            ndevs = set(client.list_volume()['devices'])
            devs = ndevs - odevs
            for dev in devs:
                client.attach_volume(snapshot['id'], dev, constants.DEV1)

            odevs = set(client.list_volume()['devices'])
            if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, disk_ref):
                attached_disk_names.append(vcloud_volume_name)
                LOG.debug("Volume %(volume_name)s attached to: %(instance_name)s",
                        {'volume_name': vcloud_volume_name, 'instance_name': clone_vapp_name})
            ndevs = set(client.list_volume()['devices'])
            devs = ndevs - odevs
            for dev in devs:
                client.attach_volume(volume['id'], dev, constants.DEV2)

            src_vref = {}
            src_vref['id'] = snapshot['id']
            src_vref['size'] = snapshot['volume_size']

            LOG.debug('begin time of clone vloume(size %s GB) is %s', snapshot['volume_size'], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            task = client.clone_volume(volume, src_vref)
            task_state = client.query_task(task)
            while task_state == client_constants.TASK_DOING:
                time.sleep(30)
                task_state = client.query_task(task)

            if task_state != client_constants.TASK_SUCCESS:
                msg = "create volume from snaptshot clone failed"
                LOG.error(msg)
                raise exception.CinderException(msg)
            else:                
                LOG.debug('end time of clone vloume is %s', time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

            attached_disk_names.remove(vcloud_volume_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)

            attached_disk_names.remove(local_disk_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, local_disk_ref)

            attached_disk_names.remove(vcloud_snapshot_volume_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, snapshot_disk_ref)            

            undo_mgr.cancel_undo(_power_off_vapp)
            self._vcloud_client.power_off_vapp(clone_vapp_name)

            undo_mgr.cancel_undo(_delete_vapp)
            self._vcloud_client.delete_vapp(clone_vapp_name)

            created_volumes.remove(local_disk_name)
            self._vcloud_client.delete_volume(local_disk_name)
        except Exception as e:
            msg = _("Failed to create volume from snapshot reason %s, rolling back") % e
            LOG.error(msg)
            undo_mgr.rollback_and_reraise(msg=msg)

    def create_cloned_volume(self, volume, src_vref):
        """Create a clone of the specified volume."""

        def _attach_disk_to_vm():
            self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)

        def _delete_volumes():
            for created_volume in created_volumes:
                self._vcloud_client.delete_volume(created_volume)

        def _delete_vapp():
            self._vcloud_client.delete_vapp(clone_vapp_name)

        def _detach_disks_to_vm():
            for attached_disk_name in attached_disk_names:
                result, disk_ref = self._vcloud_client.get_disk_ref(attached_disk_name)
                if result:
                    self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)

        def _power_off_vapp():
            self._vcloud_client.power_off(clone_vapp_name)

        LOG.debug('create cloned volume:%s src_vref %s', vars(volume), vars(src_vref))

        undo_mgr = util.UndoManager()
        created_volumes = []
        attached_disk_names = []

        try:
            volume_name = src_vref['display_name']
            vcloud_volume_name = self._get_vcloud_volume_name(src_vref['id'], volume_name)
            result, disk_ref = self._vcloud_client.get_disk_ref(vcloud_volume_name)
            if not result:
                msg = _('the volume %s for clone in vcloud cannot found', vcloud_volume_name)
                LOG.error(msg)
                raise exception.CinderException(msg)


            if src_vref['volume_attachment']:
                vapp_name = self._vcloud_client.get_disk_attached_vapp(vcloud_volume_name)
                vapp = self._vcloud_client._get_vcloud_vapp(vapp_name)
                if self._vcloud_client._get_status_first_vm(vapp) != constants.VM_POWER_OFF_STATUS:
                    msg = "when source volume is attached, the vm must be in power off state"
                    LOG.info(msg)
                    raise exception.CinderException(msg)

                self._vcloud_client.detach_disk_from_vm(vapp_name, disk_ref)
                undo_mgr.undo_with(_attach_disk_to_vm)                
                LOG.debug("source volume %s has been detached from vapp %s", vcloud_volume_name, vapp_name)

            cloned_volume_name = volume['display_name']
            vcloud_cloned_volume_name = self._get_vcloud_volume_name(volume['id'], cloned_volume_name)

            self._vcloud_client.create_volume(vcloud_cloned_volume_name, volume['size'])
            result, cloned_disk_ref = self._vcloud_client.get_disk_ref(vcloud_cloned_volume_name)

            created_volumes.append(vcloud_cloned_volume_name)
            undo_mgr.undo_with(_delete_volumes)
            LOG.debug('volume %s(size %s GB) has been created', vcloud_cloned_volume_name, volume['size'])

            #NOTE(nkapotoxin): create vapp with vapptemplate
            network_names = [CONF.vcloud.provider_tunnel_network_name, CONF.vcloud.provider_base_network_name]
            network_configs = self._vcloud_client.get_network_configs(network_names)

            # create vapp
            clone_vapp_name = 'server@%s' % vcloud_cloned_volume_name
            clone_vapp = self._vcloud_client.create_vapp(clone_vapp_name, CONF.vcloud.base_image_id, network_configs)
            undo_mgr.undo_with(_delete_vapp)
            LOG.debug("Create clone vapp %s successful" % clone_vapp_name)

            # generate the network_connection
            network_connections = self._vcloud_client.get_network_connections(clone_vapp, network_names)

            # update network
            self._vcloud_client.update_vms_connections(clone_vapp, network_connections)

            # update vm specification
            #self._vcloud_client.modify_vm_cpu(clone_vapp, instance.get_flavor().vcpus)
            #self._vcloud_client.modify_vm_memory(clone_vapp, instance.get_flavor().memory_mb)
            LOG.debug("Config vapp %s successful" % clone_vapp_name)

            if clone_vapp_name.startswith('server@'):
                local_disk_name = 'Local@%s' % clone_vapp_name[len('server@'):]
            else:
                local_disk_name = 'Local@%s' % clone_vapp_name

            self._vcloud_client.create_volume(local_disk_name, 1)
            created_volumes.append(local_disk_name)
            LOG.debug("Create Local disk %s for vapp %s successful", local_disk_name, clone_vapp_name)

            result, local_disk_ref = self._vcloud_client.get_disk_ref(local_disk_name)
            self._vcloud_client.attach_disk_to_vm(clone_vapp_name, local_disk_ref)
            attached_disk_names.append(local_disk_name)
            undo_mgr.undo_with(_detach_disks_to_vm)
            LOG.debug("attach local disk %s to vapp %s successful", local_disk_name, clone_vapp_name)

            # power on it
            self._vcloud_client.power_on_vapp(clone_vapp_name)
            undo_mgr.undo_with(_power_off_vapp)

            vapp_ip = self.get_vapp_ip(clone_vapp_name)
            client = Client(vapp_ip, port=CONF.vcloud.hybrid_service_port)
            self._wait_hybrid_service_up(vapp_ip, CONF.vcloud.hybrid_service_port)
            LOG.debug("vapp %s hybrid service has been up", clone_vapp_name)

            odevs = set(client.list_volume()['devices'])
            if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, disk_ref):
                attached_disk_names.append(vcloud_volume_name)
                LOG.debug("Volume %s attached to: %s",vcloud_volume_name, clone_vapp_name)

            ndevs = set(client.list_volume()['devices'])
            devs = ndevs - odevs
            for dev in devs:
                client.attach_volume(src_vref['id'], dev, constants.DEV1)

            odevs = set(client.list_volume()['devices'])
            if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, cloned_disk_ref):
                attached_disk_names.append(vcloud_cloned_volume_name)
                LOG.info("Volume %s attached to: %s", vcloud_cloned_volume_name, clone_vapp_name)

            ndevs = set(client.list_volume()['devices'])
            devs = ndevs - odevs
            for dev in devs:
                client.attach_volume(volume['id'], dev, constants.DEV2)

            LOG.debug('begin time of clone vloume(size %s GB) is %s', src_vref['size'], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            task = client.clone_volume(volume, src_vref)
            task_state = client.query_task(task)
            while task_state == client_constants.TASK_DOING:
                time.sleep(30)
                task_state = client.query_task(task)

            if task_state != client_constants.TASK_SUCCESS:
                msg = "create clone volume clone failed"
                LOG.error(msg)
                raise exception.CinderException(msg)
            else:
                LOG.debug('end time of clone vloume is %s', time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

            attached_disk_names.remove(vcloud_volume_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)
            if src_vref['volume_attachment']:
                undo_mgr.cancel_undo(_attach_disk_to_vm) 
                self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)

            attached_disk_names.remove(local_disk_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, local_disk_ref)

            attached_disk_names.remove(vcloud_cloned_volume_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, cloned_disk_ref)

            undo_mgr.cancel_undo(_power_off_vapp)
            self._vcloud_client.power_off_vapp(clone_vapp_name)

            undo_mgr.cancel_undo(_delete_vapp)
            self._vcloud_client.delete_vapp(clone_vapp_name)

            created_volumes.remove(local_disk_name)
            self._vcloud_client.delete_volume(local_disk_name)
        except Exception as e:
            msg = _("Failed to create cloned volume reason %s, rolling back") % e
            LOG.error(msg)
            undo_mgr.rollback_and_reraise(msg=msg)

    def extend_volume(self, volume, new_size):
        """Extend a volume."""
        pass

    def create_snapshot(self, snapshot):
        """Create a snapshot."""

        def _attach_disk_to_vm():
            self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)

        def _delete_volumes():
            for created_volume in created_volumes:
                self._vcloud_client.delete_volume(created_volume)

        def _delete_vapp():
            self._vcloud_client.delete_vapp(clone_vapp_name)

        def _detach_disks_to_vm():
            for attached_disk_name in attached_disk_names:
                result, disk_ref = self._vcloud_client.get_disk_ref(attached_disk_name)
                if result:
                    self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)

        def _power_off_vapp():
            self._vcloud_client.power_off(clone_vapp_name)

        LOG.debug('create snapshot: %s', vars(snapshot))

        undo_mgr = util.UndoManager()
        created_volumes = []
        attached_disk_names = []

        try:
            volume_name = snapshot['volume']['display_name']
            vcloud_volume_name = self._get_vcloud_volume_name(snapshot['volume_id'], volume_name)
            result, disk_ref = self._vcloud_client.get_disk_ref(vcloud_volume_name)
            if not result:
                msg = "cannot find volume %s in vcloud" % vcloud_volume_name
                LOG.error(msg)
                raise exception.CinderException(msg)            

            if snapshot['volume']['attach_status'] == 'attached':
                vapp_name = self._vcloud_client.get_disk_attached_vapp(vcloud_volume_name)
                vapp = self._vcloud_client._get_vcloud_vapp(vapp_name)
                if self._vcloud_client._get_status_first_vm(vapp) != constants.VM_POWER_OFF_STATUS:
                    msg = "when volume is attached, the vm must be in power off state"
                    LOG.info(msg)
                    raise exception.CinderException(msg)

                self._vcloud_client.detach_disk_from_vm(vapp_name, disk_ref)
                undo_mgr.undo_with(_attach_disk_to_vm)
                LOG.debug("source volume %s has been detach from vapp %s", vcloud_volume_name, vapp_name)

            snapshot_name = snapshot['display_name']
            vcloud_snapshot_volume_name = self._get_vcloud_volume_name(snapshot['id'], snapshot_name)
            self._vcloud_client.create_volume(vcloud_snapshot_volume_name, snapshot['volume_size'])
            result, snapshot_disk_ref = self._vcloud_client.get_disk_ref(vcloud_snapshot_volume_name)
            created_volumes.append(vcloud_snapshot_volume_name)
            undo_mgr.undo_with(_delete_volumes)
            LOG.debug('snapshot volume %s(size %s Gb) has been created',  vcloud_snapshot_volume_name, snapshot['volume_size'])

            #NOTE(nkapotoxin): create vapp with vapptemplate
            network_names = [CONF.vcloud.provider_tunnel_network_name, CONF.vcloud.provider_base_network_name]
            network_configs = self._vcloud_client.get_network_configs(network_names)

            # create vapp
            clone_vapp_name = 'server@%s' % vcloud_snapshot_volume_name
            clone_vapp = self._vcloud_client.create_vapp(clone_vapp_name, CONF.vcloud.base_image_id, network_configs)
            undo_mgr.undo_with(_delete_vapp) 
            LOG.debug("Clone vapp %s created successful", clone_vapp_name)        

            # generate the network_connection
            network_connections = self._vcloud_client.get_network_connections(clone_vapp, network_names)

            # update network
            self._vcloud_client.update_vms_connections(clone_vapp, network_connections)
            # update vm specification
            #self._vcloud_client.modify_vm_cpu(clone_vapp, instance.get_flavor().vcpus)
            #self._vcloud_client.modify_vm_memory(clone_vapp, instance.get_flavor().memory_mb)
            LOG.debug("Config vapp %s successful" % clone_vapp_name)


            if clone_vapp_name.startswith('server@'):
                local_disk_name = 'Local@%s' % clone_vapp_name[len('server@'):]
            else:
                local_disk_name = 'Local@%s' % clone_vapp_name

            self._vcloud_client.create_volume(local_disk_name, 1)
            created_volumes.append(local_disk_name)
            LOG.debug("Create Local disk %s for vapp %s successful", local_disk_name, clone_vapp_name)

            result, local_disk_ref = self._vcloud_client.get_disk_ref(local_disk_name)
            self._vcloud_client.attach_disk_to_vm(clone_vapp_name, local_disk_ref)
            attached_disk_names.append(local_disk_name)
            undo_mgr.undo_with(_detach_disks_to_vm)
            LOG.debug("attach local disk %s to vapp %s successful", local_disk_name, clone_vapp_name)

            # power on it
            self._vcloud_client.power_on_vapp(clone_vapp_name)
            undo_mgr.undo_with(_power_off_vapp)

            vapp_ip = self.get_vapp_ip(clone_vapp_name)
            client = Client(vapp_ip, CONF.vcloud.hybrid_service_port)
            self._wait_hybrid_service_up(vapp_ip, CONF.vcloud.hybrid_service_port)
            LOG.debug("vapp %s hybrid service has been up", clone_vapp_name)

            odevs = set(client.list_volume()['devices'])
            if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, disk_ref):
                attached_disk_names.append(vcloud_volume_name)
                LOG.debug("Volume %s attached to: %s", vcloud_volume_name, clone_vapp_name)

            ndevs = set(client.list_volume()['devices'])
            devs = ndevs - odevs
            for dev in devs:
                client.attach_volume(snapshot['volume_id'], dev, constants.DEV1)

            odevs = set(client.list_volume()['devices'])            
            if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, snapshot_disk_ref):
                attached_disk_names.append(vcloud_snapshot_volume_name)
                LOG.debug("Volume %s attached to: %s", vcloud_snapshot_volume_name, clone_vapp_name)

            ndevs = set(client.list_volume()['devices'])
            devs = ndevs - odevs
            for dev in devs:
                client.attach_volume(snapshot['id'], dev, constants.DEV2)

            volume = {}
            volume['id'] = snapshot['id']
            volume['size'] = snapshot['volume_size']
            src_vref = {}
            src_vref['id'] = snapshot['volume_id']
            src_vref['size'] = snapshot['volume_size']

            LOG.debug('begin time of clone vloume(size %s GB) is %s', snapshot['volume_size'], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            task = client.clone_volume(volume, src_vref)
            task_state = client.query_task(task)
            while client.query_task(task) == client_constants.TASK_DOING:
                time.sleep(30)
                task_state = client.query_task(task)

            if task_state != client_constants.TASK_SUCCESS:
                msg = "create snapshot clone failed"
                LOG.error(msg)
                raise exception.CinderException(msg)
            else:
                LOG.debug('end time of clone vloume is %s', time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

            attached_disk_names.remove(vcloud_volume_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)
            if snapshot['volume']['attach_status'] == 'attached':
                undo_mgr.cancel_undo(_attach_disk_to_vm)
                self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)

            attached_disk_names.remove(vcloud_snapshot_volume_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, snapshot_disk_ref)

            attached_disk_names.remove(local_disk_name)
            self._vcloud_client.detach_disk_from_vm(clone_vapp_name, local_disk_ref)

            undo_mgr.cancel_undo(_power_off_vapp)
            self._vcloud_client.power_off_vapp(clone_vapp_name)

            undo_mgr.cancel_undo(_delete_vapp)
            self._vcloud_client.delete_vapp(clone_vapp_name)

            created_volumes.remove(local_disk_name)
            self._vcloud_client.delete_volume(local_disk_name)
        except Exception as e:
            msg = _("Failed to create snapshot reason %s, rolling back") % e
            LOG.error(msg)
            undo_mgr.rollback_and_reraise(msg=msg)

    def delete_snapshot(self, snapshot):
        """Delete a snapshot."""

        snapshot_name = snapshot['display_name']
        vcloud_snapshot_volume_name = self._get_vcloud_volume_name(snapshot['id'], snapshot_name)

        LOG.debug('Deleting volume %s', vcloud_snapshot_volume_name)
        self._vcloud_client.delete_volume(vcloud_snapshot_volume_name)

    def get_volume_stats(self, refresh=False):
        """Get volume stats."""
        vdc = self._vcloud_client.vdc
        if not self._stats:
            backend_name = self.configuration.safe_get('volume_backend_name')
            LOG.debug('*******backend_name is %s' %backend_name)
            if not backend_name:
                backend_name = 'HC_vcloud'
            data = {'volume_backend_name': backend_name,
                    'vendor_name': 'Huawei',
                    'driver_version': self.VERSION,
                    'storage_protocol': 'LSI Logic SCSI',
                    # xxx(wangfeng): get from vcloud
                    'reserved_percentage': 0,
                    'total_capacity_gb': 1000,
                    'free_capacity_gb': 1000}
            self._stats = data
        return self._stats

    def create_export(self, context, volume):
        """Export the volume."""
        pass

    def ensure_export(self, context, volume):
        """Synchronously recreate an export for a volume."""
        pass

    def remove_export(self, context, volume):
        """Remove an export for a volume."""
        pass

    def _query_vmdk_url(self, the_vapp):

        # node_name = instance.node

        # 0. shut down the app first
        # node_name = instance.node

        # 0. shut down the app first
        try:
            the_vapp = self._vcloud_client.power_off_vapp(the_vapp)
        except:
            LOG.error('power off failed')

        # 1.enable download.
        task = self._vcloud_client._invoke_vapp_api(the_vapp, 'enableDownload')
        if not task:
            raise exception.CinderException(
                "enable vmdk file download failed, task:")
        self._session._wait_for_task(task)

        # 2.get vapp info and ovf descriptor
        the_vapp = self._vcloud_client._get_vcloud_vapp(the_vapp.name)
        # the_vapp = self._vcloud_client._invoke_vapp_api(the_vapp, 'get_updated_vapp')

        ovf = self._vcloud_client._invoke_vapp_api(the_vapp, 'get_ovf_descriptor')

        # 3.get referenced file url
        referenced_file_url = self._vcloud_client._invoke_vapp_api(the_vapp, 'get_referenced_file_url',ovf)
        if not referenced_file_url:
            raise exception.CinderException("get vmdk file url failed")

        return referenced_file_url

    def _download_vmdk_from_vcloud(self,context, src_url,dst_file_name):
        local_file_handle = open(dst_file_name, "wb")
        remote_file_handle = urllib2.urlopen(src_url)
        file_size = remote_file_handle.headers['content-length']
        util.start_transfer(context, IMAGE_TRANSFER_TIMEOUT_SECS,remote_file_handle, file_size,
                             write_file_handle=local_file_handle)

    def _attach_volume_to_vgw(self, volume):
        volume_name = volume['display_name']
        vcloud_volume_name = self._get_vcloud_volume_name(volume['id'],
                                                          volume_name)
        # get the provider_volume at provider cloud
        # find volume reference by it's name
        result, disk_ref = self._vcloud_client.get_disk_ref(vcloud_volume_name)
        if result:
            LOG.debug("Find volume successful, disk name is: %(disk_name)s "
                      "disk ref's href is: %(disk_href)s.",
                      {'disk_name': vcloud_volume_name,
                       'disk_href': disk_ref.href})
        else:
            LOG.error(_('Unable to find volume %s'),
                      vcloud_volume_name)
            raise exception.VolumeNotFound(volume_id=vcloud_volume_name)
        # Check whether the volume is attached to vm or not,
        # Make sure the volume is available
        vpp_name = self._vcloud_client.get_disk_attached_vapp(vcloud_volume_name)
        if vpp_name:
            self._vcloud_client.detach_disk_from_vm(vpp_name, disk_ref)
        # get the vgw host
        vapp_name = self._vgw_name
        # attach volume to vgw when the vgw is in stopped status
        if self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref):
            LOG.info("Volume %(volume_name)s attached to "
                     "vgw host: %(instance_name)s",
                     {'volume_name': vcloud_volume_name,
                      'instance_name': vapp_name})
        return disk_ref, vapp_name

    def _get_management_url(self, kc, image_name, **kwargs):
        endpoint_info= kc.service_catalog.get_endpoints(**kwargs)
        endpoint_list = endpoint_info.get(kwargs.get('service_type'),None)
        region_name = image_name.split('_')[-1]
        if endpoint_list:
            for endpoint in endpoint_list:
                if region_name == endpoint.get('region'):
                    return endpoint.get('publicURL')

    @RetryDecorator(max_retry_count=CONF.vcloud.vcloud_api_retry_count,
                    exceptions=(sshclient.SSHError,
                                sshclient.SSHTimeout))
    def _copy_volume_to_file_to_vgw(self, image_meta):
        try:
            image_id = image_meta.get('id')
            image_name = image_meta.get('name')
            container_format = image_meta.get('container_format')
            dest_file_path = os.path.join('/tmp', image_id)

            ssh_client = sshclient.SSH(user=self._vgw_username,
                                           host=self._vgw_host,
                                           password=self._vgw_password)

            cmd = '/usr/bin/rescan-scsi-bus.sh -a -r'
            ssh_client.run(cmd)

            # convert volume to image
            cmd = 'qemu-img convert -c -O qcow2 %s %s' %\
                  ('/dev/sdb', dest_file_path)
            LOG.error('begin time of %s is %s' %
                      (cmd, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()
                                          )))
            ssh_client.run(cmd)
            LOG.debug("Finished running cmd : %s" % cmd)
            LOG.error('end time of %s is %s' %
                      (cmd, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()
                                          )))
            # push the converted image to remote vgw host
            # vgw_url = CONF.vgw.vcloud_vgw_url[container_format]
            kwargs = {'auth_url': CONF.keystone_authtoken.keystone_auth_url,
                      'tenant_name': CONF.keystone_authtoken.tenant_name,
                      'username': CONF.keystone_authtoken.user_name,
                      'password': CONF.keystone_authtoken.admin_password,
                      'insecure': True}
            keystoneclient = kc.Client(**kwargs)
            vgw_url = ''
            vgw_url = self._get_management_url(keystoneclient, image_name,
                                               service_type='v2v')

            LOG.debug('The remote vgw url is %(vgw_url)s',
                      {'vgw_url': vgw_url})
            # eg: curl -X POST --http1.0 -T
            # /tmp/467bd6e1-5a6e-4daa-b8bc-356b718834f2
            # http://172.27.12.245:8090/467bd6e1-5a6e-4daa-b8bc-356b718834f2
            cmd = 'curl -X POST --http1.0 -T %s ' % dest_file_path
            cmd += vgw_url
            if cmd.endswith('/'):
                cmd += image_id
            else:
                cmd += '/' + image_id
            LOG.error('begin time of %s is %s' %
                      (cmd, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()
                                          )))
            ssh_client.run(cmd)
            LOG.error('end time of %s is %s' %
                      (cmd, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()
                                          )))

            LOG.debug("Finished running cmd : %s" % cmd)
        except Exception as e:
            with excutils.save_and_reraise_exception():
                LOG.error('Failed to copy volume to image by vgw.',
                          traceback.format_exc())
        finally:
            if ssh_client:
                # delete the temp file which is used for convert volume to image
                ssh_client.run('rm -f %s' % dest_file_path)
                ssh_client.close()

    @utils.synchronized("vcloud_volume_copy_lock", external=True)
    def copy_volume_to_image(self, context, volume, image_service, image_meta):
        """Creates glance image from volume."""
        LOG.debug('Copying volume %(volume_name)s to image %(image_name)s.',
                  {'volume_name': volume['display_name'],
                   'image_name': image_meta.get('name')})

        LOG.error('begin time of copy_volume_to_image is %s' %
                  (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())))

        container_format = image_meta.get('container_format')
        if container_format in VGW_URLS:
            # attach the volume to vgw vm
            disk_ref, vapp_name = self._attach_volume_to_vgw(volume)

            try:
                # use ssh client connect to vgw_host and
                # copy image file to volume
                self._copy_volume_to_file_to_vgw(image_meta)
            finally:
                # detach volume from vgw and
                self._vcloud_client.detach_disk_from_vm(vapp_name, disk_ref)
            # create an empty file to glance
            with image_utils.temporary_file() as tmp:
                image_utils.upload_volume(context,
                                          image_service,
                                          image_meta,
                                          tmp)

            LOG.error('end time of copy_volume_to_image is %s' %
                      (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())))

        else:
            pass

    @RetryDecorator(max_retry_count=CONF.vcloud.vcloud_api_retry_count,
                    exceptions=(sshclient.SSHError,
                                sshclient.SSHTimeout))
    def _copy_file_to_volume_from_vgw(self, image_id):
        try:
            dest_file_path = os.path.join(self._vgw_store_file_dir, image_id)
            ssh_client = sshclient.SSH(user=self._vgw_username,
                                       host=self._vgw_host,
                                       password=self._vgw_password)

            cmd = '/usr/bin/rescan-scsi-bus.sh -a -r'
            ssh_client.run(cmd)

            # copy data to volume
            # TODO(luqitao): need to get device name, does not use sdb.
            # TODO(luqitao): check the dest_file does exist or not?
            cmd = 'qemu-img convert %s %s' %\
                  (dest_file_path, '/dev/sdb')
            ssh_client.run(cmd)
            LOG.debug("Finished running cmd : %s" % cmd)

            cmd = 'rm -rf %s' % dest_file_path
            ssh_client.run(cmd)

        except Exception as e:
            LOG.error('Failed to copy data to volume from vgw. '
                      'traceback: %s', traceback.format_exc())
            raise e
        finally:
            if ssh_client:
                ssh_client.close()

    @utils.synchronized("vcloud_volume_copy_lock", external=True)
    def copy_image_to_volume(self, context, volume, image_service, image_id):
        """Creates volume from image."""
        LOG.info('begin time of copy_image_to_volume is %s' % (time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime())))

        image_meta = image_service.show(context, image_id)
        LOG.debug('Copying image %(image_name)s to volume %(volume_name)s.',
                  {'volume_name': volume['display_name'],
                   'image_name': image_meta.get('name')})

        container_format = image_meta.get('container_format')
        if container_format in VGW_URLS:
            disk_ref, vapp_name = self._attach_volume_to_vgw(volume)
            # start the vgw, so it can recognize the volume
            #   (vcloud does not support online attach or detach volume)
            # self._vcloud_client.power_on_vapp(the_vapp)

            try:
                # use ssh client connect to vgw_host and
                # copy image file to volume
                LOG.error('begin time of _copy_file_to_volume_from_vgw is %s' %
                          (time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime())))
                self._copy_file_to_volume_from_vgw(image_id)
                LOG.error('end time of _copy_file_to_volume_from_vgw is %s' %
                          (time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime())))
            finally:
                # detach volume from vgw and
                self._vcloud_client.detach_disk_from_vm(vapp_name, disk_ref)

            # shutdown the vgw, do some clean env work
            # self._vcloud_client.power_off_vapp(the_vapp)
        elif container_format == constants.HYBRID_VM:
            #if container formate eq hybrivm, doing nothing
            pass

        LOG.debug('Finished copy image %(image_name)s '
                 'to volume %(volume_name)s.',
                 {'volume_name': volume['display_name'],
                  'image_name': image_meta.get('name')})
        LOG.info('end time of copy_image_to_volume is %s' % (time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime())))

    def backup_volume(self, context, backup, backup_service):
        """Create a new backup from an existing volume."""
        LOG.debug("backup volume: backup:%s backup_service:%s", 
                   vars(backup), vars(backup_service))

        def _attach_disk_to_vm():
            self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)

        def _delete_volumes():
            for created_volume in created_volumes:
                self._vcloud_client.delete_volume(created_volume)

        def _delete_vapp():
            self._vcloud_client.delete_vapp(clone_vapp_name)

        def _detach_disks_to_vm():
            for attached_disk_name in attached_disk_names:
                result, disk_ref = self._vcloud_client.get_disk_ref(attached_disk_name)
                if result:
                    self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)

        def _power_off_vapp():
            self._vcloud_client.power_off(clone_vapp_name)

        if backup_service.DRIVER_NAME == 'VCloudBackupDriver':
            undo_mgr = util.UndoManager()
            created_volumes = []
            attached_disk_names = []
            
            try:
                volume = self.db.volume_get(context, backup['volume_id'])
                vcloud_volume_name = self._get_vcloud_volume_name(volume['id'], volume['display_name'])
                result, disk_ref = self._vcloud_client.get_disk_ref(vcloud_volume_name)
                if not result:
                    msg = "cannot find volume %s in vcloud" % vcloud_volume_name
                    LOG.error(msg)
                    raise exception.CinderException(msg)

                if volume['volume_attachment']:
                    vapp_name = self._vcloud_client.get_disk_attached_vapp(vcloud_volume_name)
                    vapp = self._vcloud_client._get_vcloud_vapp(vapp_name)
                    if self._vcloud_client._get_status_first_vm(vapp) != constants.VM_POWER_OFF_STATUS:
                        msg = "when volume is attached, the vm must be in power off state"
                        LOG.info(msg)
                        raise exception.CinderException(msg)

                    self._vcloud_client.detach_disk_from_vm(vapp_name, disk_ref)
                    undo_mgr.undo_with(_attach_disk_to_vm)                
                    LOG.debug("source volume %s has been detached from vapp %s", vcloud_volume_name, vapp_name)


                backup_name = backup['display_name']
                vcloud_backup_volume_name = self._get_vcloud_volume_name(backup['id'], backup_name)

                self._vcloud_client.create_volume(vcloud_backup_volume_name, backup['size'])
                result, backup_disk_ref = self._vcloud_client.get_disk_ref(vcloud_backup_volume_name)
                created_volumes.append(vcloud_cloned_volume_name)
                undo_mgr.undo_with(_delete_volumes)
                LOG.debug('volume %s(size %s GB) has been created', vcloud_backup_volume_name, backup['size'])

                #NOTE(nkapotoxin): create vapp with vapptemplate
                network_names = [CONF.vcloud.provider_tunnel_network_name, CONF.vcloud.provider_base_network_name]
                network_configs = self._vcloud_client.get_network_configs(network_names)

                # create vapp
                clone_vapp_name = 'server@%s' % vcloud_backup_volume_name
                clone_vapp = self._vcloud_client.create_vapp(clone_vapp_name, CONF.vcloud.base_image_id, network_configs)
                undo_mgr.undo_with(_delete_vapp)
                LOG.debug("Create clone vapp %s successful" % clone_vapp_name)

                # generate the network_connection
                network_connections = self._vcloud_client.get_network_connections(clone_vapp, network_names)

                # update network
                self._vcloud_client.update_vms_connections(clone_vapp, network_connections)

                # update vm specification
                #self._vcloud_client.modify_vm_cpu(clone_vapp, instance.get_flavor().vcpus)
                #self._vcloud_client.modify_vm_memory(clone_vapp, instance.get_flavor().memory_mb)
                LOG.debug("Config vapp %s successful" % clone_vapp_name)

                if clone_vapp_name.startswith('server@'):
                    local_disk_name = 'Local@%s' % clone_vapp_name[len('server@'):]
                else:
                    local_disk_name = 'Local@%s' % clone_vapp_name

                self._vcloud_client.create_volume(local_disk_name, 1)
                created_volumes.append(local_disk_name)
                LOG.debug("Create Local disk %s for vapp %s successful", local_disk_name, clone_vapp_name)

                result, local_disk_ref = self._vcloud_client.get_disk_ref(local_disk_name)
                self._vcloud_client.attach_disk_to_vm(clone_vapp_name, local_disk_ref)
                attached_disk_names.append(local_disk_name)
                undo_mgr.undo_with(_detach_disks_to_vm)
                LOG.debug("attach local disk %s to vapp %s successful", local_disk_name, clone_vapp_name)

                # power on it
                self._vcloud_client.power_on_vapp(clone_vapp_name)
                undo_mgr.undo_with(_power_off_vapp)

                vapp_ip = self.get_vapp_ip(clone_vapp_name)
                client = Client(vapp_ip, CONF.vcloud.hybrid_service_port)
                self._wait_hybrid_service_up(vapp_ip, CONF.vcloud.hybrid_service_port)
                LOG.debug("vapp %s hybrid service has been up", clone_vapp_name)
                
                odevs = set(client.list_volume()['devices'])
                if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, disk_ref):
                    attached_disk_names.append(vcloud_volume_name)
                    LOG.debug("Volume %s attached to: %s", vcloud_volume_name,  clone_vapp_name)

                ndevs = set(client.list_volume()['devices'])
                devs = ndevs - odevs
                for dev in devs:
                    client.attach_volume(volume['id'], dev, constants.DEV1)

                odevs = set(client.list_volume()['devices'])
                if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, backup_disk_ref):
                    attached_disk_names.append(vcloud_backup_volume_name)
                    LOG.debug("Volume %s attached to: %s", vcloud_backup_volume_name, clone_vapp_name)
                ndevs = set(client.list_volume()['devices'])
                devs = ndevs - odevs
                for dev in devs:
                    client.attach_volume(backup['id'], dev, constants.DEV2)

                dest_vref = {}
                dest_vref['id'] = backup['id']
                dest_vref['size'] = backup['size']

                LOG.debug('begin time of clone vloume(size %s GB) is %s', backup['size'], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
                task = client.clone_volume(dest_vref, volume)
                task_state = client.query_task(task)
                while client.query_task(task) == client_constants.TASK_DOING:
                    time.sleep(30)
                    task_state = client.query_task(task)

                if task_state != client_constants.TASK_SUCCESS:
                    msg = "create back volume clone failed"
                    LOG.error(msg)
                    raise exception.CinderException(msg)
                else:
                    LOG.debug('end time of clone vloume is %s', time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

                attached_disk_names.remove(vcloud_volume_name)
                self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)
                if volume['volume_attachment']:
                    undo_mgr.cancel_undo(_attach_disk_to_vm) 
                    self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)

                attached_disk_names.remove(local_disk_name)
                self._vcloud_client.detach_disk_from_vm(clone_vapp_name, local_disk_ref)

                attached_disk_names.remove(vcloud_backup_volume_name)
                self._vcloud_client.detach_disk_from_vm(clone_vapp_name, backup_disk_ref)

                undo_mgr.cancel_undo(_power_off_vapp)
                self._vcloud_client.power_off_vapp(clone_vapp_name)

                undo_mgr.cancel_undo(_delete_vapp)
                self._vcloud_client.delete_vapp(clone_vapp_name)

                created_volumes.remove(local_disk_name)
                self._vcloud_client.delete_volume(local_disk_name)
            except Exception as e:
                msg = _("Failed to backup volume reason %s, rolling back") % e
                LOG.error(msg)
                undo_mgr.rollback_and_reraise(msg=msg)
        else:
            super(VCloudVolumeDriver, self).backup_volume(context, backup, backup_service)

    def restore_backup(self, context, backup, volume, backup_service):
        """Restore an existing backup to a new or existing volume."""

        def _attach_disk_to_vm():
            self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)

        def _delete_volumes():
            for created_volume in created_volumes:
                self._vcloud_client.delete_volume(created_volume)

        def _delete_vapp():
            self._vcloud_client.delete_vapp(clone_vapp_name)

        def _detach_disks_to_vm():
            for attached_disk_name in attached_disk_names:
                result, disk_ref = self._vcloud_client.get_disk_ref(attached_disk_name)
                if result:
                    self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)

        def _power_off_vapp():
            self._vcloud_client.power_off(clone_vapp_name)

        if backup_service.DRIVER_NAME == 'VCloudBackupDriver':
            undo_mgr = util.UndoManager()
            created_volumes = []
            attached_disk_names = []

            try:
                vcloud_volume_name = self._get_vcloud_volume_name(volume['id'], volume['display_name'])
                result, disk_ref = self._vcloud_client.get_disk_ref(vcloud_volume_name)
                if not result:
                    msg = "cannot find volume %s in vcloud" % vcloud_volume_name
                    LOG.error(msg)
                    raise exception.CinderException(msg)


                if volume['volume_attachment']:
                    vapp_name = self._vcloud_client.get_disk_attached_vapp(vcloud_volume_name)
                    vapp = self._vcloud_client._get_vcloud_vapp(vapp_name)
                    if self._vcloud_client._get_status_first_vm(vapp) != constants.VM_POWER_OFF_STATUS:
                        msg = "when volume is attached, the vm must be in power off state"
                        LOG.info(msg)
                        raise exception.CinderException(msg)

                    self._vcloud_client.detach_disk_from_vm(vapp_name, disk_ref)
                    undo_mgr.undo_with(_attach_disk_to_vm)                
                    LOG.debug("dest volume %s has been detached from vapp %s", vcloud_volume_name, vapp_name)


                backup_name = backup['display_name']
                vcloud_backup_volume_name = self._get_vcloud_volume_name(backup['id'], backup_name)

                result, backup_disk_ref = self._vcloud_client.get_disk_ref(vcloud_backup_volume_name)
                if not result:
                    msg = "cannot find backup volume %s in vcloud" % vcloud_backup_volume_name
                    LOG.error(msg)
                    raise exception.CinderException(msg)
                

                #NOTE(nkapotoxin): create vapp with vapptemplate
                network_names = [CONF.vcloud.provider_tunnel_network_name, CONF.vcloud.provider_base_network_name]
                network_configs = self._vcloud_client.get_network_configs(network_names)

                # create vapp
                clone_vapp_name = 'server@%s' % vcloud_volume_name
                clone_vapp = self._vcloud_client.create_vapp(clone_vapp_name, CONF.vcloud.base_image_id, network_configs)
                undo_mgr.undo_with(_delete_vapp)
                LOG.debug("Create clone vapp %s successful" % clone_vapp_name)


                # generate the network_connection
                network_connections = self._vcloud_client.get_network_connections(clone_vapp, network_names)

                # update network
                self._vcloud_client.update_vms_connections(clone_vapp, network_connections)
                # update vm specification
                #self._vcloud_client.modify_vm_cpu(clone_vapp, instance.get_flavor().vcpus)
                #self._vcloud_client.modify_vm_memory(clone_vapp, instance.get_flavor().memory_mb)
                LOG.debug("Config vapp %s successful" % clone_vapp_name)


                if clone_vapp_name.startswith('server@'):
                    local_disk_name = 'Local@%s' % clone_vapp_name[len('server@'):]
                else:
                    local_disk_name = 'Local@%s' % clone_vapp_name

                self._vcloud_client.create_volume(local_disk_name, 1)
                undo_mgr.undo_with(_delete_volumes)
                created_volumes.append(local_disk_name)
                LOG.debug("Create Local disk %s for vapp %s successful", local_disk_name, clone_vapp_name)

                result, local_disk_ref = self._vcloud_client.get_disk_ref(local_disk_name)
                self._vcloud_client.attach_disk_to_vm(clone_vapp_name, local_disk_ref)
                attached_disk_names.append(local_disk_name)
                undo_mgr.undo_with(_detach_disks_to_vm)
                LOG.debug("attach local disk %s to vapp %s successful", local_disk_name, clone_vapp_name)
 

                # power on it
                self._vcloud_client.power_on_vapp(clone_vapp_name)
                undo_mgr.undo_with(_power_off_vapp)


                vapp_ip = self.get_vapp_ip(clone_vapp_name)
                client = Client(vapp_ip, CONF.vcloud.hybrid_service_port)
                self._wait_hybrid_service_up(vapp_ip, CONF.vcloud.hybrid_service_port)
                LOG.debug("vapp %s hybrid service has been up", clone_vapp_name)

                odevs = set(client.list_volume()['devices'])
                if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, backup_disk_ref):
                    attached_disk_names.append(vcloud_backup_volume_name)
                    LOG.debug("Volume %s attached to: %s", vcloud_backup_volume_name, clone_vapp_name)

                ndevs = set(client.list_volume()['devices'])
                devs = ndevs - odevs
                for dev in devs:
                    client.attach_volume(backup['id'], dev, constants.DEV1)


                odevs = set(client.list_volume()['devices'])
                if self._vcloud_client.attach_disk_to_vm(clone_vapp_name, disk_ref):
                    attached_disk_names.append(vcloud_volume_name)
                    LOG.info("Volume %s attached to: %s", vcloud_volume_name, clone_vapp_name)
                ndevs = set(client.list_volume()['devices'])
                devs = ndevs - odevs
                for dev in devs:
                    client.attach_volume(volume['id'], dev, constants.DEV2)

                src_vref = {}
                src_vref['id'] = backup['id']
                src_vref['size'] = backup['size']            

                LOG.debug('begin time of clone vloume(size %s GB) is %s', backup['size'], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

                task = client.clone_volume(volume, src_vref)
                task_state = client.query_task(task)
                while client.query_task(task) == client_constants.TASK_DOING:
                    time.sleep(30)
                    task_state = client.query_task(task)

                if task_state != client_constants.TASK_SUCCESS:
                    msg = "restore back clone failed"
                    LOG.error(msg)
                    raise exception.CinderException(msg)
                else:
                    LOG.debug('end time of clone vloume is %s', time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

                attached_disk_names.remove(vcloud_volume_name)
                self._vcloud_client.detach_disk_from_vm(clone_vapp_name, disk_ref)
                if volume['volume_attachment']:
                    undo_mgr.cancel_undo(_attach_disk_to_vm)
                    self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)

                attached_disk_names.remove(local_disk_name)
                self._vcloud_client.detach_disk_from_vm(clone_vapp_name, local_disk_ref)

                attached_disk_names.remove(vcloud_backup_volume_name)
                self._vcloud_client.detach_disk_from_vm(clone_vapp_name, backup_disk_ref)

                undo_mgr.cancel_undo(_power_off_vapp)
                self._vcloud_client.power_off_vapp(clone_vapp_name)

                undo_mgr.cancel_undo(_delete_vapp)
                self._vcloud_client.delete_vapp(clone_vapp_name)

                created_volumes.remove(local_disk_name)
                self._vcloud_client.delete_volume(local_disk_name)
            except Exception as e:
                msg = _("Failed to restore backup reason %s, rolling back") % e
                LOG.error(msg)
                undo_mgr.rollback_and_reraise(msg=msg)
        else:
            super(VCloudVolumeDriver, self).restore_backup(context, backup, volume, backup_service)

    def initialize_connection(self, volume, connector):
        """Allow connection to connector and return connection info."""
        LOG.debug('vCloud Driver: initialize_connection')

        driver_volume_type = 'vcloud_volume'
        data = {}
        data['backend'] = 'vcloud'
        data['volume_id'] = volume['id']
        data['display_name'] = volume['display_name']

        return {'driver_volume_type': driver_volume_type,
                 'data': data}

    def terminate_connection(self, volume, connector, **kwargs):
        """Disallow connection from connector"""
        LOG.debug('vCloud Driver: terminate_connection')
        pass

    def validate_connector(self, connector):
        """Fail if connector doesn't contain all the data needed by driver."""
        LOG.debug('vCloud Driver: validate_connector')
        pass

    @_retry_decorator(max_retry_count=60,exceptions = (errors.APIError,errors.NotFound))
    def get_vapp_ip(self, vapp_name):
        return self._vcloud_client.get_vapp_ip(vapp_name)

    @_retry_decorator(max_retry_count=60,exceptions=(errors.APIError,errors.NotFound, errors.ConnectionError, errors.InternalError))
    def _wait_hybrid_service_up(self, server_ip, port = '7127'):
        client = Client(server_ip, port = port)
        return client.get_version()

class VCloudBackupDriver(BackupDriver):
    """
    """    
    DRIVER_NAME = 'VCloudBackupDriver'

    def __init__(self, context, db_driver=None):
        self._vcloud_client = VCloudClient(scheme="https")

        super(VCloudBackupDriver, self).__init__(db_driver)

    def _get_vcloud_volume_name(self, volume_id, volume_name):
        backup_prefix = 'backup@'

        if volume_name.startswith(backup_prefix):
            vcloud_volume_name = volume_name[len(backup_prefix):]
        else:
            vcloud_volume_name = volume_id

        return vcloud_volume_name

    def backup(self, backup, volume_file, backup_metadata=False):
        """Start a backup of a specified volume."""
        raise NotImplementedError(_('VCloudBackupDriver only support VCloudVolumeDriver'))

    def restore(self, backup, volume_id, volume_file):
        """Restore a saved backup."""
        raise NotImplementedError(_('VCloudBackupDriver only support VCloudVolumeDriver'))

    def delete(self, backup):
        """Delete a saved backup."""

        backup_name = backup['display_name']
        vcloud_backup_volume_name = self._get_vcloud_volume_name(backup['id'], backup_name)
        self._vcloud_client.delete_volume(vcloud_backup_volume_name)
        return
