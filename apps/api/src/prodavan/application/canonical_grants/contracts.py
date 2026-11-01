"""M06 canonical grant/gateway contracts.

The contracts are transport and persistence neutral.  A positive result is only
an authorization decision; this zone never creates or mutates grants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4

from prodavan.application.plugin_instances import FencingToken, PluginInstanceIdentity, PluginInstanceWriteRequest


class CanonicalGrantDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    QUARANTINE = "quarantine"


class CanonicalGrantReason(StrEnum):
    ALLOWED = "allowed"
    INVALID_REQUEST = "invalid_request"
    COMPANY_NOT_AUTHENTICATED = "company_not_authenticated"
    TENANT_UNRESOLVED = "tenant_unresolved"
    DEFINITION_UNRESOLVED = "definition_unresolved"
    PLUGIN_FENCING_DENIED = "plugin_fencing_denied"
    GRANT_MISSING = "grant_missing"
    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_NOT_REQUESTED = "capability_not_requested"
    CAPABILITY_MISMATCH = "capability_mismatch"
    OPERATION_NOT_GRANTED = "operation_not_granted"
    RESOURCE_NOT_GRANTED = "resource_not_granted"
    DOMAIN_NOT_GRANTED = "domain_not_granted"
    CONSTRAINTS_NOT_SATISFIED = "constraints_not_satisfied"
    NOT_YET_ISSUED = "not_yet_issued"
    EXPIRED = "expired"
    REVOKED = "revoked"
    UNSUPPORTED_GRANT = "unsupported_grant"
    WILDCARD_GRANT = "wildcard_grant"
    UNREPRESENTABLE_GRANT = "unrepresentable_grant"


@dataclass(frozen=True, slots=True)
class RuntimeFencingEvidence:
    """Evidence forwarded to M05 for every runtime call."""

    plugin_instance_id: str
    subject_id: str
    session_id: str
    runtime_generation: int
    writer_generation: int
    lease_owner: str
    lease_id: str
    now: datetime
    artifact_digest: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.plugin_instance_id, self.subject_id, self.session_id, self.lease_owner, self.lease_id, self.artifact_digest)):
            raise ValueError("runtime fencing evidence fields must not be empty")
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

    def write_request(self, identity: PluginInstanceIdentity) -> PluginInstanceWriteRequest:
        return PluginInstanceWriteRequest(
            identity=identity,
            subject_id=self.subject_id,
            session_id=self.session_id,
            fencing=FencingToken(self.runtime_generation, self.writer_generation),
            lease_owner=self.lease_owner,
            lease_id=self.lease_id,
            now=self.now,
        )


@dataclass(frozen=True, slots=True)
class CanonicalGrant:
    """One exact, non-wildcard grant for one plugin instance and app version."""

    grant_id: str
    tenant_id: str
    app_definition_id: str
    app_id: str
    app_version_id: str
    app_artifact_digest: str
    scope_kind: str
    scope_id: str
    plugin_instance_id: str
    subject_id: str
    session_id: str
    capability_key: str
    operations: frozenset[str]
    resources: frozenset[str]
    domains: frozenset[str]
    constraints: Mapping[str, Any]
    issued_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    issued_by: str = ""

    def __post_init__(self) -> None:
        required = (self.grant_id, self.tenant_id, self.app_definition_id, self.app_id, self.app_version_id, self.app_artifact_digest, self.scope_kind, self.scope_id, self.plugin_instance_id, self.subject_id, self.session_id, self.capability_key)
        if not all(value.strip() for value in required) or not self.issued_by.strip() or not self.app_artifact_digest.startswith("sha256:") or len(self.app_artifact_digest) != 71:
            raise ValueError("grant identity fields must not be empty")
        if not self.operations or not self.resources or not self.domains:
            raise ValueError("grant operations, resources and domains must be non-empty")
        if any(not item.strip() for values in (self.operations, self.resources, self.domains) for item in values):
            raise ValueError("grant entries must not be empty")
        if self.issued_at.tzinfo is None or self.issued_at.utcoffset() is None:
            raise ValueError("issued_at must be timezone-aware")
        for timestamp in (self.expires_at, self.revoked_at):
            if timestamp is not None and (timestamp.tzinfo is None or timestamp.utcoffset() is None):
                raise ValueError("grant timestamps must be timezone-aware")
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if self.revoked_at is not None and self.revoked_at < self.issued_at:
            raise ValueError("revoked_at must not precede issued_at")
        if any(_is_wildcard(item) for values in (self.operations, self.resources, self.domains) for item in values):
            raise ValueError("wildcard grants are not representable")

    def check(self, *, operation: str, resource: str, domain: str, constraints: Mapping[str, Any], subject_id: str, session_id: str, now: datetime) -> CanonicalGrantReason | None:
        if self.subject_id != subject_id or self.session_id != session_id:
            return CanonicalGrantReason.CAPABILITY_MISMATCH
        if now < self.issued_at:
            return CanonicalGrantReason.NOT_YET_ISSUED
        if self.expires_at is not None and now >= self.expires_at:
            return CanonicalGrantReason.EXPIRED
        if self.revoked_at is not None and now >= self.revoked_at:
            return CanonicalGrantReason.REVOKED
        if operation not in self.operations:
            return CanonicalGrantReason.OPERATION_NOT_GRANTED
        if resource not in self.resources:
            return CanonicalGrantReason.RESOURCE_NOT_GRANTED
        if domain not in self.domains:
            return CanonicalGrantReason.DOMAIN_NOT_GRANTED
        if not _constraints_match(self.constraints, constraints):
            return CanonicalGrantReason.CONSTRAINTS_NOT_SATISFIED
        return None


def _is_wildcard(value: str) -> bool:
    return any(token in value for token in ("*", "?", "[", "]"))


def _constraints_match(granted: Mapping[str, Any], requested: Mapping[str, Any]) -> bool:
    return all(key in requested and requested[key] == value for key, value in granted.items())


@dataclass(frozen=True, slots=True)
class CanonicalGrantRequest:
    company_id: str
    app_id: str
    manifest_version: str
    plugin_instance_id: str
    capability_key: str
    operation: str
    resource: str
    domain: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    fencing: RuntimeFencingEvidence | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class CanonicalGrantDecisionResult:
    decision: CanonicalGrantDecision
    reason: CanonicalGrantReason
    correlation_id: str
    grant_id: str | None = None
    tenant_id: str | None = None
    app_version_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is CanonicalGrantDecision.ALLOW


@dataclass(frozen=True, slots=True)
class CanonicalAuditEvent:
    correlation_id: str
    occurred_at: datetime
    company_id: str
    tenant_id: str | None
    app_id: str
    manifest_version: str
    app_version_id: str | None
    plugin_instance_id: str
    capability_key: str
    operation: str
    resource: str
    domain: str
    decision: CanonicalGrantDecision
    reason: CanonicalGrantReason
    grant_id: str | None = None

    def redacted(self) -> dict[str, Any]:
        return {"correlation_id": self.correlation_id, "occurred_at": self.occurred_at.astimezone(UTC).isoformat(), "company_id": self.company_id, "tenant_id": self.tenant_id, "app_id": self.app_id, "manifest_version": self.manifest_version, "app_version_id": self.app_version_id, "plugin_instance_id": self.plugin_instance_id, "capability_key": self.capability_key, "operation": self.operation, "resource": self.resource, "domain": self.domain, "decision": self.decision.value, "reason": self.reason.value, "grant_id": self.grant_id}


__all__ = ["CanonicalAuditEvent", "CanonicalGrant", "CanonicalGrantDecision", "CanonicalGrantDecisionResult", "CanonicalGrantReason", "CanonicalGrantRequest", "RuntimeFencingEvidence"]
