"""Prompt M03 integration tests."""

import pytest
from httpx import AsyncClient

from tests.conftest import requires_postgres
from tests.helpers import active_cabinet_headers, register_user


@requires_postgres
@pytest.mark.asyncio
async def test_put_prompt_file_with_etag(client: AsyncClient, unique_suffix: str) -> None:
    reg = await register_user(client, unique_suffix, prefix="prm")
    cabinet_id, headers = await active_cabinet_headers(client, reg, unique_suffix)

    read = await client.get(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers=headers,
        params={"path": "AGENTS.md"},
    )
    assert read.status_code == 200
    etag = read.json()["etag"]
    original = read.json()["content"]

    updated = original + "\n\n<!-- test edit -->\n"
    put = await client.put(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers={**headers, "If-Match": etag},
        json={"path": "AGENTS.md", "content": updated},
    )
    assert put.status_code == 200, put.text
    assert put.json()["content"] == updated

    stale = await client.put(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers={**headers, "If-Match": etag},
        json={"path": "AGENTS.md", "content": original},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "ETAG_MISMATCH"


@requires_postgres
@pytest.mark.asyncio
async def test_prompt_version_and_rollback(client: AsyncClient, unique_suffix: str) -> None:
    reg = await register_user(client, unique_suffix, prefix="prm-ver")
    cabinet_id, headers = await active_cabinet_headers(client, reg, unique_suffix)

    read = await client.get(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers=headers,
        params={"path": "AGENTS.md"},
    )
    original = read.json()["content"]
    etag = read.json()["etag"]

    edited = original + "\n\n<!-- version test -->\n"
    await client.put(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers={**headers, "If-Match": etag},
        json={"path": "AGENTS.md", "content": edited},
    )

    snap = await client.post(
        f"/api/v1/cabinets/{cabinet_id}/prompts/versions",
        headers=headers,
        json={"label": "before rollback"},
    )
    assert snap.status_code == 201, snap.text
    version_id = snap.json()["version_id"]
    assert snap.json()["files_count"] >= 1

    tree = await client.get(f"/api/v1/cabinets/{cabinet_id}/prompts/tree", headers=headers)
    assert tree.json()["current_version"] == version_id

    await client.put(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers=headers,
        json={"path": "AGENTS.md", "content": original + "\n\n<!-- mutated -->\n"},
    )

    rollback = await client.post(
        f"/api/v1/cabinets/{cabinet_id}/prompts/versions/{version_id}/rollback",
        headers=headers,
        json={"confirm": True, "create_backup_version": True},
    )
    assert rollback.status_code == 200, rollback.text

    after = await client.get(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers=headers,
        params={"path": "AGENTS.md"},
    )
    assert "<!-- version test -->" in after.json()["content"]
    assert "<!-- mutated -->" not in after.json()["content"]

    versions = await client.get(
        f"/api/v1/cabinets/{cabinet_id}/prompts/versions", headers=headers
    )
    assert versions.status_code == 200
    assert len(versions.json()["items"]) >= 2


@requires_postgres
@pytest.mark.asyncio
async def test_neg_prm_001_path_traversal(client: AsyncClient, unique_suffix: str) -> None:
    """NEG-PRM-001: path traversal blocked."""
    reg = await register_user(client, unique_suffix, prefix="prm-neg")
    cabinet_id, headers = await active_cabinet_headers(client, reg, unique_suffix)

    resp = await client.put(
        f"/api/v1/cabinets/{cabinet_id}/prompts/file",
        headers=headers,
        json={"path": "../../etc/passwd", "content": "hack"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "PATH_FORBIDDEN"
