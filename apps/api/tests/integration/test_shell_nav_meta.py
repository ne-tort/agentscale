"""Integration — product modules seeded in catalog."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

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


def _token(*, sub: str, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": [] if not platform_admin else ["platform.admin"],
    }
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as client:
        yield client


@requires_postgres
def test_product_modules_seeded(client: TestClient) -> None:
    admin_h = {"Authorization": f"Bearer {_token(sub='prod-mod-admin', platform_admin=True)}"}
    modules = client.get("/api/v1/admin/modules", headers=admin_h)
    assert modules.status_code == 200, modules.text
    by_id = {m["id"]: m for m in modules.json().get("items", [])}

    # One module carries prompts, MCP packages and files.
    assert "mod_management" in by_id, list(by_id.keys())
    assert by_id["mod_management"]["name"] == "Управление"
    assert by_id["mod_management"]["status"] == "active"
    assert by_id["mod_management"]["company_grant_scope"] == "all"
    assert "mod_equipment" in by_id

    # The three former modules are archived, never deleted: their rows stay
    # recoverable, so they are still visible in the platform catalog.
    for legacy in ("mod_prompts", "mod_mcp", "mod_files"):
        assert legacy in by_id, list(by_id.keys())
        assert by_id[legacy]["status"] == "archived"

    for example in ("mod_example_suppliers", "mod_example_notes"):
        assert example not in by_id

