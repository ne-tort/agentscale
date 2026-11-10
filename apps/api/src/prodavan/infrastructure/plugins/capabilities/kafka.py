"""Secure Kafka configuration and replay-safe subscription lifecycle."""
from __future__ import annotations

import asyncio
import inspect
import os
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from prodavan.application.plugins.capabilities.contracts import EventsStartOffset
from prodavan.application.plugins.events.contracts import MAX_DELIVERY_ATTEMPTS, Subscription
from prodavan.infrastructure.plugins.adapters.kafka_adapters import (
    AiokafkaConsumerAdapter, AiokafkaProducerAdapter, KafkaTopicAdminAdapter,
)

ENV_USERNAME_REF = "PLUGIN_KAFKA_SASL_USERNAME_REF"
ENV_PASSWORD_REF = "PLUGIN_KAFKA_SASL_PASSWORD_REF"
ENV_CA_FILE = "PLUGIN_KAFKA_TLS_CA_FILE"


class KafkaSecurityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KafkaSecurityConfig:
    sasl_username: str = field(repr=False)
    sasl_password: str = field(repr=False)
    ssl_context: ssl.SSLContext = field(repr=False, compare=False)
    security_protocol: str = "SASL_SSL"
    sasl_mechanism: str = "SCRAM-SHA-512"
    request_timeout_ms: int = 15000

    def __post_init__(self) -> None:
        if self.security_protocol != "SASL_SSL":
            raise KafkaSecurityError("plugin Kafka requires SASL_SSL")
        if self.sasl_mechanism not in ("SCRAM-SHA-256", "SCRAM-SHA-512"):
            raise KafkaSecurityError("plugin Kafka allows SCRAM-SHA-256/512 only")
        if not self.sasl_username or not self.sasl_password:
            raise KafkaSecurityError("plugin Kafka SASL credentials are empty")
        if (
            not isinstance(self.ssl_context, ssl.SSLContext)
            or self.ssl_context.verify_mode != ssl.CERT_REQUIRED
            or not self.ssl_context.check_hostname
        ):
            raise KafkaSecurityError("plugin Kafka TLS requires CA and hostname verification")
        if not 1000 <= self.request_timeout_ms <= 120000:
            raise KafkaSecurityError("plugin Kafka request timeout is invalid")

    @classmethod
    def from_secret_refs(
        cls, *, secret_store: Any, env: Mapping[str, str] | None = None
    ) -> "KafkaSecurityConfig":
        source = os.environ if env is None else env

        def resolve(name: str) -> str:
            ref = str(source.get(name, "") or "").strip()
            if (
                not ref.startswith(("vault://", "file://"))
                or len(ref) > 2048
                or any(ord(char) < 32 or ord(char) == 127 for char in ref)
            ):
                raise KafkaSecurityError(f"{name} must contain a SecretStore reference")
            try:
                value = secret_store.get(ref)
            except Exception as exc:
                raise KafkaSecurityError(
                    f"{name} resolution failed ({type(exc).__name__})"
                ) from None
            if not isinstance(value, str) or not value:
                raise KafkaSecurityError(f"{name} resolved an invalid credential")
            return value

        ca_file = str(source.get(ENV_CA_FILE, "") or "").strip()
        if not ca_file or not os.path.isfile(ca_file):
            raise KafkaSecurityError("PLUGIN_KAFKA_TLS_CA_FILE must name an existing CA file")
        try:
            context = ssl.create_default_context(cafile=ca_file)
        except (OSError, ssl.SSLError):
            raise KafkaSecurityError("plugin Kafka CA could not be loaded") from None
        mechanism = str(source.get("PLUGIN_KAFKA_SASL_MECHANISM", "SCRAM-SHA-512") or "")
        return cls(
            sasl_username=resolve(ENV_USERNAME_REF),
            sasl_password=resolve(ENV_PASSWORD_REF),
            ssl_context=context,
            sasl_mechanism=mechanism,
        )

    def as_aiokafka_kwargs(self) -> dict[str, Any]:
        return {
            "security_protocol": self.security_protocol,
            "sasl_mechanism": self.sasl_mechanism,
            "sasl_plain_username": self.sasl_username,
            "sasl_plain_password": self.sasl_password,
            "ssl_context": self.ssl_context,
            "request_timeout_ms": self.request_timeout_ms,
        }


class KafkaEventResourceManager:
    def __init__(self, *, bootstrap_servers: str, security: KafkaSecurityConfig) -> None:
        if not isinstance(security, KafkaSecurityConfig):
            raise KafkaSecurityError("secure Kafka config is required")
        if not isinstance(bootstrap_servers, str) or not bootstrap_servers.strip():
            raise KafkaSecurityError("Kafka bootstrap servers are required")
        self._bootstrap = bootstrap_servers.strip()
        self.producer = AiokafkaProducerAdapter(
            bootstrap_servers=self._bootstrap, security=security
        )
        self.topic_admin = KafkaTopicAdminAdapter(
            bootstrap_servers=self._bootstrap, security=security
        )
        self._started = False
        self._closed = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._closed:
            raise KafkaSecurityError("Kafka resource manager is closed")
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            if self._closed:
                raise KafkaSecurityError("Kafka resource manager is closed")
            await self.topic_admin.ensure_plugin_topics()
            await self.producer.start()
            self._started = True

    async def publish(self, record: Any) -> None:
        if not self._started or self.producer._producer is None:
            raise KafkaSecurityError("Kafka DLQ producer is unavailable")
        from prodavan.infrastructure.plugins.adapters.kafka_adapters import KafkaDlqAdapter
        await KafkaDlqAdapter(producer=self.producer._producer).publish(record)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.producer.stop()
        finally:
            await self.topic_admin.close()


class KafkaRawDlqAdapter:
    """Raw-record DLQ with digest-only payloads and Redis natural-key dedup."""

    def __init__(self, *, resources: KafkaEventResourceManager, redis: Any, ttl_seconds: int = 604800) -> None:
        if redis is None or not all(callable(getattr(redis, method, None)) for method in ("get", "set")):
            raise KafkaSecurityError("raw DLQ idempotency requires Redis")
        self._resources = resources
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(record: Any) -> str:
        import hashlib

        raw = "|".join((
            str(record.topic), str(record.partition), str(record.offset), str(record.consumer_group)
        ))
        return "plugin:events:raw-dlq:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def publish(self, record: Any) -> bool:
        from prodavan.application.plugins.events.topics import dlq_topic
        from prodavan.application.plugins.capabilities.adapters._shared import canonical_json

        key = self._key(record)
        if await self._redis.get(key) is not None:
            return False
        if not self._resources._started or self._resources.producer._producer is None:
            raise KafkaSecurityError("raw DLQ producer is not started")
        wire = record.to_wire()
        try:
            await self._resources.producer._producer.send_and_wait(
                dlq_topic(record.topic),
                key=str(record.consumer_group).encode("utf-8"),
                value=canonical_json(wire),
            )
            await self._resources.producer._producer.flush()
        except Exception:
            raise KafkaSecurityError("raw DLQ publish failed") from None
        # A false result is an acknowledged concurrent duplicate. Kafka's
        # idempotent producer plus this natural key prevents retry multiplication
        # during normal operation; the source offset is committed only afterwards.
        created = await self._redis.set(key, "1", nx=True, ex=self._ttl)
        if not isinstance(created, (bool, int)):
            raise KafkaSecurityError("raw DLQ dedup marker was not confirmed")
        return bool(created)


class KafkaSubscriptionRuntime:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        security: KafkaSecurityConfig,
        dispatcher_factory: Callable[[Any, Subscription], Any],
        poll_timeout_ms: int = 1000,
    ) -> None:
        if not isinstance(security, KafkaSecurityConfig):
            raise KafkaSecurityError("subscription runtime requires secure Kafka config")
        if not callable(dispatcher_factory):
            raise KafkaSecurityError("subscription dispatcher_factory is required")
        self._bootstrap = bootstrap_servers.strip()
        self._security = security
        self._dispatcher_factory = dispatcher_factory
        self._poll_timeout_ms = poll_timeout_ms
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._consumers: dict[str, Any] = {}
        self._settings: dict[str, dict[str, Any]] = {}
        self._closed = False

    async def _run(self, subscription: Subscription, dispatcher: Any) -> None:
        failures = 0
        try:
            while True:
                try:
                    outcome = await dispatcher.consume(subscription)
                    failures = 0 if outcome is not None else failures
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures += 1
                    await asyncio.sleep(min(5.0, 0.1 * (2 ** min(failures, 6))))
        except asyncio.CancelledError:
            return

    async def start(
        self,
        *,
        subscription: Subscription,
        start_offset: EventsStartOffset,
        max_retries: int,
    ) -> Mapping[str, Any]:
        if self._closed:
            raise KafkaSecurityError("subscription runtime is closed")
        if start_offset is not EventsStartOffset.EARLIEST or max_retries != MAX_DELIVERY_ATTEMPTS:
            raise KafkaSecurityError("subscription replay policy is invalid")
        existing = self._settings.get(subscription.id)
        if existing is not None:
            return dict(existing)
        consumer = AiokafkaConsumerAdapter(
            bootstrap_servers=self._bootstrap,
            subscription=subscription,
            security=self._security,
        )
        try:
            await consumer.start()
            dispatcher = self._dispatcher_factory(consumer, subscription)
            if not callable(getattr(dispatcher, "consume", None)):
                raise KafkaSecurityError("dispatcher_factory returned an invalid dispatcher")
            task = asyncio.create_task(
                self._run(subscription, dispatcher),
                name=f"plugin-events-{subscription.id}",
            )
        except Exception:
            await consumer.close()
            raise
        self._consumers[subscription.id] = consumer
        self._tasks[subscription.id] = task
        settings = {
            **consumer.settings,
            "subscription_id": subscription.id,
            "consumer_group": subscription.consumer_group,
            "enable_auto_commit": False,
            "auto_offset_reset": "earliest",
            "manual_commit": True,
            "max_retries": MAX_DELIVERY_ATTEMPTS,
        }
        self._settings[subscription.id] = settings
        return dict(settings)

    async def stop(self, subscription_id: str) -> None:
        task = self._tasks.pop(subscription_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        consumer = self._consumers.pop(subscription_id, None)
        if consumer is not None:
            await consumer.close()
        self._settings.pop(subscription_id, None)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for consumer in list(self._consumers.values()):
            close = getattr(consumer, "close", None)
            if callable(close):
                await close()
        self._tasks.clear()
        self._consumers.clear()
        self._settings.clear()


__all__ = [
    "ENV_CA_FILE", "ENV_PASSWORD_REF", "ENV_USERNAME_REF",
    "KafkaEventResourceManager", "KafkaRawDlqAdapter", "KafkaSecurityConfig", "KafkaSecurityError",
    "KafkaSubscriptionRuntime",
]
