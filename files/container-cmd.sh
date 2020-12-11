#!/bin/bash

set -x

source ./setup_env_in_openshift.sh

# 10 minutes is a default timeout for the sandbox pod
sleep 600
