"""Six exact-grant quota dimensions through the real Redis/service adapters."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.capability_gateway_service import DecisionCode
from prodavan.application.plugins.capabilities.adapters.cache import RedisCacheAdapter
from prodavan.application.plugins.capabilities.audit import InMemoryAudit
from prodavan.application.plugins.capabilities.contracts import (
    CacheGetRequest,
    CapabilityEnvelope,
)
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.capabilities.quotas import (
    QUOTA_FIELDS,
    PluginQuota,
    PluginQuotaKey,
    QuotaOp,
    QuotaUsage,
)
from prodavan.application.plugins.capabilities.service import PluginCapabilityService
from prodavan.infrastructure.persistence.repositories.plugins.quotas import RedisPluginQuota

pytestmark = [pytest.mark.asyncio, pytest.mark.redis_acceptance]

NOW = datetime(2030, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


@dataclass(frozen=True, slots=True)
class Claims:
    sub: str = "plugin:quota_module:quota_company:quota_instance"
    company_id: str = "quota_company"
    plugin_instance_id: str = "quota_instance"
    artifact_digest: str = DIGEST
    grant_id: str = "quota_grant"
    scopes: frozenset[str] = frozenset({"cache"})
    plugin_id: str = "quota_module"
    manifest_version: str = "1.0.0"
    exp: int = int((NOW + timedelta(minutes=30)).timestamp())


class GrantStore:
    def __init__(self, claims: Claims, limits: PluginQuota) -> None:
        self.record = SimpleNamespace(
            grant_id=claims.grant_id,
            company_id=claims.company_id,
            plugin_id=claims.plugin_id,
            plugin_instance_id=claims.plugin_instance_id,
            artifact_digest=claims.artifact_digest,
            manifest_version=claims.manifest_version,
            capabilities=frozenset({"cache"}),
            quotas={name: getattr(limits, name) for name in QUOTA_FIELDS},
            status="active",
            issued_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=30),
            revoked_at=None,
        )

    async def get_by_grant_id(self, *, grant_id: str) -> Any | None:
        return self.record if grant_id == self.record.grant_id else None


class ActiveRevocation:
    async def is_active(self, grant_id: str, *, now: datetime) -> bool:
        return grant_id == "quota_grant" and now == NOW


class MeteredRedisCacheAdapter(RedisCacheAdapter):
    """Use real RedisCacheAdapter IO with a controlled six-dimension meter."""

    def __init__(
        self,
        *,
        client: Any,
        targeted_op: QuotaOp | None = None,
        targeted_amount: int = 0,
    ) -> None:
        super().__init__(client=client, max_value_bytes=64)
        self.targeted_op = targeted_op
        self.targeted_amount = targeted_amount
        self.meter_calls = 0
        self.get_calls = 0

    def quota_usage(self, request: Any) -> QuotaUsage:
        self.meter_calls += 1
        if self.targeted_op is None:
            return super().quota_usage(request)
        amounts = {op: 0 for op in QuotaOp}
        amounts[QuotaOp.OPS] = 1
        if self.targeted_op is not QuotaOp.OPS:
            amounts[self.targeted_op] = self.targeted_amount
        # OPS keeps the valid per-invocation ops=1 meter; the test body exhausts
        # the one-op grant window through a real allowed invocation instead.
        return QuotaUsage(
            ops=amounts[QuotaOp.OPS],
            rows=amounts[QuotaOp.ROWS],
            bytes=amounts[QuotaOp.BYTES],
            keys=amounts[QuotaOp.KEYS],
            object_bytes=amounts[QuotaOp.OBJECT_BYTES],
            egress_bytes=amounts[QuotaOp.EGRESS_BYTES],
        )

    async def get(self, request: CacheGetRequest) -> Any:
        self.get_calls += 1
        return await super().get(request)


class ObservedRedisQuota:
    """Record decisions while delegating all reservation behavior to Redis."""

    def __init__(self, redis: Any) -> None:
        self.inner = RedisPluginQuota(redis)
        self.decisions: list[Any] = []

    async def enforce(
        self, key: PluginQuotaKey, op: QuotaOp, amount: int, limits: PluginQuota
    ) -> Any:
        decision = await self.inner.enforce(key, op, amount, limits)
        self.decisions.append(decision)
        return decision


def limits_for_test() -> PluginQuota:
    # Deliberately distinct grant values: assertions never assume a global default.
    return PluginQuota(
        ops_per_minute=7,
        max_rows=11,
        max_bytes=13,
        max_keys=17,
        max_object_bytes=19,
        max_egress_bytes=23,
    )


def request_for(claims: Claims, request_id: str) -> CacheGetRequest:
    return CacheGetRequest(
        key="acceptance-missing-key",
        envelope=CapabilityEnvelope(
            request_id=request_id,
            company_id=claims.company_id,
            subject_id=claims.sub,
            plugin_instance_id=claims.plugin_instance_id,
            artifact_digest=claims.artifact_digest,
            grant_id=claims.grant_id,
        ),
    )


def service_for(
    redis: Any,
    claims: Claims,
    limits: PluginQuota,
    *,
    targeted_op: QuotaOp | None = None,
    targeted_amount: int = 0,
) -> tuple[PluginCapabilityService, ObservedRedisQuota, MeteredRedisCacheAdapter]:
    adapter = MeteredRedisCacheAdapter(
        client=redis, targeted_op=targeted_op, targeted_amount=targeted_amount,
    )
    registry = AdapterRegistry()
    registry.register("cache", adapter)
    quota = ObservedRedisQuota(redis)
    service = PluginCapabilityService(
        registry=registry,
        grant_store=GrantStore(claims, limits),
        quota=quota,
        audit=InMemoryAudit(),
        revocation=ActiveRevocation(),
    )
    return service, quota, adapter


@pytest.mark.parametrize("op", tuple(QuotaOp), ids=lambda op: op.value)
async def test_each_of_six_grant_quota_fields_returns_429_through_real_adapters(
    redis_pair: Any, op: QuotaOp,
) -> None:
    assert len(QUOTA_FIELDS) == 6
    claims = Claims()
    limits = limits_for_test()
    if op is QuotaOp.OPS:
        # Harness fix: production _CoreQuota._usage() accepts only usage.ops == 1
        # per invocation, so an OPS excess cannot be simulated with an ops=8
        # meter.  Use a one-op grant and exhaust its window through a real
        # allowed invocation before the measured (denied) invocation.
        limits = replace(limits, ops_per_minute=1)
    amount_over_grant = limits.limit_for(op) + 1
    service, quota, adapter = service_for(
        redis_pair.api,
        claims,
        limits,
        targeted_op=op,
        targeted_amount=amount_over_grant,
    )

    if op is QuotaOp.OPS:
        allowed = await service.invoke(
            claims=claims,
            capability="cache",
            operation="cache.get",
            request=request_for(claims, "quota-ops-window-fill"),
            now=NOW,
        )
        assert allowed.allowed is True
        assert allowed.http_status == 200

    decision = await service.invoke(
        claims=claims,
        capability="cache",
        operation="cache.get",
        request=request_for(claims, f"quota-{op.value}"),
        now=NOW,
    )

    assert decision.allowed is False
    assert decision.code is DecisionCode.QUOTA_EXCEEDED
    assert decision.http_status == 429
    matching = [item for item in quota.decisions if item.op is op]
    expected_decisions = 2 if op is QuotaOp.OPS else 1
    assert len(matching) == expected_decisions
    denied = matching[-1]
    # OPS denial carries the valid per-invocation ops=1 amount; the other five
    # dimensions carry the deliberately over-grant metered amount.
    assert denied.amount == (1 if op is QuotaOp.OPS else amount_over_grant)
    assert denied.limit == limits.limit_for(op)
    assert denied.code.value == "QUOTA_EXCEEDED"
    assert denied.http_status == 429
    assert adapter.meter_calls == expected_decisions
    # Only the arranging allowed OPS invocation reaches real cache IO; the
    # measured denied invocation never does.
    assert adapter.get_calls == (1 if op is QuotaOp.OPS else 0), (
        "a denied invoke must not reach Redis cache IO"
    )

    key = PluginQuotaKey(
        company_id=claims.company_id,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
    )
    redis_key = key.cache_key() + ":" + op.value
    if op is QuotaOp.OPS:
        # The window keeps the single allowed op; the denied op is not recorded.
        assert await redis_pair.api.zcard(redis_key) == 1
    else:
        assert await redis_pair.api.get(redis_key) is None


async def test_ops_rate_limit_allows_real_cache_io_then_returns_429(
    redis_pair: Any,
) -> None:
    claims = Claims()
    limits = PluginQuota(
        ops_per_minute=2,
        max_rows=100,
        max_bytes=1000,
        max_keys=100,
        max_object_bytes=100,
        max_egress_bytes=100,
    )
    service, quota, adapter = service_for(redis_pair.api, claims, limits)

    for sequence in range(limits.ops_per_minute):
        allowed = await service.invoke(
            claims=claims,
            capability="cache",
            operation="cache.get",
            request=request_for(claims, f"rate-allowed-{sequence}"),
            now=NOW,
        )
        assert allowed.allowed is True
        assert allowed.http_status == 200
        assert allowed.data is not None
        assert allowed.data.found is False, "the real namespaced Redis key must be absent"

    denied = await service.invoke(
        claims=claims,
        capability="cache",
        operation="cache.get",
        request=request_for(claims, "rate-denied"),
        now=NOW,
    )
    assert denied.allowed is False
    assert denied.code is DecisionCode.QUOTA_EXCEEDED
    assert denied.http_status == 429
    assert adapter.get_calls == limits.ops_per_minute
    ops_decisions = [item for item in quota.decisions if item.op is QuotaOp.OPS]
    assert len(ops_decisions) == limits.ops_per_minute + 1
    assert [item.limit for item in ops_decisions] == [
        limits.ops_per_minute
    ] * (limits.ops_per_minute + 1)
    assert ops_decisions[-1].used == limits.ops_per_minute
    key = PluginQuotaKey(
        company_id=claims.company_id,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
    )
    assert await redis_pair.api.zcard(key.cache_key() + ":ops") == limits.ops_per_minute
