# This is the default sandbox container image to run untrusted commands.
# It should contain basic utilities, such as git, make, rpmbuild, etc.
FROM quay.io/packit/base

ENV LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=yes \
    USER=sandcastle \
    HOME=/home/sandcastle

WORKDIR ${HOME}
# So the arbitrary user ID can access it.
RUN chmod g+rwX .

COPY files/install-rpm-packages.yaml ./
RUN ansible-playbook -vv -c local -t basic-image -i localhost, install-rpm-packages.yaml \
    && dnf clean all

COPY files/container-cmd.sh files/setup_env_in_openshift.sh ./
# default command is sleep - so users can do .exec(command=[...])
CMD ["./container-cmd.sh"]
