"""Unit tests — pod-маршрут invoke обязан проносить `params` действия.

Регрессия WAVE11: `PodModuleActionBody` содержал только `row_id`, и Pydantic
молча отбрасывал прочие поля тела. MCP-сервер шлёт
`{"row_id": ..., "params": {"line_id": ...}}`, поэтому `ready_build_attach` —
единственный способ скопировать каталожную сборку в чат — из агента не работал
ВОВСЕ и всегда отвечал

    422 VALIDATION_ERROR «row_id (готовая сборка) и params.line_id (позиция) обязательны»

Найдено сквозным прогоном через ИИ-агента на dev: инструмент вызывался с
правильными аргументами (`mcp.prodavan-equipment.ready_build_attach`,
`{ready_build_id, line_id}`), а действие до параметров не доезжало.
Cabinet-маршрут при этом работал — он передаёт `extra_params=body.params`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.api.v1.pod_modules import PodModuleActionBody, invoke_pod_module_action
from prodavan.application.tenant_infra.pod_modules import PodModuleDataService

_EXECUTOR = "prodavan.application.modules.module_action_executor.ModuleActionExecutor"


def test_action_body_keeps_params() -> None:
    """Поле params должно доходить до модели, а не отбрасываться молча."""
    body = PodModuleActionBody.model_validate(
        {"row_id": "row_build", "params": {"line_id": "row_line", "note": "n"}}
    )
    assert body.row_id == "row_build"
    assert body.params == {"line_id": "row_line", "note": "n"}


def test_action_body_params_optional() -> None:
    assert PodModuleActionBody.model_validate({"row_id": "row_x"}).params is None
    assert PodModuleActionBody.model_validate({}).params is None


def _service() -> tuple[PodModuleDataService, MagicMock]:
    svc = PodModuleDataService(AsyncMock())
    svc._require_project_row = AsyncMock(
        return_value=SimpleNamespace(id="proj_1", cabinet_id="cab_1", company_id="co_1")
    )
    svc._require_module_actions = MagicMock()
    svc._scoped_session_id = AsyncMock(return_value="ags_1")
    svc._bindings = MagicMock()
    svc._bindings.get_project_binding = AsyncMock(return_value=SimpleNamespace(id="bind_1"))
    svc._pod_principal = MagicMock(return_value=MagicMock())
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value={"action_id": "ready_build_attach", "ok": True})
    return svc, executor


@pytest.mark.asyncio
async def test_invoke_action_forwards_extra_params() -> None:
    svc, executor = _service()

    with patch(_EXECUTOR, return_value=executor):
        out = await svc.invoke_action(
            bridge=MagicMock(),
            project_id="proj_1",
            module_id="mod_equipment",
            action_id="ready_build_attach",
            row_id="row_build",
            session_id="ags_1",
            extra_params={"line_id": "row_line"},
        )

    kwargs = executor.invoke.await_args.kwargs
    assert kwargs["row_id"] == "row_build"
    assert kwargs["extra_params"] == {"line_id": "row_line"}
    assert kwargs["project_id"] == "proj_1"
    assert kwargs["session_id"] == "ags_1"
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_invoke_action_without_params_still_works() -> None:
    """Действиям только с row_id (например pipeline sync) fix ничего не ломает."""
    svc, executor = _service()

    with patch(_EXECUTOR, return_value=executor):
        await svc.invoke_action(
            bridge=MagicMock(),
            project_id="proj_1",
            module_id="mod_equipment",
            action_id="equipment_pipeline_sync",
            row_id="row_x",
        )

    assert executor.invoke.await_args.kwargs["extra_params"] is None


@pytest.mark.asyncio
async def test_route_passes_body_params_to_service() -> None:
    svc = MagicMock()
    svc.invoke_action = AsyncMock(return_value={"ok": True})
    body = PodModuleActionBody.model_validate(
        {"row_id": "row_build", "params": {"line_id": "row_line"}}
    )

    with patch("prodavan.api.v1.pod_modules.PodModuleDataService", return_value=svc):
        out = await invoke_pod_module_action(
            project_id="proj_1",
            module_id="mod_equipment",
            action_id="ready_build_attach",
            bridge=MagicMock(),
            session=AsyncMock(),
            body=body,
            x_prodavan_session_id="ags_1",
        )

    kwargs = svc.invoke_action.await_args.kwargs
    assert kwargs["extra_params"] == {"line_id": "row_line"}
    assert kwargs["row_id"] == "row_build"
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_route_tolerates_absent_body() -> None:
    svc = MagicMock()
    svc.invoke_action = AsyncMock(return_value={"ok": True})

    with patch("prodavan.api.v1.pod_modules.PodModuleDataService", return_value=svc):
        await invoke_pod_module_action(
            project_id="proj_1",
            module_id="mod_equipment",
            action_id="equipment_pipeline_sync",
            bridge=MagicMock(),
            session=AsyncMock(),
            body=None,
            x_prodavan_session_id=None,
        )

    kwargs = svc.invoke_action.await_args.kwargs
    assert kwargs["row_id"] is None
    assert kwargs["extra_params"] is None
