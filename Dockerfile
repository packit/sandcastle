# This is the default sandbox container image.
# It should contain basic utilities, such as git, make, rpmbuild, etc.
# Untrusted commands are meant to run in this container.
FROM registry.fedoraproject.org/fedora:30

# ANSIBLE_STDOUT_CALLBACK - nicer output from the playbook run
ENV LANG=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=yes \
    WORKDIR=/src \
    ANSIBLE_STDOUT_CALLBACK=debug

WORKDIR ${WORKDIR}

RUN dnf install -y ansible
# Ansible doesn't like /tmp
COPY files/install-rpm-packages.yaml /src/files/install-rpm-packages.yaml

RUN cd /src/ \
    && ansible-playbook -vv -c local -i localhost, files/install-rpm-packages.yaml
    && dnf clean all

COPY files/container-cmd.sh /src/
# default command is sleep - so users can do .exec(command=[...])
CMD ["/src/container-cmd.sh"]
