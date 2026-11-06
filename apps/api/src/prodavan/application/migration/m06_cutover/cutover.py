"""Immutable, fail-closed per-partition M06 cutover state machine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Mapping, Protocol


class CutoverState(StrEnum):
    LEGACY_ACTIVE = "legacy_active"
    SHADOW = "shadow"
    FINAL_DELTA = "final_delta"
    BLOCKED = "blocked"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    REPAIR_REQUIRED = "repair_required"


class CutoverAction(StrEnum):
    LEGACY_OWNER_BOUND = "legacy_owner_bound"
    SHADOW = "shadow"
    FINAL_DELTA = "final_delta"
    BLOCKED = "blocked"
    PROMOTED = "promoted"
    REPLAY = "replay"
    ROLLED_BACK = "rolled_back"
    REPAIR_REQUIRED = "repair_required"
    REPAIRED = "repaired"


class GateStage(StrEnum):
    M04 = "M04"
    M04E = "M04E"
    M05 = "M05"


@dataclass(frozen=True, slots=True)
class PartitionKey:
    tenant_id: str
    app_definition_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.app_definition_id.strip():
            raise ValueError("partition key fields are required")


@dataclass(frozen=True, slots=True)
class LegacyOwnerEvidence:
    """Verified initial read/decision/write owner marker for exactly one partition."""

    partition: PartitionKey
    evidence_id: str
    evidence_digest: str
    source_snapshot_digest: str
    writer_epoch: int
    exclusive_read_owner: bool
    exclusive_decision_owner: bool
    exclusive_write_owner: bool
    legacy_claims_enabled: bool

    @property
    def ready(self) -> bool:
        return bool(
            self.evidence_id.strip() and _is_sha256(self.evidence_digest)
            and _is_sha256(self.source_snapshot_digest) and self.writer_epoch >= 0
            and self.exclusive_read_owner and self.exclusive_decision_owner
            and self.exclusive_write_owner and self.legacy_claims_enabled
        )


@dataclass(frozen=True, slots=True)
class GateLineageEvidence:
    """Immutable upstream gate proof linked to the immediately preceding gate."""

    stage: GateStage
    partition: PartitionKey
    evidence_id: str
    evidence_digest: str
    source_snapshot_digest: str
    canonical_digest: str
    gate_passed: bool = False
    parent_evidence_id: str | None = None
    parent_evidence_digest: str | None = None

    @property
    def ready(self) -> bool:
        if not (self.gate_passed and self.evidence_id.strip()
                and _is_sha256(self.evidence_digest)
                and _is_sha256(self.source_snapshot_digest)
                and _is_sha256(self.canonical_digest)):
            return False
        if self.stage is GateStage.M04:
            return self.parent_evidence_id is None and self.parent_evidence_digest is None
        return bool(self.parent_evidence_id and self.parent_evidence_id.strip()
                    and _is_sha256(self.parent_evidence_digest or ""))


@dataclass(frozen=True, slots=True)
class LegacyWriterDrainEvidence:
    """Fence receipt proving legacy claims stopped and in-flight writers drained."""

    partition: PartitionKey
    evidence_id: str
    evidence_digest: str
    source_snapshot_digest: str
    writer_epoch: int
    new_claims_stopped: bool
    active_writers_remaining: int
    unsettled_claims_remaining: int
    frozen_change_sequence: int

    @property
    def ready(self) -> bool:
        return bool(
            self.evidence_id.strip() and _is_sha256(self.evidence_digest)
            and _is_sha256(self.source_snapshot_digest) and self.writer_epoch > 0
            and self.new_claims_stopped and self.active_writers_remaining == 0
            and self.unsettled_claims_remaining == 0 and self.frozen_change_sequence >= 0
        )


@dataclass(frozen=True, slots=True)
class FinalDeltaEvidence:
    """Final source checkpoint captured only after the legacy writer fence is closed."""

    partition: PartitionKey
    evidence_id: str
    evidence_digest: str
    source_snapshot_digest: str
    checkpoint: str
    checkpoint_digest: str
    writer_epoch: int
    source_change_sequence: int
    captured_after_drain: bool
    drain_evidence_id: str
    drain_evidence_digest: str

    @property
    def ready(self) -> bool:
        return bool(
            self.evidence_id.strip() and self.checkpoint.strip()
            and _is_sha256(self.evidence_digest) and _is_sha256(self.source_snapshot_digest)
            and _is_sha256(self.checkpoint_digest) and self.writer_epoch > 0
            and self.source_change_sequence >= 0 and self.captured_after_drain
            and self.drain_evidence_id.strip() and _is_sha256(self.drain_evidence_digest)
        )


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """Partition-bound M04/M04E/M05/M06 proof required for a positive transition."""

    partition: PartitionKey
    report_digest: str | None = None
    source_snapshot_digest: str | None = None
    final_delta_checkpoint: str | None = None
    writer_epoch: int | None = None
    observation_count: int = 0
    unexplained_mismatch_count: int = 0
    unresolved_positive_bindings: int = 0
    unknown_mapping_count: int = 0
    stale_generation_acceptances: int = 0
    audit_complete: bool = False
    parity_ready: bool = False
    handoff_verified: bool = False
    evidence_id: str | None = None
    evidence_digest: str | None = None
    canonical_digest: str | None = None
    verifier_ref: str | None = None
    m04: GateLineageEvidence | None = None
    m04e: GateLineageEvidence | None = None
    m05: GateLineageEvidence | None = None
    parent_evidence_id: str | None = None
    parent_evidence_digest: str | None = None
    legacy_owner_evidence_id: str | None = None
    legacy_owner_evidence_digest: str | None = None
    legacy_writer_drain: LegacyWriterDrainEvidence | None = None
    final_delta: FinalDeltaEvidence | None = None
    canonical_reconciliation_digest: str | None = None

    @property
    def ready(self) -> bool:
        if min(self.observation_count, self.unexplained_mismatch_count,
               self.unresolved_positive_bindings, self.unknown_mapping_count,
               self.stale_generation_acceptances) < 0:
            return False
        lineage = (self.m04, self.m04e, self.m05)
        if not all(item is not None and item.ready and item.partition == self.partition for item in lineage):
            return False
        assert self.m04 is not None and self.m04e is not None and self.m05 is not None
        if not (self.m04.stage is GateStage.M04 and self.m04e.stage is GateStage.M04E
                and self.m05.stage is GateStage.M05):
            return False
        if not (self.m04e.parent_evidence_id == self.m04.evidence_id
                and self.m04e.parent_evidence_digest == self.m04.evidence_digest
                and self.m05.parent_evidence_id == self.m04e.evidence_id
                and self.m05.parent_evidence_digest == self.m04e.evidence_digest
                and self.parent_evidence_id == self.m05.evidence_id
                and self.parent_evidence_digest == self.m05.evidence_digest):
            return False
        ids = (self.m04.evidence_id, self.m04e.evidence_id, self.m05.evidence_id, self.evidence_id or "")
        if len(set(ids)) != len(ids) or any(not item.strip() for item in ids):
            return False
        if any(item.source_snapshot_digest != self.source_snapshot_digest for item in lineage):
            return False
        drain, delta = self.legacy_writer_drain, self.final_delta
        if (drain is None or delta is None or not drain.ready or not delta.ready
                or drain.partition != self.partition or delta.partition != self.partition):
            return False
        assert drain is not None and delta is not None
        ids = (self.m04.evidence_id, self.m04e.evidence_id, self.m05.evidence_id,
               self.evidence_id or "", self.legacy_owner_evidence_id or "",
               drain.evidence_id, delta.evidence_id)
        if len(set(ids)) != len(ids) or any(not item.strip() for item in ids):
            return False
        if not (drain.source_snapshot_digest == self.source_snapshot_digest
                and delta.source_snapshot_digest == self.source_snapshot_digest
                and delta.writer_epoch == self.writer_epoch == drain.writer_epoch
                and delta.drain_evidence_id == drain.evidence_id
                and delta.drain_evidence_digest == drain.evidence_digest
                and delta.source_change_sequence == drain.frozen_change_sequence
                and delta.checkpoint == self.final_delta_checkpoint):
            return False
        if not all((self.evidence_id and self.evidence_id.strip(),
                    _is_sha256(self.evidence_digest or ""),
                    _is_sha256(self.canonical_digest or ""),
                    _is_sha256(self.report_digest or ""),
                    _is_sha256(self.source_snapshot_digest or ""),
                    self.verifier_ref and self.verifier_ref.strip(),
                    self.legacy_owner_evidence_id and self.legacy_owner_evidence_id.strip(),
                    _is_sha256(self.legacy_owner_evidence_digest or ""))):
            return False
        return bool(
            self.writer_epoch is not None and self.writer_epoch > 0
            and self.final_delta_checkpoint and self.final_delta_checkpoint.strip()
            and self.observation_count > 0 and self.unexplained_mismatch_count == 0
            and self.unresolved_positive_bindings == 0 and self.unknown_mapping_count == 0
            and self.stale_generation_acceptances == 0 and self.audit_complete
            and self.parity_ready and self.handoff_verified
        )


ParityGate = GateEvidence


@dataclass(frozen=True, slots=True)
class RollbackEvidence:
    """Proof to restore legacy ownership before canonical read cutover only."""

    partition: PartitionKey
    evidence_id: str
    evidence_digest: str
    source_snapshot_digest: str
    legacy_owner_evidence_id: str
    legacy_owner_evidence_digest: str
    writer_epoch: int
    legacy_owner_restored: bool
    canonical_claims_stopped: bool
    active_canonical_writers_remaining: int
    unresolved_legacy_claims: int

    @property
    def ready(self) -> bool:
        return bool(
            self.evidence_id.strip() and _is_sha256(self.evidence_digest)
            and _is_sha256(self.source_snapshot_digest)
            and self.legacy_owner_evidence_id.strip()
            and _is_sha256(self.legacy_owner_evidence_digest)
            and self.writer_epoch > 0 and self.legacy_owner_restored
            and self.canonical_claims_stopped and self.active_canonical_writers_remaining == 0
            and self.unresolved_legacy_claims == 0
        )


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    stage: str
    evidence_id: str
    evidence_digest: str
    partition: PartitionKey


@dataclass(frozen=True, slots=True)
class CanonicalRepairEvidence:
    """Fresh canonical reconciliation proof linked to the immutable current lineage head."""

    partition: PartitionKey
    evidence_id: str
    evidence_digest: str
    source_snapshot_digest: str
    checkpoint_digest: str
    writer_epoch: int
    parent_evidence_id: str
    parent_evidence_digest: str
    gate_evidence: tuple[EvidenceReference, ...]
    unresolved_conflicts: int
    unknown_mappings: int
    unresolved_positive_bindings: int
    stale_generation_acceptances: int
    active_canonical_writers_remaining: int
    canonical_writes_stopped: bool
    audit_complete: bool

    @property
    def ready(self) -> bool:
        stages = [item.stage for item in self.gate_evidence]
        evidence_ids = [item.evidence_id for item in self.gate_evidence]
        required_stages = {"M04", "M04E", "M05", "M06"}
        stages_ready = (len(stages) == len(required_stages) and set(stages) == required_stages
                        and len(evidence_ids) == len(set(evidence_ids)) and all(evidence_ids))
        digests_ready = all(item.evidence_id.strip() and _is_sha256(item.evidence_digest)
                            and item.partition == self.partition
                            for item in self.gate_evidence)
        return bool(
            self.evidence_id.strip() and _is_sha256(self.evidence_digest)
            and _is_sha256(self.source_snapshot_digest) and _is_sha256(self.checkpoint_digest)
            and self.writer_epoch > 0 and self.parent_evidence_id.strip()
            and _is_sha256(self.parent_evidence_digest)
            and stages_ready and digests_ready
            and self.unresolved_conflicts == self.unknown_mappings == 0
            and self.unresolved_positive_bindings == self.stale_generation_acceptances == 0
            and self.active_canonical_writers_remaining == 0 and self.canonical_writes_stopped
            and self.audit_complete
        )


class GateVerifier(Protocol):
    async def verify(self, *, evidence: GateEvidence) -> bool: ...


class LegacyOwnerVerifier(Protocol):
    async def verify(self, *, evidence: LegacyOwnerEvidence) -> bool: ...


class RollbackVerifier(Protocol):
    async def verify(self, *, evidence: RollbackEvidence) -> bool: ...


class CanonicalRepairVerifier(Protocol):
    async def verify(self, *, evidence: CanonicalRepairEvidence) -> bool: ...


@dataclass(frozen=True, slots=True)
class WriterLease:
    partition: PartitionKey
    holder: str
    epoch: int
    expires_at: datetime
    fencing_token: str

    def __post_init__(self) -> None:
        _aware(self.expires_at)
        if not self.holder.strip() or self.epoch < 1 or not self.fencing_token.strip():
            raise ValueError("valid writer lease holder, epoch and fencing token required")


@dataclass(frozen=True, slots=True)
class PartitionCutover:
    partition: PartitionKey
    state: CutoverState = CutoverState.LEGACY_ACTIVE
    canonical_read_owner: str = "legacy"
    decision_owner: str = "legacy"
    write_owner: str = "legacy"
    writer_epoch: int = 0
    lease_holder: str | None = None
    fencing_token: str | None = None
    legacy_projection_read_only: bool = False
    canonical_positive_writes: bool = False
    canonical_shadow_reads: bool = False
    legacy_owner_evidence_id: str | None = None
    legacy_owner_evidence_digest: str | None = None
    source_snapshot_digest: str | None = None
    final_delta_evidence_id: str | None = None
    final_delta_evidence_digest: str | None = None
    final_delta_source_change_sequence: int | None = None
    frozen_gate_digest: str | None = None
    last_evidence_id: str | None = None
    lineage_head_digest: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        owners = (self.canonical_read_owner, self.decision_owner, self.write_owner)
        if any(owner not in {"legacy", "canonical", "none"} for owner in owners):
            raise ValueError("cutover owners must be legacy, canonical, or none")
        if self.writer_epoch < 0 or self.revision < 0:
            raise ValueError("writer epoch and revision must be non-negative")
        if (self.legacy_owner_evidence_id is None) != (self.legacy_owner_evidence_digest is None):
            raise ValueError("legacy owner marker ID and digest must be supplied together")
        if self.legacy_owner_evidence_digest is not None and not _is_sha256(self.legacy_owner_evidence_digest):
            raise ValueError("legacy owner marker digest must be sha256")
        if self.source_snapshot_digest is not None and not _is_sha256(self.source_snapshot_digest):
            raise ValueError("owner source snapshot digest must be sha256")
        if self.final_delta_evidence_digest is not None and not _is_sha256(self.final_delta_evidence_digest):
            raise ValueError("final delta evidence digest must be sha256")
        if self.final_delta_source_change_sequence is not None and self.final_delta_source_change_sequence < 0:
            raise ValueError("final delta source sequence must be non-negative")
        if self.frozen_gate_digest is not None and not _is_sha256(self.frozen_gate_digest):
            raise ValueError("frozen gate digest must be sha256")
        if self.canonical_read_owner == "canonical" and not self.legacy_projection_read_only:
            raise ValueError("legacy projection must be read-only after canonical read cutover")
        if self.canonical_read_owner == "canonical" and self.decision_owner != "canonical":
            raise ValueError("canonical read cutover cannot retain a legacy decision owner")
        if self.canonical_read_owner == "canonical" and self.write_owner == "legacy":
            raise ValueError("legacy writer cannot remain enabled after canonical read cutover")
        if self.state is CutoverState.SHADOW and not (
            self.canonical_read_owner == self.decision_owner == self.write_owner == "legacy"
            and self.canonical_shadow_reads
        ):
            raise ValueError("shadow state must retain exclusive legacy ownership")
        if self.state is CutoverState.ROLLED_BACK and not (
            self.canonical_read_owner == self.decision_owner == self.write_owner == "legacy"
            and not self.legacy_projection_read_only and not self.canonical_positive_writes
        ):
            raise ValueError("rollback must restore one exclusive legacy owner")
        if self.write_owner == "legacy" and not (
            self.canonical_read_owner == self.decision_owner == "legacy"
        ):
            raise ValueError("legacy writer requires exclusive legacy read/decision ownership")
        if self.write_owner == "canonical" and not (
            self.canonical_read_owner == self.decision_owner == "canonical"
            and self.legacy_projection_read_only
        ):
            raise ValueError("canonical writer requires exclusive canonical read/decision ownership")
        if self.canonical_positive_writes != (self.write_owner == "canonical"):
            raise ValueError("canonical positive-write flag must match the exclusive writer owner")
        if self.state is CutoverState.BLOCKED and self.canonical_positive_writes:
            raise ValueError("blocked cutover state cannot enable canonical positive writes")
        if self.state is CutoverState.FINAL_DELTA and not (
            self.canonical_read_owner == self.decision_owner == "legacy" and self.write_owner == "none"
            and self.legacy_projection_read_only and not self.canonical_positive_writes
            and self.final_delta_evidence_id is not None
            and self.final_delta_evidence_digest is not None
            and self.final_delta_source_change_sequence is not None
            and self.frozen_gate_digest is not None
        ):
            raise ValueError("final-delta state must have no writer and retain legacy reads")
        if self.state is CutoverState.REPAIR_REQUIRED and not (
            self.canonical_read_owner == self.decision_owner == "canonical"
            and self.write_owner == "none" and self.legacy_projection_read_only
            and not self.canonical_positive_writes
        ):
            raise ValueError("forward-repair state must keep canonical reads and fence all writes")
        if self.state is CutoverState.PROMOTED and not (
            self.write_owner == "canonical" and self.legacy_owner_evidence_id is not None
            and self.final_delta_evidence_id is not None and self.final_delta_evidence_digest is not None
            and self.frozen_gate_digest is not None and self.lineage_head_digest is not None
        ):
            raise ValueError("promoted state requires exclusive canonical write ownership")


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    """Append-only transition record; every correction links to the prior record."""

    evidence_id: str
    partition: PartitionKey
    action: CutoverAction
    actor: str
    occurred_at: datetime
    prior_state: CutoverState
    resulting_state: CutoverState
    prior_read_owner: str
    resulting_read_owner: str
    writer_epoch: int
    lease_fencing_token: str | None
    parity_report_digest: str | None
    canonical_snapshot_digest: str | None
    source_snapshot_digest: str | None
    final_delta_checkpoint: str | None
    final_delta_source_change_sequence: int | None
    reason: str
    record_digest: str
    command_digest: str
    prior_write_owner: str
    resulting_write_owner: str
    prior_decision_owner: str
    resulting_decision_owner: str
    prior_record_id: str | None
    prior_record_digest: str | None
    gate_evidence: tuple[EvidenceReference, ...]
    canonical_positive_writes: bool

    def __post_init__(self) -> None:
        _aware(self.occurred_at)
        if not _is_sha256(self.record_digest) or not _is_sha256(self.command_digest):
            raise ValueError("lineage record and command digests must be sha256")
        for value, label in ((self.parity_report_digest, "parity_report_digest"),
                             (self.canonical_snapshot_digest, "canonical_snapshot_digest"),
                             (self.source_snapshot_digest, "source_snapshot_digest")):
            if value is not None and not _is_sha256(value):
                raise ValueError(f"{label} must be sha256")
        if (self.final_delta_source_change_sequence is not None
                and self.final_delta_source_change_sequence < 0):
            raise ValueError("final delta source sequence must be non-negative")
        if (self.prior_record_id is None) != (self.prior_record_digest is None):
            raise ValueError("prior lineage record ID and digest must be supplied together")
        if self.prior_record_digest is not None and not _is_sha256(self.prior_record_digest):
            raise ValueError("prior lineage record digest must be sha256")
        if any(ref.partition != self.partition or not ref.evidence_id.strip()
               or not _is_sha256(ref.evidence_digest) for ref in self.gate_evidence):
            raise ValueError("lineage references must be complete and partition-bound")
        if _lineage_record_digest(self) != self.record_digest:
            raise ValueError("lineage record digest does not match immutable record contents")


def _lineage_payload_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _lineage_record_digest(record: PromotionEvidence) -> str:
    payload = {
        "evidence_id": record.evidence_id, "command_digest": record.command_digest,
        "partition": [record.partition.tenant_id, record.partition.app_definition_id],
        "action": record.action.value, "actor": record.actor,
        "occurred_at": record.occurred_at.isoformat(),
        "prior_state": record.prior_state.value, "resulting_state": record.resulting_state.value,
        "prior_read_owner": record.prior_read_owner, "resulting_read_owner": record.resulting_read_owner,
        "prior_decision_owner": record.prior_decision_owner,
        "resulting_decision_owner": record.resulting_decision_owner,
        "prior_write_owner": record.prior_write_owner, "resulting_write_owner": record.resulting_write_owner,
        "prior_record_id": record.prior_record_id, "prior_record_digest": record.prior_record_digest,
        "canonical_positive_writes": record.canonical_positive_writes,
        "writer_epoch": record.writer_epoch, "lease_fencing_token": record.lease_fencing_token,
        "gate_evidence": [
            (ref.stage, ref.evidence_id, ref.evidence_digest,
             ref.partition.tenant_id, ref.partition.app_definition_id)
            for ref in record.gate_evidence
        ],
        "parity_report_digest": record.parity_report_digest,
        "canonical_snapshot_digest": record.canonical_snapshot_digest,
        "source_snapshot_digest": record.source_snapshot_digest,
        "final_delta_checkpoint": record.final_delta_checkpoint,
        "final_delta_source_change_sequence": record.final_delta_source_change_sequence,
        "reason": record.reason,
    }
    return _lineage_payload_digest(payload)


@dataclass(frozen=True, slots=True)
class PromotionRecord(PromotionEvidence):
    """Immutable successful or blocked promotion lineage record."""


@dataclass(frozen=True, slots=True)
class RollbackRecord(PromotionEvidence):
    """Immutable pre-cutover rollback or forward-repair lineage record."""


CutoverLineageRecord = PromotionEvidence


class CutoverRepository(Protocol):
    async def load(self, partition: PartitionKey) -> PartitionCutover | None: ...

    async def transition(
        self,
        *,
        expected_revision: int,
        state: PartitionCutover,
        evidence: PromotionEvidence,
        lease: WriterLease,
        expected_source_change_sequence: int | None,
    ) -> bool:
        """Atomically CAS state/evidence and compare the live source tail/fence sequence."""
        ...

    async def evidence_by_id(self, evidence_id: str) -> PromotionEvidence | None: ...


class CutoverCommandService:
    """Exclusive per-partition ownership transitions with digest-linked evidence lineage."""

    def __init__(self, repository: CutoverRepository, *, gate_verifier: GateVerifier | None = None,
                 legacy_owner_verifier: LegacyOwnerVerifier | None = None,
                 rollback_verifier: RollbackVerifier | None = None,
                 repair_verifier: CanonicalRepairVerifier | None = None,
                 max_command_bytes: int = 16_384) -> None:
        if not 1 <= max_command_bytes <= 65_536:
            raise ValueError("max_command_bytes must be between 1 and 65536")
        self._repository = repository
        self._gate_verifier = gate_verifier
        self._legacy_owner_verifier = legacy_owner_verifier
        self._rollback_verifier = rollback_verifier
        self._repair_verifier = repair_verifier
        self._max_command_bytes = max_command_bytes

    async def bind_legacy_owner(self, *, partition: PartitionKey, actor: str, command_id: str,
                                lease: WriterLease, evidence: LegacyOwnerEvidence,
                                now: datetime | None = None) -> PartitionCutover:
        checked_at = _checked_now(now)
        inputs = {"legacy_owner": _legacy_owner_fingerprint(evidence)}
        replay = await self._replay(command_id, partition, "bind_legacy_owner", actor, lease, inputs,
                                    {CutoverAction.LEGACY_OWNER_BOUND})
        if replay is not None:
            return replay
        self._validate_command(actor, command_id)
        current = await self._load(partition)
        self._require_legacy_authority(current)
        if current.state is not CutoverState.LEGACY_ACTIVE:
            raise CutoverConflict("legacy owner evidence can only bind before shadow reads")
        reason = self._lease_reason(partition, lease, checked_at, current.writer_epoch)
        if reason:
            raise CutoverConflict(reason)
        if evidence.partition != partition or not evidence.ready:
            raise CutoverConflict("exclusive partition-bound legacy owner evidence is required")
        if evidence.writer_epoch != lease.epoch:
            raise CutoverConflict("legacy owner evidence writer epoch does not match the fencing lease")
        if self._legacy_owner_verifier is None:
            raise CutoverConflict("trusted legacy owner verifier missing")
        try:
            verified = await self._legacy_owner_verifier.verify(evidence=evidence)
        except Exception as exc:
            raise CutoverConflict("legacy owner evidence verification failed") from exc
        if not verified:
            raise CutoverConflict("legacy owner evidence not verified")
        resulting = replace(
            current, writer_epoch=lease.epoch, lease_holder=lease.holder,
            fencing_token=lease.fencing_token, legacy_owner_evidence_id=evidence.evidence_id,
            legacy_owner_evidence_digest=evidence.evidence_digest,
            source_snapshot_digest=evidence.source_snapshot_digest,
            revision=current.revision + 1,
        )
        command_digest = self._command_digest(command_id, partition, "bind_legacy_owner", actor, lease, inputs)
        record = self._record(command_id, command_digest, partition, CutoverAction.LEGACY_OWNER_BOUND,
                              actor, checked_at, current, resulting, lease,
                              reason="exclusive_legacy_owner_marker_bound",
                              extra_refs=(EvidenceReference("legacy_owner", evidence.evidence_id,
                                                            evidence.evidence_digest, partition),))
        return await self._commit_or_replay(current, resulting, record, lease)

    async def enter_shadow(self, *, partition: PartitionKey, actor: str, command_id: str,
                           lease: WriterLease, now: datetime | None = None) -> PartitionCutover:
        checked_at = _checked_now(now)
        inputs: dict[str, object] = {}
        replay = await self._replay(command_id, partition, "enter_shadow", actor, lease, inputs,
                                    {CutoverAction.SHADOW})
        if replay is not None:
            return replay
        self._validate_command(actor, command_id)
        current = await self._load(partition)
        self._require_legacy_authority(current)
        if current.state not in {CutoverState.LEGACY_ACTIVE, CutoverState.ROLLED_BACK, CutoverState.BLOCKED}:
            raise CutoverConflict("shadow mode requires a pre-cutover exclusive legacy owner")
        if current.legacy_owner_evidence_id is None or current.legacy_owner_evidence_digest is None:
            raise CutoverConflict("verified legacy owner marker must be bound before shadow reads")
        reason = self._lease_reason(partition, lease, checked_at, current.writer_epoch)
        if reason:
            raise CutoverConflict(reason)
        resulting = replace(current, state=CutoverState.SHADOW, writer_epoch=lease.epoch,
                            lease_holder=lease.holder, fencing_token=lease.fencing_token,
                            canonical_shadow_reads=True, revision=current.revision + 1)
        command_digest = self._command_digest(command_id, partition, "enter_shadow", actor, lease, inputs)
        record = self._record(command_id, command_digest, partition, CutoverAction.SHADOW,
                              actor, checked_at, current, resulting, lease,
                              reason="legacy_owner_retained_shadow_only")
        return await self._commit_or_replay(current, resulting, record, lease)

    async def freeze_final_delta(self, *, partition: PartitionKey, actor: str, command_id: str,
                                 lease: WriterLease, gate: GateEvidence | None = None,
                                 now: datetime | None = None) -> PartitionCutover:
        checked_at = _checked_now(now)
        inputs = {"gate": _gate_fingerprint(gate)}
        replay = await self._replay(command_id, partition, "freeze_final_delta", actor, lease, inputs,
                                    {CutoverAction.FINAL_DELTA, CutoverAction.BLOCKED})
        if replay is not None:
            return replay
        self._validate_command(actor, command_id)
        current = await self._load(partition)
        if current.state not in {CutoverState.SHADOW, CutoverState.BLOCKED,
                                 CutoverState.FINAL_DELTA, CutoverState.ROLLED_BACK}:
            raise CutoverConflict("shadow or recoverable pre-cutover state required before final delta")
        if (current.canonical_read_owner != "legacy" or current.decision_owner != "legacy"
                or current.canonical_positive_writes):
            raise CutoverConflict("final delta cannot change canonical ownership")
        reason = self._lease_reason(partition, lease, checked_at, current.writer_epoch)
        if reason:
            raise CutoverConflict(reason)
        reason = await self._gate_block_reason(partition, lease, gate, current)
        if reason is None:
            assert gate is not None and gate.final_delta is not None
            resulting = replace(
                current, state=CutoverState.FINAL_DELTA, write_owner="none",
                writer_epoch=lease.epoch, lease_holder=lease.holder,
                fencing_token=lease.fencing_token, legacy_projection_read_only=True,
                canonical_positive_writes=False, canonical_shadow_reads=False,
                final_delta_evidence_id=gate.final_delta.evidence_id,
                final_delta_evidence_digest=gate.final_delta.evidence_digest,
                final_delta_source_change_sequence=gate.final_delta.source_change_sequence,
                frozen_gate_digest=_gate_fingerprint(gate), revision=current.revision + 1,
            )
            action, outcome_reason = CutoverAction.FINAL_DELTA, "legacy_writer_drained_final_delta_frozen"
        else:
            resulting = self._blocked_state(current, lease)
            action, outcome_reason = CutoverAction.BLOCKED, reason
        command_digest = self._command_digest(command_id, partition, "freeze_final_delta", actor,
                                              lease, inputs)
        record = self._record(command_id, command_digest, partition, action, actor, checked_at,
                              current, resulting, lease, gate, outcome_reason)
        return await self._commit_or_replay(current, resulting, record, lease)

    async def promote(self, *, partition: PartitionKey, actor: str, command_id: str,
                      lease: WriterLease, gate: GateEvidence | None = None,
                      now: datetime | None = None) -> PartitionCutover:
        checked_at = _checked_now(now)
        inputs = {"gate": _gate_fingerprint(gate)}
        replay = await self._replay(command_id, partition, "promote", actor, lease, inputs,
                                    {CutoverAction.PROMOTED, CutoverAction.BLOCKED})
        if replay is not None:
            return replay
        self._validate_command(actor, command_id)
        current = await self._load(partition)
        if current.state is not CutoverState.FINAL_DELTA:
            raise CutoverConflict("promotion requires a persisted final-delta freeze")
        if (lease.partition != partition or lease.expires_at <= checked_at
                or lease.epoch != current.writer_epoch or lease.fencing_token != current.fencing_token):
            raise CutoverConflict("promotion must use the live fence that owns the final-delta freeze")
        reason = await self._gate_block_reason(partition, lease, gate, current)
        if reason is None and gate is not None:
            if (_gate_fingerprint(gate) != current.frozen_gate_digest or gate.final_delta is None
                    or gate.final_delta.evidence_id != current.final_delta_evidence_id
                    or gate.final_delta.evidence_digest != current.final_delta_evidence_digest):
                reason = "final_delta_or_gate_lineage_changed_after_freeze"
        if reason is None:
            assert gate is not None
            resulting = replace(
                current, state=CutoverState.PROMOTED, canonical_read_owner="canonical",
                decision_owner="canonical", write_owner="canonical", writer_epoch=lease.epoch,
                lease_holder=lease.holder, fencing_token=lease.fencing_token,
                legacy_projection_read_only=True, canonical_positive_writes=True,
                canonical_shadow_reads=False, revision=current.revision + 1,
            )
            action, outcome_reason = CutoverAction.PROMOTED, "exclusive_canonical_partition_promoted"
        else:
            resulting = self._blocked_state(current, lease)
            action, outcome_reason = CutoverAction.BLOCKED, reason
        command_digest = self._command_digest(command_id, partition, "promote", actor, lease, inputs)
        record = self._record(command_id, command_digest, partition, action, actor, checked_at,
                              current, resulting, lease, gate, outcome_reason)
        return await self._commit_or_replay(current, resulting, record, lease)

    async def rollback(self, *, partition: PartitionKey, actor: str, command_id: str,
                       lease: WriterLease, evidence: RollbackEvidence | None = None,
                       now: datetime | None = None) -> PartitionCutover:
        checked_at = _checked_now(now)
        inputs = {"rollback_evidence": _rollback_fingerprint(evidence)}
        replay = await self._replay(command_id, partition, "rollback_to_legacy", actor, lease, inputs,
                                    {CutoverAction.ROLLED_BACK})
        if replay is not None:
            return replay
        self._validate_command(actor, command_id)
        current = await self._load(partition)
        if current.canonical_read_owner != "legacy" or current.decision_owner != "legacy":
            raise CutoverConflict("rollback after canonical read cutover is forbidden; repair forward")
        if current.write_owner not in {"legacy", "none"} or current.canonical_positive_writes:
            raise CutoverConflict("dual-owner rollback is forbidden")
        if current.state not in {CutoverState.SHADOW, CutoverState.FINAL_DELTA,
                                 CutoverState.BLOCKED, CutoverState.ROLLED_BACK}:
            raise CutoverConflict("rollback is available only before canonical read cutover")
        reason = self._lease_reason(partition, lease, checked_at, current.writer_epoch)
        if reason:
            raise CutoverConflict(reason)
        if (evidence is None or self._rollback_verifier is None or not evidence.ready
                or evidence.partition != partition or evidence.writer_epoch != lease.epoch
                or evidence.source_snapshot_digest != current.source_snapshot_digest
                or evidence.legacy_owner_evidence_id != current.legacy_owner_evidence_id
                or evidence.legacy_owner_evidence_digest != current.legacy_owner_evidence_digest):
            raise CutoverConflict("fresh verified exclusive legacy rollback evidence is required")
        try:
            if not await self._rollback_verifier.verify(evidence=evidence):
                raise CutoverConflict("legacy rollback evidence not verified")
        except CutoverConflict:
            raise
        except Exception as exc:
            raise CutoverConflict("legacy rollback evidence verification failed") from exc
        resulting = replace(
            current, state=CutoverState.ROLLED_BACK, canonical_read_owner="legacy",
            decision_owner="legacy", write_owner="legacy", writer_epoch=lease.epoch,
            lease_holder=lease.holder, fencing_token=lease.fencing_token,
            legacy_projection_read_only=False, canonical_positive_writes=False,
            canonical_shadow_reads=False, revision=current.revision + 1,
        )
        command_digest = self._command_digest(command_id, partition, "rollback_to_legacy", actor,
                                              lease, inputs)
        record = self._record(
            command_id, command_digest, partition, CutoverAction.ROLLED_BACK, actor, checked_at,
            current, resulting, lease, reason="verified_pre_cutover_rollback_to_exclusive_legacy_owner",
            extra_refs=(EvidenceReference("rollback", evidence.evidence_id,
                                          evidence.evidence_digest, partition),),
        )
        return await self._commit_or_replay(current, resulting, record, lease)

    async def repair_forward(self, *, partition: PartitionKey, actor: str, command_id: str,
                             lease: WriterLease, evidence: CanonicalRepairEvidence | None = None,
                             now: datetime | None = None) -> PartitionCutover:
        """Keep canonical reads authoritative and fence writes until repair proof verifies."""
        checked_at = _checked_now(now)
        inputs = {"repair_evidence": _repair_fingerprint(evidence)}
        replay = await self._replay(command_id, partition, "repair_forward", actor, lease, inputs,
                                    {CutoverAction.REPAIRED, CutoverAction.REPAIR_REQUIRED})
        if replay is not None:
            return replay
        self._validate_command(actor, command_id)
        current = await self._load(partition)
        if current.state not in {CutoverState.PROMOTED, CutoverState.REPAIR_REQUIRED}:
            raise CutoverConflict("forward repair is available only after canonical read cutover")
        if current.canonical_read_owner != "canonical" or current.decision_owner != "canonical":
            raise CutoverConflict("canonical read/decision ownership must remain authoritative during repair")
        reason = self._lease_reason(partition, lease, checked_at, current.writer_epoch)
        if reason:
            raise CutoverConflict(reason)
        reason = "canonical_repair_evidence_missing"
        if evidence is not None and evidence.ready:
            if evidence.partition != partition or evidence.writer_epoch != lease.epoch:
                reason = "canonical_repair_partition_or_epoch_mismatch"
            elif evidence.source_snapshot_digest != current.source_snapshot_digest:
                reason = "canonical_repair_source_snapshot_mismatch"
            elif (evidence.parent_evidence_id != current.last_evidence_id
                  or evidence.parent_evidence_digest != current.lineage_head_digest):
                reason = "canonical_repair_parent_lineage_mismatch"
            elif self._repair_verifier is None:
                reason = "trusted_canonical_repair_verifier_missing"
            else:
                try:
                    verified = await self._repair_verifier.verify(evidence=evidence)
                except Exception:
                    verified = False
                reason = None if verified else "canonical_repair_evidence_not_verified"
        elif evidence is not None:
            reason = "canonical_repair_evidence_incomplete"
        if reason is None:
            assert evidence is not None
            resulting = replace(
                current, state=CutoverState.PROMOTED, write_owner="canonical",
                writer_epoch=lease.epoch, lease_holder=lease.holder,
                fencing_token=lease.fencing_token, legacy_projection_read_only=True,
                canonical_positive_writes=True, canonical_shadow_reads=False,
                revision=current.revision + 1,
            )
            action, outcome_reason = CutoverAction.REPAIRED, "canonical_forward_repair_verified"
            refs = evidence.gate_evidence + (
                EvidenceReference("canonical_repair", evidence.evidence_id, evidence.evidence_digest,
                                  partition),
            )
        else:
            resulting = replace(
                current, state=CutoverState.REPAIR_REQUIRED, write_owner="none",
                writer_epoch=lease.epoch, lease_holder=lease.holder,
                fencing_token=lease.fencing_token, legacy_projection_read_only=True,
                canonical_positive_writes=False, canonical_shadow_reads=False,
                revision=current.revision + 1,
            )
            action, outcome_reason = CutoverAction.REPAIR_REQUIRED, reason
            refs = ()
            if evidence is not None:
                refs = evidence.gate_evidence + (
                    EvidenceReference("canonical_repair_attempt", evidence.evidence_id,
                                      evidence.evidence_digest, partition),
                )
        command_digest = self._command_digest(command_id, partition, "repair_forward", actor,
                                              lease, inputs)
        record = self._record(command_id, command_digest, partition, action, actor, checked_at,
                              current, resulting, lease, reason=outcome_reason, extra_refs=refs)
        return await self._commit_or_replay(current, resulting, record, lease)

    async def _load(self, partition: PartitionKey) -> PartitionCutover:
        current = await self._repository.load(partition)
        if current is None:
            return PartitionCutover(partition)
        if current.partition != partition:
            raise CutoverConflict("repository returned another partition")
        return current

    @staticmethod
    def _require_legacy_authority(current: PartitionCutover) -> None:
        if (current.canonical_read_owner != "legacy" or current.decision_owner != "legacy"
                or current.write_owner != "legacy" or current.canonical_positive_writes):
            raise CutoverConflict("shadow mode requires exclusive legacy ownership")

    async def _gate_block_reason(self, partition: PartitionKey, lease: WriterLease,
                                 gate: GateEvidence | None, current: PartitionCutover) -> str | None:
        if gate is None:
            return "m04_m04e_m05_m06_gate_evidence_missing"
        if gate.partition != partition:
            return "gate_partition_mismatch"
        if not gate.ready:
            return "full_upstream_or_m06_final_delta_evidence_incomplete"
        if (current.legacy_owner_evidence_id is None or current.legacy_owner_evidence_digest is None
                or current.source_snapshot_digest is None):
            return "legacy_owner_marker_or_source_snapshot_missing"
        if (gate.legacy_owner_evidence_id != current.legacy_owner_evidence_id
                or gate.legacy_owner_evidence_digest != current.legacy_owner_evidence_digest
                or gate.source_snapshot_digest != current.source_snapshot_digest):
            return "gate_does_not_bind_current_legacy_owner_snapshot"
        if gate.writer_epoch != lease.epoch:
            return "gate_writer_epoch_does_not_match_fencing_lease"
        if self._gate_verifier is None:
            return "trusted_full_lineage_gate_verifier_missing"
        try:
            if not await self._gate_verifier.verify(evidence=gate):
                return "upstream_m04_m04e_m05_m06_evidence_not_verified"
        except Exception:
            return "upstream_gate_verification_failed"
        return None

    def _command_digest(self, command_id: str, partition: PartitionKey, operation: str,
                        actor: str, lease: WriterLease, inputs: Mapping[str, object]) -> str:
        payload = {
            "command_id": command_id, "partition": [partition.tenant_id, partition.app_definition_id],
            "operation": operation, "actor": actor, "lease_holder": lease.holder,
            "lease_epoch": lease.epoch, "lease_expires_at": lease.expires_at.isoformat(),
            "fencing_token": lease.fencing_token, "inputs": inputs,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self._max_command_bytes:
            raise ValueError("cutover command exceeds configured byte limit")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _record(evidence_id: str, command_digest: str, partition: PartitionKey,
                action: CutoverAction, actor: str, now: datetime, prior: PartitionCutover,
                resulting: PartitionCutover, lease: WriterLease, gate: GateEvidence | None = None,
                reason: str = "", extra_refs: tuple[EvidenceReference, ...] = ()) -> PromotionEvidence:
        refs = list(_gate_references(gate)) + list(extra_refs)
        if any(ref.partition != partition for ref in refs):
            raise CutoverConflict("lineage evidence reference is bound to another partition")
        payload = {
            "evidence_id": evidence_id, "command_digest": command_digest,
            "partition": [partition.tenant_id, partition.app_definition_id],
            "action": action.value, "actor": actor,
            "occurred_at": now.isoformat(),
            "prior_state": prior.state.value, "resulting_state": resulting.state.value,
            "prior_read_owner": prior.canonical_read_owner,
            "resulting_read_owner": resulting.canonical_read_owner,
            "prior_decision_owner": prior.decision_owner,
            "resulting_decision_owner": resulting.decision_owner,
            "prior_write_owner": prior.write_owner, "resulting_write_owner": resulting.write_owner,
            "prior_record_id": prior.last_evidence_id,
            "prior_record_digest": prior.lineage_head_digest,
            "canonical_positive_writes": resulting.canonical_positive_writes,
            "writer_epoch": lease.epoch, "lease_fencing_token": lease.fencing_token,
            "gate_evidence": [
                (ref.stage, ref.evidence_id, ref.evidence_digest,
                 ref.partition.tenant_id, ref.partition.app_definition_id)
                for ref in refs
            ],
            "parity_report_digest": gate.report_digest if gate else None,
            "canonical_snapshot_digest": gate.canonical_digest if gate else None,
            "source_snapshot_digest": gate.source_snapshot_digest if gate else prior.source_snapshot_digest,
            "final_delta_checkpoint": gate.final_delta_checkpoint if gate else None,
            "final_delta_source_change_sequence": (
                gate.final_delta.source_change_sequence if gate and gate.final_delta else None
            ),
            "reason": reason,
        }
        record_digest = _lineage_payload_digest(payload)
        record_type = RollbackRecord if action in {
            CutoverAction.ROLLED_BACK, CutoverAction.REPAIR_REQUIRED, CutoverAction.REPAIRED,
        } else PromotionRecord
        return record_type(
            evidence_id=evidence_id, partition=partition, action=action, actor=actor,
            occurred_at=now, prior_state=prior.state, resulting_state=resulting.state,
            prior_read_owner=prior.canonical_read_owner,
            resulting_read_owner=resulting.canonical_read_owner, writer_epoch=lease.epoch,
            lease_fencing_token=lease.fencing_token,
            parity_report_digest=payload["parity_report_digest"],
            canonical_snapshot_digest=payload["canonical_snapshot_digest"],
            source_snapshot_digest=payload["source_snapshot_digest"],
            final_delta_checkpoint=payload["final_delta_checkpoint"],
            final_delta_source_change_sequence=payload["final_delta_source_change_sequence"],
            reason=reason,
            record_digest=record_digest, command_digest=command_digest,
            prior_write_owner=prior.write_owner, resulting_write_owner=resulting.write_owner,
            prior_decision_owner=prior.decision_owner,
            resulting_decision_owner=resulting.decision_owner,
            prior_record_id=prior.last_evidence_id,
            prior_record_digest=prior.lineage_head_digest,
            gate_evidence=tuple(refs), canonical_positive_writes=resulting.canonical_positive_writes,
        )

    @staticmethod
    def _lease_reason(partition: PartitionKey, lease: WriterLease, now: datetime,
                      minimum_epoch: int) -> str | None:
        if lease.partition != partition:
            return "lease_partition_mismatch"
        if lease.expires_at <= now:
            return "writer_lease_expired"
        if lease.epoch <= minimum_epoch:
            return "writer_epoch_not_advanced"
        return None

    @staticmethod
    def _blocked_state(current: PartitionCutover, lease: WriterLease) -> PartitionCutover:
        if current.canonical_read_owner == "canonical":
            return replace(current, state=CutoverState.REPAIR_REQUIRED, write_owner="none",
                           writer_epoch=lease.epoch, lease_holder=lease.holder,
                           fencing_token=lease.fencing_token, legacy_projection_read_only=True,
                           canonical_positive_writes=False, revision=current.revision + 1)
        return replace(current, state=CutoverState.BLOCKED, writer_epoch=lease.epoch,
                       lease_holder=lease.holder, fencing_token=lease.fencing_token,
                       canonical_positive_writes=False, revision=current.revision + 1)

    async def _commit_or_replay(self, current: PartitionCutover, resulting: PartitionCutover,
                                evidence: PromotionEvidence, lease: WriterLease) -> PartitionCutover:
        prior = await self._repository.evidence_by_id(evidence.evidence_id)
        if prior is not None:
            self._assert_same_command(prior, evidence)
            committed = await self._repository.load(evidence.partition)
            if committed is None or committed.partition != evidence.partition:
                raise CutoverConflict("idempotent command record exists without its partition state")
            if (committed.last_evidence_id != prior.evidence_id
                    and committed.lineage_head_digest != prior.record_digest):
                raise CutoverConflict("command evidence is not present in the current partition lineage")
            return committed
        resulting = replace(resulting, last_evidence_id=evidence.evidence_id,
                            lineage_head_digest=evidence.record_digest)
        if not await self._repository.transition(
            expected_revision=current.revision, state=resulting, evidence=evidence, lease=lease,
            expected_source_change_sequence=(
                resulting.final_delta_source_change_sequence
                if resulting.state is CutoverState.FINAL_DELTA
                else current.final_delta_source_change_sequence
                if current.state is CutoverState.FINAL_DELTA else None
            ),
        ):
            raced = await self._repository.evidence_by_id(evidence.evidence_id)
            if raced is not None:
                self._assert_same_command(raced, evidence)
                return await self._repository.load(evidence.partition) or resulting
            raise CutoverConflict("partition revision or fencing conflict; reconcile from immutable lineage")
        return resulting

    async def _replay(self, command_id: str, partition: PartitionKey, operation: str,
                      actor: str, lease: WriterLease, inputs: Mapping[str, object],
                      allowed_actions: set[CutoverAction]) -> PartitionCutover | None:
        self._validate_command(actor, command_id)
        command_digest = self._command_digest(command_id, partition, operation, actor, lease, inputs)
        prior = await self._repository.evidence_by_id(command_id)
        if prior is None:
            return None
        if (prior.partition != partition or prior.action not in allowed_actions
                or prior.command_digest != command_digest):
            raise CutoverConflict("command id replay has mismatched partition, action, or digest")
        current = await self._repository.load(partition)
        if current is None or current.partition != partition:
            raise CutoverConflict("idempotent command record exists without its partition state")
        return current

    @staticmethod
    def _assert_same_command(prior: PromotionEvidence, requested: PromotionEvidence) -> None:
        if (prior.partition != requested.partition or prior.action is not requested.action
                or prior.command_digest != requested.command_digest):
            raise CutoverConflict("command id replay has mismatched partition, action, or digest")

    @staticmethod
    def _validate_command(actor: str, command_id: str) -> None:
        if not actor.strip() or not command_id.strip():
            raise ValueError("actor and idempotency command id are required")
        if len(actor.encode("utf-8")) > 512 or len(command_id.encode("utf-8")) > 512:
            raise ValueError("actor or idempotency command id exceeds limit")


def _gate_references(gate: GateEvidence | None) -> tuple[EvidenceReference, ...]:
    if gate is None:
        return ()
    refs: list[EvidenceReference] = []

    def add(stage: str, evidence_id: str | None, evidence_digest: str | None) -> None:
        if evidence_id and evidence_id.strip() and evidence_digest and _is_sha256(evidence_digest):
            refs.append(EvidenceReference(stage, evidence_id, evidence_digest, gate.partition))

    for stage, evidence in (("M04", gate.m04), ("M04E", gate.m04e), ("M05", gate.m05)):
        if evidence is not None:
            add(stage, evidence.evidence_id, evidence.evidence_digest)
    add("M06", gate.evidence_id, gate.evidence_digest)
    add("legacy_owner", gate.legacy_owner_evidence_id, gate.legacy_owner_evidence_digest)
    if gate.legacy_writer_drain is not None:
        add("legacy_writer_drain", gate.legacy_writer_drain.evidence_id,
            gate.legacy_writer_drain.evidence_digest)
    if gate.final_delta is not None:
        add("final_delta", gate.final_delta.evidence_id, gate.final_delta.evidence_digest)
    return tuple(refs)


def _legacy_owner_fingerprint(evidence: LegacyOwnerEvidence) -> tuple[object, ...]:
    return (evidence.partition.tenant_id, evidence.partition.app_definition_id,
            evidence.evidence_id, evidence.evidence_digest, evidence.source_snapshot_digest,
            evidence.writer_epoch, evidence.exclusive_read_owner, evidence.exclusive_decision_owner,
            evidence.exclusive_write_owner, evidence.legacy_claims_enabled)


def _gate_fingerprint(gate: GateEvidence | None) -> str | None:
    if gate is None:
        return None
    payload = {
        "partition": [gate.partition.tenant_id, gate.partition.app_definition_id],
        "report_digest": gate.report_digest, "source_snapshot_digest": gate.source_snapshot_digest,
        "final_delta_checkpoint": gate.final_delta_checkpoint, "writer_epoch": gate.writer_epoch,
        "counts": [gate.observation_count, gate.unexplained_mismatch_count,
                   gate.unresolved_positive_bindings, gate.unknown_mapping_count,
                   gate.stale_generation_acceptances],
        "flags": [gate.audit_complete, gate.parity_ready, gate.handoff_verified],
        "m06": [gate.evidence_id, gate.evidence_digest, gate.canonical_digest,
                 gate.verifier_ref],
        "lineage": [(x.stage.value, x.partition.tenant_id, x.partition.app_definition_id,
                     x.evidence_id, x.evidence_digest, x.source_snapshot_digest,
                     x.canonical_digest, x.gate_passed, x.parent_evidence_id,
                     x.parent_evidence_digest) if x else None
                    for x in (gate.m04, gate.m04e, gate.m05)],
        "parent": [gate.parent_evidence_id, gate.parent_evidence_digest],
        "legacy_owner": [gate.legacy_owner_evidence_id, gate.legacy_owner_evidence_digest],
        "drain": ((gate.legacy_writer_drain.partition.tenant_id,
                   gate.legacy_writer_drain.partition.app_definition_id,
                   gate.legacy_writer_drain.evidence_id, gate.legacy_writer_drain.evidence_digest,
                   gate.legacy_writer_drain.source_snapshot_digest, gate.legacy_writer_drain.writer_epoch,
                   gate.legacy_writer_drain.new_claims_stopped,
                   gate.legacy_writer_drain.active_writers_remaining,
                   gate.legacy_writer_drain.unsettled_claims_remaining,
                   gate.legacy_writer_drain.frozen_change_sequence)
                  if gate.legacy_writer_drain else None),
        "delta": ((gate.final_delta.partition.tenant_id, gate.final_delta.partition.app_definition_id,
                   gate.final_delta.evidence_id, gate.final_delta.evidence_digest,
                   gate.final_delta.source_snapshot_digest, gate.final_delta.checkpoint,
                   gate.final_delta.checkpoint_digest, gate.final_delta.writer_epoch,
                   gate.final_delta.source_change_sequence, gate.final_delta.captured_after_drain,
                   gate.final_delta.drain_evidence_id, gate.final_delta.drain_evidence_digest)
                  if gate.final_delta else None),
        "reconciliation": gate.canonical_reconciliation_digest,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _rollback_fingerprint(evidence: RollbackEvidence | None) -> tuple[object, ...] | None:
    if evidence is None:
        return None
    return (evidence.partition.tenant_id, evidence.partition.app_definition_id,
            evidence.evidence_id, evidence.evidence_digest, evidence.source_snapshot_digest,
            evidence.legacy_owner_evidence_id, evidence.legacy_owner_evidence_digest,
            evidence.writer_epoch, evidence.legacy_owner_restored, evidence.canonical_claims_stopped,
            evidence.active_canonical_writers_remaining, evidence.unresolved_legacy_claims)


def _repair_fingerprint(evidence: CanonicalRepairEvidence | None) -> tuple[object, ...] | None:
    if evidence is None:
        return None
    return (evidence.partition.tenant_id, evidence.partition.app_definition_id,
            evidence.evidence_id, evidence.evidence_digest, evidence.source_snapshot_digest,
            evidence.checkpoint_digest, evidence.writer_epoch, evidence.parent_evidence_id,
            evidence.parent_evidence_digest,
            tuple((item.stage, item.evidence_id, item.evidence_digest,
                   (item.partition.tenant_id, item.partition.app_definition_id)
                   if item.partition else None) for item in evidence.gate_evidence),
            evidence.unresolved_conflicts, evidence.unknown_mappings,
            evidence.unresolved_positive_bindings, evidence.stale_generation_acceptances,
            evidence.active_canonical_writers_remaining, evidence.canonical_writes_stopped,
            evidence.audit_complete)


def _checked_now(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    _aware(value)
    return value


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")


def _is_sha256(value: str) -> bool:
    if len(value) != 71 or not value.startswith("sha256:"):
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True


class CutoverConflict(RuntimeError):
    """Concurrent, stale, post-cutover rollback, or conflicting cutover command."""
