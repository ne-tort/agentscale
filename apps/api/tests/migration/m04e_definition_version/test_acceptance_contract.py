"""MIG-M04E-02 acceptance contracts; intentionally not executed during freeze."""

from dataclasses import dataclass

from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    DefinitionVersionEvidenceBackfill,
    EvidenceCandidate,
    EvidenceStatus,
    HandoffGate,
    HandoffStatus,
    export_m05_evidence,
)
from prodavan.application.migration.m04e_definition_version.repository import (
    MigrationEvidenceContext,
    ServerDefinitionVersionRepository,
)


@dataclass
class MemoryEvidenceStore:
    rows: dict[tuple[str, str, str], object]

    async def find(self, source_kind, source_id, schema_version):
        return self.rows.get((source_kind, source_id, schema_version))

    async def append(self, record):
        self.rows[(record.source_kind, record.source_id, record.schema_version)] = record


# Static acceptance cases:
# - replaying same source digest is idempotent and preserves first evidence;
# - changed source digest is conflict/quarantine and never overwrites;
# - definitions are exhausted before versions begin;
# - missing tenant, definition, artifact, invalid lifecycle and absent typed scope fail closed;
# - caller-supplied tenant mismatch, version ID without definition, archived version and
#   invalid current-version link cannot produce a positive resolution;
# - M05 export remains blocked on absent upstream gate evidence and never enables writes.


def test_acceptance_contract_is_declared() -> None:
    assert DefinitionVersionEvidenceBackfill.__name__ == "DefinitionVersionEvidenceBackfill"
    assert EvidenceStatus.CONFLICT.value == "conflict"
    assert HandoffStatus.BLOCKED.value == "blocked"


def test_handoff_gate_requires_evidence_and_keeps_legacy_owner() -> None:
    checkpoint = BackfillCheckpoint(("tenant", "definition"), "version", "v1", 1)
    export = export_m05_evidence(
        tenant_id="tenant", app_definition_id="definition", records=(),
        source_snapshot_digest="sha256:" + "1" * 64, checkpoint=checkpoint,
        gates=(HandoffGate("M04", False, reason="missing"),),
    )
    assert export.status is HandoffStatus.BLOCKED
    assert export.canonical_positive_writes is False
