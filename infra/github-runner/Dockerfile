FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git jq libicu70 libssl3 tar gzip sudo python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/actions-runner
# Host runner is canonical — this image is not used in the happy path.
CMD ["python3", "-c", "raise SystemExit('Use Kali host runner; see infra/github-runner/README.md')"]
