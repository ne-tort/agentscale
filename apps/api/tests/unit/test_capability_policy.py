"""Unit contracts for capability gateway primitives."""

from datetime import UTC, datetime, timedelta

from prodavan.domain.capabilities import (
    AclDecision,
    CapabilityAuditEnvelope,
    CapabilityContext,
    CapabilityDecision,
    CapabilityGrant,
    CapabilityManifest,
    CapabilityRequest,
    DecisionCode,
    DenyByDefaultCapabilityPolicy,
    QuotaDecision,
)


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _request() -> CapabilityRequest:
    return CapabilityRequest(
        capability="documents",
        operation="read",
        resource="project/p-1/document/d-1",
        context=CapabilityContext(company_id="company-1", subject_id="employee-1", project_id="p-1"),
        request_id="req-1",
    )


def _grant(*, expires_at: datetime | None = None) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grant-1",
        capability="documents",
        subject_id="employee-1",
        operations=("read",),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=expires_at,
        resource_prefix="project/p-1/",
    )


def test_policy_denies_by_default_without_manifest_or_grant() -> None:
    request = _request()
    policy = DenyByDefaultCapabilityPolicy(
        manifests=(CapabilityManifest("documents", "1", ("read",)),),
    )

    result = policy.authorize(request, now=NOW)

    assert result.allowed is False
    assert result.code is DecisionCode.GRANT_MISSING


def test_policy_allows_declared_operation_with_matching_grant() -> None:
    policy = DenyByDefaultCapabilityPolicy(
        manifests=(CapabilityManifest("documents", "1", ("read",)),),
        grants=(_grant(),),
    )

    result = policy.authorize(_request(), now=NOW)

    assert result.decision is CapabilityDecision.ALLOW
    assert result.code is DecisionCode.ALLOWED
    assert result.grant_id == "grant-1"


def test_policy_denies_expired_grant_and_undeclared_operation() -> None:
    expired = DenyByDefaultCapabilityPolicy(
        manifests=(CapabilityManifest("documents", "1", ("read",)),),
        grants=(_grant(expires_at=NOW),),
    )
    expired_result = expired.authorize(_request(), now=NOW)
    assert expired_result.code is DecisionCode.GRANT_EXPIRED

    undeclared = DenyByDefaultCapabilityPolicy(
        manifests=(CapabilityManifest("documents", "1", ("write",)),),
        grants=(_grant(),),
    )
    undeclared_result = undeclared.authorize(_request(), now=NOW)
    assert undeclared_result.code is DecisionCode.OPERATION_NOT_DECLARED


def test_acl_and_quota_must_explicitly_allow() -> None:
    class DenyAcl:
        def decide(self, request: CapabilityRequest, context: CapabilityContext) -> AclDecision:
            return AclDecision(False, DecisionCode.ACL_DENIED, "resource is read-only")

    class DenyQuota:
        def decide(self, request: CapabilityRequest, context: CapabilityContext) -> QuotaDecision:
            return QuotaDecision(False, DecisionCode.QUOTA_EXCEEDED, reason="limit reached")

    request = _request()
    acl_result = DenyByDefaultCapabilityPolicy(
        manifests=(CapabilityManifest("documents", "1", ("read",)),),
        grants=(_grant(),),
        acl=DenyAcl(),
    ).authorize(request, now=NOW)
    quota_result = DenyByDefaultCapabilityPolicy(
        manifests=(CapabilityManifest("documents", "1", ("read",)),),
        grants=(_grant(),),
        quota=DenyQuota(),
    ).authorize(request, now=NOW)

    assert acl_result.code is DecisionCode.ACL_DENIED
    assert quota_result.code is DecisionCode.QUOTA_EXCEEDED


def test_audit_envelope_is_secret_free_and_serializable() -> None:
    envelope = CapabilityAuditEnvelope(
        event_id="event-1",
        occurred_at=NOW,
        request=_request(),
        decision=CapabilityDecision.DENY,
        code=DecisionCode.GRANT_MISSING,
        actor_id="employee-1",
        company_id="company-1",
    )

    payload = envelope.to_dict()

    assert payload["decision"] == "deny"
    assert payload["code"] == "grant_missing"
    assert payload["occurred_at"] == NOW.isoformat()
    assert "parameters" not in payload
