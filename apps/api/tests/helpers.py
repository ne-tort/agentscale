"""Shared integration test helpers."""

from httpx import AsyncClient


async def register_user(client: AsyncClient, suffix: str, *, prefix: str = "user") -> dict:
    body = {
        "email": f"{prefix}-{suffix}@example.com",
        "password": "securepass123",
        "display_name": "Test User",
        "tenant_slug": f"tenant-{prefix}-{suffix}",
        "tenant_display_name": "Test Tenant",
    }
    resp = await client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


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
