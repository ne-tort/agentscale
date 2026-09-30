"""Regression tests for chat-scoped module auto-actions (wave 9 follow-up).

Live dev diagnosed three defects after PR #55:

1. HTTP 500 on every manual module row create/update on the cabinet contour:
   ``CabinetModuleService._maybe_run_row_actions`` passed ``session_id`` to
   ``ModuleActionExecutor.maybe_auto_index_tabular`` which had no such kwarg
   (TypeError propagated: only AppError is caught around the action run).
2. ``equipment.budget_sync`` ignored chat scope: source reads (lines / offers
   / budget / catalogs) were unscoped and budget row writes went through the
   cabinet contour without a session (stamped synthetic ``main``), so the
   active chat's «Бюджетирование» view stayed empty.
3. ``_update_row_for_scope`` passed ``session_id`` to ``_update_module_row``
   which had no such kwarg (TypeError swallowed by budget-sync best-effort
   try/except, so budget row updates silently never happened).

Also: agent (MCP / Pod Bridge) writes now run the same auto-actions with the
row's stamped session, so budget rows materialize for agent-written lines.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService
from prodavan.application.modules.module_action_executor import ModuleActionExecutor
from prodavan.application.pod_identity.bridge import PodBridgeClaims
from prodavan.application.tenant_infra.pod_modules import PodModuleDataService
from prodavan.domain.identity import Principal


def _principal() -> Principal:
    return Principal(sub="u1", roles=frozenset())


def _executor() -> ModuleActionExecutor:
    return ModuleActionExecutor(session=MagicMock())  # type: ignore[arg-type]


def _cabinet_svc() -> CabinetModuleService:
    session = AsyncMock()
    svc = CabinetModuleService.__new__(CabinetModuleService)
    svc._session = session
    svc._access = MagicMock()
    svc._access.require_access = AsyncMock()
    svc._instances = MagicMock()
    svc._instances.ensure_cabinet_instance = AsyncMock(
        return_value=SimpleNamespace(id="minst_cab")
    )
    svc._instances.sot_may_edit = AsyncMock(return_value=True)
    svc._instances.resolve_tables_body = AsyncMock(
        return_value=[{"slug": "request_lines", "scope": {"chats": "current"}}]
    )
    svc._instances.resolve_columns_body = AsyncMock(return_value=[])
    svc._require_module_binding = AsyncMock()
    svc._cabinet_sot = AsyncMock(return_value=SimpleNamespace(id="minst_cab"))
    svc._schedule_rematerialize = AsyncMock(return_value={})
    return svc


# --------------------------------------------------------------------------
# 1. Cabinet contour: auto-actions accept the session kwarg (no TypeError 500)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cabinet_create_runs_auto_actions_with_session(monkeypatch) -> None:
    """Create on chats=current table must not 500 on the action-executor call."""
    svc = _cabinet_svc()
    svc._instances.create_data_row = AsyncMock(
        return_value={"row_id": "row_1", "table_slug": "request_lines", "body": {}}
    )
    svc._instances.get_data_row = AsyncMock(
        return_value={"row_id": "row_1", "table_slug": "request_lines", "body": {}}
    )

    index_calls: list[dict] = []
    budget_calls: list[dict] = []

    async def _index(self, **kwargs):  # noqa: ANN001, ANN003
        index_calls.append(kwargs)

    async def _budget(self, **kwargs):  # noqa: ANN001, ANN003
        budget_calls.append(kwargs)

    monkeypatch.setattr(ModuleActionExecutor, "maybe_auto_index_tabular", _index)
    monkeypatch.setattr(ModuleActionExecutor, "maybe_auto_budget_sync", _budget)

    out = await svc.create_data_row(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        body={"title": "SSD"},
        principal=_principal(),
        employee=None,
        session_id="ags_123",
    )

    assert out["row_id"] == "row_1"
    assert index_calls and index_calls[0]["session_id"] == "ags_123"
    assert budget_calls and budget_calls[0]["session_id"] == "ags_123"


@pytest.mark.asyncio
async def test_cabinet_update_runs_auto_actions_with_session(monkeypatch) -> None:
    svc = _cabinet_svc()
    svc._instances.get_data_row = AsyncMock(
        return_value={
            "row_id": "row_1",
            "session_id": "ags_123",
            "body": {"title": "SSD", "session_id": "ags_123"},
        }
    )
    svc._instances.upsert_data_row = AsyncMock(
        return_value={"row_id": "row_1", "session_id": "ags_123", "body": {}}
    )

    budget_calls: list[dict] = []

    async def _budget(self, **kwargs):  # noqa: ANN001, ANN003
        budget_calls.append(kwargs)

    async def _index(self, **kwargs):  # noqa: ANN001, ANN003
        _ = kwargs

    monkeypatch.setattr(ModuleActionExecutor, "maybe_auto_index_tabular", _index)
    monkeypatch.setattr(ModuleActionExecutor, "maybe_auto_budget_sync", _budget)

    await svc.update_data_row(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        row_id="row_1",
        body={"qty": 2},
        principal=_principal(),
        employee=None,
        session_id="ags_123",
    )
    assert budget_calls and budget_calls[0]["session_id"] == "ags_123"


@pytest.mark.asyncio
async def test_real_executor_signatures_accept_session(monkeypatch) -> None:
    """Executor kwargs contract: auto-actions + scoped helpers take session_id.

    Runs the real methods with no matching actions (``_list_actions`` -> []),
    so a missing ``session_id`` kwarg would raise TypeError.
    """
    executor = _executor()
    monkeypatch.setattr(executor, "_list_actions", AsyncMock(return_value=[]))

    await executor.maybe_auto_index_tabular(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        row_id="row_1",
        principal=_principal(),
        employee=None,
        session_id="ags_123",
    )
    await executor.maybe_auto_budget_sync(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        principal=_principal(),
        employee=None,
        session_id="ags_123",
    )
    # Scoped read helper: module contour receives the session.
    monkeypatch.setattr(executor, "_list_module_rows", AsyncMock(return_value=[]))
    await executor._list_rows_for_scope(
        module_id="mod_equipment",
        table_slug="request_lines",
        principal=_principal(),
        employee=None,
        cabinet_id="cab_1",
        session_id="ags_123",
    )
    kwargs = executor._list_module_rows.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["session_id"] == "ags_123"

    # Scoped update helper threads session into the module update.
    monkeypatch.setattr(executor, "_update_module_row", AsyncMock(return_value={}))
    await executor._update_row_for_scope(
        module_id="mod_equipment",
        table_slug="budget_lines",
        row_id="row_1",
        body={},
        principal=_principal(),
        employee=None,
        cabinet_id="cab_1",
        session_id="ags_123",
    )
    kwargs = executor._update_module_row.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["session_id"] == "ags_123"


# --------------------------------------------------------------------------
# 2. Budget sync: scoped source reads + session-stamped budget writes
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_sync_scopes_reads_and_stamps_writes(monkeypatch) -> None:
    executor = _executor()
    rows_by_table: dict[str, list[dict]] = {
        "request_lines": [
            {"row_id": "line_1", "body": {"title": "SSD 1TB", "qty": 1, "part_number": "MZ",
                                           "selected_offer_id": "offer_1"}},
        ],
        "found_offers": [
            {"row_id": "offer_1", "body": {"line_id": "line_1", "title": "SSD Samsung",
                                           "part_number": "MZ-77Q", "price": 100,
                                           "catalog_id": "cat_1", "is_selected": True}},
        ],
        "budget_lines": [
            {"row_id": "bud_1", "body": {"line_id": "line_1", "title": "OLD",
                                         "vat": 0.3, "markup": 0.15}},
        ],
        "catalogs": [{"row_id": "cat_1", "body": {"name": "OCS"}}],
    }
    list_calls: list[dict] = []
    created: list[dict] = []
    updated: list[dict] = []

    async def _rows(**kwargs):  # noqa: ANN003
        list_calls.append(kwargs)
        return rows_by_table[kwargs["table_slug"]]

    async def _create(**kwargs):  # noqa: ANN003
        created.append(kwargs)
        return {"row_id": "bud_new", "body": kwargs.get("body") or {}}

    async def _update(**kwargs):  # noqa: ANN003
        updated.append(kwargs)
        return {"row_id": kwargs.get("row_id")}

    monkeypatch.setattr(executor, "_list_rows_for_scope", _rows)
    monkeypatch.setattr(executor, "_modules", SimpleNamespace(create_data_row=_create))
    monkeypatch.setattr(executor, "_update_row_for_scope", _update)

    async def _load(*args, **kwargs):  # noqa: ANN003
        return {
            "id": "budget_sync_lines",
            "kind": "equipment.budget_sync",
            "params": {
                "lines_table": "request_lines",
                "offers_table": "found_offers",
                "budget_table": "budget_lines",
                "catalogs_table": "catalogs",
            },
        }

    monkeypatch.setattr(executor, "_load_action", _load)
    out = await executor.invoke(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        action_id="budget_sync_lines",
        principal=_principal(),
        employee=None,
        session_id="ags_123",
    )
    assert out["kind"] == "equipment.budget_sync"

    # every source read is scoped by the active chat session
    assert list_calls, "budget sync must read source tables"
    for call in list_calls:
        assert call["session_id"] == "ags_123", call
    # budget row update carries the session (stamps chats=current column)
    assert updated and updated[0]["session_id"] == "ags_123"

    # budget rows reset -> every line re-created, all stamped with the session
    rows_by_table["budget_lines"] = []
    rows_by_table["request_lines"].append(
        {"row_id": "line_2", "body": {"title": "RAM", "qty": 1}}
    )
    out = await executor.invoke(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        action_id="budget_sync_lines",
        principal=_principal(),
        employee=None,
        session_id="ags_123",
    )
    assert out["created"] == 2
    # budget rows are stamped with the chat session; trusted_sellers auto-map
    # rows (chats=all shared table) intentionally stay session-less.
    assert created and all(
        c["session_id"] == "ags_123"
        for c in created
        if c.get("table_slug") != "trusted_sellers"
    )
    seller_creates = [c for c in created if c.get("table_slug") == "trusted_sellers"]
    assert all(c["session_id"] is None for c in seller_creates)


# --------------------------------------------------------------------------
# 3. Pod (MCP) writes trigger chat-scoped auto-actions
# --------------------------------------------------------------------------


def _bridge() -> PodBridgeClaims:
    return PodBridgeClaims(
        project_id="proj_1",
        cabinet_id="cab_1",
        company_id="co_1",
        pod_id="pod_1",
        gen=1,
        scopes=("module:mod_equipment:rows",),
        jti="jti_1",
        exp=9999999999,
    )


def _pod_svc() -> PodModuleDataService:
    session = AsyncMock()
    return PodModuleDataService(session)


@pytest.mark.asyncio
async def test_pod_create_runs_budget_sync_with_stamped_session(monkeypatch) -> None:
    svc = _pod_svc()
    project = SimpleNamespace(
        id="proj_1", cabinet_id="cab_1", company_id="co_1", status="active"
    )
    inst = SimpleNamespace(id="minst_cab")

    async def _require_project_row(project_id, bridge):  # noqa: ANN001
        return project

    async def _sot_for_project(**kwargs):  # noqa: ANN003
        return inst

    monkeypatch.setattr(svc, "_require_project_row", _require_project_row)
    monkeypatch.setattr(svc._runtime, "_sot_for_project", _sot_for_project)
    monkeypatch.setattr(svc._instances, "resolve_tables_body", AsyncMock(
        return_value=[{"slug": "request_lines", "scope": {"chats": "current"}}]
    ))
    monkeypatch.setattr(svc._instances, "resolve_columns_body", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc._instances, "create_data_row", AsyncMock(
        return_value={"row_id": "row_9", "table_slug": "request_lines", "body": {"title": "SSD"}}
    ))
    monkeypatch.setattr(svc._instances, "get_data_row", AsyncMock(
        return_value={"row_id": "row_9", "table_slug": "request_lines", "body": {"title": "SSD"}}
    ))
    monkeypatch.setattr(
        "prodavan.application.modules.chat_scope_ops.ensure_session_belongs_to_project",
        AsyncMock(),
    )

    budget_calls: list[dict] = []

    async def _budget(self, **kwargs):  # noqa: ANN001, ANN003
        budget_calls.append(kwargs)

    async def _index(self, **kwargs):  # noqa: ANN001, ANN003
        _ = kwargs

    monkeypatch.setattr(ModuleActionExecutor, "maybe_auto_index_tabular", _index)
    monkeypatch.setattr(ModuleActionExecutor, "maybe_auto_budget_sync", _budget)

    out = await svc.create_data_row(
        bridge=_bridge(),
        project_id="proj_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        body={"title": "SSD"},
        session_id="ags_123",
    )
    assert out["row_id"] == "row_9"
    assert budget_calls and budget_calls[0]["session_id"] == "ags_123"
    assert budget_calls[0]["project_id"] == "proj_1"
    assert budget_calls[0]["cabinet_id"] == "cab_1"
