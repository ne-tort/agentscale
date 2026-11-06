"""M06 promotion, quarantine, and redacted audit contracts.

This zone is deliberately persistence-neutral.  Records are immutable evidence;
ports may append them, but the application contract never performs database or
ORM writes and never treats a live status flag as promotion authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


class PromotionOutcome(StrEnum):
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"
    SHADOW = "shadow"
    PERSISTENCE_READY = "persistence_ready"
    PROMOTABLE = "promotable"
    CANONICAL_OWNER = "canonical_owner"


# The order is part of the evidence contract.  A record is not promotable when
# even one gate is absent; callers must not infer readiness from a status flag.
REQUIRED_GATE_KEYS: tuple[str, ...] = (
    "tenant_context",
    "request_identity",
    "scope_parent",
    "definition_version",
    "instance_runtime",
    "capability_mapping",
    "canonical_shape",
    "source_mapping",
    "writer_cas",
    "rls_constraints",
    "audit_append",
    "final_delta_fence",
)


class QuarantineKind(StrEnum):
    UNSUPPORTED_GRANT = "unsupported_grant"
    UNSUPPORTED_MAPPING = "unsupported_mapping"
    AUDIT_FAILURE = "audit_failure"
    ORPHAN_BINDING = "orphan_binding"
    AMBIGUOUS_BINDING = "ambiguous_binding"
    CROSS_TENANT = "cross_tenant"
    CROSS_DEFINITION = "cross_definition"
    MISSING_SUBJECT = "missing_subject"
    MISSING_INSTANCE = "missing_instance"
    INVALID_LIFECYCLE = "invalid_lifecycle"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    STALE_GENERATION = "stale_generation"
    DUPLICATE_ACTIVE_IDENTITY = "duplicate_active_identity"
    DIGEST_CONFLICT = "digest_conflict"
    BROADENING_TRANSFORM = "broadening_transform"


class AuditResult(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PromotionPartitionKey:
    """Smallest bounded authorization partition with complete narrowed identity."""

    tenant_id: str
    app_definition_id: str
    scope_kind: str | None = None
    scope_id: str | None = None
    plugin_instance_id: str | None = None
    definition_version: str | None = None
    artifact_digest: str | None = None
    subject_id: str | None = None
    session_id: str | None = None
    runtime_generation: int | None = None
    lease_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.tenant_id, "tenant_id")
        _required(self.app_definition_id, "app_definition_id")
        if (self.scope_kind is None) != (self.scope_id is None):
            raise ValueError("scope_kind and scope_id must be supplied together")
        for name in ("scope_kind", "scope_id", "plugin_instance_id"):
            value = getattr(self, name)
            if value is not None:
                _required(value, name)
        if self.scope_kind is not None and self.scope_kind not in {"tenant", "cabinet", "project", "plugin_instance"}:
            raise ValueError("unsupported scope_kind")
        narrowed = (
            self.scope_kind is not None
            or self.scope_id is not None
            or self.plugin_instance_id is not None
            or self.subject_id is not None
            or self.session_id is not None
            or self.definition_version is not None
            or self.artifact_digest is not None
            or self.runtime_generation is not None
            or self.lease_id is not None
        )
        if narrowed:
            required = (
                "scope_kind", "scope_id", "plugin_instance_id", "definition_version",
                "artifact_digest", "subject_id", "session_id", "runtime_generation", "lease_id",
            )
            if any(getattr(self, name) in (None, "") for name in required):
                raise ValueError("complete narrowed partition identity is required")
        if self.scope_kind == "plugin_instance" and self.scope_id != self.plugin_instance_id:
            raise ValueError("plugin_instance scope must match plugin_instance_id")
        if self.definition_version is not None:
            _required(self.definition_version, "definition_version")
        if self.artifact_digest is not None:
            _digest(self.artifact_digest, "artifact_digest")
        for name in ("subject_id", "session_id", "lease_id"):
            value = getattr(self, name)
            if value is not None:
                _required(value, name)
        if self.runtime_generation is not None and self.runtime_generation <= 0:
            raise ValueError("runtime_generation must be positive")


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    """Append-only evidence for one partition promotion attempt."""

    partition: PromotionPartitionKey
    source_snapshot_id: str
    source_schema_version: str
    source_digest: str
    canonical_digest: str
    binding_digest: str
    final_delta_checkpoint: str
    gate_evidence: Mapping[str, Any]
    fencing_epoch: int
    writer_generation: int
    decision_owner: str
    actor: str
    decided_at: datetime
    outcome: PromotionOutcome
    canonical_positive_writes: bool = False
    reviewed_final: bool = False
    reviewer: str | None = None
    parent_record_id: str | None = None
    lineage_reference: str | None = None
    rollback_window: str | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    record_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        for name in ("source_snapshot_id", "source_schema_version", "final_delta_checkpoint", "decision_owner", "actor", "correlation_id", "record_id"):
            _required(getattr(self, name), name)
        for name in ("source_digest", "canonical_digest", "binding_digest"):
            _digest(getattr(self, name), name)
        if self.fencing_epoch <= 0 or self.writer_generation <= 0:
            raise ValueError("fencing_epoch and writer_generation must be positive")
        missing = [key for key in REQUIRED_GATE_KEYS if key not in self.gate_evidence]
        if missing:
            raise ValueError("missing required gate evidence: " + ", ".join(missing))
        if any(not _json_value(value) for value in self.gate_evidence.values()):
            raise ValueError("gate_evidence must contain JSON values")
        _aware(self.decided_at, "decided_at")
        if self.rollback_window is not None:
            _required(self.rollback_window, "rollback_window")
        if self.reviewer is not None:
            _required(self.reviewer, "reviewer")
        if self.parent_record_id is not None:
            _required(self.parent_record_id, "parent_record_id")
        if self.lineage_reference is not None:
            _required(self.lineage_reference, "lineage_reference")
        if self.reviewed_final:
            if not self.parent_record_id or not self.lineage_reference:
                raise ValueError("reviewed_final requires parent_record_id and lineage_reference")
            if self.outcome is not PromotionOutcome.CANONICAL_OWNER or not self.reviewer:
                raise ValueError("only a reviewed canonical_owner record may be reviewed_final")
            if not all(
                isinstance(self.gate_evidence.get(key), Mapping)
                and self.gate_evidence[key].get("passed") is True
                for key in REQUIRED_GATE_KEYS
            ):
                raise ValueError("reviewed_final requires passing gate evidence")
        if self.outcome is PromotionOutcome.CANONICAL_OWNER and not self.reviewed_final:
            raise ValueError("canonical_owner requires a reviewed final record")
        if self.canonical_positive_writes:
            if not self.reviewed_final or self.outcome is not PromotionOutcome.CANONICAL_OWNER:
                raise ValueError("canonical_positive_writes requires a reviewed final record")
        object.__setattr__(self, "gate_evidence", _freeze_mapping(self.gate_evidence, "gate_evidence"))


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    """Redacted, retryable evidence for data that cannot enter canonical grants."""

    partition: PromotionPartitionKey
    kind: QuarantineKind
    source_kind: str
    source_id: str
    source_schema_version: str
    source_digest: str
    reason_code: str
    observed_at: datetime
    checkpoint: str
    resolved_identities: Mapping[str, str] = field(default_factory=dict)
    redacted_metadata: Mapping[str, Any] = field(default_factory=dict)
    record_id: str = field(default_factory=lambda: str(uuid4()))
    canonical_positive_writes: bool = False

    def __post_init__(self) -> None:
        for name in ("source_kind", "source_id", "source_schema_version", "reason_code", "checkpoint", "record_id"):
            _required(getattr(self, name), name)
        _digest(self.source_digest, "source_digest")
        _aware(self.observed_at, "observed_at")
        if self.canonical_positive_writes:
            raise ValueError("quarantine records cannot enable canonical positive writes")
        _string_mapping(self.resolved_identities, "resolved_identities")
        _json_mapping(self.redacted_metadata, "redacted_metadata")
        object.__setattr__(self, "resolved_identities", _freeze_mapping(self.resolved_identities, "resolved_identities"))
        object.__setattr__(self, "redacted_metadata", _freeze_mapping(self.redacted_metadata, "redacted_metadata"))


@dataclass(frozen=True, slots=True)
class RedactedAuditOutcome:
    """MCP invocation outcome with digests instead of raw arguments or secrets."""

    mcp_trace_id: str
    span_id: str
    profile_id: str
    tool_server: str
    tool_name: str
    tool_version: str
    argument_digest: str
    result: AuditResult
    duration_ms: int
    occurred_at: datetime
    error_code: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("mcp_trace_id", "span_id", "profile_id", "tool_server", "tool_name", "tool_version"):
            _required(getattr(self, name), name)
        _digest(self.argument_digest, "argument_digest")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        _aware(self.occurred_at, "occurred_at")
        if self.result is AuditResult.ERROR and not self.error_code:
            raise ValueError("error_code is required for an error result")
        if self.error_code is not None:
            _required(self.error_code, "error_code")
        if self.correlation_id is not None:
            _required(self.correlation_id, "correlation_id")

    def redacted(self) -> dict[str, Any]:
        """Return the only wire shape permitted for audit persistence."""
        return {
            "mcp_trace_id": self.mcp_trace_id,
            "span_id": self.span_id,
            "profile_id": self.profile_id,
            "tool_server": self.tool_server,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "argument_digest": self.argument_digest,
            "result": self.result.value,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "correlation_id": self.correlation_id,
        }


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _digest(value: str, name: str) -> None:
    _required(value, name)
    encoded = value.removeprefix("sha256:")
    if len(encoded) != 64 or any(char not in "0123456789abcdefABCDEF" for char in encoded) or not value.startswith("sha256:"):
        raise ValueError(f"{name} must be a sha256: digest")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _string_mapping(value: Mapping[str, str], name: str) -> None:
    if any(not isinstance(key, str) or not key.strip() or not isinstance(item, str) or not item.strip() for key, item in value.items()):
        raise ValueError(f"{name} must contain non-empty strings")


def _json_mapping(value: Mapping[str, Any], name: str) -> None:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) or not key.strip() or not _json_value(item) for key, item in value.items()):
        raise ValueError(f"{name} must contain redacted JSON values")


def _json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and key.strip() and _json_value(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_json_value(item) for item in value)
    return False


def _freeze_mapping(value: Mapping[str, Any], name: str) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return MappingProxyType({
        key: _freeze_value(item, name)
        for key, item in value.items()
    })


def _freeze_value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value, name)
    if isinstance(value, list):
        return tuple(_freeze_value(item, name) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item, name) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item, name) for item in value)
    return value


__all__ = [
    "AuditResult", "PromotionOutcome", "PromotionPartitionKey", "PromotionRecord",
    "QuarantineKind", "QuarantineRecord", "RedactedAuditOutcome",
]
