#!/usr/bin/env python3
"""CI VM base setup: docker engine, tools, users, /cache, hosts-entry updater.

Run as root: sudo python3 01-base.py
(No .sh under infra/ — repo contract; this file is host-bootstrap, not GitOps.)
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def sh(cmd: str) -> None:
    print(f"+ {cmd}")
    subprocess.run(["bash", "-c", cmd], check=True)


def run(args: list[str]) -> None:
    print("+", " ".join(args))
    subprocess.run(args, check=True)


def main() -> None:
    print("==> apt base packages")
    sh(
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
        "ca-certificates curl gnupg jq xz-utils git unzip tar "
        "python3 python3-venv python3-pip"
    )

    print("==> docker engine (official apt repo)")
    sh(
        "install -m 0755 -d /etc/apt/keyrings && "
        "curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && "
        "chmod a+r /etc/apt/keyrings/docker.asc"
    )
    codename = subprocess.run(
        ["bash", "-c", ". /etc/os-release && echo $VERSION_CODENAME"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    sh(
        f'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] '
        f'https://download.docker.com/linux/ubuntu {codename} stable" '
        f"> /etc/apt/sources.list.d/docker.list"
    )
    sh(
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
        "docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    )

    print("==> docker daemon: log rotation")
    Path("/etc/docker").mkdir(parents=True, exist_ok=True)
    Path("/etc/docker/daemon.json").write_text(
        '{\n  "log-driver": "json-file",\n  "log-opts": { "max-size": "50m", "max-file": "3" }\n}\n',
        encoding="utf-8",
    )
    sh("systemctl enable --now docker")

    print("==> runner user + groups")
    sh("id runner >/dev/null 2>&1 || useradd -m -s /bin/bash runner")
    sh("usermod -aG docker runner")
    sh("usermod -aG docker www || true")

    print("==> /cache (shared CI cache: flutter sdk, pub, pip, poetry, tools)")
    for sub in ("pub", "pip", "poetry", "poetry-cli", "poetry-venvs",
                "kustomize", "kubectl", "toolcache", "xdg"):
        Path(f"/cache/{sub}").mkdir(parents=True, exist_ok=True)
    sh("chown -R runner:runner /cache")

    print("==> pinned CLI tools (versions pinned by CI workflows)")
    sh(
        "curl -fsSL -o /tmp/kubectl https://dl.k8s.io/release/v1.31.4/bin/linux/amd64/kubectl && "
        "install -m 0755 /tmp/kubectl /usr/local/bin/kubectl"
    )
    sh(
        "curl -fsSL -o /tmp/kustomize.tgz "
        "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.3/"
        "kustomize_v5.4.3_linux_amd64.tar.gz && "
        "tar xzf /tmp/kustomize.tgz -C /tmp && install -m 0755 /tmp/kustomize /usr/local/bin/kustomize"
    )
    sh(
        "curl -fsSL -o /tmp/gh.tgz "
        "https://github.com/cli/cli/releases/download/v2.67.0/gh_2.67.0_linux_amd64.tar.gz && "
        "tar xzf /tmp/gh.tgz -C /tmp && "
        "install -m 0755 /tmp/gh_2.67.0_linux_amd64/bin/gh /usr/local/bin/gh"
    )
    sh("kubectl version --client >/dev/null && kustomize version && gh --version | head -1")

    print("==> buildkitd GC policy (persistent buildx builder cache cap 50Gi)")
    Path("/etc/buildkit").mkdir(parents=True, exist_ok=True)
    Path("/etc/buildkit/buildkitd.toml").write_text(
        Path(__file__).with_name("buildkitd.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    sh("chmod 644 /etc/buildkit/buildkitd.toml")

    print("==> host.docker.internal -> Windows host (default gateway) updater")
    Path("/usr/local/sbin").mkdir(parents=True, exist_ok=True)
    Path("/usr/local/sbin/ci-hosts-update.py").write_text(textwrap.dedent(
        """
        #!/usr/bin/env python3
        # Keep /etc/hosts entry for host.docker.internal pointing at the Windows
        # host (the VM's default gateway). Survives Windows reboots that rotate
        # the Hyper-V Default Switch subnet while the VM stays on-link.
        import re, subprocess
        from pathlib import Path
        gw = subprocess.run(
            ["bash", "-c", "ip route show default | awk '{print $3; exit}'"],
            capture_output=True, text=True,
        ).stdout.strip()
        if not gw:
            raise SystemExit(0)
        hosts = Path("/etc/hosts")
        text = hosts.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^.*host\\.docker\\.internal.*\\n?", "", text)
        if not text.endswith("\\n"):
            text += "\\n"
        hosts.write_text(text + f"{gw} host.docker.internal\\n", encoding="utf-8")
        print(f"hosts: host.docker.internal -> {gw}")
        """
    ).lstrip(), encoding="utf-8")
    sh("chmod 755 /usr/local/sbin/ci-hosts-update.py && /usr/local/sbin/ci-hosts-update.py")

    Path("/etc/systemd/system/ci-hosts-update.service").write_text(
        "[Unit]\n"
        "Description=Update host.docker.internal hosts entry to current gateway\n"
        "Wants=network-online.target\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/local/sbin/ci-hosts-update.py\n",
        encoding="utf-8",
    )
    Path("/etc/systemd/system/ci-hosts-update.timer").write_text(
        "[Unit]\n"
        "Description=Refresh host.docker.internal gateway mapping\n\n"
        "[Timer]\n"
        "OnBootSec=30\n"
        "OnUnitActiveSec=10min\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n",
        encoding="utf-8",
    )
    sh("systemctl daemon-reload && systemctl enable --now ci-hosts-update.timer")

    print("==> CI docker network for bridge e2e (matches PRODAVAN_E2E_DOCKER_NETWORK)")
    sh("docker network inspect prodavan-runners >/dev/null 2>&1 || docker network create prodavan-runners")

    print("==> verify docker as runner user")
    sh("sudo -u runner docker version --format '{{.Server.Version}}'")
    sh("sudo -u runner docker network inspect prodavan-runners --format '{{.Name}} ok'")

    print("BASE SETUP DONE")


if __name__ == "__main__":
    import os

    if os.geteuid() != 0:
        sys.exit("run as root: sudo python3 01-base.py")
    main()
