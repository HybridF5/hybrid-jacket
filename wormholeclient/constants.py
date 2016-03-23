DEFAULT_HYPERVM_API_VERSION = '1.0'
DEFAULT_TIMEOUT_SECONDS = 60
STREAM_HEADER_SIZE_BYTES = 8

TASK_DOING = 0
TASK_SUCCESS = 1
TASK_ERROR = 2

CONTAINER_LIMITS_KEYS = [
    'memory', 'memswap', 'cpushares', 'cpusetcpus'
]

INSECURE_REGISTRY_DEPRECATION_WARNING = \
    'The `insecure_registry` argument to {} ' \
    'is deprecated and non-functional. Please remove it.'
