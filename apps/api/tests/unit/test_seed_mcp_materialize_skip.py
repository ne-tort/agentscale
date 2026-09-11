"""Unit tests — skip code materialize of prodavan-equipment when zip present."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.projects.materialize import ProjectMaterializeService
from prodavan.infrastructure.files.manager import FileStoreManager, set_file_store


@pytest.mark.asyncio
async def test_run_materialize_skips_equipment_code_when_zip_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "storage_root", tmp_path)
    set_file_store(FileStoreManager(backend="local", storage_root=tmp_path))

    svc = ProjectMaterializeService()
    session = AsyncMock()
    session.get = AsyncMock(return_value=MagicMock(name="Cabinet"))

    zip_pkg = {
        "name": "prodavan-equipment",
        "version": "1.0.0",
        "command": "python",
        "args": ["packages/prodavan-equipment/server.py"],
        "tools": ["equipment_catalog_search"],
    }
    modules_pkg = {
        "name": "prodavan-modules",
        "version": "1.0.0",
        "command": "python",
        "args": ["packages/prodavan-modules/server.py"],
    }

    planner = MagicMock()
    planner.plan_for_project = AsyncMock(return_value=([], []))
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=([], [zip_pkg]))

    with (
        patch("prodavan.application.projects.materialize.MaterializePlanner", return_value=planner),
        patch("prodavan.application.projects.materialize.MaterializeExecutor", return_value=executor),
        patch(
            "prodavan.application.mcp.platform_modules_mcp.materialize_platform_modules_mcp",
            return_value=modules_pkg,
        ),
        patch(
            "prodavan.application.mcp.platform_modules_mcp.merge_platform_modules_mcp",
            side_effect=lambda pkgs, platform_pkg: [platform_pkg, *pkgs],
        ),
        patch(
            "prodavan.application.mcp.platform_equipment_mcp.materialize_platform_equipment_mcp"
        ) as mat_eq,
        patch.object(svc, "_write_openclaw_config", AsyncMock()),
    ):
        result = await svc._run_materialize(
            session=session,
            project_id="prj_1",
            cabinet_id="cab_1",
            cabinet_name="Cab",
            project_name="Proj",
            when="project.sync",
            enabled_module_ids=["mod_equipment"],
            prune_before=False,
            manifest=None,
        )

    mat_eq.assert_not_called()
    assert "prodavan-equipment" in result.package_names
    assert "prodavan-modules" in result.package_names


@pytest.mark.asyncio
async def test_run_materialize_falls_back_to_code_equipment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "storage_root", tmp_path)
    set_file_store(FileStoreManager(backend="local", storage_root=tmp_path))

    svc = ProjectMaterializeService()
    session = AsyncMock()
    session.get = AsyncMock(return_value=MagicMock(name="Cabinet"))

    modules_pkg = {
        "name": "prodavan-modules",
        "version": "1.0.0",
        "command": "python",
        "args": ["packages/prodavan-modules/server.py"],
    }
    equipment_pkg = {
        "name": "prodavan-equipment",
        "version": "1.0.0",
        "command": "python",
        "args": ["packages/prodavan-equipment/server.py"],
    }

    planner = MagicMock()
    planner.plan_for_project = AsyncMock(return_value=([], []))
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=([], []))

    with (
        patch("prodavan.application.projects.materialize.MaterializePlanner", return_value=planner),
        patch("prodavan.application.projects.materialize.MaterializeExecutor", return_value=executor),
        patch(
            "prodavan.application.mcp.platform_modules_mcp.materialize_platform_modules_mcp",
            return_value=modules_pkg,
        ),
        patch(
            "prodavan.application.mcp.platform_modules_mcp.merge_platform_modules_mcp",
            side_effect=lambda pkgs, platform_pkg: [platform_pkg, *pkgs],
        ),
        patch(
            "prodavan.application.mcp.platform_equipment_mcp.materialize_platform_equipment_mcp",
            return_value=equipment_pkg,
        ) as mat_eq,
        patch(
            "prodavan.application.mcp.platform_equipment_mcp.merge_platform_equipment_mcp",
            side_effect=lambda pkgs, platform_pkg: [
                *(p for p in pkgs if p.get("name") != "prodavan-equipment"),
                platform_pkg,
            ],
        ),
        patch.object(svc, "_write_openclaw_config", AsyncMock()),
    ):
        result = await svc._run_materialize(
            session=session,
            project_id="prj_2",
            cabinet_id="cab_2",
            cabinet_name="Cab",
            project_name="Proj",
            when="project.sync",
            enabled_module_ids=["mod_equipment"],
            prune_before=False,
            manifest=None,
        )

    mat_eq.assert_called_once()
    assert "prodavan-equipment" in result.package_names
