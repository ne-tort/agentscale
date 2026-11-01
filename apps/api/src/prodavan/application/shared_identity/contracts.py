"""Canonical cross-domain identity envelope for M04-M06 handoffs.

This zone is persistence-, ORM-, and transport-independent.  The envelope is
an immutable snapshot: every adapter must pass the same snapshot downstream,
with the digest covering all identity and fencing fields except the digest
itself.  Missing optional identity is represented explicitly as ``None``;
callers must not infer a broader scope or another plugin instance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


DIGEST_PREFIX = "sha256:"
_SCOPE_KINDS = frozenset({"tenant", "cabinet", "project", "plugin_instance"})


class QuarantineClassification(StrEnum):
    """Fail-closed classification shared by M04 registry and M06 promotion."""

    MALFORMED = "malformed"
    ORPHAN = "orphan"
    AMBIGUOUS = "ambiguous"
    CROSS_TENANT = "cross_tenant"
    CROSS_SCOPE = "cross_scope"
    CROSS_DEFINITION = "cross_definition"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    SUBJECT_MISMATCH = "subject_mismatch"
    SESSION_MISMATCH = "session_mismatch"
    STALE_RUNTIME = "stale_runtime"
    STALE_WRITER = "stale_writer"
    LEASE_INVALID = "lease_invalid"
    DIGEST_CONFLICT = "digest_conflict"
    UNSUPPORTED = "unsupported"


class IdentityEnvelopeError(ValueError):
    """Raised when a cross-domain identity cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class PromotionPartition:
    """Smallest bounded M06 promotion partition."""

    tenant_id: str
    definition_id: str
    version_id: str
    scope_kind: str
    scope_id: str
    plugin_instance_id: str
    subject_id: str
    session_id: str

    def __post_init__(self) -> None:
        _nonempty_fields(self, ("tenant_id", "definition_id", "version_id", "scope_kind", "scope_id", "plugin_instance_id", "subject_id", "session_id"))
        if self.scope_kind not in _SCOPE_KINDS:
            raise IdentityEnvelopeError("unsupported scope_kind")
        if self.scope_kind == "plugin_instance" and self.scope_id != self.plugin_instance_id:
            raise IdentityEnvelopeError("plugin_instance scope must equal plugin_instance_id")


@dataclass(frozen=True, slots=True)
class IdentityEnvelope:
    """Canonical identity snapshot spanning tenant through promotion fencing."""

    tenant_id: str
    scope_kind: str
    scope_id: str
    definition_id: str
    version_id: str
    artifact_digest: str
    plugin_instance_id: str
    subject_id: str
    session_id: str
    runtime_generation: int
    writer_generation: int
    lease_id: str
    lease_owner: str
    lease_until: datetime
    cabinet_id: str | None = None
    project_id: str | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    promotion_partition: PromotionPartition | None = None
    envelope_digest: str = ""

    def __post_init__(self) -> None:
        _nonempty_fields(self, ("tenant_id", "scope_kind", "scope_id", "definition_id", "version_id", "plugin_instance_id", "subject_id", "session_id", "lease_id", "lease_owner", "correlation_id", "trace_id"))
        if self.scope_kind not in _SCOPE_KINDS:
            raise IdentityEnvelopeError("unsupported scope_kind")
        if self.scope_kind == "tenant" and (self.cabinet_id or self.project_id):
            raise IdentityEnvelopeError("tenant scope cannot carry cabinet/project anchors")
        if self.scope_kind == "cabinet" and (self.scope_id != self.cabinet_id or self.project_id):
            raise IdentityEnvelopeError("cabinet scope requires matching cabinet_id only")
        if self.scope_kind == "project" and (not self.cabinet_id or self.scope_id != self.project_id):
            raise IdentityEnvelopeError("project scope requires matching project_id and cabinet_id")
        if self.scope_kind == "plugin_instance" and self.scope_id != self.plugin_instance_id:
            raise IdentityEnvelopeError("plugin_instance scope must equal plugin_instance_id")
        if self.project_id and not self.cabinet_id:
            raise IdentityEnvelopeError("project anchor requires cabinet anchor")
        _digest(self.artifact_digest, "artifact_digest")
        if self.runtime_generation < 0 or self.writer_generation < 0:
            raise IdentityEnvelopeError("generations must be non-negative")
        if self.lease_until.tzinfo is None or self.lease_until.utcoffset() is None:
            raise IdentityEnvelopeError("lease_until must be timezone-aware")
        partition = self.promotion_partition or PromotionPartition(
            tenant_id=self.tenant_id,
            definition_id=self.definition_id,
            version_id=self.version_id,
            scope_kind=self.scope_kind,
            scope_id=self.scope_id,
            plugin_instance_id=self.plugin_instance_id,
            subject_id=self.subject_id,
            session_id=self.session_id,
        )
        if self.promotion_partition is not None and partition != self.promotion_partition:
            raise IdentityEnvelopeError("promotion_partition does not match canonical identity")
        object.__setattr__(self, "promotion_partition", partition)
        expected = self.compute_digest()
        if self.envelope_digest and self.envelope_digest != expected:
            raise IdentityEnvelopeError("envelope_digest does not match canonical identity")
        object.__setattr__(self, "envelope_digest", expected)

    def canonical_payload(self) -> dict[str, Any]:
        """Return the digest input; no secrets or mutable caller mapping."""
        return _jsonable({
            "tenant_id": self.tenant_id,
            "cabinet_id": self.cabinet_id,
            "project_id": self.project_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "plugin_instance_id": self.plugin_instance_id,
            "definition_id": self.definition_id,
            "version_id": self.version_id,
            "artifact_digest": self.artifact_digest,
            "subject_id": self.subject_id,
            "session_id": self.session_id,
            "runtime_generation": self.runtime_generation,
            "writer_generation": self.writer_generation,
            "lease_id": self.lease_id,
            "lease_owner": self.lease_owner,
            "lease_until": self.lease_until.isoformat(),
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "promotion_partition": _jsonable(self.promotion_partition),
        })

    def compute_digest(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return DIGEST_PREFIX + hashlib.sha256(encoded).hexdigest()

    def with_propagation_ids(self, *, correlation_id: str, trace_id: str) -> "IdentityEnvelope":
        """Create a new snapshot when a boundary intentionally starts a span."""
        return replace(self, correlation_id=correlation_id, trace_id=trace_id, envelope_digest="")


@dataclass(frozen=True, slots=True)
class IdentityContext:
    """Envelope plus transport-safe propagation metadata."""

    envelope: IdentityEnvelope
    hop: str
    baggage: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty_fields(self, ("hop",))
        if any(not key.strip() or not value.strip() for key, value in self.baggage.items()):
            raise IdentityEnvelopeError("baggage keys and values must be non-empty")
        object.__setattr__(self, "baggage", MappingProxyType(dict(self.baggage)))

    def propagation_headers(self) -> Mapping[str, str]:
        """Only IDs and digest cross transport; never lease owner or secrets."""
        return MappingProxyType({
            "x-prodavan-identity-digest": self.envelope.envelope_digest,
            "x-prodavan-correlation-id": self.envelope.correlation_id,
            "x-prodavan-trace-id": self.envelope.trace_id,
            "x-prodavan-identity-hop": self.hop,
        })


@dataclass(frozen=True, slots=True)
class QuarantineEvidence:
    """Redacted, retryable classification that cannot authorize any write."""

    envelope: IdentityEnvelope
    classification: QuarantineClassification
    reason_code: str
    source_kind: str
    source_id: str
    source_digest: str
    checkpoint: str
    observed_at: datetime
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty_fields(self, ("reason_code", "source_kind", "source_id", "checkpoint"))
        _digest(self.source_digest, "source_digest")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise IdentityEnvelopeError("observed_at must be timezone-aware")
        if any(not key.strip() or not value.strip() for key, value in self.metadata.items()):
            raise IdentityEnvelopeError("metadata must be non-empty redacted strings")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _nonempty_fields(value: Any, names: tuple[str, ...]) -> None:
    for name in names:
        item = getattr(value, name)
        if not isinstance(item, str) or not item.strip():
            raise IdentityEnvelopeError(f"{name} must be non-empty")


def _digest(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith(DIGEST_PREFIX)
        or len(value) != len(DIGEST_PREFIX) + 64
        or any(char not in "0123456789abcdef" for char in value[len(DIGEST_PREFIX):])
    ):
        raise IdentityEnvelopeError(f"{name} must be a sha256: digest")


def _jsonable(value: Any) -> Any:
    if isinstance(value, PromotionPartition):
        return {key: getattr(value, key) for key in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "DIGEST_PREFIX", "IdentityContext", "IdentityEnvelope", "IdentityEnvelopeError",
    "PromotionPartition", "QuarantineClassification", "QuarantineEvidence",
]
