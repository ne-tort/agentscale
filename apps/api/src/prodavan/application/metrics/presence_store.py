"""Redis SET/DEL/MGET for employee and company presence keys."""

from __future__ import annotations

import logging
from typing import Literal

from prodavan.config.settings import settings

logger = logging.getLogger(__name__)

PresenceKind = Literal["employee", "company"]


def presence_key(kind: PresenceKind, entity_id: str) -> str:
    return f"presence:{kind}:{entity_id.strip()}"


def _ttl_sec() -> int:
    return max(1, int(settings.metrics_presence_ttl_sec))


async def set_presence(kind: PresenceKind, entity_id: str) -> bool:
    """Mark principal online with TTL refresh."""
    from prodavan.core.infra.redis_manager import get_redis_manager

    entity = (entity_id or "").strip()
    if not entity:
        return False
    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return False
    try:
        await mgr.client.set(presence_key(kind, entity), "1", ex=_ttl_sec())
        return True
    except Exception:
        logger.exception("presence set failed kind=%s id=%s", kind, entity)
        return False


async def clear_presence(kind: PresenceKind, entity_id: str) -> bool:
    """Remove presence key on logout."""
    from prodavan.core.infra.redis_manager import get_redis_manager

    entity = (entity_id or "").strip()
    if not entity:
        return False
    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return False
    try:
        await mgr.client.delete(presence_key(kind, entity))
        return True
    except Exception:
        logger.exception("presence clear failed kind=%s id=%s", kind, entity)
        return False


async def batch_is_online(kind: PresenceKind, entity_ids: list[str]) -> dict[str, bool]:
    """Batch MGET — missing Redis → all False."""
    from prodavan.core.infra.redis_manager import get_redis_manager

    ids = [e.strip() for e in entity_ids if (e or "").strip()]
    if not ids:
        return {}
    mgr = get_redis_manager()
    if mgr is None or not mgr.enabled:
        return {eid: False for eid in ids}
    keys = [presence_key(kind, eid) for eid in ids]
    try:
        values = await mgr.client.mget(keys)
        return {eid: bool(val) for eid, val in zip(ids, values, strict=True)}
    except Exception:
        logger.exception("presence mget failed kind=%s count=%s", kind, len(ids))
        return {eid: False for eid in ids}


async def count_online(kind: PresenceKind, entity_ids: list[str]) -> int:
    online = await batch_is_online(kind, entity_ids)
    return sum(1 for v in online.values() if v)
