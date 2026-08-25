"""Offline unit tests for prodavan-ops (no cluster required)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prodavan_ops.k8s import FIRST_PARTY_DEPLOYMENTS, assert_kubeconfig_docker_ready
from prodavan_ops.validate import (
    FIRST_PARTY_LATEST,
    assert_no_compose_or_k3d,
    assert_no_shell_scripts,
    verify_image_pins,
)


def test_first_party_deployments_cover_api_web_celery() -> None:
    names = set(FIRST_PARTY_DEPLOYMENTS)
    assert "prodavan-api" in names
    assert "prodavan-web" in names
    assert "prodavan-celery-worker" in names
    assert "prodavan-celery-beat" in names


def test_assert_kubeconfig_docker_ready_noop_without_ci_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PRODAVAN_CI_HOST", raising=False)
    kube = tmp_path / "kube.yaml"
    kube.write_text("clusters:\n- cluster:\n    server: https://127.0.0.1:6443\n", encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", str(kube))
    assert_kubeconfig_docker_ready()  # must not raise


def test_prepare_docker_kubeconfig_rewrites_server(tmp_path: Path) -> None:
    from prodavan_ops.k8s import prepare_docker_kubeconfig

    src = tmp_path / "in.yaml"
    dest = tmp_path / "out.yaml"
    src.write_text(
        "\ufeffapiVersion: v1\n"
        "clusters:\n"
        "- cluster:\n"
        "    certificate-authority-data: QQ==\n"
        "    server: https://127.0.0.1:6443\n"
        "  name: default\n",
        encoding="utf-8",
    )
    prepare_docker_kubeconfig(src, dest)
    out = dest.read_text(encoding="utf-8")
    assert "host.docker.internal:6443" in out
    assert "127.0.0.1" not in out
    assert "insecure-skip-tls-verify: true" in out
    assert not out.startswith("\ufeff")


def test_resolve_kubeconfig_rewrites_when_ci_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from prodavan_ops.k8s import resolve_kubeconfig_path

    src = tmp_path / "prodavan-dev.yaml"
    src.write_text(
        "apiVersion: v1\n"
        "clusters:\n"
        "- cluster:\n"
        "    server: https://host.docker.internal:6443\n"
        "  name: default\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRODAVAN_CI_HOST", "host.docker.internal")
    monkeypatch.setenv("KUBECONFIG", str(src))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "rt"))
    path = resolve_kubeconfig_path()
    assert path != src
    text = path.read_text(encoding="utf-8")
    assert "host.docker.internal" in text
    assert "insecure-skip-tls-verify: true" in text
    assert os.environ["KUBECONFIG"] == str(path)


def test_verify_image_pins_accepts_first_party_latest() -> None:
    manifest = """
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: api
          image: ghcr.io/ne-tort/prodavan-api:latest
        - name: web
          image: ghcr.io/ne-tort/prodavan-web:latest
---
# pins
postgres:16.15
redis:7.4.11-alpine
minio/minio:RELEASE.2024-10-02T17-50-41Z
redpanda:v24.2.4
"""
    verify_image_pins(manifest)


def test_verify_image_pins_rejects_infra_latest() -> None:
    manifest = """
apiVersion: v1
kind: Pod
metadata:
  name: bad
spec:
  containers:
    - name: pg
      image: postgres:latest
    - name: api
      image: ghcr.io/ne-tort/prodavan-api:latest
    - name: web
      image: ghcr.io/ne-tort/prodavan-web:latest
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: pin-snippets
data:
  note: |
    postgres:16.15
    redis:7.4.11-alpine
    minio/minio:RELEASE.2024-10-02T17-50-41Z
    redpanda:v24.2.4
"""
    with pytest.raises(RuntimeError, match="frozen version"):
        verify_image_pins(manifest)


def test_assert_no_shell_scripts_and_no_legacy_paths() -> None:
    # Repo contract — must stay green on every Gate.
    assert_no_shell_scripts()
    assert_no_compose_or_k3d()


def test_gitops_bootstrap_template_escapes_bash_arrays() -> None:
    """Terraform templatefile must use $${KCTL[@]} so bash arrays survive render."""
    tpl = (
        Path(__file__).resolve().parents[2]
        / "terraform"
        / "modules"
        / "k3s-dev-host"
        / "templates"
        / "gitops-bootstrap.sh.tpl"
    )
    text = tpl.read_text(encoding="utf-8")
    assert "gitops-already-healthy" in text
    assert '"$${KCTL[@]}"' in text
    assert "${kubeconfig_path}" in text
    assert "${remote_repo_path}" in text
    # Unescaped bash array would be eaten by templatefile
    assert '"${KCTL[@]}"' not in text.replace('"$${KCTL[@]}"', "")


def test_export_windows_kubeconfig_script_targets_docker_gateway() -> None:
    tpl = (
        Path(__file__).resolve().parents[2]
        / "terraform"
        / "modules"
        / "k3s-dev-host"
        / "templates"
        / "export-windows-kubeconfig.sh.tpl"
    )
    text = tpl.read_text(encoding="utf-8")
    assert "host.docker.internal" in text
    assert "insecure-skip-tls-verify" in text
    assert "WINDOWS_KUBECONFIG" in text


def test_first_party_latest_constants() -> None:
    assert "ghcr.io/ne-tort/prodavan-api:latest" in FIRST_PARTY_LATEST
    assert "ghcr.io/ne-tort/prodavan-web:latest" in FIRST_PARTY_LATEST

