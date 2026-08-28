"""L3 cluster e2e — live HTTP and k8s runtime helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

import httpx
import pytest

from tests.conftest import E2E_BASE_URL, K8S_SANDBOX_NAMESPACE

# Track sandbox pods for teardown (prodavan.io/project-id label).
_e2e_project_ids: list[str] = []


def register_e2e_project(project_id: str) -> None:
    _e2e_project_ids.append(project_id)


@pytest.fixture(scope="session")
def live_base_url() -> str:
    return E2E_BASE_URL


@pytest.fixture(scope="session")
def live_api_prefix(live_base_url: str) -> str:
    return f"{live_base_url}/api/v1"


@pytest.fixture()
def live_client(live_base_url: str) -> httpx.Client:
    with httpx.Client(base_url=live_base_url, timeout=60.0) as client:
        yield client


@pytest.fixture()
def k8s_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """TestClient app with POD_RUNTIME_MODE=k8s (in-cluster Job or local kubeconfig)."""
    monkeypatch.setenv("AUTH_MODE", "test")
    monkeypatch.setenv("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")
    monkeypatch.setenv("KEYCLOAK_INVITE_MODE", "fake")
    monkeypatch.setenv("POD_RUNTIME_MODE", "k8s")
    monkeypatch.setenv("POD_SANDBOX_NAMESPACE", K8S_SANDBOX_NAMESPACE)
    monkeypatch.setenv("POD_SANDBOX_MINIO_SECRET", "prodavan-minio-hydrate")
    monkeypatch.setenv("POD_K8S_REQUIRED", "true")
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))

    from prodavan.application.pod_service.factory import build_pod_runtime
    from prodavan.config.settings import settings
    from prodavan.infrastructure.auth.jwt import reset_jwt_validator
    from prodavan.infrastructure.keycloak.invite import reset_invite_client
    from prodavan.main import create_app

    monkeypatch.setattr(settings, "secrets_dir", tmp_path)
    reset_jwt_validator()
    reset_invite_client()

    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        build_pod_runtime(force_new=True)
        yield client

    build_pod_runtime(force_new=True)


def _kubectl(args: list[str]) -> subprocess.CompletedProcess[str]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError("kubectl not on PATH")
    return subprocess.run(
        [kubectl, *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _delete_sandbox_pods_for_projects(project_ids: list[str]) -> None:
    if not project_ids:
        return
    kubectl = shutil.which("kubectl")
    if kubectl is None and not os.path.isfile(
        "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ):
        return
    for project_id in project_ids:
        _kubectl(
            [
                "delete",
                "pods",
                "-n",
                K8S_SANDBOX_NAMESPACE,
                "-l",
                f"prodavan.io/project-id={project_id}",
                "--ignore-not-found",
                "--wait=false",
            ]
        )


@pytest.fixture(autouse=True)
def e2e_cleanup(request: pytest.FixtureRequest):
    """Remove k8s sandbox pods created by L3a tests."""
    markers = {m.name for m in request.node.iter_markers()}
    if "k8s" not in markers:
        yield
        return
    _e2e_project_ids.clear()
    yield
    if _e2e_project_ids:
        _delete_sandbox_pods_for_projects(list(_e2e_project_ids))
        _e2e_project_ids.clear()


def live_json(resp: httpx.Response) -> Any:
    if not resp.content:
        return None
    return resp.json()
