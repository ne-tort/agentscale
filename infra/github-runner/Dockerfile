FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git jq libicu70 libssl3 tar gzip sudo \
      python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.1.1.tgz \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker

RUN useradd -m -u 1001 runner \
    && usermod -aG sudo runner \
    && echo "runner ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && mkdir -p /opt/actions-runner \
    && chown -R runner:runner /opt/actions-runner

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Entrypoint runs as root to fix docker.sock GID, then drops to `runner`.
USER root
VOLUME ["/opt/actions-runner"]
WORKDIR /opt/actions-runner
ENTRYPOINT ["/entrypoint.sh"]
