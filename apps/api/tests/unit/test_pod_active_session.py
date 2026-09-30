"""Regression tests: pod (Bridge / MCP) rows resolve to the active chat.

Live dev diagnosis (2026-09-30): the agent's MCP writes landed in the shared
synthetic ``main`` bucket instead of the chat the agent was working in. The
platform MCP servers are pod-scoped processes spawned once per runtime with
``${PRODAVAN_SESSION_ID}`` expanded from the pod process env — which never
carries a per-chat session (one pod hosts many chats). The agent surface
therefore resolves the missing session itself: the latest active chat of the
project, so agent-written rows surface in the active chat's UI views.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.modules.module_action_executor import ModuleActionExecutor
from prodavan.application.pod_identity.bridge import PodBridgeClaims
from prodavan.application.tenant_infra.pod_modules import PodModuleDataService
from prodavan.domain.identity import Principal


def _bridge() -> PodBridgeClaims:
    return PodBridgeClaims(
        project_id="proj_1",
        cabinet_id="cab_1",
        company_id="co_1",
        pod_id="pod_1",
        gen=1,
        scopes=("module:mod_equipment:rows", "module:mod_equipment:actions"),
        jti="jti_1",
        exp=9999999999,
    )


def _principal() -> Principal:
    return Principal(sub="u1", roles=frozenset())


def _pod_svc() -> PodModuleDataService:
    return PodModuleDataService(AsyncMock())


def _setup_common(svc: PodModuleDataService, monkeypatch) -> SimpleNamespace:
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
    monkeypatch.setattr(
        "prodavan.application.modules.chat_scope_ops.ensure_session_belongs_to_project",
        AsyncMock(),
    )
    return inst


@pytest.mark.asyncio
async def test_pod_create_without_session_resolves_active_chat(monkeypatch) -> None:
    """MCP write with no session header -> stamped with the project's active chat."""
    svc = _pod_svc()
    _setup_common(svc, monkeypatch)
    svc._instances.create_data_row = AsyncMock(
        return_value={"row_id": "row_1", "table_slug": "request_lines", "body": {}}
    )
    svc._instances.get_data_row = AsyncMock(
        return_value={"row_id": "row_1", "table_slug": "request_lines", "body": {}}
    )

    async def _resolve(project_id):
        assert project_id == "proj_1"
        return "ags_active"

    monkeypatch.setattr(svc, "_resolve_active_agent_session", _resolve)

    await svc.create_data_row(
        bridge=_bridge(),
        project_id="proj_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        body={"title": "SSD"},
        session_id=None,
    )
    kwargs = svc._instances.create_data_row.await_args.kwargs
    assert kwargs["session_id"] == "ags_active"
    assert kwargs["body"]["session_id"] == "ags_active"


@pytest.mark.asyncio
async def test_pod_create_without_session_falls_back_to_main(monkeypatch) -> None:
    """No active chat in the project -> shared 'main' bucket (unchanged default)."""
    svc = _pod_svc()
    _setup_common(svc, monkeypatch)
    svc._instances.create_data_row = AsyncMock(
        return_value={"row_id": "row_2", "table_slug": "request_lines", "body": {}}
    )
    svc._instances.get_data_row = AsyncMock(
        return_value={"row_id": "row_2", "table_slug": "request_lines", "body": {}}
    )
    monkeypatch.setattr(svc, "_resolve_active_agent_session", AsyncMock(return_value=None))

    await svc.create_data_row(
        bridge=_bridge(),
        project_id="proj_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        body={"title": "SSD"},
        session_id=None,
    )
    kwargs = svc._instances.create_data_row.await_args.kwargs
    assert kwargs["session_id"] == "main"


@pytest.mark.asyncio
async def test_pod_explicit_session_wins_over_resolution(monkeypatch) -> None:
    """An explicit session (tool argument / env header) must never be overridden."""
    svc = _pod_svc()
    _setup_common(svc, monkeypatch)
    svc._instances.create_data_row = AsyncMock(
        return_value={"row_id": "row_3", "table_slug": "request_lines", "body": {}}
    )
    svc._instances.get_data_row = AsyncMock(
        return_value={"row_id": "row_3", "table_slug": "request_lines", "body": {}}
    )
    resolve_calls: list[str] = []
    orig = svc._resolve_active_agent_session

    async def _resolve(project_id):
        resolve_calls.append(project_id)
        return "ags_active"

    monkeypatch.setattr(svc, "_resolve_active_agent_session", _resolve)
    _ = orig

    await svc.create_data_row(
        bridge=_bridge(),
        project_id="proj_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        body={"title": "SSD"},
        session_id="ags_explicit",
    )
    assert resolve_calls == []
    kwargs = svc._instances.create_data_row.await_args.kwargs
    assert kwargs["session_id"] == "ags_explicit"


@pytest.mark.asyncio
async def test_pod_list_without_session_filters_by_active_chat(monkeypatch) -> None:
    """MCP reads (request_lines_list) must see the active chat's rows, not 'main'."""
    svc = _pod_svc()
    _setup_common(svc, monkeypatch)
    svc._instances.list_data_rows = AsyncMock(return_value=[])

    async def _resolve(project_id):
        return "ags_active"

    monkeypatch.setattr(svc, "_resolve_active_agent_session", _resolve)

    await svc.list_data_rows(
        bridge=_bridge(),
        project_id="proj_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        session_id=None,
    )
    kwargs = svc._instances.list_data_rows.await_args.kwargs
    assert kwargs["session_id"] == "ags_active"


@pytest.mark.asyncio
async def test_pod_invoke_action_resolves_session(monkeypatch) -> None:
    """Agent-invoked module actions (budget sync) scope to the active chat."""
    svc = _pod_svc()
    project = SimpleNamespace(
        id="proj_1", cabinet_id="cab_1", company_id="co_1", status="active"
    )

    async def _require_project_row(project_id, bridge):  # noqa: ANN001
        return project

    monkeypatch.setattr(svc, "_require_project_row", _require_project_row)

    async def _resolve(project_id):
        return "ags_active"

    monkeypatch.setattr(svc, "_resolve_active_agent_session", _resolve)

    invoke_calls: list[dict] = []

    async def _invoke(self, **kwargs):  # noqa: ANN001, ANN003
        invoke_calls.append(kwargs)
        return {"kind": "equipment.budget_sync", "created": 0, "updated": 0}

    monkeypatch.setattr(ModuleActionExecutor, "invoke", _invoke)

    await svc.invoke_action(
        bridge=_bridge(),
        project_id="proj_1",
        module_id="mod_equipment",
        action_id="budget_sync_lines",
        row_id=None,
        session_id=None,
    )
    assert invoke_calls and invoke_calls[0]["session_id"] == "ags_active"
