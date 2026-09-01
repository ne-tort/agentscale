"""Shared helpers for L2 integration tests."""

from __future__ import annotations

from collections.abc import Callable


def owner_auth_from_company(
    token_fn: Callable[..., str],
    created: dict,
    *,
    roles: list[str] | None = None,
) -> dict[str, str]:
    """Bearer headers for company admin employee (JWT.sub = keycloak_sub from invite)."""
    emp = created.get("admin_employee") or {}
    sub = emp.get("keycloak_sub")
    if not sub:
        raise AssertionError("company create must return admin_employee.keycloak_sub")
    email = emp.get("email")
    payload: dict = {"sub": sub}
    if email:
        payload["email"] = email
    if roles is not None:
        payload["roles"] = roles
    return {"Authorization": f"Bearer {token_fn(**payload)}"}


def owner_bearer_token(
    token_fn: Callable[..., str],
    created: dict,
    *,
    roles: list[str] | None = None,
) -> str:
    """Access token string for company admin (JWT.sub = admin_employee.keycloak_sub)."""
    auth = owner_auth_from_company(token_fn, created, roles=roles)
    return auth["Authorization"].removeprefix("Bearer ")


def employee_auth_from_record(
    token_fn: Callable[..., str],
    record: dict,
    *,
    roles: list[str] | None = None,
) -> dict[str, str]:
    """Bearer headers for an employee row (create/invite API response)."""
    sub = record.get("keycloak_sub")
    if not sub:
        raise AssertionError("employee record must include keycloak_sub")
    email = record.get("email") or record.get("contact_email")
    payload: dict = {"sub": sub}
    if email:
        payload["email"] = email
    if roles is not None:
        payload["roles"] = roles
    return {"Authorization": f"Bearer {token_fn(**payload)}"}


def employee_bearer_token(
    token_fn: Callable[..., str],
    record: dict,
    *,
    roles: list[str] | None = None,
) -> str:
    auth = employee_auth_from_record(token_fn, record, roles=roles)
    return auth["Authorization"].removeprefix("Bearer ")


def configure_and_launch(
    client,
    owner_h: dict[str, str],
    project_id: str,
) -> dict:
    """Resolve first available AI key, patch project, launch pod (integration helper)."""
    keys = client.get(f"/api/v1/projects/{project_id}/ai-keys/available", headers=owner_h)
    assert keys.status_code == 200, keys.text
    items = keys.json().get("items") or []
    assert items, keys.text
    key_id = items[0]["id"]
    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"agent_provider": "cursor", "resolved_ai_key_id": key_id},
    )
    assert patched.status_code == 200, patched.text
    launched = client.post(f"/api/v1/projects/{project_id}/launch", headers=owner_h)
    assert launched.status_code == 200, launched.text
    return launched.json()
