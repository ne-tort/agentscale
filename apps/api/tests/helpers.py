"""Shared integration test helpers."""

from httpx import AsyncClient

from prodavan.config.settings import settings


async def admin_headers(client: AsyncClient) -> dict:
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "login_id": settings.platform_admin_id,
            "password": settings.platform_admin_password,
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def register_user(client: AsyncClient, suffix: str, *, prefix: str = "user") -> dict:
    """Create company user via admin API and return login TokenResponse."""
    headers = await admin_headers(client)
    login_id = f"{prefix}-{suffix}".lower()
    create = await client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "login_id": login_id,
            "company_name": "Test Tenant",
            "password": "securepass123",
            "contact_person": "Test User",
            "email": f"{prefix}-{suffix}@example.com",
        },
    )
    assert create.status_code == 201, create.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"login_id": login_id, "password": "securepass123"},
    )
    assert login.status_code == 200, login.text
    return login.json()


async def active_cabinet_headers(
    client: AsyncClient,
    reg: dict,
    suffix: str,
    *,
    profile_id: str = "electronics-procurement",
    slug: str | None = None,
) -> tuple[str, dict]:
    token = reg["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    create = await client.post(
        "/api/v1/cabinets",
        headers=headers,
        json={
            "slug": slug or f"cab-{suffix}",
            "display_name": "Test Cabinet",
            "profile_id": profile_id,
        },
    )
    assert create.status_code == 201, create.text
    cabinet_id = create.json()["id"]
    switch = await client.post(f"/api/v1/cabinets/{cabinet_id}/switch", headers=headers)
    assert switch.status_code == 200, switch.text
    sw = switch.json()
    return cabinet_id, {"Authorization": f"Bearer {sw['access_token']}"}
