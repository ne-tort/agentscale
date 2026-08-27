"""Kafka consumer for platform auth events → Redis presence (metrics BC)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from prodavan.config.settings import settings
from prodavan.core.events.envelope import EventEnvelope
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)


class MetricsPresenceConsumerResource(LifespanResource):
    """Subscribe to platform events topic when kafka consumer is enabled."""

    def __init__(self) -> None:
        self._consumer: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._handled: int = 0

    @property
    def name(self) -> str:
        return "metrics_presence_consumer"

    async def startup(self) -> None:
        if not settings.kafka_enabled or not settings.kafka_consumer_enabled:
            logger.info("metrics presence consumer: disabled")
            return
        bootstrap = (settings.kafka_bootstrap_servers or "").strip()
        if not bootstrap:
            logger.warning("metrics presence consumer: no bootstrap servers")
            return
        try:
            from aiokafka import AIOKafkaConsumer

            self._stop = asyncio.Event()
            self._consumer = AIOKafkaConsumer(
                settings.kafka_topic_platform_events,
                bootstrap_servers=bootstrap,
                client_id=f"{settings.kafka_client_id}-metrics-presence",
                group_id=settings.kafka_metrics_presence_group,
                enable_auto_commit=True,
                auto_offset_reset="latest",
            )
            await self._consumer.start()
            self._task = asyncio.create_task(
                self._consume_loop(self._stop),
                name="prodavan-metrics-presence-consumer",
            )
            logger.info(
                "metrics presence consumer: started topic=%s group=%s",
                settings.kafka_topic_platform_events,
                settings.kafka_metrics_presence_group,
            )
        except Exception:
            logger.exception("metrics presence consumer: startup failed")
            self._consumer = None

    async def _consume_loop(self, stop: asyncio.Event) -> None:
        from prodavan.application.metrics.consumer import handle_platform_envelope

        assert self._consumer is not None
        try:
            while not stop.is_set():
                try:
                    batch = await self._consumer.getmany(timeout_ms=500, max_records=50)
                except Exception:
                    if stop.is_set():
                        break
                    logger.exception("metrics presence consumer: getmany failed")
                    await asyncio.sleep(1.0)
                    continue
                if not batch:
                    continue
                for _tp, messages in batch.items():
                    for msg in messages:
                        try:
                            data = json.loads(msg.value.decode("utf-8"))
                            envelope = EventEnvelope(
                                bus=data.get("bus") or "platform",
                                event_id=str(data.get("event_id") or ""),
                                event_type=str(data.get("event_type") or ""),
                                occurred_at=str(data.get("occurred_at") or EventEnvelope.now_iso()),
                                company_id=data.get("company_id"),
                                project_id=data.get("project_id"),
                                cabinet_id=data.get("cabinet_id"),
                                payload=dict(data.get("payload") or {}),
                                schema_version=int(data.get("schema_version") or 1),
                            )
                        except Exception:
                            logger.warning(
                                "metrics presence consumer: skip bad message offset=%s",
                                msg.offset,
                            )
                            continue
                        try:
                            await handle_platform_envelope(envelope)
                            self._handled += 1
                        except Exception:
                            logger.exception(
                                "metrics presence consumer: handle failed type=%s",
                                data.get("event_type"),
                            )
        finally:
            logger.info("metrics presence consumer: stopped handled=%s", self._handled)

    async def shutdown(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        self._stop = None
        if self._consumer is not None:
            try:
                await self._consumer.stop()
            except Exception:
                logger.exception("metrics presence consumer: stop failed")
            self._consumer = None

    async def health(self) -> bool | None:
        if not settings.kafka_enabled or not settings.kafka_consumer_enabled:
            return None
        if self._consumer is None:
            return False
        if self._task is None or self._task.done():
            return False
        return True
