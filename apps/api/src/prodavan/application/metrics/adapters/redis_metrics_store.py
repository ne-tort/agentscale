"""Redis-backed hot metrics store."""

from __future__ import annotations

import json
import logging
from typing import Any

from prodavan.config.settings import settings

logger = logging.getLogger(__name__)


def _latest_key(project_id: str) -> str:
    return f"metrics:project:{project_id}:latest"


def _series_key(project_id: str, window: str) -> str:
    return f"metrics:project:{project_id}:series:{window}"


def _pod_last_key(pod_id: str) -> str:
    return f"metrics:pod:{pod_id}:last"


def _dedup_key(event_id: str) -> str:
    return f"metrics:dedup:{event_id}"


def _ttl_sec() -> int:
    return max(60, int(settings.metrics_sample_ttl_sec))


class RedisMetricsStore:
    async def put_project_latest(self, project_id: str, sample: dict[str, Any]) -> None:
        await self._set_json(_latest_key(project_id), sample, ex=_ttl_sec())

    async def get_project_latest(self, project_id: str) -> dict[str, Any] | None:
        return await self._get_json(_latest_key(project_id))

    async def append_project_series(
        self, project_id: str, window: str, sample: dict[str, Any], *, max_points: int
    ) -> None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return
        key = _series_key(project_id, window)
        try:
            raw = await mgr.client.get(key)
            items: list[dict[str, Any]] = json.loads(raw) if raw else []
            items.append(sample)
            if len(items) > max_points:
                items = items[-max_points:]
            await mgr.client.set(key, json.dumps(items, default=str), ex=_ttl_sec() * max_points)
        except Exception:
            logger.exception("metrics series append failed project=%s", project_id)

    async def get_project_series(self, project_id: str, window: str) -> list[dict[str, Any]]:
        raw = await self._get_json(_series_key(project_id, window))
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    async def get_pod_last_sample(self, pod_id: str) -> dict[str, Any] | None:
        return await self._get_json(_pod_last_key(pod_id))

    async def put_pod_last_sample(self, pod_id: str, sample: dict[str, Any]) -> None:
        await self._set_json(_pod_last_key(pod_id), sample, ex=_ttl_sec())

    async def mark_event_processed(self, event_id: str) -> bool:
        """Return True if this event_id was not seen before (should process)."""
        from prodavan.core.infra.redis_manager import get_redis_manager

        eid = (event_id or "").strip()
        if not eid:
            return True
        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return True
        key = _dedup_key(eid)
        try:
            created = await mgr.client.set(key, "1", nx=True, ex=_ttl_sec() * 24)
            return bool(created)
        except Exception:
            logger.exception("metrics dedup failed event=%s", eid)
            return True

    async def _set_json(self, key: str, value: dict[str, Any], *, ex: int) -> None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return
        try:
            await mgr.client.set(key, json.dumps(value, default=str), ex=ex)
        except Exception:
            logger.exception("metrics redis set failed key=%s", key)

    async def _get_json(self, key: str) -> Any | None:
        from prodavan.core.infra.redis_manager import get_redis_manager

        mgr = get_redis_manager()
        if mgr is None or not mgr.enabled:
            return None
        try:
            raw = await mgr.client.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            logger.exception("metrics redis get failed key=%s", key)
            return None


def build_metrics_store() -> RedisMetricsStore:
    return RedisMetricsStore()
