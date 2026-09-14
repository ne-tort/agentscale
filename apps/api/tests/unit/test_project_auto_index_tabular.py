"""Project leaf rows must auto-run content.index_tabular after file upload."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.domain.identity import Principal


@pytest.mark.asyncio
async def test_project_update_runs_auto_index_and_returns_refreshed_body() -> None:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    session = AsyncMock()
    session.commit = AsyncMock()
    svc = ProjectRuntimeModuleService.__new__(ProjectRuntimeModuleService)
    svc._session = session
    svc._instances = MagicMock()
    project = SimpleNamespace(id="prj_1", cabinet_id="cab_1")
    svc._require_project = AsyncMock(return_value=project)
    leaf = SimpleNamespace(id="minst_leaf")
    svc._sot_for_project = AsyncMock(return_value=leaf)
    svc._instances.resolve_columns_body = AsyncMock(return_value=[])
    svc._instances.resolve_tables_body = AsyncMock(return_value=[])
    existing = {
        "row_id": "row_1",
        "table_slug": "catalogs",
        "body": {"status": "draft"},
        "created_by": "u1",
    }
    indexed = {
        "row_id": "row_1",
        "table_slug": "catalogs",
        "body": {
            "status": "ready",
            "source_file": {"storage_key": "k1", "filename": "a.csv"},
            "columns_json": '["sku","price"]',
            "row_count": 2,
        },
        "created_by": "u1",
    }
    svc._instances.get_data_row = AsyncMock(side_effect=[existing, indexed])
    svc._instances.upsert_data_row = AsyncMock(
        return_value={
            "row_id": "row_1",
            "table_slug": "catalogs",
            "body": {
                "status": "draft",
                "source_file": {"storage_key": "k1", "filename": "a.csv"},
            },
        }
    )
    svc._maybe_run_row_actions = AsyncMock()

    principal = Principal(sub="u1", roles=frozenset())
    with (
        patch(
            "prodavan.application.projects.project_runtime_module_service.validate_row_with_columns",
            side_effect=lambda **kw: kw["body"],
        ),
        patch(
            "prodavan.application.projects.workspace_sync_policy.defer_or_schedule_project_sync",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(rematerialize_alias=lambda: {}),
        ),
    ):
        out = await svc.update_data_row(
            project_id="prj_1",
            module_id="mod_equipment",
            table_slug="catalogs",
            row_id="row_1",
            body={"source_file": {"storage_key": "k1", "filename": "a.csv"}},
            principal=principal,
            employee=None,
        )

    svc._maybe_run_row_actions.assert_awaited_once()
    kwargs = svc._maybe_run_row_actions.await_args.kwargs
    assert kwargs["project_id"] == "prj_1"
    assert kwargs["cabinet_id"] == "cab_1"
    assert kwargs["table_slug"] == "catalogs"
    assert kwargs["row_id"] == "row_1"
    assert out["body"]["status"] == "ready"
    assert out["body"]["row_count"] == 2


@pytest.mark.asyncio
async def test_project_update_skips_actions_when_run_actions_false() -> None:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    session = AsyncMock()
    session.commit = AsyncMock()
    svc = ProjectRuntimeModuleService.__new__(ProjectRuntimeModuleService)
    svc._session = session
    svc._instances = MagicMock()
    project = SimpleNamespace(id="prj_1", cabinet_id="cab_1")
    svc._require_project = AsyncMock(return_value=project)
    leaf = SimpleNamespace(id="minst_leaf")
    svc._sot_for_project = AsyncMock(return_value=leaf)
    svc._instances.resolve_columns_body = AsyncMock(return_value=[])
    svc._instances.resolve_tables_body = AsyncMock(return_value=[])
    existing = {"row_id": "row_1", "body": {}, "created_by": "u1"}
    saved = {"row_id": "row_1", "body": {"status": "indexing"}}
    svc._instances.get_data_row = AsyncMock(return_value=existing)
    svc._instances.upsert_data_row = AsyncMock(return_value=saved)
    svc._maybe_run_row_actions = AsyncMock()

    with (
        patch(
            "prodavan.application.projects.project_runtime_module_service.validate_row_with_columns",
            side_effect=lambda **kw: kw["body"],
        ),
        patch(
            "prodavan.application.projects.workspace_sync_policy.defer_or_schedule_project_sync",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(rematerialize_alias=lambda: {}),
        ),
    ):
        out = await svc.update_data_row(
            project_id="prj_1",
            module_id="mod_equipment",
            table_slug="catalogs",
            row_id="row_1",
            body={"status": "indexing"},
            principal=Principal(sub="u1", roles=frozenset()),
            employee=None,
            run_actions=False,
        )

    svc._maybe_run_row_actions.assert_not_awaited()
    assert out["body"]["status"] == "indexing"


@pytest.mark.asyncio
async def test_maybe_auto_index_routes_project_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.modules.module_action_executor import ModuleActionExecutor

    class _FakeSession:
        async def execute(self, _query):  # noqa: ANN001
            class _Result:
                def scalar_one_or_none(self):
                    return [
                        {
                            "id": "index",
                            "kind": "content.index_tabular",
                            "params": {"table_slug": "catalogs", "source_column": "source_file"},
                            "trigger": {"on": ["row.updated"]},
                        }
                    ]

            return _Result()

    executor = ModuleActionExecutor(session=_FakeSession())  # type: ignore[arg-type]
    indexed: list[dict] = []

    async def _fake_index(**kwargs):  # noqa: ANN003
        indexed.append(kwargs)
        return {"kind": "content.index_tabular", "status": "ready"}

    async def _fake_list(**kwargs):  # noqa: ANN003
        assert kwargs["project_id"] == "prj_1"
        return [
            {
                "row_id": "row_1",
                "body": {
                    "status": "draft",
                    "source_file": {"storage_key": "k1", "filename": "a.csv"},
                },
            }
        ]

    monkeypatch.setattr(executor, "_index_tabular", _fake_index)
    monkeypatch.setattr(executor, "_list_module_rows", _fake_list)

    await executor.maybe_auto_index_tabular(
        cabinet_id="cab_1",
        project_id="prj_1",
        module_id="mod_equipment",
        table_slug="catalogs",
        row_id="row_1",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
    )

    assert len(indexed) == 1
    assert indexed[0]["project_id"] == "prj_1"
