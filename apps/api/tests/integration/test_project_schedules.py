"""Acceptance checks for project-owned cron schedules and owner access."""

from datetime import UTC, datetime, timedelta
import os

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres
from tests.integration.test_projects import _setup_cabinet


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


def _token(*, sub: str, email: str | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "roles": [],
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@requires_postgres
def test_project_schedule_crud_is_owner_scoped(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_headers = {"Authorization": f"Bearer {owner_tok}"}
    created_project = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects", headers=owner_headers, json={"name": "Schedule project"}
    )
    assert created_project.status_code == 201, created_project.text
    project_id = created_project.json()["id"]

    result = client.post(
        f"/api/v1/projects/{project_id}/schedules",
        headers=owner_headers,
        json={"name": "Daily", "cron": "0 9 * * 1-5", "timezone": "Europe/Samara", "payload": {"x": 1}},
    )
    assert result.status_code == 201, result.text
    schedule_id = result.json()["id"]
    assert result.json()["next_run_at"]

    listed = client.get(f"/api/v1/projects/{project_id}/schedules", headers=owner_headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == schedule_id
    patched = client.patch(
        f"/api/v1/projects/{project_id}/schedules/{schedule_id}", headers=owner_headers, json={"enabled": False}
    )
    assert patched.status_code == 200
    assert patched.json()["next_run_at"] is None
    deleted = client.delete(f"/api/v1/projects/{project_id}/schedules/{schedule_id}", headers=owner_headers)
    assert deleted.status_code == 204


@requires_postgres
def test_project_schedule_api_validates_cron_and_timezone(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    headers = {"Authorization": f"Bearer {owner_tok}"}
    created_project = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects", headers=headers, json={"name": "Invalid schedule project"}
    )
    assert created_project.status_code == 201, created_project.text
    project_id = created_project.json()["id"]
    base = {"name": "Invalid", "cron": "0 9 * * *", "timezone": "UTC"}

    invalid_cron = client.post(
        f"/api/v1/projects/{project_id}/schedules", headers=headers, json={**base, "cron": "70 9 * * *"}
    )
    assert invalid_cron.status_code == 422, invalid_cron.text
    invalid_timezone = client.post(
        f"/api/v1/projects/{project_id}/schedules", headers=headers, json={**base, "timezone": "Mars/Olympus"}
    )
    assert invalid_timezone.status_code == 422, invalid_timezone.text
    valid = client.post(f"/api/v1/projects/{project_id}/schedules", headers=headers, json=base)
    assert valid.status_code == 201, valid.text
    schedule_id = valid.json()["id"]
    invalid_patch = client.patch(
        f"/api/v1/projects/{project_id}/schedules/{schedule_id}", headers=headers, json={"timezone": "Mars/Olympus"}
    )
    assert invalid_patch.status_code == 422, invalid_patch.text


@requires_postgres
def test_project_schedule_api_denies_non_owner_crud(client: TestClient) -> None:
    _, cabinet_id, owner_tok = _setup_cabinet(client)
    owner_headers = {"Authorization": f"Bearer {owner_tok}"}
    peer_headers = {"Authorization": f"Bearer {_token(sub='schedule-peer', email='peer@example.test')}"}
    created_project = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects", headers=owner_headers, json={"name": "Private schedule project"}
    )
    assert created_project.status_code == 201, created_project.text
    project_id = created_project.json()["id"]
    created_schedule = client.post(
        f"/api/v1/projects/{project_id}/schedules",
        headers=owner_headers,
        json={"name": "Daily", "cron": "0 9 * * *", "timezone": "UTC"},
    )
    assert created_schedule.status_code == 201, created_schedule.text
    schedule_id = created_schedule.json()["id"]

    assert client.get(f"/api/v1/projects/{project_id}/schedules", headers=peer_headers).status_code == 403
    assert client.post(
        f"/api/v1/projects/{project_id}/schedules",
        headers=peer_headers,
        json={"name": "Peer", "cron": "0 9 * * *", "timezone": "UTC"},
    ).status_code == 403
    assert client.patch(
        f"/api/v1/projects/{project_id}/schedules/{schedule_id}", headers=peer_headers, json={"enabled": False}
    ).status_code == 403
    assert client.delete(
        f"/api/v1/projects/{project_id}/schedules/{schedule_id}", headers=peer_headers
    ).status_code == 403
    still_there = client.get(f"/api/v1/projects/{project_id}/schedules", headers=owner_headers)
    assert still_there.status_code == 200
    assert [item["id"] for item in still_there.json()["items"]] == [schedule_id]
