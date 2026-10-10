"""Unit tests — под сам восстанавливает проектный bind после рестарта контейнера.

Проекционный env (`PRODAVAN_AUTH_TOKEN` / `PRODAVAN_PROJECT_ID` / `PRODAVAN_SESSION_ID`)
применяется ТОЛЬКО через `POST /v1/project/bind` и живёт в памяти bridge
(`applyProjectBind` мутирует `process.env`, пересоздаёт builtin MCP, перечитывает
config.yaml и переподнимает MCP-серверы). После рестарта контейнера внутри того
же пода всё это исчезает, а `sandbox_name` не меняется — значит Redis-метка
`prodavan:bind:<project>:<sandbox>` (TTL 12 ч) продолжает подавлять bind, и
модульные MCP-инструменты проекта остаются сломаны до истечения метки.

На dev это выглядело так: агент отвечает, встроенные инструменты работают, а
любой `mcp.prodavan-equipment.*` возвращает «PRODAVAN_API_BASE_URL,
PRODAVAN_AUTH_TOKEN, and PRODAVAN_PROJECT_ID are required».

Лечится двумя путями, оба без похода в k8s и без bind'ов посреди хода:
1. MCP-пакеты бросают ошибку со стабильным маркером `PRODAVAN_MCP_ENV_MISSING`;
   API, увидев его в tool_result, сбрасывает метку → следующий send биндится.
2. Recoverable-ретрай (сессии в поде нет — bridge заведомо перезапустился)
   регистрирует сессию с `force_bind=True`, минуя метку и троттлинг.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import prodavan.application.agent.openclaw_bridge as bridge_mod
from prodavan.domain.agent import AgentEventType
from prodavan.domain.agent.types import AgentEvent

_MARKER = "PRODAVAN_MCP_ENV_MISSING"
_LEGACY = (
    "PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, and PRODAVAN_PROJECT_ID are required"
)


def _tool_result(text: str, *, is_error: bool = True) -> AgentEvent:
    return AgentEvent.now(
        AgentEventType.TOOL_RESULT,
        {
            "id": "call-1",
            "name": "mcp.prodavan-equipment.ready_builds_catalog",
            "output": {"content": [{"text": text, "type": "text"}]},
            "is_error": is_error,
        },
    )


# --- детектор маркера -----------------------------------------------------


def test_detects_new_marker_and_legacy_text() -> None:
    assert bridge_mod.tool_result_lost_pod_bind(_tool_result(f"{_MARKER}: {_LEGACY}"))
    # легас-текст: поды со старым MCP-пакетом ещё долго будут отвечать им
    assert bridge_mod.tool_result_lost_pod_bind(_tool_result(_LEGACY))


def test_ignores_successful_and_unrelated_results() -> None:
    assert not bridge_mod.tool_result_lost_pod_bind(
        _tool_result('{"items": []}', is_error=False)
    )
    assert not bridge_mod.tool_result_lost_pod_bind(
        _tool_result("HTTP 422: row_id обязателен")
    )
    assert not bridge_mod.tool_result_lost_pod_bind(
        AgentEvent.now(AgentEventType.TEXT_DELTA, {"text": _LEGACY})
    )


def test_detects_plain_string_output() -> None:
    """tool_result может прийти и строкой, не только MCP-конвертом."""
    event = AgentEvent.now(
        AgentEventType.TOOL_RESULT, {"output": _LEGACY, "is_error": True}
    )
    assert bridge_mod.tool_result_lost_pod_bind(event)


# --- сброс метки ----------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidate_bind_mark_clears_redis_and_throttle() -> None:
    key = "prodavan:bind:prj_x:sbx_1"
    bridge_mod._last_bind_attempt[key] = 12345.0
    endpoint = MagicMock()
    endpoint.headers = {"X-Sandbox-Id": "sbx_1"}

    with patch(
        "prodavan.core.infra.cache.cache_delete", new=AsyncMock(return_value=True)
    ) as delete:
        await bridge_mod.invalidate_bind_mark("prj_x", endpoint)

    delete.assert_awaited_once_with(key)
    # in-memory троттлинг (деградация без Redis) тоже сброшен, иначе bind
    # остался бы подавленным ещё на 300 с
    assert key not in bridge_mod._last_bind_attempt


@pytest.mark.asyncio
async def test_force_bind_bypasses_mark() -> None:
    with (
        patch("prodavan.core.infra.cache.cache_get", new=AsyncMock(return_value="1")),
        patch("prodavan.core.infra.cache.cache_set", new=AsyncMock(return_value=True)),
        patch(
            "prodavan.application.agent.openclaw_bridge.bind_project_runtime",
            new=AsyncMock(return_value=True),
        ) as bind,
    ):
        await bridge_mod.ensure_recent_bind(MagicMock(), "prj_force", force=True)

    bind.assert_awaited_once()


@pytest.mark.asyncio
async def test_marked_project_still_skips_bind_without_force() -> None:
    with (
        patch("prodavan.core.infra.cache.cache_get", new=AsyncMock(return_value="1")),
        patch(
            "prodavan.application.agent.openclaw_bridge.bind_project_runtime",
            new=AsyncMock(return_value=True),
        ) as bind,
    ):
        await bridge_mod.ensure_recent_bind(MagicMock(), "prj_marked2")

    bind.assert_not_awaited()


# --- send сбрасывает метку по tool_result ---------------------------------


@pytest.mark.asyncio
async def test_send_invalidates_mark_when_pod_lost_bind() -> None:
    bridge = bridge_mod.OpenClawBridgeBootstrap(MagicMock(), k8s_client=MagicMock())

    async def _fake_stream(**_kwargs):
        yield _tool_result(f"{_MARKER}: {_LEGACY}")
        yield AgentEvent.now(AgentEventType.DONE, {"reason": "completed"})

    endpoint = MagicMock()
    endpoint.base_url = "http://router"
    endpoint.headers = {"X-Sandbox-Id": "sbx_9"}

    with (
        patch.object(bridge, "_stream_send", side_effect=_fake_stream),
        patch.object(bridge, "_patch_adapter_state", new=AsyncMock(return_value=None)),
        patch(
            "prodavan.application.agent.openclaw_bridge.invalidate_bind_mark",
            new=AsyncMock(return_value=None),
        ) as invalidate,
        patch.object(bridge_mod.settings, "pod_agent_runtime_enabled", True),
    ):
        events = [
            e
            async for e in bridge.iter_send_events(
                project_id="prj_1",
                session_id="ags_1",
                message="hi",
                endpoint=endpoint,
            )
        ]

    assert [e.type for e in events] == [AgentEventType.TOOL_RESULT, AgentEventType.DONE]
    invalidate.assert_awaited_once()
    assert invalidate.await_args.args[0] == "prj_1"


@pytest.mark.asyncio
async def test_send_keeps_mark_on_healthy_tool_result() -> None:
    bridge = bridge_mod.OpenClawBridgeBootstrap(MagicMock(), k8s_client=MagicMock())

    async def _fake_stream(**_kwargs):
        yield _tool_result('{"items": []}', is_error=False)
        yield AgentEvent.now(AgentEventType.DONE, {"reason": "completed"})

    endpoint = MagicMock()
    endpoint.base_url = "http://router"
    endpoint.headers = {"X-Sandbox-Id": "sbx_9"}

    with (
        patch.object(bridge, "_stream_send", side_effect=_fake_stream),
        patch.object(bridge, "_patch_adapter_state", new=AsyncMock(return_value=None)),
        patch(
            "prodavan.application.agent.openclaw_bridge.invalidate_bind_mark",
            new=AsyncMock(return_value=None),
        ) as invalidate,
        patch.object(bridge_mod.settings, "pod_agent_runtime_enabled", True),
    ):
        async for _ in bridge.iter_send_events(
            project_id="prj_1",
            session_id="ags_1",
            message="hi",
            endpoint=endpoint,
        ):
            pass

    invalidate.assert_not_awaited()


# --- контракт с MCP-пакетами ----------------------------------------------


def test_both_mcp_packages_emit_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """API матчит текст ошибки пакетов — обе стороны должны совпадать.

    Пакеты уезжают в поды отдельным zip'ом и обновляются только перевыпуском
    claim, поэтому расход текста молча сломал бы восстановление.
    """
    from prodavan.application.mcp.prodavan_equipment_mcp import server as equipment
    from prodavan.application.mcp.prodavan_modules_mcp import server as modules

    for name in ("PRODAVAN_API_BASE_URL", "PRODAVAN_AUTH_TOKEN", "PRODAVAN_PROJECT_ID",
                 "BRIDGE_AUTH_TOKEN", "PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)

    for mod in (equipment, modules):
        with pytest.raises(RuntimeError) as exc:
            mod._http("GET", "/anything")
        assert _MARKER in str(exc.value), mod.__name__
        assert any(
            marker in str(exc.value) for marker in bridge_mod._MCP_ENV_MISSING_MARKERS
        )
