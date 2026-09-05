"""Metrics BC Kafka consumer — platform presence + metrics samples."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from prodavan.config.settings import settings
from prodavan.core.events.envelope import EventEnvelope
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)


class MetricsConsumerResource(LifespanResource):
    """Subscribe to platform auth events and metrics sample topic."""

    def __init__(self) -> None:
        self._consumers: list[Any] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._stop: asyncio.Event | None = None
        self._handled: int = 0

    @property
    def name(self) -> str:
        return "metrics_consumer"

    async def startup(self) -> None:
        if not settings.kafka_enabled or not settings.kafka_consumer_enabled:
            logger.info("metrics consumer: disabled")
            return
        bootstrap = (settings.kafka_bootstrap_servers or "").strip()
        if not bootstrap:
            logger.warning("metrics consumer: no bootstrap servers")
            return
        try:
            from aiokafka import AIOKafkaConsumer

            self._stop = asyncio.Event()
            topics = [
                settings.kafka_topic_platform_events,
                settings.kafka_topic_metrics_events,
                settings.kafka_topic_relation_events,
            ]
            consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=bootstrap,
                client_id=f"{settings.kafka_client_id}-metrics",
                group_id=settings.kafka_metrics_group,
                enable_auto_commit=True,
                auto_offset_reset="latest",
            )
            await consumer.start()
            self._consumers.append(consumer)
            self._tasks.append(
                asyncio.create_task(self._consume_loop(consumer, self._stop), name="prodavan-metrics-consumer")
            )
            logger.info(
                "metrics consumer: started topics=%s group=%s",
                topics,
                settings.kafka_metrics_group,
            )
        except Exception:
            logger.exception("metrics consumer: startup failed")
            self._consumers.clear()

    async def _consume_loop(self, consumer: Any, stop: asyncio.Event) -> None:
        from prodavan.application.metrics.consumer.registry import handle_metrics_envelope

        try:
            while not stop.is_set():
                try:
                    batch = await consumer.getmany(timeout_ms=500, max_records=50)
                except Exception:
                    if stop.is_set():
                        break
                    logger.exception("metrics consumer: getmany failed")
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
                                "metrics consumer: skip bad message offset=%s",
                                msg.offset,
                            )
                            continue
                        try:
                            await handle_metrics_envelope(envelope)
                            self._handled += 1
                        except Exception:
                            logger.exception(
                                "metrics consumer: handle failed type=%s bus=%s",
                                data.get("event_type"),
                                data.get("bus"),
                            )
        finally:
            logger.info("metrics consumer: stopped handled=%s", self._handled)

    async def shutdown(self) -> None:
        if self._stop is not None:
            self._stop.set()
        for task in self._tasks:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
        self._tasks.clear()
        self._stop = None
        for consumer in self._consumers:
            try:
                await consumer.stop()
            except Exception:
                logger.exception("metrics consumer: stop failed")
        self._consumers.clear()

    async def health(self) -> bool | None:
        if not settings.kafka_enabled or not settings.kafka_consumer_enabled:
            return None
        if not self._consumers:
            return False
        if not self._tasks or any(task.done() for task in self._tasks):
            return False
        return True
