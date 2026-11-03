"""Bounded, evidence-only partition parity aggregation.

Retained as the synchronous, already-materialized entry point: it turns a frozen
set of shadow observations plus trusted evidence counts into the same stable report
schema that :mod:`parity_runner` produces, without reading or writing any store.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from .adapter import DependencyGates, ShadowObservation
from .parity_runner import (
    ObservationBatch,
    ParityCheckpoint,
    ParityEvidenceSummary,
    ParityPartition,
    ParityRunReport,
    ParityRunRequest,
    ParityStatus,
    ReportOutcome,
    _build_report,
    run_key_for,
)


def bounded_partition_parity(
    *,
    tenant_id: str,
    app_definition_id: str,
    observations: Sequence[ShadowObservation],
    source_snapshot_digest: str,
    final_delta_checkpoint: str,
    observation_window_start: str,
    observation_window_end: str,
    writer_epoch: int | None,
    unresolved_positive_bindings: int,
    unknown_mapping_count: int,
    stale_generation_acceptances: int,
    audit_complete: bool,
    max_observations: int = 1000,
    dependency_gates: DependencyGates | None = None,
) -> ParityRunReport:
    """Aggregate already-collected evidence; never reads a store or decides access."""
    if not 1 <= max_observations <= 10_000:
        raise ValueError("max_observations must be between 1 and 10000")
    window_start = _window(observation_window_start)
    window_end = _window(observation_window_end)
    if window_start >= window_end:
        raise ValueError("observation window must be increasing")
    if writer_epoch is None or isinstance(writer_epoch, bool) or writer_epoch <= 0:
        raise ValueError("writer epoch must be positive")
    if not final_delta_checkpoint.strip():
        raise ValueError("final delta checkpoint is required")
    if len(observations) > max_observations:
        raise ValueError("partition exceeds bounded observation limit")
    partition = ParityPartition(tenant_id=tenant_id, app_definition_id=app_definition_id)
    selected = tuple(observations)
    for row in selected:
        if row.tenant_id != tenant_id or row.app_definition_id != app_definition_id:
            raise ValueError("observations must belong to exactly one requested partition")
    summary = ParityEvidenceSummary(
        partition=partition,
        source_snapshot_digest=source_snapshot_digest,
        final_delta_checkpoint=final_delta_checkpoint,
        writer_epoch=writer_epoch,
        unknown_mapping_count=unknown_mapping_count,
        unresolved_positive_bindings=unresolved_positive_bindings,
        stale_generation_acceptances=stale_generation_acceptances,
        audit_complete=audit_complete,
    )
    request = ParityRunRequest(
        partition=partition,
        window_start=window_start,
        window_end=window_end,
        max_observations=max_observations,
    )
    checkpoint = ParityCheckpoint(None, len(selected), bool(selected))
    return _build_report(
        request=request,
        summary=summary,
        observations=selected,
        checkpoint=checkpoint,
        dependency_gates=dependency_gates,
    )


def _window(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("observation window is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("observation window must use timezone-aware ISO timestamps") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observation window must use timezone-aware ISO timestamps")
    return parsed


__all__ = [
    "ObservationBatch",
    "ParityCheckpoint",
    "ParityEvidenceSummary",
    "ParityPartition",
    "ParityRunReport",
    "ParityRunRequest",
    "ParityStatus",
    "ReportOutcome",
    "bounded_partition_parity",
    "run_key_for",
]
