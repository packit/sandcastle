# This is the default sandbox container image to run untrusted commands.
# It should contain basic utilities, such as git, make, rpmbuild, etc.
FROM docker.io/usercont/base

ENV LC_ALL=C \
    PYTHONDONTWRITEBYTECODE=yes \
    WORKDIR=/src

WORKDIR ${WORKDIR}

COPY files/install-rpm-packages.yaml /src/files/install-rpm-packages.yaml
RUN ansible-playbook -vv -c local -t basic-image -i localhost, files/install-rpm-packages.yaml \
    && dnf clean all

COPY files/container-cmd.sh /src/
# default command is sleep - so users can do .exec(command=[...])
CMD ["/src/container-cmd.sh"]
