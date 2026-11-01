"""Persistence-neutral contracts for exclusive durable quarantine.

A quarantine record is evidence of an unresolved or unsafe migration decision.  It
is never an authorization result and never contains raw payloads or secrets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

_DIGEST_NAMES = ("source_digest", "canonical_digest", "binding_digest")


class QuarantineReason(StrEnum):
    IDENTITY_CONFLICT = "identity_conflict"
    CROSS_TENANT = "cross_tenant"
    MISSING_IDENTITY = "missing_identity"
    DIGEST_CONFLICT = "digest_conflict"
    UNSUPPORTED_MAPPING = "unsupported_mapping"
    DUPLICATE_ACTIVE_IDENTITY = "duplicate_active_identity"
    INVALID_PROVENANCE = "invalid_provenance"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class QuarantinePartition:
    """Bounded migration partition; every record belongs to exactly one tenant."""

    tenant_id: str
    partition_key: str

    def __post_init__(self) -> None:
        _required(self.tenant_id, "tenant_id")
        _required(self.partition_key, "partition_key")


@dataclass(frozen=True, slots=True)
class QuarantineIdentity:
    """Full identity needed to prevent a quarantined row becoming a broad allow."""

    tenant_id: str
    app_definition_id: str
    app_id: str
    app_version_id: str
    manifest_version: str
    scope_kind: str
    scope_id: str
    subject_kind: str
    subject_id: str
    plugin_instance_id: str | None = None
    session_id: str | None = None
    runtime_generation: int | None = None
    writer_generation: int | None = None
    capability_key: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "tenant_id", "app_definition_id", "app_id", "app_version_id",
            "manifest_version", "scope_kind", "scope_id", "subject_kind", "subject_id",
        ):
            _required(getattr(self, name), name)
        if self.plugin_instance_id is not None:
            _required(self.plugin_instance_id, "plugin_instance_id")
        if self.session_id is not None:
            _required(self.session_id, "session_id")
        if self.capability_key is not None:
            _required(self.capability_key, "capability_key")
        for name in ("runtime_generation", "writer_generation"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class PromotionLineage:
    """Forward-only lineage linking retries and a later promotion decision."""

    source_kind: str
    source_id: str
    checkpoint: str
    attempt: int = 1
    parent_record_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("source_kind", "source_id", "checkpoint"):
            _required(getattr(self, name), name)
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if self.parent_record_id is not None:
            _required(self.parent_record_id, "parent_record_id")


@dataclass(frozen=True, slots=True)
class ImmutableEvidence:
    """Redacted, immutable evidence snapshot; values are copied and cannot mutate."""

    evidence_id: str
    captured_at: datetime
    fields: Mapping[str, str]

    def __post_init__(self) -> None:
        _required(self.evidence_id, "evidence_id")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        if not isinstance(self.fields, Mapping) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str)
            or not value.strip() for key, value in self.fields.items()
        ):
            raise ValueError("evidence fields must contain non-empty strings")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    """Canonical durable record.  It has no trusted-success or authorization state."""

    record_id: str
    idempotency_key: str
    partition: QuarantinePartition
    identity: QuarantineIdentity
    reason: QuarantineReason
    source_digest: str
    canonical_digest: str
    binding_digest: str
    promotion_lineage: PromotionLineage
    correlation_id: str
    trace_id: str
    span_id: str
    evidence: ImmutableEvidence
    created_at: datetime
    schema_version: str = "durable-quarantine.v1"

    def __post_init__(self) -> None:
        for name in ("record_id", "idempotency_key", "correlation_id", "trace_id", "span_id", "schema_version"):
            _required(getattr(self, name), name)
        if not isinstance(self.reason, QuarantineReason):
            raise ValueError("reason must use QuarantineReason")
        if self.partition.tenant_id != self.identity.tenant_id:
            raise ValueError("partition and identity tenant_id must match")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        for name in _DIGEST_NAMES:
            _sha256(getattr(self, name), name)

    @property
    def fingerprint(self) -> tuple[str, ...]:
        """All immutable content used to compare duplicate idempotency attempts."""
        identity = self.identity
        lineage = self.promotion_lineage
        evidence = self.evidence
        return (
            self.partition.tenant_id, self.partition.partition_key, self.idempotency_key,
            identity.tenant_id, identity.app_definition_id, identity.app_id,
            identity.app_version_id, identity.manifest_version, identity.scope_kind,
            identity.scope_id, identity.subject_kind, identity.subject_id,
            identity.plugin_instance_id or "", identity.session_id or "",
            str(identity.runtime_generation), str(identity.writer_generation),
            identity.capability_key or "", self.source_digest, self.canonical_digest,
            self.binding_digest, self.reason.value, lineage.source_kind, lineage.source_id,
            lineage.checkpoint, str(lineage.attempt), lineage.parent_record_id or "",
            self.correlation_id, self.trace_id, self.span_id, evidence.evidence_id,
            evidence.captured_at.isoformat(), repr(sorted(evidence.fields.items())),
            self.schema_version,
        )


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{name} must be a sha256: digest")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a sha256: digest") from exc


__all__ = [
    "ImmutableEvidence", "PromotionLineage", "QuarantineIdentity", "QuarantinePartition",
    "QuarantineReason", "QuarantineRecord",
]
