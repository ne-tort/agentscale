"""Bounded parity runner acceptance: determinism, checkpointing, replay, stable JSON."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from prodavan.application.migration.m06_grants_gateway.adapter import (
    ShadowDecision,
    binding_digest,
    digest,
    source_payload_digest,
)
from prodavan.application.migration.m06_grants_gateway.evidence_store import (
    InMemoryParityReportStore,
)
from prodavan.application.migration.m06_grants_gateway.parity_runner import (
    BoundedParityRunner,
    ObservationBatch,
    ParityCheckpoint,
    ParityEvidenceSummary,
    ParityPartition,
    ParityRunReport,
    ParityRunRequest,
    ParityStatus,
    ReportOutcome,
    UnexplainedMismatch,
    run_key_for,
)

from .acceptance_fixtures import CONTEXT, GOOD_GATES, IDENTITY, NOW, PARITY_REQUEST, SOURCE


class _Pages:
    """Deterministic, cursor-based observation source over a fixed list."""

    def __init__(self, observations, *, regress_cursor: bool = False, duplicate: bool = False):
        self._observations = observations
        self._regress_cursor = regress_cursor
        self._duplicate = duplicate
        self.reads = 0

    async def load_observations(self, *, context, tenant_id, app_definition_id, window_start,
                                window_end, after_cursor=None, limit=500):
        self.reads += 1
        start = 0 if after_cursor is None else int(after_cursor)
        page = self._observations[start:start + limit]
        if self._duplicate and start == 0 and page:
            page = [page[0], *page]
        next_cursor = (str(max(0, start - 1)) if self._regress_cursor
                       else str(start + len(page)))
        return ObservationBatch(tuple(page), next_cursor, start + limit < len(self._observations))


class _Summary:
    def __init__(self, *, snapshot="sha256:" + "a" * 64, unknown=0, unresolved=0, stale=0,
                 audit=True, partition=None):
        self.snapshot = snapshot
        self.unknown = unknown
        self.unresolved = unresolved
        self.stale = stale
        self.audit = audit
        self.partition = partition

    async def load_summary(self, *, context, partition, window_start, window_end):
        return ParityEvidenceSummary(
            partition=self.partition or partition,
            source_snapshot_digest=self.snapshot,
            final_delta_checkpoint="final-delta-1",
            writer_epoch=7,
            unknown_mapping_count=self.unknown,
            unresolved_positive_bindings=self.unresolved,
            stale_generation_acceptances=self.stale,
            audit_complete=self.audit,
        )


def _observation(index: int, *, mismatch: bool = False, explained: bool = True,
                 tenant: str = "tenant-a"):
    from prodavan.application.migration.m06_grants_gateway.adapter import ShadowObservation

    binding = binding_digest(IDENTITY)
    legacy = ShadowDecision(True, "allowed", binding)
    canonical = (ShadowDecision(False, "expired", binding) if mismatch
                 else ShadowDecision(True, "allowed", binding))
    return ShadowObservation(
        tenant_id=tenant, app_definition_id="definition-a", source_id=f"grant-{index:03d}",
        capability_key="storage.files", source_digest=digest(["source", index]),
        canonical_digest=digest(["canonical", index]), binding_digest=binding,
        legacy_decision=legacy, canonical_decision=canonical, mismatch=mismatch,
        mismatch_explained=mismatch and explained, checkpoint=f"checkpoint-{index}",
        correlation_id=f"corr-{index:03d}", observed_at=(NOW + timedelta(minutes=index)).isoformat(),
        operation_mapping_digest=digest(["mapping", index]),
        mismatch_explanation=("legacy_and_canonical_lifecycle_disagree"
                              if mismatch and explained else None),
    )


@pytest.mark.asyncio
async def test_traversal_is_deterministic_regardless_of_source_order():
    forward = await BoundedParityRunner(
        observations=_Pages([_observation(index) for index in range(5)]),
        summaries=_Summary()).run(request=PARITY_REQUEST, context=CONTEXT,
                                  dependency_gates=GOOD_GATES)
    backward = await BoundedParityRunner(
        observations=_Pages(list(reversed([_observation(index) for index in range(5)]))),
        summaries=_Summary()).run(request=PARITY_REQUEST, context=CONTEXT,
                                  dependency_gates=GOOD_GATES)
    assert forward.report_digest == backward.report_digest
    assert forward.export() == backward.export()
    assert forward.observation_count == 5


@pytest.mark.asyncio
async def test_budget_exhaustion_yields_incomplete_partition_and_no_ready_status():
    runner = BoundedParityRunner(
        observations=_Pages([_observation(index) for index in range(9)]), summaries=_Summary())
    request = replace(PARITY_REQUEST, max_observations=4, page_size=2)
    report = await runner.run(request=request, context=CONTEXT, dependency_gates=GOOD_GATES)
    assert report.status == ParityStatus.INCOMPLETE.value
    assert report.checkpoint.complete_window is False
    assert report.checkpoint.cursor == "4"
    assert "partition_coverage_incomplete" in report.blocked_reasons
    assert report.unexplained_mismatch_count == 0


@pytest.mark.asyncio
async def test_checkpoint_resumes_the_exact_remainder_of_the_window():
    observations = [_observation(index) for index in range(6)]
    runner = BoundedParityRunner(observations=_Pages(observations), summaries=_Summary())
    first = await runner.run(request=replace(PARITY_REQUEST, max_observations=3, page_size=3),
                             context=CONTEXT, dependency_gates=GOOD_GATES)
    assert first.checkpoint.cursor == "3"
    resumed = await runner.run(
        request=replace(PARITY_REQUEST, max_observations=3, page_size=3,
                        start_cursor=first.checkpoint.cursor),
        context=CONTEXT, dependency_gates=GOOD_GATES)
    assert resumed.observation_count == 3
    assert resumed.checkpoint.complete_window is True
    assert first.report_digest != resumed.report_digest


@pytest.mark.asyncio
async def test_non_monotonic_cursor_and_duplicate_evidence_are_blocked():
    non_monotonic = BoundedParityRunner(
        observations=_Pages([_observation(index) for index in range(4)], regress_cursor=True),
        summaries=_Summary())
    report = await non_monotonic.run(request=replace(PARITY_REQUEST, max_observations=4,
                                                     page_size=2),
                                     context=CONTEXT, dependency_gates=GOOD_GATES)
    assert "observation_cursor_not_monotonic" in report.blocked_reasons
    duplicated = BoundedParityRunner(
        observations=_Pages([_observation(0), _observation(1)], duplicate=True),
        summaries=_Summary())
    report = await duplicated.run(request=PARITY_REQUEST, context=CONTEXT,
                                  dependency_gates=GOOD_GATES)
    assert "duplicate_observation_evidence" in report.blocked_reasons
    assert report.status == ParityStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_unexplained_mismatch_blocks_and_is_listed_with_digests_only():
    runner = BoundedParityRunner(
        observations=_Pages([_observation(0), _observation(1, mismatch=True, explained=False)]),
        summaries=_Summary())
    report = await runner.run(request=PARITY_REQUEST, context=CONTEXT,
                              dependency_gates=GOOD_GATES)
    assert report.status == ParityStatus.BLOCKED.value
    assert report.unexplained_mismatch_count == 1
    assert "unexplained_decision_mismatch" in report.blocked_reasons
    listed = report.unexplained[0]
    assert listed.source_id_digest.startswith("sha256:")
    assert listed.legacy_outcome == "allowed" and listed.canonical_outcome == "expired"
    exported = report.export()
    assert "grant-001" not in exported
    assert listed.source_id_digest in exported


@pytest.mark.asyncio
async def test_explained_mismatch_is_counted_but_does_not_block_review():
    runner = BoundedParityRunner(
        observations=_Pages([_observation(0), _observation(1, mismatch=True, explained=True)]),
        summaries=_Summary())
    report = await runner.run(request=PARITY_REQUEST, context=CONTEXT,
                              dependency_gates=GOOD_GATES)
    assert report.status == ParityStatus.READY_FOR_REVIEW.value
    assert report.mismatch_count == 1 and report.explained_mismatch_count == 1
    assert report.unexplained_mismatch_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("summary_kwargs,reason", [
    ({"unknown": 1}, "unknown_mappings_present"),
    ({"unresolved": 2}, "unresolved_positive_bindings_present"),
    ({"stale": 1}, "stale_generation_acceptances_present"),
    ({"audit": False}, "audit_evidence_incomplete"),
])
async def test_each_unreconciled_evidence_class_blocks_the_partition(summary_kwargs, reason):
    runner = BoundedParityRunner(observations=_Pages([_observation(0)]),
                                 summaries=_Summary(**summary_kwargs))
    report = await runner.run(request=PARITY_REQUEST, context=CONTEXT,
                              dependency_gates=GOOD_GATES)
    assert report.status == ParityStatus.BLOCKED.value
    assert reason in report.blocked_reasons


@pytest.mark.asyncio
async def test_missing_upstream_gates_block_even_with_complete_observations():
    runner = BoundedParityRunner(observations=_Pages([_observation(0)]), summaries=_Summary())
    report = await runner.run(request=PARITY_REQUEST, context=CONTEXT, dependency_gates=None)
    assert report.status == ParityStatus.BLOCKED.value
    assert "accepted_upstream_and_atomic_evidence_handoffs" in report.blocked_reasons


@pytest.mark.asyncio
async def test_foreign_partition_summary_is_never_accepted():
    other = ParityPartition(tenant_id="tenant-a", app_definition_id="definition-b")
    runner = BoundedParityRunner(observations=_Pages([_observation(0)]),
                                 summaries=_Summary(partition=other))
    report = await runner.run(request=PARITY_REQUEST, context=CONTEXT,
                              dependency_gates=GOOD_GATES)
    assert "partition_evidence_summary_mismatch" in report.blocked_reasons


@pytest.mark.asyncio
async def test_mixed_partition_page_is_rejected_outright():
    runner = BoundedParityRunner(
        observations=_Pages([_observation(0, tenant="tenant-b")]), summaries=_Summary())
    with pytest.raises(ValueError, match="outside the requested partition"):
        await runner.run(request=PARITY_REQUEST, context=CONTEXT, dependency_gates=GOOD_GATES)


@pytest.mark.asyncio
async def test_identical_run_replays_and_changed_evidence_conflicts():
    reports = InMemoryParityReportStore()
    pages = _Pages([_observation(index) for index in range(3)])
    runner = BoundedParityRunner(observations=pages, summaries=_Summary(), reports=reports)
    first, first_outcome = await runner.run_and_record(
        request=PARITY_REQUEST, context=CONTEXT, dependency_gates=GOOD_GATES)
    second, second_outcome = await runner.run_and_record(
        request=PARITY_REQUEST, context=CONTEXT, dependency_gates=GOOD_GATES)
    assert first_outcome is ReportOutcome.RECORDED
    assert second_outcome is ReportOutcome.REPLAY
    assert first.export() == second.export()
    changed = BoundedParityRunner(observations=pages, summaries=_Summary(snapshot="sha256:" + "b" * 64),
                                  reports=reports)
    _, third_outcome = await changed.run_and_record(
        request=PARITY_REQUEST, context=CONTEXT, dependency_gates=GOOD_GATES)
    assert third_outcome is ReportOutcome.CONFLICT


@pytest.mark.asyncio
async def test_partial_run_is_never_recorded_as_a_partition_conclusion():
    reports = InMemoryParityReportStore()
    runner = BoundedParityRunner(
        observations=_Pages([_observation(index) for index in range(5)]),
        summaries=_Summary(), reports=reports)
    report, outcome = await runner.run_and_record(
        request=replace(PARITY_REQUEST, max_observations=2, page_size=1),
        context=CONTEXT, dependency_gates=GOOD_GATES)
    assert outcome is None
    assert report.checkpoint.complete_window is False
    assert await reports.load(run_key=report.run_key) is None


def test_run_key_is_stable_for_the_same_partition_and_snapshot():
    summary = ParityEvidenceSummary(
        partition=PARITY_REQUEST.partition, source_snapshot_digest="sha256:" + "a" * 64,
        final_delta_checkpoint="final-delta-1", writer_epoch=7, unknown_mapping_count=0,
        unresolved_positive_bindings=0, stale_generation_acceptances=0, audit_complete=True)
    first = run_key_for(summary, PARITY_REQUEST)
    second = run_key_for(summary, replace(PARITY_REQUEST, max_observations=100))
    assert first == second
    narrowed = run_key_for(replace(summary, final_delta_checkpoint="final-delta-2"), PARITY_REQUEST)
    assert narrowed != first


def test_report_rejects_mutated_content_and_ownership_transfer():
    checkpoint = ParityCheckpoint("9", 9, True)
    report = ParityRunReport(
        schema="m06-parity-report-v1", run_key=digest("run"), request_digest=digest("request"),
        partition_digest=digest("partition"), tenant_id="tenant-a",
        app_definition_id="definition-a", observation_window_start=NOW.isoformat(),
        observation_window_end=(NOW + timedelta(hours=1)).isoformat(),
        source_snapshot_digest="sha256:" + "a" * 64, final_delta_checkpoint="final-delta-1",
        writer_epoch=7, observation_count=1, mismatch_count=0, explained_mismatch_count=0,
        unexplained_mismatch_count=0, unknown_mapping_count=0,
        unresolved_positive_bindings=0, stale_generation_acceptances=0, audit_complete=True,
        source_digest=digest("s"), canonical_digest=digest("c"), binding_digest=digest("b"),
        operation_mapping_digest=digest("m"), checkpoint=checkpoint, status="ready_for_review",
        blocked_reasons=(), unexplained=(),
    )
    assert report.report_digest == report.computed_digest()
    with pytest.raises(ValueError, match="digest does not match"):
        replace(report, observation_count=2, report_digest=report.report_digest)
    with pytest.raises(ValueError, match="cannot enable canonical positive writes"):
        replace(report, canonical_positive_writes=True)
    with pytest.raises(ValueError, match="cannot transfer decision ownership"):
        replace(report, decision_owner="canonical")


def test_unexplained_entry_is_digest_only():
    entry = UnexplainedMismatch(
        source_id_digest=digest("grant-a"), capability_key="storage.files",
        legacy_outcome="allowed", canonical_outcome="expired", observed_at=NOW.isoformat(),
        correlation_id="corr-a")
    assert entry.digest.startswith("sha256:")
    assert "grant-a" not in repr(entry.digest)


def test_window_and_budget_preconditions_are_enforced():
    with pytest.raises(ValueError, match="increasing"):
        ParityRunRequest(partition=PARITY_REQUEST.partition, window_start=NOW,
                         window_end=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        ParityRunRequest(partition=PARITY_REQUEST.partition,
                         window_start=NOW.replace(tzinfo=None), window_end=NOW)
    with pytest.raises(ValueError, match="max_observations"):
        replace(PARITY_REQUEST, max_observations=0)
    with pytest.raises(ValueError, match="page_size"):
        replace(PARITY_REQUEST, page_size=5000)
    with pytest.raises(ValueError, match="tenant"):
        ParityPartition(tenant_id=" ", app_definition_id="definition-a")


def test_source_digest_helper_is_stable_for_repeat_and_changed_source():
    assert source_payload_digest(SOURCE) == source_payload_digest(SOURCE)
    assert source_payload_digest(SOURCE) != source_payload_digest(
        replace(SOURCE, expires_at=None))
