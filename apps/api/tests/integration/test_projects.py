"""Project M01 integration tests."""

import pytest
from httpx import AsyncClient

from prodavan.config.settings import settings
from tests.conftest import requires_postgres


async def _register(client: AsyncClient, suffix: str) -> dict:
    body = {
        "email": f"prj-{suffix}@example.com",
        "password": "securepass123",
        "display_name": "Project User",
        "tenant_slug": f"prj-tenant-{suffix}",
        "tenant_display_name": "Project Tenant",
    }
    resp = await client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _cabinet_with_token(client: AsyncClient, reg: dict, suffix: str) -> tuple[str, dict]:
    token = reg["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    create = await client.post(
        "/api/v1/cabinets",
        headers=headers,
        json={
            "slug": f"proc-{suffix}",
            "display_name": "Proc cabinet",
            "profile_id": "electronics-procurement",
        },
    )
    assert create.status_code == 201, create.text
    cabinet_id = create.json()["id"]
    switch = await client.post(f"/api/v1/cabinets/{cabinet_id}/switch", headers=headers)
    assert switch.status_code == 200, switch.text
    sw = switch.json()
    headers = {"Authorization": f"Bearer {sw['access_token']}"}
    return cabinet_id, headers


@requires_postgres
@pytest.mark.asyncio
async def test_create_project_storage(client: AsyncClient, unique_suffix: str) -> None:
    reg = await _register(client, unique_suffix)
    cabinet_id, headers = await _cabinet_with_token(client, reg, unique_suffix)

    create = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={"slug": f"client-{unique_suffix}", "display_name": "Client Alpha"},
    )
    assert create.status_code == 201, create.text
    data = create.json()
    assert data["status"] == "active"
    assert data["workspace_key"].startswith("cab:")
    assert data["storage_uri"].startswith("prodavan://storage/")

    tenant_id = reg["tenants"][0]["id"]
    project_id = data["id"]
    root = (
        settings.storage_root
        / "cabinets"
        / tenant_id
        / cabinet_id
        / "projects"
        / project_id
    )
    assert (root / "project.json").exists()
    assert (root / "commerce.sqlite").exists()
    assert (root / "inbox").is_dir()
    assert (root / "runs").is_dir()
    assert (root / "export").is_dir()

    open_resp = await client.post(f"/api/v1/projects/{project_id}/open", headers=headers)
    assert open_resp.status_code == 200
    assert open_resp.json()["access_token"]
    assert "inbox" in open_resp.json()["storage_paths"]


@requires_postgres
@pytest.mark.asyncio
async def test_neg_prj_002_no_active_cabinet(client: AsyncClient, unique_suffix: str) -> None:
    """NEG-PRJ-002: create project without active cabinet."""
    reg = await _register(client, unique_suffix)
    headers = {"Authorization": f"Bearer {reg['access_token']}"}
    resp = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={"slug": f"no-cab-{unique_suffix}", "display_name": "Fail"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "NO_ACTIVE_CABINET"


@requires_postgres
@pytest.mark.asyncio
async def test_neg_prj_003_slug_conflict(client: AsyncClient, unique_suffix: str) -> None:
    """NEG-PRJ-003: duplicate slug in cabinet."""
    reg = await _register(client, unique_suffix)
    _, headers = await _cabinet_with_token(client, reg, unique_suffix)
    slug = f"dup-{unique_suffix}"
    payload = {"slug": slug, "display_name": "First"}

    first = await client.post("/api/v1/projects", headers=headers, json=payload)
    assert first.status_code == 201

    second = await client.post("/api/v1/projects", headers=headers, json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "SLUG_CONFLICT"


@requires_postgres
@pytest.mark.asyncio
async def test_prompts_tree_and_file(client: AsyncClient, unique_suffix: str) -> None:
    reg = await _register(client, unique_suffix)
    cabinet_id, headers = await _cabinet_with_token(client, reg, unique_suffix)

    tree = await client.get(f"/api/v1/cabinets/{cabinet_id}/prompts/tree", headers=headers)
    assert tree.status_code == 200, tree.text
    assert tree.json()["root"] == "prompts/"
    assert len(tree.json()["tree"]) >= 1

    file_resp = await client.get(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers=headers,
        params={"path": "AGENTS.md"},
    )
    assert file_resp.status_code == 200
    assert "Commerce" in file_resp.json()["content"] or len(file_resp.json()["content"]) > 0
