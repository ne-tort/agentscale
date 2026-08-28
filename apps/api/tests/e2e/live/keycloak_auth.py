"""Real OIDC tokens for L3b live tests (dev Keycloak, AUTH_MODE=oidc)."""

from __future__ import annotations

import os

import httpx

KC_BASE = os.getenv("PRODAVAN_E2E_KC_URL", "http://127.0.0.1:8089").rstrip("/")
KC_REALM = os.getenv("PRODAVAN_E2E_KC_REALM", "prodavan")
KC_CLIENT = os.getenv("PRODAVAN_E2E_KC_CLIENT", "prodavan-flutter")
EMPLOYEE_PASSWORD = os.getenv("PRODAVAN_E2E_EMPLOYEE_PASSWORD", "test-employee-pass")


def _token_url() -> str:
    return f"{KC_BASE}/realms/{KC_REALM}/protocol/openid-connect/token"


def fetch_password_token(*, username: str, password: str) -> str:
    """ROPC against Keycloak (prodavan-flutter public client)."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            _token_url(),
            data={
                "grant_type": "password",
                "client_id": KC_CLIENT,
                "username": username,
                "password": password,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Keycloak token failed ({resp.status_code}): {resp.text[:500]}")
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise RuntimeError(f"Keycloak response missing access_token: {body}")
        return token


def fetch_platform_admin_token(client: httpx.Client | None = None, api_prefix: str | None = None) -> str:
    """Prefer Prodavan Auth Service (reachable on :8088); fall back to direct Keycloak ROPC."""
    user = os.getenv("PRODAVAN_E2E_ADMIN_USER", "admin")
    pwd = os.getenv("PRODAVAN_E2E_ADMIN_PASSWORD", "admin")
    if client is not None and api_prefix:
        try:
            return login_via_api(client, api_prefix, username=user, password=pwd)
        except RuntimeError:
            pass
    return fetch_password_token(username=user, password=pwd)


def login_via_api(client: httpx.Client, api_prefix: str, *, username: str, password: str) -> str:
    """Prodavan Auth Service login — same tokens Flutter uses against live API."""
    resp = client.post(
        f"{api_prefix}/auth/login",
        json={"username": username, "password": password},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"API login failed ({resp.status_code}): {resp.text[:500]}")
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise RuntimeError(f"login response missing access_token: {body}")
    return token


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
