#!/bin/bash

set -x

source ./setup_env_in_openshift.sh

# 30 minutes is a default timeout for the sandbox pod
sleep 1800
