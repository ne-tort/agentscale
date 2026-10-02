"""L3 cluster e2e — live HTTP and k8s runtime helpers."""

from __future__ import annotations

import json
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
    # Launch/provision against real k8s needs a long read timeout.
    timeout = httpx.Timeout(connect=5.0, read=120.0, write=60.0, pool=5.0)
    with httpx.Client(base_url=live_base_url, timeout=timeout) as client:
        yield client


@pytest.fixture()
def k8s_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """TestClient app with POD_RUNTIME_MODE=k8s (in-cluster Job or local kubeconfig)."""
    monkeypatch.setenv("AUTH_MODE", "test")
    monkeypatch.setenv("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")
    monkeypatch.setenv("KEYCLOAK_INVITE_MODE", "fake")
    monkeypatch.setenv("POD_RUNTIME_MODE", "k8s")
    monkeypatch.setenv("POD_SANDBOX_NAMESPACE", K8S_SANDBOX_NAMESPACE)
    monkeypatch.delenv("POD_SANDBOX_MINIO_SECRET", raising=False)
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


def _kubectl(args: list[str]) -> subprocess.CompletedProcess[str]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError("kubectl not on PATH")
    cmd = list(args)
    if not any(a.startswith("--request-timeout") for a in cmd):
        cmd.append("--request-timeout=10s")
    return subprocess.run(
        [kubectl, *cmd],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _incluster_list_pods(project_id: str) -> list[tuple[str, str]]:
    """List sandbox pods via in-cluster SA (e2e Job has no kubectl binary)."""
    import httpx

    from prodavan.infrastructure.k8s.auth import InClusterAuth

    auth = InClusterAuth()
    if not auth.available():
        raise RuntimeError("kubectl not on PATH and in-cluster SA unavailable")
    label = f"prodavan.io/project-id={project_id}"
    url = (
        f"{auth.api_base()}/api/v1/namespaces/{K8S_SANDBOX_NAMESPACE}/pods"
        f"?labelSelector={label}"
    )
    with httpx.Client(**auth.client_kwargs()) as client:
        resp = client.get(url, headers=auth.headers())
    if resp.status_code != 200:
        raise AssertionError(
            f"in-cluster list pods for {project_id} failed: {resp.status_code} {resp.text[:500]}"
        )
    body = resp.json()
    out: list[tuple[str, str]] = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        status = item.get("status") or {}
        name = str(meta.get("name") or "")
        phase = str(status.get("phase") or "Unknown")
        if name:
            out.append((name, phase))
    return out


def k8s_pods_for_project(project_id: str) -> list[tuple[str, str]]:
    """Return [(pod_name, phase), ...] from agentscale-dev-sandboxes for a project."""
    if shutil.which("kubectl") is None:
        return _incluster_list_pods(project_id)
    proc = _kubectl(
        [
            "get",
            "pods",
            "-n",
            K8S_SANDBOX_NAMESPACE,
            "-l",
            f"prodavan.io/project-id={project_id}",
            "-o",
            "json",
        ]
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"kubectl get pods for project {project_id} failed: {proc.stderr or proc.stdout}"
        )
    body = json.loads(proc.stdout or '{"items":[]}')
    out: list[tuple[str, str]] = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        status = item.get("status") or {}
        name = str(meta.get("name") or "")
        phase = str(status.get("phase") or "Unknown")
        if name:
            out.append((name, phase))
    return out


def assert_k8s_pod_running(project_id: str, *, wait_sec: float = 90.0) -> str:
    """Assert exactly one Running pod exists in the cluster for project_id."""
    import time

    deadline = time.time() + wait_sec
    last: list[tuple[str, str]] = []
    while time.time() < deadline:
        last = k8s_pods_for_project(project_id)
        running = [(n, p) for n, p in last if p == "Running"]
        if len(running) == 1:
            return running[0][0]
        time.sleep(2.0)
    raise AssertionError(f"expected 1 Running k8s pod, got {last!r}")


def assert_k8s_no_pods(project_id: str, *, wait_sec: float = 90.0) -> None:
    """Assert no sandbox pods remain for project_id (pause/delete)."""
    import time

    deadline = time.time() + wait_sec
    last: list[tuple[str, str]] = []
    while time.time() < deadline:
        last = k8s_pods_for_project(project_id)
        if last == []:
            return
        time.sleep(2.0)
    raise AssertionError(f"expected no k8s pods, got {last!r}")


def _incluster_delete_pods(project_id: str) -> None:
    import httpx

    from prodavan.infrastructure.k8s.auth import InClusterAuth

    auth = InClusterAuth()
    if not auth.available():
        return
    label = f"prodavan.io/project-id={project_id}"
    list_url = (
        f"{auth.api_base()}/api/v1/namespaces/{K8S_SANDBOX_NAMESPACE}/pods"
        f"?labelSelector={label}"
    )
    with httpx.Client(**auth.client_kwargs()) as client:
        listed = client.get(list_url, headers=auth.headers())
        if listed.status_code != 200:
            return
        for item in listed.json().get("items") or []:
            name = str((item.get("metadata") or {}).get("name") or "")
            if not name:
                continue
            client.delete(
                f"{auth.api_base()}/api/v1/namespaces/{K8S_SANDBOX_NAMESPACE}/pods/{name}",
                headers=auth.headers(),
            )


def _delete_sandbox_pods_for_projects(project_ids: list[str]) -> None:
    if not project_ids:
        return
    kubectl = shutil.which("kubectl")
    sa = os.path.isfile("/var/run/secrets/kubernetes.io/serviceaccount/token")
    if kubectl is None and not sa:
        return
    for project_id in project_ids:
        if kubectl is None:
            _incluster_delete_pods(project_id)
            continue
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


def pytest_collection_modifyitems(session, config, items) -> None:
    """Fail CI live e2e when dev API never becomes reachable (no silent skip)."""
    if os.getenv("PRODAVAN_E2E_LIVE_REQUIRED") != "1":
        return
    live_items = [item for item in items if item.get_closest_marker("live")]
    if not live_items:
        return
    from tests.conftest import _live_api_available, _live_e2e_base_url

    if _live_api_available():
        return
    pytest.exit(
        f"Live API unreachable at {_live_e2e_base_url()}/health/live",
        returncode=1,
    )
