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
    """WAVE7: сессия чата прокидывается в пайплайн (скоп reads + stamp writes).

    Сама семантика скопа (list по session_id, stamp chats=current при записи)
    живёт в сервисах строк и покрыта их тестами; здесь — что executor не теряет
    session_id при вызове пайплайна.
    """
    executor = _executor()
    calls: list[dict] = []

    async def _pipeline(**kwargs):
        calls.append(kwargs)
        return {"kind": "equipment.pipeline", "created": 0, "updated": 0}

    monkeypatch.setattr(executor, "_equipment_pipeline", _pipeline)

    async def _load(*args, **kwargs):  # noqa: ANN003
        return {"id": "budget_sync_lines", "kind": "equipment.budget_sync", "params": {}}

    monkeypatch.setattr(executor, "_load_action", _load)
    await executor.invoke(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        action_id="budget_sync_lines",
        principal=_principal(),
        employee=None,
        session_id="ags_123",
    )
    assert len(calls) == 1
    assert calls[0]["session_id"] == "ags_123"
    assert calls[0]["project_id"] is None


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
