import base64


def parse_host(host_ip, port, scheme):
    return '{0}://{1}:{2}'.format(scheme, host_ip, port)


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


def inject_file_config(dst_path, src_path, file_data):
    import pdb;pdb.set_trace()
    if src_path:
        with open(src_path) as f:
            data = f.read()
        encoded = base64.b64encode(data)
    elif file_data:
        encoded = base64.b64encode(file_data)

    return {
        'dst_path': dst_path,
        'file_data': encoded
    }
