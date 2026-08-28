"""L3a — real k8s Pod lifecycle (in-cluster Job or kubeconfig + sandboxes NS)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from prodavan.config.settings import settings
from tests.conftest import requires_k8s, requires_postgres
from tests.e2e.conftest import register_e2e_project

pytestmark = [pytest.mark.k8s, requires_postgres, requires_k8s]


def _token(*, sub: str, email: str | None = None, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": [] if not platform_admin else ["platform.admin"],
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


def _setup_project(client: TestClient) -> tuple[str, str, dict[str, str], str]:
    admin = _token(sub="padmin-k8s-e2e", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}
    created = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={
            "name": "PodK8sCo",
            "password": "test-company-pass",
            "admin_email": "owner@k8spod.test",
        },
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    admin_employee = created.json().get("admin_employee") or {}
    owner_email = admin_employee.get("email") or "owner@k8spod.test"
    owner_sub = admin_employee.get("keycloak_sub") or "owner-k8s-e2e"
    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "PodK8s Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-k8s-pod",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    owner_h = {"Authorization": f"Bearer {_token(sub=owner_sub, email=owner_email)}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "PodK8sCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]
    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "PodK8sProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    project_id = proj.json()["id"]
    register_e2e_project(project_id)
    return company_id, admin, owner_h, project_id


def _ensure_pod_running(client: TestClient, owner_h: dict[str, str], project_id: str) -> dict:
    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code == 200, got.text
    body = got.json()
    runtime = body.get("runtime")
    if runtime is not None and runtime.get("status") == "running":
        return runtime

    if body.get("status") == "active":
        paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
        assert paused.status_code == 200, paused.text

    resumed = client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    assert resumed.status_code == 200, resumed.text

    after = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = after.json().get("runtime")
    assert runtime is not None, after.text
    assert runtime["status"] == "running"
    return runtime


def test_k8s_resume_creates_running_pod_with_phase(k8s_client: TestClient) -> None:
    """Real Pod in sandboxes NS: resume → running + k8s phase."""
    _, admin, owner_h, project_id = _setup_project(k8s_client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    runtime = _ensure_pod_running(k8s_client, owner_h, project_id)
    assert runtime["desired_state"] == "running"
    assert runtime.get("runtime_ref")

    admin_row = k8s_client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    assert admin_row.status_code == 200, admin_row.text
    row = admin_row.json()
    assert row["runtime"]["status"] == "running"
    phase = row["runtime"].get("phase") or row.get("k8s_phase")
    assert phase in {"Running", "running"}, row


def test_k8s_pause_stops_runtime(k8s_client: TestClient) -> None:
    _, _, owner_h, project_id = _setup_project(k8s_client)
    _ensure_pod_running(k8s_client, owner_h, project_id)

    paused = k8s_client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200, paused.text

    got = k8s_client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = got.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "paused"
    assert runtime["desired_state"] == "absent"


def test_k8s_pause_resume_recreates_pod(k8s_client: TestClient) -> None:
    _, _, owner_h, project_id = _setup_project(k8s_client)
    first = _ensure_pod_running(k8s_client, owner_h, project_id)
    first_id = first["pod_id"]

    k8s_client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    k8s_client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)

    after = k8s_client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = after.json()["runtime"]
    assert runtime["status"] == "running"
    assert runtime["pod_id"] == first_id


def test_k8s_lazy_start_via_trigger_dispatch(k8s_client: TestClient) -> None:
    _, admin, owner_h, project_id = _setup_project(k8s_client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    k8s_client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)
    before = k8s_client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert before.json().get("runtime") is None

    queued = k8s_client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.message", "payload": {"text": "k8s lazy"}},
    )
    assert queued.status_code == 202, queued.text
    dispatched = k8s_client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch?max=5",
        headers=owner_h,
    )
    assert dispatched.status_code == 200, dispatched.text

    after = k8s_client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = after.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "running"

    events = k8s_client.get(
        f"/api/v1/admin/platform-events?project_id={project_id}&event_type=pod.started",
        headers=admin_h,
    )
    assert events.status_code == 200
    assert len(events.json()["items"]) >= 1


def test_k8s_rematerialize_increments_generation(k8s_client: TestClient) -> None:
    _, _, owner_h, project_id = _setup_project(k8s_client)
    before = _ensure_pod_running(k8s_client, owner_h, project_id)
    assert before["hydrate_generation"] == 0

    remat = k8s_client.post(f"/api/v1/projects/{project_id}/materialize", headers=owner_h)
    assert remat.status_code == 200, remat.text

    after = k8s_client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = after.json()["runtime"]
    assert runtime["status"] == "running"
    assert runtime["hydrate_generation"] == 1
    assert runtime["pod_id"] == before["pod_id"]


def test_k8s_admin_force_kill_clears_runtime(k8s_client: TestClient) -> None:
    _, admin, owner_h, project_id = _setup_project(k8s_client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(k8s_client, owner_h, project_id)
    row = k8s_client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    pod_id = row.json()["runtime"]["pod_id"]

    killed = k8s_client.post(
        f"/api/v1/admin/containers/pods/{pod_id}/force-kill",
        headers=admin_h,
    )
    assert killed.status_code == 200, killed.text
    assert killed.json()["status"] == "terminated"

    after = k8s_client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    assert after.json().get("runtime") is None


def test_k8s_delete_project_terminates_pod(k8s_client: TestClient) -> None:
    _, admin, owner_h, project_id = _setup_project(k8s_client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(k8s_client, owner_h, project_id)
    deleted = k8s_client.delete(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert deleted.status_code == 200, deleted.text

    events = k8s_client.get(
        f"/api/v1/admin/platform-events?project_id={project_id}&event_type=pod.terminated",
        headers=admin_h,
    )
    assert events.status_code == 200
    assert len(events.json()["items"]) >= 1
