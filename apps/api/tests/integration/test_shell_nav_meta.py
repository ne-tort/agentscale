"""Integration — example modules expose shell nav in tabs meta."""

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
def test_seed_modules_shell_nav_admin_contour(client: TestClient) -> None:
    """After migration 2026082709, demo modules inject admin shell nav."""
    admin_h = {"Authorization": f"Bearer {_token(sub='shell-nav-admin', platform_admin=True)}"}

    modules = client.get("/api/v1/admin/modules", headers=admin_h)
    assert modules.status_code == 200, modules.text
    by_id = {m["id"]: m for m in modules.json().get("items", [])}

    expected = {
        "mod_example_suppliers": "Поставщики",
        "mod_example_notes": "Заметки",
        "mod_example_hub": "Hub меню",
    }
    found = 0
    for module_id, title in expected.items():
        if module_id not in by_id:
            continue
        meta = client.get(
            f"/api/v1/admin/modules/{module_id}/meta/documents/tabs",
            headers=admin_h,
        )
        assert meta.status_code == 200, meta.text
        body = meta.json().get("body")
        assert isinstance(body, list), body
        shell_tabs = [
            t
            for t in body
            if isinstance(t, dict)
            and t.get("nav", {}).get("contour") == "admin"
            and t.get("enabled", True) is not False
        ]
        assert any(t.get("title") == title for t in shell_tabs), body
        found += 1

    assert found >= 3, f"expected 3 seed modules with shell nav, found {found}"
