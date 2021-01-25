# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

# wait for this long for a websocket call to finish
WEBSOCKET_CALL_TIMEOUT = 30.0  # seconds

# try to initiate WS Client 5 times
RETRY_INIT_WS_CLIENT_COUNTER = 5

# when calling k8s' API to create a pod, we may get an error that
# resources are not available at that moment (timebound quota) and may
# be later - so let's retry this many times
RETRY_CREATE_POD_COUNTER = 6
