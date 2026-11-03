"""Bounded, evidence-only partition parity aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .adapter import DependencyGates, ShadowObservation, digest, is_sha256


@dataclass(frozen=True, slots=True)
class PartitionParityReport:
    tenant_id: str
    app_definition_id: str
    observation_window_start: str
    observation_window_end: str
    source_snapshot_digest: str
    source_digest: str
    canonical_digest: str
    binding_digest: str
    final_delta_checkpoint: str
    writer_epoch: int | None
    observation_count: int
    mismatch_count: int
    unexplained_mismatch_count: int
    unresolved_positive_bindings: int
    unknown_mapping_count: int
    stale_generation_acceptances: int
    audit_complete: bool
    decision_owner: str
    canonical_read_owner: str
    status: str
    canonical_positive_writes: bool = False
    dependency_blockers: tuple[str, ...] = ()


def bounded_partition_parity(
    *, tenant_id: str,
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
) -> PartitionParityReport:
    if not 1 <= max_observations <= 10_000:
        raise ValueError("max_observations must be between 1 and 10000")
    if not tenant_id.strip() or not app_definition_id.strip():
        raise ValueError("partition identity is required")
    if not observation_window_start or not observation_window_end:
        raise ValueError("observation window is required")
    try:
        window_start = datetime.fromisoformat(observation_window_start)
        window_end = datetime.fromisoformat(observation_window_end)
    except ValueError as exc:
        raise ValueError("observation window must use timezone-aware ISO timestamps") from exc
    if (window_start.tzinfo is None or window_start.utcoffset() is None
            or window_end.tzinfo is None or window_end.utcoffset() is None):
        raise ValueError("observation window must use timezone-aware ISO timestamps")
    if window_start >= window_end:
        raise ValueError("observation window must be increasing")
    if any(value < 0 for value in (unresolved_positive_bindings, unknown_mapping_count,
                                    stale_generation_acceptances)):
        raise ValueError("evidence counts must be non-negative")
    if len(observations) > max_observations:
        raise ValueError("partition exceeds bounded observation limit")
    selected = tuple(row for row in observations
                     if row.tenant_id == tenant_id and row.app_definition_id == app_definition_id)
    if len(selected) != len(observations):
        raise ValueError("observations must belong to exactly one requested partition")
    for row in selected:
        if not is_sha256(row.source_digest):
            raise ValueError("observation source_digest must be sha256")
        if row.canonical_digest is not None and not is_sha256(row.canonical_digest):
            raise ValueError("observation canonical_digest must be sha256 when present")
        if row.binding_digest is not None and not is_sha256(row.binding_digest):
            raise ValueError("observation binding_digest must be sha256 when present")
        try:
            observed_at = datetime.fromisoformat(row.observed_at)
        except ValueError as exc:
            raise ValueError("observation timestamp must use timezone-aware ISO format") from exc
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observation timestamp must use timezone-aware ISO format")
        if not window_start <= observed_at < window_end:
            raise ValueError("observation lies outside the requested parity window")
        if not row.checkpoint.strip() or not row.correlation_id.strip():
            raise ValueError("observation checkpoint and correlation are required")
    ordered = tuple(sorted(selected, key=lambda row: (
        datetime.fromisoformat(row.observed_at), row.source_id, row.capability_key, row.correlation_id,
        row.source_digest, row.canonical_digest or "", row.binding_digest or "",
    )))
    source_digests = tuple(row.source_digest for row in ordered)
    canonical_digests = tuple(row.canonical_digest or "quarantine" for row in ordered)
    binding_digests = tuple(row.binding_digest or "unresolved" for row in ordered)
    mismatches = tuple(row for row in selected if row.mismatch)
    unexplained = tuple(row for row in mismatches if not row.mismatch_explained)
    dependency_blockers = (dependency_gates.missing_markers if dependency_gates is not None
                           else ("accepted_upstream_and_atomic_evidence_handoffs",))
    ready = (bool(ordered) and is_sha256(source_snapshot_digest)
             and bool(final_delta_checkpoint.strip()) and writer_epoch is not None and writer_epoch > 0
             and not unexplained and unresolved_positive_bindings == 0 and unknown_mapping_count == 0
             and stale_generation_acceptances == 0 and audit_complete is True
             and all(row.canonical_digest is not None and row.binding_digest is not None for row in ordered)
             and dependency_gates is not None and dependency_gates.positive_projection_ready)
    return PartitionParityReport(
        tenant_id, app_definition_id, observation_window_start, observation_window_end,
        source_snapshot_digest, digest(source_digests), digest(canonical_digests), digest(binding_digests),
        final_delta_checkpoint, writer_epoch, len(ordered), len(mismatches), len(unexplained),
        unresolved_positive_bindings, unknown_mapping_count, stale_generation_acceptances, audit_complete,
        "legacy", "legacy", "ready_for_review" if ready else "blocked", False,
        dependency_blockers,
    )
