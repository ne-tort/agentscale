"""OpenClaw bridge bootstrap unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.agent.openclaw_bridge import (
    PRODAVAN_EVENTS_OWNER_API,
    PRODAVAN_EVENTS_OWNER_HEADER,
    BridgeSessionBootstrap,
    OpenClawBridgeBootstrap,
    api_kind_to_bridge_adapter,
    bridge_envelope_is_stub,
    bridge_envelope_to_agent_event,
    bridge_stub_error_event,
)
from prodavan.domain.agent import AgentEventType
from prodavan.infrastructure.k8s.sandbox.client import PodSnapshot


def _running_runtime_view(*, runtime_ref: str = "pod-wk-demo") -> dict:
    return {
        "observed_state": "running",
        "k8s_pod_name": runtime_ref,
        "runtime_ref": runtime_ref,
    }


def _mock_running_pod(k8s: MagicMock, *, pod_ip: str = "10.42.0.99", name: str = "pod-wk-demo") -> None:
    k8s.available.return_value = True
    k8s.get_pod = AsyncMock(
        return_value=PodSnapshot(
            name=name,
            uid="u1",
            phase="Running",
            restarts=0,
            ready=True,
            labels={},
            pod_ip=pod_ip,
        ),
    )


def test_api_kind_to_bridge_adapter() -> None:
    assert api_kind_to_bridge_adapter("cursor_sdk") == "cursor_sdk"
    assert api_kind_to_bridge_adapter("openrouter") == "platform_openclaw"
    assert api_kind_to_bridge_adapter("anthropic_api") == "platform_openclaw"


def test_bridge_envelope_to_agent_event_skips_ping() -> None:
    assert bridge_envelope_to_agent_event({"type": "ping", "data": {}}) is None
    ev = bridge_envelope_to_agent_event({"type": "system_init", "data": {"adapter": "cursor_sdk"}})
    assert ev is not None
    assert ev.type == "system_init"
    ev = bridge_envelope_to_agent_event({"type": "text_delta", "data": {"text": "hi"}})
    assert ev is not None
    assert ev.type == AgentEventType.TEXT_DELTA
    sub = bridge_envelope_to_agent_event({"type": "subagent_start", "data": {"agent_id": "a1"}})
    assert sub is not None
    assert sub.type == "subagent_start"


def test_bridge_envelope_is_stub() -> None:
    assert bridge_envelope_is_stub({"type": "system_init", "data": {"stub": True}})
    assert bridge_envelope_is_stub(
        {"type": "text_delta", "data": {"text": "[cursor-sdk stub] hello"}}
    )
    assert not bridge_envelope_is_stub({"type": "text_delta", "data": {"text": "hello"}})


def test_bridge_stub_error_event() -> None:
    ev = bridge_stub_error_event()
    assert ev.type == AgentEventType.ERROR
    assert ev.data.get("code") == "AGENT_STUB_RESPONSE"


@pytest.mark.asyncio
async def test_register_session_skips_when_disabled() -> None:
    bootstrap = OpenClawBridgeBootstrap(MagicMock())
    with patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings:
        mock_settings.pod_agent_runtime_enabled = False
        ok = await bootstrap.register_session(
            project_id="prj_1",
            payload=BridgeSessionBootstrap(
                session_id="ags_1",
                prodavan_session_id="ags_1",
                adapter_kind="platform_openclaw",
            ),
        )
    assert ok is False


@pytest.mark.asyncio
async def test_register_session_posts_to_bridge() -> None:
    session = MagicMock()
    k8s = MagicMock()
    _mock_running_pod(k8s)

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.text = ""

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.post = AsyncMock(return_value=mock_response)

    bootstrap = OpenClawBridgeBootstrap(session, k8s_client=k8s, http_client=lambda **_: mock_http)

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.query.PodQuery.runtime_view",
            new=AsyncMock(return_value=_running_runtime_view()),
        ),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_bootstrap_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
        ok = await bootstrap.register_session(
            project_id="prj_1",
            payload=BridgeSessionBootstrap(
                session_id="ags_abc",
                prodavan_session_id="ags_abc",
                adapter_kind="platform_openclaw",
                model="gpt-4o-mini",
            ),
        )

    assert ok is True
    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.await_args
    assert call_args.args[0] == "http://10.42.0.99:3921/v1/sessions"
    assert call_args.kwargs["json"]["session_id"] == "ags_abc"
    assert call_args.kwargs["headers"][PRODAVAN_EVENTS_OWNER_HEADER] == PRODAVAN_EVENTS_OWNER_API


@pytest.mark.asyncio
async def test_iter_send_events_parses_sse() -> None:
    session = MagicMock()
    k8s = MagicMock()
    _mock_running_pod(k8s)

    sse_lines = [
        'data: {"type":"text_delta","data":{"text":"hi"}}',
        "data: [DONE]",
    ]

    class _StreamResponse:
        status_code = 200

        async def aread(self) -> bytes:
            return b""

        def aiter_lines(self):
            async def _gen():
                for line in sse_lines:
                    yield line

            return _gen()

    class _StreamCtx:
        def __init__(self, response: _StreamResponse) -> None:
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, *args):
            return None

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.stream = MagicMock(return_value=_StreamCtx(_StreamResponse()))

    bootstrap = OpenClawBridgeBootstrap(session, k8s_client=k8s, http_client=lambda **_: mock_http)

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.query.PodQuery.runtime_view",
            new=AsyncMock(return_value=_running_runtime_view()),
        ),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
        mock_settings.pod_agent_runtime_token = "bridge-token"
        events = [
            event
            async for event in bootstrap.iter_send_events(
                project_id="prj_1",
                session_id="ags_abc",
                message="hello",
            )
        ]

    assert len(events) == 1
    assert events[0].type == AgentEventType.TEXT_DELTA
    stream_call = mock_http.stream.call_args
    assert stream_call.kwargs["headers"][PRODAVAN_EVENTS_OWNER_HEADER] == PRODAVAN_EVENTS_OWNER_API
    assert stream_call.kwargs["headers"]["Authorization"] == "Bearer bridge-token"
    assert "api_key" not in stream_call.kwargs["json"]


@pytest.mark.asyncio
async def test_iter_send_events_empty_stream_yields_error() -> None:
    session = MagicMock()
    k8s = MagicMock()
    _mock_running_pod(k8s)

    class _StreamResponse:
        status_code = 200

        async def aread(self) -> bytes:
            return b""

        def aiter_lines(self):
            async def _gen():
                if False:
                    yield ""

            return _gen()

    class _StreamCtx:
        async def __aenter__(self):
            return _StreamResponse()

        async def __aexit__(self, *args):
            return None

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.stream = MagicMock(return_value=_StreamCtx())

    bootstrap = OpenClawBridgeBootstrap(session, k8s_client=k8s, http_client=lambda **_: mock_http)

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.query.PodQuery.runtime_view",
            new=AsyncMock(return_value=_running_runtime_view()),
        ),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
        mock_settings.pod_agent_runtime_token = ""
        events = [
            event
            async for event in bootstrap.iter_send_events(
                project_id="prj_1",
                session_id="ags_abc",
                message="hello",
            )
        ]

    assert len(events) == 1
    assert events[0].type == AgentEventType.ERROR
    assert events[0].data.get("code") == "BRIDGE_EMPTY_STREAM"
