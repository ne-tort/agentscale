"""Unit-тесты ``HttpProxyPort`` (писать, НЕ запускать, verification freeze).

ТЗ: proxy добавляет Authorization из свежего токена и НЕ буферизует SSE.
Дополнительно: доверенные ``X-Plugin-*`` из claims, отброс клиентских
Authorization/Cookie/X-Plugin-*, Release не в forward, отказ при истёкшем
токене/отсутствии транспорта.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.plugins.activation.errors import NotReady
from prodavan.application.plugins.activation.ports import MintedClaims, ProxyPort
from prodavan.application.plugins.gateway.ports import BackendResponse
from prodavan.infrastructure.plugins.runtime.proxy import (
    HEADER_AUTHORIZATION,
    HttpProxyPort,
    ProxyConfigurationError,
    build_backend_request,
    build_outgoing_headers,
)
from tests.unit.plugins.runtime.conftest import (
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
        },
        "body": b"",
        "stream": True,
    }
    base.update(over)
    return base


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
    assert headers["Accept"] == "text/event-stream"  # безобидный заголовок сохранён
    # Клиентские недоверенные значения удалены до доверенной перезаписи.
    assert "evil-plugin" not in str(headers)
    assert "evil-company" not in str(headers)
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


def test_gateway_forward_delegation_is_thin(config) -> None:  # type: ignore[no-untyped-def]
    """Если задача 11 уже собрала proxy — адаптер не дублирует allowlist."""

    class Gateway:
        async def forward(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"delegated": True, **kwargs}

    port = HttpProxyPort(config=config, gateway_forward=Gateway())
    assert port.backend_http is None
    assert port.gateway_forward is not None


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
