"""Backend boundary for the declarative-app capability gateway.

This module intentionally owns transport-neutral service contracts.  Adapters are
called only after identity, scope, grant and quota checks have all allowed a
request; missing integrations fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4


DECLARATIVE_CAPABILITIES = frozenset(
    {
        "ui.render",
        "ui.navigation",
        "storage.kv",
        "storage.files",
        "network.fetch",
        "events.subscribe",
        "platform.commands",
        "platform.queries",
        "secrets.read",
        "identity.read",
    }
)


class CapabilityDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class DecisionCode(StrEnum):
    ALLOWED = "allowed"
    INVALID_REQUEST = "invalid_request"
    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_NOT_REQUESTED = "capability_not_requested"
    IDENTITY_DENIED = "identity_denied"
    SCOPE_DENIED = "scope_denied"
    GRANT_MISSING = "grant_missing"
    GRANT_NOT_YET_ISSUED = "grant_not_yet_issued"
    GRANT_EXPIRED = "grant_expired"
    GRANT_REVOKED = "grant_revoked"
    QUOTA_EXCEEDED = "quota_exceeded"
    ADAPTER_MISSING = "adapter_missing"
    ADAPTER_FAILED = "adapter_failed"


@dataclass(frozen=True, slots=True)
class RequestContext:
    app_id: str
    manifest_version: str
    company_id: str
    subject_id: str
    project_id: str | None = None
    cabinet_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    context: RequestContext
    capability: str
    operation: str
    resource: str
    params: Mapping[str, Any] = field(default_factory=dict)
    domain: str | None = None
    requested_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    app_id: str
    manifest_version: str
    subject_id: str
    capability: str
    operations: frozenset[str]
    resources: frozenset[str]
    domains: frozenset[str]
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    grant_id: str = field(default_factory=lambda: str(uuid4()))

    def allows(self, request: CapabilityRequest, *, now: datetime) -> bool:
        if (
            self.app_id != request.context.app_id
            or self.manifest_version != request.context.manifest_version
            or self.subject_id != request.context.subject_id
        ):
            return False
        if self.capability != request.capability or request.operation not in self.operations:
            return False
        if request.resource not in self.resources or request.domain not in self.domains:
            return False
        if self.issued_at is not None and now < self.issued_at:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        if self.revoked_at is not None and now >= self.revoked_at:
            return False
        return True


@dataclass(frozen=True, slots=True)
class GatewayDecision:
    decision: CapabilityDecision
    code: DecisionCode
    reason: str | None = None
    grant_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is CapabilityDecision.ALLOW


@dataclass(frozen=True, slots=True)
class AuditEvent:
    request_id: str
    occurred_at: datetime
    app_id: str
    company_id: str
    subject_id: str
    capability: str
    operation: str
    resource: str
    decision: CapabilityDecision
    code: DecisionCode
    grant_id: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "occurred_at": self.occurred_at.isoformat(),
            "app_id": self.app_id,
            "company_id": self.company_id,
            "subject_id": self.subject_id,
            "capability": self.capability,
            "operation": self.operation,
            "resource": self.resource,
            "decision": self.decision.value,
            "code": self.code.value,
            "grant_id": self.grant_id,
            "reason": self.reason,
        }


class IdentityPort(Protocol):
    def allows(self, context: RequestContext) -> bool: ...


class ScopePort(Protocol):
    def allows(self, request: CapabilityRequest) -> bool: ...


class QuotaPort(Protocol):
    def allows(self, request: CapabilityRequest) -> bool: ...


class AuditPort(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class CapabilityAdapter(Protocol):
    """Typed adapter protocol for a capability operation."""

    capability: str

    def invoke(self, request: CapabilityRequest) -> Any: ...


class CapabilityGatewayService:
    """Evaluate a declarative-app request through a fail-closed decision pipeline."""

    def __init__(
        self,
        *,
        requested_capabilities: Sequence[str],
        grants: Sequence[CapabilityGrant] = (),
        identity: IdentityPort | None = None,
        scope: ScopePort | None = None,
        quota: QuotaPort | None = None,
        audit: AuditPort | None = None,
        adapters: Mapping[str, CapabilityAdapter] | None = None,
    ) -> None:
        self._requested = frozenset(requested_capabilities)
        self._grants = tuple(grants)
        self._identity = identity
        self._scope = scope
        self._quota = quota
        self._audit = audit
        self._adapters = dict(adapters or {})

    def authorize(self, request: CapabilityRequest, *, now: datetime | None = None) -> GatewayDecision:
        checked_at = now or datetime.now(UTC)
        if not self._valid(request):
            return self._finish(request, GatewayDecision(CapabilityDecision.DENY, DecisionCode.INVALID_REQUEST), checked_at)
        if request.capability not in DECLARATIVE_CAPABILITIES:
            return self._finish(request, GatewayDecision(CapabilityDecision.DENY, DecisionCode.CAPABILITY_UNKNOWN), checked_at)
        if request.capability not in self._requested:
            return self._finish(request, GatewayDecision(CapabilityDecision.DENY, DecisionCode.CAPABILITY_NOT_REQUESTED), checked_at)
        if self._identity is None or not self._identity.allows(request.context):
            return self._finish(request, GatewayDecision(CapabilityDecision.DENY, DecisionCode.IDENTITY_DENIED), checked_at)
        if self._scope is None or not self._scope.allows(request):
            return self._finish(request, GatewayDecision(CapabilityDecision.DENY, DecisionCode.SCOPE_DENIED), checked_at)
        grant = next((candidate for candidate in self._grants if candidate.allows(request, now=checked_at)), None)
        if grant is None:
            matching = [
                candidate for candidate in self._grants
                if candidate.app_id == request.context.app_id
                and candidate.manifest_version == request.context.manifest_version
                and candidate.subject_id == request.context.subject_id
                and candidate.capability == request.capability
                and request.operation in candidate.operations
            ]
            code = DecisionCode.GRANT_MISSING
            if any(candidate.revoked_at is not None and checked_at >= candidate.revoked_at for candidate in matching):
                code = DecisionCode.GRANT_REVOKED
            elif any(candidate.issued_at is not None and checked_at < candidate.issued_at for candidate in matching):
                code = DecisionCode.GRANT_NOT_YET_ISSUED
            elif any(candidate.expires_at is not None and checked_at >= candidate.expires_at for candidate in matching):
                code = DecisionCode.GRANT_EXPIRED
            return self._finish(request, GatewayDecision(CapabilityDecision.DENY, code), checked_at)
        if self._quota is None or not self._quota.allows(request):
            return self._finish(request, GatewayDecision(CapabilityDecision.DENY, DecisionCode.QUOTA_EXCEEDED, grant_id=grant.grant_id), checked_at)
        return self._finish(request, GatewayDecision(CapabilityDecision.ALLOW, DecisionCode.ALLOWED, grant_id=grant.grant_id), checked_at)

    def invoke(self, request: CapabilityRequest, *, now: datetime | None = None) -> tuple[GatewayDecision, Any | None]:
        decision = self.authorize(request, now=now)
        if not decision.allowed:
            return decision, None
        adapter = self._adapters.get(request.capability)
        if adapter is None:
            denied = GatewayDecision(CapabilityDecision.DENY, DecisionCode.ADAPTER_MISSING, grant_id=decision.grant_id)
            self._record(request, denied, now or datetime.now(UTC))
            return denied, None
        try:
            return decision, adapter.invoke(request)
        except Exception as exc:  # adapter failures are a boundary decision, not a leak
            denied = GatewayDecision(
                CapabilityDecision.DENY,
                DecisionCode.ADAPTER_FAILED,
                "capability adapter failed",
                decision.grant_id,
            )
            self._record(request, denied, now or datetime.now(UTC))
            return denied, None

    @staticmethod
    def _valid(request: CapabilityRequest) -> bool:
        return bool(
            request.context.app_id.strip()
            and request.context.manifest_version.strip()
            and request.context.company_id.strip()
            and request.context.subject_id.strip()
            and request.capability.strip()
            and request.operation.strip()
            and request.resource.strip()
        )

    def _finish(self, request: CapabilityRequest, decision: GatewayDecision, occurred_at: datetime) -> GatewayDecision:
        self._record(request, decision, occurred_at)
        return decision

    def _record(self, request: CapabilityRequest, decision: GatewayDecision, occurred_at: datetime) -> None:
        if self._audit is not None:
            self._audit.record(
                AuditEvent(
                    request_id=request.context.request_id,
                    occurred_at=occurred_at,
                    app_id=request.context.app_id,
                    company_id=request.context.company_id,
                    subject_id=request.context.subject_id,
                    capability=request.capability,
                    operation=request.operation,
                    resource=request.resource,
                    decision=decision.decision,
                    code=decision.code,
                    grant_id=decision.grant_id,
                    reason=decision.reason,
                )
            )
