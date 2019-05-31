FROM registry.fedoraproject.org/fedora:30

ENV LANG=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=yes \
    HOME=/tmp/packit-generator

COPY requirements.sh files/packit-run.sh ${HOME}

WORKDIR ${HOME}

RUN bash requirements.sh && \
    dnf clean all

COPY . ${HOME}

RUN pip3 install .

CMD ["/tmp/packit-generator/packit-run.sh"]
