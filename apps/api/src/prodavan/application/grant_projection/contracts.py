"""Contracts for the M04.e/M05/M06 canonical grant projection adapter.

This zone composes decisions only.  It does not own canonical grants, dynamic
registry rows, or persistence sessions; a repository-owned atomic port is the
only way to materialize the resulting transition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from prodavan.application.canonical_grants import CanonicalGrantDecisionResult, RuntimeFencingEvidence
from prodavan.application.dynamic_apps_definition import DefinitionVersionRef


class GrantProjectionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    QUARANTINE = "quarantine"


class GrantProjectionReason(StrEnum):
    ALLOWED = "allowed"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_SOURCE = "unsupported_source"
    LEGACY_SOURCE = "legacy_source"
    WILDCARD_SOURCE = "wildcard_source"
    DEFINITION_UNRESOLVED = "definition_unresolved"
    RUNTIME_FENCING_DENIED = "runtime_fencing_denied"
    GRANT_DENIED = "grant_denied"
    GRANT_QUARANTINED = "grant_quarantined"
    DEFINITION_QUARANTINED = "definition_quarantined"


@dataclass(frozen=True, slots=True)
class GrantProjectionRequest:
    """Exact source decision context; caller ownership is never inferred."""

    tenant_id: str
    company_id: str
    app_id: str
    manifest_version: str
    plugin_instance_id: str
    scope_kind: str
    scope_id: str
    subject_id: str
    session_id: str
    capability_key: str
    operation: str
    resource: str
    domain: str
    fencing: RuntimeFencingEvidence
    source_kind: str = "canonical"
    source_schema_version: str = "canonical.v1"
    constraints: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "tenant_id", "company_id", "app_id", "manifest_version", "plugin_instance_id",
            "scope_kind", "scope_id", "subject_id", "session_id", "capability_key",
            "operation", "resource", "domain", "source_kind", "source_schema_version",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.scope_kind not in {"tenant", "cabinet", "project", "plugin_instance"}:
            raise ValueError("scope_kind is not supported")
        if self.scope_kind == "plugin_instance" and self.scope_id != self.plugin_instance_id:
            raise ValueError("plugin_instance scope_id must match plugin_instance_id")
        if self.fencing.plugin_instance_id != self.plugin_instance_id:
            raise ValueError("fencing plugin_instance_id must match request")
        if self.fencing.subject_id != self.subject_id or self.fencing.session_id != self.session_id:
            raise ValueError("fencing subject/session must match request")
        if not isinstance(self.constraints, Mapping):
            raise ValueError("constraints must be a mapping")


@dataclass(frozen=True, slots=True)
class GrantProjectionCommand:
    """Immutable command accepted by the repository-owned atomic transition."""

    request: GrantProjectionRequest
    decision: GrantProjectionDecision
    reason: GrantProjectionReason
    definition: DefinitionVersionRef | None = None
    grant: CanonicalGrantDecisionResult | None = None


@dataclass(frozen=True, slots=True)
class GrantProjectionResult:
    request: GrantProjectionRequest
    decision: GrantProjectionDecision
    reason: GrantProjectionReason
    definition: DefinitionVersionRef | None = None
    grant: CanonicalGrantDecisionResult | None = None
    projected: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is GrantProjectionDecision.ALLOW

    @property
    def quarantined(self) -> bool:
        return self.decision is GrantProjectionDecision.QUARANTINE

    @property
    def requested_capability_key(self) -> str:
        return self.request.capability_key

    @property
    def granted_capability_key(self) -> str | None:
        return self.request.capability_key if self.allowed else None

    @property
    def requested_operation(self) -> str:
        return self.request.operation

    @property
    def requested_resource(self) -> str:
        return self.request.resource

    @property
    def requested_domain(self) -> str:
        return self.request.domain

    @property
    def granted_operation(self) -> str | None:
        return self.request.operation if self.allowed else None

    @property
    def granted_resource(self) -> str | None:
        return self.request.resource if self.allowed else None

    @property
    def granted_domain(self) -> str | None:
        return self.request.domain if self.allowed else None

    @property
    def requested_constraints(self) -> Mapping[str, Any]:
        return self.request.constraints

    @property
    def granted_constraints(self) -> Mapping[str, Any] | None:
        return self.request.constraints if self.allowed else None

    @property
    def granted_scope(self) -> tuple[str, str] | None:
        return (self.request.scope_kind, self.request.scope_id) if self.allowed else None

    @property
    def granted_subject_id(self) -> str | None:
        return self.request.subject_id if self.allowed else None

    @property
    def granted_session_id(self) -> str | None:
        return self.request.session_id if self.allowed else None


__all__ = [
    "GrantProjectionCommand", "GrantProjectionDecision", "GrantProjectionReason",
    "GrantProjectionRequest", "GrantProjectionResult",
]
