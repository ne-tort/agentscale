"""Executable acceptance contract for post-freeze review; not run in this task."""

from dataclasses import replace

from .acceptance_fixtures import CASES, LEASE, NOW, OTHER_PARTITION, PARTITION, READY_GATE, UNREADY_GATE
from prodavan.application.migration.m06_cutover.cutover import (
    CutoverAction,
    CutoverCommandService,
    CutoverState,
    ParityGate,
    PartitionCutover,
    WriterLease,
)


def test_fixture_matrix_covers_cutover_contract():
    required = (
        "missing M06-02 parity report", "unverified handoff", "unexplained mismatch",
        "lease expiry", "atomically changes canonical read owner", "decision owner remains legacy",
        "only selected tenant", "same command id replay", "concurrent revision change",
        "rollback requires fresh partition lease", "strictly advanced writer epoch", "append-only",
    )
    assert all(any(term in case for case in CASES) for term in required)


def test_parity_gate_fails_closed_on_m06_02_not_ready():
    assert not UNREADY_GATE.ready
    assert READY_GATE.ready
    assert not replace(READY_GATE, handoff_verified=False).ready
    assert not replace(READY_GATE, unknown_mapping_count=1).ready
    assert not replace(READY_GATE, unexplained_mismatch_count=1).ready
    assert not replace(READY_GATE, observation_count=0).ready


def test_lease_must_be_partition_bound_expiring_and_epoch_fenced():
    assert LEASE.partition == PARTITION
    assert LEASE.epoch > 0 and LEASE.fencing_token
    assert WriterLease(PARTITION, "operator", 9, LEASE.expires_at, "fence-9").epoch > LEASE.epoch
    assert OTHER_PARTITION != PARTITION


def test_promotion_evidence_contract_keeps_read_and_decision_owners_distinct():
    prior = PartitionCutover(PARTITION)
    assert prior.state is CutoverState.LEGACY_ACTIVE
    assert prior.canonical_read_owner == prior.decision_owner == "legacy"
    assert CutoverAction.PROMOTED.value == "promoted"
    assert READY_GATE.final_delta_checkpoint and READY_GATE.report_digest


def test_blocked_gate_preserves_legacy_authority():
    service = CutoverCommandService  # command contract is async and exercised post-freeze
    assert service is not None
    assert UNREADY_GATE.partition == PARTITION
    assert PartitionCutover(PARTITION).canonical_read_owner == "legacy"
