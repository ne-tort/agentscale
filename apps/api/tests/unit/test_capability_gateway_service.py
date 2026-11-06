"""Acceptance contracts for the declarative-app capability service boundary."""

from datetime import UTC, datetime, timedelta

from prodavan.application.capability_gateway_service import (
    AuditEvent,
    CapabilityAdapter,
    CapabilityGatewayService,
    CapabilityGrant,
    CapabilityRequest,
    DecisionCode,
    RequestContext,
)


NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Allow:
    def allows(self, value) -> bool:
        return True


class Deny:
    def allows(self, value) -> bool:
        return False


class Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


class EchoAdapter:
    capability = "storage.kv"

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request: CapabilityRequest) -> dict[str, str]:
        self.calls += 1
        return {"resource": request.resource}


def request() -> CapabilityRequest:
    # `domain` — часть точного сравнения с грантом: запрос без домена не доказывает
    # принадлежность домен-скоуд гранту, поэтому gateway обязан отказывать. Прежняя
    # фикстура оставляла domain=None и ожидала ALLOW для гранта с domains={"storage"}.
    return CapabilityRequest(
        context=RequestContext(
            app_id="inventory-app",
            manifest_version="1.0.0",
            company_id="company-1",
            subject_id="employee-1",
        ),
        capability="storage.kv",
        operation="read",
        resource="project/p-1/key/item",
        domain="storage",
    )


def grant(*, expires_at: datetime | None = None) -> CapabilityGrant:
    return CapabilityGrant(
        app_id="inventory-app",
        manifest_version="1.0.0",
        subject_id="employee-1",
        capability="storage.kv",
        operations=frozenset({"read"}),
        resources=frozenset({"project/p-1/key/item"}),
        domains=frozenset({"storage"}),
        expires_at=expires_at,
    )


def service(*, adapter=None, grants=(grant(),), identity=None, scope=None, quota=None, audit=None):
    return CapabilityGatewayService(
        requested_capabilities=("storage.kv",),
        grants=grants,
        identity=identity or Allow(),
        scope=scope or Allow(),
        quota=quota or Allow(),
        audit=audit,
        adapters={"storage.kv": adapter} if adapter else {},
    )


def test_pipeline_allows_only_after_identity_scope_grant_and_quota() -> None:
    audit = Audit()
    adapter = EchoAdapter()
    gateway = service(adapter=adapter, audit=audit)

    decision, result = gateway.invoke(request(), now=NOW)

    assert decision.allowed is True
    assert result == {"resource": "project/p-1/key/item"}
    assert adapter.calls == 1
    assert audit.events[-1].code is DecisionCode.ALLOWED


def test_missing_dependency_denies_by_default_and_does_not_call_adapter() -> None:
    adapter = EchoAdapter()
    gateway = CapabilityGatewayService(
        requested_capabilities=("storage.kv",),
        grants=(grant(),),
        adapters={"storage.kv": adapter},
    )

    decision, result = gateway.invoke(request(), now=NOW)

    assert decision.code is DecisionCode.IDENTITY_DENIED
    assert result is None
    assert adapter.calls == 0


def test_requested_capabilities_are_not_grants() -> None:
    gateway = service(grants=())

    decision = gateway.authorize(request(), now=NOW)

    assert decision.code is DecisionCode.GRANT_MISSING


def test_expired_grant_is_denied_and_audited() -> None:
    audit = Audit()
    gateway = service(
        grants=(grant(expires_at=NOW - timedelta(seconds=1)),),
        audit=audit,
    )

    decision = gateway.authorize(request(), now=NOW)

    assert decision.code is DecisionCode.GRANT_EXPIRED
    assert audit.events[-1].decision.value == "deny"


def test_unrequested_or_unknown_capability_fails_closed() -> None:
    unrequested = CapabilityRequest(request().context, "network.fetch", "get", "example.com")
    gateway = service()

    decision = gateway.authorize(unrequested, now=NOW)

    assert decision.code is DecisionCode.CAPABILITY_NOT_REQUESTED


def test_missing_adapter_is_denied_after_authorization() -> None:
    gateway = service()

    decision, result = gateway.invoke(request(), now=NOW)

    assert decision.code is DecisionCode.ADAPTER_MISSING
    assert result is None
