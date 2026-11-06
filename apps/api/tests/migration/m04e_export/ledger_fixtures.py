"""Ledger fixtures for the M04E -> M05 export reader (``MIG-M04E-EXPORT-01``).

Two rules, the same ones ``tests/migration/m04e_ports`` states:

**1. The ledger is written by the production store, never by hand.**  Every fixture that
claims "this partition has a committed page" runs
:meth:`SqlM04EEvidenceStore.classify_and_commit_page` over the SQLite mirror of the four
registered tables.  A hand-written row set would let a reader pass against a shape the
store never produces.

**2. The handoff marker is encoded by the production encoder.**  The SQLite mirror has no
transaction-local GUCs, so ``SqlM05HandoffEvidenceStore`` cannot append there; the marker
row is therefore inserted straight from
:func:`~prodavan.application.migration.evidence_ports.handoff_marker_row_values`, which is
the same function the real append calls.  The envelope and the six gate columns are the
production ones.  The live PostgreSQL module uses ``persist_m05_handoff`` itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from prodavan.application.migration.evidence_ports import (
    backfill_checkpoint_digest,
    handoff_marker_row_values,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    BackfillRecord,
    CandidateDecision,
    CandidateProof,
    EvidenceCandidate,
    EvidenceStatus,
    HandoffGate,
    M05EvidenceExport,
    M05HandoffMarker,
    UpstreamGateEvidence,
    export_m05_evidence,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    HandoffStatus as M04EHandoffStatus,
)
from prodavan.application.migration.m04e_ports import (
    SqlM04EEvidenceStore,
    definition_candidate,
    version_candidate,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from tests.migration.m04e_ports.acceptance_fixtures import (  # noqa: F401 - re-exported
    APP_ID,
    CORRELATION_ID,
    DEFINITION_ID,
    MANIFEST_VERSION,
    OTHER_DEFINITION_ID,
    OTHER_TENANT,
    SHA_A,
    SHA_B,
    SHA_C,
    SHA_D,
    SNAPSHOT_DIGEST,
    SNAPSHOT_ID,
    SOURCE_SCHEMA,
    TENANT,
    context,
    definition_row,
    version_row,
)
from tests.migration.m04e_ports.sqlite_ledger import (
    AUDIT_TABLE,
    CHECKPOINT_TABLE,
    MAPPING_TABLE,
    QUARANTINE_TABLE,
    LedgerMirror,
    mirror_table,
)

PROMOTION_TABLE = M04EDefinitionVersionPromotionEvidenceRow.__tablename__

#: A second version id, so a page can hold two version rows when a case needs one.
VERSION_TWO = "aver_0002"

#: One transaction per committed page.  The page's transaction id is *not* constant in
#: production (``BackfillContext.transaction_id`` is the writer's own), and the reader
#: reports it per page, so the fixture commits each page under its own token.
DEFINITION_TRANSACTION = "pg:4711"
VERSION_TRANSACTION = "pg:4712"

#: The alt record id a second handoff marker of the same partition needs: the table's key
#: is ``(tenant_id, definition_id, revision_id)``, so two markers cannot share one id.
SECOND_MARKER_RECORD_ID = "m04ehandoff_" + "9" * 24

#: The six gates every READY handoff must carry (``export_m05_evidence``'s required set
#: minus the release switch's own ``upstream_owner_scope_gate``).
HANDOFF_GATE_NAMES = (
    "MIG-M04-01",
    "MIG-M04-02",
    "MIG-M04E-01",
    "active_typed_parent_scope",
    "same_definition_version",
    "immutable_artifact",
)


# ---------------------------------------------------------------------------
# Mirror
# ---------------------------------------------------------------------------


class ExportLedgerMirror(LedgerMirror):
    """The four ledger tables **plus** the promotion table the handoff marker lives in.

    The reader needs both: rows come from the ledger, gates come from the stored marker.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tables[PROMOTION_TABLE] = mirror_table(
            M04EDefinitionVersionPromotionEvidenceRow, self.metadata
        )

    async def delete(self, table_name: str) -> None:
        """Empty one mirrored table (used to model a ledger that never got a head)."""

        table = self.tables[table_name]
        async with self.engine.begin() as connection:
            await connection.execute(table.delete())

    async def reset(self) -> None:
        """Empty every mirrored table, so one test can model two independent scenarios.

        The unique keys the mirror copies are the ledger's real idempotency contract
        (one head per partition/phase, one first mapping per source identity, one marker
        per revision), so a second scenario that models *another* handoff must start from
        an empty ledger rather than collide with the first.
        """

        for table_name in self.tables:
            await self.delete(table_name)


def store_for(ledger: LedgerMirror) -> SqlM04EEvidenceStore:
    """The production store bound to the mirror, with the PostgreSQL-only guard relaxed."""

    return SqlM04EEvidenceStore(
        session_factory=ledger.session_factory, allow_non_postgresql=True,
    )


# ---------------------------------------------------------------------------
# Classifications (the shapes ``run_page`` really produces)
# ---------------------------------------------------------------------------


def upstream_gates(
    *, transaction_id: str | None = None, **overrides: Any,
) -> UpstreamGateEvidence:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "app_definition_id": DEFINITION_ID,
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "transaction_id": (
            transaction_id if transaction_id is not None else context().transaction_id
        ),
        "correlation_id": CORRELATION_ID,
        "m04_gate_digest": SHA_A,
        "m04e_gate_digest": SHA_B,
        "active_scope_digest": SHA_C,
    }
    values.update(overrides)
    return UpstreamGateEvidence(**values)


def accepted(
    candidate: EvidenceCandidate,
    *,
    verified: str | None = None,
    transaction_id: str | None = None,
) -> CandidateDecision:
    """An accepted classification carrying every proof digest the contract requires.

    ``transaction_id`` is the page's own: every page commits under its own token, so a
    fixture that pins all pages to one constant would make "each page reports its own
    transaction" unprovable (`SqlM04EEvidenceStore._classify` stamps
    ``context.transaction_id`` onto both the record and the audit event).
    """

    proof = CandidateProof(
        tenant_id=TENANT,
        app_definition_id=DEFINITION_ID,
        source_kind=candidate.source_kind,
        source_id=candidate.source_id,
        source_digest=candidate.source_digest,
        transaction_id=transaction_id if transaction_id is not None else context().transaction_id,
        correlation_id=CORRELATION_ID,
        owner_proof_digest=SHA_C,
        active_scope_proof_digest=upstream_gates().active_scope_digest,
        canonical_definition_id=DEFINITION_ID,
        app_id=APP_ID,
        app_version_id=candidate.source_id,
        manifest_version=str(candidate.payload.get("manifest_version")),
        artifact_uri=str(candidate.payload.get("artifact_uri")),
        same_definition_proof_digest=SHA_D,
        artifact_digest=str(candidate.payload.get("artifact_digest")),
        artifact_immutability_proof_digest=SHA_A,
    )
    payload_artifact = candidate.payload.get("artifact_digest")
    return CandidateDecision(
        candidate=candidate,
        status=EvidenceStatus.ACCEPTED,
        reason_code=None,
        proof=proof,
        verified_artifact_digest=(
            verified
            if verified is not None
            else (str(payload_artifact) if candidate.phase == "version" else None)
        ),
    )


def quarantined(candidate: EvidenceCandidate, *, reason: str = "source_lifecycle_unproven"):
    return CandidateDecision(
        candidate=candidate,
        status=EvidenceStatus.QUARANTINED,
        reason_code=reason,
        proof=None,
        verified_artifact_digest=(
            str(candidate.payload.get("artifact_digest"))
            if candidate.phase == "version" else None
        ),
    )


def version_two_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": VERSION_TWO,
        "manifest_version": "1.0.1",
        "artifact_digest": SHA_D,
        "artifact_uri": f"content://{TENANT}/{DEFINITION_ID}/{VERSION_TWO}",
        "manifest": {
            "protocol": "1.0",
            "appId": APP_ID,
            "version": "1.0.1",
            "displayName": "demo",
            "entrypoints": {"plugin": {}},
            "requestedCapabilities": [],
        },
    }
    values.update(overrides)
    return version_row(**values)


def fresh_checkpoint(*, phase: str, after: str | None = None, sequence: int = 0,
                     complete: bool = False) -> BackfillCheckpoint:
    ctx = context()
    return BackfillCheckpoint(
        partition=(ctx.tenant_id, ctx.app_definition_id),
        phase=phase,
        after_source_id=after,
        sequence=sequence,
        source_snapshot_id=ctx.source_snapshot_id,
        source_schema_version=ctx.source_schema_version,
        source_snapshot_digest=ctx.source_snapshot_digest,
        canonical_schema_version=ctx.canonical_schema_version,
        writer_generation=ctx.writer_generation,
        phase_complete=complete,
    )


# ---------------------------------------------------------------------------
# A complete ledger: definition page, then version page
# ---------------------------------------------------------------------------


async def commit_definition_page(
    ledger: LedgerMirror,
    *,
    decision: CandidateDecision | None = None,
    transaction_id: str = DEFINITION_TRANSACTION,
) -> tuple[Sequence[BackfillRecord], BackfillCheckpoint]:
    """Commit the definition phase and let it transition the head to ``version``."""

    store = store_for(ledger)
    ctx = context(transaction_id=transaction_id)
    chosen = decision if decision is not None else accepted(
        definition_candidate(ctx, definition_row()), transaction_id=transaction_id,
    )
    batch = await store.classify_and_commit_page(
        context=ctx,
        gates=upstream_gates(transaction_id=transaction_id),
        expected_checkpoint=fresh_checkpoint(phase="definition"),
        decisions=(chosen,),
        page_exhausted=True,
    )
    return batch.records, batch.checkpoint


async def commit_version_page(
    ledger: LedgerMirror,
    *,
    head: BackfillCheckpoint,
    decisions: Sequence[CandidateDecision] | None = None,
    transaction_id: str = VERSION_TRANSACTION,
) -> tuple[Sequence[BackfillRecord], BackfillCheckpoint]:
    """Commit an exhausted version page over ``head`` and complete the stream."""

    store = store_for(ledger)
    ctx = context(transaction_id=transaction_id)
    chosen = tuple(decisions) if decisions is not None else (
        accepted(
            version_candidate(ctx, version_row(), definition_row()),
            transaction_id=transaction_id,
        ),
    )
    batch = await store.classify_and_commit_page(
        context=ctx,
        gates=upstream_gates(transaction_id=transaction_id),
        expected_checkpoint=head,
        decisions=chosen,
        page_exhausted=True,
    )
    return batch.records, batch.checkpoint


async def complete_ledger(
    ledger: LedgerMirror,
) -> tuple[tuple[BackfillRecord, ...], BackfillCheckpoint]:
    """One accepted definition row, one accepted version row, a complete cursor."""

    definition_records, version_head = await commit_definition_page(ledger)
    version_records, complete_head = await commit_version_page(ledger, head=version_head)
    return (*definition_records, *version_records), complete_head


# ---------------------------------------------------------------------------
# The stored MIG-M04E-02 handoff marker (production encoder, direct insert)
# ---------------------------------------------------------------------------


def handoff_gates(*, m04e_digest: str, parent_digest: str = SHA_C,
                  m04_digest: str = SHA_C) -> tuple[HandoffGate, ...]:
    return (
        HandoffGate("MIG-M04-01", True, parent_digest),
        HandoffGate("MIG-M04-02", True, m04_digest),
        HandoffGate("MIG-M04E-01", True, m04e_digest),
        HandoffGate("active_typed_parent_scope", True, m04_digest),
        HandoffGate("same_definition_version", True, SHA_D),
        HandoffGate("immutable_artifact", True, SHA_A),
    )


def export_for(
    records: Sequence[BackfillRecord],
    *,
    checkpoint: BackfillCheckpoint,
    gates: Sequence[HandoffGate] | None = None,
) -> M05EvidenceExport:
    """The export the M04E stage hands to M05, computed by the contract's own function.

    With no ``gates`` argument the ``MIG-M04E-01`` gate cites the export's own
    ``canonical_digest``, so the export is computed twice -- exactly what
    ``persist_m05_handoff`` does.  An explicit gate list is *used as given*: a caller that
    passes closed gates is modelling a stored handoff whose gates were closed, and silently
    replacing them with the open six would make that state unrepresentable.
    """

    source_snapshot_digest = checkpoint.source_snapshot_digest
    first = export_m05_evidence(
        tenant_id=checkpoint.partition[0],
        app_definition_id=checkpoint.partition[1],
        records=records,
        source_snapshot_digest=source_snapshot_digest,
        checkpoint=checkpoint,
        gates=gates or (),
    )
    resolved = (
        tuple(gates) if gates else handoff_gates(m04e_digest=first.canonical_digest)
    )
    return export_m05_evidence(
        tenant_id=checkpoint.partition[0],
        app_definition_id=checkpoint.partition[1],
        records=records,
        source_snapshot_digest=source_snapshot_digest,
        checkpoint=checkpoint,
        gates=resolved,
    )


def marker_for(
    export: M05EvidenceExport,
    *,
    record_id: str | None = None,
    checkpoint: BackfillCheckpoint | None = None,
    accepted_count: int | None = None,
    quarantined_count: int | None = None,
) -> M05HandoffMarker:
    checkpoint = checkpoint if checkpoint is not None else export.checkpoint
    return M05HandoffMarker(
        record_id=record_id or (
            "m04ehandoff_" + backfill_checkpoint_digest(checkpoint).removeprefix("sha256:")[:24]
        ),
        task_id="MIG-M04E-02",
        status=M04EHandoffStatus.READY,
        partition=(export.tenant_id, export.app_definition_id),
        source_snapshot_digest=export.source_snapshot_digest,
        canonical_digest=export.canonical_digest,
        checkpoint=checkpoint,
        accepted_count=(
            accepted_count if accepted_count is not None else len(export.accepted)
        ),
        quarantined_count=(
            quarantined_count if quarantined_count is not None else len(export.quarantined)
        ),
        gate_evidence=export.gates,
        actor="migration-operator",
        recorded_at="2026-11-04T00:00:00+00:00",
        decision_owner="coordinator",
        writer_generation=checkpoint.writer_generation,
        correlation_id=CORRELATION_ID,
        canonical_positive_writes=False,
    )


async def append_marker(ledger: LedgerMirror, marker: M05HandoffMarker) -> None:
    """Insert one marker row through the production column mapping."""

    await ledger.insert(PROMOTION_TABLE, **handoff_marker_row_values(marker))


async def seeded_partition(
    ledger: LedgerMirror,
) -> tuple[M05EvidenceExport, M05HandoffMarker]:
    """A complete ledger plus the handoff marker recorded over it."""

    records, head = await complete_ledger(ledger)
    export = export_for(records, checkpoint=head)
    marker = marker_for(export)
    await append_marker(ledger, marker)
    return export, marker


# ---------------------------------------------------------------------------
# Raw row helpers (to model states the store cannot produce)
# ---------------------------------------------------------------------------


def mapping_row_values(
    *,
    source_id: str = "aver_0001",
    source_kind: str = "m04e.legacy.app_version",
    phase: str = "version",
    status: str = "accepted",
    checkpoint_token: str = "m04e-cp:test-token",
    source_digest: str = SHA_B,
    canonical_digest: str | None = SHA_C,
    canonical_identity: Sequence[str] | None = None,
    reason_code: str | None = None,
    proof_evidence: Mapping[str, str] | None = None,
    tenant_id: str = TENANT,
    definition_id: str = DEFINITION_ID,
    suffix: str = "0001",
) -> dict[str, Any]:
    """One ledger mapping row, in the shape ``_append_first_mapping`` writes."""

    identity = list(
        canonical_identity
        if canonical_identity is not None
        else ([tenant_id, definition_id, MANIFEST_VERSION] if phase == "version"
              else [tenant_id, APP_ID])
    )
    return {
        "id": f"m04emap_{suffix}",
        "tenant_id": tenant_id,
        "definition_id": definition_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_schema_version": SOURCE_SCHEMA,
        "phase": phase,
        "status": status,
        "source_digest": source_digest,
        "canonical_digest": canonical_digest,
        "canonical_identity": identity,
        "reason_code": reason_code,
        "proof_evidence": dict(proof_evidence or {
            "owner": SHA_C,
            "active_scope": SHA_C,
            "same_definition": SHA_D,
            "immutable_artifact": SHA_A,
            "artifact_content": SHA_A,
        }),
        "artifact_digest": SHA_A,
        "verified_artifact_digest": SHA_A,
        "checkpoint": checkpoint_token,
        "correlation_id": CORRELATION_ID,
    }


def checkpoint_row_values(
    *,
    phase: str = "version",
    after_source_id: str | None = "aver_0001",
    sequence: int = 2,
    phase_complete: bool = True,
    checkpoint_token: str = "m04e-cp:head-token",
    tenant_id: str = TENANT,
    definition_id: str = DEFINITION_ID,
    suffix: str = "0001",
) -> dict[str, Any]:
    ctx = context()
    return {
        "id": f"m04ecp_{suffix}",
        "tenant_id": tenant_id,
        "definition_id": definition_id,
        "phase": phase,
        "after_source_id": after_source_id,
        "sequence": sequence,
        "phase_complete": phase_complete,
        "source_snapshot_id": ctx.source_snapshot_id,
        "source_schema_version": ctx.source_schema_version,
        "source_snapshot_digest": ctx.source_snapshot_digest,
        "canonical_schema_version": ctx.canonical_schema_version,
        "writer_generation": ctx.writer_generation,
        "checkpoint_token": checkpoint_token,
    }


def audit_row_values(
    *,
    checkpoint_token: str = "m04e-cp:test-token",
    phase: str = "version",
    sequence: int = 0,
    event_kind: str = "page_committed",
    accepted_count: int = 1,
    tenant_id: str = TENANT,
    definition_id: str = DEFINITION_ID,
    suffix: str = "0001",
) -> dict[str, Any]:
    ctx = context()
    return {
        "id": f"m04eaudit_{suffix}",
        "event_id": f"sha256:{'0' * 63}{suffix[-1]}",
        "tenant_id": tenant_id,
        "definition_id": definition_id,
        "phase": phase,
        "event_kind": event_kind,
        "reason_code": None,
        "correlation_id": CORRELATION_ID,
        "checkpoint": checkpoint_token,
        "sequence": sequence,
        "actor": ctx.actor,
        "transaction_id": ctx.transaction_id,
        "record_count": accepted_count,
        "accepted_count": accepted_count,
        "replay_count": 0,
        "quarantined_count": 0,
        "conflict_count": 0,
        "blocker_evidence": [],
    }


def quarantine_row_values(
    *,
    source_id: str = "aver_0001",
    source_kind: str = "m04e.legacy.app_version",
    phase: str = "version",
    source_digest: str = SHA_B,
    observed_source_digest: str | None = SHA_D,
    observed_artifact_digest: str | None = None,
    reason_code: str = "source_digest_changed",
    checkpoint_token: str = "m04e-cp:test-token",
    status: str = "open",
    tenant_id: str = TENANT,
    definition_id: str = DEFINITION_ID,
    suffix: str = "0001",
) -> dict[str, Any]:
    return {
        "id": f"m04equar_{suffix}",
        "tenant_id": tenant_id,
        "definition_id": definition_id,
        "phase": phase,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_schema_version": SOURCE_SCHEMA,
        "source_digest": source_digest,
        "observed_source_digest": observed_source_digest,
        "observed_artifact_digest": observed_artifact_digest,
        "reason_code": reason_code,
        "blocker_evidence": [reason_code],
        "checkpoint": checkpoint_token,
        "status": status,
        "correlation_id": CORRELATION_ID,
    }


async def seed_raw_partition(
    ledger: LedgerMirror,
    *,
    mappings: Sequence[Mapping[str, Any]] = (),
    observations: Sequence[Mapping[str, Any]] = (),
    checkpoints: Sequence[Mapping[str, Any]] = (),
    audits: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Insert the given rows verbatim, to model a ledger no page commit produces."""

    for name, rows in (
        (MAPPING_TABLE, mappings),
        (QUARANTINE_TABLE, observations),
        (CHECKPOINT_TABLE, checkpoints),
        (AUDIT_TABLE, audits),
    ):
        for row in rows:
            await ledger.insert(name, **dict(row))


async def count_rows(ledger: LedgerMirror, table_name: str) -> int:
    return await ledger.count(table_name)


def with_checkpoint(record: BackfillRecord, checkpoint: BackfillCheckpoint) -> BackfillRecord:
    """Restamp one record's cursor (used to model a handoff recorded over another cursor)."""

    return replace(record, checkpoint=checkpoint)


__all__ = [
    "APP_ID",
    "AUDIT_TABLE",
    "CHECKPOINT_TABLE",
    "CORRELATION_ID",
    "DEFINITION_ID",
    "DEFINITION_TRANSACTION",
    "ExportLedgerMirror",
    "HANDOFF_GATE_NAMES",
    "MANIFEST_VERSION",
    "MAPPING_TABLE",
    "OTHER_DEFINITION_ID",
    "OTHER_TENANT",
    "PROMOTION_TABLE",
    "QUARANTINE_TABLE",
    "SNAPSHOT_DIGEST",
    "SNAPSHOT_ID",
    "SOURCE_SCHEMA",
    "SECOND_MARKER_RECORD_ID",
    "TENANT",
    "VERSION_TWO",
    "VERSION_TRANSACTION",
    "accepted",
    "append_marker",
    "audit_row_values",
    "checkpoint_row_values",
    "commit_definition_page",
    "commit_version_page",
    "complete_ledger",
    "context",
    "count_rows",
    "definition_candidate",
    "export_for",
    "fresh_checkpoint",
    "handoff_gates",
    "mapping_row_values",
    "marker_for",
    "quarantine_row_values",
    "quarantined",
    "seed_raw_partition",
    "seeded_partition",
    "store_for",
    "upstream_gates",
    "version_candidate",
    "version_row",
    "version_two_row",
    "with_checkpoint",
]
