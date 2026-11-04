"""Scenario builders for the Content API acceptance suite.

Two independent layers are exercised:

* **Pure layer** — `prodavan.application.content.contracts`,
  `.pagination` and `.download_service.safe_filename`. No database, no app.
  These assertions are runnable as-is and cover tenant boundary, ACL entry
  validation, canonical idempotency projections and cursor round-tripping.
* **HTTP layer** — a `TestClient` over `create_app()` with `AUTH_MODE=test`.
  Two tenants are created through the real company API so membership, cabinet
  and project relations exist exactly as in production. Marked
  `@requires_postgres` like the rest of `tests/integration`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")
os.environ.setdefault("OBJECT_STORE_BACKEND", "local")
os.environ.setdefault("KEYCLOAK_INVITE_MODE", "fake")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app

CONTENT = "/api/v1/content"


def token(
    *,
    sub: str,
    email: str | None = None,
    platform_admin: bool = False,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": ["platform.admin"] if platform_admin else (roles or []),
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


def bearer(sub: str, *, email: str | None = None, roles: list[str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub=sub, email=email, roles=roles)}"}


def platform_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {token(sub='padmin-content', platform_admin=True)}"}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as test_client:
        yield test_client


@dataclass(frozen=True, slots=True)
class Tenant:
    """One company plus its admin employee, cabinet and (optionally) a project."""

    company_id: str
    employee_id: str
    headers: dict[str, str]
    cabinet_id: str
    project_id: str | None = None

    def with_project(self, client: TestClient, name: str) -> "Tenant":
        created = client.post(
            f"/api/v1/cabinets/{self.cabinet_id}/projects",
            headers=self.headers,
            json={"name": name},
        )
        assert created.status_code == 201, created.text
        return Tenant(
            company_id=self.company_id,
            employee_id=self.employee_id,
            headers=self.headers,
            cabinet_id=self.cabinet_id,
            project_id=created.json()["id"],
        )

    def add_member(self, client: TestClient, *, login: str) -> "Member":
        """Invite a plain member of this tenant (membership role ``member``)."""
        invited = client.post(
            f"/api/v1/companies/{self.company_id}/employees",
            headers=platform_headers(),
            json={"login": login, "password": "member-pass", "role": "member"},
        )
        assert invited.status_code == 201, invited.text
        body = invited.json()
        return Member(
            employee_id=body["id"],
            headers=bearer(body["keycloak_sub"], email=body.get("email"), roles=["employee"]),
        )


@dataclass(frozen=True, slots=True)
class Member:
    employee_id: str
    headers: dict[str, str]


def provision_tenant(client: TestClient, *, name: str, admin_sub: str) -> Tenant:
    """Create a company through the real API and derive its admin session.

    The admin's identity is whatever the invite flow assigned — the company API
    returns ``admin_employee.keycloak_sub`` — not a sub invented by the test.
    A made-up sub authenticates fine at the JWT layer but resolves to no
    employee row, so every downstream call failed closed with 403 "No employee
    record for subject" (which is the correct answer for an unknown subject).
    ``admin_sub`` is kept so callers can tell tenants apart in logs/trace ids;
    it does not decide which employee the token maps to.
    """
    created = client.post(
        "/api/v1/companies",
        headers=platform_headers(),
        json={
            "name": name,
            "password": "test-company-pass",
            "admin_email": f"owner@{name.lower()}.test",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    company_id = body["company"]["id"]
    admin = body["admin_employee"]
    assert admin.get("keycloak_sub"), f"invite must persist keycloak_sub ({admin_sub})"
    headers = bearer(admin["keycloak_sub"], email=admin.get("email"), roles=["employee"])
    cab = client.post(
        "/api/v1/cabinets",
        headers=headers,
        json={"name": f"{name} Cabinet", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    return Tenant(
        company_id=company_id,
        employee_id=admin["id"],
        headers=headers,
        cabinet_id=cab.json()["id"],
    )


@pytest.fixture()
def tenant_a(client: TestClient):
    from tests.conftest import _postgres_available

    if not _postgres_available():
        pytest.skip("requires postgres")
    return provision_tenant(client, name="ContentA", admin_sub="emp-sub-content-a")


@pytest.fixture()
def tenant_b(client: TestClient):
    from tests.conftest import _postgres_available

    if not _postgres_available():
        pytest.skip("requires postgres")
    return provision_tenant(client, name="ContentB", admin_sub="emp-sub-content-b")


# --- HTTP helpers -----------------------------------------------------------


def create_asset(
    client: TestClient,
    tenant: Tenant,
    *,
    title: str = "spec.xlsx",
    mime: str = "application/octet-stream",
    visibility: str = "company",
    tags: list[str] | None = None,
    idempotency_key: str | None = None,
) -> dict:
    headers = dict(tenant.headers)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    response = client.post(
        f"{CONTENT}/assets",
        headers=headers,
        json={
            "owner_company_id": tenant.company_id,
            "visibility": visibility,
            "mime": mime,
            "title": title,
            "tags": tags or [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def begin_version(
    client: TestClient,
    tenant: Tenant,
    asset_id: str,
    *,
    mime: str = "application/octet-stream",
    sha256: str | None = None,
    ttl_seconds: int = 900,
) -> dict:
    body: dict = {"mime": mime, "ttl_seconds": ttl_seconds}
    if sha256:
        body["sha256"] = sha256
    response = client.post(
        f"{CONTENT}/assets/{asset_id}/versions", headers=tenant.headers, json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


def upload_version(
    client: TestClient,
    tenant: Tenant,
    asset_id: str,
    version_id: str,
    data: bytes,
    *,
    mime: str = "application/octet-stream",
    expected_sha256: str | None = None,
) -> "object":
    headers = {**tenant.headers, "Content-Type": mime}
    if expected_sha256:
        headers["X-Content-SHA256"] = expected_sha256
    return client.put(
        f"{CONTENT}/assets/{asset_id}/versions/{version_id}/upload",
        headers=headers,
        content=data,
    )


def finalize_version(
    client: TestClient,
    tenant: Tenant,
    asset_id: str,
    version_id: str,
    *,
    expected_sha256: str | None = None,
):
    headers = dict(tenant.headers)
    if expected_sha256:
        headers["X-Content-SHA256"] = expected_sha256
    return client.post(
        f"{CONTENT}/assets/{asset_id}/versions/{version_id}/finalize", headers=headers
    )


def upload_sealed_version(
    client: TestClient,
    tenant: Tenant,
    asset_id: str,
    data: bytes,
    *,
    mime: str = "application/octet-stream",
) -> dict:
    """Open, upload, and seal one version; returns the sealed version object."""
    begun = begin_version(client, tenant, asset_id, mime=mime)
    version_id = begun["version"]["id"]
    uploaded = upload_version(client, tenant, asset_id, version_id, data, mime=mime)
    assert uploaded.status_code == 200, uploaded.text
    return uploaded.json()


def store_presigned_bytes(begin_response: dict, data: bytes) -> None:
    """Emulate the browser doing the presigned PUT (local backend)."""
    key = begin_response["version"]["storage_key"]
    ensure_file_store().put_bytes_sync(key, data, content_type="application/octet-stream")


__all__ = [
    "CONTENT",
    "Member",
    "Tenant",
    "bearer",
    "begin_version",
    "client",
    "create_asset",
    "finalize_version",
    "platform_headers",
    "provision_tenant",
    "store_presigned_bytes",
    "tenant_a",
    "tenant_b",
    "token",
    "upload_sealed_version",
    "upload_version",
]
