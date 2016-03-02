import base64


def parse_host(addr, platform=None, tls=False):
    return addr.strip()


def create_container_config(image_name):
    return {
        'image_name': image_name
    }


def restart_container_config(network_info, block_device_info):
    return {
        'network_info': network_info,
        'block_device_info': block_device_info
    }


def start_container_config(network_info, block_device_info):
    return {
        'network_info': network_info,
        'block_device_info': block_device_info
    }


def inject_file_config(dst_path, src_path):
    with open(src_path) as f:
        data = f.read()
    encoded = base64.b64encode(data)
    return {
        'dst_path': dst_path,
        'file_data': encoded
    }