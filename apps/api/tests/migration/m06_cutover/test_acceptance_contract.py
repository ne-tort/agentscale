"""Post-freeze acceptance contract for immutable M06 cutover transitions."""

from dataclasses import replace

import pytest

from prodavan.application.migration.m06_cutover.cutover import (
    CutoverAction,
    CutoverCommandService,
    CutoverConflict,
    CutoverState,
    PartitionCutover,
    PromotionEvidence,
    PromotionRecord,
    RollbackRecord,
)
from .acceptance_fixtures import (
    CASES,
    NOW,
    OTHER_PARTITION,
    PARTITION,
    UNREADY_GATE,
    gate,
    lease,
    legacy_owner,
    repair_evidence,
    rollback_evidence,
)


class Verifier:
    def __init__(self, accepts=True):
        self.accepts = accepts

    async def verify(self, *, evidence):
        return self.accepts


class Repository:
    """Acceptance fake models atomic owner/evidence CAS and final source-tail fencing."""

    def __init__(self, initial=None, *, live_source_sequence=None):
        self.states = {initial.partition: initial} if initial else {}
        self.records: dict[str, PromotionEvidence] = {}
        self.live_source_sequence = live_source_sequence

    async def load(self, partition):
        return self.states.get(partition)

    async def evidence_by_id(self, evidence_id):
        return self.records.get(evidence_id)

    async def transition(self, *, expected_revision, state, evidence, lease,
                         expected_source_change_sequence):
        current = self.states.get(evidence.partition) or PartitionCutover(evidence.partition)
        if current.revision != expected_revision or lease.partition != evidence.partition:
            return False
        if (expected_source_change_sequence is not None
                and self.live_source_sequence != expected_source_change_sequence):
            return False
        if evidence.evidence_id in self.records:
            return False
        if state.partition != evidence.partition or evidence.prior_record_id != current.last_evidence_id:
            return False
        if evidence.prior_record_digest != current.lineage_head_digest:
            return False
        if state.last_evidence_id != evidence.evidence_id:
            return False
        self.records[evidence.evidence_id] = evidence
        self.states[evidence.partition] = state
        return True


def service(repository, *, gate_accepts=True):
    return CutoverCommandService(
        repository, gate_verifier=Verifier(gate_accepts), legacy_owner_verifier=Verifier(),
        rollback_verifier=Verifier(), repair_verifier=Verifier(),
    )


async def establish_final_delta(prefix="promote", *, gate_evidence=None):
    repository = Repository(live_source_sequence=42)
    commands = service(repository)
    bound = await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id=f"{prefix}-owner",
        lease=lease(1), evidence=legacy_owner(1), now=NOW,
    )
    assert bound.canonical_read_owner == "legacy"
    shadow = await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id=f"{prefix}-shadow",
        lease=lease(2), now=NOW,
    )
    assert shadow.canonical_shadow_reads and shadow.write_owner == "legacy"
    frozen = await commands.freeze_final_delta(
        partition=PARTITION, actor="operator", command_id=f"{prefix}-delta",
        lease=lease(3), gate=gate_evidence or gate(), now=NOW,
    )
    assert frozen.state is CutoverState.FINAL_DELTA
    assert frozen.canonical_read_owner == "legacy" and frozen.write_owner == "none"
    return repository, commands, frozen


def test_fixture_matrix_covers_required_cutover_cases():
    required = (
        "M04", "M04E", "M05", "M06", "legacy writer drain", "same command id",
        "canonical promotion", "rollback", "forward repair",
    )
    assert all(any(term in case for case in CASES) for term in required)


def test_gate_evidence_fails_closed_without_all_upstream_lineage():
    assert not UNREADY_GATE.ready
    complete = gate()
    assert complete.ready
    assert not replace(complete, m04=None).ready
    assert not replace(complete, m04e=None).ready
    assert not replace(complete, m05=None).ready
    assert complete.m04e is not None
    assert complete.m05 is not None
    assert not replace(complete, m04e=replace(complete.m04e, parent_evidence_id="wrong")).ready
    assert not replace(complete, m04e=replace(complete.m04e, parent_evidence_digest="sha256:" + "9" * 64)).ready
    assert complete.m05 is not None
    assert not replace(complete, m05=replace(complete.m05, partition=OTHER_PARTITION)).ready
    assert not replace(complete, m05=replace(complete.m05, evidence_id=complete.m04e.evidence_id)).ready
    assert not replace(complete, canonical_digest=None).ready
    assert not replace(complete, unknown_mapping_count=1).ready
    assert not replace(complete, unexplained_mismatch_count=1).ready
    assert not replace(complete, observation_count=0).ready
    assert not replace(complete, audit_complete=False).ready
    assert not replace(complete, observation_count=-1).ready


def test_final_delta_requires_stopped_and_drained_legacy_writers():
    assert gate().ready
    assert not gate(active_legacy_writers=1).ready
    changed_seq = gate()
    assert changed_seq.final_delta is not None
    assert changed_seq.legacy_writer_drain is not None
    assert not replace(changed_seq, final_delta=replace(
        changed_seq.final_delta, source_change_sequence=changed_seq.legacy_writer_drain.frozen_change_sequence + 1,
    )).ready
    complete = gate()
    assert complete.final_delta is not None
    assert not replace(complete, final_delta=replace(complete.final_delta, captured_after_drain=False)).ready
    assert not replace(complete, final_delta=replace(complete.final_delta, writer_epoch=4)).ready
    assert complete.legacy_writer_drain is not None
    assert not replace(complete, legacy_writer_drain=replace(complete.legacy_writer_drain, new_claims_stopped=False)).ready
    assert not replace(complete, final_delta=replace(
        complete.final_delta, drain_evidence_id="different-drain",
    )).ready


@pytest.mark.asyncio
async def test_legacy_owner_marker_must_be_verified_before_shadow_and_epoch_fenced():
    repository = Repository()
    commands = service(repository)
    with pytest.raises(CutoverConflict, match="writer epoch"):
        await commands.bind_legacy_owner(
            partition=PARTITION, actor="operator", command_id="owner-stale-epoch",
            lease=lease(1), evidence=legacy_owner(0), now=NOW,
        )
    with pytest.raises(CutoverConflict, match="marker must be bound"):
        await commands.enter_shadow(
            partition=PARTITION, actor="operator", command_id="shadow-without-owner-proof",
            lease=lease(1), now=NOW,
        )
    assert repository.states == {}


@pytest.mark.asyncio
async def test_gate_verifier_rejection_keeps_legacy_authority_and_blocks_cutover():
    repository = Repository(live_source_sequence=42)
    commands = service(repository, gate_accepts=False)
    await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="unverified-owner",
        lease=lease(1), evidence=legacy_owner(1), now=NOW,
    )
    await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="unverified-shadow",
        lease=lease(2), now=NOW,
    )
    blocked = await commands.freeze_final_delta(
        partition=PARTITION, actor="operator", command_id="unverified-final-delta",
        lease=lease(3), gate=gate(), now=NOW,
    )
    assert blocked.state is CutoverState.BLOCKED
    assert blocked.canonical_read_owner == blocked.decision_owner == "legacy"
    assert blocked.write_owner == "legacy" and not blocked.canonical_positive_writes
    assert repository.records["unverified-final-delta"].action is CutoverAction.BLOCKED


@pytest.mark.asyncio
async def test_positive_owner_transition_requires_full_verified_gate_and_is_partition_bound():
    repository, commands, _ = await establish_final_delta()

    promoted = await commands.promote(
        partition=PARTITION, actor="operator", command_id="promote-final",
        lease=lease(3), gate=gate(), now=NOW,
    )
    assert promoted.state is CutoverState.PROMOTED
    assert (promoted.canonical_read_owner, promoted.decision_owner, promoted.write_owner) == (
        "canonical", "canonical", "canonical",
    )
    assert promoted.legacy_projection_read_only and promoted.canonical_positive_writes
    assert await repository.load(OTHER_PARTITION) is None
    record = repository.records["promote-final"]
    assert isinstance(record, PromotionRecord)
    assert record.partition == PARTITION
    assert {ref.stage for ref in record.gate_evidence} >= {"M04", "M04E", "M05", "M06"}
    assert record.prior_record_id == "promote-delta"
    assert record.canonical_snapshot_digest == gate().canonical_digest
    assert record.final_delta_source_change_sequence == 42


@pytest.mark.asyncio
async def test_missing_gate_evidence_cannot_promote_or_enable_canonical_writes():
    repository, commands, _ = await establish_final_delta()
    blocked = await commands.promote(
        partition=PARTITION, actor="operator", command_id="promote-missing",
        lease=lease(3), gate=None, now=NOW,
    )
    assert blocked.state is CutoverState.BLOCKED
    assert blocked.write_owner == "none" and not blocked.canonical_positive_writes
    assert blocked.canonical_read_owner == "legacy"
    assert repository.records["promote-missing"].action is CutoverAction.BLOCKED


@pytest.mark.asyncio
async def test_changed_final_delta_after_freeze_is_blocked():
    repository, commands, _ = await establish_final_delta()
    changed = gate(final_delta_checkpoint="checkpoint-43", source_sequence=42)
    result = await commands.promote(
        partition=PARTITION, actor="operator", command_id="promote-raced-delta",
        lease=lease(3), gate=changed, now=NOW,
    )
    assert result.state is CutoverState.BLOCKED
    assert result.write_owner == "none" and not result.canonical_positive_writes
    assert result.canonical_read_owner == "legacy"
    assert repository.records["promote-raced-delta"].action is CutoverAction.BLOCKED


@pytest.mark.asyncio
async def test_live_source_tail_race_fails_closed_before_promotion():
    repository, commands, _ = await establish_final_delta("source-race")
    repository.live_source_sequence = 43
    with pytest.raises(CutoverConflict, match="revision or fencing conflict"):
        await commands.promote(
            partition=PARTITION, actor="operator", command_id="source-race-promote",
            lease=lease(3), gate=gate(), now=NOW,
        )
    assert repository.states[PARTITION].state is CutoverState.FINAL_DELTA
    assert "source-race-promote" not in repository.records


@pytest.mark.asyncio
async def test_same_command_retry_is_idempotent_and_cross_partition_reuse_conflicts():
    repository, commands, _ = await establish_final_delta()
    first = await commands.promote(
        partition=PARTITION, actor="operator", command_id="promote-once",
        lease=lease(3), gate=gate(), now=NOW,
    )
    retry = await commands.promote(
        partition=PARTITION, actor="operator", command_id="promote-once",
        lease=lease(3), gate=gate(), now=NOW,
    )
    assert retry == first
    assert len(repository.records) == 4
    with pytest.raises(CutoverConflict, match="partition, action, or digest"):
        await commands.promote(
            partition=PARTITION, actor="different-operator", command_id="promote-once",
            lease=lease(3), gate=gate(), now=NOW,
        )
    with pytest.raises(CutoverConflict, match="partition, action, or digest"):
        await commands.promote(
            partition=OTHER_PARTITION, actor="operator", command_id="promote-once",
            lease=lease(3, partition=OTHER_PARTITION), gate=gate(partition=OTHER_PARTITION), now=NOW,
        )


@pytest.mark.asyncio
async def test_pre_cutover_rollback_requires_fresh_evidence_and_keeps_one_legacy_owner():
    repository, commands, _ = await establish_final_delta("rollback")
    with pytest.raises(CutoverConflict, match="rollback evidence"):
        await commands.rollback(
            partition=PARTITION, actor="operator", command_id="rollback-missing",
            lease=lease(4), now=NOW,
        )
    rolled = await commands.rollback(
        partition=PARTITION, actor="operator", command_id="rollback-valid",
        lease=lease(4), evidence=rollback_evidence(4), now=NOW,
    )
    assert rolled.state is CutoverState.ROLLED_BACK
    assert (rolled.canonical_read_owner, rolled.decision_owner, rolled.write_owner) == (
        "legacy", "legacy", "legacy",
    )
    assert not rolled.legacy_projection_read_only
    assert isinstance(repository.records["rollback-valid"], RollbackRecord)
    assert repository.records["rollback-valid"].action is CutoverAction.ROLLED_BACK


@pytest.mark.asyncio
async def test_post_cutover_rollback_is_forbidden_and_repair_is_forward_only():
    repository, commands, _ = await establish_final_delta("forward")
    promoted = await commands.promote(
        partition=PARTITION, actor="operator", command_id="forward-promote",
        lease=lease(3), gate=gate(), now=NOW,
    )
    with pytest.raises(CutoverConflict, match="repair forward"):
        await commands.rollback(
            partition=PARTITION, actor="operator", command_id="rollback-after-promotion",
            lease=lease(4), evidence=rollback_evidence(4), now=NOW,
        )
    repair_lease = lease(4)
    assert promoted.last_evidence_id is not None and promoted.lineage_head_digest is not None
    repair = repair_evidence(4, promoted.last_evidence_id, promoted.lineage_head_digest)
    repaired = await commands.repair_forward(
        partition=PARTITION, actor="operator", command_id="repair-valid",
        lease=repair_lease, evidence=repair, now=NOW,
    )
    assert repaired.state is CutoverState.PROMOTED
    assert repaired.canonical_read_owner == repaired.decision_owner == repaired.write_owner == "canonical"
    assert repository.records["repair-valid"].action is CutoverAction.REPAIRED


@pytest.mark.asyncio
async def test_unverified_repair_keeps_canonical_reads_and_fences_all_writes():
    repository, commands, _ = await establish_final_delta("repair-blocked")
    promoted = await commands.promote(
        partition=PARTITION, actor="operator", command_id="repair-blocked-promote",
        lease=lease(3), gate=gate(), now=NOW,
    )
    assert promoted.last_evidence_id is not None and promoted.lineage_head_digest is not None
    invalid = repair_evidence(4, promoted.last_evidence_id, promoted.lineage_head_digest)
    invalid = replace(invalid, unknown_mappings=1)
    fenced = await commands.repair_forward(
        partition=PARTITION, actor="operator", command_id="repair-invalid",
        lease=lease(4), evidence=invalid, now=NOW,
    )
    assert fenced.state is CutoverState.REPAIR_REQUIRED
    assert fenced.canonical_read_owner == fenced.decision_owner == "canonical"
    assert fenced.write_owner == "none" and fenced.legacy_projection_read_only
    assert not fenced.canonical_positive_writes
    assert repository.records["repair-invalid"].action is CutoverAction.REPAIR_REQUIRED
    assert fenced.last_evidence_id is not None and fenced.lineage_head_digest is not None
    valid = repair_evidence(5, fenced.last_evidence_id, fenced.lineage_head_digest)
    restored = await commands.repair_forward(
        partition=PARTITION, actor="operator", command_id="repair-forward",
        lease=lease(5), evidence=valid, now=NOW,
    )
    assert restored.state is CutoverState.PROMOTED
    assert restored.canonical_read_owner == "canonical" and restored.write_owner == "canonical"


@pytest.mark.asyncio
async def test_writer_epoch_must_advance_after_rollback_before_retrying_shadow():
    repository, commands, _ = await establish_final_delta("epoch")
    await commands.rollback(
        partition=PARTITION, actor="operator", command_id="epoch-rollback",
        lease=lease(4), evidence=rollback_evidence(4), now=NOW,
    )
    with pytest.raises(CutoverConflict, match="writer_epoch_not_advanced"):
        await commands.enter_shadow(
            partition=PARTITION, actor="operator", command_id="epoch-stale",
            lease=lease(4), now=NOW,
        )
    advanced = await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="epoch-advanced",
        lease=lease(5), now=NOW,
    )
    assert advanced.state is CutoverState.SHADOW and advanced.writer_epoch == 5

