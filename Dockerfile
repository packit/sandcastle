FROM registry.fedoraproject.org/fedora:30

ENV LANG=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=yes \
    HOME=/tmp/packit-generator

COPY requirements.txt requirements.sh files/packit-run.sh /tmp/packit-generator/

WORKDIR ${HOME}

RUN bash requirements.sh && \
    dnf clean all

COPY . ${HOME}

RUN pip3 install .

CMD ["/tmp/packit-generator/packit-run.sh"]
