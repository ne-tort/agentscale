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


# ---------------------------------------------------------------------------
# M04-02 accepted scope evidence (m04_scope_promotion_evidence)
# ---------------------------------------------------------------------------

M04_SCOPE_ROW_ID = "m04scope-evidence-1"
M04_SCOPE_REVISION_ID = "m04scope-revision-1"
M04_SCOPE_REGISTRY_ID = "scope-registry-evidence-1"


def scope_evidence_row(**overrides: Any) -> Any:
    """A complete accepted M04-02 row in ORM *state*, never persisted.

    ``canonical_digest`` defaults to :data:`M04_SCOPE_DIGEST`, the same digest the M05
    promotion record cites as its ``M04_scope`` gate, so a default row is the row that
    attests the default partition. Every override is a named negative case.
    """
    from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
        M04ScopePromotionEvidenceRow,
    )

    values: dict[str, Any] = {
        "id": M04_SCOPE_ROW_ID,
        "tenant_id": TENANT,
        "scope_kind": "tenant",
        "scope_id": TENANT,
        "cabinet_id": None,
        "project_id": None,
        "scope_registry_id": M04_SCOPE_REGISTRY_ID,
        "revision_id": M04_SCOPE_REVISION_ID,
        "previous_revision_id": None,
        "outcome": "ready",
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "canonical_digest": M04_SCOPE_DIGEST,
        "canonical_schema_version": "scope-registry.v1",
        "checkpoint_digest": M04E_GATE_DIGEST,
        "gate_evidence_digest": M05_RUNTIME_DIGEST,
        "rls_policy_digest": M06_SCHEMA_DIGEST,
        "rls_security_review_id": "rls-review-evidence",
        "rls_database_role": "prodavan_migration",
        "gate_evidence": {"gates": [], "redaction": "identifiers_digests_counts_only"},
        "reason_code": None,
        "writer_generation": 3,
        "correlation_id": "corr-evidence",
        "actor_id": "actor-evidence",
        "canonical_positive_writes": False,
    }
    values.update(overrides)
    return M04ScopePromotionEvidenceRow(**values)


class RecordingStatementSession:
    """Minimal read-only session stub: records the statement, returns one preset row.

    Used only to inspect the exact SQL a single loader emits (filters and ordering), not
    to emulate a database. Foreign-partition invisibility is proved by the compiled
    predicate, not by the stub's answer.
    """

    def __init__(self, row: Any = None) -> None:
        self._row = row
        self.statements: list[Any] = []

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        return self._row

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(statement)
        raise AssertionError("this stub only answers scalar() loads")

    def compiled(self, index: int = -1) -> str:
        return str(self.statements[index].compile(
            dialect=_postgresql_dialect(), compile_kwargs={"literal_binds": False}
        ))


def _postgresql_dialect() -> Any:
    from sqlalchemy.dialects import postgresql

    return postgresql.dialect()
