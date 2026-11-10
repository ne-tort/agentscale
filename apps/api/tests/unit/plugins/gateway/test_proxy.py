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

import hashlib
import hmac

import pytest

from prodavan.application.plugins.gateway.errors import (
    GatewayError,
    GatewayErrorCode,
)
from prodavan.application.plugins.gateway.ports import (
    BackendRequest,
    BackendResponse,
    GatewayClaims,
    GatewayGrant,
    StaticResponsePolicy,
)
from prodavan.application.plugins.gateway.proxy import (
    IDEMPOTENT_METHODS,
    sanitize_response_headers,
)

from .conftest import ARTIFACT_DIGEST, RecordingHttpPort, build_harness


def _direct_identity(
    *,
    company_id: str = "co-123",
    instance_id: str = "inst-alpha-1",
    artifact_digest: str | None = ARTIFACT_DIGEST,
) -> tuple[GatewayClaims, GatewayGrant]:
    claims = GatewayClaims(
        plugin_id="mod_equipment",
        company_id=company_id,
        subject_id="employee:emp-1",
        plugin_instance_id=instance_id,
        artifact_digest=artifact_digest,
        scopes=frozenset({"pluginpage:api"}),
    )
    grant = GatewayGrant(
        grant_id="grant-1",
        plugin_instance_id=instance_id,
        artifact_digest=artifact_digest,
        active=True,
        capability_token="stale-grant-token",
    )
    return claims, grant


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
    assert h.activation.wake_calls == [("mod_equipment", "co-123", "inst-alpha-1")]
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
        plugin_instance_id="inst-alpha-1",
        artifact_digest=ARTIFACT_DIGEST,
        scopes=frozenset({"pluginpage:api"}),
    )
    grant = GatewayGrant(
        grant_id="grant-1", plugin_instance_id="inst-alpha-1",
        active=True, capability_token="t", artifact_digest=ARTIFACT_DIGEST,
    )
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
        plugin_id="p", company_id="c", subject_id="s",
        plugin_instance_id="inst-a", scopes=frozenset({"x"}),
        artifact_digest=ARTIFACT_DIGEST,
    )
    grant = GatewayGrant(
        grant_id="g", plugin_instance_id="inst-a", active=True, capability_token="t",
        artifact_digest=ARTIFACT_DIGEST,
    )

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
    h.activation.grant_id = grant.grant_id
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
        plugin_id="p", company_id="c", subject_id="s",
        plugin_instance_id="inst-a", scopes=frozenset({"x"}),
        artifact_digest=ARTIFACT_DIGEST,
    )
    grant = GatewayGrant(
        grant_id="g", plugin_instance_id="inst-a", active=True, capability_token="t",
        artifact_digest=ARTIFACT_DIGEST,
    )

    class BrokenHttp(RecordingHttpPort):
        async def send(self, request: BackendRequest) -> BackendResponse:
            raise ConnectionError("connection refused")

    h = build_harness()
    h.activation.grant_id = grant.grant_id
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


def test_static_policy_requires_exact_shell_origin_and_matches_ci_headers() -> None:
    policy = StaticResponsePolicy(shell_origin="https://agentscale.example.test")
    headers = policy.response_headers(artifact_digest=ARTIFACT_DIGEST)
    assert headers == {
        "X-AgentScale-Bundle-Digest": ARTIFACT_DIGEST,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            "frame-ancestors https://agentscale.example.test"
        ),
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    for invalid in (
        "", "http://agentscale.example.test", "https://*.example.test",
        "https://agentscale.example.test/path",
        "https://agentscale.example.test/?token=x",
    ):
        with pytest.raises(ValueError):
            StaticResponsePolicy(shell_origin=invalid)


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
async def test_static_response_has_exact_server_digest_and_approved_security_headers() -> None:
    spoofed_digest = "sha256:" + "f" * 64
    capability_secret = "client-capability-secret-never-leak"
    dsn_secret = "postgresql://user:dsn-password@db.example/private"
    static = RecordingHttpPort(
        response=BackendResponse(
            status_code=200,
            headers={
                "Content-Type": "text/css; charset=utf-8",
                "x-agentscale-bundle-digest": spoofed_digest,
                "content-security-policy": "default-src *",
                "x-content-type-options": "unsafe",
            },
            body=b"body { color: green; }",
        )
    )
    h = build_harness(static_http=static)
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/assets/plugin.css",
            headers={
                "Authorization": "Bearer good-session",
                "X-AgentScale-Bundle-Digest": spoofed_digest,
                "Content-Security-Policy": "default-src *",
                "X-Plugin-Capability-Token": capability_secret,
                "X-Plugin-DSN": dsn_secret,
            },
        )

    assert response.status_code == 200
    assert response.headers["X-AgentScale-Bundle-Digest"] == ARTIFACT_DIGEST
    assert response.headers["X-AgentScale-Bundle-Digest"] != spoofed_digest
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors https://agentscale.example.test"
    )
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )
    assert response.headers["Content-Type"] == "text/css; charset=utf-8"
    assert response.content == b"body { color: green; }"

    sent = h.static_http.requests[0]
    assert sent.headers["X-Plugin-Artifact-Digest"] == ARTIFACT_DIGEST
    assert spoofed_digest not in sent.headers.values()
    assert "X-AgentScale-Bundle-Digest" not in sent.headers
    assert "Content-Security-Policy" not in sent.headers
    assert "X-Plugin-Capability-Token" not in sent.headers
    assert capability_secret not in repr(sent.headers)
    assert dsn_secret not in repr(sent.headers)
    assert capability_secret not in repr(h.audit.events)
    assert dsn_secret not in repr(h.audit.events)
    assert capability_secret not in response.text
    assert dsn_secret not in response.text
    assert capability_secret not in repr(response.headers)
    assert dsn_secret not in repr(response.headers)
    assert capability_secret not in str(response.request.url)
    assert dsn_secret not in str(response.request.url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "published_digest",
    (None, "sha256:" + "b" * 64),
)
async def test_static_missing_or_mismatched_published_digest_is_typed_unavailable(
    published_digest: str | None,
) -> None:
    h = build_harness(
        public_pages=frozenset({"/public/info"}),
        published_artifact_digest=published_digest,
    )
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/public/info"
        )

    assert response.status_code == 503
    assert response.json()["code"] == GatewayErrorCode.PORT_UNAVAILABLE.value
    assert "X-AgentScale-Bundle-Digest" not in response.headers
    assert h.static_http.requests == []
    assert h.backend_http.requests == []
    assert h.activation.wake_calls == []


@pytest.mark.asyncio
async def test_static_missing_durable_digest_is_typed_unavailable() -> None:
    h = build_harness(
        public_pages=frozenset({"/public/info"}),
        artifact_digest=None,
        published_artifact_digest=None,
    )
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/public/info"
        )

    assert response.status_code == 503
    assert response.json()["code"] == GatewayErrorCode.PORT_UNAVAILABLE.value
    assert "X-AgentScale-Bundle-Digest" not in response.headers
    assert h.static_http.requests == []


@pytest.mark.asyncio
async def test_static_missing_shell_policy_is_fail_closed() -> None:
    h = build_harness(public_pages=frozenset({"/public/info"}))
    h.proxy.static_response_policy = None
    async with h.client as client:
        response = await client.get("/pluginpage/mod_equipment/co-123/public/info")
    assert response.status_code == 503
    assert response.json()["code"] == GatewayErrorCode.PORT_UNAVAILABLE.value
    assert h.static_http.requests == []


@pytest.mark.asyncio
async def test_static_public_and_private_auth_contract_is_unchanged() -> None:
    h = build_harness(public_pages=frozenset({"/public/info"}))
    async with h.client as client:
        public = await client.get(
            "/pluginpage/mod_equipment/co-123/public/info"
        )
        private_anonymous = await client.get(
            "/pluginpage/mod_equipment/co-123/private/info"
        )
        private_authenticated = await client.get(
            "/pluginpage/mod_equipment/co-123/private/info",
            headers={"Authorization": "Bearer good-session"},
        )

    assert public.status_code == 200
    assert public.headers["X-AgentScale-Bundle-Digest"] == ARTIFACT_DIGEST
    assert public.headers["X-Content-Type-Options"] == "nosniff"
    assert public.headers["Content-Security-Policy"] == (
        "default-src 'self'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors https://agentscale.example.test"
    )
    assert private_anonymous.status_code == 401
    assert private_anonymous.json()["code"] == GatewayErrorCode.SESSION_REQUIRED.value
    assert private_authenticated.status_code == 200
    assert private_authenticated.headers["X-AgentScale-Bundle-Digest"] == ARTIFACT_DIGEST
    assert len(h.static_http.requests) == 2
    for request in h.static_http.requests:
        assert "X-Plugin-Capability-Token" not in request.headers
        assert "Authorization" not in request.headers
        assert "Cookie" not in request.headers
    assert h.backend_http.requests == []
    assert h.activation.wake_calls == []


@pytest.mark.asyncio
async def test_static_remains_public_read_only_get_head() -> None:
    h = build_harness(public_pages=frozenset({"/public/info"}))
    async with h.client as client:
        head = await client.head(
            "/pluginpage/mod_equipment/co-123/public/info"
        )
        post = await client.post(
            "/pluginpage/mod_equipment/co-123/public/info"
        )
        head_body = await client.request(
            "HEAD", "/pluginpage/mod_equipment/co-123/public/info", content=b"{}"
        )

    assert head.status_code == 200
    assert head.headers["X-AgentScale-Bundle-Digest"] == ARTIFACT_DIGEST
    assert head.headers["X-Content-Type-Options"] == "nosniff"
    assert head.headers["Content-Security-Policy"].startswith(
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors "
    )
    assert post.status_code == 405
    assert post.json()["code"] == GatewayErrorCode.METHOD_NOT_ALLOWED.value
    assert head_body.status_code == 403  # HEAD не несёт problem JSON body
    assert [request.method for request in h.static_http.requests] == ["HEAD"]
    assert "X-Plugin-Capability-Token" not in h.static_http.requests[0].headers
    assert h.backend_http.requests == []


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
        "X-Plugin-Instance-ID": "evil",
        "X-Project-Id": "evil",
        "X-Plugin-Capability-Token": "evil",
        "X-Plugin-Artifact-Digest": "sha256:" + "f" * 64,
        "Cookie": "session=evil",
        "X-Forwarded-For": "9.9.9.9",
    }
    async with h.client as client:
        await client.get("/pluginpage/mod_equipment/co-123/api/items", headers=evil)
    sent = h.backend_http.requests[0].headers
    assert sent["X-Company-ID"] == "co-123"
    assert sent["X-Plugin-ID"] == "mod_equipment"
    assert sent["X-Plugin-Instance-ID"] == "inst-alpha-1"
    assert sent["X-Plugin-Artifact-Digest"] == ARTIFACT_DIGEST
    assert "sha256:" + "f" * 64 not in sent.values()
    assert "evil" not in sent.values()
    assert "Cookie" not in sent and "Authorization" not in sent
    assert "X-Forwarded-For" not in sent


@pytest.mark.asyncio
async def test_missing_durable_instance_is_503_before_wake_or_send() -> None:
    from prodavan.application.plugins.gateway.ports import GatewayClaims, GatewayGrant

    h = build_harness()
    claims = GatewayClaims(plugin_id="p", company_id="c", subject_id="s")
    grant = GatewayGrant(grant_id="g", active=True, capability_token="stale")
    with pytest.raises(GatewayError) as exc:
        await h.proxy.forward(
            path="items", method="POST", body=b"{}", headers={},
            claims=claims, grant=grant,
        )
    assert exc.value.status == 503
    assert exc.value.gateway_code is GatewayErrorCode.PORT_UNAVAILABLE
    assert h.activation.wake_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_real_grant_instance_reaches_backend_request_and_trusted_headers() -> None:
    body = b"{}"
    signature = "sha256=" + hmac.new(
        b"unit-test-webhook-secret-never-in-db", body, hashlib.sha256
    ).hexdigest()
    h = build_harness(plugin_instance_id="inst-durable-real")
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events?keep=1",
            content=body,
            headers={
                "X-Prodavan-Signature": signature,
                "X-Prodavan-Event": "offer.created",
                "X-Prodavan-Delivery": "del-instance-real",
                "X-Plugin-Instance-ID": "inst-client-spoof",
            },
        )
    assert response.status_code == 200
    sent = h.backend_http.requests[0]
    assert sent.plugin_instance_id == "inst-durable-real"
    assert sent.headers["X-Plugin-Instance-ID"] == "inst-durable-real"
    assert sent.headers["X-Plugin-Artifact-Digest"] == ARTIFACT_DIGEST
    assert sent.headers["X-Plugin-Capability-Token"] == "fresh-capability-token-from-lease"
    assert sent.headers["X-Plugin-ID"] == "mod_equipment"
    assert sent.path == "/events"
    assert sent.query_string == b"keep=1"
    assert "inst-client-spoof" not in sent.headers.values()


@pytest.mark.asyncio
async def test_per_instance_a_b_do_not_mix_backend_routes() -> None:
    class RoutedHttp(RecordingHttpPort):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []

        async def send(self, request: BackendRequest) -> BackendResponse:
            self.urls.append(f"http://{request.plugin_instance_id}.svc{request.path}")
            return await super().send(request)

    async def exercise(instance_id: str) -> tuple[object, RoutedHttp]:
        h = build_harness(plugin_instance_id=instance_id)
        routed = RoutedHttp()
        h.proxy.backend_http = routed
        async with h.client as client:
            response = await client.get(
                "/pluginpage/mod_equipment/co-123/api/items",
                headers={"Authorization": "Bearer good-session"},
            )
        return response, routed

    response_a, routed_a = await exercise("inst-a")
    response_b, routed_b = await exercise("inst-b")
    assert response_a.status_code == response_b.status_code == 200
    assert routed_a.urls == ["http://inst-a.svc/api/items"]
    assert routed_b.urls == ["http://inst-b.svc/api/items"]
    assert routed_a.requests[0].headers["X-Plugin-Instance-ID"] == "inst-a"
    assert routed_b.requests[0].headers["X-Plugin-Instance-ID"] == "inst-b"



@pytest.mark.asyncio
async def test_route_missing_grant_instance_returns_503() -> None:
    h = build_harness()
    h.grants.plugin_instance_id = None  # type: ignore[assignment]
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 503
    assert response.json()["code"] == GatewayErrorCode.PORT_UNAVAILABLE.value
    assert h.activation.wake_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_route_missing_grant_digest_returns_503_before_wake() -> None:
    h = build_harness()
    h.grants.artifact_digest = None
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 503
    assert response.json()["code"] == GatewayErrorCode.PORT_UNAVAILABLE.value
    assert h.activation.wake_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("plugin_instance_id", "inst-other"),
        ("artifact_digest", "sha256:" + "b" * 64),
    ),
)
async def test_claim_grant_identity_mismatch_is_rejected_before_wake(
    field: str, value: str
) -> None:
    from dataclasses import replace

    h = build_harness()
    claims, grant = _direct_identity()
    claims = replace(claims, **{field: value})
    with pytest.raises(GatewayError) as exc:
        await h.proxy.forward(
            path="items", method="POST", body=b"{}", headers={},
            claims=claims, grant=grant,
        )
    assert exc.value.status == 503
    assert exc.value.gateway_code is GatewayErrorCode.PORT_UNAVAILABLE
    assert h.activation.wake_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_missing_durable_digest_is_rejected_before_wake() -> None:
    h = build_harness()
    claims, grant = _direct_identity(artifact_digest=None)
    with pytest.raises(GatewayError) as exc:
        await h.proxy.forward(
            path="items", method="POST", body=b"{}", headers={},
            claims=claims, grant=grant,
        )
    assert exc.value.status == 503
    assert exc.value.gateway_code is GatewayErrorCode.PORT_UNAVAILABLE
    assert h.activation.wake_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        ("artifact_digest", "sha256:" + "b" * 64),
        ("grant_id", "grant-other"),
        ("company_id", "co-other"),
        ("plugin_id", "other-plugin"),
        ("plugin_instance_id", "inst-other"),
    ),
)
async def test_lease_identity_mismatch_is_rejected_without_backend_send(
    attribute: str, value: str
) -> None:
    h = build_harness()
    if attribute == "artifact_digest":
        h.activation.artifact_digest = value
    else:
        original_wake = h.activation.wake

        async def mismatched_wake(**kwargs):
            lease = await original_wake(**kwargs)
            from dataclasses import replace

            return replace(lease, **{attribute: value})

        h.proxy.activation.wake = mismatched_wake  # type: ignore[method-assign]
    claims, grant = _direct_identity()
    with pytest.raises(GatewayError) as exc:
        await h.proxy.forward(
            path="items", method="POST", body=b"{}", headers={},
            claims=claims, grant=grant,
        )
    assert exc.value.status == 503
    assert exc.value.gateway_code is GatewayErrorCode.WAKE_LEASE_MISSING
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_every_wake_supplies_its_own_fresh_token_and_bound_identity() -> None:
    from prodavan.application.plugins.gateway.ports import WakeLease

    class SequenceActivation:
        def __init__(self) -> None:
            self.wake_calls: list[tuple[str, str, str | None]] = []

        async def wake(
            self, *, plugin_id: str, company_id: str, plugin_instance_id: str | None = None
        ) -> WakeLease:
            self.wake_calls.append((plugin_id, company_id, plugin_instance_id))
            return WakeLease(
                plugin_id=plugin_id,
                company_id=company_id,
                plugin_instance_id=plugin_instance_id,
                artifact_digest=ARTIFACT_DIGEST,
                grant_id="grant-1",
                capability_token=f"fresh-wake-token-{len(self.wake_calls)}",
                expires_at=1_000_000.0,
                ready=True,
            )

        async def wait_ready(self, lease: WakeLease, *, timeout_seconds: float) -> bool:
            return lease.ready

    h = build_harness()
    activation = SequenceActivation()
    h.proxy.activation = activation
    claims, grant = _direct_identity()
    for _ in range(2):
        await h.proxy.forward(
            path="items", method="POST", body=b"{}", headers={},
            claims=claims, grant=grant,
        )
    sent = h.backend_http.requests
    assert [request.headers["X-Plugin-Capability-Token"] for request in sent] == [
        "fresh-wake-token-1", "fresh-wake-token-2"
    ]
    assert all(request.headers["X-Plugin-Instance-ID"] == "inst-alpha-1" for request in sent)
    assert all(request.headers["X-Plugin-Artifact-Digest"] == ARTIFACT_DIGEST for request in sent)
    assert all("stale-grant-token" not in request.headers.values() for request in sent)


@pytest.mark.asyncio
async def test_tenant_a_b_keep_instance_digest_and_token_bindings() -> None:
    digest_a = ARTIFACT_DIGEST
    digest_b = "sha256:" + "b" * 64

    async def exercise(company_id: str, instance_id: str, digest: str):
        h = build_harness(artifact_digest=digest)
        claims, grant = _direct_identity(
            company_id=company_id, instance_id=instance_id, artifact_digest=digest
        )
        await h.proxy.forward(
            path="items", method="POST", body=b"{}", headers={},
            claims=claims, grant=grant,
        )
        return h

    a = await exercise("co-a", "inst-a", digest_a)
    b = await exercise("co-b", "inst-b", digest_b)
    request_a = a.backend_http.requests[0]
    request_b = b.backend_http.requests[0]
    assert a.activation.wake_calls == [("mod_equipment", "co-a", "inst-a")]
    assert b.activation.wake_calls == [("mod_equipment", "co-b", "inst-b")]
    assert request_a.headers["X-Company-ID"] == "co-a"
    assert request_b.headers["X-Company-ID"] == "co-b"
    assert request_a.headers["X-Plugin-Instance-ID"] == "inst-a"
    assert request_b.headers["X-Plugin-Instance-ID"] == "inst-b"
    assert request_a.headers["X-Plugin-Artifact-Digest"] == digest_a
    assert request_b.headers["X-Plugin-Artifact-Digest"] == digest_b


@pytest.mark.asyncio
async def test_trusted_transport_values_stay_out_of_url_body_and_audit() -> None:
    from dataclasses import asdict

    h = build_harness()
    claims, grant = _direct_identity()
    spoof_digest = "sha256:" + "f" * 64
    await h.proxy.forward(
        path="items",
        method="POST",
        body=b"{}",
        headers={
            "X-Plugin-Capability-Token": "client-secret-token",
            "X-Plugin-Artifact-Digest": spoof_digest,
            "X-Plugin-Instance-ID": "inst-client-spoof",
        },
        claims=claims,
        grant=grant,
        query_string=b"keep=1",
    )
    sent = h.backend_http.requests[0]
    audit_text = repr([asdict(event) for event in h.audit.events])
    for secret in (
        "fresh-capability-token-from-lease",
        "client-secret-token",
        ARTIFACT_DIGEST,
        spoof_digest,
    ):
        assert secret not in audit_text
        assert secret not in sent.path
        assert secret not in sent.query_string.decode("ascii")
        assert secret.encode("utf-8") not in sent.body
    assert sent.body == b"{}"
    assert sent.headers["X-Plugin-Artifact-Digest"] == ARTIFACT_DIGEST
    assert sent.headers["X-Plugin-Instance-ID"] == "inst-alpha-1"
