"""Regression tests for the canonical async plugin capability pipeline.

Written under verification freeze; intentionally not executed in this migration
round.  The fakes are async at production boundaries, except where a test
explicitly checks the core's perhaps-await compatibility contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.capability_gateway_service import CapabilityDecision, DecisionCode
from prodavan.application.plugins.capabilities.audit import (
    AuditEventKind,
    InMemoryAudit,
)
from prodavan.application.plugins.capabilities.contracts import (
    CacheGetRequest,
    CacheGetResponse,
    CapabilityEnvelope,
    NetworkEgressRequest,
    NetworkHttpMethod,
    ObjectPresignMethod,
    ObjectsPresignRequest,
    ObjectsPresignResponse,
    empty_digest,
)
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.capabilities.quotas import (
    InMemoryQuota,
    PluginQuota,
    PluginQuotaKey,
    QuotaOp,
    QuotaUsage,
)
from prodavan.application.plugins.capabilities.service import PluginCapabilityService

pytestmark = pytest.mark.asyncio
DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FakeClaims:
    sub: str = "plugin:mod_equipment:co_123:inst_abc"
    company_id: str = "co_123"
    plugin_instance_id: str = "inst_abc"
    artifact_digest: str = DIGEST
    grant_id: str = "grant_1"
    scopes: frozenset[str] = frozenset({"cache", "network"})
    cabinet_id: str | None = "cab_456"
    project_id: str | None = None
    plugin_id: str = "mod_equipment"
    manifest_version: str = "1.2.0"
    exp: int = int((NOW + timedelta(minutes=5)).timestamp())


@dataclass(frozen=True, slots=True)
class FakeGrantRecord:
    grant_id: str = "grant_1"
    company_id: str = "co_123"
    plugin_instance_id: str = "inst_abc"
    plugin_id: str = "mod_equipment"
    manifest_version: str = "1.2.0"
    artifact_digest: str = DIGEST
    capabilities: tuple[str, ...] = ("cache", "network")
    status: str = "active"
    created_at: datetime = NOW
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    quotas: object = PluginQuota(ops_per_minute=100)


class FakeGrantStore:
    def __init__(self, record: FakeGrantRecord | None = None) -> None:
        self.record = FakeGrantRecord() if record is None else record
        self.calls: list[str] = []
        self.fail = False

    async def get_by_grant_id(self, *, grant_id: str):
        self.calls.append(grant_id)
        if self.fail:
            raise RuntimeError("database unavailable")
        return self.record if self.record.grant_id == grant_id else None


class ActiveGrant:
    def __init__(self, *, active: bool = True) -> None:
        self.active = active
        self.calls: list[str] = []

    async def is_active(self, grant_id: str, *, now: datetime) -> bool:
        self.calls.append(grant_id)
        return self.active


class AllowEgress:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, int, str]] = []

    async def allows(self, *, grant_id: str, host: str, port: int, path: str) -> bool:
        self.calls.append((grant_id, host, port, path))
        return self.allowed


class EchoCacheAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def quota_usage(self, request: CacheGetRequest) -> QuotaUsage:
        # One touched cache key; all other dimensions are explicitly not used.
        return QuotaUsage(
            ops=1, rows=0, bytes=0, keys=1, object_bytes=0, egress_bytes=0
        )

    async def get(self, request: CacheGetRequest) -> CacheGetResponse:
        self.calls += 1
        return CacheGetResponse(
            request_id=request.envelope.request_id, ok=True, digest=DIGEST, found=True
        )

    async def set(self, request: object) -> CacheGetResponse:
        return CacheGetResponse(request_id="req-1", ok=True, digest=DIGEST)

    async def delete(self, request: object) -> CacheGetResponse:
        return CacheGetResponse(request_id="req-1", ok=True, digest=DIGEST)

    async def incr(self, request: object) -> CacheGetResponse:
        return CacheGetResponse(request_id="req-1", ok=True, digest=DIGEST)

    async def publish(self, request: object) -> CacheGetResponse:
        return CacheGetResponse(request_id="req-1", ok=True, digest=DIGEST)


class StubNetworkAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def quota_usage(self, request: NetworkEgressRequest) -> QuotaUsage:
        # Pre-invoke server reservation bound for request + response octets.
        return QuotaUsage(
            ops=1, rows=0, bytes=0, keys=0, object_bytes=0, egress_bytes=64
        )

    async def egress(self, request: NetworkEgressRequest):
        from prodavan.application.plugins.capabilities.contracts import NetworkEgressResponse

        self.calls += 1
        return NetworkEgressResponse(
            request_id=request.envelope.request_id,
            ok=True,
            digest=DIGEST,
            status_code=200,
        )


class FailAfterFirstAudit:
    def __init__(self) -> None:
        self.calls = 0

    async def append(self, event: object) -> None:
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("audit unavailable after adapter side effect")


def envelope() -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req-1",
        correlation_id="corr-1",
        run_id="run-1",
        company_id="co_123",
        cabinet_id="cab_456",
        subject_id=FakeClaims().sub,
        plugin_instance_id="inst_abc",
        artifact_digest=DIGEST,
        grant_id="grant_1",
    )


def cache_request() -> CacheGetRequest:
    return CacheGetRequest(key="tenant:co_123:plugin:mod:inst_abc:k", envelope=envelope())


def network_request() -> NetworkEgressRequest:
    return NetworkEgressRequest(
        host="api.example.com",
        port=443,
        path="/v1/items",
        method=NetworkHttpMethod.GET,
        body_digest=empty_digest(),
        envelope=envelope(),
    )


def build(
    *,
    record: FakeGrantRecord | None = None,
    active: bool = True,
    audit: object | None = None,
    quota: InMemoryQuota | None = None,
    register_cache: bool = True,
    register_network: bool = False,
    egress: AllowEgress | None = None,
    revocation: ActiveGrant | None = None,
):
    registry = AdapterRegistry()
    cache = EchoCacheAdapter()
    network = StubNetworkAdapter()
    if register_cache:
        registry.register("cache", cache)
    if register_network:
        registry.register("network", network)
    store = FakeGrantStore(record)
    audit_sink = audit if audit is not None else InMemoryAudit()
    quota_port = quota or InMemoryQuota(now=NOW)
    revocation_port = revocation if revocation is not None else ActiveGrant(active=active)
    service = PluginCapabilityService(
        registry=registry,
        grant_store=store,
        quota=quota_port,
        audit=audit_sink,  # type: ignore[arg-type]
        revocation=revocation_port,
        egress_policy=egress,
    )
    return service, {
        "store": store,
        "cache": cache,
        "network": network,
        "audit": audit_sink,
        "quota": quota_port,
        "revocation": revocation_port,
    }


async def test_plugin_uses_real_capability_names_without_declarative_bridge() -> None:
    from prodavan.application.plugins.capabilities import service as module

    assert not hasattr(module, "CAPABILITY_BRIDGE")
    service, parts = build()
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="cache.get",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is True
    assert parts["cache"].calls == 1


async def test_db_direct_and_wrong_operation_are_rejected_before_side_effects() -> None:
    service, parts = build()
    direct = await service.invoke(
        claims=FakeClaims(), capability="db.direct", operation="query",
        request=cache_request(), now=NOW,
    )
    assert direct.code is DecisionCode.CAPABILITY_UNKNOWN

    wrong = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="docs.query",
        request=cache_request(), now=NOW,
    )
    assert wrong.code is DecisionCode.INVALID_REQUEST
    assert parts["cache"].calls == 0


async def test_cold_revocation_denies_before_adapter_and_reads_each_request() -> None:
    service, parts = build(active=False)
    for _ in range(2):
        result = await service.invoke(
            claims=FakeClaims(), capability="cache", operation="get",
            request=cache_request(), now=NOW,
        )
        assert result.code is DecisionCode.GRANT_REVOKED
    assert parts["cache"].calls == 0
    assert parts["store"].calls == ["grant_1", "grant_1"]
    assert parts["revocation"].calls == ["grant_1", "grant_1"]


async def test_missing_quota_is_fail_closed_at_construction() -> None:
    """Без явного quota сервис не собирается: молчаливого InMemoryQuota больше нет."""
    registry = AdapterRegistry()
    registry.register("cache", EchoCacheAdapter())
    with pytest.raises(TypeError, match="quota"):
        PluginCapabilityService(  # type: ignore[call-arg]
            registry=registry,
            grant_store=FakeGrantStore(),
            audit=InMemoryAudit(),
            revocation=ActiveGrant(),
        )
    # Явный None — тоже typed failure, а не process-local default.
    with pytest.raises(TypeError, match="quota"):
        PluginCapabilityService(  # type: ignore[arg-type]
            registry=registry,
            grant_store=FakeGrantStore(),
            quota=None,
            audit=InMemoryAudit(),
            revocation=ActiveGrant(),
        )


async def test_explicit_quota_and_audit_ports_are_used_without_duplicates() -> None:
    """Переданные порты используются по identity: второй InMemory не создаётся."""
    quota = InMemoryQuota(now=NOW)
    audit = InMemoryAudit()
    service, parts = build(quota=quota, audit=audit)
    assert service._quota is quota
    assert service._audit is audit
    assert parts["quota"] is quota
    assert parts["audit"] is audit


async def test_missing_grant_store_and_revocation_are_fail_closed() -> None:
    registry = AdapterRegistry()
    registry.register("cache", EchoCacheAdapter())
    missing_store = PluginCapabilityService(
        registry=registry,
        grant_store=None,
        quota=InMemoryQuota(now=NOW),  # явный test double, НЕ default сервиса
        audit=InMemoryAudit(),
        revocation=ActiveGrant(),
    )
    result = await missing_store.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.code is DecisionCode.GRANT_MISSING

    _, parts = build()
    second_registry = AdapterRegistry()
    second_registry.register("cache", EchoCacheAdapter())
    no_revocation = PluginCapabilityService(
        registry=second_registry,
        grant_store=parts["store"],
        quota=InMemoryQuota(now=NOW),  # явный test double, НЕ default сервиса
        audit=InMemoryAudit(),
        revocation=None,
    )
    denied = await no_revocation.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert denied.code is DecisionCode.GRANT_REVOKED


async def test_success_invokes_once_and_writes_distinct_audit_events() -> None:
    service, parts = build()
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="cache.get",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is True
    assert result.http_status == 200
    assert isinstance(result.data, CacheGetResponse)
    assert parts["cache"].calls == 1
    audit = parts["audit"]
    assert len(audit.by_kind(AuditEventKind.AUTHORIZATION)) == 1
    assert len(audit.by_kind(AuditEventKind.INVOCATION)) == 1
    authorization = audit.by_kind(AuditEventKind.AUTHORIZATION)[0]
    invocation = audit.by_kind(AuditEventKind.INVOCATION)[0]
    assert invocation.linked_event_id == authorization.event_id
    assert authorization.decision is CapabilityDecision.ALLOW
    assert invocation.decision.value == "success"


async def test_post_side_effect_audit_failure_is_unknown_503_not_no_effect() -> None:
    service, parts = build(audit=FailAfterFirstAudit())
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert parts["cache"].calls == 1
    assert result.allowed is False
    assert result.code is DecisionCode.OUTCOME_UNKNOWN
    assert result.http_status == 503
    assert result.reason == "outcome_unknown_audit_append_failed"
    assert result.data is None
    key = PluginQuotaKey(
        company_id="co_123", plugin_instance_id="inst_abc", artifact_digest=DIGEST
    )
    assert parts["quota"].usage(key, QuotaOp.OPS) == 1
    assert parts["quota"].usage(key, QuotaOp.KEYS) == 1


async def test_dry_run_authorize_does_not_reserve_quota_and_invoke_reserves_once() -> None:
    service, parts = build()
    dry = await service.authorize(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert dry.allowed is True
    assert parts["cache"].calls == 0
    key = PluginQuotaKey(
        company_id="co_123", plugin_instance_id="inst_abc", artifact_digest=DIGEST
    )
    for op in QuotaOp:
        assert parts["quota"].usage(key, op) == 0

    invoked = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert invoked.allowed is True
    assert parts["cache"].calls == 1
    assert parts["quota"].usage(key, QuotaOp.OPS) == 1
    assert parts["quota"].usage(key, QuotaOp.KEYS) == 1
    for op in (QuotaOp.ROWS, QuotaOp.BYTES, QuotaOp.OBJECT_BYTES, QuotaOp.EGRESS_BYTES):
        assert parts["quota"].usage(key, op) == 0


async def test_claims_cannot_expand_database_capabilities() -> None:
    # Token asks for cache+network, DB grants only cache. Network is denied even
    # though its typed request and token scope are otherwise valid.
    service, parts = build(record=FakeGrantRecord(capabilities=("cache",)), register_network=True)
    result = await service.invoke(
        claims=FakeClaims(), capability="network", operation="egress",
        request=network_request(), now=NOW,
    )
    assert result.code is DecisionCode.CAPABILITY_NOT_REQUESTED
    assert parts["network"].calls == 0


async def test_token_scopes_also_narrow_database_capabilities() -> None:
    service, parts = build(
        record=FakeGrantRecord(capabilities=("cache", "network")),
        register_network=True,
    )
    claims = FakeClaims(scopes=frozenset({"cache"}))
    result = await service.invoke(
        claims=claims, capability="network", operation="egress",
        request=network_request(), now=NOW,
    )
    assert result.code is DecisionCode.CAPABILITY_NOT_REQUESTED
    assert parts["network"].calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", "co_other"),
        ("plugin_instance_id", "inst_other"),
        ("plugin_id", "mod_other"),
        ("manifest_version", "9.9.9"),
        ("artifact_digest", "sha256:" + "f" * 64),
    ],
)
async def test_database_identity_mismatch_denies(field: str, value: str) -> None:
    record = FakeGrantRecord(**{field: value})
    service, parts = build(record=record)
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.code is DecisionCode.GRANT_MISSING
    assert parts["cache"].calls == 0


async def test_database_status_and_expiry_are_authoritative() -> None:
    revoked, _ = build(record=FakeGrantRecord(status="revoked"))
    result = await revoked.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.code is DecisionCode.GRANT_REVOKED

    expired, _ = build(record=FakeGrantRecord(expires_at=NOW - timedelta(seconds=1)))
    result = await expired.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.code is DecisionCode.GRANT_EXPIRED


async def test_quota_exceeded_is_429() -> None:
    # Legacy composition may still pass runtime120; durable grant ops=1 wins.
    quota = InMemoryQuota(PluginQuota(ops_per_minute=120), now=NOW)
    service, _ = build(
        quota=quota,
        record=FakeGrantRecord(quotas=PluginQuota(ops_per_minute=1)),
    )
    first = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    second = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert first.allowed is True
    assert second.code is DecisionCode.QUOTA_EXCEEDED
    assert second.http_status == 429


@pytest.mark.parametrize(
    "quotas",
    [
        None,
        {},
        {"ops_per_minute": 1},
        {
            "ops_per_minute": 1,
            "max_rows": 1,
            "max_bytes": 1,
            "max_keys": 1,
            "max_object_bytes": 1,
            "max_egress_bytes": True,
        },
    ],
)
async def test_missing_or_invalid_six_grant_quotas_fail_closed(quotas: object) -> None:
    service, parts = build(record=FakeGrantRecord(quotas=quotas))
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.code is DecisionCode.GRANT_MISSING
    assert result.reason == "grant_quotas_invalid"
    assert parts["cache"].calls == 0
    key = PluginQuotaKey(
        company_id="co_123", plugin_instance_id="inst_abc", artifact_digest=DIGEST
    )
    for op in QuotaOp:
        assert parts["quota"].usage(key, op) == 0


async def test_async_egress_policy_is_enforced() -> None:
    egress = AllowEgress()
    service, parts = build(register_network=True, egress=egress)
    result = await service.invoke(
        claims=FakeClaims(), capability="network", operation="egress",
        request=network_request(), now=NOW,
    )
    assert result.allowed is True
    assert parts["network"].calls == 1
    assert egress.calls == [("grant_1", "api.example.com", 443, "/v1/items")]


async def test_missing_adapter_is_fail_closed() -> None:
    service, parts = build(register_cache=False)
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.code is DecisionCode.ADAPTER_MISSING
    assert result.http_status == 503


async def test_server_identity_overrides_forged_envelope() -> None:
    forged = CapabilityEnvelope(
        request_id="forged",
        company_id="co_OTHER",
        subject_id="attacker",
        plugin_instance_id="inst_evil",
        artifact_digest="sha256:" + "f" * 64,
        grant_id="grant_evil",
    )
    service, parts = build()
    result = await service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=CacheGetRequest(key="k", envelope=forged), now=NOW,
    )
    assert result.allowed is True
    row = parts["audit"].rows[0]
    assert row["company_id"] == "co_123"
    assert row["subject_id"] == FakeClaims().sub
    assert row["artifact_digest"] == DIGEST
    assert row["grant_id"] == "grant_1"
    assert "co_OTHER" not in str(row)


class GatewayPresignAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def quota_usage(self, request: object) -> QuotaUsage:
        return QuotaUsage(ops=1, rows=1, bytes=0, keys=1, object_bytes=0, egress_bytes=0)

    async def presign(self, request: ObjectsPresignRequest) -> ObjectsPresignResponse:
        self.calls += 1
        identity = request.envelope
        return ObjectsPresignResponse(
            request_id=identity.request_id,
            ok=True,
            digest=DIGEST,
            url=(
                "https://minio.example/tenant-co_123/module-mod_equipment/report.bin"
                "?X-Amz-Expires=600&X-Amz-Signature=signature-material"
            ),
            bucket="tenant-co_123",
            key_digest=DIGEST,
            method=request.method,
            ttl_seconds=request.ttl_seconds,
            expires_at_epoch=int(NOW.timestamp()) + request.ttl_seconds,
            company_id=identity.company_id,
            plugin_id="mod_equipment",
            project_id=identity.project_id,
            plugin_instance_id=identity.plugin_instance_id,
        )

    async def scoped_credentials(self, request: object) -> object:
        raise AssertionError("presign gateway test must not invoke STS")

    async def list(self, request: object) -> object:
        raise AssertionError("presign is not a list operation")

    async def delete(self, request: object) -> object:
        raise AssertionError("presign is not a delete operation")


def presign_service(
    adapter: GatewayPresignAdapter, *, capabilities: tuple[str, ...] = ("objects",),
) -> tuple[PluginCapabilityService, InMemoryAudit, InMemoryQuota]:
    registry = AdapterRegistry()
    registry.register("objects", adapter)
    audit = InMemoryAudit()
    quota = InMemoryQuota(now=NOW)
    service = PluginCapabilityService(
        registry=registry,
        grant_store=FakeGrantStore(FakeGrantRecord(capabilities=capabilities)),
        quota=quota,
        audit=audit,
        revocation=ActiveGrant(),
    )
    return service, audit, quota


async def test_objects_presign_uses_gateway_grant_quota_audit_and_returns_usable_url() -> None:
    adapter = GatewayPresignAdapter()
    service, audit, quota = presign_service(adapter)
    claims = FakeClaims(scopes=frozenset({"objects"}), project_id="proj_1")
    request = ObjectsPresignRequest(
        bucket="tenant-co_123",
        object_key="module-mod_equipment/report.bin",
        method=ObjectPresignMethod.GET,
        ttl_seconds=600,
        envelope=envelope(),
    )
    result = await service.invoke(
        claims=claims, capability="objects", operation="objects.presign",
        request=request, now=NOW,
    )

    assert result.allowed is True
    assert isinstance(result.data, ObjectsPresignResponse)
    assert result.data.url.endswith("X-Amz-Signature=signature-material")
    assert result.data.project_id == "proj_1"
    assert adapter.calls == 1
    key = PluginQuotaKey(
        company_id="co_123", plugin_instance_id="inst_abc", artifact_digest=DIGEST,
    )
    assert quota.usage(key, QuotaOp.OPS) == 1
    assert quota.usage(key, QuotaOp.ROWS) == 1
    assert quota.usage(key, QuotaOp.KEYS) == 1
    assert len(audit.by_kind(AuditEventKind.AUTHORIZATION)) == 1
    assert len(audit.by_kind(AuditEventKind.INVOCATION)) == 1
    assert all(event.operation == "objects.presign" for event in audit.events)
    for row in audit.rows:
        assert result.data.url not in str(row)
        assert "signature-material" not in str(row)
        assert "X-Amz-Signature" not in str(row)

    denied_adapter = GatewayPresignAdapter()
    denied_service, _, _ = presign_service(denied_adapter, capabilities=("cache",))
    denied = await denied_service.invoke(
        claims=claims, capability="objects", operation="objects.presign",
        request=request, now=NOW,
    )
    assert denied.code is DecisionCode.CAPABILITY_NOT_REQUESTED
    assert denied_adapter.calls == 0
