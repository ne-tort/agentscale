"""Integration — pod_service lifecycle (runtime summary, events, lazy start)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")
os.environ.setdefault("KEYCLOAK_INVITE_MODE", "fake")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres


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


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


def _setup_project(client: TestClient) -> tuple[str, str, dict[str, str], str]:
    admin = _token(sub="padmin-pod-e2e", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}
    created = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "PodE2ECo", "password": "test-company-pass", "admin_email": "owner@pode2e.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "PodE2E Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-pod-e2e",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    owner_tok = _token(sub="owner-pod-e2e", email="owner@pode2e.test")
    owner_h = {"Authorization": f"Bearer {owner_tok}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "PodE2ECab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]
    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "PodE2EProj"},
    )
    assert proj.status_code == 201, proj.text
    return company_id, admin, owner_h, proj.json()["id"]


def _platform_events(
    client: TestClient,
    *,
    admin_h: dict[str, str],
    project_id: str,
    event_type: str | None = None,
) -> list[dict]:
    url = f"/api/v1/admin/platform-events?project_id={project_id}"
    if event_type:
        url += f"&event_type={event_type}"
    resp = client.get(url, headers=admin_h)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


@requires_postgres
def test_project_runtime_lifecycle(client: TestClient) -> None:
    """Create → no runtime; resume → running; pause → paused."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code == 200, got.text
    assert got.json().get("runtime") is None

    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)

    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

    resumed = client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"

    active_after_resume = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert active_after_resume.json()["runtime"]["status"] == "running"

    paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"

    paused_get = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = paused_get.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "paused"
    assert runtime["desired_state"] == "absent"

    resumed = client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"

    active_get = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = active_get.json()["runtime"]
    assert runtime["status"] == "running"
    assert runtime["desired_state"] == "running"

    admin_row = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    assert admin_row.status_code == 200, admin_row.text
    assert admin_row.json()["runtime"]["status"] == "running"


@requires_postgres
def test_pause_emits_pod_before_project_event(client: TestClient) -> None:
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

    events = _platform_events(client, admin_h=admin_h, project_id=project_id)
    types = [e["event_type"] for e in events if e["event_type"] in {"pod.paused", "project.paused"}]
    assert types.index("pod.paused") < types.index("project.paused")


@requires_postgres
def test_trigger_dispatch_lazy_starts_pod(client: TestClient) -> None:
    """Active project without resume: first chat dispatch provisions and starts pod."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)

    before = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert before.json().get("runtime") is None

    queued = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.message", "payload": {"text": "lazy pod"}},
    )
    assert queued.status_code == 202, queued.text

    dispatched = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch?max=5",
        headers=owner_h,
    )
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json().get("dispatched") is True

    after = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = after.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "running"
    assert runtime["desired_state"] == "running"

    events = _platform_events(client, admin_h=admin_h, project_id=project_id, event_type="pod.started")
    assert len(events) >= 1

    started = _platform_events(
        client, admin_h=admin_h, project_id=project_id, event_type="project.started"
    )
    assert len(started) >= 1

    hydrated = _platform_events(
        client, admin_h=admin_h, project_id=project_id, event_type="pod.hydrated"
    )
    assert len(hydrated) >= 1

@requires_postgres
def test_delete_emits_pod_terminated(client: TestClient) -> None:
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)

    deleted = client.delete(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "deleted"

    events = _platform_events(client, admin_h=admin_h, project_id=project_id, event_type="pod.terminated")
    assert len(events) >= 1

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.json().get("runtime") is None


@requires_postgres
def test_admin_force_kill_and_reconcile(client: TestClient) -> None:
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)

    row = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    assert row.status_code == 200, row.text
    pod_id = row.json()["runtime"]["pod_id"]

    killed = client.post(f"/api/v1/admin/containers/pods/{pod_id}/force-kill", headers=admin_h)
    assert killed.status_code == 200, killed.text
    assert killed.json()["status"] == "terminated"

    reconciled = client.post("/api/v1/admin/containers/reconcile", headers=admin_h)
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["fixed"] >= 0

    after = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    runtime = after.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "running"
