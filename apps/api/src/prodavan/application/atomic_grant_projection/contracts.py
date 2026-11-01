"""Exclusive transaction command for the M04/M05/M06 grant projection boundary.

The command is intentionally persistence-neutral.  It carries every identity and
migration evidence item needed by one repository-owned transaction; callers
cannot perform a positive grant write, quarantine write, or promotion transition
through separate application calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from prodavan.application.canonical_grants import CanonicalGrantDecisionResult
from prodavan.application.canonical_grants_promotion import (
    PromotionPartitionKey,
    PromotionRecord,
    QuarantineRecord,
)


class AtomicProjectionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    QUARANTINE = "quarantine"


class AtomicProjectionReason(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    MISSING_CONTEXT = "missing_context"
    MISSING_PARTITION_EVIDENCE = "missing_partition_evidence"
    QUARANTINED = "quarantined"
    PROMOTION_NOT_READY = "promotion_not_ready"
    CONTEXT_MISMATCH = "context_mismatch"
    STALE_WRITER = "stale_writer"


@dataclass(frozen=True, slots=True)
class M04GrantProjectionContext:
    """Server-resolved owner/definition evidence from M04."""

    tenant_id: str
    scope_kind: str
    scope_id: str
    app_definition_id: str
    app_id: str
    app_version_id: str
    manifest_version: str
    artifact_digest: str
    owner_evidence_digest: str

    def __post_init__(self) -> None:
        _required(self, ("tenant_id", "scope_kind", "scope_id", "app_definition_id", "app_id", "app_version_id", "manifest_version", "artifact_digest", "owner_evidence_digest"))
        if self.scope_kind not in {"tenant", "cabinet", "project", "plugin_instance"}:
            raise ValueError("scope_kind must be a typed M04 scope")
        _sha256(self.artifact_digest, "artifact_digest")


@dataclass(frozen=True, slots=True)
class M05GrantProjectionContext:
    """Exact plugin-instance and runtime fencing evidence from M05."""

    plugin_instance_id: str
    subject_id: str
    session_id: str
    runtime_generation: int
    writer_generation: int
    lease_id: str
    lease_owner: str
    artifact_digest: str

    def __post_init__(self) -> None:
        _required(self, ("plugin_instance_id", "subject_id", "session_id", "lease_id", "lease_owner", "artifact_digest"))
        if self.runtime_generation <= 0 or self.writer_generation <= 0:
            raise ValueError("M05 generations must be positive")
        _sha256(self.artifact_digest, "artifact_digest")


@dataclass(frozen=True, slots=True)
class AtomicGrantProjectionCommand:
    """The only command accepted by the repository-owned transaction port."""

    m04: M04GrantProjectionContext
    m05: M05GrantProjectionContext
    canonical_decision: CanonicalGrantDecisionResult
    partition: PromotionPartitionKey
    promotion_evidence: PromotionRecord | None
    quarantine_evidence: QuarantineRecord | None
    decision: AtomicProjectionDecision
    reason: AtomicProjectionReason
    issued_at: datetime
    capability_key: str
    operation: str
    resource: str
    domain: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str = ""

    def __post_init__(self) -> None:
        _required(self, ("capability_key", "operation", "resource", "domain", "correlation_id"))
        if self.issued_at.tzinfo is None or self.issued_at.utcoffset() is None:
            raise ValueError("issued_at must be timezone-aware")
        if not isinstance(self.constraints, Mapping):
            raise ValueError("constraints must be a mapping")
        if self.m04.tenant_id != self.partition.tenant_id or self.m04.app_definition_id != self.partition.app_definition_id:
            raise ValueError("M04 context must match promotion partition")
        if self.m05.artifact_digest != self.m04.artifact_digest:
            raise ValueError("M04/M05 artifact digests must match")
        if self.m05.plugin_instance_id != self.partition.plugin_instance_id:
            raise ValueError("M05 instance must match promotion partition")
        if self.partition.scope_kind != self.m04.scope_kind or self.partition.scope_id != self.m04.scope_id:
            raise ValueError("scope must match promotion partition")
        if self.decision is AtomicProjectionDecision.ALLOW:
            if self.quarantine_evidence is not None:
                raise ValueError("allow cannot carry quarantine evidence")
            if self.promotion_evidence is None or not self.promotion_evidence.reviewed_final:
                raise ValueError("allow requires reviewed promotion evidence")
            if not self.canonical_decision.allowed:
                raise ValueError("allow requires canonical allow decision")
        elif self.decision is AtomicProjectionDecision.QUARANTINE:
            if self.quarantine_evidence is None:
                raise ValueError("quarantine requires quarantine evidence")
            if self.canonical_decision.allowed:
                raise ValueError("quarantine cannot carry canonical allow decision")
        elif self.promotion_evidence is not None and self.promotion_evidence.reviewed_final:
            raise ValueError("deny cannot carry final promotion evidence")


@dataclass(frozen=True, slots=True)
class AtomicGrantProjectionResult:
    decision: AtomicProjectionDecision
    reason: AtomicProjectionReason
    command: AtomicGrantProjectionCommand
    committed: bool


def _required(instance: object, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(instance, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")


def _sha256(value: str, name: str) -> None:
    if not value.startswith("sha256:") or len(value) != 71 or any(char not in "0123456789abcdef" for char in value[7:]):
        raise ValueError(f"{name} must be a sha256 digest")


__all__ = [
    "AtomicGrantProjectionCommand", "AtomicGrantProjectionResult", "AtomicProjectionDecision",
    "AtomicProjectionReason", "M04GrantProjectionContext", "M05GrantProjectionContext",
]
