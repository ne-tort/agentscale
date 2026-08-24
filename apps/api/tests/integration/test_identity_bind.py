"""Identity bind — duplicate employee rows per email (seed/idempotency regression)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.config.settings import settings
from prodavan.domain.identity import EmployeeStatus
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.infrastructure.persistence.database import get_session_factory
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
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


@requires_postgres
def test_me_survives_duplicate_employee_email_rows(client: TestClient) -> None:
    email = "dup-bind@prodavan.test"
    admin = _token(sub="padmin-dup", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "DupCo", "admin_email": email, "admin_display_name": "Dup Admin"},
    )
    assert created.status_code == 201, created.text

    async def _insert_duplicate_invited() -> None:
        factory = get_session_factory()
        async with factory() as session:
            session.add(
                EmployeeRow(
                    email=email,
                    display_name="Stale invite duplicate",
                    status=EmployeeStatus.INVITED,
                    keycloak_sub=None,
                )
            )
            await session.commit()

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _run() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_insert_duplicate_invited())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result(timeout=30)

    owner_tok = _token(sub="owner-dup-sub", email=email)
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
        json={"name": "Co A", "admin_email": email},
    )
    assert first.status_code == 201, first.text
    emp_id = first.json()["admin_employee"]["id"]

    second = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Co B", "admin_email": email},
    )
    assert second.status_code == 201, second.text
    assert second.json()["admin_employee"]["id"] == emp_id
