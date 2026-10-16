"""Unit tests — лимит шагов (max_turns) как настройка чата.

Поведение: по умолчанию БЕЗ ограничений (в send поле не уходит вовсе, работает
конфиг проекта); когда пользователь включил лимит — значение уходит в теле
каждого send, рантайм обрывает ход с `done.reason = "max_turns"`, а чат
показывает «Лимит шагов закончен, попросите агента продолжить».
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.agent.chat_projection import events_to_chat_blocks
from prodavan.domain.agent import PLATFORM_EVENT_USER_MESSAGE, AgentEventType
from prodavan.domain.agent.turn_limits import (
    DONE_REASON_MAX_TURNS,
    MAX_TURNS_CEILING,
    UNLIMITED_MAX_TURNS,
    normalize_max_turns,
    resolve_send_max_turns,
)


# --- доменные правила -----------------------------------------------------


def test_normalize_max_turns_unlimited_by_default() -> None:
    assert normalize_max_turns(None) is None
    # 0 и отрицательные — это «снять лимит», а не ошибка: в UI так выглядит
    # выключенный переключатель «Ограничить количество шагов за раз»
    assert normalize_max_turns(0) is None
    assert normalize_max_turns(-5) is None


def test_normalize_max_turns_keeps_positive_and_caps_ceiling() -> None:
    assert normalize_max_turns(1) == 1
    assert normalize_max_turns(25) == 25
    assert normalize_max_turns(MAX_TURNS_CEILING) == MAX_TURNS_CEILING
    assert normalize_max_turns(MAX_TURNS_CEILING + 500) == MAX_TURNS_CEILING


def test_resolve_send_max_turns_omits_field_when_unlimited() -> None:
    """None → в тело send ничего не кладём: работает `.prodavan/config.yaml`.

    Отправлять «бесконечность» числом нельзя: так send-оверрайд обошёл бы
    платформенную политику и конфиг проекта (send.max_turns имеет наивысший
    приоритет в turnLimitsFromConfig).
    """
    assert resolve_send_max_turns(None) is None
    assert resolve_send_max_turns(0) is None
    assert resolve_send_max_turns(25) == 25


def test_project_default_is_unlimited() -> None:
    """Дефолт проекта — «без ограничений», а не прежние жёсткие 50."""
    assert UNLIMITED_MAX_TURNS > 50
    assert MAX_TURNS_CEILING == UNLIMITED_MAX_TURNS
    assert DONE_REASON_MAX_TURNS == "max_turns"


# --- проекция транскрипта -------------------------------------------------


def test_done_max_turns_projects_system_notice() -> None:
    events = [
        {"type": PLATFORM_EVENT_USER_MESSAGE, "data": {"text": "собери смету"}},
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "Начинаю"}},
        {"type": AgentEventType.DONE, "data": {"reason": "max_turns", "turns": 25}},
    ]

    blocks = events_to_chat_blocks(events)

    notices = [b for b in blocks if b["kind"] == "system_notice"]
    assert len(notices) == 1
    assert notices[0]["reason"] == "max_turns"
    # частичный ответ модели не теряется — уведомление идёт ПОСЛЕ него
    assert blocks[-2]["kind"] == "assistant_markdown"
    assert blocks[-2]["text"] == "Начинаю"


def test_done_other_reason_projects_no_notice() -> None:
    events = [
        {"type": AgentEventType.TEXT_DELTA, "data": {"text": "готово"}},
        {"type": AgentEventType.DONE, "data": {"reason": "completed"}},
    ]

    blocks = events_to_chat_blocks(events)

    assert not [b for b in blocks if b["kind"] == "system_notice"]


def test_error_frame_still_wins_over_notice() -> None:
    """Ошибка провайдера важнее уведомления о лимите — показываем ошибку."""
    events = [
        {
            "type": AgentEventType.ERROR,
            "data": {"code": "PROVIDER_MODEL_ERROR", "message": "401", "retryable": False},
        },
        {"type": AgentEventType.DONE, "data": {"reason": "max_turns"}},
    ]

    blocks = events_to_chat_blocks(events)

    assert [b["kind"] for b in blocks] == ["error", "system_notice"]


# --- PATCH сессии ---------------------------------------------------------


def _row():
    from prodavan.infrastructure.persistence.models.agent import AgentSessionRow

    return AgentSessionRow(
        id="ags_1",
        project_id="proj_1",
        provider="xai",
        api_kind="xai_oauth",
        vendor_agent_id="ags_1",
        cwd="/workspace",
        status="active",
        title="Диалог",
    )


def _service(row):
    from prodavan.application.agent.session_service import AgentSessionService

    svc = AgentSessionService(AsyncMock())
    svc._projects = MagicMock()
    svc._projects.require_access = AsyncMock(return_value=None)
    svc.get_session = AsyncMock(return_value=row)
    return svc


@pytest.mark.asyncio
async def test_patch_session_sets_max_turns() -> None:
    row = _row()
    svc = _service(row)

    out = await svc.patch_session(
        project_id="proj_1",
        session_id="ags_1",
        principal=MagicMock(),
        employee=None,
        max_turns=25,
        update_max_turns=True,
    )

    assert row.max_turns == 25
    assert out["max_turns"] == 25


@pytest.mark.asyncio
async def test_patch_session_clears_max_turns_on_zero_and_null() -> None:
    row = _row()
    row.max_turns = 25
    svc = _service(row)

    out = await svc.patch_session(
        project_id="proj_1",
        session_id="ags_1",
        principal=MagicMock(),
        employee=None,
        max_turns=None,
        update_max_turns=True,
    )
    assert row.max_turns is None
    assert out["max_turns"] is None

    row.max_turns = 25
    out = await svc.patch_session(
        project_id="proj_1",
        session_id="ags_1",
        principal=MagicMock(),
        employee=None,
        max_turns=0,
        update_max_turns=True,
    )
    assert row.max_turns is None
    assert out["max_turns"] is None


@pytest.mark.asyncio
async def test_patch_session_without_flag_leaves_max_turns_alone() -> None:
    """Переименование чата не должно молча сбрасывать лимит."""
    row = _row()
    row.max_turns = 25
    svc = _service(row)

    out = await svc.patch_session(
        project_id="proj_1",
        session_id="ags_1",
        principal=MagicMock(),
        employee=None,
        title="Новое имя",
    )

    assert row.max_turns == 25
    assert row.title == "Новое имя"
    assert out["max_turns"] == 25


# --- тело send ------------------------------------------------------------


def _bridge_mocks():
    from prodavan.application.agent.openclaw_bridge import OpenClawBridgeBootstrap

    class _StreamResponse:
        status_code = 200

        async def aread(self) -> bytes:
            return b""

        def aiter_lines(self):
            async def _gen():
                yield 'data: {"type":"done","data":{"reason":"max_turns","turns":25}}'
                yield "data: [DONE]"

            return _gen()

    class _StreamCtx:
        def __init__(self, response) -> None:
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, *args):
            return None

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.stream = MagicMock(return_value=_StreamCtx(_StreamResponse()))
    bridge = OpenClawBridgeBootstrap(
        MagicMock(), k8s_client=MagicMock(), http_client=lambda **_: mock_http
    )
    return bridge, mock_http


@pytest.mark.asyncio
async def test_send_body_carries_max_turns_when_limited() -> None:
    from prodavan.config.settings import settings as real_settings

    bridge, mock_http = _bridge_mocks()

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch(
            "prodavan.application.agent.openclaw_bridge.ensure_recent_bind",
            new=AsyncMock(return_value=None),
        ),
        patch.object(real_settings, "pod_agent_runtime_token", "bridge-token"),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
        endpoint = MagicMock()
        endpoint.base_url = "http://pod:3921"
        endpoint.headers = {}
        events = [
            e
            async for e in bridge.iter_send_events(
                project_id="prj_1",
                session_id="ags_1",
                message="продолжай",
                endpoint=endpoint,
                max_turns=25,
            )
        ]

    assert events[-1].type == AgentEventType.DONE
    body = mock_http.stream.call_args.kwargs["json"]
    assert body["max_turns"] == 25


@pytest.mark.asyncio
async def test_send_body_omits_max_turns_when_unlimited() -> None:
    from prodavan.config.settings import settings as real_settings

    bridge, mock_http = _bridge_mocks()

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch(
            "prodavan.application.agent.openclaw_bridge.ensure_recent_bind",
            new=AsyncMock(return_value=None),
        ),
        patch.object(real_settings, "pod_agent_runtime_token", "bridge-token"),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
        endpoint = MagicMock()
        endpoint.base_url = "http://pod:3921"
        endpoint.headers = {}
        async for _ in bridge.iter_send_events(
            project_id="prj_1",
            session_id="ags_1",
            message="продолжай",
            endpoint=endpoint,
            max_turns=None,
        ):
            pass

    body = mock_http.stream.call_args.kwargs["json"]
    assert "max_turns" not in body
