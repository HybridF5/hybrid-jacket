# Copyright 2011 OpenStack Foundation
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
A connection to the VMware vCloud platform.
"""

import os
import subprocess
import shutil
import time
import urllib2

from oslo.config import cfg

import sshclient
from nova.compute import power_state
from nova.compute import task_states
from nova import image
from nova.openstack.common import log as logging
from nova.openstack.common import fileutils as fileutils
from nova.i18n import _
from nova.virt.jacket.common import fake_driver
from nova.virt.jacket.common import common_tools
from nova.virt.jacket.vcloud import hyper_agent_api
from nova.virt.jacket.vcloud import util
from nova.virt.jacket.vcloud.vcloud import VCLOUD_STATUS
from nova.virt.jacket.vcloud.vcloud_client import VCloudClient
from nova.volume.cinder import API as cinder_api
from nova.network import neutronv2
from hyperserviceclient.client import Client
from hyperserviceclient import errors

vcloudapi_opts = [

    cfg.StrOpt('vcloud_node_name',
               default='vcloud_node_01',
               help='node name, which a node is a vcloud vcd'
               'host.'),
    cfg.StrOpt('vcloud_host_ip',
               help='Hostname or IP address for connection to VMware VCD '
               'host.'),
    cfg.IntOpt('vcloud_host_port',
               default=443,
               help='Host port for cnnection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_host_username',
               help='Host username for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_host_password',
               help='Host password for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_org',
               help='User org for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_vdc',
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
    cfg.BoolOpt('use_link_clone',
                default=True,
                help='Use link clone or not '),
    cfg.StrOpt('vcloud_service_type',
               default='vcd',
               help='Service type for connection to VMware VCD '
               'host.'),
    cfg.IntOpt('vcloud_api_retry_count',
               default=2,
               help='Api retry count for connection to VMware VCD '
               'host.'),
    cfg.StrOpt('vcloud_conversion_dir',
               default='/vcloud/convert_tmp',
               help='the directory where images are converted in '),
    cfg.StrOpt('vcloud_volumes_dir',
               default='/vcloud/volumes',
               help='the directory of volume files'),
    cfg.StrOpt('vcloud_vm_naming_rule',
               default='openstack_vm_id',
               help='the rule to name vcloud VMs, valid options:'
               'openstack_vm_id, openstack_vm_name, cascaded_openstack_rule'),
    cfg.DictOpt('vcloud_flavor_map',
                default={
                    'm1.tiny': '1',
                    'm1.small': '2',
                    'm1.medium': '3',
                    'm1.large': '4',
                    'm1.xlarge': '5'},
                help='map nova flavor name to vcloud vm specification id'),
    cfg.StrOpt('metadata_iso_catalog',
               default='metadata-isos',
               help='The metadata iso cotalog.'),
    cfg.StrOpt('provider_base_network_name',
               help='The provider network name which base provider network use.'),
    cfg.StrOpt('provider_tunnel_network_name',
               help='The provider network name which tunnel provider network use.'),
    cfg.StrOpt('image_user',
               default='',
               help=''),
    cfg.StrOpt('image_password',
               default='',
               help=''),
    cfg.StrOpt('tunnel_cidr',
               help='The tunnel cidr of provider network.'),
    cfg.StrOpt('route_gw',
               help='The route gw of the provider network.'),

    cfg.StrOpt('dst_path',
               default = '/home/neutron_agent_conf.txt',
               help='The config location for hybrid vm.'),
    cfg.StrOpt('hybrid_service_port',
               default = '7127',
               help='The port of the hybrid service.')
]

status_dict_vapp_to_instance = {
    VCLOUD_STATUS.FAILED_CREATION: power_state.CRASHED,
    VCLOUD_STATUS.UNRESOLVED: power_state.BUILDING,
    VCLOUD_STATUS.RESOLVED: power_state.BUILDING,
    VCLOUD_STATUS.DEPLOYED: power_state.NOSTATE,
    VCLOUD_STATUS.SUSPENDED: power_state.SUSPENDED,
    VCLOUD_STATUS.POWERED_ON: power_state.RUNNING,
    VCLOUD_STATUS.WAITING_FOR_INPUT: power_state.NOSTATE,
    VCLOUD_STATUS.UNKNOWN: power_state.NOSTATE,
    VCLOUD_STATUS.UNRECOGNIZED: power_state.NOSTATE,
    VCLOUD_STATUS.POWERED_OFF: power_state.SHUTDOWN,
    VCLOUD_STATUS.INCONSISTENT_STATE: power_state.NOSTATE,
    VCLOUD_STATUS.MIXED: power_state.NOSTATE,
    VCLOUD_STATUS.DESCRIPTOR_PENDING: power_state.NOSTATE,
    VCLOUD_STATUS.COPYING_CONTENTS: power_state.NOSTATE,
    VCLOUD_STATUS.DISK_CONTENTS_PENDING: power_state.NOSTATE,
    VCLOUD_STATUS.QUARANTINED: power_state.NOSTATE,
    VCLOUD_STATUS.QUARANTINE_EXPIRED: power_state.NOSTATE,
    VCLOUD_STATUS.REJECTED: power_state.NOSTATE,
    VCLOUD_STATUS.TRANSFER_TIMEOUT: power_state.NOSTATE,
    VCLOUD_STATUS.VAPP_UNDEPLOYED: power_state.NOSTATE,
    VCLOUD_STATUS.VAPP_PARTIALLY_DEPLOYED: power_state.NOSTATE,
}


CONF = cfg.CONF
CONF.register_opts(vcloudapi_opts, 'vcloud')


LOG = logging.getLogger(__name__)


IMAGE_API = image.API()
  
def _retry_decorator(max_retry_count=-1, inc_sleep_time=10, max_sleep_time=60, exceptions=()):
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
                    retry_count, sleep_time = _sleep(retry_count, sleep_time)
            
            if max_retry_count != -1 and retry_count >= max_retry_count:
                LOG.error(_("func (%(name)s) exec failed since retry count (%(retry_count)d) reached max retry count (%(max_retry_count)d)."),
                                  {'name': func, 'retry_count': retry_count, 'max_retry_count': max_retry_count})
        return handle_args
    return handle_func

class VCloudDriver(fake_driver.FakeNovaDriver):
    """The VCloud host connection object."""

    def __init__(self, virtapi, scheme="https"):
        self._node_name = CONF.vcloud.vcloud_node_name
        self._vcloud_client = VCloudClient(scheme=scheme)
        self.cinder_api = cinder_api()

        if not os.path.exists(CONF.vcloud.vcloud_conversion_dir):
            os.makedirs(CONF.vcloud.vcloud_conversion_dir)

        if not os.path.exists(CONF.vcloud.vcloud_volumes_dir):
            os.makedirs(CONF.vcloud.vcloud_volumes_dir)

        self.hyper_agent_api = hyper_agent_api.HyperAgentAPI()
        super(VCloudDriver, self).__init__(virtapi)

        #begin test
        disk_refs = self._vcloud_client._invoke_api('get_diskRefs',
                                     self._vcloud_client._get_vcloud_vdc())
        print '---------------------------------------------------------------------------------------------'
        print '---------------------------------------------------------------------------------------------'
        for disk_ref in disk_refs:
            print vars(disk_ref)
        print '---------------------------------------------------------------------------------------------'
        print '---------------------------------------------------------------------------------------------'
        #end test

    def _update_vm_task_state(self, instance, task_state):
        instance.task_state = task_state
        instance.save()

    def _get_volume_ids_from_bdms(self, bdms):
        volume_ids = []
        for bdm in bdms:
            volume_ids.append(bdm['connection_info']['data']['volume_id'])
        return volume_ids

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        """Create VM instance."""
        LOG.debug(_("image meta is:%s") % image_meta)
        LOG.debug(_("instance is:%s") % instance)
        LOG.debug(_("network_info is %s") % network_info)
        LOG.debug(_("block_device_info is %s") % block_device_info)
        LOG.info('begin time of vcloud create vm is %s' % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())))
        
        if not instance.image_ref:
            self._spawn_from_volume(context, instance, image_meta, injected_files,
                                    admin_password, network_info, block_device_info)
        else:
            self._spawn_from_image(context, instance, image_meta, injected_files,
                                    admin_password, network_info, block_device_info)
        LOG.info('end time of vcloud create vm is %s' % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())))

    def _spawn_from_volume(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        bdms = block_device_info.get('block_device_mapping',[])
        volume_ids = self._get_volume_ids_from_bdms(bdms)
        root_volume_id = volume_ids[0]
        root_volume = self.cinder_api.get(context, root_volume_id)
        volume_image_metadata = root_volume['volume_image_metadata']

        rabbit_host = CONF.rabbit_host
        if 'localhost' in rabbit_host or '127.0.0.1' in rabbit_host:
            rabbit_host = CONF.rabbit_hosts[0]
        if ':' in rabbit_host:
            rabbit_host = rabbit_host[0:rabbit_host.find(':')]

        if volume_image_metadata['container_format'] == 'hybridvm':
            self._binding_host(context, network_info, instance.uuid)

            network_names = [CONF.vcloud.provider_tunnel_network_name, CONF.vcloud.provider_base_network_name]
            network_configs = self._vcloud_client.get_network_configs(network_names)

            vapp_name = self._get_vcloud_vapp_name(instance)
            vapp = self._vcloud_client.create_vapp(vapp_name, volume_image_metadata['image_id'], network_configs)

            # generate the network_connection
            network_connections = self._vcloud_client.get_network_connections(vapp, network_names)
            # update network
            self._vcloud_client.update_vms_connections(vapp, network_connections)
            
            # update vm specification
            self._vcloud_client.modify_vm_cpu(vapp, instance.get_flavor().vcpus)
            self._vcloud_client.modify_vm_memory(vapp, instance.get_flavor().memory_mb)


            for volume_id in volume_ids:
                volume = self.cinder_api.get(context, volume_id)
                volume_name = volume['display_name']
                vcloud_volume_name = self._get_vcloud_volume_name(volume_id, volume_name)
                result,disk_ref = self._vcloud_client.get_disk_ref(vcloud_volume_name)
                if result:
                    self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)
                else:
                    LOG.error('attach vm %s disk %s failed', vapp_name, vcloud_volume_name)
                
            # power on it
            self._vcloud_client.power_on_vapp(vapp_name)

            vapp_ip = self.get_vapp_ip(vapp_name)
            instance.metadata['vapp_ip'] = vapp_ip
            instance.metadata['is_hybrid_vm'] = True
            instance.save()

            self._wait_hybrid_service_up(vapp_ip, port = CONF.vcloud.hybrid_service_port)

            file_data = 'rabbit_userid=%s\nrabbit_password=%s\nrabbit_host=%s\n' % (CONF.rabbit_userid, CONF.rabbit_password, rabbit_host)
            file_data += 'host=%s\ntunnel_cidr=%s\nroute_gw=%s\n' % (instance.uuid,CONF.vcloud.tunnel_cidr,CONF.vcloud.route_gw)

            client = Client(vapp_ip, port = CONF.vcloud.hybrid_service_port)
            try:
                LOG.info("To inject file %s for instance %s", CONF.vcloud.dst_path, vapp_name)
                client.inject_file(CONF.vcloud.dst_path, file_data = file_data)
                LOG.info("To create container %s for instance %s", image_meta.get('name', ''), vapp_name)
                client.create_container(volume_image_metadata['image_name'])
                LOG.info("To start container network:%s, block_device_info:%s for instance %s", network_info, block_device_info, vapp_name)
                client.start_container(network_info=network_info, block_device_info=block_device_info)
            except (errors.NotFound, errors.APIError) as e:
                LOG.error("instance %s spawn from image failed, reason %s"%(vapp_name, e))
        
            # update port bind host
            self._binding_host(context, network_info, instance.uuid)
                        
        else:
            LOG.info("boot from volume for normal vm not supported!")
    def _spawn_from_image(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        image_cache_dir = CONF.vcloud.vcloud_conversion_dir

        if 'container_format' in image_meta and image_meta['container_format'] == 'hybridvm':
            is_hybrid_vm = True
    
        # update port bind host
        self._binding_host(context, network_info, instance.uuid)
    
        vapp_name = self._get_vcloud_vapp_name(instance)
    
        # 0.get vorg, user name,password vdc  from configuration file (only one
        # org)
    
        # 1.1 get image id, vm info ,flavor info
        # image_uuid = instance.image_ref
        if 'id' in image_meta:
            # create from image
            image_uuid = image_meta['id']
        else:
            # create from volume
            image_uuid = image_meta['properties']['image_id']
    
        #NOTE(nkapotoxin): create vapp with vapptemplate
        network_names = [CONF.vcloud.provider_tunnel_network_name, CONF.vcloud.provider_base_network_name]
        network_configs = self._vcloud_client.get_network_configs(network_names)
    
        # create vapp
        if is_hybrid_vm:
            vapp = self._vcloud_client.create_vapp(vapp_name, image_uuid , network_configs)
        else:
            vapp = self._vcloud_client.create_vapp(vapp_name,image_uuid , network_configs,
                                                   root_gb=instance.get_flavor().root_gb)
    
        # generate the network_connection
        network_connections = self._vcloud_client.get_network_connections(vapp, network_names)
    
        # update network
        self._vcloud_client.update_vms_connections(vapp, network_connections)
    
        # update vm specification
        self._vcloud_client.modify_vm_cpu(vapp, instance.get_flavor().vcpus)
        self._vcloud_client.modify_vm_memory(vapp, instance.get_flavor().memory_mb)
    
        rabbit_host = CONF.rabbit_host
        if 'localhost' in rabbit_host or '127.0.0.1' in rabbit_host:
            rabbit_host = CONF.rabbit_hosts[0]
        if ':' in rabbit_host:
            rabbit_host = rabbit_host[0:rabbit_host.find(':')]
        
        if not is_hybrid_vm:
            this_conversion_dir = '%s/%s' % (CONF.vcloud.vcloud_conversion_dir, 
                                             instance.uuid)
            fileutils.ensure_tree(this_conversion_dir)
            os.chdir(this_conversion_dir)
            #0: create metadata iso and upload to vcloud
            iso_file = common_tools.create_user_data_iso(
                "userdata.iso",
                {"rabbit_userid": CONF.rabbit_userid,
                 "rabbit_password": CONF.rabbit_password,
                 "rabbit_host": rabbit_host,
                 "host": instance.uuid,
                 "tunnel_cidr": CONF.vcloud.tunnel_cidr,
                 "route_gw": CONF.vcloud.route_gw},
                this_conversion_dir)
            metadata_iso = self._vcloud_client.upload_metadata_iso(iso_file,
                                                                   vapp_name)
            # mount it
            self._vcloud_client.insert_media(vapp_name, metadata_iso)
            
            # 7. clean up
            shutil.rmtree(this_conversion_dir, ignore_errors=True)
        else:
            if vapp_name.startswith('server@'):
                disk_name = 'Local@%s' % vapp_name[len('server@'):]
            else:
                disk_name = 'Local@%s' % vapp_name
            self._vcloud_client.create_volume(disk_name, instance.get_flavor().root_gb)
            result, disk_ref = self._vcloud_client.get_disk_ref(disk_name)
            if result:
                self._vcloud_client.attach_disk_to_vm(vapp_name, disk_ref)
            else:
                LOG.error(_('Unable to find volume %s to instance'),disk_name)

        # power on it
        self._vcloud_client.power_on_vapp(vapp_name)
    
        if is_hybrid_vm:
            vapp_ip = self.get_vapp_ip(vapp_name)
            #dhcp can changed?
            instance.metadata['vapp_ip'] = vapp_ip
            instance.metadata['is_hybrid_vm'] = True
            instance.save()

            port = CONF.vcloud.hybrid_service_port
            self._wait_hybrid_service_up(vapp_ip, port)

            file_data = 'rabbit_userid=%s\nrabbit_password=%s\nrabbit_host=%s\n' % (CONF.rabbit_userid, CONF.rabbit_password, rabbit_host)
            file_data += 'host=%s\ntunnel_cidr=%s\nroute_gw=%s\n' % (instance.uuid,CONF.vcloud.tunnel_cidr,CONF.vcloud.route_gw)

            client = Client(vapp_ip, port = port)
            try:
                LOG.info("To inject file %s for instance %s", CONF.vcloud.dst_path, vapp_name)
                client.inject_file(CONF.vcloud.dst_path, file_data = file_data)
                LOG.info("To create container %s for instance %s", image_meta.get('name', ''), vapp_name)
                client.create_container(image_meta.get('name', ''))
                LOG.info("To start container network:%s, block_device_info:%s for instance %s", network_info, block_device_info, vapp_name)
                client.start_container(network_info=network_info, block_device_info=block_device_info)
            except (errors.NotFound, errors.APIError) as e:
                LOG.error("instance %s spawn from image failed, reason %s"%(vapp_name, e))
 
        # update port bind host
        self._binding_host(context, network_info, instance.uuid)

    @staticmethod
    def _binding_host(context, network_info, host_id):
        neutron = neutronv2.get_client(context, admin=True)
        port_req_body = {'port': {'binding:host_id': host_id}}
        for vif in network_info:
            neutron.update_port(vif.get('id'), port_req_body)

    def _get_vcloud_vapp_name(self, instance):
        if CONF.vcloud.vcloud_vm_naming_rule == 'openstack_vm_id':
            return instance.uuid
        elif CONF.vcloud.vcloud_vm_naming_rule == 'openstack_vm_name':
            return instance.display_name
        elif CONF.vcloud.vcloud_vm_naming_rule == 'cascaded_openstack_rule':
            return instance.display_name
        else:
            return instance.uuid
    
    def _get_vcloud_volume_name(self, volume_id, volume_name):
        prefix = 'volume@'
        if volume_name.startswith(prefix):
            vcloud_volume_name = volume_name[len(prefix):]
        else:
            vcloud_volume_name = volume_id

        return vcloud_volume_name

    def _download_vmdk_from_vcloud(self, context, src_url, dst_file_name):

        # local_file_handle = open(dst_file_name, "wb")
        local_file_handle = fileutils.file_open(dst_file_name, "wb")

        remote_file_handle = urllib2.urlopen(src_url)
        file_size = remote_file_handle.headers['content-length']

        util.start_transfer(context, remote_file_handle, file_size,
                            write_file_handle=local_file_handle)

    def _upload_image_to_glance(
            self, context, src_file_name, image_id, instance):

        vm_task_state = instance.task_state
        file_size = os.path.getsize(src_file_name)
        read_file_handle = fileutils.file_open(src_file_name, "rb")

        metadata = IMAGE_API.get(context, image_id)

        # The properties and other fields that we need to set for the image.
        image_metadata = {"disk_format": "qcow2",
                          "is_public": "false",
                          "name": metadata['name'],
                          "status": "active",
                          "container_format": "bare",
                          "size": file_size,
                          "properties": {"owner_id": instance['project_id']}}

        util.start_transfer(context,
                            read_file_handle,
                            file_size,
                            image_id=metadata['id'],
                            image_meta=image_metadata,
                            task_state=task_states.IMAGE_UPLOADING,
                            instance=instance)
        self._update_vm_task_state(instance, task_state=vm_task_state)

    #TODO: test it
    def snapshot(self, context, instance, image_id, update_task_state):

        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)
        # 1. get vmdk url
        vapp_name = self._get_vcloud_vapp_name(instance)
        remote_vmdk_url = self._vcloud_client.query_vmdk_url(vapp_name)

        # 2. download vmdk
        temp_dir = '%s/%s' % (CONF.vcloud.vcloud_conversion_dir, instance.uuid)
        fileutils.ensure_tree(temp_dir)

        vmdk_name = remote_vmdk_url.split('/')[-1]
        local_file_name = '%s/%s' % (temp_dir, vmdk_name)

        self._download_vmdk_from_vcloud(
            context,
            remote_vmdk_url,
            local_file_name)

        # 3. convert vmdk to qcow2
        converted_file_name = temp_dir + '/converted-file.qcow2'
        convert_commond = "qemu-img convert -f %s -O %s %s %s" % \
            ('vmdk',
             'qcow2',
             local_file_name,
             converted_file_name)
        convert_result = subprocess.call([convert_commond], shell=True)

        if convert_result != 0:
            # do something, change metadata
            LOG.error('converting file failed')

        # 4. upload qcow2 to image repository\
        update_task_state(task_state=task_states.IMAGE_UPLOADING,
                          expected_state=task_states.IMAGE_PENDING_UPLOAD)

        self._upload_image_to_glance(
            context,
            converted_file_name,
            image_id,
            instance)

        # 5. delete temporary files
        shutil.rmtree(temp_dir, ignore_errors=True)

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        LOG.debug('[vcloud nova driver] begin reboot instance: %s' %
                  instance.uuid)
        if not instance.metadata.get('is_hybrid_vm', False):
            vapp_name = self._get_vcloud_vapp_name(instance)

            try:
                self._vcloud_client.reboot_vapp(vapp_name)
            except Exception as e:
                LOG.error('reboot instance %s failed, %s' % (vapp_name, e))
        else:
            vapp_ip = instance.metadata.get('vapp_ip', None)
            client = Client(vapp_ip, port = CONF.vcloud.hybrid_service_port)
            try:
                client.restart_container(network_info=network_info, block_device_info=block_device_info)
            except (errors.NotFound, errors.APIError) as e:
                LOG.error("reboot instance %s failed reason %s" % (vapp_name, e))

    def power_off(self, instance, shutdown_timeout=0, shutdown_attempts=0):
        LOG.debug('[vcloud nova driver] begin reboot instance: %s' %
                  instance.uuid)
        if instance.metadata.get('is_hybrid_vm', False):  
            vapp_ip = instance.metadata.get('vapp_ip', None)
            client = Client(vapp_ip, port = CONF.vcloud.hybrid_service_port)
            try:
                client.stop_container()
            except (errors.NotFound, errors.APIError) as e:
                LOG.error("power off instance %s failed, reason %s" % (vapp_name, e))

        vapp_name = self._get_vcloud_vapp_name(instance)
        try:
            self._vcloud_client.power_off_vapp(vapp_name)
        except Exception as e:
            LOG.error('power off failed, %s' % e)

    def power_on(self, context, instance, network_info, block_device_info):
        vapp_name = self._get_vcloud_vapp_name(instance)
        self._vcloud_client.power_on_vapp(vapp_name)
        
        if instance.metadata.get('is_hybrid_vm', False): 
            vapp_ip = instance.metadata.get('vapp_ip', None)
            self._wait_hybrid_service_up(vapp_ip, port = CONF.vcloud.hybrid_service_port)
            client = Client(vapp_ip, port = CONF.vcloud.hybrid_service_port)
            try:
                client.start_container(network_info = network_info, block_device_info = block_device_info)
            except (errors.NotFound, errors.APIError) as e:
                LOG.error("power on instance %s failed, reason %s" % (vapp_name, e))

    def _do_destroy_vm(self, context, instance, network_info, block_device_info=None,
                       destroy_disks=True, migrate_data=None):
        vapp_name = self._get_vcloud_vapp_name(instance)
        try:
            self._vcloud_client.power_off_vapp(vapp_name)
        except Exception as e:
            LOG.error('power off failed, %s' % e)

        vm_task_state = instance.task_state
        self._update_vm_task_state(instance, vm_task_state)

        if instance.metadata.get('is_hybrid_vm', False):
            if vapp_name.startswith('server@'):
                disk_name = 'Local@%s' % vapp_name[len('server@'):]
            else:
                disk_name = 'Local@%s' % vapp_name

            result, disk_ref = self._vcloud_client.get_disk_ref(disk_name)
            #spawn from image           
            if result:
                self._vcloud_client.detach_disk_from_vm(vapp_name, disk_ref)
                self._vcloud_client.delete_volume(disk_name)
        
        try:
            self._vcloud_client.delete_vapp(vapp_name)
        except Exception as e:
            LOG.error('delete vapp failed %s' % e)
        try:
            if not instance.metadata.get('is_hybrid_vm', False):
                self._vcloud_client.delete_metadata_iso(vapp_name)
        except Exception as e:
            LOG.error('delete metadata iso failed %s' % e)

    def destroy(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True, migrate_data=None):
        LOG.debug('[vcloud nova driver] destroy: %s' % instance.uuid)
        self._do_destroy_vm(context, instance, network_info, block_device_info,
                            destroy_disks, migrate_data)

        self.cleanup(context, instance, network_info, block_device_info,
                     destroy_disks, migrate_data)

        # delete agent
        instance_id = instance.uuid
        neutron_client = neutronv2.get_client(context=None, admin=True)
        agent = neutron_client.list_agents(host=instance_id)
        if len(agent['agents']) == 1:
            neutron_client.delete_agent(agent['agents'][0]['id'])

    def cleanup(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True, migrate_data=None, destroy_vifs=True):
        if destroy_vifs:
            self.unplug_vifs(instance, network_info)

        LOG.debug("Cleanup network finished", instance=instance)

    def attach_interface(self, instance, image_meta, vif):
        LOG.debug("attach interface: %s, %s" % (instance, vif))
        
        if instance.metadata.get('is_hybrid_vm'):
            client = Client(instance.metadata['vapp_ip'] , port = CONF.vcloud.hybrid_service_port)
            client.attach_interface(vif)

    def detach_interface(self, instance, vif):
        LOG.debug("detach interface: %s, %s" % (instance, vif))
        
        if instance.metadata.get('is_hybrid_vm'):
            client = Client(instance.metadata['vapp_ip'] , port = CONF.vcloud.hybrid_service_port)
            client.detach_interface(vif)

    def _get_vapp_ip(self, instance):
        instance_id = instance.uuid
        neutron_client = neutronv2.get_client(context=None, admin=True)
        agent = neutron_client.list_agents(host=instance_id)
        times=10
        while len(agent['agents']) == 0:
            if times==0:
                break
            time.sleep(10)
            agent = neutron_client.list_agents(host=instance_id)
            times = times - 1
        if times==0:
            return None
        else:
            return agent['agents'][0]['configurations']['tunneling_ip']

    @_retry_decorator(max_retry_count=10,exceptions = (errors.APIError,errors.NotFound))
    def get_vapp_ip(self, vapp_name):
        return self._vcloud_client.get_vapp_ip(vapp_name)

    def _attach_volume_iscsi(self, instance, connection_info):
        user = CONF.vcloud.image_user
        pwd = CONF.vcloud.image_password
        vapp_ip = self._get_vapp_ip(instance)
        if vapp_ip:
            host = vapp_ip
        else:
            LOG.error("vapp_ip is None ,attach volume failed")
            raise Exception(_("vapp_ip is None ,attach volume failed"))

        ssh_client = sshclient.SSH(user, host, password=pwd)

        target_iqn = connection_info['data']['target_iqn']
        target_portal = connection_info['data']['target_portal']
        cmd1 = "sudo iscsiadm -m node -T %s -p %s" % (target_iqn, target_portal)
        while True:
            try:
                cmd1_status, cmd1_out, cmd1_err = ssh_client.execute(cmd1)
                LOG.debug("sudo cmd1 info status=%s ,out=%s, err=%s " % (cmd1_status, cmd1_out, cmd1_err))
                if cmd1_status in [21, 255]:
                    cmd2 = "sudo iscsiadm -m node -T %s -p %s --op new" % (target_iqn, target_portal)
                    cmd2_status, cmd2_out, cmd2_err = ssh_client.execute(cmd2)
                    LOG.debug("sudo cmd2 info status=%s ,out=%s, err=%s " % (cmd2_status, cmd2_out, cmd2_err))
                break
            except sshclient.SSHError:
                LOG.debug("wait for vm to initialize network")
                time.sleep(5)

        cmd3 = "sudo iscsiadm -m session"
        cmd3_status, cmd3_out, cmd3_err = ssh_client.execute(cmd3)
        portals = [{'portal': p.split(" ")[2], 'iqn': p.split(" ")[3]}
                   for p in cmd3_out.splitlines() if p.startswith("tcp:")]
        stripped_portal = connection_info['data']['target_portal'].split(",")[0]
        if len(portals) == 0 or len([s for s in portals
                                     if stripped_portal ==
                                     s['portal'].split(",")[0]
                                     and
                                     s['iqn'] ==
                                     connection_info['data']['target_iqn']]
                                    ) == 0:
            cmd4 = "sudo iscsiadm -m node -T %s -p %s --login" % (target_iqn, target_portal)
            cmd4_status, cmd4_out, cmd4_err = ssh_client.execute(cmd4)
            LOG.debug("sudo cmd4 info status=%s ,out=%s, err=%s " % (cmd4_status, cmd4_out, cmd4_err))
            cmd5 = "sudo iscsiadm -m node -T %s -p %s --op update -n node.startup  -v automatic" % \
                   (target_iqn, target_portal)
            cmd5_status, cmd5_out, cmd5_err = ssh_client.execute(cmd5)
            LOG.debug("sudo cmd5 info status=%s ,out=%s, err=%s " % (cmd5_status, cmd5_out, cmd5_err))
        ssh_client.close()

    def attach_volume(self, context, connection_info, instance, mountpoint,
                      disk_bus=None, device_type=None, encryption=None):
        """Attach volume storage to VM instance."""
        instance_name = instance['display_name']
        LOG.debug("Attach_volume: %(connection_info)s to %(instance_name)s",
                  {'connection_info': connection_info,
                   'instance_name': instance_name})
        volume_id = connection_info['data']['volume_id']
        driver_type = connection_info['driver_volume_type']

        volume = self.cinder_api.get(context, volume_id)
        volume_name = volume['display_name']
        # use volume_name as vcloud disk name, remove prefix str `volume@`
        # if volume_name does not start with volume@, then use volume id instead
        vcloud_volume_name = self._get_vcloud_volume_name(volume_id,  volume_name)

        # find volume reference by it's name
        vapp_name = self._get_vcloud_vapp_name(instance)
        if driver_type == 'iscsi':
            self._attach_volume_iscsi(instance, connection_info)
            return

        result, resp = self._vcloud_client.get_disk_ref(vcloud_volume_name)
        if result:
            LOG.debug("Find volume successful, disk name is: %(disk_name)s"
                      "disk ref's href is: %(disk_href)s.",
                      {'disk_name': vcloud_volume_name,
                       'disk_href': resp.href})

            if self._vcloud_client.attach_disk_to_vm(vapp_name, resp):
                LOG.info("Volume %(volume_name)s attached to: %(instance_name)s",
                         {'volume_name': vcloud_volume_name,
                          'instance_name': instance_name})

        else:
            LOG.error(_('Unable to find volume %s to instance'),  vcloud_volume_name)



    def _detach_volume_iscsi(self, instance, connection_info):
        user = CONF.vcloud.image_user
        pwd = CONF.vcloud.image_password
        vapp_ip = self._get_vapp_ip(instance)
        if vapp_ip:
            host = vapp_ip
        else:
            LOG.debug("vapp_ip is None ,attach volume failed")
            raise

        ssh_client = sshclient.SSH(user, host, password=pwd)
        target_iqn = connection_info['data']['target_iqn']
        target_portal = connection_info['data']['target_portal']
        cmd1 = "ls -l /dev/disk/by-path/ | grep %s | awk -F '/' '{print $NF}'" % target_iqn
        cmd1_status, cmd1_out, cmd1_err = ssh_client.execute(cmd1)
        LOG.debug(" cmd1 info status=%s ,out=%s, err=%s " % (cmd1_status, cmd1_out, cmd1_err))
        device = "/dev/" + cmd1_out.split('\n')[0]
        path = "/sys/block/" + cmd1_out.split('\n')[0] + "/device/delete"
        cmd2 = "sudo blockdev --flushbufs %s" % device
        cmd2_status, cmd2_out, cmd2_err = ssh_client.execute(cmd2)
        LOG.debug(" cmd2 info status=%s ,out=%s, err=%s " % (cmd2_status, cmd2_out, cmd2_err))
        cmd3 = "echo 1 | sudo tee -a %s" % path
        cmd3_status, cmd3_out, cmd3_err = ssh_client.execute(cmd3)
        LOG.debug("sudo cmd3 info status=%s ,out=%s, err=%s " % (cmd3_status, cmd3_out, cmd3_err))
        cmd4 = "sudo iscsiadm -m node -T %s -p %s --op update -n node.startup  -v manual" % (target_iqn, target_portal)
        cmd4_status, cmd4_out, cmd4_err = ssh_client.execute(cmd4)
        LOG.debug("sudo cmd4 info status=%s ,out=%s, err=%s " % (cmd4_status, cmd4_out, cmd4_err))
        cmd5 = "sudo iscsiadm -m node -T %s -p %s --logout" % (target_iqn, target_portal)
        cmd5_status, cmd5_out, cmd5_err = ssh_client.execute(cmd5)
        LOG.debug("sudo cmd5 info status=%s ,out=%s, err=%s " % (cmd5_status, cmd5_out, cmd5_err))
        cmd6 = "sudo iscsiadm -m node -T %s -p %s --op delete" % (target_iqn, target_portal)
        cmd6_status, cmd6_out, cmd6_err = ssh_client.execute(cmd6)
        LOG.debug("sudo cmd6 info status=%s ,out=%s, err=%s " % (cmd6_status, cmd6_out, cmd6_err))

    def detach_volume(self, connection_info, instance, mountpoint,
                      encryption=None):
        """Detach the disk attached to the instance."""
        instance_name = instance['display_name']
        LOG.debug("Detach_volume: %(connection_info)s to %(instance_name)s",
                  {'connection_info': connection_info,
                   'instance_name': instance_name})
        volume_id = connection_info['data']['volume_id']
        driver_type = connection_info['driver_volume_type']

        if driver_type == 'iscsi':
            self._detach_volume_iscsi(instance, connection_info)
            return

        volume_name = connection_info['data']['display_name']

        # use volume_name as vcloud disk name, remove prefix str `volume@`
        # if volume_name does not start with volume@, then use volume id instead
        vcloud_volume_name = self._get_vcloud_volume_name(volume_id,
                                                          volume_name)
        # find volume reference by it's name
        vapp_name = self._get_vcloud_vapp_name(instance)

        #if driver_type == 'iscsi':
        #    self._detach_volume_iscsi(instance, connection_info)
        #    return

        result, resp = self._vcloud_client.get_disk_ref(vcloud_volume_name)
        if result:
            LOG.debug("Find volume successful, disk name is: %(disk_name)s"
                      "disk ref's href is: %(disk_href)s.",
                      {'disk_name': vcloud_volume_name,
                       'disk_href': resp.href})
        else:
            LOG.error(_('Unable to find volume from instance %s'),
                      vcloud_volume_name)

        if self._vcloud_client.detach_disk_from_vm(vapp_name, resp):
            LOG.info("Volume %(volume_name)s detached from: %(instance_name)s",
                     {'volume_name': vcloud_volume_name,
                      'instance_name': instance_name})

    def get_info(self, instance):
        state = power_state.NOSTATE
        try:
            vapp_name = self._get_vcloud_vapp_name(instance)
            vapp_status = self._vcloud_client.get_vcloud_vapp_status(vapp_name)
            state = status_dict_vapp_to_instance.get(vapp_status)
        except Exception as e:
            LOG.info('can not find the vapp %s' % e)

        return {'state': state,
                'max_mem': 0,
                'mem': 0,
                'num_cpu': 1,
                'cpu_time': 0}

    def get_available_nodes(self, refresh=False):
        return [self._node_name]

    def plug_vifs(self, instance, network_info):
        LOG.debug("plug_vifs")
        # TODO: retrieve provider info ips/macs for vcloud
        for vif in network_info:
            self.hyper_agent_api.plug(instance.uuid, vif, None)

    def unplug_vifs(self, instance, network_info):
        LOG.debug("unplug_vifs")
        for vif in network_info:
            self.hyper_agent_api.unplug(instance.uuid, vif)

    @_retry_decorator(max_retry_count=10,exceptions=(errors.APIError,errors.NotFound, errors.ConnectionError, errors.InternalError))
    def _wait_hybrid_service_up(self, server_ip, port = '7127'):
        client = Client(server_ip, port = port)
        return client.get_version()
