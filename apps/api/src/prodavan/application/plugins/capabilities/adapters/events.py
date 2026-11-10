"""Kafka publish/subscribe with durable idempotency and replay-safe consumers."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping, Protocol

from prodavan.application.plugins.capabilities.contracts import (
    EventsPublishRequest,
    EventsPublishResponse,
    EventsStartOffset,
    EventsSubscribeRequest,
    EventsSubscribeResponse,
)
from prodavan.application.plugins.events.contracts import (
    MAX_DELIVERY_ATTEMPTS,
    PluginEvent,
    Subscription,
)
from prodavan.application.plugins.events.topics import (
    DEFAULT_NUM_PARTITIONS,
    PLUGIN_EVENTS_TOPIC,
)
from ._shared import (
    CloseOnce,
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
    await_io,
    canonical_digest,
    canonical_json,
    plugin_id,
    request_envelope,
    CapabilityAdapterError,
    usage,
)


class KafkaSubscriptionRuntime(Protocol):
    async def start(
        self,
        *,
        subscription: Subscription,
        start_offset: EventsStartOffset,
        max_retries: int,
    ) -> Mapping[str, Any]: ...

    async def aclose(self) -> None: ...


class KafkaEventsAdapter(CloseOnce):
    def __init__(
        self,
        *,
        dispatcher: Any,
        subscriptions: Any,
        runtime: KafkaSubscriptionRuntime,
        lifecycle: Any = None,
        timeout_seconds: float = 10.0,
        owns_runtime: bool = True,
    ) -> None:
        owned = (runtime, lifecycle) if owns_runtime and lifecycle is not None else (
            (runtime,) if owns_runtime else ()
        )
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=owned,
        )
        if dispatcher is None or not all(
            callable(getattr(dispatcher, method, None)) for method in ("publish", "consume")
        ):
            raise CapabilityConfigurationError("kafka_event_dispatcher_required")
        if getattr(dispatcher, "topic", None) != PLUGIN_EVENTS_TOPIC:
            raise CapabilityConfigurationError("kafka_event_topic_not_allowed")
        if not all(callable(getattr(subscriptions, method, None)) for method in ("get", "put", "revoke")):
            raise CapabilityConfigurationError("durable_subscription_store_required")
        if not callable(getattr(runtime, "start", None)):
            raise CapabilityConfigurationError("kafka_subscription_runtime_required")
        if lifecycle is not None and not all(
            callable(getattr(lifecycle, method, None)) for method in ("start", "aclose")
        ):
            raise CapabilityConfigurationError("kafka_resource_lifecycle_invalid")
        self._lifecycle = lifecycle
        if DEFAULT_NUM_PARTITIONS != 1:
            raise CapabilityConfigurationError("event_partition_projection_requires_single_partition")
        self._dispatcher = dispatcher
        self._subscriptions = subscriptions
        self._runtime = runtime

    def quota_usage(self, request: Any) -> Any:
        if isinstance(request, EventsPublishRequest):
            return usage(keys=1, bytes=len(canonical_json({
                "event_id": self._event_id(request),
                "event_type": request.event_type,
                "payload_digest": request.payload_digest,
                "company_id": request.envelope.company_id,
            })))
        if isinstance(request, EventsSubscribeRequest):
            return usage(rows=1, keys=1, bytes=len(canonical_json({
                "subscription_id": request.subscription_id,
                "consumer_group": request.consumer_group,
                "event_types": request.event_types,
            })))
        raise CapabilityAccessDenied("typed_request_required")

    async def _prepare(self) -> None:
        if self._lifecycle is not None:
            await await_io(
                self._lifecycle.start(),
                timeout=self._timeout_seconds,
                code="kafka_resource_start_failed",
            )

    @staticmethod
    def _event_id(request: EventsPublishRequest) -> str:
        envelope = request.envelope
        return "evt_" + canonical_digest({
            "request_id": envelope.request_id,
            "company_id": envelope.company_id,
            "plugin_instance_id": envelope.plugin_instance_id,
            "topic": request.topic,
            "event_type": request.event_type,
            "partition_key": request.partition_key,
            "payload_digest": request.payload_digest,
        })[len("sha256:"):]

    async def publish(self, request: EventsPublishRequest) -> EventsPublishResponse:
        envelope = request_envelope(request, EventsPublishRequest)
        self._ensure_open()
        if request.topic != self._dispatcher.topic:
            raise CapabilityAccessDenied("events_topic_not_allowed")
        if request.partition_key != envelope.company_id:
            raise CapabilityAccessDenied("events_partition_key_tenant_mismatch")
        await self._prepare()
        source = plugin_id(envelope)
        event = PluginEvent(
            event_id=self._event_id(request),
            event_type=request.event_type,
            source=source,
            company_id=envelope.company_id,
            plugin_id=source,
            subscription_id=f"publish:{envelope.plugin_instance_id}",
            occurred_at=datetime.now(UTC),
            payload_digest=request.payload_digest,
            run_id=envelope.run_id or envelope.request_id,
            correlation_id=envelope.correlation_id or envelope.request_id,
        )
        # Duplicate event_id is a successful idempotent retry. Dispatcher itself
        # flushes Kafka before remembering the producer idempotency marker.
        published = await await_io(
            self._dispatcher.publish(event),
            timeout=self._timeout_seconds,
            code="kafka_event_publish_failed",
        )
        if not isinstance(published, bool):
            raise CapabilityBackendError("kafka_event_publish_invalid_ack")
        return EventsPublishResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "event_id": event.event_id,
                "event_type": event.event_type,
                "company_id": event.company_id,
                "plugin_instance_id": envelope.plugin_instance_id,
                "topic": request.topic,
                "payload_digest": event.payload_digest,
                "partition": 0,
            }),
            event_id=event.event_id,
            partition=0,
        )

    async def subscribe(self, request: EventsSubscribeRequest) -> EventsSubscribeResponse:
        envelope = request_envelope(request, EventsSubscribeRequest)
        self._ensure_open()
        if request.topic != self._dispatcher.topic:
            raise CapabilityAccessDenied("events_topic_not_allowed")
        if request.start_offset is not EventsStartOffset.EARLIEST:
            raise CapabilityAccessDenied("events_latest_offset_replay_unsafe")
        if request.max_retries != MAX_DELIVERY_ATTEMPTS:
            raise CapabilityAccessDenied("events_retry_policy_mismatch")
        await self._prepare()
        plugin = plugin_id(envelope)
        subscription_fingerprint = canonical_digest({
            "company_id": envelope.company_id,
            "plugin_instance_id": envelope.plugin_instance_id,
            "subscription_id": request.subscription_id,
            "consumer_group": request.consumer_group,
            "event_types": tuple(dict.fromkeys(request.event_types)),
        })[len("sha256:"):len("sha256:") + 16]
        subscription_id = (
            f"{envelope.company_id}:{envelope.plugin_instance_id}:"
            f"{request.subscription_id}:{subscription_fingerprint}"
        )
        consumer_group = (
            f"tenant:{envelope.company_id}:plugin:{plugin}:"
            f"{envelope.plugin_instance_id}:group:{request.consumer_group}"
        )
        subscription = Subscription(
            id=subscription_id,
            plugin_id=plugin,
            event_types=tuple(dict.fromkeys(request.event_types)),
            consumer_group=consumer_group,
        )
        existing = await await_io(
            self._subscriptions.get(subscription.id),
            timeout=self._timeout_seconds,
            code="subscription_store_read_failed",
        )
        if existing is not None and (
            existing.consumer_group != subscription.consumer_group
            or existing.event_types != subscription.event_types
        ):
            raise CapabilityAccessDenied("events_subscription_conflict")
        await await_io(
            self._subscriptions.put(subscription, active=True),
            timeout=self._timeout_seconds,
            code="subscription_store_write_failed",
        )
        try:
            settings = await await_io(
                self._runtime.start(
                    subscription=subscription,
                    start_offset=request.start_offset,
                    max_retries=request.max_retries,
                ),
                timeout=self._timeout_seconds,
                code="kafka_subscription_start_failed",
            )
        except Exception:
            try:
                await await_io(
                    self._subscriptions.revoke(subscription.id),
                    timeout=self._timeout_seconds,
                    code="subscription_rollback_failed",
                )
            except CapabilityAdapterError:
                pass
            raise
        expected = {
            "subscription_id": subscription.id,
            "consumer_group": consumer_group,
            "enable_auto_commit": False,
            "auto_offset_reset": "earliest",
            "manual_commit": True,
            "max_retries": MAX_DELIVERY_ATTEMPTS,
        }
        if not isinstance(settings, Mapping) or any(
            settings.get(name) != value for name, value in expected.items()
        ):
            await await_io(
                self._subscriptions.revoke(subscription.id),
                timeout=self._timeout_seconds,
                code="subscription_rollback_failed",
            )
            stop = getattr(self._runtime, "stop", None)
            if callable(stop):
                await await_io(
                    stop(subscription.id),
                    timeout=self._timeout_seconds,
                    code="kafka_subscription_stop_failed",
                )
            raise CapabilityBackendError("kafka_subscription_replay_policy_mismatch")
        return EventsSubscribeResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                **expected,
                "topic": request.topic,
                "event_types": subscription.event_types,
            }),
            subscription_id=subscription.id,
            manual_commit=True,
        )


__all__ = ["KafkaEventsAdapter", "KafkaSubscriptionRuntime"]
