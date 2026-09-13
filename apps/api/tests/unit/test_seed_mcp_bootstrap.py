"""Unit tests — seed MCP zip builder + bootstrap attach."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.mcp.seed_mcp_builder import (
    SEED_PACKAGES,
    build_seed_mcp_zip,
    load_manifest,
    storage_key_for,
)
from prodavan.application.platform.seed_mcp_bootstrap_service import SeedMcpBootstrapService
from prodavan.infrastructure.files.manager import FileStoreManager, set_file_store


def test_build_seed_mcp_zip_contains_sources() -> None:
    spec = SEED_PACKAGES[0]
    assert spec.name == "prodavan-equipment"
    zip_bytes, manifest = build_seed_mcp_zip(spec)
    assert manifest["name"] == "prodavan-equipment"
    assert manifest["format"] == "mcp.package"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
    assert "manifest.json" in names
    assert "server.py" in names
    assert "equipment_catalog_search.py" not in names
    assert storage_key_for(spec, version=str(manifest["version"])) == (
        "platform/seed-mcp/prodavan-equipment-1.0.0.zip"
    )
    assert load_manifest(spec)["version"] == "1.0.0"


def test_load_manifest_fallback_when_seed_dir_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import prodavan.application.mcp.seed_mcp_builder as builder

    monkeypatch.setattr(builder, "_SEED_ROOT", Path("/nonexistent/seed_mcp_packages"))
    spec = SEED_PACKAGES[0]
    manifest = builder.load_manifest(spec)
    assert manifest["name"] == "prodavan-equipment"
    assert manifest["format"] == "mcp.package"
    assert "equipment_catalog_search" in manifest["tools"]


@pytest.mark.asyncio
async def test_attach_equipment_mcp_sets_file_ref_when_empty() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    svc = SeedMcpBootstrapService(session)
    inst = SimpleNamespace(id="minst_1")
    row = SimpleNamespace(body={"name": "prodavan-equipment", "enabled": True})

    with (
        patch.object(
            svc,
            "_session",
            session,
        ),
    ):
        session.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [inst]),
            )
        )
        svc._get_or_create_mcp_row = AsyncMock(return_value=row)  # type: ignore[method-assign]

        attached, skipped = await svc._attach_equipment_mcp(
            {"storage_key": "platform/seed-mcp/prodavan-equipment-1.0.0.zip", "asset_id": "a1"},
            {"version": "1.0.0"},
        )

    assert attached == 1
    assert skipped == 0
    assert row.body["file_ref"]["storage_key"].endswith("prodavan-equipment-1.0.0.zip")
    assert row.body["name"] == "prodavan-equipment"


@pytest.mark.asyncio
async def test_attach_equipment_mcp_skips_user_replace() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    svc = SeedMcpBootstrapService(session)
    inst = SimpleNamespace(id="minst_2")
    row = SimpleNamespace(
        body={
            "name": "prodavan-equipment",
            "file_ref": {"storage_key": "user/custom.zip", "asset_id": "cast_user"},
        }
    )
    session.execute = AsyncMock(
        return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [inst]),
        )
    )
    svc._get_or_create_mcp_row = AsyncMock(return_value=row)  # type: ignore[method-assign]

    attached, skipped = await svc._attach_equipment_mcp(
        {"storage_key": "platform/seed-mcp/prodavan-equipment-1.0.0.zip"},
        {"version": "1.0.0"},
    )
    assert attached == 0
    assert skipped == 1
    assert row.body["file_ref"]["storage_key"] == "user/custom.zip"


@pytest.mark.asyncio
async def test_ensure_asset_uploads_zip(tmp_path: Path) -> None:
    set_file_store(None)
    mgr = FileStoreManager(backend="local", storage_root=tmp_path)
    await mgr.startup()
    set_file_store(mgr)
    session = AsyncMock()
    session.flush = AsyncMock()
    session.get = AsyncMock(return_value=None)

    empty = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))
    session.execute = AsyncMock(return_value=empty)

    def _add(obj: object) -> None:
        if obj.__class__.__name__ == "ContentAssetRow":
            obj.id = "cast_seed"  # type: ignore[attr-defined]
        if obj.__class__.__name__ == "ContentBlobVersionRow":
            obj.id = "cbv_seed"  # type: ignore[attr-defined]

    session.add = _add
    svc = SeedMcpBootstrapService(session)
    asset_id, version_id = await svc._ensure_asset(
        package_name="prodavan-equipment",
        version="1.0.0",
        storage_key="platform/seed-mcp/prodavan-equipment-1.0.0.zip",
        zip_bytes=b"PK\x03\x04fake",
        sha256="abc",
        filename="prodavan-equipment-1.0.0.zip",
    )
    assert asset_id == "cast_seed"
    assert version_id == "cbv_seed"
    assert await mgr.get_bytes("platform/seed-mcp/prodavan-equipment-1.0.0.zip") == b"PK\x03\x04fake"
    await mgr.shutdown()
