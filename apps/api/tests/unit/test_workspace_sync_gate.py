"""Workspace-sync gating: module row writes must not mark the project
«требует обновления» when the table does not feed the workspace.

Only tables referenced by materialize rules (catalogs → manifest.json,
equipment_mcp → MCP package) justify a project sync; everything else
(позиции/офферы/бюджет/закупка/поставщики) lives in DB only.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import prodavan.application.projects.workspace_sync_policy as sync_policy
from prodavan.application.platform.product_module_seeds import mod_equipment_meta
from prodavan.application.projects.project_runtime_module_service import (
    ProjectRuntimeModuleService,
)
from prodavan.application.projects.workspace_sync_policy import (
    materialize_source_tables,
    table_feeds_workspace,
)
from prodavan.domain.identity import Principal


def test_materialize_source_tables_equipment_seed() -> None:
    meta = mod_equipment_meta()
    sources = materialize_source_tables(meta["materialize"], meta["columns"])
    # catalogs → workspace manifest, equipment_mcp → MCP zip,
    # equipment_prompts → промпты подбора (prompt_paths в workspace)
    assert sources == {"catalogs", "equipment_mcp", "equipment_prompts"}


def test_materialize_source_tables_empty_defaults_safe() -> None:
    assert materialize_source_tables(None, None) == set()
    assert materialize_source_tables([{"source": {"type": "rows"}}], []) == set()
    # disabled rules не считаются
    assert (
        materialize_source_tables(
            [
                {
                    "enabled": False,
                    "source": {"type": "rows", "table_slug": "catalogs"},
                }
            ],
            [],
        )
        == set()
    )


@pytest.mark.asyncio
async def test_table_feeds_workspace_against_seed_docs(monkeypatch) -> None:
    meta = mod_equipment_meta()

    class _Result:
        def __init__(self, rows: list) -> None:
            self._rows = rows

        def all(self) -> list:
            return self._rows

    docs = [("materialize", meta["materialize"]), ("columns", meta["columns"])]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Result(docs))

    assert await table_feeds_workspace(session, module_id="mod_equipment", table_slug="catalogs")
    assert await table_feeds_workspace(session, module_id="mod_equipment", table_slug="equipment_mcp")
    assert not await table_feeds_workspace(
        session, module_id="mod_equipment", table_slug="request_lines"
    )
    assert not await table_feeds_workspace(
        session, module_id="mod_equipment", table_slug="found_offers"
    )
    assert not await table_feeds_workspace(
        session, module_id="mod_equipment", table_slug="budget_lines"
    )
    assert not await table_feeds_workspace(
        session, module_id="mod_equipment", table_slug="procurement"
    )

    # меты нет → безопасный дефолт True
    session.execute = AsyncMock(return_value=_Result([]))
    assert await table_feeds_workspace(session, module_id="mod_equipment", table_slug="request_lines")


def _runtime_svc() -> ProjectRuntimeModuleService:
    session = AsyncMock()
    svc = ProjectRuntimeModuleService(session)
    svc._require_project = AsyncMock(
        return_value=SimpleNamespace(id="proj_1", cabinet_id="cab_1")
    )
    svc._sot_for_project = AsyncMock(return_value=SimpleNamespace(id="inst_1"))
    svc._instances.resolve_tables_body = AsyncMock(
        return_value=[{"slug": "catalogs", "scope": {"chats": "all"}}]
    )
    svc._instances.resolve_columns_body = AsyncMock(return_value=[])
    svc._instances.get_data_row = AsyncMock(
        return_value={"row_id": "r1", "body": {"name": "old"}}
    )
    svc._instances.upsert_data_row = AsyncMock(
        return_value={"row_id": "r1", "body": {"name": "new"}}
    )
    return svc


@pytest.mark.asyncio
async def test_runtime_row_write_feeding_table_schedules_sync(monkeypatch) -> None:
    svc = _runtime_svc()
    defer_calls: list[dict] = []

    async def _defer(session, **kwargs):  # noqa: ANN001, ANN003
        defer_calls.append(kwargs)
        from prodavan.application.projects.workspace_sync_policy import (
            WorkspaceSyncNotification,
        )

        return WorkspaceSyncNotification(mode="deferred", marked_outdated=1)

    monkeypatch.setattr(sync_policy, "defer_or_schedule_project_sync", _defer)
    monkeypatch.setattr(
        sync_policy, "table_feeds_workspace", AsyncMock(return_value=True)
    )

    out = await svc.update_data_row(
        project_id="proj_1",
        module_id="mod_equipment",
        table_slug="catalogs",
        row_id="r1",
        body={"name": "new"},
        principal=Principal(sub="u1"),
        employee=None,
        run_actions=False,
    )
    assert defer_calls, "таблица кормит workspace — sync обязан планироваться"
    assert out["rematerialize"]["marked_outdated"] == 1


@pytest.mark.asyncio
async def test_runtime_row_write_non_feeding_table_skips_sync(monkeypatch) -> None:
    svc = _runtime_svc()
    defer_calls: list[dict] = []

    async def _defer(session, **kwargs):  # noqa: ANN001, ANN003
        defer_calls.append(kwargs)
        raise AssertionError("не должно доходить до defer для не-workspace таблицы")

    monkeypatch.setattr(sync_policy, "defer_or_schedule_project_sync", _defer)
    monkeypatch.setattr(
        sync_policy, "table_feeds_workspace", AsyncMock(return_value=False)
    )

    out = await svc.update_data_row(
        project_id="proj_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        row_id="r1",
        body={"title": "SSD"},
        principal=Principal(sub="u1"),
        employee=None,
        run_actions=False,
    )
    assert defer_calls == []
    assert out["rematerialize"] is None
