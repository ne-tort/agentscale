"""Shared-Redis one-time claims for fresh, correctly signed auth events."""

from __future__ import annotations

import hashlib
from typing import Any

from prodavan.core.infra.redis_manager import get_redis_manager

_KEY_PREFIX = "prodavan:auth:event:seen:"


class AuthEventReplayStoreError(RuntimeError):
    pass


def _client() -> Any:
    manager = get_redis_manager()
    if manager is None or not manager.enabled:
        raise AuthEventReplayStoreError("shared Redis is required for auth event replay protection")
    try:
        return manager.client
    except Exception:
        raise AuthEventReplayStoreError("shared Redis is not started") from None


async def claim_auth_event_once(event_id: str) -> bool:
    """Claim an id through its freshness window; a duplicate can never bind."""

    from prodavan.config.settings import settings

    # After this TTL the signed occurred_at is necessarily outside the accepted
    # freshness window, so expiry of the marker cannot make a replay valid.
    ttl = max(1, int(settings.auth_event_max_age_seconds)) + 60
    event_hash = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    try:
        claimed = await _client().set(f"{_KEY_PREFIX}{event_hash}", "1", nx=True, ex=ttl)
    except AuthEventReplayStoreError:
        raise
    except Exception:
        raise AuthEventReplayStoreError("could not claim auth event") from None
    return bool(claimed)
