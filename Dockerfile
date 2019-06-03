# This image is going to be used by packit-service
# for running 'actions' used by packit.
# Once packit service receives a webhook or a fedmsg
# then if packit.yaml configuration file in upstream
# repo contains some actions, then this image is run
# and actions are executed in alone POD.
# The aim of this image is to secure running actions in another POD
# so we will not destroy our packit-service instances.
FROM registry.fedoraproject.org/fedora:30

ENV LANG=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=yes \
    HOME=/tmp/packit-generator

COPY requirements.sh files/packit-run.sh ${HOME}/

WORKDIR ${HOME}

RUN bash requirements.sh && \
    dnf clean all

COPY . ${HOME}

RUN pip3 install .

# Run this image with sleep 3600.
# Packit service calls actions by `oc exec` inside this image
# so we secure source generation.
CMD ["/tmp/packit-generator/packit-run.sh"]
