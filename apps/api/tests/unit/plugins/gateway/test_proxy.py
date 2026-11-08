"""Тесты ``GatewayProxy`` и роутинга (§7.2/§7.4, критерии §11 №9).

Ключевые проверки:
* backend спит → ``wake`` вызван ДО ``forward`` (общий timeline);
* SSE/стриминг НЕ буферизуется (живой async-генератор чанков);
* retry ТОЛЬКО на идемпотентные методы;
* недоступность → ``202 + Retry-After`` / типизированная ошибка, не молчание;
* статика идёт на отдельный static-origin, ``/api/*`` — в backend.

Тесты НЕ запускаются в рамках миграции (верификационный freeze).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.gateway.errors import (
    GatewayError,
    GatewayErrorCode,
)
from prodavan.application.plugins.gateway.ports import (
    BackendRequest,
    BackendResponse,
)
from prodavan.application.plugins.gateway.proxy import (
    IDEMPOTENT_METHODS,
    sanitize_response_headers,
)

from .conftest import RecordingHttpPort, build_harness


# --------------------------------------------------------------- wake → forward


@pytest.mark.asyncio
async def test_sleeping_backend_wakes_before_forward() -> None:
    """КРИТЕРИЙ: backend спит → wake вызван ДО forward.

    Общий ``timeline`` заполняют и ``ActivationPort.wake``, и
    ``BackendHttpPort.send``. Первым обязано идти ``wake``.
    """
    timeline: list[str] = []
    h = build_harness(timeline=timeline)
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 200
    assert timeline == ["wake", "send"], timeline
    assert h.activation.wake_calls == [("mod_equipment", "co-123")]
    assert h.activation.wait_calls
    assert len(h.backend_http.requests) == 1


@pytest.mark.asyncio
async def test_wake_lease_token_overrides_stale_grant_token() -> None:
    """Новый короткий capability-token из lease перезаписывает токен grant'а."""
    h = build_harness()
    async with h.client as client:
        await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={
                "Authorization": "Bearer good-session",
                "X-Plugin-Capability-Token": "client-forged-token",
            },
        )
    sent = h.backend_http.requests[0]
    assert sent.headers["X-Plugin-Capability-Token"] == "fresh-capability-token-from-lease"
    assert "client-forged-token" not in sent.headers.values()
    # Клиентские Cookie/Authorization не доходят до backend.
    assert "Authorization" not in sent.headers
    assert "Cookie" not in sent.headers


@pytest.mark.asyncio
async def test_backend_not_ready_returns_202_with_retry_after() -> None:
    """Недоступность → 202 + Retry-After, а не тихое молчание/пустой 200."""
    h = build_harness(wake_ready=False)
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 202
    assert response.json()["code"] == "PLUGIN_BACKEND_WAKING"
    assert int(response.headers["Retry-After"]) > 0
    # До send дело не дошло: wake был, forward — нет.
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_missing_static_origin_is_fail_closed() -> None:
    """Нет static-origin → 503, а не «отдадим index.html»."""
    h = build_harness()
    h.proxy.static_http = None
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/public/info",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 503
    assert response.json()["code"] == GatewayErrorCode.STATIC_ORIGIN_NOT_CONFIGURED.value


# ---------------------------------------------------------------------- SSE


@pytest.mark.asyncio
async def test_sse_is_not_buffered() -> None:
    """КРИТЕРИЙ: SSE не буферизуется — ответ потоковый, чанки приходят как есть."""
    chunks = [b"data: one\n\n", b"data: two\n\n", b"data: three\n\n"]
    h = build_harness(chunks=chunks)
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/stream",
            headers={
                "Authorization": "Bearer good-session",
                "Accept": "text/event-stream",
            },
        )
    assert response.status_code == 200
    assert response.text == "data: one\n\ndata: two\n\ndata: three\n\n"
    # Потоковый ответ помечен запретом буферизации на reverse-proxy.
    assert response.headers.get("X-Accel-Buffering") == "no"
    assert "text/event-stream" in response.headers.get("content-type", "")
    # Запрос ушёл в backend с флагом stream=True и без retry.
    assert h.backend_http.requests[0].stream is True
    assert len(h.backend_http.requests) == 1


@pytest.mark.asyncio
async def test_sse_response_is_a_live_iterator_not_a_buffered_body() -> None:
    """Прямая проверка ``GatewayProxy``: SSE-ответ НЕ собран в ``bytes``."""
    from prodavan.application.plugins.gateway.ports import (
        GatewayClaims,
        GatewayGrant,
    )

    chunks = [b"data: a\n\n", b"data: b\n\n"]
    h = build_harness(chunks=chunks)
    claims = GatewayClaims(
        plugin_id="mod_equipment",
        company_id="co-123",
        subject_id="employee:emp-1",
        scopes=frozenset({"pluginpage:api"}),
    )
    grant = GatewayGrant(grant_id="grant-1", active=True, capability_token="t")
    response = await h.proxy.forward(
        path="api/stream",
        method="GET",
        body=b"",
        headers={"Accept": "text/event-stream"},
        claims=claims,
        grant=grant,
        stream=True,
    )
    assert response.is_streaming is True
    assert response.body is None
    assert response.stream is not None
    # Ровно один запрос: стриминг НЕ ретраится.
    assert len(h.backend_http.requests) == 1
    assert h.backend_http.requests[0].stream is True
    assert h.backend_http.requests[0].timeout_seconds == h.proxy.limits.stream_timeout_seconds
    # Итератор живой: чанки приходят по одному, без предварительной сборки.
    collected = [chunk async for chunk in h.proxy.streaming_chunks(response)]
    assert collected == chunks


def test_streaming_response_upstream_must_provide_stream() -> None:
    """Fail-closed: upstream вернул обычный ответ вместо потока → GatewayError."""
    h = build_harness()
    response = BackendResponse(status_code=200, body=b"not a stream")
    with pytest.raises(GatewayError) as ei:
        h.proxy.streaming_chunks(response)
    assert ei.value.gateway_code is GatewayErrorCode.UPSTREAM_ERROR


# --------------------------------------------------------------------- retry


@pytest.mark.asyncio
async def test_retry_only_on_idempotent_methods() -> None:
    """POST не ретраится; GET ретраится на 5xx (в пределах лимита)."""
    from prodavan.application.plugins.gateway.ports import (
        GatewayClaims,
        GatewayGrant,
    )

    claims = GatewayClaims(
        plugin_id="p", company_id="c", subject_id="s", scopes=frozenset({"x"})
    )
    grant = GatewayGrant(grant_id="g", active=True, capability_token="t")

    class FlakyHttp(RecordingHttpPort):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def send(self, request: BackendRequest) -> BackendResponse:
            self.calls += 1
            self.requests.append(request)
            return BackendResponse(status_code=503, body=b"unavailable")

    http = FlakyHttp()
    h = build_harness(timeline=[])
    h.proxy.backend_http = http
    post_response = await h.proxy.forward(
        path="items", method="POST", body=b"{}", headers={},
        claims=claims, grant=grant,
    )
    # POST: ровно ОДНА попытка, несмотря на 503 (дубль мутации недопустим).
    assert http.calls == 1
    assert post_response.status_code == 503

    http2 = FlakyHttp()
    h.proxy.backend_http = http2
    get_response = await h.proxy.forward(
        path="items", method="GET", body=b"", headers={},
        claims=claims, grant=grant,
    )
    # GET (идемпотентный): 1 + max_idempotent_retries попыток.
    assert http2.calls == 1 + h.proxy.limits.max_idempotent_retries
    # Итоговый 5xx пробрасывается клиенту — это НЕ тихое молчание.
    assert get_response.status_code == 503


@pytest.mark.asyncio
async def test_transport_error_is_typed_not_silent() -> None:
    from prodavan.application.plugins.gateway.ports import (
        GatewayClaims,
        GatewayGrant,
    )

    claims = GatewayClaims(
        plugin_id="p", company_id="c", subject_id="s", scopes=frozenset({"x"})
    )
    grant = GatewayGrant(grant_id="g", active=True, capability_token="t")

    class BrokenHttp(RecordingHttpPort):
        async def send(self, request: BackendRequest) -> BackendResponse:
            raise ConnectionError("connection refused")

    h = build_harness()
    h.proxy.backend_http = BrokenHttp()
    with pytest.raises(GatewayError) as ei:
        await h.proxy.forward(
            path="items", method="POST", body=b"{}", headers={},
            claims=claims, grant=grant,
        )
    assert ei.value.gateway_code is GatewayErrorCode.UPSTREAM_ERROR
    assert ei.value.status == 502


def test_idempotent_method_set() -> None:
    assert "POST" not in IDEMPOTENT_METHODS
    assert "PATCH" not in IDEMPOTENT_METHODS
    assert {"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"} <= IDEMPOTENT_METHODS


def test_response_headers_strip_hop_by_hop_and_cookies() -> None:
    out = sanitize_response_headers(
        {
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
            "Content-Length": "999",
            "Set-Cookie": "session=stolen",
            "X-Trace-Id": "t-1",
        }
    )
    assert out == {"Content-Type": "application/json", "X-Trace-Id": "t-1"}


# ------------------------------------------------------------- роутинг static


@pytest.mark.asyncio
async def test_static_goes_to_static_origin_not_backend() -> None:
    h = build_harness(public_pages=frozenset({"/public/info"}))
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/public/info"
        )
    assert response.status_code == 200
    assert b"<html>plugin</html>" in response.content
    assert len(h.static_http.requests) == 1
    assert h.backend_http.requests == []
    # Статика НЕ получает capability-токен и НЕ будит backend.
    assert "X-Plugin-Capability-Token" not in h.static_http.requests[0].headers
    assert h.activation.wake_calls == []


@pytest.mark.asyncio
async def test_api_goes_to_backend_not_static_origin() -> None:
    h = build_harness()
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 200
    assert len(h.backend_http.requests) == 1
    assert h.static_http.requests == []


@pytest.mark.asyncio
async def test_empty_pluginpage_path_has_no_index_html_fallback() -> None:
    h = build_harness()
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/",
            headers={"Authorization": "Bearer good-session"},
        )
    # Пустой путь статики — явный отказ, а не подстановка index.html.
    assert response.status_code == 400
    assert "<html" not in response.text.lower()
    assert h.static_http.requests == []


@pytest.mark.asyncio
async def test_health_is_200_when_runtime_installed() -> None:
    h = build_harness()
    async with h.client as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "plugin-gateway"


@pytest.mark.asyncio
async def test_incoming_trusted_headers_never_reach_backend() -> None:
    """Комплексно: злые X-Plugin-* не проходят ни в static, ни в backend."""
    h = build_harness()
    evil = {
        "Authorization": "Bearer good-session",
        "X-Company-Id": "evil",
        "X-Plugin-Id": "evil",
        "X-Project-Id": "evil",
        "X-Plugin-Capability-Token": "evil",
        "Cookie": "session=evil",
        "X-Forwarded-For": "9.9.9.9",
    }
    async with h.client as client:
        await client.get("/pluginpage/mod_equipment/co-123/api/items", headers=evil)
    sent = h.backend_http.requests[0].headers
    assert sent["X-Company-ID"] == "co-123"
    assert sent["X-Plugin-ID"] == "mod_equipment"
    assert "evil" not in sent.values()
    assert "Cookie" not in sent and "Authorization" not in sent
    assert "X-Forwarded-For" not in sent
