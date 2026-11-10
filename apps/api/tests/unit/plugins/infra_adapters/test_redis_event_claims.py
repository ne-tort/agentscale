"""Redis claim adapter tests; no Redis/network is started."""
from __future__ import annotations

from typing import Any

import pytest

from prodavan.application.plugins.events.contracts import IdempotencyKey
from prodavan.infrastructure.plugins.adapters.redis_event_claims import RedisDeliveryClaimStore


class FakeRedis:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Any, ...]] = []

    async def eval(self, *args: Any) -> Any:
        self.calls.append(args)
        if not self.responses:
            raise AssertionError("unexpected Redis eval")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_acquire_uses_atomic_lease_generation_and_safe_cluster_keys() -> None:
    redis = FakeRedis([[1, 0], [0, 0]])
    store = RedisDeliveryClaimStore(redis=redis, prefix="test:delivery:")
    key = IdempotencyKey("sub-secret", "source-secret")

    claim = await store.acquire(key, lease_seconds=2.5)
    assert claim is not None
    assert claim.key == key
    assert claim.generation == 1
    assert claim.completed is False
    assert claim.owner_token

    assert await store.acquire(key, lease_seconds=2.5) is None
    first_call = redis.calls[0]
    keys = first_call[2:5]
    assert all("sub-secret" not in item and "source-secret" not in item for item in keys)
    assert all("{" in item and "}" in item for item in keys), "Redis Cluster hash tag"
    assert first_call[6] == 2500


@pytest.mark.asyncio
async def test_completed_marker_is_returned_without_replaying_backend_identity() -> None:
    redis = FakeRedis([[4, 1]])
    store = RedisDeliveryClaimStore(redis=redis)
    key = IdempotencyKey("sub", "source")
    claim = await store.acquire(key, lease_seconds=1)
    assert claim is not None
    assert claim.generation == 4
    assert claim.completed is True


@pytest.mark.asyncio
async def test_renew_complete_release_require_owner_token_and_ttl() -> None:
    redis = FakeRedis([[1, 0], 1, 1, 1])
    store = RedisDeliveryClaimStore(redis=redis)
    key = IdempotencyKey("sub", "source")
    claim = await store.acquire(key, lease_seconds=1)
    assert claim is not None

    assert await store.renew(claim) is True
    assert await store.complete(claim) is True
    assert await store.release(claim) is True
    assert len(redis.calls) == 4


@pytest.mark.asyncio
async def test_lost_owner_returns_false_not_success() -> None:
    redis = FakeRedis([[1, 0], 0, 0, 0])
    store = RedisDeliveryClaimStore(redis=redis)
    key = IdempotencyKey("sub", "source")
    claim = await store.acquire(key, lease_seconds=1)
    assert claim is not None
    assert await store.renew(claim) is False
    assert await store.complete(claim) is False
    assert await store.release(claim) is False


@pytest.mark.asyncio
async def test_invalid_lease_is_rejected_before_redis_call() -> None:
    redis = FakeRedis([])
    store = RedisDeliveryClaimStore(redis=redis)
    with pytest.raises(ValueError):
        await store.acquire(IdempotencyKey("sub", "source"), lease_seconds=0)
    assert redis.calls == []
