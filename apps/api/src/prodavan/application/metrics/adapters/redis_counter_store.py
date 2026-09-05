"""Redis counters, entity links, storage snapshots, overview series."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Durable gauges — no short TTL (survive presence churn).
_COUNTER_TTL = 60 * 60 * 24 * 90  # 90d refresh-on-write
_SERIES_TTL = 60 * 60 * 24 * 14
_SERIES_MAX = 336  # ~14d hourly


def _counter_key(entity_type: str, entity_id: str, metric: str) -> str:
    return f"metrics:counter:{entity_type}:{entity_id}:{metric}"


def _link_children_key(parent_type: str, parent_id: str, child_type: str) -> str:
    return f"metrics:link:{parent_type}:{parent_id}:children:{child_type}"


def _link_parent_key(child_type: str, child_id: str, parent_type: str) -> str:
    return f"metrics:link:{child_type}:{child_id}:parent:{parent_type}"


def _storage_key(entity_type: str, entity_id: str) -> str:
    return f"metrics:storage:{entity_type}:{entity_id}"


def _series_key(entity_type: str, entity_id: str, metric: str, window: str) -> str:
    return f"metrics:cseries:{entity_type}:{entity_id}:{metric}:{window}"


class RedisCounterStore:
    async def get_counter(self, entity_type: str, entity_id: str, metric: str) -> int | None:
        raw = await self._get(_counter_key(entity_type, entity_id, metric))
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def set_counter(self, entity_type: str, entity_id: str, metric: str, value: int) -> None:
        await self._set(_counter_key(entity_type, entity_id, metric), str(max(0, int(value))), ex=_COUNTER_TTL)

    async def incr_counter(
        self, entity_type: str, entity_id: str, metric: str, delta: int
    ) -> int:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return 0
        key = _counter_key(entity_type, entity_id, metric)
        try:
            val = await mgr.client.incrby(key, int(delta))
            await mgr.client.expire(key, _COUNTER_TTL)
            if val < 0:
                await mgr.client.set(key, "0", ex=_COUNTER_TTL)
                return 0
            return int(val)
        except Exception:
            logger.exception("counter incr failed key=%s", key)
            return 0

    async def put_link(
        self,
        *,
        parent_type: str,
        parent_id: str,
        child_type: str,
        child_id: str,
    ) -> None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return
        try:
            await mgr.client.sadd(_link_children_key(parent_type, parent_id, child_type), child_id)
            await mgr.client.set(
                _link_parent_key(child_type, child_id, parent_type),
                parent_id,
                ex=_COUNTER_TTL,
            )
        except Exception:
            logger.exception("link put failed parent=%s/%s child=%s/%s", parent_type, parent_id, child_type, child_id)

    async def remove_link(
        self,
        *,
        parent_type: str,
        parent_id: str,
        child_type: str,
        child_id: str,
    ) -> None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return
        try:
            await mgr.client.srem(_link_children_key(parent_type, parent_id, child_type), child_id)
            await mgr.client.delete(_link_parent_key(child_type, child_id, parent_type))
        except Exception:
            logger.exception("link remove failed")

    async def get_parent(self, child_type: str, child_id: str, parent_type: str) -> str | None:
        raw = await self._get(_link_parent_key(child_type, child_id, parent_type))
        return str(raw).strip() if raw else None

    async def list_children(self, parent_type: str, parent_id: str, child_type: str) -> list[str]:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return []
        try:
            members = await mgr.client.smembers(_link_children_key(parent_type, parent_id, child_type))
            return sorted(str(m) for m in (members or set()))
        except Exception:
            logger.exception("list children failed")
            return []

    async def put_storage_snapshot(self, entity_type: str, entity_id: str, bytes_value: int) -> None:
        await self.set_counter(entity_type, entity_id, "storage_bytes", max(0, int(bytes_value)))
        await self._set(
            _storage_key(entity_type, entity_id),
            json.dumps({"bytes": max(0, int(bytes_value)), "at": datetime.now(UTC).isoformat()}),
            ex=_COUNTER_TTL,
        )

    async def append_counter_series(
        self,
        entity_type: str,
        entity_id: str,
        metric: str,
        *,
        value: int,
        at: str | None = None,
        window: str = "7d",
    ) -> None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return
        key = _series_key(entity_type, entity_id, metric, window)
        point = {"at": at or datetime.now(UTC).isoformat(), "value": int(value)}
        try:
            raw = await mgr.client.get(key)
            items: list[dict[str, Any]] = json.loads(raw) if raw else []
            items.append(point)
            if len(items) > _SERIES_MAX:
                items = items[-_SERIES_MAX:]
            await mgr.client.set(key, json.dumps(items), ex=_SERIES_TTL)
        except Exception:
            logger.exception("counter series append failed key=%s", key)

    async def get_counter_series(
        self, entity_type: str, entity_id: str, metric: str, *, window: str = "7d"
    ) -> list[dict[str, Any]]:
        raw = await self._get(_series_key(entity_type, entity_id, metric, window))
        if not raw:
            return []
        try:
            items = json.loads(raw)
            return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
        except Exception:
            return []

    async def _set(self, key: str, value: str, *, ex: int) -> None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return
        try:
            await mgr.client.set(key, value, ex=ex)
        except Exception:
            logger.exception("redis set failed key=%s", key)

    async def _get(self, key: str) -> str | None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return None
        try:
            raw = await mgr.client.get(key)
            if raw is None:
                return None
            return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        except Exception:
            logger.exception("redis get failed key=%s", key)
            return None


def build_counter_store() -> RedisCounterStore:
    return RedisCounterStore()
