"""Fixture-free helpers for the evidence-port acceptance artefacts.

Nothing here touches a database: the acceptance artefacts in this package are static
and pure units of behaviour, per the migration-stage verification freeze. Three helpers
are worth explaining:

``ExplodingSessionFactory``
    A session factory that raises on first use. Passing it proves that a refusal happens
    *before* any SQL is issued (foreign tenant, missing generation, missing request key),
    which is the property that makes the ports fail-closed rather than fail-late.

``marker_row_view`` / ``promotion_row_view``
    Wrap the column mapping produced by a mapper into the read-only row view the decoders
    consume, so the encode/decode round trip is exercised without a live row.

``MODULE_PATH``
    The single production module under acceptance, so the source-level checks cannot
    drift onto a copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from prodavan.application.migration.evidence_ports import _RowView
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    BackfillContext,
    BackfillOutcome,
    BackfillRecord,
    EvidenceStatus,
    HandoffGate,
    HandoffStatus as M04EHandoffStatus,
    M05HandoffMarker,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    HandoffStatus as M05HandoffStatus,
    ImportCheckpoint,
    PromotionGateEvidence,
    PromotionRecord,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "prodavan"
    / "application"
    / "migration"
    / "evidence_ports.py"
)

TENANT = "tenant-evidence"
DEFINITION = "definition-evidence"
VERSION = "version-evidence"
INSTANCE = "instance-evidence"
SNAPSHOT_ID = "snapshot-evidence-1"
SOURCE_SCHEMA = "legacy-definition-version.v1"
CANONICAL_SCHEMA = "m04e.v1"
NOW_TEXT = "2026-11-04T00:00:00+00:00"


def digest_of(byte: str) -> str:
    return "sha256:" + byte * 64


SNAPSHOT_DIGEST = digest_of("a")
CANONICAL_DIGEST = digest_of("b")
M04_SCOPE_DIGEST = digest_of("c")
M04E_GATE_DIGEST = digest_of("d")
M05_RUNTIME_DIGEST = digest_of("e")
M06_SCHEMA_DIGEST = digest_of("f")


class ExplodingSessionFactory:
    """A session factory that fails the test if a port opens a transaction."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("the port opened a session before refusing the request")


def backfill_context(**overrides: Any) -> BackfillContext:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "app_definition_id": DEFINITION,
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "source_schema_version": SOURCE_SCHEMA,
        "canonical_schema_version": CANONICAL_SCHEMA,
        "transaction_id": "tx-evidence",
        "correlation_id": "corr-evidence",
        "actor": "migration-fixture",
        "decision_owner": "coordinator-fixture",
        "writer_generation": 3,
    }
    values.update(overrides)
    return BackfillContext(**values)


def checkpoint(*, sequence: int = 0, after: str | None = None, **overrides: Any) -> BackfillCheckpoint:
    values: dict[str, Any] = {
        "partition": (TENANT, DEFINITION),
        "phase": "version",
        "after_source_id": after,
        "sequence": sequence,
        "source_snapshot_id": SNAPSHOT_ID,
        "source_schema_version": SOURCE_SCHEMA,
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "canonical_schema_version": CANONICAL_SCHEMA,
        "writer_generation": 3,
    }
    values.update(overrides)
    return BackfillCheckpoint(**values)


def handoff_gates(*, passed: bool = True) -> tuple[HandoffGate, ...]:
    return (
        HandoffGate("MIG-M04-01", passed, M04_SCOPE_DIGEST),
        HandoffGate("MIG-M04-02", passed, M04_SCOPE_DIGEST),
        HandoffGate("MIG-M04E-01", passed, CANONICAL_DIGEST),
        HandoffGate("active_typed_parent_scope", passed, M04_SCOPE_DIGEST),
        HandoffGate("same_definition_version", passed, M04E_GATE_DIGEST),
        HandoffGate("immutable_artifact", passed, M05_RUNTIME_DIGEST),
    )


def marker(**overrides: Any) -> M05HandoffMarker:
    values: dict[str, Any] = {
        "record_id": "sha256:" + "1" * 64,
        "task_id": "MIG-M04E-02",
        "status": M04EHandoffStatus.READY,
        "partition": (TENANT, DEFINITION),
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "canonical_digest": CANONICAL_DIGEST,
        "checkpoint": checkpoint(sequence=12, after="version-12", phase_complete=True),
        "accepted_count": 3,
        "quarantined_count": 0,
        "gate_evidence": handoff_gates(),
        "actor": "migration-worker",
        "recorded_at": NOW_TEXT,
        "decision_owner": "cutover-owner",
        "writer_generation": 3,
        "correlation_id": "corr-evidence",
        "canonical_positive_writes": False,
    }
    values.update(overrides)
    return M05HandoffMarker(**values)


def import_checkpoint(*, sequence: int = 3, after: str = "instance-source-3") -> ImportCheckpoint:
    return ImportCheckpoint(
        partition=(TENANT, DEFINITION),
        source_snapshot_id=SNAPSHOT_ID,
        source_schema_version=SOURCE_SCHEMA,
        source_snapshot_digest=SNAPSHOT_DIGEST,
        after_source_id=after,
        sequence=sequence,
    )


def promotion_gates(*, passed: bool = True) -> tuple[PromotionGateEvidence, ...]:
    return (
        PromotionGateEvidence("M04_scope", passed, M04_SCOPE_DIGEST),
        PromotionGateEvidence("M04E_definition_version", passed, CANONICAL_DIGEST),
        PromotionGateEvidence("M05_runtime", passed, M05_RUNTIME_DIGEST),
        PromotionGateEvidence("final_delta_checkpoint", passed, SNAPSHOT_DIGEST),
        PromotionGateEvidence("legacy_writer_fence", passed, M04E_GATE_DIGEST),
        PromotionGateEvidence("zero_unknown_and_unresolved", passed, M04E_GATE_DIGEST),
        PromotionGateEvidence("parity", passed, M05_RUNTIME_DIGEST),
        PromotionGateEvidence("final_delta_and_instance_evidence", passed, CANONICAL_DIGEST),
    )


def promotion_record(**overrides: Any) -> PromotionRecord:
    values: dict[str, Any] = {
        "record_id": "m05promo-evidence-1",
        "task_id": "MIG-M05-02",
        "status": M05HandoffStatus.READY,
        "partition": (TENANT, DEFINITION),
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "canonical_digest": CANONICAL_DIGEST,
        "checkpoint": import_checkpoint(),
        "imported_count": 2,
        "replay_count": 0,
        "quarantined_count": 0,
        "conflict_count": 0,
        "gate_evidence": promotion_gates(),
        "actor": "migration-worker",
        "recorded_at": NOW_TEXT,
        "decision_owner": "cutover-owner",
        "writer_generation": 3,
        "correlation_id": "corr-evidence",
        "m06_handoff": M05HandoffStatus.READY,
        "retry_evidence_digest": M04E_GATE_DIGEST,
        "final_delta_checkpoint_digest": SNAPSHOT_DIGEST,
        "parity_evidence_digest": M05_RUNTIME_DIGEST,
        "legacy_writer_fenced": True,
    }
    values.update(overrides)
    return PromotionRecord(**values)


def backfill_record(**overrides: Any) -> BackfillRecord:
    values: dict[str, Any] = {
        "source_kind": "legacy_app_version",
        "source_id": VERSION,
        "schema_version": SOURCE_SCHEMA,
        "source_digest": digest_of("9"),
        "canonical_digest": CANONICAL_DIGEST,
        "canonical_identity": (TENANT, DEFINITION, "1.0.0"),
        "checkpoint": checkpoint(sequence=4, after=VERSION),
        "status": EvidenceStatus.ACCEPTED,
        "reason_code": None,
        "correlation_id": "corr-evidence",
        "tenant_id": TENANT,
        "app_definition_id": DEFINITION,
        "phase": "version",
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "proof_evidence": (
            ("owner", M04_SCOPE_DIGEST),
            ("active_scope", M04_SCOPE_DIGEST),
        ),
        "artifact_uri": "content://tenant-evidence/definition-evidence/p/A=",
        "artifact_digest": CANONICAL_DIGEST,
        "verified_artifact_digest": CANONICAL_DIGEST,
        "transaction_id": "tx-evidence",
        "writer_generation": 3,
        "outcome": BackfillOutcome.ACCEPTED,
    }
    values.update(overrides)
    return BackfillRecord(**values)


def marker_row_view(values: Mapping[str, Any]) -> _RowView:
    return _RowView(
        id=values["id"],
        tenant_id=values["tenant_id"],
        definition_id=values["definition_id"],
        revision_id=values["revision_id"],
        outcome=values["outcome"],
        source_snapshot_id=values["source_snapshot_id"],
        source_digest=values["source_digest"],
        target_digest=values["target_digest"],
        gate_evidence=values["gate_evidence"],
    )


def promotion_row_view(values: Mapping[str, Any]) -> _RowView:
    return _RowView(
        id=values["id"],
        tenant_id=values["tenant_id"],
        definition_id=values["definition_id"],
        revision_id=values["revision_id"],
        outcome=values["outcome"],
        source_snapshot_id=values["source_snapshot_id"],
        source_digest=values["source_digest"],
        target_digest=values["target_digest"],
        gate_evidence=values["gate_evidence"],
    )
