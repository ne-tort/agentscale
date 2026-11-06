"""Fixtures for the persisted materialization contract."""

from prodavan.domain.materialization.types import (
    MaterializationOperation,
    MaterializationPlanEntry,
    OperationKind,
    SourceProvenance,
)


MATERIALIZATION_SOURCE = SourceProvenance(
    source_type="module_row",
    source_id="row_prompts_001",
    source_generation=7,
    source_path="prompts/AGENTS.md",
    source_revision="cabinet-rev-7",
    metadata={"module_id": "mod_prompts", "table_slug": "prompt_paths"},
)


def make_materialization_entry(*, kind: str = "prompt", generation: int = 7) -> MaterializationPlanEntry:
    operation = MaterializationOperation(
        kind=OperationKind(kind),
        target="workspace/AGENTS.md",
        payload={"body": "Use explicit tool scopes.", "priority": 100},
    )
    return MaterializationPlanEntry(
        entry_id="mat-entry-001",
        project_id="project-001",
        operation=operation,
        provenance=MATERIALIZATION_SOURCE,
        digest=f"digest-generation-{generation}",
        idempotency_key=f"idem-generation-{generation}",
        desired_generation=generation,
    )
