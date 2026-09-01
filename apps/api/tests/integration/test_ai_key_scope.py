"""AI key scope + cascade tests."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.application.ai_keys.service import AiKeysService
from prodavan.config.settings import settings
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.secrets.file_store import FileSecretStore
from prodavan.main import create_app
from tests.conftest import DATABASE_URL, requires_postgres
from tests.integration.support import configure_and_launch, owner_bearer_token


def _token(
    *,
    sub: str,
    platform_admin: bool = False,
    username: str | None = None,
    roles: list[str] | None = None,
    email: str | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "platform_admin": platform_admin,
        "roles": roles or ([] if not platform_admin else ["platform.admin"]),
    }
    if username:
        payload["preferred_username"] = username
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


def _run_ai_keys(coro_fn, secrets_dir: Path | None = None) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    store_root = secrets_dir if secrets_dir is not None else Path(settings.secrets_dir)

    async def _go() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with factory() as session:
                svc = AiKeysService(session, FileSecretStore(store_root))
                await coro_fn(svc, session)
        finally:
            await engine.dispose()

    asyncio.run(_go())


def _setup_company_cabinet_project(client: TestClient) -> dict[str, str]:
    admin = _token(sub=f"padmin-{uuid.uuid4().hex[:8]}", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": f"ScopeCo-{uuid.uuid4().hex[:6]}", "password": "test-company-pass", "admin_email": "scope@test.local"},
    )
    assert created.status_code == 201, created.text
    company = created.json()["company"]
    company_id = company["id"]
    owner_tok = owner_bearer_token(_token, created.json())
    org_tok = _token(sub=company["keycloak_sub"], username=company_id, roles=["company"])

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Platform scope key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "scope-secret-1",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    key_id = key.json()["id"]

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "ScopeCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "ScopeProj", "agent_provider": "cursor"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    return {
        "admin": admin,
        "company_id": company_id,
        "org_tok": org_tok,
        "owner_tok": owner_tok,
        "key_id": key_id,
        "cabinet_id": cabinet_id,
        "project_id": project_id,
    }


@requires_postgres
def test_platform_key_scope_bindings_allowed(client: TestClient, tmp_path: Path) -> None:
    ctx = _setup_company_cabinet_project(client)
    h = {"Authorization": f"Bearer {ctx['org_tok']}"}

    fetched = client.get(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}",
        headers=h,
    )
    assert fetched.status_code == 200
    assert fetched.json()["writable"] is False

    put = client.put(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}/scope-bindings",
        headers=h,
        json={
            "employee_ids": [],
            "cabinet_ids": [ctx["cabinet_id"]],
            "project_ids": [ctx["project_id"]],
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert ctx["cabinet_id"] in body["cabinet_ids"]
    assert ctx["project_id"] in body["project_ids"]


@requires_postgres
def test_scope_cabinet_binding_filters_availability(client: TestClient, tmp_path: Path) -> None:
    ctx = _setup_company_cabinet_project(client)
    h = {"Authorization": f"Bearer {ctx['org_tok']}"}

    other_cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {ctx['owner_tok']}"},
        json={"name": "OtherCab", "company_id": ctx["company_id"]},
    )
    assert other_cab.status_code in (200, 201), other_cab.text
    other_cabinet_id = other_cab.json()["id"]

    other_proj = client.post(
        f"/api/v1/cabinets/{other_cabinet_id}/projects",
        headers={"Authorization": f"Bearer {ctx['owner_tok']}"},
        json={"name": "OtherProj", "agent_provider": "cursor"},
    )
    assert other_proj.status_code == 201, other_proj.text
    other_project_id = other_proj.json()["id"]

    put = client.put(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}/scope-bindings",
        headers=h,
        json={"employee_ids": [], "cabinet_ids": [ctx["cabinet_id"]], "project_ids": []},
    )
    assert put.status_code == 200, put.text

    async def _check(svc: AiKeysService, session) -> None:
        in_scope = await session.get(ProjectRow, ctx["project_id"])
        out_scope = await session.get(ProjectRow, other_project_id)
        assert in_scope is not None and out_scope is not None
        assert await svc.project_is_key_allowed(in_scope, ctx["key_id"]) is True
        assert await svc.project_is_key_allowed(out_scope, ctx["key_id"]) is False
        available = await svc.list_available_keys_for_project(project=in_scope)
        assert any(k["id"] == ctx["key_id"] for k in available)
        unavailable = await svc.list_available_keys_for_project(project=out_scope)
        assert not any(k["id"] == ctx["key_id"] for k in unavailable)

    _run_ai_keys(_check)


@requires_postgres
def test_resolve_for_project_respects_scope(client: TestClient, tmp_path: Path) -> None:
    ctx = _setup_company_cabinet_project(client)
    h = {"Authorization": f"Bearer {ctx['org_tok']}"}
    put = client.put(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}/scope-bindings",
        headers=h,
        json={"employee_ids": [], "cabinet_ids": [ctx["cabinet_id"]], "project_ids": []},
    )
    assert put.status_code == 200, put.text

    async def _check(svc: AiKeysService, session) -> None:
        project = await session.get(ProjectRow, ctx["project_id"])
        assert project is not None
        cred = await svc.resolve_credentials_for_project(project=project)
        assert cred.key_id == ctx["key_id"]
        assert cred.secret == "scope-secret-1"

    _run_ai_keys(_check)


@requires_postgres
def test_cabinet_unbind_pauses_affected_project(client: TestClient, tmp_path: Path) -> None:
    ctx = _setup_company_cabinet_project(client)
    h = {"Authorization": f"Bearer {ctx['org_tok']}"}
    owner_h = {"Authorization": f"Bearer {ctx['owner_tok']}"}
    configure_and_launch(client, owner_h, ctx["project_id"])

    other_cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {ctx['owner_tok']}"},
        json={"name": "AnchorCab", "company_id": ctx["company_id"]},
    )
    assert other_cab.status_code in (200, 201), other_cab.text
    anchor_cabinet_id = other_cab.json()["id"]

    client.put(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}/scope-bindings",
        headers=h,
        json={
            "employee_ids": [],
            "cabinet_ids": [ctx["cabinet_id"], anchor_cabinet_id],
            "project_ids": [],
        },
    )

    client.put(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}/scope-bindings",
        headers=h,
        json={"employee_ids": [], "cabinet_ids": [anchor_cabinet_id], "project_ids": []},
    )

    async def _check(_svc: AiKeysService, session) -> None:
        project = await session.get(ProjectRow, ctx["project_id"])
        assert project is not None
        assert project.status == ProjectStatus.PAUSED

    _run_ai_keys(_check)


@requires_postgres
def test_employee_unbind_does_not_pause_project(client: TestClient, tmp_path: Path) -> None:
    ctx = _setup_company_cabinet_project(client)
    h = {"Authorization": f"Bearer {ctx['org_tok']}"}
    owner_h = {"Authorization": f"Bearer {ctx['owner_tok']}"}
    configure_and_launch(client, owner_h, ctx["project_id"])

    me = client.get("/api/v1/me", headers=owner_h)
    assert me.status_code == 200, me.text
    employee_id = me.json()["employee"]["id"]

    client.put(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}/scope-bindings",
        headers=h,
        json={"employee_ids": [employee_id], "cabinet_ids": [], "project_ids": []},
    )

    async def _set_resolved(svc: AiKeysService, session) -> None:
        project = await session.get(ProjectRow, ctx["project_id"])
        assert project is not None
        project.resolved_ai_key_id = ctx["key_id"]
        await session.commit()

    _run_ai_keys(_set_resolved)

    client.put(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}/scope-bindings",
        headers=h,
        json={"employee_ids": [], "cabinet_ids": [], "project_ids": []},
    )

    async def _check(_svc: AiKeysService, session) -> None:
        project = await session.get(ProjectRow, ctx["project_id"])
        assert project is not None
        assert project.status == ProjectStatus.ACTIVE
        assert project.resolved_ai_key_id == ctx["key_id"]

    _run_ai_keys(_check)


@requires_postgres
def test_admin_unbind_clears_scope(client: TestClient, tmp_path: Path) -> None:
    ctx = _setup_company_cabinet_project(client)
    h = {"Authorization": f"Bearer {ctx['org_tok']}"}
    admin_h = {"Authorization": f"Bearer {ctx['admin']}"}

    client.put(
        f"/api/v1/companies/{ctx['company_id']}/ai-keys/{ctx['key_id']}/scope-bindings",
        headers=h,
        json={
            "employee_ids": [],
            "cabinet_ids": [ctx["cabinet_id"]],
            "project_ids": [ctx["project_id"]],
        },
    )

    unbind = client.put(
        f"/api/v1/admin/ai-keys/{ctx['key_id']}/companies",
        headers=admin_h,
        json={"company_ids": []},
    )
    assert unbind.status_code == 200, unbind.text

    async def _check(_svc: AiKeysService, session) -> None:
        from sqlalchemy import select

        from prodavan.infrastructure.persistence.models.ai_keys import (
            CabinetAiKeyBindingRow,
            EmployeeAiKeyBindingRow,
            ProjectAiKeyBindingRow,
        )

        eq = await session.execute(
            select(EmployeeAiKeyBindingRow).where(
                EmployeeAiKeyBindingRow.key_id == ctx["key_id"],
                EmployeeAiKeyBindingRow.company_id == ctx["company_id"],
            )
        )
        assert list(eq.scalars().all()) == []
        cq = await session.execute(
            select(CabinetAiKeyBindingRow).where(CabinetAiKeyBindingRow.key_id == ctx["key_id"])
        )
        assert list(cq.scalars().all()) == []
        pq = await session.execute(
            select(ProjectAiKeyBindingRow).where(
                ProjectAiKeyBindingRow.key_id == ctx["key_id"],
                ProjectAiKeyBindingRow.company_id == ctx["company_id"],
            )
        )
        assert list(pq.scalars().all()) == []
        project = await session.get(ProjectRow, ctx["project_id"])
        assert project is not None
        assert project.resolved_ai_key_id is None

    _run_ai_keys(_check)
