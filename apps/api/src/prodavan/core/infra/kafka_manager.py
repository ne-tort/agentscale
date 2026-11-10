"""KafkaManager — producer/consumer facade + LifespanResource (C-EVENT-BUS)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import deque
from typing import Any, Literal

from prodavan.core.events.auth_command import (
    AuthCommandError,
    validate_auth_command_signing_secret,
    verified_auth_command_envelope,
    verify_auth_command_envelope,
)
from prodavan.core.events.auth_event import (
    AuthEventError,
    AuthEventReplay,
    sign_auth_event_envelope,
    validate_auth_event_signing_secret,
    verified_auth_event_envelope,
    verify_auth_event_envelope,
)
from prodavan.core.events.envelope import EventEnvelope
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

ConsumerMode = Literal["kick", "dispatch"]

_manager: KafkaManager | None = None


def get_kafka_manager() -> KafkaManager:
    if _manager is None:
        raise RuntimeError("KafkaManager is not started (lifespan)")
    return _manager


def get_kafka_manager_optional() -> KafkaManager | None:
    return _manager


def set_kafka_manager(manager: KafkaManager | None) -> None:
    global _manager
    _manager = manager


class KafkaManager(LifespanResource):
    """Publish EventEnvelope to Kafka; optional consumer accelerates Celery.

    PG outbox remains claim/drain SoT until full consumer cutover (documented hole).

    Consumer modes (project triggers):
    - ``kick`` (default): debounce → ``prodavan.jobs.trigger_drain``
    - ``dispatch``: per message → ``prodavan.jobs.dispatch_trigger(event_id)``

    Platform jobs (optional, ``rematerialize_via_bus``):
    - ``project.rematerialize.requested`` on platform topic → ``prodavan.jobs.rematerialize_project``

    Auth topics (when consumer enabled):
    - ``auth.commands`` → Auth Service register handler → publish ``auth.events``
    - ``auth.events`` → enqueue ``apply_auth_user_registered``
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        bootstrap_servers: str | None = None,
        client_id: str = "prodavan-api",
        topic_platform_events: str = "prodavan.platform.events",
        topic_project_triggers: str = "prodavan.project.triggers",
        topic_auth_commands: str = "prodavan.auth.commands",
        topic_auth_command_dlq: str | None = None,
        topic_auth_events: str = "prodavan.auth.events",
        topic_auth_event_dlq: str | None = None,
        topic_relation_events: str = "prodavan.relation.events",
        topic_metrics_events: str = "prodavan.metrics.events",
        topic_document_events: str = "prodavan.document.events",
        topic_search_events: str = "prodavan.search.events",
        topic_tenant_events: str = "prodavan.tenant.events",
        required: bool = False,
        buffer_size: int = 200,
        consumer_enabled: bool = False,
        consumer_group: str = "prodavan-api-triggers",
        auth_commands_group: str = "prodavan-auth-commands",
        auth_events_group: str = "prodavan-auth-events",
        relation_events_group: str = "prodavan-relation-events",
        platform_jobs_group: str = "prodavan-platform-jobs",
        rematerialize_via_bus: bool = False,
        drain_debounce_sec: float = 1.0,
        consumer_mode: str = "kick",
        auth_command_signing_secret: str | None = None,
        auth_event_signing_secret: str | None = None,
    ) -> None:
        self._enabled = enabled
        self._bootstrap = (bootstrap_servers or "").strip() or None
        self._client_id = client_id
        self._topic_platform = topic_platform_events
        self._topic_triggers = topic_project_triggers
        self._topic_auth_commands = topic_auth_commands
        self._topic_auth_command_dlq = topic_auth_command_dlq or f"{topic_auth_commands}.dlq"
        self._topic_auth_events = topic_auth_events
        self._topic_auth_event_dlq = topic_auth_event_dlq or f"{topic_auth_events}.dlq"
        self._topic_relation_events = topic_relation_events
        self._topic_metrics_events = topic_metrics_events
        self._topic_document_events = topic_document_events
        self._topic_search_events = topic_search_events
        self._topic_tenant_events = topic_tenant_events
        self._required = required
        self._consumer_enabled = consumer_enabled
        self._consumer_group = consumer_group
        self._auth_commands_group = auth_commands_group
        self._auth_events_group = auth_events_group
        self._relation_events_group = relation_events_group
        self._platform_jobs_group = platform_jobs_group
        self._rematerialize_via_bus = bool(rematerialize_via_bus)
        self._drain_debounce_sec = max(0.1, float(drain_debounce_sec))
        mode = (consumer_mode or "kick").strip().lower()
        self._consumer_mode: ConsumerMode = "dispatch" if mode == "dispatch" else "kick"
        self._producer: Any = None
        self._consumer: Any = None
        self._auth_commands_consumer: Any = None
        self._auth_events_consumer: Any = None
        self._relation_events_consumer: Any = None
        self._platform_jobs_consumer: Any = None
        self._consume_task: asyncio.Task[None] | None = None
        self._auth_commands_task: asyncio.Task[None] | None = None
        self._auth_events_task: asyncio.Task[None] | None = None
        self._relation_events_task: asyncio.Task[None] | None = None
        self._platform_jobs_task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max(1, buffer_size))
        self._drain_kicks: int = 0
        self._dispatch_enqueues: int = 0
        self._rematerialize_enqueues: int = 0
        self._auth_register_handled: int = 0
        self._auth_bind_enqueues: int = 0
        self._relation_events_handled: int = 0
        self._local_auth_depth: int = 0
        self._auth_command_signing_secret_override = auth_command_signing_secret
        self._auth_commands_rejected: int = 0
        self._auth_commands_dlq: int = 0
        self._auth_command_dlq_buffer: deque[dict[str, Any]] = deque(maxlen=max(1, buffer_size))
        self._auth_event_signing_secret_override = auth_event_signing_secret
        self._auth_events_rejected: int = 0
        self._auth_events_dlq: int = 0
        self._auth_event_dlq_buffer: deque[dict[str, Any]] = deque(maxlen=max(1, buffer_size))

    def _auth_command_secret(self) -> str | None:
        if self._auth_command_signing_secret_override is not None:
            return self._auth_command_signing_secret_override
        from prodavan.config.settings import settings

        return settings.auth_command_signing_secret

    def _auth_event_secret(self) -> str | None:
        if self._auth_event_signing_secret_override is not None:
            return self._auth_event_signing_secret_override
        from prodavan.config.settings import settings

        return settings.auth_event_signing_secret

    @property
    def name(self) -> str:
        return "kafka"

    @property
    def enabled(self) -> bool:
        return bool(self._enabled and self._bootstrap and self._producer is not None)

    @property
    def consumer_running(self) -> bool:
        """True when all enabled consumer loops are alive (triggers + auth + relation)."""
        if not self._consumer_enabled:
            return False
        required_tasks = (
            self._consume_task,
            self._auth_commands_task,
            self._auth_events_task,
            self._relation_events_task,
        )
        if self._rematerialize_via_bus:
            required_tasks = (*required_tasks, self._platform_jobs_task)
        return all(t is not None and not t.done() for t in required_tasks)

    @property
    def consumer_mode(self) -> ConsumerMode:
        return self._consumer_mode

    @property
    def buffering_only(self) -> bool:
        return not self.enabled

    @property
    def drain_kicks(self) -> int:
        return self._drain_kicks

    @property
    def dispatch_enqueues(self) -> int:
        return self._dispatch_enqueues

    @property
    def rematerialize_enqueues(self) -> int:
        return self._rematerialize_enqueues

    @property
    def auth_commands_rejected(self) -> int:
        return self._auth_commands_rejected

    @property
    def auth_commands_dlq(self) -> int:
        return self._auth_commands_dlq

    def recent_auth_command_dlq(self) -> list[dict[str, Any]]:
        """Sanitized rejection metadata, never the original command payload."""

        return list(self._auth_command_dlq_buffer)

    @property
    def auth_events_rejected(self) -> int:
        return self._auth_events_rejected

    @property
    def auth_events_dlq(self) -> int:
        return self._auth_events_dlq

    def recent_auth_event_dlq(self) -> list[dict[str, Any]]:
        return list(self._auth_event_dlq_buffer)

    def topic_for(self, bus: str) -> str:
        if bus == "platform":
            return self._topic_platform
        if bus == "project_trigger":
            return self._topic_triggers
        if bus == "auth_command":
            return self._topic_auth_commands
        if bus == "auth_event":
            return self._topic_auth_events
        if bus == "relation_event":
            return self._topic_relation_events
        if bus == "metrics":
            return self._topic_metrics_events
        if bus == "document":
            return self._topic_document_events
        if bus == "search":
            return self._topic_search_events
        if bus == "tenant":
            return self._topic_tenant_events
        raise ValueError(f"unknown bus: {bus}")

    def recent_envelopes(self) -> list[dict[str, Any]]:
        return list(self._buffer)

    def clear_buffer(self) -> None:
        self._buffer.clear()

    def reset_local_test_state(self) -> None:
        """Drop in-memory auth buffer between integration tests (no-broker path)."""
        self._buffer.clear()
        self._local_auth_depth = 0
        self._auth_command_dlq_buffer.clear()
        self._auth_commands_rejected = 0
        self._auth_commands_dlq = 0
        self._auth_event_dlq_buffer.clear()
        self._auth_events_rejected = 0
        self._auth_events_dlq = 0

    async def publish(self, envelope: EventEnvelope) -> bool:
        if envelope.bus == "auth_command":
            try:
                # Even an internally constructed envelope cannot bypass signing.
                verify_auth_command_envelope(envelope, signing_secret=self._auth_command_secret())
            except AuthCommandError as exc:
                await self._reject_auth_command(envelope.to_dict(), exc.code)
                return False
            try:
                # A command must never create a Keycloak user if its result cannot
                # subsequently pass the independent signed identity-bind path.
                validate_auth_event_signing_secret(self._auth_event_secret())
            except AuthEventError as exc:
                await self._reject_auth_command(envelope.to_dict(), exc.code)
                return False
        if envelope.bus == "auth_event":
            try:
                # Direct domain calls may return an unsigned result; the trusted
                # bus producer is the single edge that signs every auth event.
                if envelope.signature is None:
                    sign_auth_event_envelope(envelope, signing_secret=self._auth_event_secret())
                verify_auth_event_envelope(envelope, signing_secret=self._auth_event_secret())
            except AuthEventError as exc:
                await self._reject_auth_event(envelope.to_dict(), exc.code)
                return False
        payload = envelope.to_dict()
        self._buffer.append(payload)
        if not self.enabled:
            logger.debug(
                "kafka: buffer-only bus=%s type=%s id=%s",
                envelope.bus,
                envelope.event_type,
                envelope.event_id,
            )
            await self._local_auth_dispatch(envelope)
            await self._local_metrics_dispatch(envelope)
            return True
        topic = self.topic_for(envelope.bus)
        key = (envelope.project_id or envelope.company_id or envelope.event_id).encode("utf-8")
        value = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        try:
            await self._producer.send_and_wait(topic, value=value, key=key)
            return True
        except Exception as exc:
            if envelope.bus in {"auth_command", "auth_event"}:
                # Broker errors can echo serialized values (including refs/PII).
                logger.error(
                    "kafka: auth bus publish failed bus=%s error_type=%s",
                    envelope.bus,
                    type(exc).__name__,
                )
                if self._required:
                    raise RuntimeError("auth bus publish failed") from None
            else:
                logger.exception("kafka: publish failed topic=%s id=%s", topic, envelope.event_id)
                if self._required:
                    raise
            return False

    async def _local_auth_dispatch(self, envelope: EventEnvelope) -> None:
        """CI / no-broker: process auth commands/events in-process (Fake register path)."""
        if self._local_auth_depth > 8:
            logger.error("kafka: local auth dispatch depth exceeded")
            return
        self._local_auth_depth += 1
        try:
            if envelope.bus == "auth_command":
                from prodavan.application.auth.register import handle_auth_command_envelope

                result = await handle_auth_command_envelope(
                    envelope, signing_secret=self._auth_command_secret()
                )
                self._auth_register_handled += 1
                if result is not None:
                    await self.publish(result)
                return
            if envelope.bus == "auth_event":
                # Same signed/fresh/one-time lifecycle as the broker consumer.
                await self._handle_auth_event_envelope(envelope)
                return
            if envelope.bus == "relation_event":
                from prodavan.application.relations.commands import handle_relation_event_envelope

                await handle_relation_event_envelope(envelope)
                self._relation_events_handled += 1
        except AuthCommandError as exc:
            await self._reject_auth_command(envelope.to_dict(), exc.code)
        except AuthEventError as exc:
            await self._reject_auth_event(envelope.to_dict(), exc.code)
        except Exception as exc:
            # Exception messages/tracebacks may contain command data.
            logger.error(
                "kafka: local auth dispatch failed bus=%s type=%s error_type=%s",
                envelope.bus,
                envelope.event_type,
                type(exc).__name__,
            )
            if envelope.bus == "auth_event":
                await self._reject_auth_event(envelope.to_dict(), "AUTH_EVENT_HANDLER_FAILED")
        finally:
            self._local_auth_depth -= 1

    async def _local_metrics_dispatch(self, envelope: EventEnvelope) -> None:
        if envelope.bus not in {"platform", "metrics"}:
            return
        try:
            from prodavan.application.metrics.consumer.registry import handle_metrics_envelope

            await handle_metrics_envelope(envelope)
        except Exception:
            logger.exception(
                "kafka: local metrics dispatch failed bus=%s type=%s",
                envelope.bus,
                envelope.event_type,
            )

    async def _apply_auth_bind(self, payload: dict[str, Any]) -> None:
        from prodavan.application.identity.auth_bind import apply_auth_user_registered_payload
        from prodavan.infrastructure.persistence.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            result = await apply_auth_user_registered_payload(session, payload)
        self._auth_bind_enqueues += 1
        logger.info(
            "kafka: auth bind applied ok=%s bound=%s client_ref=%s",
            result.get("ok"),
            result.get("bound"),
            payload.get("client_ref"),
        )

    async def _kick_drain(self) -> None:
        """Enqueue Celery drain; coalesce across API replicas via Redis lock when available."""
        from prodavan.core.infra.cache import acquire_lock, cache_key
        from prodavan.core.infra.redis_manager import get_redis_manager
        from prodavan.core.jobs.enqueue import enqueue_trigger_drain

        redis = get_redis_manager()
        if redis is not None and redis.enabled:
            ttl = max(1, int(self._drain_debounce_sec))
            token = await acquire_lock(cache_key("lock", "kafka", "drain-kick"), ttl_sec=ttl)
            if token is None:
                logger.debug("kafka consumer: drain kick skipped (lock held)")
                return

        result = enqueue_trigger_drain()
        self._drain_kicks += 1
        logger.info("kafka consumer: kick trigger_drain enqueued=%s", result.get("enqueued"))

    def _enqueue_dispatch(self, trigger_id: str) -> None:
        from prodavan.core.jobs.enqueue import enqueue_dispatch_trigger

        result = enqueue_dispatch_trigger(trigger_id)
        self._dispatch_enqueues += 1
        logger.info(
            "kafka consumer: dispatch_trigger id=%s enqueued=%s",
            trigger_id,
            result.get("enqueued"),
        )

    def _enqueue_rematerialize(self, project_id: str) -> None:
        from prodavan.core.jobs.enqueue import enqueue_rematerialize_project

        result = enqueue_rematerialize_project(project_id)
        self._rematerialize_enqueues += 1
        logger.info(
            "kafka consumer: rematerialize_project id=%s enqueued=%s",
            project_id,
            result.get("enqueued"),
        )

    async def _consume_loop_kick(self, stop: asyncio.Event) -> None:
        assert self._consumer is not None
        pending_kick = False
        try:
            while not stop.is_set():
                try:
                    batch = await self._consumer.getmany(timeout_ms=500, max_records=50)
                except Exception:
                    if stop.is_set():
                        break
                    logger.exception("kafka: consumer getmany failed")
                    await asyncio.sleep(1.0)
                    continue
                if not batch:
                    if pending_kick:
                        await self._kick_drain()
                        pending_kick = False
                    continue
                for _tp, messages in batch.items():
                    for msg in messages:
                        try:
                            data = json.loads(msg.value.decode("utf-8"))
                        except Exception:
                            logger.warning("kafka: skip bad message offset=%s", msg.offset)
                            continue
                        if data.get("bus") == "project_trigger":
                            pending_kick = True
                if pending_kick:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self._drain_debounce_sec)
                        break
                    except TimeoutError:
                        await self._kick_drain()
                        pending_kick = False
        finally:
            logger.info("kafka: kick consumer stopped kicks=%s", self._drain_kicks)

    async def _consume_loop_dispatch(self, stop: asyncio.Event) -> None:
        assert self._consumer is not None
        try:
            while not stop.is_set():
                try:
                    batch = await self._consumer.getmany(timeout_ms=500, max_records=50)
                except Exception:
                    if stop.is_set():
                        break
                    logger.exception("kafka: consumer getmany failed")
                    await asyncio.sleep(1.0)
                    continue
                if not batch:
                    continue
                for _tp, messages in batch.items():
                    for msg in messages:
                        try:
                            data = json.loads(msg.value.decode("utf-8"))
                        except Exception:
                            logger.warning("kafka: skip bad message offset=%s", msg.offset)
                            continue
                        if data.get("bus") != "project_trigger":
                            continue
                        event_id = str(data.get("event_id") or "").strip()
                        if not event_id:
                            logger.warning("kafka: project_trigger without event_id offset=%s", msg.offset)
                            continue
                        self._enqueue_dispatch(event_id)
        finally:
            logger.info(
                "kafka: dispatch consumer stopped enqueues=%s",
                self._dispatch_enqueues,
            )

    async def _reject_auth_command(
        self,
        data: dict[str, Any],
        reason: str,
        *,
        source_partition: int | None = None,
        source_offset: int | None = None,
    ) -> bool:
        """Dead-letter sanitized metadata only; never copy command payload/secrets."""

        self._auth_commands_rejected += 1
        raw_event_id = str(data.get("event_id") or "")[:200]
        # Rejected input is attacker-controlled: never echo its fields into
        # logs/DLQ, even if a secret was copied into an unexpected field.
        event_id_hash = hashlib.sha256(raw_event_id.encode("utf-8")).hexdigest()[:16]
        record = {
            "schema_version": 1,
            "event_id_hash": event_id_hash,
            "source_topic": self._topic_auth_commands,
            "source_partition": source_partition,
            "source_offset": source_offset,
            "rejected_at": EventEnvelope.now_iso(),
            "reason": reason,
        }
        self._auth_command_dlq_buffer.append(record)
        logger.warning(
            "kafka: auth command rejected reason=%s id_hash=%s offset=%s",
            reason,
            event_id_hash,
            source_offset,
        )
        if not self.enabled:
            self._auth_commands_dlq += 1
            return True
        try:
            await self._producer.send_and_wait(
                self._topic_auth_command_dlq,
                value=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                key=event_id_hash.encode("utf-8"),
            )
            self._auth_commands_dlq += 1
            return True
        except Exception:
            # Do not expose broker exception text, which may echo message bytes.
            logger.error("kafka: auth command DLQ publish failed reason=%s", reason)
            return False

    async def _process_auth_command_data(
        self,
        data: dict[str, Any],
        *,
        source_partition: int | None = None,
        source_offset: int | None = None,
    ) -> bool:
        """Verify raw Kafka data first; only then parse and dispatch the command."""

        from prodavan.application.auth.register import handle_auth_command_envelope

        try:
            envelope = verified_auth_command_envelope(
                data, signing_secret=self._auth_command_secret()
            )
        except AuthCommandError as exc:
            await self._reject_auth_command(
                data,
                exc.code,
                source_partition=source_partition,
                source_offset=source_offset,
            )
            return False
        try:
            validate_auth_event_signing_secret(self._auth_event_secret())
        except AuthEventError as exc:
            await self._reject_auth_command(
                data,
                exc.code,
                source_partition=source_partition,
                source_offset=source_offset,
            )
            return False
        try:
            result = await handle_auth_command_envelope(
                envelope, signing_secret=self._auth_command_secret()
            )
            self._auth_register_handled += 1
            if result is not None:
                await self.publish(result)
            return True
        except AuthCommandError as exc:
            await self._reject_auth_command(
                data,
                exc.code,
                source_partition=source_partition,
                source_offset=source_offset,
            )
            return False
        except Exception as exc:
            logger.error(
                "kafka: auth command handler failed id=%s error_type=%s",
                envelope.event_id,
                type(exc).__name__,
            )
            await self._reject_auth_command(
                data,
                "AUTH_COMMAND_HANDLER_FAILED",
                source_partition=source_partition,
                source_offset=source_offset,
            )
            return False

    async def _consume_auth_commands(self, stop: asyncio.Event) -> None:
        assert self._auth_commands_consumer is not None

        try:
            while not stop.is_set():
                try:
                    batch = await self._auth_commands_consumer.getmany(timeout_ms=500, max_records=20)
                except Exception as exc:
                    if stop.is_set():
                        break
                    logger.error(
                        "kafka: auth commands getmany failed error_type=%s", type(exc).__name__
                    )
                    await asyncio.sleep(1.0)
                    continue
                if not batch:
                    continue
                for _tp, messages in batch.items():
                    for msg in messages:
                        try:
                            data = json.loads(msg.value.decode("utf-8"))
                            if not isinstance(data, dict):
                                raise TypeError("auth command envelope must be an object")
                        except Exception:
                            await self._reject_auth_command(
                                {},
                                "AUTH_COMMAND_JSON_INVALID",
                                source_partition=getattr(msg, "partition", None),
                                source_offset=getattr(msg, "offset", None),
                            )
                            continue
                        await self._process_auth_command_data(
                            data,
                            source_partition=getattr(msg, "partition", None),
                            source_offset=getattr(msg, "offset", None),
                        )
        finally:
            logger.info(
                "kafka: auth commands consumer stopped handled=%s rejected=%s dlq=%s",
                self._auth_register_handled,
                self._auth_commands_rejected,
                self._auth_commands_dlq,
            )

    async def _reject_auth_event(
        self,
        data: dict[str, Any],
        reason: str,
        *,
        source_partition: int | None = None,
        source_offset: int | None = None,
    ) -> bool:
        """Reject auth events without binding or copying potentially forged payload."""

        self._auth_events_rejected += 1
        raw_event_id = str(data.get("event_id") or "")[:200]
        event_id_hash = hashlib.sha256(raw_event_id.encode("utf-8")).hexdigest()[:16]
        record = {
            "schema_version": 1,
            "event_id_hash": event_id_hash,
            "source_topic": self._topic_auth_events,
            "source_partition": source_partition,
            "source_offset": source_offset,
            "rejected_at": EventEnvelope.now_iso(),
            "reason": reason,
        }
        self._auth_event_dlq_buffer.append(record)
        logger.warning(
            "kafka: auth event rejected reason=%s id_hash=%s offset=%s",
            reason,
            event_id_hash,
            source_offset,
        )
        if not self.enabled:
            self._auth_events_dlq += 1
            return True
        try:
            await self._producer.send_and_wait(
                self._topic_auth_event_dlq,
                value=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                key=event_id_hash.encode("utf-8"),
            )
            self._auth_events_dlq += 1
            return True
        except Exception:
            logger.error("kafka: auth event DLQ publish failed reason=%s", reason)
            return False

    async def _handle_auth_event_envelope(self, envelope: EventEnvelope) -> bool:
        from prodavan.application.auth.event_replay import claim_auth_event_once
        from prodavan.application.auth.register import AUTH_USER_REGISTERED

        verify_auth_event_envelope(envelope, signing_secret=self._auth_event_secret())
        try:
            claimed = await claim_auth_event_once(envelope.event_id)
        except Exception:
            raise AuthEventError(
                "AUTH_EVENT_REPLAY_STORE_UNAVAILABLE",
                "Auth event replay store is unavailable",
            ) from None
        if not claimed:
            raise AuthEventReplay("AUTH_EVENT_REPLAYED", "Auth event was already consumed")
        if envelope.event_type != AUTH_USER_REGISTERED:
            return False
        # Bind in-process only after separate event provenance + one-time claim.
        await self._apply_auth_bind(envelope.payload or {})
        return True

    async def _process_auth_event_data(
        self,
        data: dict[str, Any],
        *,
        source_partition: int | None = None,
        source_offset: int | None = None,
    ) -> bool:
        """Verify raw event HMAC/freshness before identity bind."""

        try:
            envelope = verified_auth_event_envelope(
                data, signing_secret=self._auth_event_secret()
            )
        except AuthEventError as exc:
            await self._reject_auth_event(
                data,
                exc.code,
                source_partition=source_partition,
                source_offset=source_offset,
            )
            return False
        try:
            await self._handle_auth_event_envelope(envelope)
            return True
        except AuthEventError as exc:
            await self._reject_auth_event(
                data,
                exc.code,
                source_partition=source_partition,
                source_offset=source_offset,
            )
            return False
        except Exception as exc:
            logger.error(
                "kafka: auth event handler failed error_type=%s",
                type(exc).__name__,
            )
            await self._reject_auth_event(
                data,
                "AUTH_EVENT_HANDLER_FAILED",
                source_partition=source_partition,
                source_offset=source_offset,
            )
            return False

    async def _consume_auth_events(self, stop: asyncio.Event) -> None:
        assert self._auth_events_consumer is not None

        try:
            while not stop.is_set():
                try:
                    batch = await self._auth_events_consumer.getmany(timeout_ms=500, max_records=20)
                except Exception as exc:
                    if stop.is_set():
                        break
                    logger.error(
                        "kafka: auth events getmany failed error_type=%s", type(exc).__name__
                    )
                    await asyncio.sleep(1.0)
                    continue
                if not batch:
                    continue
                for _tp, messages in batch.items():
                    for msg in messages:
                        try:
                            data = json.loads(msg.value.decode("utf-8"))
                            if not isinstance(data, dict):
                                raise TypeError("auth event envelope must be an object")
                        except Exception:
                            await self._reject_auth_event(
                                {},
                                "AUTH_EVENT_JSON_INVALID",
                                source_partition=getattr(msg, "partition", None),
                                source_offset=getattr(msg, "offset", None),
                            )
                            continue
                        await self._process_auth_event_data(
                            data,
                            source_partition=getattr(msg, "partition", None),
                            source_offset=getattr(msg, "offset", None),
                        )
        finally:
            logger.info(
                "kafka: auth events consumer stopped binds=%s rejected=%s dlq=%s",
                self._auth_bind_enqueues,
                self._auth_events_rejected,
                self._auth_events_dlq,
            )

    async def _consume_relation_events(self, stop: asyncio.Event) -> None:
        assert self._relation_events_consumer is not None
        from prodavan.application.relations.commands import handle_relation_event_envelope
        from prodavan.core.events.envelope import EventEnvelope

        try:
            while not stop.is_set():
                try:
                    batch = await self._relation_events_consumer.getmany(
                        timeout_ms=500, max_records=20
                    )
                except Exception:
                    if stop.is_set():
                        break
                    logger.exception("kafka: relation events getmany failed")
                    await asyncio.sleep(1.0)
                    continue
                if not batch:
                    continue
                for _tp, messages in batch.items():
                    for msg in messages:
                        try:
                            data = json.loads(msg.value.decode("utf-8"))
                        except Exception:
                            logger.warning("kafka: skip bad relation event offset=%s", msg.offset)
                            continue
                        if data.get("bus") != "relation_event":
                            continue
                        try:
                            envelope = EventEnvelope(
                                bus="relation_event",
                                event_id=str(data.get("event_id") or ""),
                                event_type=str(data.get("event_type") or ""),
                                occurred_at=str(data.get("occurred_at") or EventEnvelope.now_iso()),
                                company_id=data.get("company_id"),
                                project_id=data.get("project_id"),
                                cabinet_id=data.get("cabinet_id"),
                                payload=dict(data.get("payload") or {}),
                                schema_version=int(data.get("schema_version") or 1),
                            )
                            await handle_relation_event_envelope(envelope)
                            self._relation_events_handled += 1
                        except Exception:
                            logger.exception(
                                "kafka: relation event handle failed id=%s",
                                data.get("event_id"),
                            )
        finally:
            logger.info(
                "kafka: relation events consumer stopped handled=%s",
                self._relation_events_handled,
            )

    async def _consume_platform_jobs(self, stop: asyncio.Event) -> None:
        assert self._platform_jobs_consumer is not None
        from prodavan.core.jobs.rematerialize_bus import handle_rematerialize_requested_envelope

        try:
            while not stop.is_set():
                try:
                    batch = await self._platform_jobs_consumer.getmany(timeout_ms=500, max_records=50)
                except Exception:
                    if stop.is_set():
                        break
                    logger.exception("kafka: platform jobs consumer getmany failed")
                    await asyncio.sleep(1.0)
                    continue
                if not batch:
                    continue
                for _tp, messages in batch.items():
                    for msg in messages:
                        try:
                            data = json.loads(msg.value.decode("utf-8"))
                        except Exception:
                            logger.warning("kafka: skip bad platform job message offset=%s", msg.offset)
                            continue
                        project_id = handle_rematerialize_requested_envelope(data)
                        if project_id:
                            self._enqueue_rematerialize(project_id)
        finally:
            logger.info(
                "kafka: platform jobs consumer stopped enqueues=%s",
                self._rematerialize_enqueues,
            )

    async def _ensure_topics(self) -> None:
        """Best-effort create platform + project_trigger + auth + relation topics."""
        topics = [
            self._topic_platform,
            self._topic_triggers,
            self._topic_auth_commands,
            self._topic_auth_command_dlq,
            self._topic_auth_events,
            self._topic_auth_event_dlq,
            self._topic_relation_events,
            self._topic_metrics_events,
            self._topic_document_events,
            self._topic_search_events,
        ]
        try:
            from aiokafka.admin import AIOKafkaAdminClient, NewTopic

            admin = AIOKafkaAdminClient(
                bootstrap_servers=self._bootstrap,
                client_id=f"{self._client_id}-admin",
            )
            await admin.start()
            try:
                existing = await admin.list_topics()
                missing = [t for t in topics if t not in existing]
                if not missing:
                    return
                await admin.create_topics(
                    [NewTopic(name=t, num_partitions=1, replication_factor=1) for t in missing]
                )
                logger.info("kafka: created topics %s", missing)
            finally:
                await admin.close()
        except Exception:
            # Dev Redpanda often auto-creates on first produce; do not fail startup.
            logger.warning("kafka: topic ensure skipped (will rely on auto-create)", exc_info=True)

    async def _consume_loop(self, stop: asyncio.Event) -> None:
        logger.info(
            "kafka: consumer started topic=%s group=%s mode=%s",
            self._topic_triggers,
            self._consumer_group,
            self._consumer_mode,
        )
        if self._consumer_mode == "dispatch":
            await self._consume_loop_dispatch(stop)
        else:
            await self._consume_loop_kick(stop)

    async def startup(self) -> None:
        set_kafka_manager(self)
        if self._consumer_enabled:
            try:
                # Auth consumers must never start with a missing/dev-fallback key.
                validate_auth_command_signing_secret(self._auth_command_secret())
            except AuthCommandError:
                set_kafka_manager(None)
                raise
            try:
                validate_auth_event_signing_secret(self._auth_event_secret())
            except AuthEventError:
                set_kafka_manager(None)
                raise
        if not self._enabled:
            logger.info("kafka: disabled (KAFKA_ENABLED=false); dual-write uses memory buffer only")
            return
        if not self._bootstrap:
            msg = "KAFKA_ENABLED requires KAFKA_BOOTSTRAP_SERVERS"
            if self._required:
                raise RuntimeError(msg)
            logger.warning("%s — buffer-only mode", msg)
            return
        # After node/WSL reboot CoreDNS + kafka STS often lag; retry before fail-fast.
        attempts = 30 if self._required else 3
        delay_sec = 2.0
        last_exc: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                from aiokafka import AIOKafkaProducer

                self._producer = AIOKafkaProducer(
                    bootstrap_servers=self._bootstrap,
                    client_id=self._client_id,
                    acks="all",
                    enable_idempotence=True,
                )
                await self._producer.start()
                await self._ensure_topics()
                logger.info(
                    "kafka: producer started servers=%s topics=%s,%s,%s,%s,%s,%s,%s (attempt %s/%s)",
                    self._bootstrap,
                    self._topic_platform,
                    self._topic_triggers,
                    self._topic_auth_commands,
                    self._topic_auth_events,
                    self._topic_relation_events,
                    self._topic_metrics_events,
                    self._topic_document_events,
                    attempt,
                    attempts,
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                self._producer = None
                logger.warning(
                    "kafka: producer startup attempt %s/%s failed: %s",
                    attempt,
                    attempts,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(delay_sec)
        if last_exc is not None:
            logger.error("kafka: producer startup failed after %s attempts", attempts, exc_info=last_exc)
            self._producer = None
            if self._required:
                set_kafka_manager(None)
                raise last_exc
            logger.warning("kafka: falling back to buffer-only")
            return

        if not self._consumer_enabled:
            return
        try:
            from aiokafka import AIOKafkaConsumer

            self._stop = asyncio.Event()
            self._consumer = AIOKafkaConsumer(
                self._topic_triggers,
                bootstrap_servers=self._bootstrap,
                client_id=f"{self._client_id}-consumer",
                group_id=self._consumer_group,
                enable_auto_commit=True,
                auto_offset_reset="latest",
            )
            await self._consumer.start()
            self._consume_task = asyncio.create_task(
                self._consume_loop(self._stop),
                name="prodavan-kafka-trigger-consumer",
            )

            self._auth_commands_consumer = AIOKafkaConsumer(
                self._topic_auth_commands,
                bootstrap_servers=self._bootstrap,
                client_id=f"{self._client_id}-auth-cmd",
                group_id=self._auth_commands_group,
                enable_auto_commit=True,
                auto_offset_reset="earliest",
            )
            await self._auth_commands_consumer.start()
            self._auth_commands_task = asyncio.create_task(
                self._consume_auth_commands(self._stop),
                name="prodavan-kafka-auth-commands",
            )

            self._auth_events_consumer = AIOKafkaConsumer(
                self._topic_auth_events,
                bootstrap_servers=self._bootstrap,
                client_id=f"{self._client_id}-auth-evt",
                group_id=self._auth_events_group,
                enable_auto_commit=True,
                auto_offset_reset="earliest",
            )
            await self._auth_events_consumer.start()
            self._auth_events_task = asyncio.create_task(
                self._consume_auth_events(self._stop),
                name="prodavan-kafka-auth-events",
            )

            self._relation_events_consumer = AIOKafkaConsumer(
                self._topic_relation_events,
                bootstrap_servers=self._bootstrap,
                client_id=f"{self._client_id}-rel-evt",
                group_id=self._relation_events_group,
                enable_auto_commit=True,
                auto_offset_reset="earliest",
            )
            await self._relation_events_consumer.start()
            self._relation_events_task = asyncio.create_task(
                self._consume_relation_events(self._stop),
                name="prodavan-kafka-relation-events",
            )

            if self._rematerialize_via_bus:
                self._platform_jobs_consumer = AIOKafkaConsumer(
                    self._topic_platform,
                    bootstrap_servers=self._bootstrap,
                    client_id=f"{self._client_id}-platform-jobs",
                    group_id=self._platform_jobs_group,
                    enable_auto_commit=True,
                    auto_offset_reset="latest",
                )
                await self._platform_jobs_consumer.start()
                self._platform_jobs_task = asyncio.create_task(
                    self._consume_platform_jobs(self._stop),
                    name="prodavan-kafka-platform-jobs",
                )
        except Exception:
            logger.exception("kafka: consumer startup failed (producer still up)")
            self._consumer = None

    async def shutdown(self) -> None:
        if self._stop is not None:
            self._stop.set()
        for task_attr in (
            "_consume_task",
            "_auth_commands_task",
            "_auth_events_task",
            "_relation_events_task",
            "_platform_jobs_task",
        ):
            task = getattr(self, task_attr)
            if task is not None:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (TimeoutError, asyncio.CancelledError):
                    task.cancel()
                setattr(self, task_attr, None)
        self._stop = None
        for cons_attr in (
            "_consumer",
            "_auth_commands_consumer",
            "_auth_events_consumer",
            "_relation_events_consumer",
            "_platform_jobs_consumer",
        ):
            cons = getattr(self, cons_attr)
            if cons is not None:
                try:
                    await cons.stop()
                except Exception:
                    logger.exception("kafka: consumer stop failed (%s)", cons_attr)
                setattr(self, cons_attr, None)
        if self._producer is not None:
            try:
                await self._producer.stop()
            except Exception:
                logger.exception("kafka: producer stop failed")
            self._producer = None
        if get_kafka_manager_optional() is self:
            set_kafka_manager(None)

    async def health(self) -> bool | None:
        if not self._enabled:
            return None
        if not self._bootstrap:
            return False
        if self._producer is None:
            return False
        if self._consumer_enabled and not self.consumer_running:
            return False
        return True
