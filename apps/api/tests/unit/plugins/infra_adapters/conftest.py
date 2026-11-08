"""Тестовые двойники для инфраструктурных адаптеров (БЕЗ сети/БД/Redis/k3s).

Все фейки детерминированы и НЕ подсказывают адаптеру «успех по умолчанию»:
это позволяет честно проверять fail-closed поведение. Тесты НЕ запускаются
в рамках миграции (verification freeze), но полностью самодостаточны.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from prodavan.application.plugins.events.contracts import (
    PLUGIN_EVENT_SCHEMA_VERSION,
    IdempotencyKey,
    PluginEvent,
    Subscription,
)
from prodavan.application.plugins.gateway.ports import (
    BackendRequest,
    BackendResponse,
    GatewaySession,
    WakeLease,
)

TEST_DIGEST = "sha256:" + ("a1b2c3d4" * 8)
WEBHOOK_SECRET = "unit-secret-never-in-db"
WEBHOOK_SECRET_OLD = "unit-secret-rotated-away"


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Redis fake: SET NX с ОТПЕЧАТКОМ первой доставки + INCR/EXPIRE
# ---------------------------------------------------------------------------


class FakeRedis:
    """Минимальная эмуляция Redis, достаточная для RateLimitPort."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.counters: dict[str, int] = {}
        self.fail = fail
        self.set_calls: list[dict[str, Any]] = []

    async def eval(self, script: str, keys_count: int, key: str, ttl_ms: Any) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        self.counters[key] = self.counters.get(key, 0) + 1
        if self.counters[key] == 1:
            self.ttl[key] = int(ttl_ms)
        return self.counters[key]

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if self.fail:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttl[key] = int(ex)
        return True

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("redis down")
        return self.store.get(key)


# ---------------------------------------------------------------------------
# Secret store / refs fakes
# ---------------------------------------------------------------------------


class FakeSecretStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.get_calls: list[str] = []

    def get(self, ref: str) -> str:
        self.get_calls.append(ref)
        if ref not in self.values:
            raise KeyError(ref)
        return self.values[ref]


# ---------------------------------------------------------------------------
# Session / Directory fakes
# ---------------------------------------------------------------------------


@dataclass
class FakePrincipal:
    sub: str = "oidc-sub-1"


class FakeJwtValidator:
    def __init__(self, *, tokens: dict[str, str] | None = None, fail: bool = False) -> None:
        self.tokens = tokens or {"good-jwt": "oidc-sub-1"}
        self.fail = fail
        self.calls: list[str] = []

    def validate(self, token: str) -> FakePrincipal:
        self.calls.append(token)
        if self.fail or token not in self.tokens:
            raise RuntimeError("invalid token")
        return FakePrincipal(sub=self.tokens[token])


class FakeDirectory:
    def __init__(
        self,
        *,
        subjects: dict[str, str] | None = None,
        memberships: dict[str, set[str]] | None = None,
        fail: bool = False,
    ) -> None:
        self.subjects = subjects or {"oidc-sub-1": "emp-1"}
        self.memberships = memberships or {"emp-1": {"co-123"}}
        self.fail = fail

    async def employee_by_subject(self, sub: str) -> str | None:
        if self.fail:
            raise RuntimeError("db down")
        return self.subjects.get(sub)

    async def company_ids_for_employee(self, employee_id: str) -> frozenset[str]:
        if self.fail:
            raise RuntimeError("db down")
        return frozenset(self.memberships.get(employee_id, set()))


class FakeCookieSessions:
    def __init__(self, mapping: dict[str, GatewaySession] | None = None) -> None:
        self.mapping = dict(mapping or {})

    async def resolve(self, token: str) -> GatewaySession | None:
        return self.mapping.get(token)


# ---------------------------------------------------------------------------
# Activation (задача 09) fakes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeWakeResult:
    token: str
    jti: str = "jti-1"
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) + timedelta(seconds=300)
    )


class FakeActivator:
    def __init__(self, *, token: str = "fresh-wake-token", fail: bool = False) -> None:
        self.token = token
        self.fail = fail
        self.wake_calls: list[tuple[str, float | None]] = []

    async def wake(self, instance_id: str, *, timeout: float | None = None) -> FakeWakeResult:
        self.wake_calls.append((instance_id, timeout))
        if self.fail:
            raise RuntimeError("scale unavailable")
        return FakeWakeResult(token=self.token)


class ReadyProbe:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[str] = []

    async def is_ready(self, plugin_instance_id: str) -> bool:
        self.calls.append(plugin_instance_id)
        return self.ready


# ---------------------------------------------------------------------------
# httpx fake: НЕБУФЕРИЗОВАННЫЙ SSE + запись исходящих заголовков
# ---------------------------------------------------------------------------


class FakeHttpxResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        body: bytes = b"ok",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/event-stream"}
        self._chunks = chunks
        self._body = body
        self.closed = False
        self.read_calls = 0

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        # Генератор: чанки отдаются по одному, НЕ собираются в bytes целиком.
        for chunk in (self._chunks or []):
            yield chunk

    async def aread(self) -> bytes:
        self.read_calls += 1
        return self._body

    async def aclose(self) -> None:
        self.closed = True


class FakeHttpxClient:
    def __init__(self, response: FakeHttpxResponse | None = None) -> None:
        self.response = response or FakeHttpxResponse()
        self.requests: list[dict[str, Any]] = []
        self.send_kwargs: list[dict[str, Any]] = []
        self.closed = False

    def build_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
        timeout: float,
    ) -> dict[str, Any]:
        built = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "content": content,
            "timeout": timeout,
        }
        self.requests.append(built)
        return built

    async def send(self, built: dict[str, Any], *, stream: bool) -> FakeHttpxResponse:
        self.send_kwargs.append({"stream": stream})
        return self.response

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Kafka fakes
# ---------------------------------------------------------------------------


class FakeKafkaProducer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict[str, Any]] = []
        self.flushes: int = 0
        self.started = True
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def send_and_wait(self, topic: str, *, key: bytes, value: bytes, headers: Any = None) -> None:
        if self.fail:
            raise RuntimeError("broker down")
        self.sent.append({"topic": topic, "key": key, "value": value, "headers": headers})

    async def flush(self) -> None:
        if self.fail:
            raise RuntimeError("broker down")
        self.flushes += 1

    async def stop(self) -> None:
        self.stopped = True


@dataclass
class FakeConsumerRecord:
    value: bytes
    topic: str = "agentscale.plugin.events"
    partition: int = 0
    offset: int = 17
    key: bytes | None = b"co_42"


class FakeKafkaConsumer:
    def __init__(self, records: list[FakeConsumerRecord] | None = None, *, fail_commit: bool = False) -> None:
        self._records = list(records or [])
        self.fail_commit = fail_commit
        self.commits: list[dict[Any, Any]] = []
        self.seeks: list[tuple[Any, int]] = []
        self.polls: int = 0
        self.started = True
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def getone(self, *, timeout_ms: int = 1000) -> FakeConsumerRecord | None:
        self.polls += 1
        if not self._records:
            return None
        return self._records.pop(0)

    def assignment(self) -> list[Any]:
        return [("agentscale.plugin.events", 0)]

    async def commit(self, assignment: dict[Any, Any]) -> None:
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.commits.append(assignment)

    def seek(self, tp: Any, offset: int) -> None:
        self.seeks.append((tp, offset))

    async def stop(self) -> None:
        self.stopped = True


@dataclass(frozen=True, slots=True)
class FakeNewTopic:
    """Двойник ``aiokafka.admin.NewTopic``."""

    name: str
    num_partitions: int = 1
    replication_factor: int = 1


class FakeKafkaAdmin:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.existing: set[str] = set()
        self.created: list[tuple[str, int, int]] = []
        self.fail_create = fail_create
        self.started = True
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def list_topics(self) -> list[str]:
        return sorted(self.existing)

    async def create_topics(self, topics: list[Any]) -> None:
        if self.fail_create:
            raise RuntimeError("admin unavailable")
        for topic in topics:
            self.created.append((topic.name, topic.num_partitions, topic.replication_factor))
            self.existing.add(topic.name)

    # Тот же объект можно передать и КАК TopicAdminPort (ensure_topic задачи 10),
    # и КАК aiokafka-admin клиент для KafkaTopicAdminAdapter.
    async def topic_exists(self, topic: str) -> bool:
        return topic in self.existing

    async def create_topic(
        self, topic: str, *, num_partitions: int, replication_factor: int
    ) -> None:
        if self.fail_create:
            raise RuntimeError("admin unavailable")
        self.created.append((topic, num_partitions, replication_factor))
        self.existing.add(topic)

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeAdminModule:
    """Подмена модуля ``aiokafka.admin`` для тестов без aiokafka."""

    NewTopic: Any = FakeNewTopic


# ---------------------------------------------------------------------------
# Event builders (задача 10)
# ---------------------------------------------------------------------------


def make_event(**kwargs: Any) -> PluginEvent:
    defaults: dict[str, Any] = dict(
        event_id="evt-1",
        event_type="offer.created",
        source="offers",
        company_id="co_42",
        subscription_id="sub_1",
        plugin_id="mod_equipment",
        occurred_at=datetime(2026, 11, 8, 6, 0, tzinfo=UTC),
        payload_digest=TEST_DIGEST,
        run_id="run_77",
        correlation_id="corr_77",
        schema_version=PLUGIN_EVENT_SCHEMA_VERSION,
    )
    defaults.update(kwargs)
    return PluginEvent(**defaults)


def make_subscription(**kwargs: Any) -> Subscription:
    defaults: dict[str, Any] = dict(
        id="sub_1",
        plugin_id="mod_equipment",
        event_types=("offer.created",),
        consumer_group="plugin-mod_equipment-sub_1",
    )
    defaults.update(kwargs)
    return Subscription(**defaults)


def make_backend_request(**kwargs: Any) -> BackendRequest:
    defaults: dict[str, Any] = dict(
        path="/events/handler",
        method="POST",
        headers={
            "X-Plugin-ID": "mod_equipment",
            "X-Company-ID": "co-123",
            "X-Plugin-Capability-Token": "fresh-wake-token",
            # Клиентские значения: адаптер ОБЯЗАН их выкинуть/перезаписать.
            "Authorization": "Bearer client-forged",
            "Cookie": "session=client",
        },
        body=b"{}",
        timeout_seconds=30.0,
        stream=False,
    )
    defaults.update(kwargs)
    return BackendRequest(**defaults)


__all__ = [
    "TEST_DIGEST",
    "WEBHOOK_SECRET",
    "WEBHOOK_SECRET_OLD",
    "BackendResponse",
    "FakeActivator",
    "FakeAdminModule",
    "FakeConsumerRecord",
    "FakeNewTopic",
    "FakeCookieSessions",
    "FakeDirectory",
    "FakeHttpxClient",
    "FakeHttpxResponse",
    "FakeJwtValidator",
    "FakeKafkaAdmin",
    "FakeKafkaConsumer",
    "FakeKafkaProducer",
    "FakePrincipal",
    "FakeRedis",
    "FakeSecretStore",
    "FakeWakeResult",
    "IdempotencyKey",
    "ReadyProbe",
    "make_backend_request",
    "make_event",
    "make_subscription",
    "signature",
]
