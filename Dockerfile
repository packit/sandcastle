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

RUN dnf install -y ansible && dnf clean all

COPY files/install-rpm-packages.yaml /src/files/install-rpm-packages.yaml
COPY tests/requirements.txt /src/tests/requirements.txt

# assert
RUN ls tests/requirements.txt

RUN ansible-playbook -vv -c local -t basic-image -i localhost, files/install-rpm-packages.yaml \
    && dnf clean all

COPY files/container-cmd.sh /src/
# default command is sleep - so users can do .exec(command=[...])
CMD ["/src/container-cmd.sh"]
