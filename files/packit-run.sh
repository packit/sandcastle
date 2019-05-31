#!/bin/bash

# We need to have POD running for an hour
# Each packit actions is called from packit-service
# As soon as actions from packit-service are done
# POD is automatically deleted
sleep 3600
