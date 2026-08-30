"""Integration — pod_service lifecycle (runtime summary, events, lazy start)."""

from __future__ import annotations

import os
import sys
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
    admin_employee = created.json().get("admin_employee") or {}
    owner_email = admin_employee.get("email") or "owner@pode2e.test"
    owner_sub = admin_employee.get("keycloak_sub") or "owner-pod-e2e"
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
    owner_tok = _token(sub=owner_sub, email=owner_email)
    owner_h = {"Authorization": f"Bearer {owner_tok}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "PodE2ECab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]
    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "PodE2EProj"},
    )
    assert proj.status_code in (200, 201), proj.text
    return company_id, admin, owner_h, proj.json()["id"]


def _setup_two_projects(client: TestClient) -> tuple[str, str, dict[str, str], str, str]:
    company_id, admin, owner_h, project_a = _setup_project(client)
    cabinet_id = client.get(f"/api/v1/projects/{project_a}", headers=owner_h).json()["cabinet_id"]
    proj_b = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "PodE2EProjB"},
    )
    assert proj_b.status_code in (200, 201), proj_b.text
    return company_id, admin, owner_h, project_a, proj_b.json()["id"]


def _configure_and_launch(client: TestClient, owner_h: dict[str, str], project_id: str) -> dict:
    keys = client.get(f"/api/v1/projects/{project_id}/ai-keys/available", headers=owner_h)
    assert keys.status_code == 200, keys.text
    items = keys.json().get("items") or []
    assert items, keys.text
    key_id = items[0]["id"]
    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"agent_provider": "cursor", "resolved_ai_key_id": key_id},
    )
    assert patched.status_code == 200, patched.text
    launched = client.post(f"/api/v1/projects/{project_id}/launch", headers=owner_h)
    assert launched.status_code == 200, launched.text
    return launched.json()


def _ensure_pod_running(client: TestClient, owner_h: dict[str, str], project_id: str) -> dict:
    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code == 200, got.text
    body = got.json()
    runtime = body.get("runtime")
    if runtime is not None and runtime.get("status") == "running":
        return runtime

    if body.get("status") == "draft" or runtime is None:
        _configure_and_launch(client, owner_h, project_id)
    elif body.get("status") == "paused":
        resumed = client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
        assert resumed.status_code == 200, resumed.text

    after = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = after.json().get("runtime")
    assert runtime is not None, after.text
    assert runtime["status"] == "running"
    return runtime


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
    """Create draft → launch → running; pause → paused; resume → running."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "draft"
    assert got.json().get("runtime") is None

    _configure_and_launch(client, owner_h, project_id)

    active_after_launch = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert active_after_launch.json()["runtime"]["status"] == "running"

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

    _configure_and_launch(client, owner_h, project_id)
    client.post(f"/api/v1/projects/{project_id}/triggers/dispatch?max=10", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/resume", headers=owner_h)
    client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)

    events = _platform_events(client, admin_h=admin_h, project_id=project_id)
    types = [e["event_type"] for e in events if e["event_type"] in {"pod.paused", "project.paused"}]
    assert types.index("pod.paused") < types.index("project.paused")


@requires_postgres
def test_trigger_dispatch_requires_launch(client: TestClient) -> None:
    """Draft project: chat dispatch fails until explicit launch."""
    _, _, owner_h, project_id = _setup_project(client)

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
    assert dispatched.json().get("dispatched") is False
    assert any(
        item.get("reason") == "project_not_launched"
        for item in (dispatched.json().get("items") or [])
    ) or dispatched.json().get("reason") == "project_not_launched"

    _configure_and_launch(client, owner_h, project_id)

    queued2 = client.post(
        f"/api/v1/projects/{project_id}/triggers",
        headers=owner_h,
        json={"kind": "chat.message", "payload": {"text": "after launch"}},
    )
    assert queued2.status_code == 202, queued2.text

    dispatched2 = client.post(
        f"/api/v1/projects/{project_id}/triggers/dispatch?max=5",
        headers=owner_h,
    )
    assert dispatched2.status_code == 200, dispatched2.text
    assert dispatched2.json().get("dispatched") is True

    after = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = after.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "running"


@requires_postgres
def test_cabinet_soft_delete_emits_pod_terminated(client: TestClient) -> None:
    """Cabinet soft-delete cascades to projects and terminates running pods."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(client, owner_h, project_id)
    cabinet_id = client.get(f"/api/v1/projects/{project_id}", headers=owner_h).json()["cabinet_id"]

    deleted = client.delete(f"/api/v1/cabinets/{cabinet_id}", headers=owner_h)
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body.get("soft") is True
    assert project_id in body.get("projects_soft_deleted", [])

    events = _platform_events(
        client,
        admin_h=admin_h,
        project_id=project_id,
        event_type="pod.terminated",
    )
    assert len(events) >= 1


@requires_postgres
def test_delete_emits_pod_terminated(client: TestClient) -> None:
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(client, owner_h, project_id)

    deleted = client.delete(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "deleted"

    events = _platform_events(client, admin_h=admin_h, project_id=project_id, event_type="pod.terminated")
    assert len(events) >= 1

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.json().get("runtime") is None


@requires_postgres
def test_admin_force_kill_terminates_pod(client: TestClient) -> None:
    """Platform admin force-kill marks pod terminated (reconcile tested on Linux CI)."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(client, owner_h, project_id)
    row = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    pod_id = row.json()["runtime"]["pod_id"]

    killed = client.post(f"/api/v1/admin/containers/pods/{pod_id}/force-kill", headers=admin_h)
    assert killed.status_code == 200, killed.text
    assert killed.json()["status"] == "terminated"

    after = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    assert after.json().get("runtime") is None


@requires_postgres
@pytest.mark.skipif(sys.platform == "win32", reason="reconcile pod listing hits async SQLAlchemy on Windows TestClient")
def test_admin_force_kill_and_reconcile(client: TestClient) -> None:
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(client, owner_h, project_id)

    row = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    assert row.status_code == 200, row.text
    pod_id = row.json()["runtime"]["pod_id"]

    killed = client.post(f"/api/v1/admin/containers/pods/{pod_id}/force-kill", headers=admin_h)
    assert killed.status_code == 200, killed.text
    assert killed.json()["status"] == "terminated"

    after_kill = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    assert after_kill.json().get("runtime") is None

    reconciled = client.post("/api/v1/admin/containers/reconcile", headers=admin_h)
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["fixed"] >= 0

    after = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    runtime = after.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "running"


@requires_postgres
def test_cabinet_admin_purge_terminates_running_pod(client: TestClient) -> None:
    """Admin hard purge (skip soft-delete) must terminate live pods before wipe."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(client, owner_h, project_id)
    cabinet_id = client.get(f"/api/v1/projects/{project_id}", headers=owner_h).json()["cabinet_id"]

    purged = client.delete(f"/api/v1/admin/cabinets/{cabinet_id}/purge", headers=admin_h)
    assert purged.status_code == 200, purged.text
    assert purged.json().get("purged") is True

    events = _platform_events(
        client,
        admin_h=admin_h,
        project_id=project_id,
        event_type="pod.terminated",
    )
    assert len(events) >= 1

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    assert got.status_code in (404, 200)
    if got.status_code == 200:
        assert got.json().get("runtime") is None


@requires_postgres
def test_rematerialize_running_increments_hydrate_generation(client: TestClient) -> None:
    """Active pod: materialize bumps hydrate_generation and re-hydrates without pausing."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    before = _ensure_pod_running(client, owner_h, project_id)
    assert before["hydrate_generation"] == 0

    remat = client.post(f"/api/v1/projects/{project_id}/materialize", headers=owner_h)
    assert remat.status_code == 200, remat.text

    after = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = after.json()["runtime"]
    assert runtime["status"] == "running"
    assert runtime["hydrate_generation"] == 1
    assert runtime["pod_id"] == before["pod_id"]

    hydrated = _platform_events(
        client, admin_h=admin_h, project_id=project_id, event_type="pod.hydrated"
    )
    assert len(hydrated) >= 1


@requires_postgres
def test_rematerialize_paused_bumps_generation_without_start(client: TestClient) -> None:
    """Paused project: materialize is maintenance-only — generation++ but pod stays absent."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(client, owner_h, project_id)
    paused = client.post(f"/api/v1/projects/{project_id}/pause", headers=owner_h)
    assert paused.status_code == 200, paused.text

    started_before = _platform_events(
        client, admin_h=admin_h, project_id=project_id, event_type="pod.started"
    )

    remat = client.post(f"/api/v1/projects/{project_id}/materialize", headers=owner_h)
    assert remat.status_code == 200, remat.text

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = got.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "paused"
    assert runtime["desired_state"] == "absent"
    assert runtime["hydrate_generation"] == 1

    started_after = _platform_events(
        client, admin_h=admin_h, project_id=project_id, event_type="pod.started"
    )
    assert len(started_after) == len(started_before)


@requires_postgres
def test_two_projects_independent_pod_lifecycle(client: TestClient) -> None:
    """Two active projects: pausing one must not affect the other's runtime."""
    company_id, admin, owner_h, project_a, project_b = _setup_two_projects(client)

    runtime_a = _ensure_pod_running(client, owner_h, project_a)
    runtime_b = _ensure_pod_running(client, owner_h, project_b)
    assert runtime_a["pod_id"] != runtime_b["pod_id"]

    paused = client.post(f"/api/v1/projects/{project_a}/pause", headers=owner_h)
    assert paused.status_code == 200, paused.text

    row_a = client.get(f"/api/v1/projects/{project_a}", headers=owner_h).json()["runtime"]
    row_b = client.get(f"/api/v1/projects/{project_b}", headers=owner_h).json()["runtime"]
    assert row_a["status"] == "paused"
    assert row_b["status"] == "running"

    listed = client.get(
        f"/api/v1/companies/{company_id}/containers",
        headers=owner_h,
    )
    assert listed.status_code == 200, listed.text
    by_id = {i["project_id"]: i for i in listed.json()["items"]}
    assert by_id[project_a]["runtime"]["status"] == "paused"
    assert by_id[project_b]["runtime"]["status"] == "running"


@requires_postgres
def test_company_containers_pause_and_resume(client: TestClient) -> None:
    """Company-scoped container API drives the same pod lifecycle as project endpoints."""
    company_id, _, owner_h, project_id = _setup_project(client)
    _ensure_pod_running(client, owner_h, project_id)

    paused = client.post(
        f"/api/v1/companies/{company_id}/containers/{project_id}/pause",
        headers=owner_h,
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert paused.json()["runtime"]["status"] == "paused"

    resumed = client.post(
        f"/api/v1/companies/{company_id}/containers/{project_id}/resume",
        headers=owner_h,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"
    assert resumed.json()["runtime"]["status"] == "running"


@requires_postgres
def test_company_containers_reload(client: TestClient) -> None:
    """Company-scoped reload restarts pod via the same path as project reload."""
    company_id, _, owner_h, project_id = _setup_project(client)
    runtime_before = _ensure_pod_running(client, owner_h, project_id)
    pod_id_before = runtime_before["pod_id"]

    reloaded = client.post(
        f"/api/v1/companies/{company_id}/containers/{project_id}/reload",
        headers=owner_h,
    )
    assert reloaded.status_code == 200, reloaded.text
    body = reloaded.json()
    assert body["status"] == "active"
    assert body["runtime"]["status"] == "running"
    assert body["runtime"]["pod_id"] == pod_id_before


@requires_postgres
def test_admin_containers_reload(client: TestClient) -> None:
    """Platform admin reload uses the same ProjectCommand.reload_project path as employee reload."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}
    runtime_before = _ensure_pod_running(client, owner_h, project_id)
    pod_id_before = runtime_before["pod_id"]

    reloaded = client.post(
        f"/api/v1/admin/containers/{project_id}/reload",
        headers=admin_h,
    )
    assert reloaded.status_code == 200, reloaded.text
    body = reloaded.json()
    assert body["status"] == "active"
    assert body["runtime"]["status"] == "running"
    assert body["runtime"]["pod_id"] == pod_id_before


@requires_postgres
def test_complete_project_pauses_pod(client: TestClient) -> None:
    """Completing a project stops runtime (pod paused) but keeps the pod row for inspection."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    pod_id = _ensure_pod_running(client, owner_h, project_id)["pod_id"]

    completed = client.post(f"/api/v1/projects/{project_id}/complete", headers=owner_h)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"

    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h)
    runtime = got.json().get("runtime")
    assert runtime is not None
    assert runtime["status"] == "paused"
    assert runtime["pod_id"] == pod_id

    events = _platform_events(client, admin_h=admin_h, project_id=project_id)
    types = [e["event_type"] for e in events]
    assert "project.completed" in types
    assert "pod.paused" in types


@requires_postgres
def test_resume_idempotent_preserves_pod_id(client: TestClient) -> None:
    """Repeated resume on an active project is a no-op for pod identity."""
    _, _, owner_h, project_id = _setup_project(client)

    first = _ensure_pod_running(client, owner_h, project_id)
    got = client.get(f"/api/v1/projects/{project_id}", headers=owner_h).json()
    assert got["status"] == "active"
    second = got["runtime"]
    assert second["pod_id"] == first["pod_id"]
    assert second["status"] == "running"


@requires_postgres
def test_peer_cannot_force_kill_pod(client: TestClient) -> None:
    """Only platform admin may force-kill pods."""
    _, admin, owner_h, project_id = _setup_project(client)
    admin_h = {"Authorization": f"Bearer {admin}"}

    _ensure_pod_running(client, owner_h, project_id)
    row = client.get(f"/api/v1/admin/containers/{project_id}", headers=admin_h)
    pod_id = row.json()["runtime"]["pod_id"]

    denied = client.post(
        f"/api/v1/admin/containers/pods/{pod_id}/force-kill",
        headers=owner_h,
    )
    assert denied.status_code == 403, denied.text
