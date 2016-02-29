def parse_host(addr, platform=None, tls=False):
    return addr.strip()

def create_container_config(image_name):
    return {
        'image_name':image_name
    }

def restart_container_config(network_info, block_device_info):
    return {
        'network_info':network_info,
        'block_device_info':block_device_info
    }

def start_container_config(network_info, block_device_info):
    return {
        'network_info':network_info,
        'block_device_info':block_device_info
    }

def network_service_config(rabbit_user_id, rabbit_passwd, rabbit_host, rabbit_port):
    return {
        'rabbit_user_id':rabbit_user_id,
        'rabbit_passwd':rabbit_passwd,
        'rabbit_host':rabbit_host,
        'rabbit_port':rabbit_port
    }