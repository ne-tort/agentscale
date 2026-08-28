"""Real OIDC tokens for L3b live tests (dev Keycloak, AUTH_MODE=oidc)."""

from __future__ import annotations

import os
import time

import httpx

KC_BASE = os.getenv("PRODAVAN_E2E_KC_URL", "http://127.0.0.1:8089").rstrip("/")
KC_REALM = os.getenv("PRODAVAN_E2E_KC_REALM", "prodavan")
KC_CLIENT = os.getenv("PRODAVAN_E2E_KC_CLIENT", "prodavan-flutter")
EMPLOYEE_PASSWORD = os.getenv("PRODAVAN_E2E_EMPLOYEE_PASSWORD", "test-employee-pass")

_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)


def _token_url() -> str:
    return f"{KC_BASE}/realms/{KC_REALM}/protocol/openid-connect/token"


def fetch_password_token(*, username: str, password: str) -> str:
    """ROPC against Keycloak (prodavan-flutter public client)."""
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
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


def login_via_api(
    client: httpx.Client,
    api_prefix: str,
    *,
    username: str,
    password: str,
    retries: int = 12,
    delay_sec: float = 1.0,
    connect_fail_limit: int = 3,
) -> str:
    """Prodavan Auth Service login — retries while Keycloak provisions new users."""
    last_err: RuntimeError | None = None
    connect_fails = 0
    for _ in range(retries):
        try:
            resp = client.post(
                f"{api_prefix}/auth/login",
                json={"username": username, "password": password},
            )
        except _CONNECT_ERRORS as exc:
            connect_fails += 1
            if connect_fails >= connect_fail_limit:
                raise RuntimeError(f"API unreachable at {api_prefix} ({exc})") from exc
            last_err = RuntimeError(f"API unreachable at {api_prefix} ({exc})")
            time.sleep(delay_sec)
            continue
        connect_fails = 0
        if resp.status_code == 200:
            body = resp.json()
            token = body.get("access_token")
            if token:
                return token
            last_err = RuntimeError(f"login response missing access_token: {body}")
        else:
            last_err = RuntimeError(f"API login failed ({resp.status_code}): {resp.text[:500]}")
        time.sleep(delay_sec)
    raise last_err or RuntimeError("API login failed")


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
