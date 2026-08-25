# Prodavan Actions runner for Docker Desktop — Python 3.12 (api/ops require >=3.12)
FROM myoung34/github-runner:ubuntu-jammy

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common ca-certificates curl \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.12 \
      python3.12-venv \
      python3.12-dev \
      python3-pip \
      xz-utils \
      jq \
 && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
 && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
 && curl -fsSL \
      "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz" \
    | tar xz -C /usr/local/bin \
 && python3 --version \
 && kustomize version \
 && mkdir -p /cache \
 && chmod 1777 /cache \
 && rm -rf /var/lib/apt/lists/*
