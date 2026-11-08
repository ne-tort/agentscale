"""Unit-тесты ``PluginCapabilityService`` (F2).

Ключевые acceptance:
* №12: отозванный grant отклоняется по ``grant_id`` на КАЖДОМ запросе;
* §4.5: success audit (INVOCATION) ≠ authorization audit (AUTHORIZATION);
* §4.3/ADR-007: ``db.direct`` не проходит через gateway;
* §4.3: typed adapter / fail-closed при отсутствии адаптера;
* §4.4/№10: ``QUOTA_EXCEEDED`` → 429;
* §13.3/№22: egress-политика из grant'а в БД, не из JWT;
* §1/§13.3: ядро ``CapabilityGatewayService`` переиспользуется, не копируется.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (верификация заморожена, план §0.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.capability_gateway_service import (
    CapabilityDecision,
    CapabilityGatewayService,
    DecisionCode,
)
from prodavan.application.plugins.capabilities.audit import (
    AuditEventKind,
    FailingAudit,
    InMemoryAudit,
)
from prodavan.application.plugins.capabilities.contracts import (
    CacheGetRequest,
    CacheGetResponse,
    CapabilityEnvelope,
    NetworkEgressRequest,
    NetworkHttpMethod,
    empty_digest,
)
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.capabilities.quotas import (
    InMemoryQuota,
    PluginQuota,
)
from prodavan.application.plugins.capabilities.service import (
    CAPABILITY_BRIDGE,
    PluginCapabilityService,
)

DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FakeClaims:
    """Структурный минимум задачи 02: есть ``sub``, НЕТ ``subject_id``."""

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


class ActiveGrant:
    def __init__(self, *, active: bool = True) -> None:
        self.active = active
        self.calls: list[str] = []

    def is_active(self, grant_id: str, *, now: datetime) -> bool:
        self.calls.append(grant_id)
        return self.active


class AllowEgress:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, int, str]] = []

    def allows(self, *, grant_id: str, host: str, port: int, path: str) -> bool:
        self.calls.append((grant_id, host, port, path))
        return self.allowed


class EchoCacheAdapter:
    """Все 5 методов ``CachePort``; возвращает typed Response."""

    def __init__(self) -> None:
        self.calls = 0

    def get(self, request: CacheGetRequest) -> CacheGetResponse:
        self.calls += 1
        return CacheGetResponse(request_id=request.envelope.request_id, ok=True, digest=DIGEST, found=True)

    def set(self, request: object) -> CacheGetResponse:
        return CacheGetResponse(request_id="req-1", ok=True, digest=DIGEST)

    def delete(self, request: object) -> CacheGetResponse:
        return CacheGetResponse(request_id="req-1", ok=True, digest=DIGEST)

    def incr(self, request: object) -> CacheGetResponse:
        return CacheGetResponse(request_id="req-1", ok=True, digest=DIGEST)

    def publish(self, request: object) -> CacheGetResponse:
        return CacheGetResponse(request_id="req-1", ok=True, digest=DIGEST)


class StubNetworkAdapter:
    def egress(self, request: NetworkEgressRequest) -> object:
        from prodavan.application.plugins.capabilities.contracts import NetworkEgressResponse

        return NetworkEgressResponse(
            request_id=request.envelope.request_id, ok=True, digest=DIGEST, status_code=200,
        )


def envelope() -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req-1", correlation_id="corr-1", run_id="run-1",
        company_id="co_123", cabinet_id="cab_456",
        subject_id=FakeClaims().sub, plugin_instance_id="inst_abc",
        artifact_digest=DIGEST, grant_id="grant_1",
    )


def cache_request() -> CacheGetRequest:
    return CacheGetRequest(key="tenant:co_123:plugin:mod:inst_abc:k", envelope=envelope())


def build(
    *,
    active: bool = True,
    audit: InMemoryAudit | None = None,
    quota: InMemoryQuota | None = None,
    register_cache: bool = True,
    register_network: bool = False,
    egress: AllowEgress | None = None,
    revocation: ActiveGrant | None = None,
):
    registry = AdapterRegistry()
    if register_cache:
        registry.register("cache", EchoCacheAdapter())
    if register_network:
        registry.register("network", StubNetworkAdapter())
    return PluginCapabilityService(
        registry=registry,
        quota=quota or InMemoryQuota(PluginQuota(ops_per_minute=100), now=NOW),
        audit=audit or InMemoryAudit(),
        revocation=revocation if revocation is not None else ActiveGrant(active=active),
        egress_policy=egress,
    )


# ---------------------------------------------------------------------------
# Переиспользование ядра (§1, §13.3)
# ---------------------------------------------------------------------------


def test_service_reuses_core_gateway_without_copying_logic() -> None:
    """Ядро импортируется и вызывается; собственной копии pipeline нет."""
    from prodavan.application.plugins.capabilities import service as module

    assert module.CapabilityGatewayService is CapabilityGatewayService
    source_names = {name for name in vars(module) if name.startswith("_Core")}
    # Мосты — адаптеры к протоколам ядра, а не дубли проверок.
    assert {"_CoreIdentity", "_CoreScope", "_CoreQuota", "_CoreAudit", "_CoreAdapter"} <= source_names
    assert set(CAPABILITY_BRIDGE) == {"cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"}
    assert "db.direct" not in CAPABILITY_BRIDGE


def test_bridge_is_injective_and_uses_core_capabilities() -> None:
    """Мост инъективен и ведёт в существующий frozenset ядра."""
    from prodavan.application.capability_gateway_service import DECLARATIVE_CAPABILITIES

    assert len(set(CAPABILITY_BRIDGE.values())) == len(CAPABILITY_BRIDGE)
    assert set(CAPABILITY_BRIDGE.values()) <= DECLARATIVE_CAPABILITIES


# ---------------------------------------------------------------------------
# Отзыв grant'а (acceptance №12)
# ---------------------------------------------------------------------------


def test_revoked_grant_is_denied_before_adapter_invocation() -> None:
    """Отзыв по ``grant_id`` проверяется на каждом запросе; адаптер не вызывается."""
    registry = AdapterRegistry()
    adapter = EchoCacheAdapter()
    registry.register("cache", adapter)
    revocation = ActiveGrant(active=False)
    service = PluginCapabilityService(
        registry=registry,
        quota=InMemoryQuota(PluginQuota(ops_per_minute=100), now=NOW),
        audit=InMemoryAudit(),
        revocation=revocation,
    )

    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )

    assert result.allowed is False
    assert result.decision == "deny"
    assert result.code is DecisionCode.GRANT_REVOKED
    assert result.http_status == 403
    assert result.data is None
    assert adapter.calls == 0
    assert revocation.calls == ["grant_1"]


def test_missing_revocation_port_fails_closed() -> None:
    """Нет порта отзыва = grant считается неактивным (fail-closed)."""
    service = PluginCapabilityService(
        registry=AdapterRegistry(), audit=InMemoryAudit(), revocation=None,
    )
    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.code is DecisionCode.GRANT_REVOKED


def test_revocation_port_exception_is_treated_as_revoked() -> None:
    class Broken:
        def is_active(self, grant_id: str, *, now: datetime) -> bool:
            raise RuntimeError("revocation store unavailable")

    service = PluginCapabilityService(
        registry=AdapterRegistry(), audit=InMemoryAudit(), revocation=Broken(),
    )
    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.code is DecisionCode.GRANT_REVOKED


def test_valid_grant_allows_and_calls_typed_adapter() -> None:
    audit = InMemoryAudit()
    service = build(audit=audit)
    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is True
    assert result.decision == "allow"
    assert result.code is DecisionCode.ALLOWED
    assert result.data is not None and isinstance(result.data, CacheGetResponse)
    assert result.http_status == 200


# ---------------------------------------------------------------------------
# Аудит: success ≠ authorization (§4.5)
# ---------------------------------------------------------------------------


def test_success_invocation_audit_is_separate_from_authorization_audit() -> None:
    """Два РАЗНЫХ события: AUTHORIZATION (allow) и INVOCATION (success)."""
    audit = InMemoryAudit()
    service = build(audit=audit)

    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )

    assert result.allowed is True
    kinds = [event.event_kind for event in audit.events]
    assert AuditEventKind.AUTHORIZATION in kinds
    assert AuditEventKind.INVOCATION in kinds
    assert len(audit.by_kind(AuditEventKind.AUTHORIZATION)) == 1
    assert len(audit.by_kind(AuditEventKind.INVOCATION)) == 1
    authorization = audit.by_kind(AuditEventKind.AUTHORIZATION)[0]
    invocation = audit.by_kind(AuditEventKind.INVOCATION)[0]
    assert authorization.event_id != invocation.event_id
    assert invocation.linked_event_id == authorization.event_id
    assert authorization.decision is CapabilityDecision.ALLOW
    assert invocation.decision.value == "success"
    assert result.audit_event_id == invocation.event_id
    # Обе строки append-only sink уже redacted.
    assert len(audit.rows) == 2


def test_denied_request_has_authorization_audit_but_no_success_event() -> None:
    """Отказ НЕ порождает success-событие."""
    audit = InMemoryAudit()
    service = build(audit=audit, active=False)
    service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert len(audit.by_kind(AuditEventKind.AUTHORIZATION)) == 1
    assert len(audit.by_kind(AuditEventKind.INVOCATION)) == 0


def test_audit_append_failure_turns_success_into_deny() -> None:
    """Append failure позитивной мутации = deny, а не allow (§4.5)."""
    service = PluginCapabilityService(
        registry=AdapterRegistry(),
        quota=InMemoryQuota(PluginQuota(ops_per_minute=100), now=NOW),
        audit=FailingAudit(),
        revocation=ActiveGrant(active=True),
    )
    registry = AdapterRegistry()
    registry.register("cache", EchoCacheAdapter())
    service = PluginCapabilityService(
        registry=registry,
        quota=InMemoryQuota(PluginQuota(ops_per_minute=100), now=NOW),
        audit=FailingAudit(),
        revocation=ActiveGrant(active=True),
    )
    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.ADAPTER_FAILED


# ---------------------------------------------------------------------------
# db.direct / адаптеры / валидация
# ---------------------------------------------------------------------------


def test_db_direct_never_passes_through_gateway() -> None:
    """БД — прямой DSN; gateway отказывает явно, адаптера нет."""
    service = build()
    result = service.invoke(
        claims=FakeClaims(), capability="db.direct", operation="query",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.CAPABILITY_UNKNOWN


def test_unknown_operation_is_rejected() -> None:
    service = build()
    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="execute_sql",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.INVALID_REQUEST
    assert result.http_status == 422


def test_unknown_capability_is_rejected() -> None:
    service = build()
    result = service.invoke(
        claims=FakeClaims(), capability="shell", operation="exec",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.CAPABILITY_UNKNOWN


def test_missing_adapter_is_denied_with_separate_invocation_event() -> None:
    """Fail-closed: неизвестный адаптер → ADAPTER_MISSING, а не тихий allow."""
    audit = InMemoryAudit()
    service = build(audit=audit, register_cache=False)
    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.ADAPTER_MISSING
    assert result.http_status == 503
    # Авторизация зафиксирована, успех — НЕ зафиксирован.
    assert len(audit.by_kind(AuditEventKind.AUTHORIZATION)) == 1
    invocation = audit.by_kind(AuditEventKind.INVOCATION)
    assert len(invocation) == 1
    assert invocation[0].decision.value == "error"


def test_capability_not_in_claims_scopes_is_denied() -> None:
    service = build()
    result = service.invoke(
        claims=FakeClaims(scopes=frozenset({"objects"})), capability="cache",
        operation="get", request=cache_request(), now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.CAPABILITY_NOT_REQUESTED


def test_server_identity_overrides_client_supplied_envelope() -> None:
    """Плагин не может подменить company_id/subject_id/digest через тело."""
    forged = CapabilityEnvelope(
        request_id="forged", company_id="co_OTHER", subject_id="attacker",
        plugin_instance_id="inst_evil", artifact_digest="sha256:" + "f" * 64,
        grant_id="grant_evil",
    )
    audit = InMemoryAudit()
    service = build(audit=audit)
    result = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=CacheGetRequest(key="k", envelope=forged), now=NOW,
    )
    assert result.allowed is True
    row = audit.rows[0]
    assert row["company_id"] == "co_123"
    assert row["subject_id"] == FakeClaims().sub
    assert row["artifact_digest"] == DIGEST
    assert row["grant_id"] == "grant_1"
    assert "co_OTHER" not in str(row)


# ---------------------------------------------------------------------------
# Квоты / egress (№10, №22)
# ---------------------------------------------------------------------------


def test_quota_exceeded_returns_429_via_core_gateway() -> None:
    """Кр. 10: ядро выдаёт QUOTA_EXCEEDED, HTTP-проекция = 429."""
    quota = InMemoryQuota(PluginQuota(ops_per_minute=1), now=NOW)
    audit = InMemoryAudit()
    service = build(audit=audit, quota=quota)

    first = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    second = service.invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert first.allowed is True
    assert second.allowed is False
    assert second.code is DecisionCode.QUOTA_EXCEEDED
    assert second.http_status == 429
    assert second.data is None


def test_egress_policy_comes_from_grant_not_from_token() -> None:
    """Кр. 22: egress разрешает политика БД, а не scopes/payload JWT."""
    egress = AllowEgress(allowed=True)
    service = build(register_network=True, egress=egress)
    request = NetworkEgressRequest(
        host="api.example.com", port=443, path="/v1/items",
        method=NetworkHttpMethod.GET, body_digest=empty_digest(), envelope=envelope(),
    )
    result = service.invoke(
        claims=FakeClaims(), capability="network", operation="egress",
        request=request, now=NOW,
    )
    assert result.allowed is True
    assert egress.calls == [("grant_1", "api.example.com", 443, "/v1/items")]


def test_egress_denied_when_policy_from_grant_disallows_host() -> None:
    egress = AllowEgress(allowed=False)
    service = build(register_network=True, egress=egress)
    request = NetworkEgressRequest(
        host="evil.example", port=443, path="/", method=NetworkHttpMethod.GET,
        body_digest=empty_digest(), envelope=envelope(),
    )
    result = service.invoke(
        claims=FakeClaims(), capability="network", operation="egress",
        request=request, now=NOW,
    )
    assert result.allowed is False
    assert result.code is DecisionCode.SCOPE_DENIED


def test_egress_without_policy_fails_closed() -> None:
    """Отсутствие EgressPolicyPort = запрет egress (правила не из JWT)."""
    service = build(register_network=True, egress=None)
    request = NetworkEgressRequest(
        host="api.example.com", port=443, path="/", method=NetworkHttpMethod.GET,
        body_digest=empty_digest(), envelope=envelope(),
    )
    result = service.invoke(
        claims=FakeClaims(), capability="network", operation="egress",
        request=request, now=NOW,
    )
    assert result.allowed is False


# ---------------------------------------------------------------------------
# Стык с задачей 04
# ---------------------------------------------------------------------------


def test_result_shape_matches_http_surface_contract() -> None:
    """``.decision`` == "allow"/"deny", ``.data`` — typed Response или None."""
    result = build().invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.decision == "allow"
    assert result.data is not None
    assert hasattr(result, "code")
    denied = build(active=False).invoke(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert denied.decision == "deny"
    assert denied.data is None


def test_authorize_only_does_not_call_adapter() -> None:
    """authorize() — только решение ядра, без вызова адаптера."""
    registry = AdapterRegistry()
    adapter = EchoCacheAdapter()
    registry.register("cache", adapter)
    service = PluginCapabilityService(
        registry=registry, audit=InMemoryAudit(), revocation=ActiveGrant(),
        quota=InMemoryQuota(PluginQuota(ops_per_minute=100), now=NOW),
    )
    result = service.authorize(
        claims=FakeClaims(), capability="cache", operation="get",
        request=cache_request(), now=NOW,
    )
    assert result.allowed is True
    assert result.data is None
    assert adapter.calls == 0
