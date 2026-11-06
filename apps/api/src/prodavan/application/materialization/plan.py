"""Factories for immutable persisted materialization plan entries."""

from __future__ import annotations

from prodavan.application.materialization.identity import build_digest, build_idempotency_key
from prodavan.domain.materialization.types import (
    MaterializationOperation,
    MaterializationPlanEntry,
    SourceProvenance,
)


def build_plan_entry(
    *,
    entry_id: str,
    project_id: str,
    operation: MaterializationOperation,
    provenance: SourceProvenance,
    desired_generation: int,
) -> MaterializationPlanEntry:
    """Create a planned entry with identities derived from desired state."""
    digest = build_digest(
        project_id=project_id,
        operation=operation,
        provenance=provenance,
        desired_generation=desired_generation,
    )
    return MaterializationPlanEntry(
        entry_id=entry_id,
        project_id=project_id,
        operation=operation,
        provenance=provenance,
        digest=digest,
        idempotency_key=build_idempotency_key(
            project_id=project_id,
            target=operation.target,
            digest=digest,
        ),
        desired_generation=desired_generation,
    )
