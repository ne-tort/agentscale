"""AI keys contract tests (L03) — CRUD + resolve ban cli_subscription."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.application.ai_keys.service import AiKeysService
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.persistence.database import get_session_factory
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.secrets.file_store import FileSecretStore
from prodavan.main import create_app
from tests.conftest import requires_postgres


def _admin_token() -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "admin-keys",
            "aud": settings.oidc_audience,
            "exp": now + timedelta(hours=1),
            "platform_admin": True,
            "roles": ["platform.admin"],
        },
        settings.auth_test_secret,
        algorithm="HS256",
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "secrets_dir", tmp_path)
    reset_jwt_validator()
    return TestClient(create_app())


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


def test_list_requires_admin(client: TestClient) -> None:
    r = client.get("/api/v1/admin/ai-keys")
    assert r.status_code == 401


async def _create_company(name: str) -> str:
    factory = get_session_factory()
    async with factory() as session:
        co = CompanyRow(name=name)
        session.add(co)
        await session.commit()
        await session.refresh(co)
        return co.id


@requires_postgres
def test_crud_and_resolve_bans_cli_subscription(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    company_id = asyncio.run(_create_company("Keys Co"))

    create = client.post(
        "/api/v1/admin/ai-keys",
        headers=auth_headers,
        json={
            "name": "Cursor pool",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "cursor-secret-aaa",
            "company_ids": [company_id],
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert "secret" not in body
    assert body["secret_ref_prefix"].startswith("file://")
    key_id = body["id"]

    listed = client.get("/api/v1/admin/ai-keys", headers=auth_headers)
    assert listed.status_code == 200
    assert any(k["id"] == key_id for k in listed.json())
    for k in listed.json():
        assert "secret" not in k
    listed_match = next(k for k in listed.json() if k["id"] == key_id)
    assert listed_match["company_ids"] == [company_id]

    async def _resolve_ok() -> None:
        factory = get_session_factory()
        async with factory() as session:
            cred = await AiKeysService(session, FileSecretStore(tmp_path)).resolve_credentials(
                company_id=company_id, preferred_provider="cursor"
            )
            assert cred.api_kind == "cursor_sdk"
            assert cred.secret == "cursor-secret-aaa"

    asyncio.run(_resolve_ok())

    cli_co = asyncio.run(_create_company("CLI Co"))
    cli_key = client.post(
        "/api/v1/admin/ai-keys",
        headers=auth_headers,
        json={
            "name": "Max sub",
            "provider": "cursor",
            "api_kind": "cli_subscription",
            "secret": "not-a-runtime-secret",
            "company_ids": [cli_co],
        },
    )
    assert cli_key.status_code == 201

    async def _resolve_cli() -> None:
        factory = get_session_factory()
        async with factory() as session:
            with pytest.raises(AppError) as ei:
                await AiKeysService(session, FileSecretStore(tmp_path)).resolve_credentials(
                    company_id=cli_co
                )
            assert ei.value.code == "NO_AI_KEY"
            assert "cli_subscription" in (ei.value.detail or "")

    asyncio.run(_resolve_cli())

    deleted = client.delete(f"/api/v1/admin/ai-keys/{key_id}", headers=auth_headers)
    assert deleted.status_code == 204


@requires_postgres
def test_resolve_lazy_expires_past_renewal(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    company_id = asyncio.run(_create_company("Expire Co"))
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    create = client.post(
        "/api/v1/admin/ai-keys",
        headers=auth_headers,
        json={
            "name": "Past renewal",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-expired",
            "next_renewal_at": past,
            "company_ids": [company_id],
        },
    )
    assert create.status_code == 201, create.text
    key_id = create.json()["id"]

    async def _resolve_fails() -> None:
        factory = get_session_factory()
        async with factory() as session:
            with pytest.raises(AppError) as ei:
                await AiKeysService(session, FileSecretStore(tmp_path)).resolve_credentials(
                    company_id=company_id
                )
            assert ei.value.code == "NO_AI_KEY"

    asyncio.run(_resolve_fails())

    got = client.get(f"/api/v1/admin/ai-keys/{key_id}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["status"] == "expired"

    renewed = client.post(
        f"/api/v1/admin/ai-keys/{key_id}/renew",
        headers=auth_headers,
        json={"months": 1},
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["status"] == "active"
    assert renewed.json()["next_renewal_at"] is not None

    async def _resolve_ok() -> None:
        factory = get_session_factory()
        async with factory() as session:
            cred = await AiKeysService(session, FileSecretStore(tmp_path)).resolve_credentials(
                company_id=company_id
            )
            assert cred.secret == "sk-expired"

    asyncio.run(_resolve_ok())
