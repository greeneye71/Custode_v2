FROM registry.fedoraproject.org/fedora:42

# Python 3.13, la stessa versione con cui è generato il lock.
RUN dnf install -y python3 python3-pip \
    && dnf clean all

WORKDIR /app

# Prima solo il lock: il layer delle dipendenze resta in cache finché non cambia.
# pystray serve solo al launcher di Windows e su Linux chiede python-xlib.
# --no-deps: si installano le versioni bloccate, pip non risolve nulla;
# pip check fa fallire la build se una dipendenza manca davvero.
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN grep -vi '^pystray==' /tmp/requirements.lock.txt > /tmp/lock-container.txt \
    && python3 -m pip install --no-cache-dir --no-deps -r /tmp/lock-container.txt \
    && python3 -m pip check \
    && rm /tmp/requirements.lock.txt /tmp/lock-container.txt

COPY . /app
RUN chmod +x deploy/podman/custode deploy/podman/configura.sh \
    && mkdir -p /stato

# Tutto lo stato modificabile vive qui (percorsi.py): volume e backup del Quadlet.
ENV MEDINVENTORY_RADICE=/stato \
    PYTHONUNBUFFERED=1

EXPOSE 5000
# Nessun punto di ingresso fisso: il comando lo decide il Quadlet (o il wrapper `custode`).
