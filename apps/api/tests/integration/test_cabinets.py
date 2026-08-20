"""Cabinet M00 integration tests."""

import pytest
from httpx import AsyncClient

from prodavan.config.settings import settings
from tests.conftest import requires_postgres


async def _register(client: AsyncClient, suffix: str) -> dict:
    from tests.helpers import register_user

    return await register_user(client, suffix, prefix="cab")


@requires_postgres
@pytest.mark.asyncio
async def test_create_cabinet_pack_seed(client: AsyncClient, unique_suffix: str) -> None:
    """Pack seed: prompts, s4b capability, storage marker."""
    reg = await _register(client, unique_suffix)
    token = reg["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create = await client.post(
        "/api/v1/cabinets",
        headers=headers,
        json={
            "slug": f"proc-{unique_suffix}",
            "display_name": "Закупки",
            "profile_id": "electronics-procurement",
        },
    )
    assert create.status_code == 201, create.text
    data = create.json()
    assert data["status"] == "active"
    assert data["profile_id"] == "electronics-procurement"
    assert data["capabilities"]["integrations"]["s4b"]["enabled"] is True
    assert data["storage_uri"].startswith("prodavan://storage/cabinets/")

    tenant_id = reg["tenants"][0]["id"]
    cabinet_id = data["id"]
    root = settings.storage_root / "cabinets" / tenant_id / cabinet_id
    assert (root / ".cabinet.json").exists()
    assert (root / ".seed-complete").exists()
    assert (root / "prompts" / "AGENTS.md").exists()
    assert (root / "shops" / "allowlist.json").exists()

    caps = await client.get(f"/api/v1/cabinets/{cabinet_id}/capabilities", headers=headers)
    assert caps.status_code == 200
    assert caps.json()["effective"]["s4b"] is True

    manifest = await client.get(f"/api/v1/cabinets/{cabinet_id}/manifest", headers=headers)
    assert manifest.status_code == 200
    assert "navigation" in manifest.json()["ui"]


@requires_postgres
@pytest.mark.asyncio
async def test_switch_cabinet_updates_token(client: AsyncClient, unique_suffix: str) -> None:
    reg = await _register(client, unique_suffix)
    token = reg["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/cabinets",
        headers=headers,
        json={
            "slug": f"sw-{unique_suffix}",
            "display_name": "Switch target",
            "profile_id": "generic-assistant",
        },
    )
    assert created.status_code == 201
    cabinet_id = created.json()["id"]

    switch = await client.post(f"/api/v1/cabinets/{cabinet_id}/switch", headers=headers)
    assert switch.status_code == 200, switch.text
    sw = switch.json()
    assert sw["cabinet_id"] == cabinet_id
    assert sw["access_token"]
    assert sw["capabilities"]["integrations"]["s4b"]["enabled"] is False


@requires_postgres
@pytest.mark.asyncio
async def test_neg_cab_004_archived_switch(client: AsyncClient, unique_suffix: str) -> None:
    """NEG-CAB-004: cannot switch to archived cabinet."""
    reg = await _register(client, unique_suffix)
    headers = {"Authorization": f"Bearer {reg['access_token']}"}

    created = await client.post(
        "/api/v1/cabinets",
        headers=headers,
        json={
            "slug": f"arch-{unique_suffix}",
            "display_name": "To archive",
            "profile_id": "generic-assistant",
        },
    )
    cabinet_id = created.json()["id"]

    delete = await client.delete(f"/api/v1/cabinets/{cabinet_id}", headers=headers)
    assert delete.status_code == 204

    switch = await client.post(f"/api/v1/cabinets/{cabinet_id}/switch", headers=headers)
    assert switch.status_code == 409
    assert switch.json()["code"] == "CABINET_ARCHIVED"


@requires_postgres
@pytest.mark.asyncio
async def test_neg_cab_006_slug_conflict(client: AsyncClient, unique_suffix: str) -> None:
    """NEG-CAB-006: duplicate slug in same tenant."""
    reg = await _register(client, unique_suffix)
    headers = {"Authorization": f"Bearer {reg['access_token']}"}
    slug = f"dup-{unique_suffix}"
    payload = {
        "slug": slug,
        "display_name": "First",
        "profile_id": "generic-assistant",
    }

    first = await client.post("/api/v1/cabinets", headers=headers, json=payload)
    assert first.status_code == 201

    second = await client.post("/api/v1/cabinets", headers=headers, json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "SLUG_CONFLICT"


@requires_postgres
@pytest.mark.asyncio
async def test_list_cabinet_profiles(client: AsyncClient, unique_suffix: str) -> None:
    reg = await _register(client, unique_suffix)
    headers = {"Authorization": f"Bearer {reg['access_token']}"}

    profiles = await client.get("/api/v1/cabinet-profiles", headers=headers)
    assert profiles.status_code == 200
    ids = {p["id"] for p in profiles.json()["items"]}
    assert "electronics-procurement" in ids
    assert "generic-assistant" in ids
