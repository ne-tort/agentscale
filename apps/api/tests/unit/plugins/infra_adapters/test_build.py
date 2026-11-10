"""Тесты fail-closed композиции (build.py) — ПИШЕМ, НЕ ЗАПУСКАЕМ.

Главное: отсутствие ЛЮБОГО порта падает на СТАРТЕ, а не «деградирует до
разрешения» и не откладывает ошибку до первого запроса.
"""

from __future__ import annotations

from typing import Any

import pytest

from prodavan.application.plugins.events.contracts import ConsumerAssignment
from prodavan.application.plugins.gateway.auth import GatewayLimits
from prodavan.infrastructure.plugins.adapters.build import (
    AdapterCompositionError,
    build_event_dispatcher,
    build_gateway_runtime,
)
from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
    ActivationAdapter,
    AuditAdapter,
    BackendHttpAdapter,
    GrantAdapter,
    InMemoryGrantStore,
    InMemoryWebhookSecretRefs,
    MappingInstanceResolver,
    RedisRateLimitAdapter,
    SecretAdapter,
    SecretKeyRing,
    SessionAdapter,
    Sha256HmacAdapter,
)
from prodavan.infrastructure.plugins.adapters.stores import in_memory_event_ports

from .conftest import (
    WEBHOOK_SECRET,
    FakeActivator,
    FakeDirectory,
    FakeHttpxClient,
    FakeJwtValidator,
    FakeRedis,
    FakeSecretStore,
    make_subscription,
)


# ---------------------------------------------------------------------------
# Полный «счастливый» набор портов
# ---------------------------------------------------------------------------


class _Sink:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


def _gateway_ports() -> dict[str, Any]:
    refs = InMemoryWebhookSecretRefs(
        {("mod_equipment", "co-123"): SecretKeyRing(active_ref="ref-1")}
    )
    sink = _Sink()
    return {
        "sessions": SessionAdapter(
            validator=FakeJwtValidator(), directory=FakeDirectory()
        ),
        "grants": GrantAdapter(InMemoryGrantStore()),
        "secrets": SecretAdapter(
            refs=refs, secret_reader=FakeSecretStore({"ref-1": WEBHOOK_SECRET}).get
        ),
        "rate_limit": RedisRateLimitAdapter(client=FakeRedis()),
        "audit": AuditAdapter(sink),
        "hmac_verifier": Sha256HmacAdapter(),
        "activation": ActivationAdapter(
            activator=FakeActivator(),
            instance_resolver=MappingInstanceResolver(
                {("mod_equipment", "co-123"): "inst_abc"}
            ),
        ),
        "backend_http": BackendHttpAdapter(
            base_url="http://plugin:8080", client=FakeHttpxClient(), inject_trusted_bearer=False
        ),
        "static_http": BackendHttpAdapter(
            base_url="http://plugin-static:8080", client=FakeHttpxClient()
        ),
        "limits": GatewayLimits(),
        "shell_origin": "https://agentscale.example.test",
    }


class _RawDlqPort:
    async def publish(self, record: Any) -> bool:
        return True


class _ClaimsPort:
    async def acquire(self, key: Any, *, lease_seconds: float) -> Any:
        return None

    async def renew(self, claim: Any) -> bool:
        return True

    async def complete(self, claim: Any) -> bool:
        return True

    async def release(self, claim: Any) -> bool:
        return True


def _event_ports() -> dict[str, Any]:
    ports = in_memory_event_ports(
        activator=FakeActivator(),
        instance_mapping={("mod_equipment", "co_42"): "inst_abc"},
    )
    ports.subscriptions.register(make_subscription(), active=True)

    class _Handler:
        async def handle(self, event: Any, *, subscription: Any) -> None:
            return None

    return {
        "producer": _ProducerPort(),
        "consumer": _ConsumerPort(),
        "topic_admin": _TopicAdminPort(),
        "dlq": _DlqPort(),
        "raw_dlq": _RawDlqPort(),
        "subscriptions": ports.subscriptions,
        "published_events": ports.published_events,
        "idempotency": ports.idempotency,
        "delivery_claims": _ClaimsPort(),
        "audit": ports.audit,  # InMemoryAudit: append() событий + write() аудита
        "handler": _Handler(),
        "wake_hook": ports.wake_hook,
    }


# ---------------------------------------------------------------------------
# build_gateway_runtime: fail-closed при отсутствии ЛЮБОГО порта
# ---------------------------------------------------------------------------


def test_build_gateway_runtime_succeeds_with_all_ports() -> None:
    runtime = build_gateway_runtime(**_gateway_ports())
    assert runtime.auth is not None
    assert runtime.proxy is not None
    # ЯВНАЯ обёртка: доверенный Bearer будет добавлен при проксировании.
    from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
        BearerAuthorizationInjector,
    )

    assert isinstance(runtime.proxy.backend_http, BearerAuthorizationInjector)


@pytest.mark.parametrize(
    "omitted",
    [
        "sessions",
        "grants",
        "secrets",
        "rate_limit",
        "audit",
        "hmac_verifier",
        "activation",
        "backend_http",
        "static_http",
        "limits",
        "shell_origin",
    ],
)
def test_build_gateway_runtime_fails_without_any_port(omitted: str) -> None:
    """КАЖДЫЙ порт обязателен: None → AdapterCompositionError на старте."""
    ports = _gateway_ports()
    ports[omitted] = None
    with pytest.raises(AdapterCompositionError) as exc:
        build_gateway_runtime(**ports)
    assert exc.value.missing, "должен быть назван отсутствующий порт"


def test_build_gateway_runtime_rejects_broken_adapter_shape() -> None:
    """Объект есть, а метода нет — та же ошибка композиции, что и None."""
    ports = _gateway_ports()
    ports["grants"] = object()  # нет active_grant/public_pages
    with pytest.raises(AdapterCompositionError):
        build_gateway_runtime(**ports)


def test_build_gateway_runtime_accepts_delivery_lease_contract_without_legacy_claim() -> None:
    class LeaseOnlyRateLimit:
        async def allow(self, **kwargs: Any) -> bool:
            return True

        async def claim_delivery_lease(self, **kwargs: Any) -> Any:
            return None

        async def commit_delivery(self, **kwargs: Any) -> bool:
            return True

        async def release_delivery(self, **kwargs: Any) -> None:
            return None

    ports = _gateway_ports()
    ports["rate_limit"] = LeaseOnlyRateLimit()
    runtime = build_gateway_runtime(**ports)
    assert runtime.auth.rate_limit is ports["rate_limit"]
    assert not hasattr(ports["rate_limit"], "claim_delivery")


def test_build_gateway_runtime_rejects_legacy_claim_only_rate_limit() -> None:
    class LegacyRateLimit:
        async def allow(self, **kwargs: Any) -> bool:
            return True

        async def claim_delivery(self, **kwargs: Any) -> Any:
            return None

    ports = _gateway_ports()
    ports["rate_limit"] = LegacyRateLimit()
    with pytest.raises(AdapterCompositionError, match="RateLimitPort"):
        build_gateway_runtime(**ports)


def test_build_gateway_runtime_can_disable_injector_explicitly() -> None:
    """Отключение инъекции — ЯВНОЕ решение интегратора (не молчаливый дефолт)."""
    ports = _gateway_ports()
    runtime = build_gateway_runtime(**ports, inject_trusted_bearer=False)
    from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
        BackendHttpAdapter,
        BearerAuthorizationInjector,
    )

    assert isinstance(runtime.proxy.backend_http, BackendHttpAdapter)
    assert not isinstance(runtime.proxy.backend_http, BearerAuthorizationInjector)


# ---------------------------------------------------------------------------
# build_event_dispatcher: fail-closed при отсутствии ЛЮБОГО из 10 портов
# ---------------------------------------------------------------------------


def test_build_event_dispatcher_succeeds_with_all_ports() -> None:
    dispatcher = build_event_dispatcher(**_event_ports())
    assert dispatcher.consumer_settings["auto_offset_reset"] == "earliest"
    assert dispatcher.consumer_settings["enable_auto_commit"] is False


@pytest.mark.parametrize(
    "omitted",
    [
        "producer",
        "consumer",
        "topic_admin",
        "dlq",
        "raw_dlq",
        "subscriptions",
        "published_events",
        "idempotency",
        "delivery_claims",
        "audit",
        "handler",
        "wake_hook",
    ],
)
def test_build_event_dispatcher_fails_without_any_port(omitted: str) -> None:
    ports = _event_ports()
    ports[omitted] = None
    with pytest.raises(AdapterCompositionError):
        build_event_dispatcher(**ports)


def test_build_event_dispatcher_rejects_broken_consumer() -> None:
    """ConsumerPort без commit/seek/poll отклоняется на старте."""
    ports = _event_ports()
    ports["consumer"] = object()
    with pytest.raises(AdapterCompositionError):
        build_event_dispatcher(**ports)


# ---------------------------------------------------------------------------
# Вспомогательные порты-двойники
# ---------------------------------------------------------------------------


class _ProducerPort:
    async def send(self, *, topic: str, key: str, value: dict[str, Any], headers: Any = None) -> None:
        return None

    async def flush(self) -> None:
        return None


class _ConsumerPort:
    def delivery_ownership(self, record: Any) -> ConsumerAssignment:
        return ConsumerAssignment(
            topic=record.topic,
            partition=record.partition,
            owned=True,
            offset=record.offset,
        )

    async def poll(self, *, timeout_ms: int = 1000) -> Any:
        return None

    async def commit(self, offset: int) -> None:
        return None

    async def seek(self, offset: int) -> None:
        return None

    async def close(self) -> None:
        return None


class _TopicAdminPort:
    async def topic_exists(self, topic: str) -> bool:
        return True

    async def create_topic(self, topic: str, *, num_partitions: int, replication_factor: int) -> None:
        return None


class _DlqPort:
    async def publish(self, record: Any) -> None:
        return None
