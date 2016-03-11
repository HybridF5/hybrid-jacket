import sys

from hyperservice import config
from hyperservice.common import log as logging
from hyperservice import service

def main(servername):
    config.parse_args(sys.argv)
    logging.setup("hyperservice")
     

    launcher = service.process_launcher()
    server = service.WSGIService(servername, use_ssl=False,
                                         max_url_len=16384)
    launcher.launch_service(server, workers=server.workers or 1)
    launcher.wait()
    
