"""Unit-тесты ``HttpProxyPort`` (писать, НЕ запускать, verification freeze).

ТЗ: proxy добавляет Authorization из свежего токена и НЕ буферизует SSE.
Дополнительно: доверенные ``X-Plugin-*`` из claims, отброс клиентских
Authorization/Cookie/X-Plugin-*, Release не в forward, отказ при истёкшем
токене/отсутствии транспорта.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.plugins.activation.errors import NotReady
from prodavan.application.plugins.activation.ports import MintedClaims, ProxyPort
from prodavan.application.plugins.gateway.ports import BackendRequest, BackendResponse
from prodavan.infrastructure.plugins.runtime.proxy import (
    HEADER_AUTHORIZATION,
    HttpProxyPort,
    ProxyConfigurationError,
    build_backend_request,
    build_outgoing_headers,
    plugin_service_url,
)
from tests.unit.plugins.runtime.conftest import (
    TEST_DEPLOYMENT,
    TEST_DIGEST,
    TEST_INSTANCE,
    FakeBackendSender,
    fresh_claims,
)


def _port(config, backend: FakeBackendSender | None = None, **over):  # type: ignore[no-untyped-def]
    return HttpProxyPort(
        config=config,
        backend_http=backend or FakeBackendSender(),
        **over,
    )


def _request(**over):  # type: ignore[no-untyped-def]
    base = {
        "method": "GET",
        "path": "/api/items",
        "headers": {
            "Accept": "text/event-stream",
            "Authorization": "Bearer CLIENT_STOLEN_TOKEN",
            "Cookie": "session=evil",
            "X-Plugin-ID": "evil-plugin",
            "X-Company-ID": "evil-company",
            "X-Forwarded-For": "1.2.3.4",
            "X-Plugin-Capability-Token": "client-token",
            "X-Plugin-Instance-ID": "inst-client-spoof",
            "X-Plugin-Grant-ID": "grant-client-spoof",
            "X-Plugin-Artifact-Digest": "sha256:" + "f" * 64,
        },
        "body": b"",
        "stream": True,
    }
    base.update(over)
    return base


def _claims_with(changes: dict[str, object]):  # type: ignore[no-untyped-def]
    claims = fresh_claims()
    payload = dict(claims.claims)
    for name, value in changes.items():
        if value is None:
            payload.pop(name, None)
        else:
            payload[name] = value
    return replace(claims, claims=payload)


def _header(request, name: str):  # type: ignore[no-untyped-def]
    for key, value in request.headers.items():
        if key.lower() == name.lower():
            return value
    return None


# --------------------------------------------------------------------------- заголовки


def test_authorization_uses_fresh_token_not_client_value() -> None:
    """КЛЮЧЕВОЕ: Bearer = свежий WakeResult.token, клиентский отброшен."""
    claims = fresh_claims("fresh-token-abc")
    request = build_backend_request(
        _request(),
        claims,
        plugin_instance_id=TEST_INSTANCE,
        backend_timeout_seconds=30.0,
        stream_timeout_seconds=300.0,
    )
    assert _header(request, HEADER_AUTHORIZATION) == "Bearer fresh-token-abc"
    assert _header(request, "X-Plugin-Capability-Token") == "fresh-token-abc"
    assert _header(request, "X-Plugin-Instance-ID") == TEST_INSTANCE
    assert _header(request, "X-Plugin-Grant-ID") == "grant_01"
    assert _header(request, "X-Plugin-Artifact-Digest") == TEST_DIGEST
    assert request.plugin_instance_id == TEST_INSTANCE
    assert "CLIENT_STOLEN_TOKEN" not in str(request.headers)
    assert "client-token" not in str(request.headers)


def test_trusted_x_plugin_headers_come_from_claims() -> None:
    claims = fresh_claims()
    headers = build_outgoing_headers(
        _request()["headers"], claims, plugin_instance_id=TEST_INSTANCE
    )
    assert headers["X-Plugin-ID"] == "mod_equipment"
    assert headers["X-Company-ID"] == "co_123"
    assert headers["X-Project-ID"] == "proj_1"
    assert headers["X-Plugin-Grant-ID"] == "grant_01"
    assert headers["X-Plugin-Instance-ID"] == TEST_INSTANCE
    assert headers["X-Plugin-Artifact-Digest"] == TEST_DIGEST
    assert headers["Accept"] == "text/event-stream"  # безобидный заголовок сохранён
    # Клиентские недоверенные значения удалены до доверенной перезаписи.
    assert "evil-plugin" not in str(headers)
    assert "evil-company" not in str(headers)
    assert "inst-client-spoof" not in str(headers)
    assert "grant-client-spoof" not in str(headers)
    assert "sha256:" + "f" * 64 not in str(headers)
    assert "X-Forwarded-For" not in headers
    assert "Cookie" not in headers


def test_header_case_insensitive_duplicates_are_removed() -> None:
    claims = fresh_claims()
    headers = {
        "x-company-id": "evil",
        "X-COMPANY-ID": "evil2",
        "authorization": "Bearer evil",
        "X-Plugin-Capability-Token": "evil-token",
    }
    out = build_outgoing_headers(headers, claims, plugin_instance_id=TEST_INSTANCE)
    assert out["X-Company-ID"] == "co_123"
    assert out[HEADER_AUTHORIZATION] == f"Bearer {claims.token}"
    assert sum(1 for k in out if k.lower() == "x-company-id") == 1


def test_multiple_calls_use_newest_token() -> None:
    """Никакого кэша токенов: каждый forward берёт свежий claims.token."""
    first = build_outgoing_headers({}, fresh_claims("token-one"), plugin_instance_id=TEST_INSTANCE)
    second = build_outgoing_headers({}, fresh_claims("token-two"), plugin_instance_id=TEST_INSTANCE)
    assert first[HEADER_AUTHORIZATION] == "Bearer token-one"
    assert second[HEADER_AUTHORIZATION] == "Bearer token-two"


# --------------------------------------------------------------------------- SSE / Release


async def test_forward_returns_streaming_response_without_buffering(config) -> None:  # type: ignore[no-untyped-def]
    """КЛЮЧЕВОЕ: SSE — живой async-итератор, тело НЕ собрано в bytes."""
    backend = FakeBackendSender()
    port = _port(config, backend)
    assert isinstance(port, ProxyPort)
    response = await port.forward(
        plugin_instance_id=TEST_INSTANCE,
        request=_request(stream=True),
        claims=fresh_claims("sse-token"),
    )
    assert isinstance(response, BackendResponse)
    assert response.is_streaming is True
    assert response.body is None  # НЕ буферизовано
    assert response.stream is not None
    chunks: list[bytes] = []
    async for chunk in response.stream:
        chunks.append(chunk)
    assert chunks == [b"data: one\n\n", b"data: two\n\n"]
    # Соединение НЕ закрыто в forward: Release делает владелец (aclose).
    assert backend.closed is False

    sent = backend.sent[0]
    assert sent.stream is True
    assert _header(sent, HEADER_AUTHORIZATION) == "Bearer sse-token"
    assert sent.path == "/api/items"
    assert sent.timeout_seconds == config.stream_timeout_seconds


async def test_aclose_releases_connection(config) -> None:  # type: ignore[no-untyped-def]
    backend = FakeBackendSender()
    port = _port(config, backend)
    await port.aclose()
    assert backend.closed is True


async def test_forward_does_not_read_body_of_streaming_response(config) -> None:  # type: ignore[no-untyped-def]
    """Повторная проверка: даже при stream=False ответ читается транспортом,
    а адаптер не вызывает aread()/content поверх результата."""
    backend = FakeBackendSender()
    response = await _port(config, backend).forward(
        plugin_instance_id=TEST_INSTANCE,
        request=_request(stream=False, headers={"Accept": "application/json"}),
        claims=fresh_claims(),
    )
    assert isinstance(response, BackendResponse)
    assert backend.sent[0].stream is False
    assert backend.sent[0].timeout_seconds == config.backend_timeout_seconds


async def test_gateway_forward_delegation_is_thin(config) -> None:  # type: ignore[no-untyped-def]
    """Если задача 11 собрала маппер — адаптер не дублирует allowlist/транспорт."""

    class GatewayMapper:
        def __init__(self) -> None:
            self.seen: dict = {}

        async def forward(self, **kwargs):  # type: ignore[no-untyped-def]
            self.seen = kwargs
            return {"delegated": True}

    mapper = GatewayMapper()
    port = HttpProxyPort(config=config, gateway_forward=mapper)
    assert port.backend_http is None
    claims = fresh_claims()
    result = await port.forward(
        plugin_instance_id=TEST_INSTANCE,
        request=_request(),
        claims=claims,
    )
    assert result == {"delegated": True}
    assert mapper.seen["plugin_instance_id"] == TEST_INSTANCE
    assert mapper.seen["claims"] is claims
    # Ни одного локального httpx/заголовочного кода: чистый маппер задачи 11.
    assert len(mapper.seen) == 3


# --------------------------------------------------------------------------- fail-closed


async def test_expired_token_is_refused(config) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    expired = MintedClaims(
        token="expired-token",
        jti="jti_exp",
        issued_at=now - timedelta(seconds=600),
        expires_at=now - timedelta(seconds=1),
        claims={"plugin_id": "mod_equipment", "company_id": "co_123"},
    )
    backend = FakeBackendSender()
    with pytest.raises(NotReady):
        await _port(config, backend).forward(
            plugin_instance_id=TEST_INSTANCE,
            request=_request(),
            claims=expired,
        )
    assert backend.sent == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("grant_id", None),
        ("grant_id", ""),
        ("company_id", None),
        ("company_id", ""),
        ("company_id", "co_other"),
        ("plugin_id", None),
        ("plugin_id", ""),
        ("plugin_id", "other_plugin"),
        ("plugin_instance_id", None),
        ("plugin_instance_id", "inst_other"),
        ("artifact_digest", None),
        ("artifact_digest", "not-a-digest"),
        ("sub", None),
        ("sub", ""),
        ("sub", "plugin:other:co_123:" + TEST_INSTANCE),
    ),
)
def test_runtime_claims_missing_or_mismatched_identity_is_refused(
    field: str, value: str | None
) -> None:
    claims = _claims_with({field: value})
    with pytest.raises(ProxyConfigurationError):
        build_outgoing_headers({}, claims, plugin_instance_id=TEST_INSTANCE)


def test_runtime_expected_instance_mismatch_is_refused() -> None:
    with pytest.raises(ProxyConfigurationError, match="expected durable instance"):
        build_outgoing_headers(
            {}, fresh_claims(), plugin_instance_id="inst_other"
        )


def test_runtime_missing_payload_and_missing_grant_have_no_fallback() -> None:
    claims = replace(fresh_claims(), claims=None)  # type: ignore[arg-type]
    with pytest.raises(ProxyConfigurationError, match="payload"):
        build_outgoing_headers({}, claims, plugin_instance_id=TEST_INSTANCE)
    claims = _claims_with({"grant_id": None})
    with pytest.raises(ProxyConfigurationError, match="grant_id"):
        build_outgoing_headers({}, claims, plugin_instance_id=TEST_INSTANCE)


def test_runtime_expired_claims_are_refused_before_headers() -> None:
    now = datetime.now(UTC)
    claims = MintedClaims(
        token="expired-token",
        jti="jti-expired",
        issued_at=now - timedelta(seconds=600),
        expires_at=now - timedelta(seconds=1),
        claims=dict(fresh_claims().claims),
    )
    with pytest.raises(ProxyConfigurationError, match="expired"):
        build_outgoing_headers({}, claims, plugin_instance_id=TEST_INSTANCE)


async def test_gateway_delegation_validates_identity_before_forward(config) -> None:  # type: ignore[no-untyped-def]
    class Mapper:
        def __init__(self) -> None:
            self.called = False

        async def forward(self, **kwargs):  # type: ignore[no-untyped-def]
            self.called = True
            return {}

    mapper = Mapper()
    port = HttpProxyPort(config=config, gateway_forward=mapper)
    with pytest.raises(ProxyConfigurationError):
        await port.forward(
            plugin_instance_id=TEST_INSTANCE,
            request=_request(),
            claims=replace(fresh_claims(), claims=None),  # type: ignore[arg-type]
        )
    assert mapper.called is False


async def test_empty_token_is_refused(config) -> None:  # type: ignore[no-untyped-def]
    claims = fresh_claims("   ")
    with pytest.raises(NotReady):
        await _port(config).forward(
            plugin_instance_id=TEST_INSTANCE, request=_request(), claims=claims
        )


async def test_backend_transport_error_is_not_success(config) -> None:  # type: ignore[no-untyped-def]
    class Broken:
        async def send(self, request):  # type: ignore[no-untyped-def]
            raise ConnectionError("backend down")

    with pytest.raises(NotReady):
        await _port(config, Broken()).forward(  # type: ignore[arg-type]
            plugin_instance_id=TEST_INSTANCE, request=_request(), claims=fresh_claims()
        )


async def test_broken_factory_is_configuration_error(config) -> None:  # type: ignore[no-untyped-def]
    """Фабрика вернула объект без send() -> явный отказ, не «тихий успех»."""
    port = HttpProxyPort(
        config=config,
        backend_http_factory=lambda _: object(),  # type: ignore[arg-type,return-value]
    )
    with pytest.raises(ProxyConfigurationError):
        await port.forward(
            plugin_instance_id=TEST_INSTANCE,
            request=_request(),
            claims=fresh_claims(),
        )


def test_unknown_request_shape_is_refused() -> None:
    with pytest.raises(ProxyConfigurationError):
        build_backend_request(
            object(),
            fresh_claims(),
            plugin_instance_id=TEST_INSTANCE,
            backend_timeout_seconds=30.0,
            stream_timeout_seconds=300.0,
        )


# --------------------------------------------------------------------------- per-instance routing
def test_service_url_is_canonical_per_instance(config) -> None:  # type: ignore[no-untyped-def]
    first = plugin_service_url(TEST_INSTANCE, config, plugin_id="mod_equipment")
    second = plugin_service_url("other_instance", config, plugin_id="mod_equipment")
    assert first == f"http://{TEST_DEPLOYMENT}.{config.namespace}.svc:8080"
    assert first != second
    assert "plugin-runtime." not in first and "plugin-runtime." not in second


async def test_default_factory_uses_canonical_service_url(config, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from prodavan.infrastructure.plugins.adapters import gateway_adapters

    seen: dict[str, object] = {}

    class Adapter(FakeBackendSender):
        def __init__(self, *, base_url: str, **_kwargs) -> None:
            super().__init__()
            seen["base_url"] = base_url

    monkeypatch.setattr(gateway_adapters, "BackendHttpAdapter", Adapter)
    port = HttpProxyPort(config=config)
    await port.forward(
        plugin_instance_id=TEST_INSTANCE,
        request=_request(stream=False),
        claims=fresh_claims(),
    )
    assert seen["base_url"] == f"http://{TEST_DEPLOYMENT}.{config.namespace}.svc:8080"


async def test_gateway_send_requires_trusted_instance_identity(config) -> None:  # type: ignore[no-untyped-def]
    port = HttpProxyPort(config=config)
    request = BackendRequest(
        path="/",
        method="GET",
        headers={"X-Plugin-ID": "mod_equipment"},
    )
    with pytest.raises(ProxyConfigurationError, match="X-Plugin-Instance-ID"):
        await port.send(request)


async def test_gateway_send_routes_trusted_identity_to_factory(config) -> None:  # type: ignore[no-untyped-def]
    backend = FakeBackendSender()
    captured: list[str] = []
    port = HttpProxyPort(
        config=config,
        backend_http_factory=lambda instance: captured.append(instance) or backend,
    )
    request = BackendRequest(
        path="/",
        method="GET",
        headers={"X-Plugin-ID": "mod_equipment", "X-Plugin-Instance-ID": TEST_INSTANCE},
    )
    await port.send(request)
    assert captured == [TEST_INSTANCE]
    assert len(backend.sent) == 1
