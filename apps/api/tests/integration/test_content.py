"""Integration tests — Content Service (assets, aliases, ACL)."""

from __future__ import annotations

import os
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
from tests.conftest import requires_postgres


def _token(
    *,
    sub: str,
    email: str | None = None,
    platform_admin: bool = False,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": roles or ([] if not platform_admin else ["platform.admin"]),
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
def test_content_alias_bind_and_resolve(client: TestClient) -> None:
    admin_tok = _token(sub="padmin-content", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={
            "name": "ContentCo",
            "password": "test-company-pass",
            "admin_email": "boss@contentco.test",
        },
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    admin_sub = created.json()["admin_employee"]["keycloak_sub"]
    emp_hdr = {"Authorization": f"Bearer {_token(sub=admin_sub, email='boss@contentco.test', roles=['employee'])}"}

    alias = client.post(
        "/api/v1/content/aliases",
        json={"slug": "docs.readme", "owner_company_id": company_id, "label": "Readme"},
        headers=emp_hdr,
    )
    assert alias.status_code == 200, alias.text
    alias_id = alias.json()["id"]

    asset = client.post(
        "/api/v1/content/assets",
        json={"owner_company_id": company_id, "title": "readme.txt", "mime": "text/plain"},
        headers=emp_hdr,
    )
    assert asset.status_code == 200, asset.text
    asset_id = asset.json()["id"]

    ver = client.post(
        f"/api/v1/content/assets/{asset_id}/versions",
        json={"mime": "text/plain"},
        headers=emp_hdr,
    )
    assert ver.status_code == 200, ver.text
    version_id = ver.json()["version"]["id"]
    storage_key = ver.json()["version"]["storage_key"]

    store = ensure_file_store()
    store.put_bytes_sync(storage_key, b"# hello content", content_type="text/plain")

    fin = client.post(
        f"/api/v1/content/assets/{asset_id}/versions/{version_id}/finalize",
        headers=emp_hdr,
    )
    assert fin.status_code == 200, fin.text

    bind_r = client.post(
        f"/api/v1/content/aliases/{alias_id}/bind",
        json={"asset_id": asset_id, "blob_version_id": version_id},
        headers=emp_hdr,
    )
    assert bind_r.status_code == 200, bind_r.text

    resolve = client.get(
        "/api/v1/content/aliases/docs.readme/resolve",
        headers=emp_hdr,
        follow_redirects=False,
    )
    assert resolve.status_code in (200, 302), resolve.text
    if resolve.status_code == 302:
        assert resolve.headers["location"]
    else:
        assert resolve.content
