FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git jq libicu70 libssl3 tar gzip sudo xz-utils \
      python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.1.1.tgz \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker

# CLI used by CI Gate / Deploy (avoid first-job install race).
RUN mkdir -p /usr/local/bin \
    && curl -fsSL "https://dl.k8s.io/release/v1.29.5/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && curl -fsSL https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh \
      | USE_SUDO=false K3D_INSTALL_DIR=/usr/local/bin bash \
    && curl -fsSL "https://github.com/cli/cli/releases/download/v2.67.0/gh_2.67.0_linux_amd64.tar.gz" \
      | tar -xz -C /tmp \
    && mv /tmp/gh_2.67.0_linux_amd64/bin/gh /usr/local/bin/gh \
    && kubectl version --client && k3d version && gh --version

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
