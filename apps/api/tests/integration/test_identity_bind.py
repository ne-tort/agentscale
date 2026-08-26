"""Identity bind — duplicate employee rows per email (seed/idempotency regression)."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import asyncpg
import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.config.settings import settings
from prodavan.domain.identity import EmployeeStatus
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import DATABASE_URL, requires_postgres


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


def _insert_employee_row(*, email: str, display_name: str, status: str) -> None:
    """Sync-side insert via dedicated asyncpg connection (avoid shared SQLAlchemy loop)."""
    dsn = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(dsn)

    async def _go() -> None:
        conn = await asyncpg.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            database=(parsed.path or "/prodavan").lstrip("/") or "prodavan",
        )
        try:
            await conn.execute(
                """
                INSERT INTO employees (id, email, display_name, status, keycloak_sub)
                VALUES ($1, $2, $3, $4, NULL)
                """,
                f"emp_{uuid.uuid4().hex[:16]}",
                email,
                display_name,
                status,
            )
        finally:
            await conn.close()

    asyncio.run(_go())


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


@requires_postgres
def test_me_survives_duplicate_employee_email_rows(client: TestClient) -> None:
    email = "dup-bind@prodavan.test"
    admin = _token(sub="padmin-dup", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "DupCo", "password": "test-company-pass", "admin_email": email, "admin_display_name": "Dup Admin"},
    )
    assert created.status_code == 201, created.text
    admin_sub = created.json()["admin_employee"]["keycloak_sub"]
    assert admin_sub

    _insert_employee_row(
        email=email,
        display_name="Stale invite duplicate",
        status=EmployeeStatus.INVITED,
    )

    # Authz by keycloak_sub (not silent email merge onto a different sub)
    owner_tok = _token(sub=admin_sub, email=email)
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {owner_tok}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["employee"]["email"] == email
    memberships = body["employee"]["memberships"]
    assert len(memberships) >= 1


@requires_postgres
def test_create_company_reuses_existing_employee_email(client: TestClient) -> None:
    email = "reuse-admin@prodavan.test"
    admin = _token(sub="padmin-reuse", platform_admin=True)
    first = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Co A", "password": "test-company-pass", "admin_email": email},
    )
    assert first.status_code == 201, first.text
    emp_id = first.json()["admin_employee"]["id"]

    second = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Co B", "password": "test-company-pass", "admin_email": email},
    )
    assert second.status_code == 201, second.text
    assert second.json()["admin_employee"]["id"] == emp_id
