FROM python:3.12-slim

# docker CLI + compose plugin (talks to the host engine via the mounted socket)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git openssh-client procps nano less \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/helmsman
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY web ./web

# `pocketadm` for the coding agents (Claude Code, Codex, Vibe): PocketADM's
# records, the assistant's notes, and running a command on the host
RUN printf '#!/bin/sh\nPYTHONPATH=/opt/helmsman exec python -m server.cli "$@"\n' \
        > /usr/local/bin/pocketadm && chmod 0755 /usr/local/bin/pocketadm

ENV HELMSMAN_DATA=/data
VOLUME /data
# 8080: plain HTTP for a reverse proxy on the same host (and the loopback port)
# 8443: HTTPS with a self-signed key whose fingerprint is in the pairing QR
EXPOSE 8080 8443

CMD ["python", "-m", "server.run"]
