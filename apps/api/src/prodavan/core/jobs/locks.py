"""Coalesce helpers for periodic Celery jobs (C-CACHE / C-JOBS)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from prodavan.core.infra.cache import acquire_lock, cache_key, release_lock
from prodavan.core.infra.redis_manager import get_redis_manager

logger = logging.getLogger(__name__)


async def run_with_job_lock[T](
    job: str,
    *,
    ttl_sec: int,
    fn: Callable[[], Awaitable[T]],
) -> T | dict[str, Any]:
    """Run ``fn`` under a short Redis lock; skip if lock held.

    When Redis is disabled, runs ``fn`` without locking (dev / transitional).
    """
    redis = get_redis_manager()
    key = cache_key("lock", "job", job)
    token: str | None = None
    if redis is not None and redis.enabled:
        token = await acquire_lock(key, ttl_sec=max(1, int(ttl_sec)))
        if token is None:
            logger.debug("job lock held — skip job=%s", job)
            return {"ok": False, "skipped": True, "reason": "lock_held", "job": job}
    try:
        return await fn()
    finally:
        if token is not None:
            await release_lock(key, token)
