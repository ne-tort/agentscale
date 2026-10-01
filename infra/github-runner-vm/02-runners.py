#!/usr/bin/env python3
"""Install GitHub Actions runners on the CI VM as systemd services.

Prereq: 01-base.py done; /tmp/runner_tokens.env contains
    RUNNER_TOKEN_AS=<registration token ne-tort/agentscale>
    RUNNER_TOKEN_CLAW=<registration token ne-tort/prodavan-claw>
and /tmp/prodavan-dev.yaml is the Windows-host kubeconfig.
Run as root: sudo python3 02-runners.py

Idempotent: runners whose service is already active are skipped; broken ones
are removed (--token) and re-registered (--replace).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


def sh(cmd: str) -> subprocess.CompletedProcess:
    print(f"+ {cmd}")
    return subprocess.run(["bash", "-c", cmd], check=True)


def unit_for(name: str) -> str:
    return f"actions.runner.*.{name}.service"


def service_active(name: str) -> str | None:
    units = sorted(Path("/etc/systemd/system").glob(unit_for(name)))
    if not units:
        return None
    unit = units[0]
    res = subprocess.run(
        ["systemctl", "is-active", "--quiet", unit.name], check=False
    )
    return unit.name if res.returncode == 0 else None


def install_runner(repo_url: str, name: str, token: str) -> None:
    print(f"==> {name} -> {repo_url}")
    existing = service_active(name)
    if existing:
        print(f"    {name}: service already active ({existing}) — skip")
        return

    d = Path(f"/opt/actions-runners/{name}")
    d.mkdir(parents=True, exist_ok=True)
    sh(f"tar xzf /tmp/actions-runner.tar.gz -C {d}")
    sh(f"chown -R runner:runner {d}")
    if (d / ".runner").exists():
        sh(f"cd {d} && ./svc.sh uninstall 2>/dev/null || true")
        sh(
            f"cd {d} && sudo -u runner ./config.sh remove --token '{token}' 2>/dev/null || true"
        )
    sh(
        f"cd {d} && sudo -u runner ./config.sh --unattended "
        f"--url {repo_url} --token '{token}' "
        f"--name {name} --labels self-hosted,linux,docker,ci-vm --replace"
    )
    # svc.sh install [user]: unit name auto-derived (actions.runner.<slug>.<name>)
    sh(f"cd {d} && ./svc.sh install runner")
    units = sorted(Path("/etc/systemd/system").glob(unit_for(name)))
    if not units:
        sys.exit(f"service unit not found for {name}")
    unit = units[0]
    dropin = Path(f"{unit}.d")
    dropin.mkdir(exist_ok=True)
    (dropin / "ci.conf").write_text(
        "[Service]\n"
        "Environment=RUNNER_TOOL_CACHE=/cache/toolcache\n"
        "Environment=AGENT_TOOLSDIRECTORY=/cache/toolcache\n",
        encoding="utf-8",
    )
    sh("systemctl daemon-reload")
    sh(f"systemctl enable --now {unit.name}")


def main() -> None:
    tokens: dict[str, str] = {}
    for line in Path("/tmp/runner_tokens.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            tokens[k.strip()] = v.strip()
    token_as = tokens["RUNNER_TOKEN_AS"]
    token_claw = tokens["RUNNER_TOKEN_CLAW"]

    ver = json.loads(
        urllib.request.urlopen(
            "https://api.github.com/repos/actions/runner/releases/latest", timeout=30
        ).read()
    )["tag_name"].lstrip("v")
    print(f"==> actions/runner version: {ver}")
    urllib.request.urlretrieve(
        f"https://github.com/actions/runner/releases/download/v{ver}/"
        f"actions-runner-linux-x64-{ver}.tar.gz",
        "/tmp/actions-runner.tar.gz",
    )

    kube_src = Path("/tmp/prodavan-dev.yaml")
    if kube_src.is_file():
        sh("install -d -o runner -g runner -m 700 /home/runner/.kube")
        sh(
            "install -o runner -g runner -m 600 /tmp/prodavan-dev.yaml "
            "/home/runner/.kube/prodavan-dev.yaml"
        )
        print("==> kubeconfig -> /home/runner/.kube/prodavan-dev.yaml "
              "(server host.docker.internal, resolved via /etc/hosts)")

    for i in range(1, 6):
        install_runner(
            "https://github.com/ne-tort/agentscale", f"vm-as-{i}", token_as
        )
    for i in range(1, 3):
        install_runner(
            "https://github.com/ne-tort/prodavan-claw", f"vm-claw-{i}", token_claw
        )

    print("==> service states")
    sh("systemctl list-units 'actions.runner.*' --no-pager --plain")
    print("RUNNERS INSTALLED")


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run as root: sudo python3 02-runners.py")
    main()
