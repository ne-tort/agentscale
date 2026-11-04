"""Executable cutover ports: the durable repository and the four trusted verifiers.

Task ``MIG-CUTOVER-PORTS-01``. ``cutover.py`` declares five ports with no implementation
(``composition.UNIMPLEMENTED_PORTS``, review §3 G5). This module implements them, and is
deliberately explicit about what the reviewed schema can and cannot prove.

What is implemented here
========================

``SqlCutoverRepository`` (``CutoverRepository``)
    CAS/append-only store for ``PartitionCutover`` plus the ``PromotionEvidence`` lineage.
    One row per transition; the row *is* the state snapshot and the lineage record, so the
    store never issues ``UPDATE``/``DELETE`` and ``load`` is the latest revision.

``SqlGateVerifier`` (``GateVerifier``)
    Completeness of the M04 -> M04E -> M05 -> M06 chain read from the four registered
    evidence tables (no caller-provided digest is trusted on its own: every digest must be
    present in the stored row that claims it).

``SqlLegacyOwnerVerifier`` (``LegacyOwnerVerifier``)
    Legacy owner state: the accepted M04-02 row for the partition, its stored owner block,
    and the absence of any canonical claim.

``SqlRollbackVerifier`` (``RollbackVerifier``)
    Pre-read-cutover rollback feasibility: owner row still authoritative, stored rollback
    proof, no canonical promotion, no writer fence ahead of the rollback epoch.

``SqlCanonicalRepairVerifier`` (``CanonicalRepairVerifier``)
    Forward-repair proof: every stage reference resolvable in its real table, the repair
    envelope anchored to the immutable lineage head, and the fence not ahead of the repair.

Storage status -- read this before believing a cutover can run
==============================================================
The five tables in the reviewed schema host *upstream evidence*; none of them can host a
cutover state machine or a positive-write promotion record. ``CUTOVER_SCHEMA_BLOCKERS``
records why, and ``CUTOVER_REQUIRED_SCHEMA`` is the exact specification of the two tables
the repository needs (no Alembic revision is created by this module; a reviewer copies the
specification into one). ``SqlCutoverRepository`` therefore fails closed: every operation
first proves the required tables exist and otherwise raises
:class:`CutoverRepositoryBlocked` naming the missing tables plus the specification.

Envelope contract for the verifiers
===================================
The verifiers refuse to believe a boolean the caller merely asserts. Owner/rollback/repair
booleans and counts must be *stored* in the redacted ``gate_evidence`` JSON of the evidence
row they belong to, under the keys below (written by the trusted producer of that row):

``legacy_owner`` (on the M04 scope row)
    ``exclusive_read_owner``, ``exclusive_decision_owner``, ``exclusive_write_owner``,
    ``legacy_claims_enabled``, ``evidence_id``, ``writer_epoch``, ``source_snapshot_digest``.

``rollback`` (on the M04 scope row)
    ``evidence_id``, ``evidence_digest``, ``writer_epoch``, ``legacy_owner_restored``,
    ``canonical_claims_stopped``, ``active_canonical_writers_remaining``,
    ``unresolved_legacy_claims``.

``canonical_repair`` (on the M06 evidence row)
    ``evidence_id``, ``evidence_digest``, ``writer_epoch``, ``unresolved_conflicts``,
    ``unknown_mappings``, ``unresolved_positive_bindings``, ``stale_generation_acceptances``,
    ``active_canonical_writers_remaining``, ``canonical_writes_stopped``, ``audit_complete``,
    ``parent_evidence_id``, ``parent_evidence_digest``.

Readiness is read from ``outcome`` when the table admits a ready value and from the
documented envelope status otherwise (``m04e``/``m05`` pin ``outcome`` to
blocked/quarantined -- blocker ``promotion_outcome_cannot_express_ready``). A row that
carries neither is not ready.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncIterator

from sqlalchemy import inspect as sa_inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.m06_cutover.cutover import (
    CanonicalRepairEvidence,
    CutoverAction,
    CutoverState,
    EvidenceReference,
    GateEvidence,
    GateLineageEvidence,
    GateStage,
    LegacyOwnerEvidence,
    PartitionCutover,
    PartitionKey,
    PromotionEvidence,
    PromotionRecord,
    RollbackEvidence,
    RollbackRecord,
    WriterLease,
)
from prodavan.infrastructure.persistence.models.canonical_grants import (
    CanonicalGrantWriterEpochRow,
    M06CanonicalGrantPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04ScopePromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.plugin_instance_registry import (
    M05PluginInstancePromotionEvidenceRow,
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Statuses a consumable evidence row may carry, from the column or the envelope.
READY_STATUSES = frozenset({"ready", "promotable"})

#: Actions whose lineage record is a rollback/repair record rather than a promotion record.
ROLLBACK_ACTIONS = frozenset({
    CutoverAction.ROLLED_BACK,
    CutoverAction.REPAIR_REQUIRED,
    CutoverAction.REPAIRED,
})

#: One model per cutover stage name. ``GateStage`` itself only names M04/M04E/M05 (the M06
#: proof is the ``GateEvidence`` object itself), but a ``CanonicalRepairEvidence`` reference
#: may name ``"M06"``, so the repair path looks rows up by name.
STAGE_MODEL_BY_NAME: Mapping[str, Any] = {
    "M04": M04ScopePromotionEvidenceRow,
    "M04E": M04EDefinitionVersionPromotionEvidenceRow,
    "M05": M05PluginInstancePromotionEvidenceRow,
    "M06": M06CanonicalGrantPromotionEvidenceRow,
}

STAGE_BY_NAME: Mapping[str, GateStage] = {stage.value: stage for stage in GateStage}

#: Digest-bearing columns per stage. The M04 row has no ``target_digest``; the other three
#: carry the canonical digest in ``target_digest`` and the source snapshot in ``source_digest``.
CANONICAL_DIGEST_COLUMN: Mapping[GateStage, str] = {
    GateStage.M04: "canonical_digest",
    GateStage.M04E: "target_digest",
    GateStage.M05: "target_digest",
}
SOURCE_DIGEST_COLUMN: Mapping[GateStage, str] = {
    GateStage.M04: "source_snapshot_digest",
    GateStage.M04E: "source_digest",
    GateStage.M05: "source_digest",
}

LEGACY_OWNER_BLOCK = "legacy_owner"
ROLLBACK_BLOCK = "rollback"
CANONICAL_REPAIR_BLOCK = "canonical_repair"


# ---------------------------------------------------------------------------
# Refusals and shared helpers
# ---------------------------------------------------------------------------


class CutoverPortBlocked(RuntimeError):
    """A cutover port cannot decide, because its reviewed storage contract is unmet."""

    def __init__(self, blockers: Sequence[str], *, tables: Sequence["TableSpec"] = ()) -> None:
        unique = tuple(dict.fromkeys(str(item) for item in blockers))
        if not unique:
            raise ValueError("a blocked cutover port needs at least one blocker")
        self.blockers = unique
        self.tables = tuple(tables)
        super().__init__(", ".join(unique))


class CutoverRepositoryBlocked(CutoverPortBlocked):
    """The repository's required table(s) are absent, so no cutover state can be stored."""


class CutoverRepositoryCorrupt(CutoverPortBlocked):
    """A stored payload cannot be decoded into its own immutable contract type."""


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _all_strings(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _all_strings(key, out)
            _all_strings(item, out)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _all_strings(item, out)


def recorded_strings(row: Any) -> frozenset[str]:
    """Every string the stored row actually contains, columns and JSON envelopes alike."""

    if row is None:
        return frozenset()
    values: set[str] = set()
    for column in row.__table__.columns:
        _all_strings(getattr(row, column.key, None), values)
    return frozenset(values)


def recorded_digests(row: Any) -> frozenset[str]:
    """Every sha256 the stored row actually contains (columns and JSON envelopes)."""

    return frozenset(value for value in recorded_strings(row) if is_sha256(value))


def mentions(row: Any, *tokens: str | None) -> bool:
    """True only when the row physically records every non-empty token."""

    required = [token for token in tokens if token]
    if len(required) != len(tokens) or not required:
        return False
    recorded = recorded_strings(row)
    return all(token in recorded for token in required)


def envelope(row: Any) -> Mapping[str, Any]:
    value = getattr(row, "gate_evidence", None)
    return value if isinstance(value, Mapping) else {}


def envelope_block(row: Any, key: str) -> Mapping[str, Any] | None:
    block = envelope(row).get(key)
    return block if isinstance(block, Mapping) else None


def row_is_ready(row: Any) -> bool:
    """Ready status from the ``outcome`` column or from the documented envelope status."""

    if row is None:
        return False
    outcome = getattr(row, "outcome", None)
    if isinstance(outcome, str) and outcome in READY_STATUSES:
        return True
    body = envelope(row)
    for candidate in (body.get("status"), (body.get("record") or {}).get("status")
                      if isinstance(body.get("record"), Mapping) else None):
        if isinstance(candidate, str) and candidate in READY_STATUSES:
            return True
    return False


def partition_bound(row: Any, partition: PartitionKey) -> bool:
    if row is None:
        return False
    if getattr(row, "tenant_id", None) != partition.tenant_id:
        return False
    definition = getattr(row, "definition_id", None)
    if definition is not None and definition != partition.app_definition_id:
        return False
    return True


def lineage_row_ok(stage: GateStage, row: Any, lineage: GateLineageEvidence,
                   partition: PartitionKey) -> bool:
    """One stored row must prove the lineage claim it is cited for."""

    if not partition_bound(row, partition):
        return False
    if bool(getattr(row, "canonical_positive_writes", False)):
        return False
    if not row_is_ready(row):
        return False
    canonical = getattr(row, CANONICAL_DIGEST_COLUMN[stage], None)
    source = getattr(row, SOURCE_DIGEST_COLUMN[stage], None)
    if canonical != lineage.canonical_digest or source != lineage.source_snapshot_digest:
        return False
    return lineage.evidence_digest in recorded_digests(row)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _fence_not_ahead(epochs: Sequence[int], epoch: int) -> bool:
    """No canonical writer fence may have advanced past the claimed epoch.

    This is the strongest available statement about canonical ownership: the reviewed schema
    has no table that records "this partition was promoted" (blocker
    ``cutover_promotion_marker_absent``), so a fence ahead of the claimed epoch -- not a
    promotion row -- is what makes a legacy claim or a rollback unsafe.
    """

    return all(value <= epoch for value in epochs)


# ---------------------------------------------------------------------------
# Cutover storage specification (blocker artefact; no revision is created here)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column of a required cutover table."""

    name: str
    sql_type: str
    nullable: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Exact specification of one table a cutover port needs but the schema lacks."""

    name: str
    purpose: str
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...]
    unique: tuple[tuple[str, ...], ...] = ()
    checks: tuple[str, ...] = ()
    indexes: tuple[tuple[str, ...], ...] = ()
    append_only: bool = True

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def column(self, name: str) -> ColumnSpec | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None


CUTOVER_LEDGER_TABLE = "m06_partition_cutover_evidence"
CUTOVER_SOURCE_TAIL_TABLE = "m06_partition_cutover_source_tail"

#: The two tables ``SqlCutoverRepository`` needs. Nothing in ``Base.metadata`` can host
#: them: ``m06_canonical_grant_promotion_evidence`` pins ``canonical_positive_writes`` to
#: ``false`` by check constraint, has no per-partition current-state row and no revision
#: counter, and no registered table stores the live legacy source change sequence.
CUTOVER_REQUIRED_SCHEMA: tuple[TableSpec, ...] = (
    TableSpec(
        name=CUTOVER_LEDGER_TABLE,
        purpose=(
            "append-only cutover ledger: one row per partition transition, carrying the "
            "resulting PartitionCutover snapshot and its immutable PromotionEvidence record; "
            "UNIQUE (tenant, definition, revision) is the state CAS and UNIQUE "
            "(tenant, definition, action, correlation) is the command idempotency key"
        ),
        columns=(
            ColumnSpec("id", "TEXT", note="lineage record id; equals the cutover command id"),
            ColumnSpec("tenant_id", "TEXT"),
            ColumnSpec("app_definition_id", "TEXT"),
            ColumnSpec("revision", "BIGINT", note="resulting PartitionCutover.revision"),
            ColumnSpec("action", "TEXT", note="CutoverAction value"),
            ColumnSpec("correlation_id", "TEXT", note="idempotency correlation of the command"),
            ColumnSpec("command_digest", "TEXT", note="sha256"),
            ColumnSpec("record_digest", "TEXT", note="sha256"),
            ColumnSpec("prior_record_id", "TEXT", nullable=True),
            ColumnSpec("prior_record_digest", "TEXT", nullable=True),
            ColumnSpec("resulting_state", "TEXT", note="CutoverState value"),
            ColumnSpec("writer_epoch", "BIGINT"),
            ColumnSpec("lease_fencing_token", "TEXT", nullable=True),
            ColumnSpec("canonical_positive_writes", "BOOLEAN"),
            ColumnSpec("occurred_at", "TIMESTAMPTZ"),
            ColumnSpec("state_payload", "TEXT", note="canonical JSON of PartitionCutover"),
            ColumnSpec("record_payload", "TEXT", note="canonical JSON of PromotionEvidence"),
        ),
        primary_key=("id",),
        unique=(
            ("tenant_id", "app_definition_id", "revision"),
            ("tenant_id", "app_definition_id", "action", "correlation_id"),
        ),
        checks=(
            "record_digest ~ '^sha256:[0-9a-f]{64}$'",
            "command_digest ~ '^sha256:[0-9a-f]{64}$'",
            "revision >= 0 AND writer_epoch >= 0",
            "canonical_positive_writes = false OR resulting_state = 'promoted'",
            "prior_record_id IS NULL OR prior_record_digest IS NOT NULL",
        ),
        indexes=(("tenant_id", "app_definition_id", "revision"),),
    ),
    TableSpec(
        name=CUTOVER_SOURCE_TAIL_TABLE,
        purpose=(
            "live legacy source tail per partition, written only by the legacy writer and "
            "read by the cutover CAS; without it expected_source_change_sequence cannot be "
            "compared and a final-delta transition must refuse"
        ),
        columns=(
            ColumnSpec("tenant_id", "TEXT"),
            ColumnSpec("app_definition_id", "TEXT"),
            ColumnSpec("change_sequence", "BIGINT"),
            ColumnSpec("writer_epoch", "BIGINT"),
            ColumnSpec("updated_at", "TIMESTAMPTZ"),
        ),
        primary_key=("tenant_id", "app_definition_id"),
        checks=("change_sequence >= 0 AND writer_epoch >= 0",),
        append_only=False,
    ),
)

#: Why the reviewed schema cannot host the cutover state machine. Review inputs, not
#: executed checks: each names a real constraint or a real absent artefact.
CUTOVER_SCHEMA_BLOCKERS: tuple[str, ...] = (
    "cutover_state_row_absent: no registered table stores the current PartitionCutover of a "
    "(tenant, app_definition) partition (state, read/decision/write owners, writer epoch, "
    "lease holder, fencing token, frozen gate digest, lineage head, revision)",
    "cutover_positive_write_ledger_absent: m06_canonical_grant_promotion_evidence pins "
    "canonical_positive_writes = false (ck_m06_grant_no_positive_writes), so it cannot store "
    "a promoted cutover record; recording the true flag only inside the JSON envelope would "
    "misstate a security-relevant column",
    "cutover_source_tail_absent: no registered table exposes the live legacy source change "
    "sequence the transition must compare with expected_source_change_sequence",
    "cutover_state_cas_absent: no registered table carries a per-partition revision column "
    "with a UNIQUE (tenant, definition, revision) key, which is the only atomic state CAS "
    "available without UPDATE",
    "cutover_promotion_marker_absent: no registered table records that a partition has been "
    "promoted, so the verifiers can only prove that no dynamic_app_capability_writer_epochs "
    "row advanced past the claimed epoch -- never that no promotion happened",
)


def cutover_schema_ddl(*, dialect: str = "postgresql") -> tuple[str, ...]:
    """Render ``CUTOVER_REQUIRED_SCHEMA`` as ``CREATE TABLE`` statements.

    This is the blocker artefact in executable form: a reviewer pastes the result into the
    Alembic revision instead of re-deriving the columns, and the acceptance fixture creates
    the same tables in SQLite so the fixture cannot drift from the specification. Nothing in
    the application runs this on a live database.
    """

    statements: list[str] = []
    postgres = dialect == "postgresql"
    for spec in CUTOVER_REQUIRED_SCHEMA:
        lines = [
            f"    {column.name} {column.sql_type}" + ("" if column.nullable else " NOT NULL")
            for column in spec.columns
        ]
        lines.append(f"    PRIMARY KEY ({', '.join(spec.primary_key)})")
        for key in spec.unique:
            lines.append(f"    UNIQUE ({', '.join(key)})")
        if postgres:
            for check in spec.checks:
                lines.append(f"    CHECK ({check})")
        statements.append(f"CREATE TABLE {spec.name} (\n" + ",\n".join(lines) + "\n)")
        for index, columns in enumerate(spec.indexes):
            statements.append(
                f"CREATE INDEX ix_{spec.name}_{index} ON {spec.name} ({', '.join(columns)})"
            )
    return tuple(statements)


# ---------------------------------------------------------------------------
# Payload codecs (the ledger stores JSON text, dialect-neutral)
# ---------------------------------------------------------------------------

STATE_PAYLOAD_SCHEMA = "m06-partition-cutover-state.v1"
EVIDENCE_PAYLOAD_SCHEMA = "m06-partition-cutover-evidence.v1"


def state_payload(state: PartitionCutover) -> dict[str, Any]:
    """Canonical, loss-free payload of one cutover state snapshot."""

    return {
        "schema": STATE_PAYLOAD_SCHEMA,
        "partition": [state.partition.tenant_id, state.partition.app_definition_id],
        "state": state.state.value,
        "canonical_read_owner": state.canonical_read_owner,
        "decision_owner": state.decision_owner,
        "write_owner": state.write_owner,
        "writer_epoch": state.writer_epoch,
        "lease_holder": state.lease_holder,
        "fencing_token": state.fencing_token,
        "legacy_projection_read_only": state.legacy_projection_read_only,
        "canonical_positive_writes": state.canonical_positive_writes,
        "canonical_shadow_reads": state.canonical_shadow_reads,
        "legacy_owner_evidence_id": state.legacy_owner_evidence_id,
        "legacy_owner_evidence_digest": state.legacy_owner_evidence_digest,
        "source_snapshot_digest": state.source_snapshot_digest,
        "final_delta_evidence_id": state.final_delta_evidence_id,
        "final_delta_evidence_digest": state.final_delta_evidence_digest,
        "final_delta_source_change_sequence": state.final_delta_source_change_sequence,
        "frozen_gate_digest": state.frozen_gate_digest,
        "last_evidence_id": state.last_evidence_id,
        "lineage_head_digest": state.lineage_head_digest,
        "revision": state.revision,
    }


def state_from_payload(payload: Mapping[str, Any]) -> PartitionCutover:
    """Decode one stored state payload; ``PartitionCutover`` itself re-checks invariants."""

    try:
        if payload.get("schema") != STATE_PAYLOAD_SCHEMA:
            raise ValueError("unrecognised cutover state payload schema")
        tenant_id, app_definition_id = payload["partition"]
        return PartitionCutover(
            partition=PartitionKey(str(tenant_id), str(app_definition_id)),
            state=CutoverState(payload["state"]),
            canonical_read_owner=str(payload["canonical_read_owner"]),
            decision_owner=str(payload["decision_owner"]),
            write_owner=str(payload["write_owner"]),
            writer_epoch=int(payload["writer_epoch"]),
            lease_holder=payload["lease_holder"],
            fencing_token=payload["fencing_token"],
            legacy_projection_read_only=bool(payload["legacy_projection_read_only"]),
            canonical_positive_writes=bool(payload["canonical_positive_writes"]),
            canonical_shadow_reads=bool(payload["canonical_shadow_reads"]),
            legacy_owner_evidence_id=payload["legacy_owner_evidence_id"],
            legacy_owner_evidence_digest=payload["legacy_owner_evidence_digest"],
            source_snapshot_digest=payload["source_snapshot_digest"],
            final_delta_evidence_id=payload["final_delta_evidence_id"],
            final_delta_evidence_digest=payload["final_delta_evidence_digest"],
            final_delta_source_change_sequence=payload["final_delta_source_change_sequence"],
            frozen_gate_digest=payload["frozen_gate_digest"],
            last_evidence_id=payload["last_evidence_id"],
            lineage_head_digest=payload["lineage_head_digest"],
            revision=int(payload["revision"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CutoverRepositoryCorrupt(("cutover_state_payload_invalid",)) from exc


def evidence_payload(record: PromotionEvidence) -> dict[str, Any]:
    """Canonical, loss-free payload of one lineage record."""

    return {
        "schema": EVIDENCE_PAYLOAD_SCHEMA,
        "evidence_id": record.evidence_id,
        "partition": [record.partition.tenant_id, record.partition.app_definition_id],
        "action": record.action.value,
        "actor": record.actor,
        "occurred_at": record.occurred_at.isoformat(),
        "prior_state": record.prior_state.value,
        "resulting_state": record.resulting_state.value,
        "prior_read_owner": record.prior_read_owner,
        "resulting_read_owner": record.resulting_read_owner,
        "writer_epoch": record.writer_epoch,
        "lease_fencing_token": record.lease_fencing_token,
        "parity_report_digest": record.parity_report_digest,
        "canonical_snapshot_digest": record.canonical_snapshot_digest,
        "source_snapshot_digest": record.source_snapshot_digest,
        "final_delta_checkpoint": record.final_delta_checkpoint,
        "final_delta_source_change_sequence": record.final_delta_source_change_sequence,
        "reason": record.reason,
        "record_digest": record.record_digest,
        "command_digest": record.command_digest,
        "prior_write_owner": record.prior_write_owner,
        "resulting_write_owner": record.resulting_write_owner,
        "prior_decision_owner": record.prior_decision_owner,
        "resulting_decision_owner": record.resulting_decision_owner,
        "prior_record_id": record.prior_record_id,
        "prior_record_digest": record.prior_record_digest,
        "gate_evidence": [
            [ref.stage, ref.evidence_id, ref.evidence_digest,
             ref.partition.tenant_id, ref.partition.app_definition_id]
            for ref in record.gate_evidence
        ],
        "canonical_positive_writes": record.canonical_positive_writes,
    }


def evidence_from_payload(payload: Mapping[str, Any]) -> PromotionEvidence:
    """Decode one stored lineage payload; the record re-checks its own immutable digest."""

    try:
        if payload.get("schema") != EVIDENCE_PAYLOAD_SCHEMA:
            raise ValueError("unrecognised cutover evidence payload schema")
        refs = tuple(
            EvidenceReference(
                stage=str(item[0]),
                evidence_id=str(item[1]),
                evidence_digest=str(item[2]),
                partition=PartitionKey(str(item[3]), str(item[4])),
            )
            for item in payload["gate_evidence"]
        )
        kwargs: dict[str, Any] = {
            "evidence_id": str(payload["evidence_id"]),
            "partition": PartitionKey(str(payload["partition"][0]), str(payload["partition"][1])),
            "action": CutoverAction(payload["action"]),
            "actor": str(payload["actor"]),
            "occurred_at": datetime.fromisoformat(str(payload["occurred_at"])),
            "prior_state": CutoverState(payload["prior_state"]),
            "resulting_state": CutoverState(payload["resulting_state"]),
            "prior_read_owner": str(payload["prior_read_owner"]),
            "resulting_read_owner": str(payload["resulting_read_owner"]),
            "writer_epoch": int(payload["writer_epoch"]),
            "lease_fencing_token": payload["lease_fencing_token"],
            "parity_report_digest": payload["parity_report_digest"],
            "canonical_snapshot_digest": payload["canonical_snapshot_digest"],
            "source_snapshot_digest": payload["source_snapshot_digest"],
            "final_delta_checkpoint": payload["final_delta_checkpoint"],
            "final_delta_source_change_sequence": payload["final_delta_source_change_sequence"],
            "reason": str(payload["reason"]),
            "record_digest": str(payload["record_digest"]),
            "command_digest": str(payload["command_digest"]),
            "prior_write_owner": str(payload["prior_write_owner"]),
            "resulting_write_owner": str(payload["resulting_write_owner"]),
            "prior_decision_owner": str(payload["prior_decision_owner"]),
            "resulting_decision_owner": str(payload["resulting_decision_owner"]),
            "prior_record_id": payload["prior_record_id"],
            "prior_record_digest": payload["prior_record_digest"],
            "gate_evidence": refs,
            "canonical_positive_writes": bool(payload["canonical_positive_writes"]),
        }
        record_type = RollbackRecord if kwargs["action"] in ROLLBACK_ACTIONS else PromotionRecord
        return record_type(**kwargs)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise CutoverRepositoryCorrupt(("cutover_evidence_payload_invalid",)) from exc


# ---------------------------------------------------------------------------
# Port 1: durable CutoverRepository
# ---------------------------------------------------------------------------


def _has_table(sync_session: Any, name: str) -> bool:
    return bool(sa_inspect(sync_session.connection()).has_table(name))


class SqlCutoverRepository:
    """``CutoverRepository`` over the append-only cutover ledger.

    One transaction per call, one appended row per transition: the row holds the resulting
    ``PartitionCutover`` snapshot and its ``PromotionEvidence`` record, so the state is never
    rewritten and the lineage is never mutated. The state CAS is
    ``UNIQUE (tenant_id, app_definition_id, revision)``; a lost race surfaces as an
    ``IntegrityError`` and returns ``False``, never a partial write.

    Fail-closed rules, all enforced before the append:

    * the required tables must exist (:data:`CUTOVER_REQUIRED_SCHEMA`);
    * ``expected_revision`` must be the stored revision, and the stored lineage head must be
      exactly the record's ``prior_record_id``/``prior_record_digest``;
    * the lease must be the partition's lease, not expired at ``evidence.occurred_at``,
      ahead of the stored epoch, and carry a fencing token different from the stored one;
    * the resulting state must be internally consistent with the record (same partition,
      revision, epoch, token, resulting state and canonical positive-write flag);
    * a ``FINAL_DELTA`` transition compares the live legacy source tail with
      ``expected_source_change_sequence``; a missing tail row refuses.

    A repeated append of the same record id with the same ``record_digest`` is an idempotent
    replay and returns ``True``; the same id with a different digest is a conflict (``False``).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        schema: Sequence[TableSpec] = CUTOVER_REQUIRED_SCHEMA,
    ) -> None:
        self._session_factory = session_factory
        self._schema = tuple(schema)

    @property
    def required_schema(self) -> tuple[TableSpec, ...]:
        return self._schema

    @property
    def schema_blockers(self) -> tuple[str, ...]:
        return CUTOVER_SCHEMA_BLOCKERS

    async def missing_tables(self) -> tuple[str, ...]:
        """Names of the required tables that the bound database does not have."""

        async with self._session_factory() as session:
            return await self._missing(session)

    # -- CutoverRepository protocol ---------------------------------------

    async def load(self, partition: PartitionKey) -> PartitionCutover | None:
        async with self._session_factory() as session:
            await self._require_schema(session)
            row = await self._latest_row(session, partition)
            if row is None:
                return None
            state = state_from_payload(_decode_json(row["state_payload"], "state"))
            if state.partition != partition:
                raise CutoverRepositoryCorrupt(("cutover_state_partition_drift",))
            return state

    async def evidence_by_id(self, evidence_id: str) -> PromotionEvidence | None:
        async with self._session_factory() as session:
            await self._require_schema(session)
            row = await self._row_by_id(session, evidence_id)
            if row is None:
                return None
            return evidence_from_payload(_decode_json(row["record_payload"], "record"))

    async def transition(
        self,
        *,
        expected_revision: int,
        state: PartitionCutover,
        evidence: PromotionEvidence,
        lease: WriterLease,
        expected_source_change_sequence: int | None,
    ) -> bool:
        _require_transition_arguments(expected_revision, state, evidence)
        async with self._session_factory() as session:
            await self._require_schema(session)
            current_row = await self._latest_row(session, evidence.partition)
            current = (
                None if current_row is None
                else state_from_payload(_decode_json(current_row["state_payload"], "state"))
            )
            if not _state_transition_allowed(current, expected_revision):
                return False
            if not _lineage_continues(current, evidence):
                return False
            if not _lease_is_live(current, lease, evidence, state):
                return False
            if expected_source_change_sequence is not None:
                tail = await self._source_tail(session, evidence.partition)
                if tail is None or tail["change_sequence"] != expected_source_change_sequence:
                    return False
            values = _ledger_values(state, evidence, lease)
            try:
                await session.execute(
                    text(
                        f"INSERT INTO {CUTOVER_LEDGER_TABLE} ("
                        "id, tenant_id, app_definition_id, revision, action, correlation_id, "
                        "command_digest, record_digest, prior_record_id, prior_record_digest, "
                        "resulting_state, writer_epoch, lease_fencing_token, "
                        "canonical_positive_writes, occurred_at, state_payload, record_payload"
                        ") VALUES ("
                        ":id, :tenant_id, :app_definition_id, :revision, :action, :correlation_id, "
                        ":command_digest, :record_digest, :prior_record_id, :prior_record_digest, "
                        ":resulting_state, :writer_epoch, :lease_fencing_token, "
                        ":canonical_positive_writes, :occurred_at, :state_payload, :record_payload)"
                    ),
                    values,
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self._row_by_id(session, evidence.evidence_id)
                return existing is not None and existing["record_digest"] == evidence.record_digest
            return True

    # -- internals ---------------------------------------------------------

    async def _missing(self, session: AsyncSession) -> tuple[str, ...]:
        missing: list[str] = []
        for spec in self._schema:
            if not await session.run_sync(_has_table, spec.name):
                missing.append(spec.name)
        return tuple(missing)

    async def _require_schema(self, session: AsyncSession) -> None:
        missing = await self._missing(session)
        if missing:
            raise CutoverRepositoryBlocked(
                tuple(f"cutover_repository_table_missing:{name}" for name in missing)
                + CUTOVER_SCHEMA_BLOCKERS,
                tables=self._schema,
            )

    async def _latest_row(self, session: AsyncSession, partition: PartitionKey) -> Any | None:
        return (
            await session.execute(
                text(
                    f"SELECT revision, record_digest, state_payload FROM {CUTOVER_LEDGER_TABLE} "
                    "WHERE tenant_id = :tenant_id AND app_definition_id = :app_definition_id "
                    "ORDER BY revision DESC LIMIT 1"
                ),
                {"tenant_id": partition.tenant_id,
                 "app_definition_id": partition.app_definition_id},
            )
        ).mappings().first()

    async def _row_by_id(self, session: AsyncSession, evidence_id: str) -> Any | None:
        return (
            await session.execute(
                text(
                    f"SELECT id, record_digest, record_payload FROM {CUTOVER_LEDGER_TABLE} "
                    "WHERE id = :id"
                ),
                {"id": evidence_id},
            )
        ).mappings().first()

    async def _source_tail(self, session: AsyncSession, partition: PartitionKey) -> Any | None:
        return (
            await session.execute(
                text(
                    f"SELECT change_sequence, writer_epoch FROM {CUTOVER_SOURCE_TAIL_TABLE} "
                    "WHERE tenant_id = :tenant_id AND app_definition_id = :app_definition_id"
                ),
                {"tenant_id": partition.tenant_id,
                 "app_definition_id": partition.app_definition_id},
            )
        ).mappings().first()


def _decode_json(raw: Any, label: str) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, (str, bytes)):
        raise CutoverRepositoryCorrupt((f"cutover_{label}_payload_missing",))
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise CutoverRepositoryCorrupt((f"cutover_{label}_payload_not_json",)) from exc
    if not isinstance(decoded, Mapping):
        raise CutoverRepositoryCorrupt((f"cutover_{label}_payload_not_object",))
    return decoded


def _require_transition_arguments(expected_revision: int, state: PartitionCutover,
                                  evidence: PromotionEvidence) -> None:
    """Structural contract violations are programming errors, not conflicts.

    Only the *state* is checked against the record here: a lease that is expired, foreign,
    stale or replayed is a real-world conflict and :func:`_lease_is_live` returns ``False``
    for it instead of raising.
    """

    if not _positive_int(expected_revision):
        raise ValueError("expected_revision must be a non-negative integer")
    if state.partition != evidence.partition:
        raise ValueError("resulting state and record must describe one partition")
    if state.revision != expected_revision + 1:
        raise ValueError("resulting state revision must advance exactly one")
    if state.last_evidence_id != evidence.evidence_id:
        raise ValueError("resulting state must point at the appended lineage record")
    if state.lineage_head_digest != evidence.record_digest:
        raise ValueError("resulting state lineage head must be the record digest")
    if state.state is not evidence.resulting_state:
        raise ValueError("resulting state must be the record's resulting state")
    if state.writer_epoch != evidence.writer_epoch:
        raise ValueError("resulting state and record disagree on the writer epoch")
    if state.canonical_positive_writes != evidence.canonical_positive_writes:
        raise ValueError("resulting state and record disagree on canonical positive writes")
    if not is_sha256(evidence.record_digest) or not is_sha256(evidence.command_digest):
        raise ValueError("lineage record digests must be sha256")


def _state_transition_allowed(current: PartitionCutover | None, expected_revision: int) -> bool:
    if current is None:
        return expected_revision == 0
    return current.revision == expected_revision


def _lineage_continues(current: PartitionCutover | None, evidence: PromotionEvidence) -> bool:
    if current is None:
        return evidence.prior_record_id is None and evidence.prior_record_digest is None
    return (evidence.prior_record_id == current.last_evidence_id
            and evidence.prior_record_digest == current.lineage_head_digest)


def _lease_is_live(current: PartitionCutover | None, lease: WriterLease,
                   evidence: PromotionEvidence, resulting: PartitionCutover) -> bool:
    """Expired, foreign, replayed or stale leases never commit a transition.

    Two cases are admissible: a *new* fence (``epoch > current.writer_epoch`` with a new
    fencing token) and the *live* fence already recorded by the partition. The second case is
    not a replay: ``promote`` deliberately re-uses the fence that owns the final-delta freeze
    (``cutover.py`` refuses a promotion issued under any other fence), so an equal epoch is
    accepted only when holder and fencing token are byte-identical to the stored pair.
    """

    if lease.partition != evidence.partition or resulting.partition != lease.partition:
        return False
    if lease.expires_at <= evidence.occurred_at:
        return False
    if lease.epoch != evidence.writer_epoch or resulting.writer_epoch != lease.epoch:
        return False
    if resulting.fencing_token != lease.fencing_token:
        return False
    if current is None:
        return True
    if lease.epoch < current.writer_epoch:
        return False
    if lease.epoch == current.writer_epoch:
        return (lease.holder == current.lease_holder
                and lease.fencing_token == current.fencing_token)
    return lease.fencing_token != current.fencing_token


def _ledger_values(state: PartitionCutover, evidence: PromotionEvidence,
                   lease: WriterLease) -> dict[str, Any]:
    return {
        "id": evidence.evidence_id,
        "tenant_id": evidence.partition.tenant_id,
        "app_definition_id": evidence.partition.app_definition_id,
        "revision": state.revision,
        "action": evidence.action.value,
        "correlation_id": evidence.evidence_id,
        "command_digest": evidence.command_digest,
        "record_digest": evidence.record_digest,
        "prior_record_id": evidence.prior_record_id,
        "prior_record_digest": evidence.prior_record_digest,
        "resulting_state": state.state.value,
        "writer_epoch": evidence.writer_epoch,
        "lease_fencing_token": lease.fencing_token,
        "canonical_positive_writes": bool(evidence.canonical_positive_writes),
        "occurred_at": evidence.occurred_at.isoformat(),
        "state_payload": _json_text(state_payload(state)),
        "record_payload": _json_text(evidence_payload(evidence)),
    }


# ---------------------------------------------------------------------------
# Port 2: GateVerifier -- completeness of the M04 -> M04E -> M05 -> M06 chain
# ---------------------------------------------------------------------------


class _Reading:
    """One read transaction per verification call; no write path exists here."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            yield session

    async def row(self, session: AsyncSession, stage_name: str, identifier: str) -> Any | None:
        """Fetch one evidence row by stage name; an unknown stage is not a row."""

        model = STAGE_MODEL_BY_NAME.get(stage_name)
        if model is None:
            return None
        return await session.get(model, identifier)

    async def fence_epochs(self, session: AsyncSession,
                           partition: PartitionKey) -> tuple[int, ...]:
        rows = await session.execute(
            select(CanonicalGrantWriterEpochRow.writer_epoch).where(
                CanonicalGrantWriterEpochRow.tenant_id == partition.tenant_id,
                CanonicalGrantWriterEpochRow.app_definition_id == partition.app_definition_id,
            )
        )
        return tuple(int(value) for value in rows.scalars())


class SqlGateVerifier:
    """``GateVerifier`` over the four registered evidence tables.

    A gate is verified only when every link is *stored*: each stage row exists for the same
    partition, is ready, never enabled canonical positive writes, carries exactly the digests
    the gate cites, and physically records the cited evidence digest. The chain is then
    checked link by link -- the M04E row must record the M04 id and digest, the M05 row the
    M04E pair, the M06 row the M05 pair -- and the M06 row must additionally record the
    legacy owner marker, the writer-drain receipt, the final-delta receipt and the parity
    report digest. Anything else returns ``False``; nothing is inferred.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._reads = _Reading(session_factory)

    async def verify(self, *, evidence: GateEvidence) -> bool:
        if not evidence.ready:
            return False
        partition = evidence.partition
        m04, m04e, m05 = evidence.m04, evidence.m04e, evidence.m05
        drain, delta = evidence.legacy_writer_drain, evidence.final_delta
        if m04 is None or m04e is None or m05 is None or drain is None or delta is None:
            return False
        async with self._reads.session() as session:
            rows = {
                GateStage.M04: await self._reads.row(session, "M04", m04.evidence_id),
                GateStage.M04E: await self._reads.row(session, "M04E", m04e.evidence_id),
                GateStage.M05: await self._reads.row(session, "M05", m05.evidence_id),
            }
            m06_row = await self._reads.row(
                session, "M06", evidence.evidence_id or ""
            )
            if m06_row is None:
                return False
            for stage, lineage in ((GateStage.M04, m04), (GateStage.M04E, m04e),
                                   (GateStage.M05, m05)):
                if not lineage_row_ok(stage, rows[stage], lineage, partition):
                    return False
            if not _m06_gate_row_ok(m06_row, evidence, partition):
                return False
            if not mentions(rows[GateStage.M04E], m04.evidence_id, m04.evidence_digest):
                return False
            if not mentions(rows[GateStage.M05], m04e.evidence_id, m04e.evidence_digest):
                return False
            if not mentions(m06_row, m05.evidence_id, m05.evidence_digest):
                return False
            if not mentions(m06_row, evidence.legacy_owner_evidence_id,
                            evidence.legacy_owner_evidence_digest):
                return False
            if not mentions(m06_row, drain.evidence_id, drain.evidence_digest):
                return False
            if not mentions(m06_row, delta.evidence_id, delta.evidence_digest):
                return False
            if not mentions(m06_row, evidence.report_digest):
                return False
            return await self._fence_covers(session, partition, evidence.writer_epoch)

    async def _fence_covers(self, session: AsyncSession, partition: PartitionKey,
                            epoch: int | None) -> bool:
        if epoch is None or epoch <= 0:
            return False
        return all(value <= epoch for value in await self._reads.fence_epochs(session, partition))


def _m06_gate_row_ok(row: Any, evidence: GateEvidence, partition: PartitionKey) -> bool:
    """The M06 row is the gate's own record: ready, partition-bound and epoch-bound."""

    if not partition_bound(row, partition):
        return False
    if bool(getattr(row, "canonical_positive_writes", False)):
        return False
    if not row_is_ready(row):
        return False
    if getattr(row, "target_digest", None) != evidence.canonical_digest:
        return False
    if getattr(row, "source_digest", None) != evidence.source_snapshot_digest:
        return False
    if getattr(row, "writer_epoch", None) != evidence.writer_epoch:
        return False
    return (evidence.evidence_digest or "") in recorded_digests(row)


# ---------------------------------------------------------------------------
# Port 3: LegacyOwnerVerifier -- legacy owner state
# ---------------------------------------------------------------------------


class SqlLegacyOwnerVerifier:
    """``LegacyOwnerVerifier`` over the accepted M04-02 scope row.

    The partition has exactly one legacy owner marker: the ready ``m04_scope_promotion_evidence``
    row cited by the evidence. It must belong to the partition, carry the same source snapshot
    and writer epoch, still pin ``canonical_positive_writes = false``, and its stored
    ``legacy_owner`` block must attest exclusive read/decision/write ownership and enabled
    legacy claims for that same evidence id and epoch. A partition with an existing canonical
    promotion record, or a writer fence ahead of the claimed epoch, is not a legacy owner.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._reads = _Reading(session_factory)

    async def verify(self, *, evidence: LegacyOwnerEvidence) -> bool:
        if not evidence.ready:
            return False
        async with self._reads.session() as session:
            row = await self._reads.row(session, "M04", evidence.evidence_id)
            if not partition_bound(row, evidence.partition) or not row_is_ready(row):
                return False
            if bool(getattr(row, "canonical_positive_writes", False)):
                return False
            if getattr(row, "source_snapshot_digest", None) != evidence.source_snapshot_digest:
                return False
            if getattr(row, "writer_generation", None) != evidence.writer_epoch:
                return False
            if evidence.evidence_digest not in recorded_digests(row):
                return False
            block = envelope_block(row, LEGACY_OWNER_BLOCK)
            if block is None:
                return False
            for flag in ("exclusive_read_owner", "exclusive_decision_owner",
                         "exclusive_write_owner", "legacy_claims_enabled"):
                if block.get(flag) is not True:
                    return False
            if block.get("evidence_id") != evidence.evidence_id:
                return False
            if block.get("writer_epoch") != evidence.writer_epoch:
                return False
            if block.get("source_snapshot_digest") != evidence.source_snapshot_digest:
                return False
            return _fence_not_ahead(
                await self._reads.fence_epochs(session, evidence.partition),
                evidence.writer_epoch,
            )


# ---------------------------------------------------------------------------
# Port 4: RollbackVerifier -- pre-read-cutover rollback feasibility
# ---------------------------------------------------------------------------


class SqlRollbackVerifier:
    """``RollbackVerifier`` over the same M04-02 row and the live writer fence.

    Rollback is possible only while the cited legacy-owner row is still the accepted owner
    (ready, same source snapshot, canonical positive writes still false) **and** that row
    stores a ``rollback`` block proving legacy ownership was restored, canonical claims
    stopped and drained, and no legacy claim left unresolved. A canonical promotion record
    or a writer fence ahead of the rollback epoch makes the rollback impossible.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._reads = _Reading(session_factory)

    async def verify(self, *, evidence: RollbackEvidence) -> bool:
        if not evidence.ready:
            return False
        async with self._reads.session() as session:
            row = await self._reads.row(
                session, "M04", evidence.legacy_owner_evidence_id
            )
            if not partition_bound(row, evidence.partition) or not row_is_ready(row):
                return False
            if bool(getattr(row, "canonical_positive_writes", False)):
                return False
            if getattr(row, "source_snapshot_digest", None) != evidence.source_snapshot_digest:
                return False
            if evidence.legacy_owner_evidence_digest not in recorded_digests(row):
                return False
            block = envelope_block(row, ROLLBACK_BLOCK)
            if block is None:
                return False
            if block.get("evidence_id") != evidence.evidence_id:
                return False
            if block.get("evidence_digest") != evidence.evidence_digest:
                return False
            if block.get("writer_epoch") != evidence.writer_epoch:
                return False
            if block.get("legacy_owner_restored") is not True:
                return False
            if block.get("canonical_claims_stopped") is not True:
                return False
            if block.get("active_canonical_writers_remaining") != 0:
                return False
            if block.get("unresolved_legacy_claims") != 0:
                return False
            return _fence_not_ahead(
                await self._reads.fence_epochs(session, evidence.partition),
                evidence.writer_epoch,
            )


# ---------------------------------------------------------------------------
# Port 5: CanonicalRepairVerifier -- forward repair proof
# ---------------------------------------------------------------------------


class SqlCanonicalRepairVerifier:
    """``CanonicalRepairVerifier`` over the four evidence tables and the writer fence.

    Every ``gate_evidence`` reference must resolve in its own real table for the same
    partition, be ready, and record the cited digest. The M06 row must record both the
    immutable lineage head named by the evidence (``parent_*``) and a ``canonical_repair``
    block whose counts, flags and epoch equal the evidence exactly. Finally the partition
    must already have a writer fence and no fence row may be ahead of the repair epoch.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._reads = _Reading(session_factory)

    async def verify(self, *, evidence: CanonicalRepairEvidence) -> bool:
        if not evidence.ready:
            return False
        partition = evidence.partition
        async with self._reads.session() as session:
            m06_row: Any | None = None
            for reference in evidence.gate_evidence:
                if reference.stage not in STAGE_MODEL_BY_NAME:
                    return False
                row = await self._reads.row(session, reference.stage, reference.evidence_id)
                if not partition_bound(row, partition):
                    return False
                if bool(getattr(row, "canonical_positive_writes", False)):
                    return False
                if not row_is_ready(row):
                    return False
                if reference.evidence_digest not in recorded_digests(row):
                    return False
                if reference.stage == "M06":
                    m06_row = row
            if m06_row is None:
                return False
            if getattr(m06_row, "source_digest", None) != evidence.source_snapshot_digest:
                return False
            block = envelope_block(m06_row, CANONICAL_REPAIR_BLOCK)
            if block is None or not _repair_block_matches(block, evidence):
                return False
            epochs = await self._reads.fence_epochs(session, partition)
            if not epochs:
                return False
            return _fence_not_ahead(epochs, evidence.writer_epoch)


def _repair_block_matches(block: Mapping[str, Any], evidence: CanonicalRepairEvidence) -> bool:
    if block.get("evidence_id") != evidence.evidence_id:
        return False
    if block.get("evidence_digest") != evidence.evidence_digest:
        return False
    if block.get("writer_epoch") != evidence.writer_epoch:
        return False
    if block.get("parent_evidence_id") != evidence.parent_evidence_id:
        return False
    if block.get("parent_evidence_digest") != evidence.parent_evidence_digest:
        return False
    for field in ("unresolved_conflicts", "unknown_mappings", "unresolved_positive_bindings",
                  "stale_generation_acceptances", "active_canonical_writers_remaining"):
        if block.get(field) != getattr(evidence, field):
            return False
    return (block.get("canonical_writes_stopped") is evidence.canonical_writes_stopped
            and block.get("audit_complete") is evidence.audit_complete)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CutoverPorts:
    """The five cutover ports over one session factory."""

    repository: SqlCutoverRepository
    gate_verifier: SqlGateVerifier
    legacy_owner_verifier: SqlLegacyOwnerVerifier
    rollback_verifier: SqlRollbackVerifier
    canonical_repair_verifier: SqlCanonicalRepairVerifier


def build_cutover_ports(*, session_factory: async_sessionmaker[AsyncSession]) -> CutoverPorts:
    """Construct all five ports. Never a stub: the repository refuses until its schema lands."""

    return CutoverPorts(
        repository=SqlCutoverRepository(session_factory=session_factory),
        gate_verifier=SqlGateVerifier(session_factory=session_factory),
        legacy_owner_verifier=SqlLegacyOwnerVerifier(session_factory=session_factory),
        rollback_verifier=SqlRollbackVerifier(session_factory=session_factory),
        canonical_repair_verifier=SqlCanonicalRepairVerifier(session_factory=session_factory),
    )


def _utc_now() -> datetime:  # pragma: no cover - convenience for hosts, unused internally
    return datetime.now(UTC)


__all__ = [
    "CANONICAL_DIGEST_COLUMN",
    "CANONICAL_REPAIR_BLOCK",
    "CUTOVER_LEDGER_TABLE",
    "CUTOVER_REQUIRED_SCHEMA",
    "CUTOVER_SCHEMA_BLOCKERS",
    "CUTOVER_SOURCE_TAIL_TABLE",
    "ColumnSpec",
    "CutoverPortBlocked",
    "CutoverPorts",
    "CutoverRepositoryBlocked",
    "CutoverRepositoryCorrupt",
    "LEGACY_OWNER_BLOCK",
    "READY_STATUSES",
    "ROLLBACK_ACTIONS",
    "ROLLBACK_BLOCK",
    "SOURCE_DIGEST_COLUMN",
    "STAGE_BY_NAME",
    "STAGE_MODEL_BY_NAME",
    "SqlCanonicalRepairVerifier",
    "SqlCutoverRepository",
    "SqlGateVerifier",
    "SqlLegacyOwnerVerifier",
    "SqlRollbackVerifier",
    "TableSpec",
    "build_cutover_ports",
    "cutover_schema_ddl",
    "envelope",
    "envelope_block",
    "evidence_from_payload",
    "evidence_payload",
    "is_sha256",
    "lineage_row_ok",
    "mentions",
    "partition_bound",
    "recorded_digests",
    "recorded_strings",
    "row_is_ready",
    "state_from_payload",
    "state_payload",
]
