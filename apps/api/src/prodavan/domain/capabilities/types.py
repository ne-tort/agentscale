"""Capability gateway domain primitives.

These types deliberately contain no persistence or transport concerns.  Gateway
adapters can implement the decision protocols while callers depend on stable,
typed authorization and audit contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol


class CapabilityDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class DecisionCode(StrEnum):
    ALLOWED = "allowed"
    CAPABILITY_UNKNOWN = "capability_unknown"
    OPERATION_NOT_DECLARED = "operation_not_declared"
    GRANT_MISSING = "grant_missing"
    GRANT_EXPIRED = "grant_expired"
    ACL_DENIED = "acl_denied"
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """The versioned capabilities and operations exposed by a provider."""

    capability: str
    version: str
    operations: tuple[str, ...]
    description: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("capability must not be empty")
        if not self.version.strip():
            raise ValueError("version must not be empty")
        if not self.operations or any(not operation.strip() for operation in self.operations):
            raise ValueError("manifest must declare at least one non-empty operation")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("manifest operations must be unique")

    def declares(self, operation: str) -> bool:
        return operation in self.operations


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    """Request scope used by ACL, quota, and audit implementations."""

    company_id: str
    subject_id: str
    project_id: str | None = None
    cabinet_id: str | None = None
    pod_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.company_id.strip():
            raise ValueError("company_id must not be empty")
        if not self.subject_id.strip():
            raise ValueError("subject_id must not be empty")


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    """A single operation authorization request."""

    capability: str
    operation: str
    resource: str
    context: CapabilityContext
    request_id: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("capability must not be empty")
        if not self.operation.strip():
            raise ValueError("operation must not be empty")
        if not self.resource.strip():
            raise ValueError("resource must not be empty")


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """An explicit allow grant for a subject and a bounded operation set."""

    grant_id: str
    capability: str
    subject_id: str
    operations: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime | None = None
    resource_prefix: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.grant_id.strip() or not self.capability.strip() or not self.subject_id.strip():
            raise ValueError("grant_id, capability, and subject_id must not be empty")
        if not self.operations or any(not operation.strip() for operation in self.operations):
            raise ValueError("grant must declare at least one operation")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("grant operations must be unique")
        if self.expires_at is not None and self.expires_at < self.issued_at:
            raise ValueError("expires_at must be after issued_at")

    def allows(self, request: CapabilityRequest, *, now: datetime) -> bool:
        if self.capability != request.capability or self.subject_id != request.context.subject_id:
            return False
        if request.operation not in self.operations:
            return False
        if self.resource_prefix is not None and not request.resource.startswith(self.resource_prefix):
            return False
        return self.expires_at is None or now < self.expires_at


@dataclass(frozen=True, slots=True)
class AclDecision:
    allowed: bool
    code: DecisionCode
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    allowed: bool
    code: DecisionCode = DecisionCode.ALLOWED
    remaining: int | None = None
    retry_after_seconds: int | None = None
    reason: str | None = None


class CapabilityAcl(Protocol):
    """Port for evaluating resource and subject ACLs."""

    def decide(self, request: CapabilityRequest, context: CapabilityContext) -> AclDecision: ...


class CapabilityQuota(Protocol):
    """Port for evaluating usage limits for a capability operation."""

    def decide(self, request: CapabilityRequest, context: CapabilityContext) -> QuotaDecision: ...


@dataclass(frozen=True, slots=True)
class CapabilityAuditEnvelope:
    """Structured, secret-free audit record for an authorization decision."""

    event_id: str
    occurred_at: datetime
    request: CapabilityRequest
    decision: CapabilityDecision
    code: DecisionCode
    actor_id: str
    company_id: str
    grant_id: str | None = None
    reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.actor_id.strip() or not self.company_id.strip():
            raise ValueError("event_id, actor_id, and company_id must not be empty")
        if self.company_id != self.request.context.company_id:
            raise ValueError("audit company_id must match request context")

    def to_dict(self) -> dict[str, Any]:
        """Return a transport-neutral audit payload with no grant parameters."""
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.isoformat(),
            "request_id": self.request.request_id,
            "capability": self.request.capability,
            "operation": self.request.operation,
            "resource": self.request.resource,
            "subject_id": self.request.context.subject_id,
            "company_id": self.company_id,
            "decision": self.decision.value,
            "code": self.code.value,
            "actor_id": self.actor_id,
            "grant_id": self.grant_id,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


class CapabilityAuditSink(Protocol):
    """Port for persisting or publishing capability audit envelopes."""

    def record(self, envelope: CapabilityAuditEnvelope) -> None: ...


@dataclass(frozen=True, slots=True)
class CapabilityAuthorization:
    decision: CapabilityDecision
    code: DecisionCode
    reason: str | None = None
    grant_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is CapabilityDecision.ALLOW
