"""Per-partition legacy owner marker and exclusive automation cutover state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .durable import (
    DurableAdapterEvidence, DurableAdapterVerifierPort, OutboxBlockedError,
    ResolvedScopeActorEvidence, ScopeActorVerifierPort, UpstreamRunLinkEvidence,
    UpstreamRunVerifierPort, authorize_scope_and_adapter,
    canonical_digest,
)
from .outbox import PartitionKey, WriterLease


class LegacyQueueKind(StrEnum):
    WEBHOOK_DELIVERIES = "webhook_deliveries"
    PROJECT_TRIGGERS = "project_triggers"


class CutoverState(StrEnum):
    LEGACY_ACTIVE = "legacy_active"
    SHADOW = "shadow"
    TARGET_READ = "target_read_legacy_write"
    FINAL_DELTA = "final_delta"
    TARGET_ACTIVE = "target_active"
    ROLLED_BACK = "rolled_back"
    TARGET_FROZEN = "target_frozen"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class LegacyOwnerMarker:
    partition: PartitionKey
    queue_kind: LegacyQueueKind
    source_partition_id: str
    source_schema_version: str
    source_snapshot_digest: str
    lease_inventory_digest: str
    checkpoint_id: str
    owner_epoch: int
    active_leases: int
    claims_stopped: bool

    def __post_init__(self) -> None:
        for name in ("source_partition_id", "source_schema_version", "checkpoint_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        for name in ("source_snapshot_digest", "lease_inventory_digest"):
            _digest_check(getattr(self, name), name)
        if self.owner_epoch < 0 or self.active_leases < 0:
            raise ValueError("legacy epoch and active lease count cannot be negative")

    @property
    def digest(self) -> str:
        return canonical_digest({
            "partition": (self.partition.tenant_id, self.partition.project_id, self.partition.automation_id),
            "queue_kind": self.queue_kind.value, "source_partition_id": self.source_partition_id,
            "source_schema_version": self.source_schema_version,
            "source_snapshot_digest": self.source_snapshot_digest,
            "lease_inventory_digest": self.lease_inventory_digest,
            "checkpoint_id": self.checkpoint_id,
            "owner_epoch": self.owner_epoch,
            "active_leases": self.active_leases,
            "claims_stopped": self.claims_stopped,
        })


@dataclass(frozen=True, slots=True)
class PartitionOwnership:
    partition: PartitionKey
    state: CutoverState
    legacy_marker: LegacyOwnerMarker
    read_owner: str = "legacy"
    write_owner: str = "legacy"
    legacy_claims_enabled: bool = True
    target_claims_enabled: bool = False
    projection_read_only: bool = False
    epoch: int = 0
    fencing_token: str | None = None
    revision: int = 0
    evidence_id: str | None = None
    shadow_reads_enabled: bool = False

    def __post_init__(self) -> None:
        if self.legacy_marker.partition != self.partition:
            raise ValueError("legacy marker must bind the same partition")
        if self.legacy_claims_enabled and self.target_claims_enabled:
            raise ValueError("legacy and target claims cannot both be enabled")
        if min(self.epoch, self.revision) < 0:
            raise ValueError("epoch and revision cannot be negative")
        if self.state is CutoverState.TARGET_ACTIVE:
            if self.read_owner != "target" or self.write_owner != "target" or not self.target_claims_enabled or self.legacy_claims_enabled:
                raise ValueError("target-active state requires one exclusive target owner")
        if self.state is CutoverState.SHADOW:
            if (self.read_owner != "legacy" or self.write_owner != "legacy"
                    or not self.legacy_claims_enabled or self.target_claims_enabled
                    or not self.shadow_reads_enabled):
                raise ValueError("shadow state keeps legacy authoritative and permits diagnostics only")
        elif self.shadow_reads_enabled:
            raise ValueError("shadow read permission is valid only in shadow state")
        if self.state is CutoverState.FINAL_DELTA:
            if self.read_owner != "target" or self.write_owner != "none" or self.legacy_claims_enabled or self.target_claims_enabled:
                raise ValueError("final-delta state must fence both workers while retaining target reads")
        if self.state is CutoverState.TARGET_READ:
            if self.read_owner != "target" or self.write_owner != "legacy" or not self.legacy_claims_enabled or self.target_claims_enabled:
                raise ValueError("target-read state requires target reads and legacy as sole writer")
        if self.state is CutoverState.TARGET_READ:
            if self.read_owner != "target" or self.write_owner != "legacy" or not self.legacy_claims_enabled or self.target_claims_enabled:
                raise ValueError("target-read state must retain legacy as sole delivery worker")
        if self.state in (CutoverState.TARGET_FROZEN, CutoverState.BLOCKED):
            if self.target_claims_enabled or self.write_owner == "target":
                raise ValueError("frozen or blocked ownership cannot accept target writes")
        if (self.state is CutoverState.BLOCKED
                and (self.legacy_claims_enabled or self.target_claims_enabled or self.write_owner != "none")):
            raise ValueError("blocked ownership must fence both delivery workers")
        if self.state is CutoverState.TARGET_FROZEN and (self.legacy_claims_enabled or self.write_owner != "none"):
            raise ValueError("post-target freeze cannot enable the legacy worker")
        if self.state in (CutoverState.LEGACY_ACTIVE, CutoverState.ROLLED_BACK):
            if self.read_owner != "legacy" or self.write_owner != "legacy" or not self.legacy_claims_enabled:
                raise ValueError("legacy owner state must retain legacy read/write authority")


@dataclass(frozen=True, slots=True)
class CutoverEvidence:
    transition_id: str
    evidence_id: str
    partition: PartitionKey
    target_state: CutoverState
    source_snapshot_digest: str
    source_target_digest: str
    outbox_digest: str
    delivery_digest: str
    parity_digest: str
    legacy_marker_digest: str
    final_delta_checkpoint: str
    scope_evidence_id: str
    actor_evidence_id: str
    actor_id: str
    actor_time: datetime
    upstream_evidence_id: str
    upstream_evidence_digest: str
    upstream_task_run_id: str
    upstream_source_run_id: str
    upstream_ordered_event_ids: tuple[str, ...]
    upstream_approval_ids: tuple[str, ...]
    adapter_evidence_id: str
    lease_fencing_epoch: int
    worker_generation: int
    observation_window: str
    rollback_window: str
    compatibility_projection_marker: str
    decision_owner: str
    signature_ref: str
    correlation_id: str
    legacy_claims_stopped: bool
    active_legacy_leases: int
    target_claims_stopped: bool
    active_target_leases: int
    duplicate_intent_count: int
    one_current_owner: bool
    accepted: bool

    def __post_init__(self) -> None:
        for name in (
            "transition_id", "evidence_id", "final_delta_checkpoint", "scope_evidence_id",
            "actor_evidence_id", "actor_id", "upstream_evidence_id", "upstream_task_run_id",
            "upstream_source_run_id", "adapter_evidence_id",
            "correlation_id", "observation_window",
            "rollback_window", "compatibility_projection_marker", "decision_owner", "signature_ref",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        for name in (
            "source_snapshot_digest", "source_target_digest", "outbox_digest", "delivery_digest",
            "parity_digest", "legacy_marker_digest", "upstream_evidence_digest",
        ):
            _digest_check(getattr(self, name), name)
        if self.actor_time.tzinfo is None or self.actor_time.utcoffset() is None:
            raise ValueError("actor_time must be timezone-aware")
        if not self.upstream_ordered_event_ids or any(not item.strip() for item in self.upstream_ordered_event_ids):
            raise ValueError("upstream ordered event identities are required")
        if len(set(self.upstream_ordered_event_ids)) != len(self.upstream_ordered_event_ids):
            raise ValueError("upstream ordered event identities must be unique")
        if any(not item.strip() for item in self.upstream_approval_ids):
            raise ValueError("upstream approval identities cannot be empty")
        if self.lease_fencing_epoch < 1 or self.worker_generation < 1:
            raise ValueError("fencing epoch and worker generation must be positive")
        if min(self.active_legacy_leases, self.active_target_leases, self.duplicate_intent_count) < 0:
            raise ValueError("lease and duplicate counts cannot be negative")


class CutoverEvidenceVerifierPort(Protocol):
    async def verify(self, *, evidence: CutoverEvidence) -> bool:
        """Verify immutable, signed and non-revoked gate evidence server-side."""
        ...


class PartitionOwnershipRepositoryPort(Protocol):
    async def load_ownership(self, partition: PartitionKey) -> PartitionOwnership | None: ...

    async def compare_and_set_ownership(
        self, *, expected_revision: int, ownership: PartitionOwnership,
        evidence: CutoverEvidence, actor_evidence: ResolvedScopeActorEvidence,
        adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        """CAS owner and append the immutable transition/gate evidence in one durable transaction."""
        ...


class PartitionCutoverService:
    """Bounded partition transitions; target ownership never overlaps legacy claims."""

    _ALLOWED = {
        CutoverState.LEGACY_ACTIVE: {CutoverState.SHADOW, CutoverState.BLOCKED},
        CutoverState.SHADOW: {CutoverState.TARGET_READ, CutoverState.ROLLED_BACK, CutoverState.BLOCKED},
        CutoverState.TARGET_READ: {CutoverState.FINAL_DELTA, CutoverState.BLOCKED},
        CutoverState.FINAL_DELTA: {CutoverState.TARGET_ACTIVE, CutoverState.BLOCKED},
        CutoverState.TARGET_ACTIVE: {CutoverState.TARGET_FROZEN, CutoverState.BLOCKED},
        CutoverState.ROLLED_BACK: {CutoverState.SHADOW, CutoverState.BLOCKED},
        CutoverState.TARGET_FROZEN: {CutoverState.BLOCKED},
        CutoverState.BLOCKED: set(),
    }

    def __init__(
        self, *, repository: PartitionOwnershipRepositoryPort,
        scope_verifier: ScopeActorVerifierPort | None,
        adapter_verifier: DurableAdapterVerifierPort | None,
        upstream_verifier: UpstreamRunVerifierPort | None,
        gate_verifier: CutoverEvidenceVerifierPort | None,
    ) -> None:
        self._repository = repository
        self._scope_verifier = scope_verifier
        self._adapter_verifier = adapter_verifier
        self._upstream_verifier = upstream_verifier
        self._gate_verifier = gate_verifier

    async def transition(
        self, *, partition: PartitionKey, target: CutoverState, lease: WriterLease,
        actor_evidence: ResolvedScopeActorEvidence, adapter_evidence: DurableAdapterEvidence,
        upstream_evidence: UpstreamRunLinkEvidence,
        evidence: CutoverEvidence, now: datetime,
    ) -> PartitionOwnership:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        await authorize_scope_and_adapter(
            partition=partition, actor_evidence=actor_evidence, adapter_evidence=adapter_evidence,
            scope_verifier=self._scope_verifier, adapter_verifier=self._adapter_verifier,
            actor_id=evidence.actor_id,
            upstream_evidence=upstream_evidence, upstream_verifier=self._upstream_verifier,
        )
        current = await self._repository.load_ownership(partition)
        if current is None or current.partition != partition:
            raise OutboxBlockedError("durable legacy owner marker is required before cutover")
        if evidence.partition != partition or evidence.target_state is not target:
            raise OutboxBlockedError("cutover evidence does not bind the requested partition transition")
        if (evidence.scope_evidence_id != actor_evidence.evidence_id
                or evidence.actor_evidence_id != actor_evidence.evidence_id
                or evidence.adapter_evidence_id != adapter_evidence.evidence_id):
            raise OutboxBlockedError("cutover evidence does not bind verified scope, actor and adapter evidence")
        if (evidence.upstream_evidence_id != upstream_evidence.evidence_id
                or evidence.upstream_evidence_digest != upstream_evidence.evidence_digest
                or evidence.actor_time != actor_evidence.resolved_at
                or evidence.upstream_task_run_id != upstream_evidence.task_run_id
                or evidence.upstream_source_run_id != upstream_evidence.source_run_id
                or evidence.upstream_ordered_event_ids != upstream_evidence.ordered_event_ids
                or evidence.upstream_approval_ids != upstream_evidence.approval_ids):
            raise OutboxBlockedError("cutover evidence does not bind exact upstream run/event/approval lineage")
        if evidence.legacy_marker_digest != current.legacy_marker.digest:
            raise OutboxBlockedError("legacy source/lease marker changed; reconcile a new checkpoint")
        if evidence.source_snapshot_digest != current.legacy_marker.source_snapshot_digest:
            raise OutboxBlockedError("cutover source snapshot does not match the frozen legacy owner marker")
        if target not in self._ALLOWED[current.state]:
            raise OutboxBlockedError(f"invalid ownership transition {current.state.value}->{target.value}")
        if lease.partition != partition or not lease.valid_at(now):
            raise OutboxBlockedError("a live same-partition fencing lease is required")
        if lease.epoch <= max(current.epoch, current.legacy_marker.owner_epoch) or lease.epoch != evidence.lease_fencing_epoch:
            raise OutboxBlockedError("fencing epoch must advance beyond legacy/current ownership and match evidence")
        if self._gate_verifier is None or not await self._gate_verifier.verify(evidence=evidence):
            raise OutboxBlockedError("server-verified immutable cutover evidence is required")
        if target is CutoverState.TARGET_READ:
            if not evidence.accepted or evidence.duplicate_intent_count != 0 or not evidence.observation_window:
                raise OutboxBlockedError("target-read gate requires accepted zero-duplicate parity evidence")
        if target is CutoverState.FINAL_DELTA:
            if (not evidence.accepted or evidence.duplicate_intent_count != 0
                    or not evidence.legacy_claims_stopped or evidence.active_legacy_leases != 0
                    or not evidence.target_claims_stopped or evidence.active_target_leases != 0):
                raise OutboxBlockedError("final-delta gate requires accepted parity and both worker pools drained")
        if target is CutoverState.BLOCKED:
            if (not evidence.legacy_claims_stopped or evidence.active_legacy_leases != 0
                    or not evidence.target_claims_stopped or evidence.active_target_leases != 0
                    or evidence.duplicate_intent_count != 0):
                raise OutboxBlockedError("blocked ownership must fence both workers after lease drain and reconciliation")
        if target is CutoverState.TARGET_ACTIVE:
            if (current.state is not CutoverState.FINAL_DELTA or not evidence.accepted
                    or evidence.duplicate_intent_count != 0 or not evidence.one_current_owner
                    or not evidence.observation_window or not evidence.rollback_window
                    or not evidence.legacy_claims_stopped or evidence.active_legacy_leases != 0
                    or not evidence.target_claims_stopped or evidence.active_target_leases != 0):
                raise OutboxBlockedError("target promotion requires complete accepted partition gate evidence")
        if target is CutoverState.TARGET_FROZEN:
            if (not evidence.target_claims_stopped or evidence.active_target_leases != 0
                    or not evidence.legacy_claims_stopped or evidence.active_legacy_leases != 0):
                raise OutboxBlockedError("post-target freeze requires both worker pools stopped and drained")
        next_owner = self._build(current, target, lease, evidence)
        if not await self._repository.compare_and_set_ownership(
            expected_revision=current.revision, ownership=next_owner, evidence=evidence,
            actor_evidence=actor_evidence, adapter_evidence=adapter_evidence,
        ):
            raise OutboxBlockedError("ownership CAS conflict; reconcile from immutable evidence")
        return next_owner

    @staticmethod
    def _build(
        current: PartitionOwnership, target: CutoverState, lease: WriterLease, evidence: CutoverEvidence,
    ) -> PartitionOwnership:
        marker = current.legacy_marker
        if target is CutoverState.TARGET_READ:
            return PartitionOwnership(
                current.partition, target, marker, "target", "legacy", True, False, True,
                lease.epoch, lease.fencing_token, current.revision + 1, evidence.evidence_id,
            )
        if target is CutoverState.FINAL_DELTA:
            return PartitionOwnership(
                current.partition, target, marker, "target", "none", False, False, True,
                lease.epoch, lease.fencing_token, current.revision + 1, evidence.evidence_id,
            )
        if target is CutoverState.TARGET_ACTIVE:
            return PartitionOwnership(
                current.partition, target, marker, "target", "target", False, True, True,
                lease.epoch, lease.fencing_token, current.revision + 1, evidence.evidence_id,
            )
        if target is CutoverState.TARGET_FROZEN:
            return PartitionOwnership(
                current.partition, target, marker, "target", "none", False, False, True,
                lease.epoch, lease.fencing_token, current.revision + 1, evidence.evidence_id,
            )
        if target is CutoverState.ROLLED_BACK:
            if current.state is not CutoverState.SHADOW:
                raise OutboxBlockedError("legacy delivery rollback is forbidden after target reads or ownership")
            return PartitionOwnership(
                current.partition, target, marker, "legacy", "legacy", True, False, False,
                lease.epoch, lease.fencing_token, current.revision + 1, evidence.evidence_id,
            )
        if target is CutoverState.SHADOW:
            return PartitionOwnership(
                current.partition, target, marker, "legacy", "legacy", True, False, False,
                lease.epoch, lease.fencing_token, current.revision + 1, evidence.evidence_id,
                shadow_reads_enabled=True,
            )
        if target is CutoverState.BLOCKED:
            return PartitionOwnership(
                current.partition, target, marker, current.read_owner, "none", False, False,
                current.projection_read_only, lease.epoch, lease.fencing_token,
                current.revision + 1, evidence.evidence_id,
            )
        raise OutboxBlockedError("unsupported cutover target")


def _digest_check(value: str, name: str) -> None:
    if len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError(f"{name} must be a sha256 digest")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a sha256 digest") from exc


__all__ = [
    "CutoverEvidence", "CutoverEvidenceVerifierPort", "CutoverState", "LegacyOwnerMarker",
    "LegacyQueueKind", "PartitionCutoverService", "PartitionOwnership",
    "PartitionOwnershipRepositoryPort",
]
