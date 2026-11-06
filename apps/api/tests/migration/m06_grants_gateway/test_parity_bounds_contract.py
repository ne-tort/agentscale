"""Bounded parity traversal acceptance: page budget, overshoot, narrowing, stable JSON.

These cases pin the traversal itself rather than the parity verdict: a source that
ignores the page limit, never finishes its window, or a partition narrowed to a
version/instance the shadow evidence cannot prove must all block instead of silently
reporting a wider or shorter window than the one requested.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from prodavan.application.migration.m06_grants_gateway.adapter import (
    ShadowDecision,
    ShadowObservation,
    binding_digest,
    digest,
)
from prodavan.application.migration.m06_grants_gateway.parity_runner import (
    BoundedParityRunner,
    ObservationBatch,
    ParityEvidenceSummary,
    ParityPartition,
    ParityStatus,
)

from .acceptance_fixtures import CONTEXT, GOOD_GATES, IDENTITY, NOW, PARITY_REQUEST


def _observation(index: int) -> ShadowObservation:
    binding = binding_digest(IDENTITY)
    return ShadowObservation(
        tenant_id="tenant-a", app_definition_id="definition-a", source_id=f"grant-{index:03d}",
        capability_key="storage.files", source_digest=digest(["source", index]),
        canonical_digest=digest(["canonical", index]), binding_digest=binding,
        legacy_decision=ShadowDecision(True, "allowed", binding),
        canonical_decision=ShadowDecision(True, "allowed", binding), mismatch=False,
        mismatch_explained=False, checkpoint=f"checkpoint-{index}",
        correlation_id=f"corr-{index:03d}", observed_at=(NOW + timedelta(minutes=index)).isoformat(),
        operation_mapping_digest=digest(["mapping", index]),
    )


class _Pages:
    """Cursor source with switchable failure modes; counts every read."""

    def __init__(self, observations, *, overshoot: bool = False, endless: bool = False):
        self._observations = observations
        self._overshoot = overshoot
        self._endless = endless
        self.reads = 0

    async def load_observations(self, *, context, tenant_id, app_definition_id, window_start,
                                window_end, after_cursor=None, limit=500):
        self.reads += 1
        if self._endless:
            return ObservationBatch((), str(self.reads), True)
        start = 0 if after_cursor is None else int(after_cursor)
        if self._overshoot:
            page = self._observations[:limit + 1]
            return ObservationBatch(tuple(page), str(len(page)), False)
        page = self._observations[start:start + limit]
        return ObservationBatch(tuple(page), str(start + len(page)),
                                start + limit < len(self._observations))


class _Summary:
    def __init__(self, *, partition=None, snapshot="sha256:" + "a" * 64):
        self._partition = partition
        self._snapshot = snapshot

    async def load_summary(self, *, context, partition, window_start, window_end):
        return ParityEvidenceSummary(
            partition=self._partition or partition, source_snapshot_digest=self._snapshot,
            final_delta_checkpoint="final-delta-1", writer_epoch=7, unknown_mapping_count=0,
            unresolved_positive_bindings=0, stale_generation_acceptances=0, audit_complete=True)


class _Unreadable:
    """Any read at all is a contract violation for the case under test."""

    async def load_observations(self, **kwargs):
        raise AssertionError("no observation read may happen for this request")

    async def load_summary(self, **kwargs):
        raise AssertionError("no summary read may happen for this request")


@pytest.mark.asyncio
@pytest.mark.parametrize("partition", [
    ParityPartition(tenant_id="tenant-a", app_definition_id="definition-a",
                    plugin_instance_id="instance-a"),
    ParityPartition(tenant_id="tenant-a", app_definition_id="definition-a",
                    app_version_id="version-a"),
])
async def test_narrowed_partition_is_refused_before_any_read(partition):
    runner = BoundedParityRunner(observations=_Unreadable(), summaries=_Unreadable())
    report = await runner.run(request=replace(PARITY_REQUEST, partition=partition),
                              context=CONTEXT, dependency_gates=GOOD_GATES)
    assert report.status == ParityStatus.BLOCKED.value
    assert report.blocked_reasons == ("partition_narrowing_unsupported",)
    assert report.observation_count == 0
    assert report.checkpoint.complete_window is False
    assert report.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_page_larger_than_the_requested_limit_is_blocked_not_truncated():
    runner = BoundedParityRunner(
        observations=_Pages([_observation(index) for index in range(6)], overshoot=True),
        summaries=_Summary())
    report = await runner.run(request=replace(PARITY_REQUEST, max_observations=4, page_size=2),
                              context=CONTEXT, dependency_gates=GOOD_GATES)
    assert report.status == ParityStatus.BLOCKED.value
    assert "observation_page_exceeds_page_size" in report.blocked_reasons
    assert report.observation_count == 0


@pytest.mark.asyncio
async def test_endless_truncated_pagination_stays_within_the_page_budget():
    pages = _Pages([], endless=True)
    runner = BoundedParityRunner(observations=pages, summaries=_Summary())
    request = replace(PARITY_REQUEST, max_observations=4, page_size=2)
    report = await runner.run(request=request, context=CONTEXT, dependency_gates=GOOD_GATES)
    assert report.status == ParityStatus.BLOCKED.value
    assert "observation_page_budget_exhausted" in report.blocked_reasons
    assert pages.reads <= request.max_observations + 2
    assert report.checkpoint.complete_window is False


@pytest.mark.asyncio
async def test_identical_evidence_exports_byte_identical_json():
    first = await BoundedParityRunner(
        observations=_Pages([_observation(index) for index in range(4)]),
        summaries=_Summary()).run(request=PARITY_REQUEST, context=CONTEXT,
                                  dependency_gates=GOOD_GATES)
    second = await BoundedParityRunner(
        observations=_Pages([_observation(index) for index in range(4)]),
        summaries=_Summary()).run(request=PARITY_REQUEST, context=CONTEXT,
                                  dependency_gates=GOOD_GATES)
    assert first.export() == second.export()
    assert first.report_digest == second.report_digest
    decoded = json.loads(first.export())
    assert decoded["report_digest"] == first.report_digest
    assert decoded["report_digest"] == first.computed_digest()
    assert decoded["checkpoint"]["digest"] == first.checkpoint.digest
    assert "grant-000" not in first.export()


@pytest.mark.asyncio
async def test_blocked_reports_are_self_consistent_and_never_ready():
    runner = BoundedParityRunner(
        observations=_Pages([_observation(index) for index in range(3)], overshoot=True),
        summaries=_Summary())
    report = await runner.run(request=replace(PARITY_REQUEST, max_observations=2, page_size=2),
                              context=CONTEXT, dependency_gates=GOOD_GATES)
    assert report.status != ParityStatus.READY_FOR_REVIEW.value
    assert report.report_digest == report.computed_digest()
    assert report.blocked_reasons == tuple(sorted(report.blocked_reasons))
    assert report.unexplained == ()
