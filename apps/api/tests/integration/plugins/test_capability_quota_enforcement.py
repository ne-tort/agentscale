"""Component integration for exact-grant quota enforcement.

Uses the real gateway/service and deterministic in-memory quota/audit doubles.
It is not live Redis/API/cluster evidence and is intentionally not executed here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.capability_gateway_service import DecisionCode
from prodavan.application.plugins.capabilities.audit import InMemoryAudit
from prodavan.application.plugins.capabilities.contracts import (
    CacheGetRequest,
    CacheGetResponse,
    CapabilityEnvelope,
)
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.capabilities.quotas import (
    InMemoryQuota,
    PluginQuota,
    QuotaOp,
    QuotaUsage,
)
from prodavan.application.plugins.capabilities.service import PluginCapabilityService

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
SMALL_LIMITS = PluginQuota(
    ops_per_minute=1,
    max_rows=1,
    max_bytes=1,
    max_keys=1,
    max_object_bytes=1,
    max_egress_bytes=1,
)
pytestmark = pytest.mark.asyncio


@dataclass(frozen=True, slots=True)
class Claims:
    sub: str = "plugin:mod:co:inst"
    company_id: str = "co"
    plugin_instance_id: str = "inst"
    artifact_digest: str = DIGEST
    grant_id: str = "grant"
    scopes: frozenset[str] = frozenset({"cache"})
    plugin_id: str = "mod"
    manifest_version: str = "1.0.0"
    exp: int = int((NOW + timedelta(minutes=5)).timestamp())


@dataclass(frozen=True, slots=True)
class Grant:
    grant_id: str
    company_id: str
    plugin_instance_id: str
    artifact_digest: str
    quotas: object
    plugin_id: str = "mod"
    manifest_version: str = "1.0.0"
    capabilities: tuple[str, ...] = ("cache",)
    status: str = "active"
    created_at: datetime = NOW
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class GrantStore:
    def __init__(self, records: list[Grant]) -> None:
        self.records = {record.grant_id: record for record in records}

    async def get_by_grant_id(self, *, grant_id: str) -> Grant | None:
        return self.records.get(grant_id)


class Active:
    async def is_active(self, grant_id: str, *, now: datetime) -> bool:
        return True


class RecordingQuota:
    def __init__(self) -> None:
        self.inner = InMemoryQuota(now=NOW)
        self.decisions = []

    async def enforce(self, key, op, amount, limits):
        decision = await self.inner.enforce(key, op, amount, limits)
        self.decisions.append(decision)
        return decision

    def usage(self, key, op):
        return self.inner.usage(key, op)


class MeteredCache:
    def __init__(self, usage: QuotaUsage) -> None:
        self.usage = usage
        self.calls = 0
        self.meter_calls = 0

    def quota_usage(self, request: CacheGetRequest) -> QuotaUsage:
        self.meter_calls += 1
        return self.usage

    async def get(self, request: CacheGetRequest) -> CacheGetResponse:
        self.calls += 1
        return CacheGetResponse(
            request_id=request.envelope.request_id,
            ok=True,
            digest=DIGEST,
            found=True,
        )

    # Registry surface; this integration invokes only get.
    set = get
    delete = get
    incr = get
    publish = get


def request_for(claims: Claims) -> CacheGetRequest:
    envelope = CapabilityEnvelope(
        request_id="req-" + claims.grant_id,
        company_id=claims.company_id,
        subject_id=claims.sub,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
        grant_id=claims.grant_id,
    )
    return CacheGetRequest(key="key", envelope=envelope)


def grant_for(claims: Claims, quotas: object = SMALL_LIMITS) -> Grant:
    return Grant(
        grant_id=claims.grant_id,
        company_id=claims.company_id,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
        quotas=quotas,
    )


def service_for(
    records: list[Grant], quota: RecordingQuota, adapter: object, audit: object | None = None
) -> PluginCapabilityService:
    registry = AdapterRegistry()
    registry.register("cache", adapter)
    return PluginCapabilityService(
        registry=registry,
        grant_store=GrantStore(records),
        quota=quota,
        audit=audit or InMemoryAudit(),  # type: ignore[arg-type]
        revocation=Active(),
    )


def usage_for(op: QuotaOp, amount: int) -> QuotaUsage:
    values = {
        QuotaOp.OPS: 1,
        QuotaOp.ROWS: 0,
        QuotaOp.BYTES: 0,
        QuotaOp.KEYS: 0,
        QuotaOp.OBJECT_BYTES: 0,
        QuotaOp.EGRESS_BYTES: 0,
    }
    values[op] = amount
    return QuotaUsage(
        ops=values[QuotaOp.OPS],
        rows=values[QuotaOp.ROWS],
        bytes=values[QuotaOp.BYTES],
        keys=values[QuotaOp.KEYS],
        object_bytes=values[QuotaOp.OBJECT_BYTES],
        egress_bytes=values[QuotaOp.EGRESS_BYTES],
    )


@pytest.mark.parametrize("op", list(QuotaOp))
async def test_small_grant_limit_returns_429_for_every_quota_dimension(op: QuotaOp) -> None:
    claims = Claims()
    quota = RecordingQuota()
    adapter = MeteredCache(usage_for(op, 1 if op is QuotaOp.OPS else 2))
    service = service_for([grant_for(claims)], quota, adapter)

    first = await service.invoke(
        claims=claims, capability="cache", operation="get",
        request=request_for(claims), now=NOW,
    )
    if op is QuotaOp.OPS:
        assert first.allowed is True
        decision = await service.invoke(
            claims=claims, capability="cache", operation="get",
            request=request_for(claims), now=NOW,
        )
    else:
        decision = first

    assert decision.allowed is False
    assert decision.code is DecisionCode.QUOTA_EXCEEDED
    assert decision.http_status == 429
    matching = [item for item in quota.decisions if item.op is op]
    assert matching
    assert matching[-1].limit == 1
    assert matching[-1].allowed is False
    assert adapter.calls == (1 if op is QuotaOp.OPS else 0)


async def test_instance_and_digest_counters_are_independent_at_service_boundary() -> None:
    first = Claims()
    other_instance = replace(
        first, grant_id="grant-other-instance", plugin_instance_id="inst-other"
    )
    other_digest = replace(
        first, grant_id="grant-other-digest", artifact_digest=OTHER_DIGEST
    )
    claims_list = [first, other_instance, other_digest]
    quota = RecordingQuota()
    adapter = MeteredCache(usage_for(QuotaOp.OPS, 1))
    service = service_for([grant_for(item) for item in claims_list], quota, adapter)

    for claims in claims_list:
        result = await service.invoke(
            claims=claims, capability="cache", operation="get",
            request=request_for(claims), now=NOW,
        )
        assert result.allowed is True

    repeated = await service.invoke(
        claims=first, capability="cache", operation="get",
        request=request_for(first), now=NOW,
    )
    assert repeated.http_status == 429
    assert adapter.calls == 3


async def test_dry_run_and_missing_meter_never_fabricate_zero_usage() -> None:
    claims = Claims()
    quota = RecordingQuota()
    adapter = MeteredCache(usage_for(QuotaOp.ROWS, 1))
    service = service_for([grant_for(claims)], quota, adapter)

    dry = await service.authorize(
        claims=claims, capability="cache", operation="get",
        request=request_for(claims), now=NOW,
    )
    assert dry.allowed is True
    assert adapter.meter_calls == 0
    assert quota.decisions == []

    class UnmeteredCache(MeteredCache):
        quota_usage = None  # type: ignore[assignment]

    unmetered = UnmeteredCache(usage_for(QuotaOp.OPS, 1))
    denied_service = service_for([grant_for(claims)], RecordingQuota(), unmetered)
    denied = await denied_service.invoke(
        claims=claims, capability="cache", operation="get",
        request=request_for(claims), now=NOW,
    )
    assert denied.http_status == 429
    assert unmetered.calls == 0
    assert unmetered.meter_calls == 0
