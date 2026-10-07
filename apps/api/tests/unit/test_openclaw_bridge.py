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
from prodavan.application.agent.runtime_transport import RuntimeEndpoint
from prodavan.config.settings import settings as _real_settings
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


def test_bridge_envelope_merges_parent_tool_use_id_into_data() -> None:
    ev = bridge_envelope_to_agent_event(
        {
            "type": "subagent_event",
            "parent_tool_use_id": "sub-1",
            "data": {"child_event": {"type": "text_delta", "data": {"text": "hi"}}},
        }
    )
    assert ev is not None
    assert ev.data["parent_tool_use_id"] == "sub-1"
    assert ev.data["child_event"]["type"] == "text_delta"


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
        patch.object(_real_settings, "pod_agent_runtime_token", "bridge-token"),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
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
async def test_iter_send_events_forwards_images_in_body() -> None:
    """Vision images ride with the send body as {mime, data_base64} entries."""
    session = MagicMock()
    k8s = MagicMock()
    _mock_running_pod(k8s)

    class _StreamResponse:
        status_code = 200

        async def aread(self) -> bytes:
            return b""

        def aiter_lines(self):
            async def _gen():
                yield 'data: {"type":"done","data":{"reason":"completed"}}'
                yield "data: [DONE]"

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
        patch.object(_real_settings, "pod_agent_runtime_token", "bridge-token"),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
        events = [
            event
            async for event in bootstrap.iter_send_events(
                project_id="prj_1",
                session_id="ags_abc",
                message="что на скриншоте?",
                images=[{"mime": "image/png", "data_base64": "aGVsbG8="}],
            )
        ]

    assert events[-1].type == AgentEventType.DONE
    body = mock_http.stream.call_args.kwargs["json"]
    assert body["images"] == [{"mime": "image/png", "data_base64": "aGVsbG8="}]


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


# ------------------------------------------- 409-hydrating (B2) + ensure_recent_bind (H2/H4)


def test_hydrating_code_is_recoverable() -> None:
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    assert "BRIDGE_HYDRATING" in bridge_mod._RECOVERABLE_SEND_ERROR_CODES


@pytest.mark.asyncio
async def test_stream_send_hydrating_409_yields_retryable_event() -> None:
    session = MagicMock()
    k8s = MagicMock()
    _mock_running_pod(k8s)

    class _StreamResponse:
        status_code = 409

        async def aread(self) -> bytes:
            return b'{"error":"hydrating","retryable":true}'

        def aiter_lines(self):
            async def _gen():
                return
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
        patch(
            "prodavan.application.agent.openclaw_bridge.ensure_recent_bind",
            new=AsyncMock(),
        ),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
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
    assert events[0].data.get("code") == "BRIDGE_HYDRATING"
    assert events[0].data.get("retryable") is True


@pytest.mark.asyncio
async def test_register_session_retries_once_on_hydrating_409() -> None:
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    session = MagicMock()
    k8s = MagicMock()
    _mock_running_pod(k8s)

    resp_409 = MagicMock()
    resp_409.status_code = 409
    resp_409.text = '{"error":"hydrating","retryable":true}'
    resp_201 = MagicMock()
    resp_201.status_code = 201
    resp_201.text = ""

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.post = AsyncMock(side_effect=[resp_409, resp_201])

    bootstrap = OpenClawBridgeBootstrap(session, k8s_client=k8s, http_client=lambda **_: mock_http)

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.query.PodQuery.runtime_view",
            new=AsyncMock(return_value=_running_runtime_view()),
        ),
        patch.object(bridge_mod, "_HYDRATING_RETRY_DELAY_SEC", 0.01),
        patch(
            "prodavan.application.agent.openclaw_bridge.ensure_recent_bind",
            new=AsyncMock(),
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
            ),
        )

    assert ok is True
    assert mock_http.post.await_count == 2


@pytest.mark.asyncio
async def test_register_session_hydrating_409_twice_returns_false() -> None:
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    session = MagicMock()
    k8s = MagicMock()
    _mock_running_pod(k8s)

    resp_409 = MagicMock()
    resp_409.status_code = 409
    resp_409.text = '{"error":"hydrating","retryable":true}'

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.post = AsyncMock(side_effect=[resp_409, resp_409])

    bootstrap = OpenClawBridgeBootstrap(session, k8s_client=k8s, http_client=lambda **_: mock_http)

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.query.PodQuery.runtime_view",
            new=AsyncMock(return_value=_running_runtime_view()),
        ),
        patch.object(bridge_mod, "_HYDRATING_RETRY_DELAY_SEC", 0.01),
        patch(
            "prodavan.application.agent.openclaw_bridge.ensure_recent_bind",
            new=AsyncMock(),
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
            ),
        )

    assert ok is False
    assert mock_http.post.await_count == 2


@pytest.mark.asyncio
async def test_ensure_recent_bind_skips_when_marked() -> None:
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    session = MagicMock()
    with (
        patch("prodavan.core.infra.cache.cache_get", new=AsyncMock(return_value="1")),
        patch("prodavan.core.infra.cache.cache_set", new=AsyncMock(return_value=True)),
        patch(
            "prodavan.application.agent.openclaw_bridge.bind_project_runtime",
            new=AsyncMock(return_value=True),
        ) as bind,
    ):
        await bridge_mod.ensure_recent_bind(session, "prj_marked")
    bind.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_recent_bind_binds_when_unmarked() -> None:
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    session = MagicMock()
    bridge_mod._last_bind_attempt.pop("prodavan:bind:prj_unmarked", None)
    try:
        with (
            patch("prodavan.core.infra.cache.cache_get", new=AsyncMock(return_value=None)),
            patch(
                "prodavan.core.infra.cache.cache_set", new=AsyncMock(return_value=True)
            ) as cache_set,
            patch(
                "prodavan.application.agent.openclaw_bridge.bind_project_runtime",
                new=AsyncMock(return_value=True),
            ) as bind,
        ):
            await bridge_mod.ensure_recent_bind(session, "prj_unmarked")
        bind.assert_awaited_once()
        cache_set.assert_awaited_once()
        assert cache_set.await_args.args[0] == "prodavan:bind:prj_unmarked"
        assert cache_set.await_args.kwargs["ttl_sec"] == 12 * 60 * 60
    finally:
        bridge_mod._last_bind_attempt.pop("prodavan:bind:prj_unmarked", None)


@pytest.mark.asyncio
async def test_ensure_recent_bind_throttles_without_redis() -> None:
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    session = MagicMock()
    bridge_mod._last_bind_attempt.pop("prodavan:bind:prj_no_redis", None)
    try:
        with (
            patch("prodavan.core.infra.cache.cache_get", new=AsyncMock(return_value=None)),
            patch("prodavan.core.infra.cache.cache_set", new=AsyncMock(return_value=False)),
            patch(
                "prodavan.application.agent.openclaw_bridge.bind_project_runtime",
                new=AsyncMock(return_value=True),
            ) as bind,
        ):
            await bridge_mod.ensure_recent_bind(session, "prj_no_redis")
            await bridge_mod.ensure_recent_bind(session, "prj_no_redis")
        assert bind.await_count == 1
    finally:
        bridge_mod._last_bind_attempt.pop("prodavan:bind:prj_no_redis", None)


# --------------------------------------------------- B3: bind mark on success only + sandbox-keyed


@pytest.mark.asyncio
async def test_ensure_recent_bind_does_not_mark_failed_bind() -> None:
    """B3: bind_project_runtime never raises — a False result must NOT write
    the 12h mark, or a failed bind suppresses retries for half a JWT TTL."""
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    session = MagicMock()
    try:
        with (
            patch("prodavan.core.infra.cache.cache_get", new=AsyncMock(return_value=None)),
            patch(
                "prodavan.core.infra.cache.cache_set", new=AsyncMock(return_value=True)
            ) as cache_set,
            patch(
                "prodavan.application.agent.openclaw_bridge.bind_project_runtime",
                new=AsyncMock(return_value=False),
            ),
        ):
            await bridge_mod.ensure_recent_bind(session, "prj_bind_fail")
        cache_set.assert_not_awaited()
    finally:
        bridge_mod._last_bind_attempt.pop("prodavan:bind:prj_bind_fail", None)


@pytest.mark.asyncio
async def test_ensure_recent_bind_keys_mark_by_sandbox_identity() -> None:
    """B3: after a re-adoption (new sandbox name) the old mark must not
    suppress a re-bind — the fresh runtime has no project token yet."""
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    session = MagicMock()
    endpoint_old = RuntimeEndpoint(
        base_url="http://router:8080",
        headers={"X-Sandbox-Id": "sbx-old"},
    )
    endpoint_new = RuntimeEndpoint(
        base_url="http://router:8080",
        headers={"X-Sandbox-Id": "sbx-new"},
    )

    def _fake_get(key: str) -> str | None:
        # Only the OLD sandbox bind is marked.
        return "1" if key == "prodavan:bind:prj_sbx:sbx-old" else None

    try:
        with (
            patch("prodavan.core.infra.cache.cache_get", new=AsyncMock(side_effect=_fake_get)),
            patch(
                "prodavan.core.infra.cache.cache_set", new=AsyncMock(return_value=True)
            ) as cache_set,
            patch(
                "prodavan.application.agent.openclaw_bridge.bind_project_runtime",
                new=AsyncMock(return_value=True),
            ) as bind,
        ):
            await bridge_mod.ensure_recent_bind(session, "prj_sbx", endpoint=endpoint_old)
            bind.assert_not_awaited()  # mark for the CURRENT sandbox still valid
            await bridge_mod.ensure_recent_bind(session, "prj_sbx", endpoint=endpoint_new)
            bind.assert_awaited_once()  # new sandbox → fresh bind
            assert cache_set.await_args.args[0] == "prodavan:bind:prj_sbx:sbx-new"
    finally:
        bridge_mod._last_bind_attempt.pop("prodavan:bind:prj_sbx:sbx-old", None)
        bridge_mod._last_bind_attempt.pop("prodavan:bind:prj_sbx:sbx-new", None)


def test_bind_attempt_throttle_is_bounded() -> None:
    """B8a: the Redis-down fallback throttle map is capped (LRU eviction)."""
    import prodavan.application.agent.openclaw_bridge as bridge_mod

    saved = bridge_mod._last_bind_attempt
    try:
        bridge_mod._last_bind_attempt = bridge_mod.OrderedDict()
        cap = bridge_mod._BIND_ATTEMPT_CACHE_MAX
        for i in range(cap + 50):
            # spaced beyond the throttle interval → every call records
            assert not bridge_mod._bind_attempt_throttled(f"k{i}", now=10_000.0 + i * 1000.0)
        assert len(bridge_mod._last_bind_attempt) == cap
        # the freshest key is still throttled
        assert bridge_mod._bind_attempt_throttled(
            f"k{cap + 49}", now=10_000.0 + (cap + 49) * 1000.0 + 1.0
        )
    finally:
        bridge_mod._last_bind_attempt = saved


# --------------------------------------------------- B4: endpoint reuse + 404 invalidation


@pytest.mark.asyncio
async def test_iter_send_events_reuses_pre_resolved_endpoint() -> None:
    """B4: the send hot path resolves the endpoint once (shared with the
    lease push) — no second runtime_view + claim status round-trip."""
    session = MagicMock()

    endpoint = RuntimeEndpoint(
        base_url="http://router:8080",
        headers={"X-Sandbox-Id": "sbx-abc123"},
    )

    class _StreamResponse:
        status_code = 200

        async def aread(self) -> bytes:
            return b""

        def aiter_lines(self):
            async def _gen():
                yield 'data: {"type":"text_delta","data":{"text":"hi"}}'
                yield "data: [DONE]"

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

    bootstrap = OpenClawBridgeBootstrap(session, http_client=lambda **_: mock_http)
    bootstrap._resolve_endpoint_for_project = AsyncMock(return_value=None)

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch(
            "prodavan.application.agent.openclaw_bridge.ensure_recent_bind",
            new=AsyncMock(),
        ),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        events = [
            event
            async for event in bootstrap.iter_send_events(
                project_id="prj_1",
                session_id="ags_abc",
                message="hello",
                endpoint=endpoint,
            )
        ]

    assert [e.type for e in events] == [AgentEventType.TEXT_DELTA]
    bootstrap._resolve_endpoint_for_project.assert_not_awaited()
    stream_call = mock_http.stream.call_args
    assert stream_call.kwargs["headers"]["X-Sandbox-Id"] == "sbx-abc123"


@pytest.mark.asyncio
async def test_stream_send_404_invalidates_cache_and_reresolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B4: router 404 → cached claim→sandbox mapping invalidated, then ONE
    re-resolve + retry against the fresh sandbox."""
    import prodavan.application.agent.openclaw_bridge as bridge_mod
    from prodavan.application.agent import runtime_transport as rt

    monkeypatch.setattr(rt.settings, "pod_runtime_mode", "sandbox")

    session = MagicMock()

    ep_old = RuntimeEndpoint(
        base_url="http://router:8080",
        headers={"X-Sandbox-Id": "sbx-old"},
    )
    ep_new = RuntimeEndpoint(
        base_url="http://router:8080",
        headers={"X-Sandbox-Id": "sbx-new"},
    )

    class _StreamResponse:
        def __init__(self, status_code: int, body: bytes = b"") -> None:
            self.status_code = status_code
            self._body = body

        async def aread(self) -> bytes:
            return self._body

        def aiter_lines(self):
            async def _gen():
                yield 'data: {"type":"text_delta","data":{"text":"hi"}}'
                yield "data: [DONE]"

            return _gen()

    class _StreamCtx:
        def __init__(self, resp: _StreamResponse) -> None:
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *args):
            return None

    responses = iter(
        [
            _StreamResponse(404, b'{"error":"sandbox not found"}'),
            _StreamResponse(200),
        ]
    )

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.stream = MagicMock(side_effect=lambda *a, **kw: _StreamCtx(next(responses)))

    bootstrap = OpenClawBridgeBootstrap(session, http_client=lambda **_: mock_http)
    bootstrap._resolve_endpoint_for_project = AsyncMock(side_effect=[ep_new])
    invalidate = AsyncMock()

    with (
        patch("prodavan.application.agent.openclaw_bridge.settings") as mock_settings,
        patch.object(bridge_mod, "invalidate_sandbox_name_cache", invalidate),
        patch(
            "prodavan.application.agent.openclaw_bridge.ensure_recent_bind",
            new=AsyncMock(),
        ),
    ):
        mock_settings.pod_agent_runtime_enabled = True
        events = [
            event
            async for event in bootstrap.iter_send_events(
                project_id="prj_1",
                session_id="ags_abc",
                message="hello",
                endpoint=ep_old,
            )
        ]

    assert [e.type for e in events] == [AgentEventType.TEXT_DELTA]
    invalidate.assert_awaited_once()
    assert invalidate.await_args.kwargs["sandbox_name"] == "sbx-old"
    # exactly ONE re-resolve — the fresh endpoint served the retry
    bootstrap._resolve_endpoint_for_project.assert_awaited_once()
    assert mock_http.stream.call_count == 2
