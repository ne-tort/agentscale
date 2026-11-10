"""Composition of the real-service acceptance harness.

Kafka, PostgreSQL, Redis and HTTP transports are real. No mock Kafka clients,
in-memory event stores, fake DLQ or fake proxy responses are installed. The only
service-scope fixture is the documented K8s-free wake fixture in backend.py.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient
from redis.asyncio import Redis

from prodavan.application.plugins.events.contracts import (
    DeliveryClaim,
    DeliveryOutcome,
    IdempotencyKey,
    PluginEvent,
    Subscription,
    canonical_digest,
)
from prodavan.application.plugins.events.dispatch import (
    ActivatingPluginEventHandler,
    HmacPluginEventSigner,
    PluginEventDispatcher,
)
from prodavan.infrastructure.plugins.adapters.kafka_adapters import (
    AiokafkaConsumerAdapter,
    AiokafkaProducerAdapter,
    KafkaDlqAdapter,
    KafkaTopicAdminAdapter,
)
from prodavan.infrastructure.plugins.adapters.redis_event_claims import RedisDeliveryClaimStore
from prodavan.infrastructure.plugins.capabilities.kafka import (
    ENV_CA_FILE,
    ENV_PASSWORD_REF,
    ENV_USERNAME_REF,
    KafkaRawDlqAdapter,
    KafkaSecurityConfig,
)

from .backend import AcceptanceBackend, TestOnlyWakeFixture, build_production_http_proxy
from .config import AcceptanceConfig
from .database import RealSqlAcceptance
from .resources import (
    AcceptanceIdentity,
    KafkaResourceCleanup,
    new_identity,
    prepare_random_topics,
)
from .secrets import SensitiveValues, TestOnlyFileSecretStore, assert_no_sensitive_text


class _RawDlqResources:
    """Resource-owner adapter for production raw DLQ with a random topic.

    KafkaEventResourceManager.start() creates the fixed production topic. This
    owner only exposes the already-started real producer to KafkaRawDlqAdapter;
    it does not emulate Kafka or return a fabricated DLQ result.
    """

    def __init__(self, producer: AiokafkaProducerAdapter) -> None:
        self.producer = producer
        self._started = producer._producer is not None


class TrackedRawDlq:
    """Observation/cleanup wrapper; all writes go to KafkaRawDlqAdapter."""

    def __init__(self, adapter: KafkaRawDlqAdapter, redis: Redis) -> None:
        self._adapter = adapter
        self._redis = redis
        self._keys: set[str] = set()

    async def publish(self, record: Any) -> bool:
        self._keys.add(self._adapter._key(record))
        result = await self._adapter.publish(record)
        if not isinstance(result, bool):
            raise RuntimeError("production raw DLQ returned a non-boolean acknowledgement")
        return result

    async def cleanup(self) -> None:
        if self._keys:
            keys = sorted(self._keys)
            await self._redis.delete(*keys)
            remaining = [key for key in keys if await self._redis.get(key) is not None]
            if remaining:
                raise RuntimeError("owned raw-DLQ Redis keys survived cleanup")
            self._keys.clear()


class TrackedDeliveryClaims:
    """Delegate production claims and track exact owned Redis triple keys."""

    def __init__(
        self,
        *,
        store: RedisDeliveryClaimStore,
        redis: Redis,
        sensitive: SensitiveValues,
    ) -> None:
        self._store = store
        self._redis = redis
        self._sensitive = sensitive
        self._owned_keys: set[str] = set()
        self._claims: dict[IdempotencyKey, DeliveryClaim] = {}
        self.acquire_count = 0
        self.complete_count = 0
        self.release_count = 0

    @property
    def owned_key_count(self) -> int:
        return len(self._owned_keys)

    def keys_for(self, key: IdempotencyKey) -> tuple[str, str, str]:
        return self._store._keys(key)

    def _track(self, key: IdempotencyKey) -> None:
        self._owned_keys.update(self._store._keys(key))

    def _protect(self, claim: DeliveryClaim | None) -> None:
        if claim is not None:
            self._sensitive.add(claim.owner_token)

    async def acquire(
        self,
        key: IdempotencyKey,
        *,
        lease_seconds: float,
    ) -> DeliveryClaim | None:
        self._track(key)
        self.acquire_count += 1
        claim = await self._store.acquire(key, lease_seconds=lease_seconds)
        self._protect(claim)
        if claim is not None:
            self._claims[key] = claim
        return claim

    async def renew(self, claim: DeliveryClaim) -> bool:
        self._track(claim.key)
        self._protect(claim)
        return await self._store.renew(claim)

    async def complete(self, claim: DeliveryClaim) -> bool:
        self._track(claim.key)
        self._protect(claim)
        self.complete_count += 1
        return await self._store.complete(claim)

    async def release(self, claim: DeliveryClaim) -> bool:
        self._track(claim.key)
        self._protect(claim)
        self.release_count += 1
        released = await self._store.release(claim)
        if released and self._claims.get(claim.key) == claim:
            self._claims.pop(claim.key, None)
        return released

    async def release_for(self, key: IdempotencyKey) -> bool:
        claim = self._claims.get(key)
        if claim is None:
            return False
        return await self.release(claim)

    async def cleanup(self) -> None:
        if not self._owned_keys:
            return
        keys = sorted(self._owned_keys)
        await self._redis.delete(*keys)
        remaining = [key for key in keys if await self._redis.get(key) is not None]
        if remaining:
            raise RuntimeError("owned delivery claim Redis keys survived cleanup")
        self._owned_keys.clear()


class KafkaPluginAcceptanceHarness:
    def __init__(self, *, config: AcceptanceConfig, label: str) -> None:
        self.config = config
        self.identity: AcceptanceIdentity = new_identity(label)
        self.secret_store = TestOnlyFileSecretStore(config.secret_root)
        self.sensitive = SensitiveValues()
        self.sensitive.add_config(config, self.secret_store)
        self.signer = HmacPluginEventSigner(
            secret_store=self.secret_store,
            secret_ref=config.event_secret_ref,
        )
        self.signer.validate()
        self._claims_secret = self.secret_store.get(config.claims_secret_ref)
        if len(self._claims_secret.encode("utf-8")) < 32:
            raise RuntimeError("claims signing key must contain at least 32 bytes")
        if self._claims_secret == self.secret_store.get(config.event_secret_ref):
            raise RuntimeError("event and claims signing keys must be independent")
        self._security = self._build_security()
        self.subscription = Subscription(
            id=self.identity.subscription_id,
            plugin_id=self.identity.plugin_id,
            event_types=("acceptance.created",),
            consumer_group=self.identity.consumer_group,
        )
        self._admin: AIOKafkaAdminClient | None = None
        self._producer: AIOKafkaProducer | None = None
        self.producer: AiokafkaProducerAdapter | None = None
        self.topic_admin: KafkaTopicAdminAdapter | None = None
        self.consumer: AiokafkaConsumerAdapter | None = None
        self.dispatcher: PluginEventDispatcher | None = None
        self.backend: AcceptanceBackend | None = None
        self.wake: TestOnlyWakeFixture | None = None
        self.proxy: Any = None
        self.handler: ActivatingPluginEventHandler | None = None
        self.redis: Redis | None = None
        self.raw_dlq: TrackedRawDlq | None = None
        self.delivery_claims: TrackedDeliveryClaims | None = None
        self.sql = RealSqlAcceptance(config=config, identity=self.identity)
        self.sql_state: Any = None
        self.cleanup_resources: KafkaResourceCleanup | None = None
        self._consumers: list[AiokafkaConsumerAdapter] = []
        self._external_consumers: list[AIOKafkaConsumer] = []
        self._dlq_adapters: list[KafkaDlqAdapter] = []
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    async def __aenter__(self) -> "KafkaPluginAcceptanceHarness":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.aclose()

    @classmethod
    async def open(cls, *, config: AcceptanceConfig, label: str) -> "KafkaPluginAcceptanceHarness":
        harness = cls(config=config, label=label)
        try:
            await harness._open()
        except Exception:
            try:
                await harness.aclose()
            except Exception:
                pass
            raise
        return harness

    async def _open(self) -> None:
        timeout = self.config.operation_timeout_seconds
        async with asyncio.timeout(timeout):
            self._admin = AIOKafkaAdminClient(
                bootstrap_servers=self.config.bootstrap_servers,
                client_id=f"acceptance-admin-{self.identity.run_id}",
                **self._security_kwargs(),
            )
            await self._admin.start()
            self.cleanup_resources = KafkaResourceCleanup(admin=self._admin)
            self.cleanup_resources.track_topics(
                self.identity.topic,
                self.identity.dlq_topic,
                self.identity.unknown_topic,
            )
            self.topic_admin = KafkaTopicAdminAdapter(
                bootstrap_servers=self.config.bootstrap_servers,
                admin=self._admin,
                security=self._security,
            )
            await self.topic_admin.start()
            await prepare_random_topics(self.topic_admin, self.identity)

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.config.bootstrap_servers,
                client_id=f"acceptance-producer-{self.identity.run_id}",
                acks="all",
                enable_idempotence=True,
                **self._security_kwargs(),
            )
            await self._producer.start()
            self.producer = AiokafkaProducerAdapter(
                bootstrap_servers=self.config.bootstrap_servers,
                producer=self._producer,
                security=self._security,
            )
            await self.producer.start()
            self.sql_state = await self.sql.open()
            await self.sql_state.subscriptions.put(self.subscription, active=True)
            self.redis = Redis.from_url(
                self.config.redis_dsn,
                socket_connect_timeout=min(timeout, 10.0),
                socket_timeout=min(timeout, 10.0),
                health_check_interval=15,
            )
            await self.redis.ping()

            self.backend = AcceptanceBackend(
                identity=self.identity,
                event_signer=self.signer,
                claims_secret=self._claims_secret,
                topic=self.identity.topic,
                artifact_digest=self.sql_state.artifact_digest,
                timeout_seconds=timeout,
            )
            self.backend.start()
            self.wake = TestOnlyWakeFixture(
                grants=self.sql_state.grants,
                claims_secret=self._claims_secret,
                identity=self.identity,
                sensitive=self.sensitive,
                timeout_seconds=timeout,
            )
            self.proxy = build_production_http_proxy(
                backend=self.backend,
                timeout_seconds=timeout,
            )
            self.handler = ActivatingPluginEventHandler(
                activator=self.wake,
                proxy=self.proxy,
                grant_store=self.sql_state.grants,
                signer=self.signer,
                topic=self.identity.topic,
                timeout_seconds=timeout,
                wake_timeout=timeout,
            )
            self.raw_dlq = TrackedRawDlq(
                KafkaRawDlqAdapter(
                    resources=_RawDlqResources(self.producer),
                    redis=self.redis,
                ),
                self.redis,
            )
            self.delivery_claims = TrackedDeliveryClaims(
                store=RedisDeliveryClaimStore(
                    redis=self.redis,
                    prefix=(
                        "agentscale:plugin:delivery:acceptance:"
                        f"{self.identity.run_id}:"
                    ),
                ),
                redis=self.redis,
                sensitive=self.sensitive,
            )
            self.consumer = await self._new_consumer()
            self.dispatcher = self._build_dispatcher(self.consumer)

    def _build_security(self) -> KafkaSecurityConfig | None:
        if self.config.security_protocol == "PLAINTEXT":
            return None
        return KafkaSecurityConfig.from_secret_refs(
            secret_store=self.secret_store,
            env={
                ENV_USERNAME_REF: self.config.sasl_username_ref or "",
                ENV_PASSWORD_REF: self.config.sasl_password_ref or "",
                ENV_CA_FILE: self.config.tls_ca_file or "",
                "PLUGIN_KAFKA_SASL_MECHANISM": self.config.sasl_mechanism,
            },
        )

    def _security_kwargs(self) -> dict[str, Any]:
        kwargs = self._security.as_aiokafka_kwargs() if self._security is not None else {}
        kwargs.setdefault("request_timeout_ms", 15000)
        return kwargs

    async def _new_consumer(
        self,
        *,
        topics: tuple[str, ...] | None = None,
    ) -> AiokafkaConsumerAdapter:
        external: AIOKafkaConsumer | None = None
        if topics is not None:
            external = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=self.config.bootstrap_servers,
                client_id=f"acceptance-consumer-{uuid.uuid4().hex[:12]}",
                group_id=self.identity.consumer_group,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                value_deserializer=lambda raw: raw,
                **self._security_kwargs(),
            )
            self._external_consumers.append(external)
            await external.start()
        adapter = AiokafkaConsumerAdapter(
            bootstrap_servers=self.config.bootstrap_servers,
            subscription=self.subscription,
            topic=self.identity.topic,
            consumer=external,
            security=self._security,
        )
        self._consumers.append(adapter)
        async with asyncio.timeout(self.config.operation_timeout_seconds):
            await adapter.start()
        assert self.cleanup_resources is not None
        self.cleanup_resources.track_group(self.identity.consumer_group)
        return adapter

    def _build_dispatcher(
        self,
        consumer: AiokafkaConsumerAdapter,
        *,
        include_delivery_claims: bool = True,
    ) -> PluginEventDispatcher:
        assert self.producer is not None
        assert self.topic_admin is not None
        assert self.raw_dlq is not None
        assert self.delivery_claims is not None
        dlq = KafkaDlqAdapter(producer=self._producer, topic=self.identity.topic)
        self._dlq_adapters.append(dlq)
        return PluginEventDispatcher(
            producer=self.producer,
            consumer=consumer,
            topic_admin=self.topic_admin,
            dlq=dlq,
            raw_dlq=self.raw_dlq,
            subscriptions=self.sql_state.subscriptions,
            published_events=self.sql_state.published_events,
            idempotency=self.sql_state.idempotency,
            delivery_claims=(
                self.delivery_claims if include_delivery_claims else None
            ),
            delivery_lease_seconds=min(
                30.0,
                max(1.0, self.config.operation_timeout_seconds),
            ),
            audit=self.sql_state.audit,
            handler=self.handler,
            wake_hook=self.handler,
            signer=self.signer,
            topic=self.identity.topic,
        )

    async def create_secondary_dispatcher(self) -> PluginEventDispatcher:
        consumer = await self._new_consumer()
        return self._build_dispatcher(consumer)

    def dispatcher_without_delivery_claims(self) -> PluginEventDispatcher:
        assert self.consumer is not None
        return self._build_dispatcher(
            self.consumer,
            include_delivery_claims=False,
        )

    async def replace_consumer_with_topics(self, *topics: str) -> None:
        assert self.consumer is not None
        async with asyncio.timeout(self.config.operation_timeout_seconds):
            await self.consumer.close()
            self.consumer = await self._new_consumer(topics=tuple(topics))
            self.dispatcher = self._build_dispatcher(self.consumer)

    async def restart_consumer(self) -> None:
        assert self.consumer is not None
        async with asyncio.timeout(self.config.operation_timeout_seconds):
            await self.consumer.close()
            self.consumer = await self._new_consumer()
            self.dispatcher = self._build_dispatcher(self.consumer)

    @property
    def valid_dlq_count(self) -> int:
        return sum(len(adapter.published) for adapter in self._dlq_adapters)

    async def release_abandoned_claim_for(self, event: PluginEvent) -> bool:
        assert self.delivery_claims is not None
        key = IdempotencyKey(
            subscription_id=self.identity.subscription_id,
            source_event_id=event.source_id,
        )
        return await self.delivery_claims.release_for(key)

    async def claim_snapshot(self, event: PluginEvent) -> dict[str, Any]:
        assert self.delivery_claims is not None
        assert self.redis is not None
        key = IdempotencyKey(
            subscription_id=self.identity.subscription_id,
            source_event_id=event.source_id,
        )
        lease_key, completed_key, generation_key = self.delivery_claims.keys_for(key)
        lease, completed, generation = await asyncio.gather(
            self.redis.get(lease_key),
            self.redis.get(completed_key),
            self.redis.get(generation_key),
        )
        return {
            "lease_exists": lease is not None,
            "completed": completed is not None,
            "generation": int(generation or 0),
            "acquire_count": self.delivery_claims.acquire_count,
            "complete_count": self.delivery_claims.complete_count,
            "release_count": self.delivery_claims.release_count,
            "owned_key_count": self.delivery_claims.owned_key_count,
        }

    def new_event(self, *, event_type: str = "acceptance.created") -> PluginEvent:
        marker = f"raw-payload-canary-{uuid.uuid4().hex}"
        self.sensitive.add(marker)
        event_id = f"evt-{uuid.uuid4().hex}"
        return PluginEvent(
            event_id=event_id,
            source_event_id=f"src-{uuid.uuid4().hex}",
            event_type=event_type,
            source="kafka-plugin-acceptance",
            company_id=self.identity.company_id,
            plugin_id=self.identity.plugin_id,
            subscription_id=self.identity.subscription_id,
            occurred_at=datetime.now(UTC),
            payload_digest=canonical_digest({"never_on_kafka": marker}),
            run_id=f"run-{uuid.uuid4().hex}",
            correlation_id=f"cor-{uuid.uuid4().hex}",
        )

    def signed_bytes(self, event: PluginEvent) -> bytes:
        return json.dumps(
            self.signer.signed_wire(event, topic=self.identity.topic),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    async def publish_raw(
        self,
        value: bytes,
        *,
        topic: str | None = None,
        key: bytes | None = None,
    ) -> Any:
        assert self._producer is not None
        async with asyncio.timeout(self.config.operation_timeout_seconds):
            return await self._producer.send_and_wait(
                topic or self.identity.topic,
                key=key if key is not None else self.identity.company_id.encode("utf-8"),
                value=value,
            )

    async def publish_valid(self, event: PluginEvent) -> bool:
        assert self.dispatcher is not None
        async with asyncio.timeout(self.config.operation_timeout_seconds):
            return await self.dispatcher.publish(event)

    async def consume_next(
        self,
        *,
        dispatcher: PluginEventDispatcher | None = None,
        subscription: Subscription | None = None,
    ) -> DeliveryOutcome:
        selected = dispatcher or self.dispatcher
        assert selected is not None
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks.add(task)
        try:
            async with asyncio.timeout(self.config.operation_timeout_seconds):
                while True:
                    outcome = await selected.consume(subscription or self.subscription)
                    if outcome is not None:
                        return outcome
        finally:
            if task is not None:
                self._active_tasks.discard(task)

    async def read_dlq(self, count: int) -> list[Any]:
        if count < 1 or count > 20:
            raise ValueError("DLQ read count must be bounded to 1..20")
        consumer = AIOKafkaConsumer(
            self.identity.dlq_topic,
            bootstrap_servers=self.config.bootstrap_servers,
            client_id=f"acceptance-dlq-reader-{self.identity.run_id}",
            group_id=self.identity.observer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda raw: raw,
            **self._security_kwargs(),
        )
        records: list[Any] = []
        try:
            async with asyncio.timeout(self.config.operation_timeout_seconds):
                await consumer.start()
                assert self.cleanup_resources is not None
                self.cleanup_resources.track_group(self.identity.observer_group)
                while len(records) < count:
                    try:
                        record = await asyncio.wait_for(consumer.getone(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    records.append(record)
        finally:
            async with asyncio.timeout(self.config.operation_timeout_seconds):
                await consumer.stop()
        return records

    async def _cancel_active_tasks(self) -> None:
        current = asyncio.current_task()
        tasks = [task for task in self._active_tasks if task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active_tasks.clear()

    async def close_consumers(self) -> None:
        errors: list[Exception] = []
        for adapter in reversed(self._consumers):
            try:
                await adapter.close()
            except Exception as exc:
                errors.append(exc)
        self._consumers.clear()
        for consumer in reversed(self._external_consumers):
            try:
                await consumer.stop()
            except Exception as exc:
                errors.append(exc)
        self._external_consumers.clear()
        if errors:
            raise RuntimeError("one or more Kafka consumers could not be closed") from errors[0]

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []

        async def attempt(operation: Any) -> None:
            try:
                async with asyncio.timeout(self.config.operation_timeout_seconds):
                    await operation()
            except Exception as exc:
                errors.append(exc)

        await attempt(self._cancel_active_tasks)
        await attempt(self.close_consumers)
        if self.proxy is not None:
            await attempt(self.proxy.aclose)
        if self.backend is not None:
            await attempt(self.backend.stop)
        if self.delivery_claims is not None:
            await attempt(self.delivery_claims.cleanup)
        if self.raw_dlq is not None:
            await attempt(self.raw_dlq.cleanup)
        if self.redis is not None:
            await attempt(self.redis.aclose)
        await attempt(self.sql.aclose)
        if self._producer is not None:
            await attempt(self._producer.stop)
        if self.cleanup_resources is not None:
            await attempt(lambda: self.cleanup_resources.delete_consumer_groups(self.config.operation_timeout_seconds))
            await attempt(lambda: self.cleanup_resources.delete_topics(self.config.operation_timeout_seconds))
        if self._admin is not None:
            await attempt(self._admin.close)
        if errors:
            raise RuntimeError("one or more acceptance cleanup operations failed") from errors[0]

    def assert_no_sensitive_logs(self, text: str) -> None:
        assert_no_sensitive_text(text, self.sensitive)

    def assert_safe_dlq(self, records: list[Any]) -> None:
        forbidden = {
            "payload", "body", "claims", "token", "secret", "authorization",
            "event_signature", "event_signature_version",
        }
        for record in records:
            raw = bytes(record.value)
            self.assert_no_sensitive_logs(raw.decode("utf-8", errors="replace"))
            decoded = json.loads(raw)
            if forbidden & set(decoded):
                raise AssertionError("DLQ contains a forbidden plaintext field")


__all__ = [
    "KafkaPluginAcceptanceHarness",
    "TrackedDeliveryClaims",
    "TrackedRawDlq",
]
