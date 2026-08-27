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
    for mid, label in (
        ("mod_prompts", "Промпты"),
        ("mod_mcp", "MCP"),
        ("mod_files", "Файлы"),
    ):
        assert mid in by_id, list(by_id.keys())
        assert by_id[mid]["name"] == label
    for legacy in ("mod_example_suppliers", "mod_example_notes"):
        assert legacy not in by_id

