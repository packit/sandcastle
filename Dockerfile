# This is the default sandbox container image to run untrusted commands.
# It should contain basic utilities, such as git, make, rpmbuild, etc.
FROM registry.fedoraproject.org/fedora:30

# ANSIBLE_STDOUT_CALLBACK - nicer output from the playbook run
ENV LANG=en_US.UTF-8 \
    LC_ALL=C \
    PYTHONDONTWRITEBYTECODE=yes \
    WORKDIR=/src \
    ANSIBLE_PYTHON_INTERPRETER=/usr/bin/python3 \
    ANSIBLE_STDOUT_CALLBACK=debug

WORKDIR ${WORKDIR}

RUN dnf install -y ansible

COPY files/install-rpm-packages.yaml /src/files/install-rpm-packages.yaml
RUN ansible-playbook -vv -c local -t basic-image -i localhost, files/install-rpm-packages.yaml \
    && dnf clean all

COPY files/container-cmd.sh /src/
# default command is sleep - so users can do .exec(command=[...])
CMD ["/src/container-cmd.sh"]
