"""Versioned, redacted MCP audit outcome contracts.

This zone contains immutable wire evidence only. Raw MCP arguments, payloads,
secrets, and persistence concerns are intentionally outside the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class AuditDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    QUARANTINE = "quarantine"


class AuditResult(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ERROR = "error"


REDACTION_VERSION = "1"


@dataclass(frozen=True, slots=True)
class AuditOutcome:
    """Complete M06 audit envelope with redacted MCP evidence only."""

    occurred_at: datetime
    event_id: str
    event_type: str
    decision: AuditDecision
    reason_code: str
    tenant_id: str
    app_definition_id: str
    app_id: str
    app_version_id: str
    request_id: str
    trace_id: str
    span_id: str
    profile_id: str
    tool_server: str
    tool_name: str
    tool_version: str
    arguments_digest: str
    operation: str
    resource: str
    domain: str
    payload_digest: str
    result_status: AuditResult
    error_code: str | None
    duration_ms: int
    correlation_id: str
    cabinet_id: str | None = None
    project_id: str | None = None
    user_id: str | None = None
    subject_id: str | None = None
    session_id: str | None = None
    manifest_version: str | None = None
    app_artifact_digest: str | None = None
    plugin_instance_id: str | None = None
    runtime_generation: int | None = None
    writer_generation: int | None = None
    lease_id: str | None = None
    capability_grant_id: str | None = None
    capability_key: str | None = None
    source_kind: str | None = None
    source_id: str | None = None
    mapping_version: str | None = None
    capability_mapping_digest: str | None = None
    capability_snapshot_digest: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    redaction_version: str = REDACTION_VERSION

    def __post_init__(self) -> None:
        for name in (
            "event_id", "event_type", "reason_code", "tenant_id", "app_definition_id",
            "app_id", "app_version_id", "request_id", "trace_id", "span_id", "profile_id",
            "tool_server", "tool_name", "tool_version", "operation", "resource", "domain",
            "correlation_id",
        ):
            _required(getattr(self, name), name)
        for name in (
            "cabinet_id", "project_id", "user_id", "subject_id", "session_id", "manifest_version",
            "plugin_instance_id", "lease_id", "capability_grant_id", "capability_key",
            "source_kind", "source_id", "mapping_version",
        ):
            value = getattr(self, name)
            if value is not None:
                _required(value, name)
        for name in ("arguments_digest", "payload_digest", "app_artifact_digest", "capability_mapping_digest", "capability_snapshot_digest"):
            value = getattr(self, name)
            if value is not None:
                _digest(value, name)
        if not isinstance(self.decision, AuditDecision) or not isinstance(self.result_status, AuditResult):
            raise ValueError("decision must be AuditDecision and result_status must be AuditResult")
        if self.decision is AuditDecision.ALLOW and self.result_status is not AuditResult.ALLOW:
            raise ValueError("allow decision must have allow result_status")
        if self.decision is AuditDecision.DENY and self.result_status is AuditResult.ALLOW:
            raise ValueError("deny decision cannot have allow result_status")
        if self.decision is AuditDecision.QUARANTINE and self.result_status is not AuditResult.ERROR:
            raise ValueError("quarantine decision must have error result_status")
        if self.result_status is AuditResult.ALLOW and self.error_code is not None:
            raise ValueError("allow result cannot contain an error_code")
        if self.result_status is not AuditResult.ALLOW and self.error_code is None:
            raise ValueError("error_code is required for deny or error result")
        if self.error_code is not None:
            _required(self.error_code, "error_code")
        if not isinstance(self.duration_ms, int) or isinstance(self.duration_ms, bool) or self.duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative integer")
        if self.runtime_generation is not None and self.runtime_generation <= 0:
            raise ValueError("runtime_generation must be positive")
        if self.writer_generation is not None and self.writer_generation <= 0:
            raise ValueError("writer_generation must be positive")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.redaction_version != REDACTION_VERSION:
            raise ValueError("unsupported redaction_version")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def redacted(self) -> dict[str, Any]:
        """Return the complete persistence/wire envelope; no raw values are added."""
        return {
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "event_id": self.event_id,
            "event_type": self.event_type,
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "tenant_id": self.tenant_id,
            "cabinet_id": self.cabinet_id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "subject_id": self.subject_id,
            "session_id": self.session_id,
            "app_definition_id": self.app_definition_id,
            "app_id": self.app_id,
            "app_version_id": self.app_version_id,
            "manifest_version": self.manifest_version,
            "app_artifact_digest": self.app_artifact_digest,
            "plugin_instance_id": self.plugin_instance_id,
            "runtime_generation": self.runtime_generation,
            "writer_generation": self.writer_generation,
            "lease_id": self.lease_id,
            "capability_grant_id": self.capability_grant_id,
            "capability_key": self.capability_key,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "mapping_version": self.mapping_version,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "profile_id": self.profile_id,
            "tool_server": self.tool_server,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "arguments_digest": self.arguments_digest,
            "operation": self.operation,
            "resource": self.resource,
            "domain": self.domain,
            "payload_digest": self.payload_digest,
            "result_status": self.result_status.value,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "correlation_id": self.correlation_id,
            "capability_mapping_digest": self.capability_mapping_digest,
            "capability_snapshot_digest": self.capability_snapshot_digest,
            "metadata": dict(self.metadata),
            "redaction_version": self.redaction_version,
        }


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _digest(value: str, name: str) -> None:
    _required(value, name)
    encoded = value.removeprefix("sha256:")
    if not value.startswith("sha256:") or len(encoded) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in encoded
    ):
        raise ValueError(f"{name} must be a sha256: digest")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping")
    forbidden = {"args", "arguments", "payload", "raw_input", "secret", "secrets"}
    if any(str(key).casefold() in forbidden for key in value):
        raise ValueError("metadata must not contain raw input or secrets")
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ValueError("metadata must contain JSON values")


__all__ = ["AuditDecision", "AuditOutcome", "AuditResult", "REDACTION_VERSION"]
