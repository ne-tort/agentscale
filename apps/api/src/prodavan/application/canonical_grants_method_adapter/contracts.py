"""Decision-only MCP method to canonical-grant adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from prodavan.application.canonical_grants import (
    CanonicalGrantDecisionResult,
    RuntimeFencingEvidence,
)
from prodavan.application.capability_method_mapping import (
    MappingAuditMetadata,
    MappingResult,
    MethodIdentity,
)


@dataclass(frozen=True, slots=True)
class McpAuditCorrelation:
    """Redacted MCP correlation carried across both decision services."""

    trace_id: str
    span_id: str
    correlation_id: str
    actor: str
    request_digest: str
    occurred_at: str

    def __post_init__(self) -> None:
        for name in ("trace_id", "span_id", "correlation_id", "actor", "request_digest", "occurred_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if not self.request_digest.startswith("sha256:") or len(self.request_digest) != 71:
            raise ValueError("request_digest must be a sha256: digest")

    def mapping_audit(self) -> MappingAuditMetadata:
        return MappingAuditMetadata(
            trace_id=self.trace_id,
            span_id=self.span_id,
            actor=self.actor,
            request_digest=self.request_digest,
            occurred_at=self.occurred_at,
            extra={"correlation_id": self.correlation_id},
        )


@dataclass(frozen=True, slots=True)
class CanonicalGrantsMethodRequest:
    """One exact MCP invocation and its canonical grant lookup context."""

    company_id: str
    app_id: str
    manifest_version: str
    plugin_instance_id: str
    identity: MethodIdentity
    capability_key: str
    operation: str
    protocol_version: str
    tool_version: str
    resource: str
    domain: str
    mcp: McpAuditCorrelation
    constraints: Mapping[str, Any] = field(default_factory=dict)
    fencing: RuntimeFencingEvidence | None = None

    def __post_init__(self) -> None:
        for name in ("company_id", "app_id", "manifest_version", "plugin_instance_id", "capability_key", "operation", "protocol_version", "tool_version", "resource", "domain"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not isinstance(self.constraints, Mapping):
            raise ValueError("constraints must be a mapping")


@dataclass(frozen=True, slots=True)
class CanonicalGrantsMethodDecision:
    """Combined decision preserving requested and mapped/granted evidence."""

    request: CanonicalGrantsMethodRequest
    mapping: MappingResult
    grant: CanonicalGrantDecisionResult | None
    requested_capability_key: str
    requested_operation: str
    requested_resource: str
    requested_domain: str
    requested_constraints: Mapping[str, Any]
    granted_capability_key: str | None = None
    granted_operation: str | None = None
    granted_resources: tuple[str, ...] = ()
    granted_domains: tuple[str, ...] = ()
    granted_constraints: Mapping[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.grant is not None and self.grant.allowed


__all__ = ["CanonicalGrantsMethodDecision", "CanonicalGrantsMethodRequest", "McpAuditCorrelation"]
