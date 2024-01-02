# This is the default sandbox container image to run untrusted commands.
# It should contain basic utilities, such as git, make, rpmbuild, etc.
FROM quay.io/packit/base:c9s

ENV PYTHONDONTWRITEBYTECODE=yes \
    USER=sandcastle \
    HOME=/home/sandcastle

COPY files/install-rpm-packages.yaml ./
RUN ansible-playbook -vv -c local -t basic-image -i localhost, install-rpm-packages.yaml \
    && dnf clean all

WORKDIR ${HOME}
# So the arbitrary user ID can access it.
RUN chmod g+rwX .

COPY files/container-cmd.sh files/setup_env_in_openshift.sh ./

# wipe the mess from Ansible and Python…
RUN rm -rf ./.cache ./.ansible

# default command is sleep - so users can do .exec(command=[...])
CMD ["./container-cmd.sh"]
