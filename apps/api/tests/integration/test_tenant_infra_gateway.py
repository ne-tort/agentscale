"""Integration — Tenant Infra Gateway planes (cache/docs/events/userdb) via Bridge JWT."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.application.pod_identity.bridge import build_launch_scopes, mint_pod_bridge_token
from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres
from tests.integration.support import owner_auth_from_company


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
    with TestClient(create_app()) as test_client:
        yield test_client


def _bridge_headers(*, project_id: str, cabinet_id: str, company_id: str, pod_id: str) -> dict[str, str]:
    token, _ = asyncio.run(
        mint_pod_bridge_token(
            project_id=project_id,
            cabinet_id=cabinet_id,
            company_id=company_id,
            pod_id=pod_id,
            scopes=build_launch_scopes([]),
        )
    )
    return {"Authorization": f"Bearer {token}"}


def _bootstrap(client: TestClient) -> tuple[str, str, str, str, dict[str, str]]:
    suffix = uuid.uuid4().hex[:8]
    admin_h = {"Authorization": f"Bearer {_token(sub=f'ti-admin-{suffix}', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={
            "name": f"TICo-{suffix}",
            "password": "test-company-pass",
            "admin_email": f"ti-{suffix}@co.test",
        },
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]
    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-ti",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "TICab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]
    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "TIProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"agent_provider": "cursor", "resolved_ai_key_id": key.json()["id"]},
    )
    assert patched.status_code == 200, patched.text
    launched = client.post(f"/api/v1/projects/{project_id}/launch", headers=owner_h)
    assert launched.status_code == 200, launched.text
    pod_id = (launched.json().get("runtime") or {}).get("pod_id")
    assert pod_id, launched.text
    return company_id, cabinet_id, project_id, pod_id, owner_h


@requires_postgres
def test_tenant_infra_cache_docs_events_userdb(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.tenant_infra.adapters.memory_cache import reset_shared_memory_tenant_cache

    monkeypatch.setattr(settings, "pod_identity_bridge_secret", "unit-bridge-secret")
    monkeypatch.setattr(settings, "tenant_infra_cache_ops_per_minute", 0)
    # Force shared in-memory cache: CI has no Redis, and Redis manager must not leak across requests.
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: None,
    )
    reset_shared_memory_tenant_cache()
    company_id, cabinet_id, project_id, pod_id, _owner = _bootstrap(client)
    pod_h = _bridge_headers(
        project_id=project_id, cabinet_id=cabinet_id, company_id=company_id, pod_id=pod_id
    )

    put = client.put(
        f"/api/v1/projects/{project_id}/infra/cache/demo",
        headers=pod_h,
        json={"value": "v1"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["ttl_sec"] >= 1
    got = client.get(f"/api/v1/projects/{project_id}/infra/cache/demo", headers=pod_h)
    assert got.status_code == 200, got.text
    assert got.json()["value"] == "v1"

    doc = client.put(
        f"/api/v1/projects/{project_id}/infra/docs/notes/n1",
        headers=pod_h,
        json={"document": {"text": "hello"}},
    )
    assert doc.status_code == 200, doc.text
    listed = client.get(f"/api/v1/projects/{project_id}/infra/docs/notes", headers=pod_h)
    assert listed.status_code == 200, listed.text
    assert listed.json()["count"] >= 1

    # Force memory userdb
    import prodavan.application.tenant_infra.userdb_service as udb

    udb._engine = None
    monkeypatch.setattr(udb, "get_userdb_engine", lambda: None)
    table = client.post(
        f"/api/v1/projects/{project_id}/infra/userdb/tables/items",
        headers=pod_h,
        json={"columns": [{"name": "title", "type": "text"}]},
    )
    assert table.status_code == 200, table.text
    row = client.post(
        f"/api/v1/projects/{project_id}/infra/userdb/tables/items/rows",
        headers=pod_h,
        json={"row": {"title": "x"}},
    )
    assert row.status_code == 200, row.text

    ev = client.post(
        f"/api/v1/projects/{project_id}/infra/events",
        headers=pod_h,
        json={"type": "ping", "payload": {"ok": True}},
    )
    assert ev.status_code == 200, ev.text
    polled = client.get(f"/api/v1/projects/{project_id}/infra/events", headers=pod_h)
    assert polled.status_code == 200, polled.text
    assert polled.json()["items"]

    shared = client.get(
        f"/api/v1/projects/{project_id}/infra/cache/demo",
        headers={"Authorization": "Bearer shared-should-fail"},
    )
    assert shared.status_code in (401, 403)
