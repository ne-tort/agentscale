"""Bounded per-partition M06 cutover with fail-closed gates and fencing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class CutoverState(StrEnum):
    LEGACY_ACTIVE = "legacy_active"
    BLOCKED = "blocked"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"


class CutoverAction(StrEnum):
    BLOCKED = "blocked"
    PROMOTED = "promoted"
    REPLAY = "replay"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class PartitionKey:
    tenant_id: str
    app_definition_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.app_definition_id.strip():
            raise ValueError("partition key fields are required")


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """Typed, partition-bound M06-02 evidence; omitted evidence is not ready."""

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
    verifier_ref: str | None = None

    def __post_init__(self) -> None:
        for value, label in ((self.report_digest, "report_digest"),
                             (self.source_snapshot_digest, "source_snapshot_digest"),
                             (self.evidence_digest, "evidence_digest")):
            if value is not None and (not value.startswith("sha256:") or len(value) != 71):
                raise ValueError(f"{label} must be a sha256 digest")

    @property
    def ready(self) -> bool:
        return bool(
            self.parity_ready and self.handoff_verified and self.report_digest
            and self.source_snapshot_digest and self.final_delta_checkpoint
            and self.writer_epoch is not None and self.writer_epoch > 0
            and self.observation_count > 0 and self.unexplained_mismatch_count == 0
            and self.unresolved_positive_bindings == 0 and self.unknown_mapping_count == 0
            and self.stale_generation_acceptances == 0 and self.audit_complete
            and self.evidence_id and self.evidence_digest and self.verifier_ref
        )


# Existing acceptance fixtures use this name; keep it as the typed gate contract.
ParityGate = GateEvidence


class GateVerifier(Protocol):
    async def verify(self, *, evidence: GateEvidence) -> bool: ...


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
    writer_epoch: int = 0
    lease_holder: str | None = None
    fencing_token: str | None = None
    legacy_projection_read_only: bool = False
    last_evidence_id: str | None = None
    revision: int = 0


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
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
    source_snapshot_digest: str | None
    final_delta_checkpoint: str | None
    reason: str
    record_digest: str


class CutoverRepository(Protocol):
    async def load(self, partition: PartitionKey) -> PartitionCutover | None: ...

    async def transition(
        self,
        *,
        expected_revision: int,
        state: PartitionCutover,
        evidence: PromotionEvidence,
        lease: WriterLease,
    ) -> bool:
        """Atomically persist state and immutable evidence under revision/fencing."""
        ...

    async def evidence_by_id(self, evidence_id: str) -> PromotionEvidence | None: ...


class CutoverCommandService:
    """Partition-only command handler with one atomic state/evidence transition."""

    def __init__(self, repository: CutoverRepository, *, gate_verifier: GateVerifier | None = None,
                 max_command_bytes: int = 16_384) -> None:
        if not 1 <= max_command_bytes <= 65_536:
            raise ValueError("max_command_bytes must be between 1 and 65536")
        self._repository = repository
        self._gate_verifier = gate_verifier
        self._max_command_bytes = max_command_bytes

    async def promote(self, *, partition: PartitionKey, actor: str, command_id: str,
                      lease: WriterLease, gate: GateEvidence | None = None,
                      now: datetime | None = None) -> PartitionCutover:
        checked_at = now or datetime.now(UTC)
        _aware(checked_at)
        self._validate_command(actor, command_id)
        current = await self._repository.load(partition) or PartitionCutover(partition)
        if current.partition != partition:
            raise CutoverConflict("repository returned another partition")

        reason = await self._promotion_block_reason(partition, lease, gate, checked_at, current)
        if reason is not None:
            resulting = self._blocked(partition, command_id, current)
            evidence = self._evidence(command_id, partition, CutoverAction.BLOCKED, actor, checked_at,
                                      current, resulting, lease, gate, reason)
            return await self._commit_or_replay(current, resulting, evidence, lease)

        promoted = PartitionCutover(
            partition=partition, state=CutoverState.PROMOTED, canonical_read_owner="canonical",
            decision_owner="legacy", writer_epoch=lease.epoch, lease_holder=lease.holder,
            fencing_token=lease.fencing_token, legacy_projection_read_only=True,
            last_evidence_id=command_id, revision=current.revision + 1,
        )
        evidence = self._evidence(command_id, partition, CutoverAction.PROMOTED, actor, checked_at,
                                  current, promoted, lease, gate, "cutover_promoted")
        return await self._commit_or_replay(current, promoted, evidence, lease)

    async def rollback(self, *, partition: PartitionKey, actor: str, command_id: str,
                       lease: WriterLease, now: datetime | None = None) -> PartitionCutover:
        checked_at = now or datetime.now(UTC)
        _aware(checked_at)
        self._validate_command(actor, command_id)
        current = await self._repository.load(partition) or PartitionCutover(partition)
        if current.state is not CutoverState.PROMOTED:
            raise CutoverConflict("only the promoted partition can be rolled back")
        reason = self._lease_reason(partition, lease, checked_at, current.writer_epoch)
        if reason is not None:
            raise CutoverConflict(reason)
        rolled_back = PartitionCutover(
            partition=partition, state=CutoverState.ROLLED_BACK, canonical_read_owner="legacy",
            decision_owner="legacy", writer_epoch=lease.epoch, lease_holder=lease.holder,
            fencing_token=lease.fencing_token, legacy_projection_read_only=False,
            last_evidence_id=command_id, revision=current.revision + 1,
        )
        evidence = self._evidence(command_id, partition, CutoverAction.ROLLED_BACK, actor, checked_at,
                                  current, rolled_back, lease, None, "partition_rollback_to_legacy")
        return await self._commit_or_replay(current, rolled_back, evidence, lease)

    async def _commit_or_replay(self, current: PartitionCutover, resulting: PartitionCutover,
                                evidence: PromotionEvidence, lease: WriterLease) -> PartitionCutover:
        prior = await self._repository.evidence_by_id(evidence.evidence_id)
        if prior is not None:
            if (prior.partition != evidence.partition or prior.record_digest != evidence.record_digest
                    or prior.resulting_state is not evidence.resulting_state):
                raise CutoverConflict("command id replay has mismatched digest or resulting state")
            return await self._repository.load(evidence.partition) or current
        if not await self._repository.transition(expected_revision=current.revision,
                                                 state=resulting, evidence=evidence, lease=lease):
            raise CutoverConflict("partition revision or fencing conflict; reconciliation required")
        return resulting

    @staticmethod
    def _validate_command(actor: str, command_id: str) -> None:
        if not actor.strip() or not command_id.strip():
            raise ValueError("actor and idempotency command id are required")

    @staticmethod
    def _blocked(partition: PartitionKey, command_id: str, current: PartitionCutover) -> PartitionCutover:
        return PartitionCutover(
            partition=partition, state=CutoverState.BLOCKED,
            canonical_read_owner=current.canonical_read_owner, decision_owner=current.decision_owner,
            writer_epoch=current.writer_epoch, lease_holder=current.lease_holder,
            fencing_token=current.fencing_token, legacy_projection_read_only=current.legacy_projection_read_only,
            last_evidence_id=command_id, revision=current.revision + 1,
        )

    @staticmethod
    async def _verify_gate(verifier: GateVerifier | None, gate: GateEvidence | None) -> bool:
        if gate is None or verifier is None or not gate.ready:
            return False
        return await verifier.verify(evidence=gate)

    async def _promotion_block_reason(self, partition: PartitionKey, lease: WriterLease,
                                      gate: GateEvidence | None, now: datetime,
                                      current: PartitionCutover) -> str | None:
        if gate is None:
            return "m06_02_gate_evidence_missing"
        if gate.partition != partition:
            return "parity_partition_mismatch"
        if not gate.ready or not await self._verify_gate(self._gate_verifier, gate):
            return "m06_02_gate_evidence_not_verified"
        lease_reason = self._lease_reason(partition, lease, now, current.writer_epoch)
        if lease_reason:
            return lease_reason
        if current.state is CutoverState.PROMOTED:
            return "already_promoted"
        if current.state is CutoverState.ROLLED_BACK and lease.epoch <= current.writer_epoch:
            return "writer_epoch_not_advanced_after_rollback"
        return None

    @staticmethod
    def _lease_reason(partition: PartitionKey, lease: WriterLease, now: datetime, minimum_epoch: int) -> str | None:
        if lease.partition != partition:
            return "lease_partition_mismatch"
        if lease.expires_at <= now:
            return "writer_lease_expired"
        if lease.epoch <= minimum_epoch:
            return "writer_epoch_stale"
        return None

    @staticmethod
    def _evidence(evidence_id: str, partition: PartitionKey, action: CutoverAction, actor: str,
                  now: datetime, prior: PartitionCutover, resulting: PartitionCutover,
                  lease: WriterLease, gate: GateEvidence | None, reason: str) -> PromotionEvidence:
        payload = {
            "evidence_id": evidence_id, "tenant_id": partition.tenant_id,
            "app_definition_id": partition.app_definition_id, "action": action.value,
            "actor": actor, "occurred_at": now.isoformat(), "prior_state": prior.state.value,
            "resulting_state": resulting.state.value, "prior_read_owner": prior.canonical_read_owner,
            "resulting_read_owner": resulting.canonical_read_owner, "writer_epoch": lease.epoch,
            "lease_fencing_token": lease.fencing_token,
            "parity_report_digest": gate.report_digest if gate else None,
            "source_snapshot_digest": gate.source_snapshot_digest if gate else None,
            "final_delta_checkpoint": gate.final_delta_checkpoint if gate else None,
            "reason": reason,
        }
        record_digest = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return PromotionEvidence(evidence_id, partition, action, actor, now, prior.state, resulting.state,
                                 prior.canonical_read_owner, resulting.canonical_read_owner, lease.epoch,
                                 lease.fencing_token, payload["parity_report_digest"],
                                 payload["source_snapshot_digest"], payload["final_delta_checkpoint"],
                                 reason, record_digest)


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")


class CutoverConflict(RuntimeError):
    """Concurrent, stale or conflicting partition cutover command."""
