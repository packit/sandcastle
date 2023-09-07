# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

# wait for this long for a websocket call to finish
WEBSOCKET_CALL_TIMEOUT = 30.0  # seconds

# try to initiate WS Client 5 times
RETRY_INIT_WS_CLIENT_MAX = 5

# when calling k8s' API to create a pod, we may get an error that
# resources are not available at that moment (timebound quota) and may
# be later - so let's retry this many times
RETRY_CREATE_POD_MAX = 8

# a mapped sub-dir where the script will be copyied before
# being executed
SANDCASTLE_EXEC_DIR = "sandcastle-exec-dir"
