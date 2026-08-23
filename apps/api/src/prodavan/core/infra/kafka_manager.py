"""KafkaManager — producer facade + LifespanResource (C-EVENT-BUS)."""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any

from prodavan.core.events.envelope import EventEnvelope
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

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
    """Publish EventEnvelope to Kafka topics (or in-memory buffer when disabled).

    Consumer cutover (replace PG drain) is a later hole — this wave is dual-write publish.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        bootstrap_servers: str | None = None,
        client_id: str = "prodavan-api",
        topic_platform_events: str = "prodavan.platform.events",
        topic_project_triggers: str = "prodavan.project.triggers",
        required: bool = False,
        buffer_size: int = 200,
    ) -> None:
        self._enabled = enabled
        self._bootstrap = (bootstrap_servers or "").strip() or None
        self._client_id = client_id
        self._topic_platform = topic_platform_events
        self._topic_triggers = topic_project_triggers
        self._required = required
        self._producer: Any = None
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max(1, buffer_size))

    @property
    def name(self) -> str:
        return "kafka"

    @property
    def enabled(self) -> bool:
        return bool(self._enabled and self._bootstrap and self._producer is not None)

    @property
    def buffering_only(self) -> bool:
        """True when dual-write records locally but does not talk to Kafka."""
        return not self.enabled

    def topic_for(self, bus: str) -> str:
        if bus == "platform":
            return self._topic_platform
        if bus == "project_trigger":
            return self._topic_triggers
        raise ValueError(f"unknown bus: {bus}")

    def recent_envelopes(self) -> list[dict[str, Any]]:
        return list(self._buffer)

    def clear_buffer(self) -> None:
        self._buffer.clear()

    async def publish(self, envelope: EventEnvelope) -> bool:
        payload = envelope.to_dict()
        self._buffer.append(payload)
        if not self.enabled:
            logger.debug(
                "kafka: buffer-only bus=%s type=%s id=%s",
                envelope.bus,
                envelope.event_type,
                envelope.event_id,
            )
            return True
        topic = self.topic_for(envelope.bus)
        key = (envelope.project_id or envelope.company_id or envelope.event_id).encode("utf-8")
        value = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        try:
            await self._producer.send_and_wait(topic, value=value, key=key)
            return True
        except Exception:
            logger.exception("kafka: publish failed topic=%s id=%s", topic, envelope.event_id)
            if self._required:
                raise
            return False

    async def startup(self) -> None:
        set_kafka_manager(self)
        if not self._enabled:
            logger.info("kafka: disabled (KAFKA_ENABLED=false); dual-write uses memory buffer only")
            return
        if not self._bootstrap:
            msg = "KAFKA_ENABLED requires KAFKA_BOOTSTRAP_SERVERS"
            if self._required:
                raise RuntimeError(msg)
            logger.warning("%s — buffer-only mode", msg)
            return
        try:
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                client_id=self._client_id,
                acks="all",
            )
            await self._producer.start()
            logger.info(
                "kafka: producer started servers=%s topics=%s,%s",
                self._bootstrap,
                self._topic_platform,
                self._topic_triggers,
            )
        except Exception:
            logger.exception("kafka: producer startup failed")
            self._producer = None
            if self._required:
                set_kafka_manager(None)
                raise
            logger.warning("kafka: falling back to buffer-only")

    async def shutdown(self) -> None:
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
        return self._producer is not None
