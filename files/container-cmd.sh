#!/bin/bash

set -x

source ./setup_env_in_openshift.sh

# [WARN] When adjusting, don't forget the timeout in spec (sandcastle/api.py)
# 30 minutes is a default timeout for the sandbox pod
sleep 1800
