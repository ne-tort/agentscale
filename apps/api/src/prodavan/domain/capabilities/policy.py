"""Deny-by-default capability authorization policy."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from prodavan.domain.capabilities.types import (
    AclDecision,
    CapabilityAcl,
    CapabilityAuditEnvelope,
    CapabilityAuditSink,
    CapabilityAuthorization,
    CapabilityContext,
    CapabilityDecision,
    CapabilityGrant,
    CapabilityManifest,
    CapabilityQuota,
    CapabilityRequest,
    DecisionCode,
    QuotaDecision,
)


class DenyByDefaultCapabilityPolicy:
    """Authorize only declared operations with an explicit valid grant.

    ACL and quota ports are optional integrations, but when supplied they must
    explicitly allow the request.  Any missing manifest, grant, ACL, or quota
    decision therefore fails closed.
    """

    def __init__(
        self,
        *,
        manifests: tuple[CapabilityManifest, ...] = (),
        grants: tuple[CapabilityGrant, ...] = (),
        acl: CapabilityAcl | None = None,
        quota: CapabilityQuota | None = None,
        audit: CapabilityAuditSink | None = None,
    ) -> None:
        self._manifests = {manifest.capability: manifest for manifest in manifests}
        self._grants = grants
        self._acl = acl
        self._quota = quota
        self._audit = audit

    def authorize(
        self,
        request: CapabilityRequest,
        *,
        now: datetime,
        actor_id: str | None = None,
        event_id: str | None = None,
    ) -> CapabilityAuthorization:
        manifest = self._manifests.get(request.capability)
        if manifest is None:
            result = CapabilityAuthorization(CapabilityDecision.DENY, DecisionCode.CAPABILITY_UNKNOWN)
        elif not manifest.declares(request.operation):
            result = CapabilityAuthorization(CapabilityDecision.DENY, DecisionCode.OPERATION_NOT_DECLARED)
        else:
            grant = next((candidate for candidate in self._grants if candidate.allows(request, now=now)), None)
            if grant is None:
                matching = any(
                    candidate.capability == request.capability
                    and candidate.subject_id == request.context.subject_id
                    and request.operation in candidate.operations
                    for candidate in self._grants
                )
                code = DecisionCode.GRANT_EXPIRED if matching else DecisionCode.GRANT_MISSING
                result = CapabilityAuthorization(CapabilityDecision.DENY, code)
            else:
                result = self._authorize_with_guards(request, grant, result_context=request.context)

        if self._audit is not None:
            self._audit.record(
                CapabilityAuditEnvelope(
                    event_id=event_id or str(uuid4()),
                    occurred_at=now,
                    request=request,
                    decision=result.decision,
                    code=result.code,
                    actor_id=actor_id or request.context.subject_id,
                    company_id=request.context.company_id,
                    grant_id=result.grant_id,
                    reason=result.reason,
                )
            )
        return result

    def _authorize_with_guards(
        self,
        request: CapabilityRequest,
        grant: CapabilityGrant,
        *,
        result_context: CapabilityContext,
    ) -> CapabilityAuthorization:
        if self._acl is not None:
            acl_decision: AclDecision = self._acl.decide(request, result_context)
            if not acl_decision.allowed:
                return CapabilityAuthorization(
                    CapabilityDecision.DENY,
                    acl_decision.code or DecisionCode.ACL_DENIED,
                    acl_decision.reason,
                    grant.grant_id,
                )
        if self._quota is not None:
            quota_decision: QuotaDecision = self._quota.decide(request, result_context)
            if not quota_decision.allowed:
                return CapabilityAuthorization(
                    CapabilityDecision.DENY,
                    quota_decision.code or DecisionCode.QUOTA_EXCEEDED,
                    quota_decision.reason,
                    grant.grant_id,
                )
        return CapabilityAuthorization(CapabilityDecision.ALLOW, DecisionCode.ALLOWED, grant_id=grant.grant_id)
