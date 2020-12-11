#!/usr/bin/bash

set -x

# Generate passwd file based on current uid, needed for fedpkg
grep -v ^sandcastle /etc/passwd > "${HOME}/passwd"
printf "sandcastle:x:$(id -u):0:Sandcastle:${HOME}:/bin/bash\n" >> "${HOME}/passwd"
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD="${HOME}/passwd"
export NSS_WRAPPER_GROUP=/etc/group
