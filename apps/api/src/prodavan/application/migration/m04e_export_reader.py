"""Read the M04E ``M05EvidenceExport`` of one partition out of its durable ledger.

Task ``MIG-M04E-EXPORT-01``.

Why this module exists
======================

``m05_contexts.build_m05_upstream_evidence`` has always taken ``M05EvidenceExport``
as an *input* and refused without it
(``m05_upstream_evidence_m04e_export_missing``).  The reason recorded there was that
the export carries the accepted M04E ledger rows, "which the marker only counts", so
producing it was said to be impossible without inventing the ``canonical_digest`` M05
recomputes.

That reasoning is now stale.  Revision ``2026110315`` created four ledger tables and
:class:`~prodavan.application.migration.m04e_ports.SqlM04EEvidenceStore` really writes
them:

===============================  ==========================================
table                            what one row is
===============================  ==========================================
``..._mappings``                 the first mapping of one exact source identity, with its
                                 ``accepted`` / ``quarantined`` / ``conflict`` status, the
                                 canonical identity/digest and the named proof digests
``..._quarantine``               one changed-digest observation of an already-mapped source
``..._checkpoints``              the durable cursor of the partition (one head row)
``..._audit``                    the redacted per-page event, carrying the page token, the
                                 phase, the observed sequence and the transaction id
===============================  ==========================================

So the accepted rows are *in the database*.  This module reads them back and hands
them to the **unchanged** :func:`export_m05_evidence`, which recomputes
``canonical_digest`` exactly as it does for a freshly committed page.  Nothing here
re-implements that digest: :func:`~prodavan.application.migration.m04e_definition_version
.backfill.export_m05_evidence` is the only place it is computed.

Two values the four ledger tables do not hold, and where they come from instead
==============================================================================

``M05EvidenceExport.gates``
    The six handoff gates are not ledger columns.  They are recorded *once per
    handoff* by ``persist_m05_handoff`` in the redacted ``gate_evidence`` envelope of
    ``m04e_definition_version_promotion_evidence``, and
    :func:`~prodavan.application.migration.evidence_ports.decode_marker_row` reads
    them back.  This reader therefore also reads the partition's stored
    ``MIG-M04E-02`` handoff marker -- it never builds a gate list of its own.  A
    partition whose ledger is complete but whose handoff was never recorded is
    ``m04e_export_handoff_marker_missing``: that is a true statement about the
    partition, not a missing derivation.

``M05EvidenceExport.handoff_marker``
    The same stored marker, attached verbatim, so ``M05UpstreamEvidence
    .ready_for_import`` can re-check it (``marker_ready``) against the rows this read
    actually returned.

The per-record ``checkpoint``
    ``BackfillRecord.checkpoint`` is the cursor the row was classified under.
    ``..._mappings`` stores the page *token* it was committed with and
    ``..._audit`` records, for that token, the page's phase and the sequence it
    started from.  The reconstruction below is therefore read, not invented:

    * ``phase`` and ``sequence`` -- from the audit event of the row's own page token;
    * ``after_source_id`` -- the greatest source identity that page consumed, which is
      exactly the cursor ``SqlM04EEvidenceStore._next_checkpoint`` advances a
      within-phase page to;
    * partition, snapshot lineage and ``writer_generation`` -- from the durable head,
      which is the only row that carries them.

    A mapping row whose token has no committed page event is refused
    (``m04e_export_page_audit_missing``): such a row was not written by a page commit
    and no cursor can be claimed for it.

Fail-closed rules
=================

* **Absence is named, never empty.**  A partition with no ledger row at all is
  ``m04e_export_ledger_empty``; a ledger with no checkpoint head is
  ``m04e_export_checkpoint_missing``.  Neither yields an export with empty tuples.
* **Ambiguity is a refusal.**  Two checkpoint heads for one partition, two different
  handoff markers for one snapshot *and* cursor, two audit events disagreeing about one
  page token, or an observation that contradicts the first mapping it comments on --
  each is a typed refusal, never "take the first row".
* **A divergence is a refusal.**  The ``canonical_digest`` this module recomputes from
  the rows must equal the digest the stored handoff marker recorded, and the marker's
  counts must equal the number of accepted / quarantined records read.  A ledger that
  moved after the handoff, or a handoff recorded over different rows, is
  ``m04e_export_digest_mismatch`` / ``m04e_export_counts_mismatch``.
* **Tenant-scoped twice.**  Every statement carries an explicit ``tenant_id`` (and
  ``definition_id``) predicate *and* runs inside a transaction whose
  ``app.tenant_id`` / ``app.migration_role`` were applied transaction-locally and read
  back, the same shape ``infrastructure/persistence/migration_artifact_store.py``
  uses for its reads.  A policy regression cannot widen the read and an unbound tenant
  cannot be misread as "this partition has no rows".
* **Read-only.**  No statement here writes.  The ledger's append-only triggers are
  never even reached.

Nothing in this module logs, returns or digests a payload: the ledger holds no
payload column in the first place, and a refusal names a code, the partition and (at
most) a source identity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final, Protocol

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.evidence_ports import (
    MigrationEvidenceCorrupt,
    decode_marker_row,
    is_sha256,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    BackfillOutcome,
    BackfillRecord,
    EvidenceStatus,
    M05EvidenceExport,
    M05HandoffMarker,
    export_m05_evidence,
)
from prodavan.application.migration.m04e_ports import (
    checkpoint_from_row,
    record_from_mapping_row,
    record_from_observation_row,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionAuditRow as EVIDENCE_AUDIT_TABLE_MODEL,
)
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionCheckpointRow as EVIDENCE_CHECKPOINT_TABLE_MODEL,
)
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionMappingRow as EVIDENCE_MAPPING_TABLE_MODEL,
)
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionQuarantineRow as EVIDENCE_QUARANTINE_TABLE_MODEL,
)
from prodavan.infrastructure.persistence.rls.policies import TRUSTED_SERVICE_ROLE

# ---------------------------------------------------------------------------
# Roles and vocabulary
# ---------------------------------------------------------------------------

#: Recorded transaction-locally.  A read role, distinct from the writer's
#: ``m04e_backfill``, so an audit of the GUCs can tell a read from a page commit.
CONTEXT_ROLE: Final = "m04e_export_read"

#: The only mapping statuses the ledger can hold (``ck_m04e_mapping_status``) and the
#: ``BackfillOutcome`` each one records.  ``replay`` and ``blocked`` are deliberately
#: absent: the store never writes a replay mapping (a replay appends nothing) and a
#: blocked page is an audit event, so a row carrying either claims a classification the
#: ledger says did not happen.
MAPPING_OUTCOMES: Final[Mapping[str, BackfillOutcome]] = {
    "accepted": BackfillOutcome.ACCEPTED,
    "quarantined": BackfillOutcome.QUARANTINED,
    "conflict": BackfillOutcome.CONFLICT,
}

#: The quarantine row's own lifecycle vocabulary (``ck_m04e_quarantine_status``).
QUARANTINE_STATES: Final[frozenset[str]] = frozenset({"open", "resolved", "archived"})

#: Canonical identity arity per phase (``ck_m04e_mapping_canonical_identity_shape``).
IDENTITY_ARITY: Final[Mapping[str, int]] = {"definition": 2, "version": 3}

#: Columns read from the ledger.  Named once so a test can compare the select list with
#: the models instead of reading three SQL strings.
MAPPING_COLUMNS: Final[tuple[str, ...]] = (
    "id", "tenant_id", "definition_id", "source_kind", "source_id",
    "source_schema_version", "phase", "status", "source_digest", "canonical_digest",
    "canonical_identity", "reason_code", "proof_evidence", "artifact_digest",
    "verified_artifact_digest", "checkpoint", "correlation_id", "recorded_at",
)
QUARANTINE_COLUMNS: Final[tuple[str, ...]] = (
    "id", "tenant_id", "definition_id", "phase", "source_kind", "source_id",
    "source_schema_version", "source_digest", "observed_source_digest",
    "observed_artifact_digest", "reason_code", "blocker_evidence", "checkpoint",
    "status", "correlation_id", "recorded_at",
)
CHECKPOINT_COLUMNS: Final[tuple[str, ...]] = (
    "id", "tenant_id", "definition_id", "phase", "after_source_id", "sequence",
    "phase_complete", "source_snapshot_id", "source_schema_version",
    "source_snapshot_digest", "canonical_schema_version", "writer_generation",
    "checkpoint_token", "updated_at",
)
AUDIT_COLUMNS: Final[tuple[str, ...]] = (
    "id", "checkpoint", "phase", "sequence", "transaction_id", "accepted_count",
    "quarantined_count", "conflict_count", "recorded_at",
)


class M04EExportRefusalCode(StrEnum):
    """Closed vocabulary of every way this reader can refuse to produce an export.

    Each code names *which* fact is missing or contradictory, so an operator does not
    have to parse prose to know whether to finish a backfill, record a handoff, or
    investigate a divergence.
    """

    # -- the question itself -------------------------------------------------
    PARTITION_INVALID = "m04e_export_partition_invalid"
    TENANT_CONTEXT_UNBOUND = "m04e_export_tenant_context_unbound"
    DATABASE_ROLE_NOT_TRUSTED_SERVICE = "m04e_export_database_role_is_not_trusted_service"
    ROW_SECURITY_DISABLED = "m04e_export_row_security_disabled"
    SESSION_FACTORY_MISSING = "m04e_export_session_factory_missing"
    DIALECT_UNSUPPORTED = "m04e_export_dialect_unsupported"
    SNAPSHOT_MISMATCH = "m04e_export_snapshot_mismatch"
    # -- the ledger ----------------------------------------------------------
    LEDGER_EMPTY = "m04e_export_ledger_empty"
    CHECKPOINT_MISSING = "m04e_export_checkpoint_missing"
    CHECKPOINT_AMBIGUOUS = "m04e_export_checkpoint_ambiguous"
    CHECKPOINT_INCOMPLETE = "m04e_export_checkpoint_incomplete"
    CHECKPOINT_LINEAGE_INVALID = "m04e_export_checkpoint_lineage_invalid"
    MAPPING_STATUS_UNKNOWN = "m04e_export_mapping_status_unknown"
    MAPPING_ACCEPTED_INCOMPLETE = "m04e_export_accepted_mapping_incomplete"
    OBSERVATION_WITHOUT_MAPPING = "m04e_export_observation_without_mapping"
    OBSERVATION_CONTRADICTS_MAPPING = "m04e_export_observation_contradicts_mapping"
    OBSERVATION_STATUS_UNKNOWN = "m04e_export_observation_status_unknown"
    PAGE_AUDIT_MISSING = "m04e_export_page_audit_missing"
    PAGE_AUDIT_AMBIGUOUS = "m04e_export_page_audit_ambiguous"
    # -- the stored handoff --------------------------------------------------
    HANDOFF_MARKER_MISSING = "m04e_export_handoff_marker_missing"
    HANDOFF_MARKER_AMBIGUOUS = "m04e_export_handoff_marker_ambiguous"
    HANDOFF_MARKER_CORRUPT = "m04e_export_handoff_marker_corrupt"
    CHECKPOINT_MISMATCH = "m04e_export_checkpoint_mismatch"
    DIGEST_MISMATCH = "m04e_export_digest_mismatch"
    COUNTS_MISMATCH = "m04e_export_counts_mismatch"


@dataclass(frozen=True, slots=True)
class M04EExportRefusal:
    """One named, redacted reason the export could not be produced.

    Falsy by construction, so ``if refusal:`` reads as "there is an export".
    """

    code: M04EExportRefusalCode
    detail: str
    needed: str
    partition: tuple[str, str] | None = None

    def __bool__(self) -> bool:
        return False

    def evidence_text(self) -> str:
        """Redacted single line, suitable for an operator log or a blocker report."""

        partition = ""
        if self.partition is not None:
            partition = f" partition=({self.partition[0]},{self.partition[1]})"
        return f"{self.code.value}{partition}: {self.detail}"


class M04EExportRefused(RuntimeError):
    """A typed refusal raised by the reader.  Never converted into a partial export."""

    def __init__(self, refusal: M04EExportRefusal) -> None:
        self.refusal = refusal
        super().__init__(f"{refusal.evidence_text()} needed: {refusal.needed}")

    @property
    def code(self) -> M04EExportRefusalCode:
        return self.refusal.code

    @property
    def detail(self) -> str:
        return self.refusal.detail

    @property
    def needed(self) -> str:
        return self.refusal.needed


def _refuse(
    code: M04EExportRefusalCode,
    detail: str,
    needed: str,
    *,
    partition: tuple[str, str] | None = None,
) -> M04EExportRefused:
    return M04EExportRefused(
        M04EExportRefusal(code=code, detail=detail, needed=needed, partition=partition)
    )


# ---------------------------------------------------------------------------
# SQL: four explicit, tenant-predicated reads
# ---------------------------------------------------------------------------

_APPLY_CONTEXT_SQL = text(
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role, "
    "set_config('app.correlation_id', :correlation_id, true) AS correlation_id, "
    "set_config('app.migration_role', 'true', true) AS migration_role"
)

_READBACK_CONTEXT_SQL = text(
    "SELECT "
    "current_setting('app.tenant_id', true) AS tenant_id, "
    "current_setting('app.migration_role', true) AS migration_role, "
    "current_setting('row_security', true) AS row_security, "
    "current_user AS database_role, "
    "(SELECT r.rolsuper OR r.rolbypassrls FROM pg_roles AS r WHERE r.rolname = current_user) "
    "AS role_bypasses_rls"
)

#: The four reads, as typed selects over the **registered models** rather than raw text.
#: Two reasons, both load-bearing: a typed select decodes the two ``JSONB`` columns
#: (``canonical_identity``, ``proof_evidence``) into the list/dict the contract's record
#: carries -- raw text would hand back their serialised form and the record would be built
#: from a string -- and it keeps the select list derived from the model, so a renamed column
#: fails loudly instead of reading ``None``.
#:
#: Each one still carries the explicit ``tenant_id``/``definition_id`` predicate in addition
#: to row security: a policy regression must not be able to widen the partition.
def select_mappings() -> Any:
    table = EVIDENCE_MAPPING_TABLE_MODEL.__table__
    return (
        sa.select(table)
        .where(sa.and_(
            table.c.tenant_id == sa.bindparam("tenant_id"),
            table.c.definition_id == sa.bindparam("definition_id"),
        ))
        .order_by(table.c.phase, table.c.source_id, table.c.source_kind,
                  table.c.source_schema_version, table.c.id)
    )


def select_observations() -> Any:
    table = EVIDENCE_QUARANTINE_TABLE_MODEL.__table__
    return (
        sa.select(table)
        .where(sa.and_(
            table.c.tenant_id == sa.bindparam("tenant_id"),
            table.c.definition_id == sa.bindparam("definition_id"),
        ))
        .order_by(table.c.source_id, table.c.source_kind, table.c.source_schema_version,
                  table.c.id)
    )


def select_checkpoints() -> Any:
    """``LIMIT 2``: enough to tell "one head" from "ambiguous" -- the reader refuses two."""
    table = EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__
    return (
        sa.select(table)
        .where(sa.and_(
            table.c.tenant_id == sa.bindparam("tenant_id"),
            table.c.definition_id == sa.bindparam("definition_id"),
        ))
        .order_by(table.c.phase, table.c.id)
        .limit(2)
    )


def select_page_audit() -> Any:
    """Committed pages only: a blocked page wrote no classification, so it can never
    explain a mapping row's page token."""
    table = EVIDENCE_AUDIT_TABLE_MODEL.__table__
    return (
        sa.select(table)
        .where(sa.and_(
            table.c.tenant_id == sa.bindparam("tenant_id"),
            table.c.definition_id == sa.bindparam("definition_id"),
            table.c.event_kind == "page_committed",
        ))
        .order_by(table.c.recorded_at, table.c.id)
    )


def _rows(result: Any) -> tuple[Mapping[str, Any], ...]:
    return tuple(result.mappings().all())


def _require_partition(tenant_id: object, app_definition_id: object) -> tuple[str, str]:
    for name, value in (
        ("tenant_id", tenant_id),
        ("app_definition_id", app_definition_id),
    ):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise _refuse(
                M04EExportRefusalCode.PARTITION_INVALID,
                f"{name} must be a non-empty, unpadded identifier",
                "the exact (tenant_id, app_definition_id) partition the ledger is keyed by",
            )
    return str(tenant_id), str(app_definition_id)


# ---------------------------------------------------------------------------
# Read result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class M04EExportLedger:
    """Everything the four ledger tables say about one partition, as records.

    ``records`` is ``accepted`` followed by the non-accepted rows (non-accepted
    mappings then their observations), which is the order :func:`export_m05_evidence`
    is fed and the order it preserves.  The order is a pure function of the stored
    keys, so two reads of an unchanged ledger are equal values.
    """

    tenant_id: str
    app_definition_id: str
    checkpoint: BackfillCheckpoint
    accepted: tuple[BackfillRecord, ...]
    quarantined: tuple[BackfillRecord, ...]
    #: ``(page_token, transaction_id)`` of every committed page that wrote a read row.
    page_transactions: tuple[tuple[str, str], ...] = ()

    @property
    def records(self) -> tuple[BackfillRecord, ...]:
        return (*self.accepted, *self.quarantined)

    @property
    def counts(self) -> Mapping[str, int]:
        return {"accepted": len(self.accepted), "quarantined": len(self.quarantined)}


class M04EEvidenceExportReader(Protocol):
    """The port ``m05_contexts`` consumes: one partition in, its export or a refusal."""

    async def read_export(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        source_snapshot_digest: str | None = None,
    ) -> M05EvidenceExport: ...


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Page:
    """One committed page event, as the audit ledger records it."""

    token: str
    phase: str
    sequence: int
    transaction_id: str


@dataclass(frozen=True, slots=True)
class _PageCursor:
    """The cursor a page's rows were classified under, plus that page's transaction.

    ``record.checkpoint`` and ``record.transaction_id`` come from the same event, so they
    are resolved together: a record whose cursor and whose transaction describe two
    different pages would be exported as evidence that never existed.
    """

    checkpoint: BackfillCheckpoint
    transaction_id: str


class SqlM04EEvidenceExportReader:
    """``M05EvidenceExport`` of one partition, read out of the M04E ledger.

    One call owns one transaction over the injected ``async_sessionmaker``; nothing is
    cached between calls, so a pooled connection cannot carry one partition's tenant
    into the next read.

    ``allow_non_postgresql`` exists for the **test harness only** -- the SQLite mirror
    in ``tests/migration/m04e_export`` reproduces the ledger tables so the assembly and
    its refusals are executable, but SQLite has neither transaction-local GUCs nor row
    security.  The flag defaults to off, so a production composition root cannot read
    the ledger without the RLS binding: the first call raises a named refusal.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        allow_non_postgresql: bool = False,
    ) -> None:
        if session_factory is None:
            raise ValueError(M04EExportRefusalCode.SESSION_FACTORY_MISSING.value)
        self._session_factory = session_factory
        self._allow_non_postgresql = allow_non_postgresql

    # -- public reads -------------------------------------------------------

    async def read_ledger(
        self, *, tenant_id: str, app_definition_id: str,
    ) -> M04EExportLedger:
        """The partition's accepted and quarantined records plus its durable head.

        Raises :class:`M04EExportRefused` for every state in which those records cannot
        be turned into the export the M04E contract hands to M05: an empty ledger, a
        missing/ambiguous/incomplete cursor, a row that contradicts another row.
        """

        partition = _require_partition(tenant_id, app_definition_id)
        async with self._session_factory() as session:
            async with session.begin():
                self._require_dialect(session, partition, allow_non_postgresql=self._allow_non_postgresql)
                await self._bind_tenant(session, partition)
                parameters = {"tenant_id": partition[0], "definition_id": partition[1]}
                mappings = _rows(await session.execute(select_mappings(), parameters))
                observations = _rows(
                    await session.execute(select_observations(), parameters)
                )
                heads = _rows(await session.execute(select_checkpoints(), parameters))
                audits = _rows(await session.execute(select_page_audit(), parameters))
        return self._assemble(
            partition, mappings=mappings, observations=observations, heads=heads,
            audits=audits,
        )

    async def read_export(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        source_snapshot_digest: str | None = None,
    ) -> M05EvidenceExport:
        """The partition's ``M05EvidenceExport``, recomputed from the rows it holds.

        ``canonical_digest`` comes from
        :func:`~prodavan.application.migration.m04e_definition_version.backfill
        .export_m05_evidence` -- the same function a freshly committed page is exported
        with -- and must equal the digest the stored ``MIG-M04E-02`` marker recorded.
        ``source_snapshot_digest`` pins the snapshot the caller expects; a ledger of
        another snapshot is refused rather than silently exported.
        """

        partition = _require_partition(tenant_id, app_definition_id)
        async with self._session_factory() as session:
            async with session.begin():
                self._require_dialect(session, partition, allow_non_postgresql=self._allow_non_postgresql)
                await self._bind_tenant(session, partition)
                parameters = {"tenant_id": partition[0], "definition_id": partition[1]}
                mappings = _rows(await session.execute(select_mappings(), parameters))
                observations = _rows(
                    await session.execute(select_observations(), parameters)
                )
                heads = _rows(await session.execute(select_checkpoints(), parameters))
                audits = _rows(await session.execute(select_page_audit(), parameters))
                markers = await self._marker_rows(session, partition)

        ledger = self._assemble(
            partition, mappings=mappings, observations=observations, heads=heads,
            audits=audits,
        )
        if source_snapshot_digest is not None and (
            source_snapshot_digest != ledger.checkpoint.source_snapshot_digest
        ):
            raise _refuse(
                M04EExportRefusalCode.SNAPSHOT_MISMATCH,
                "the ledger's durable head is bound to a different source snapshot than "
                "the one the caller asked for",
                "the export of the snapshot the caller expects, or the caller's snapshot "
                "corrected to the one the checkpoint records",
                partition=partition,
            )
        marker = self._select_marker(partition, checkpoint=ledger.checkpoint, rows=markers)
        # ``export_m05_evidence`` is the contract's own function: it recomputes
        # ``canonical_digest`` from the accepted rows and decides READY/BLOCKED.  This
        # reader supplies rows and the recorded gates -- it computes neither itself.
        export = export_m05_evidence(
            tenant_id=partition[0],
            app_definition_id=partition[1],
            records=ledger.records,
            source_snapshot_digest=ledger.checkpoint.source_snapshot_digest,
            checkpoint=ledger.checkpoint,
            gates=marker.gate_evidence,
        )
        self._require_same_evidence(partition, export=export, marker=marker)
        return replace(export, handoff_marker=marker)

    # -- transaction ---------------------------------------------------------

    @staticmethod
    def _require_dialect(
        session: AsyncSession, partition: tuple[str, str], *, allow_non_postgresql: bool,
    ) -> None:
        bind = session.get_bind()
        dialect = str(getattr(getattr(bind, "dialect", None), "name", "")) or "unknown"
        if dialect != "postgresql" and not allow_non_postgresql:
            raise _refuse(
                M04EExportRefusalCode.DIALECT_UNSUPPORTED,
                "the M04E ledger is read under a transaction-local tenant context and "
                f"row security; the bound dialect is {dialect!r}",
                "a PostgreSQL session factory, or allow_non_postgresql for the SQLite "
                "acceptance mirror",
                partition=partition,
            )

    async def _bind_tenant(
        self, session: AsyncSession, partition: tuple[str, str],
    ) -> None:
        """Apply and prove the transaction-local tenant context before any table read.

        Same shape as ``migration_artifact_store.SqlArtifactContentStore._bind_tenant``:
        an unbound tenant would make the ``FORCE ROW LEVEL SECURITY`` ledger return
        zero rows, which this reader would otherwise report as "the partition has no
        ledger" -- a false finding about a partition that may well have one.
        """

        if self._allow_non_postgresql and not self._is_postgresql(session):
            return
        await session.execute(_APPLY_CONTEXT_SQL, {
            "tenant_id": partition[0],
            "user_id": "migration-operator",
            "role": CONTEXT_ROLE,
            "correlation_id": f"m04e-export-read:{partition[0]}:{partition[1]}",
        })
        readback = (await session.execute(_READBACK_CONTEXT_SQL)).mappings().one()
        if str(readback.get("tenant_id") or "") != partition[0]:
            raise _refuse(
                M04EExportRefusalCode.TENANT_CONTEXT_UNBOUND,
                "the transaction-local tenant setting does not match the requested tenant",
                "a transaction that can bind app.tenant_id transaction-locally",
                partition=partition,
            )
        if str(readback.get("migration_role") or "") != "true":
            raise _refuse(
                M04EExportRefusalCode.TENANT_CONTEXT_UNBOUND,
                "the migration role marker was not applied transaction-locally",
                "a transaction whose app.migration_role reads back as true",
                partition=partition,
            )
        if str(readback.get("row_security") or "").lower() != "on":
            raise _refuse(
                M04EExportRefusalCode.ROW_SECURITY_DISABLED,
                "row_security is not on; the M04E ledger policies would be skipped",
                "a connection where row security is enforced",
                partition=partition,
            )
        # The tenant predicate is defence in depth, not the boundary: the boundary is the
        # reviewed policy, and it grants nothing to any role but the trusted one. A session
        # running as anything else never reaches this reader today -- but that is a property
        # of today's composition root, not of this module, and a reader that trusts its
        # caller to have chosen the right role would silently read across tenants the day
        # someone wired it elsewhere.
        if str(readback.get("database_role") or "") != TRUSTED_SERVICE_ROLE:
            raise _refuse(
                M04EExportRefusalCode.DATABASE_ROLE_NOT_TRUSTED_SERVICE,
                f"the reader must run as {TRUSTED_SERVICE_ROLE!r}; the trusted-service policy "
                "grants nothing to any other role",
                f"a session authenticated as {TRUSTED_SERVICE_ROLE}",
                partition=partition,
            )
        if bool(readback.get("role_bypasses_rls")):
            raise _refuse(
                M04EExportRefusalCode.ROW_SECURITY_DISABLED,
                "the database role is a superuser or has BYPASSRLS, so the reviewed policies "
                "would not bound this read",
                "a role that is neither superuser nor BYPASSRLS",
                partition=partition,
            )

    @staticmethod
    def _is_postgresql(session: AsyncSession) -> bool:
        bind = session.get_bind()
        return str(getattr(getattr(bind, "dialect", None), "name", "")) == "postgresql"

    # -- stored handoff marker ----------------------------------------------

    async def _marker_rows(
        self, session: AsyncSession, partition: tuple[str, str],
    ) -> tuple[M05HandoffMarker, ...]:
        """Every stored ``MIG-M04E-02`` handoff marker of the partition, oldest first.

        Read through the same model and the same decoder the writer uses, so the reader
        and ``persist_m05_handoff`` can never disagree about what a stored marker says.
        Rows of another envelope schema (the M04E *page* rows
        ``SqlM04EBackfillEvidenceStore`` writes into the same table) decode to ``None``
        and are simply not markers; a marker envelope that cannot be decoded is a
        refusal rather than a silently skipped row.
        """

        result = await session.execute(
            sa.select(M04EDefinitionVersionPromotionEvidenceRow)
            .where(
                M04EDefinitionVersionPromotionEvidenceRow.tenant_id == partition[0],
                M04EDefinitionVersionPromotionEvidenceRow.definition_id == partition[1],
            )
            .order_by(
                M04EDefinitionVersionPromotionEvidenceRow.recorded_at,
                M04EDefinitionVersionPromotionEvidenceRow.id,
            )
        )
        markers: list[M05HandoffMarker] = []
        for row in result.scalars():
            try:
                decoded = decode_marker_row(row)
            except MigrationEvidenceCorrupt as exc:
                raise _refuse(
                    M04EExportRefusalCode.HANDOFF_MARKER_CORRUPT,
                    "a stored M04E handoff evidence row cannot be decoded into its own "
                    f"contract type ({exc})",
                    "a handoff marker written by persist_m05_handoff, or its row repaired "
                    "forward by a reviewed revision",
                    partition=partition,
                ) from exc
            if decoded is not None:
                markers.append(decoded)
        return tuple(markers)

    def _select_marker(
        self,
        partition: tuple[str, str],
        *,
        checkpoint: BackfillCheckpoint,
        rows: Sequence[M05HandoffMarker],
    ) -> M05HandoffMarker:
        """The one stored handoff marker that describes *this* cursor of *this* snapshot."""

        if not rows:
            raise _refuse(
                M04EExportRefusalCode.HANDOFF_MARKER_MISSING,
                "the partition has no stored MIG-M04E-02 handoff marker, so nothing "
                "attests the six handoff gates the export must carry",
                "a MIG-M04E-02 marker recorded by persist_m05_handoff for this "
                "partition's completed export",
                partition=partition,
            )
        same_snapshot = tuple(
            row for row in rows
            if row.source_snapshot_digest == checkpoint.source_snapshot_digest
        )
        if not same_snapshot:
            raise _refuse(
                M04EExportRefusalCode.HANDOFF_MARKER_MISSING,
                "the ledger's durable head belongs to a snapshot no stored handoff marker "
                "describes; the handoff recorded for another snapshot cannot be reused",
                "a MIG-M04E-02 marker for this ledger's source snapshot",
                partition=partition,
            )
        exact = tuple(row for row in same_snapshot if row.checkpoint == checkpoint)
        distinct = tuple(dict.fromkeys(exact))
        if len(distinct) > 1:
            raise _refuse(
                M04EExportRefusalCode.HANDOFF_MARKER_AMBIGUOUS,
                "several stored handoff markers claim this snapshot and cursor but record "
                "different evidence",
                "exactly one MIG-M04E-02 marker for this snapshot and checkpoint",
                partition=partition,
            )
        if not distinct:
            raise _refuse(
                M04EExportRefusalCode.CHECKPOINT_MISMATCH,
                "the ledger's durable cursor is not the cursor the stored handoff marker "
                "recorded: the ledger moved after the handoff",
                "a handoff marker re-recorded over the current checkpoint",
                partition=partition,
            )
        return distinct[0]

    # -- assembly ------------------------------------------------------------

    def _assemble(
        self,
        partition: tuple[str, str],
        *,
        mappings: Sequence[Mapping[str, Any]],
        observations: Sequence[Mapping[str, Any]],
        heads: Sequence[Mapping[str, Any]],
        audits: Sequence[Mapping[str, Any]],
    ) -> M04EExportLedger:
        """Turn the four reads into records, or name the fact that forbids it."""

        if not mappings and not observations and not heads:
            raise _refuse(
                M04EExportRefusalCode.LEDGER_EMPTY,
                "the partition has no row in any of the four M04E ledger tables, so there "
                "are no accepted rows to export and no cursor to export them under",
                "an M04E backfill page committed for this partition (mappings, "
                "checkpoint and audit), then a MIG-M04E-02 handoff recorded over it",
                partition=partition,
            )

        checkpoint = self._require_head(partition, heads=heads)
        pages = self._pages(partition, audits=audits)
        consumed = self._consumed_by_page(mappings)
        cursors = {
            token: _PageCursor(
                checkpoint=self._page_checkpoint(
                    checkpoint, page=page, consumed=consumed.get(token),
                ),
                transaction_id=page.transaction_id,
            )
            for token, page in pages.items()
        }

        accepted: list[BackfillRecord] = []
        quarantined: list[BackfillRecord] = []
        recorded_by_source: dict[tuple[str, str, str], Mapping[str, Any]] = {}

        for row in mappings:
            record = self._mapping_record(partition, row, cursors=cursors)
            recorded_by_source[_source_key(record)] = row
            (accepted if record.status is EvidenceStatus.ACCEPTED else quarantined).append(
                record
            )

        for row in observations:
            quarantined.append(
                self._observation_record(
                    partition,
                    row,
                    recorded=recorded_by_source,
                    cursors=cursors,
                )
            )

        return M04EExportLedger(
            tenant_id=partition[0],
            app_definition_id=partition[1],
            checkpoint=checkpoint,
            accepted=tuple(accepted),
            quarantined=tuple(quarantined),
            page_transactions=tuple(
                (token, page.transaction_id) for token, page in pages.items()
            ),
        )

    def _require_head(
        self, partition: tuple[str, str], *, heads: Sequence[Mapping[str, Any]],
    ) -> BackfillCheckpoint:
        """The partition's single durable head, complete and digest-bound."""

        if not heads:
            raise _refuse(
                M04EExportRefusalCode.CHECKPOINT_MISSING,
                "no checkpoint head exists for this partition, so no cursor can bind the "
                "records into an export",
                "the durable checkpoint row an M04E page commit creates",
                partition=partition,
            )
        if len(heads) > 1:
            raise _refuse(
                M04EExportRefusalCode.CHECKPOINT_AMBIGUOUS,
                "more than one checkpoint head exists for this partition; the export "
                "cannot say which cursor it represents",
                "exactly one checkpoint head per (tenant, definition)",
                partition=partition,
            )
        row = heads[0]
        checkpoint = checkpoint_from_row(row)
        if checkpoint.partition != partition:
            raise _refuse(
                M04EExportRefusalCode.CHECKPOINT_LINEAGE_INVALID,
                "the checkpoint head belongs to another partition than the one requested",
                "the checkpoint head of this exact partition",
                partition=partition,
            )
        if not is_sha256(checkpoint.source_snapshot_digest):
            raise _refuse(
                M04EExportRefusalCode.CHECKPOINT_LINEAGE_INVALID,
                "the checkpoint head's source snapshot digest is not a sha256 digest",
                "a checkpoint written by a page commit (ck_m04e_checkpoint_source_sha256)",
                partition=partition,
            )
        for name in ("source_snapshot_id", "source_schema_version",
                     "canonical_schema_version"):
            if not str(getattr(checkpoint, name) or "").strip():
                raise _refuse(
                    M04EExportRefusalCode.CHECKPOINT_LINEAGE_INVALID,
                    f"the checkpoint head carries an empty {name}, so the export could not "
                    "state its lineage",
                    "a checkpoint with a complete snapshot/schema lineage",
                    partition=partition,
                )
        if checkpoint.phase != "version" or not checkpoint.phase_complete:
            raise _refuse(
                M04EExportRefusalCode.CHECKPOINT_INCOMPLETE,
                f"the durable cursor reads phase={checkpoint.phase!r} with "
                f"phase_complete={checkpoint.phase_complete!r}: the definition/version "
                "stream is not drained, so an export of it would hand M05 a partial "
                "snapshot",
                "a partition whose version phase is exhausted (phase='version', "
                "phase_complete=true)",
                partition=partition,
            )
        return checkpoint

    def _pages(
        self, partition: tuple[str, str], *, audits: Sequence[Mapping[str, Any]],
    ) -> dict[str, _Page]:
        """Page token -> the committed page event that carries it."""

        pages: dict[str, _Page] = {}
        for row in audits:
            token = str(row.get("checkpoint") or "")
            page = _Page(
                token=token,
                phase=str(row["phase"]),
                sequence=int(row["sequence"]),
                transaction_id=str(row["transaction_id"]),
            )
            existing = pages.get(token)
            if existing is not None and existing != page:
                raise _refuse(
                    M04EExportRefusalCode.PAGE_AUDIT_AMBIGUOUS,
                    "two committed page events report different phase, sequence or "
                    "transaction for one page token",
                    "one audit event per committed page token",
                    partition=partition,
                )
            pages.setdefault(token, page)
        return pages

    @staticmethod
    def _consumed_by_page(
        mappings: Sequence[Mapping[str, Any]],
    ) -> dict[str, str]:
        """Page token -> the greatest source identity that page consumed.

        ``SqlM04EEvidenceStore._next_checkpoint`` sets the within-phase cursor to the
        last source identity a page consumed, and pages are ordered, so the greatest
        identity of a page's rows *is* that cursor.  Deriving it from the rows keeps it
        a read: the audit ledger keeps the page token and the starting sequence, not the
        cursor the page advanced to.
        """

        consumed: dict[str, str] = {}
        for row in mappings:
            token = str(row.get("checkpoint") or "")
            source_id = str(row["source_id"])
            if token not in consumed or source_id > consumed[token]:
                consumed[token] = source_id
        return consumed

    def _page_checkpoint(
        self,
        head: BackfillCheckpoint,
        *,
        page: _Page,
        consumed: str | None,
    ) -> BackfillCheckpoint:
        """The cursor one page's rows were classified under, read from the audit event.

        Partition, snapshot lineage and writer generation are the head's (they are
        immutable per partition); ``phase`` and ``sequence`` are the page's own;
        ``phase_complete`` is only ever true for the head phase.
        """

        return BackfillCheckpoint(
            partition=head.partition,
            phase=page.phase,
            after_source_id=consumed,
            sequence=page.sequence,
            source_snapshot_id=head.source_snapshot_id,
            source_schema_version=head.source_schema_version,
            source_snapshot_digest=head.source_snapshot_digest,
            canonical_schema_version=head.canonical_schema_version,
            writer_generation=head.writer_generation,
            phase_complete=head.phase_complete if page.phase == head.phase else False,
        )

    def _mapping_record(
        self,
        partition: tuple[str, str],
        row: Mapping[str, Any],
        *,
        cursors: Mapping[str, _PageCursor],
    ) -> BackfillRecord:
        """One mapping row as the contract's record, or a named refusal."""

        token = str(row.get("checkpoint") or "")
        status = str(row.get("status") or "")
        outcome = MAPPING_OUTCOMES.get(status)
        if outcome is None:
            # ``replay`` and ``blocked`` are in ck_m04e_mapping_status but a page commit
            # never writes them: a replay appends nothing and a blocked page is an audit
            # event.  A row claiming either cannot be classified truthfully.
            raise _refuse(
                M04EExportRefusalCode.MAPPING_STATUS_UNKNOWN,
                f"a ledger mapping row records status {status!r}, which no page commit "
                "writes; the row cannot be exported as a classification",
                "the statuses a page commit records: accepted, quarantined, conflict",
                partition=partition,
            )
        cursor = self._cursor_for(partition, token=token, cursors=cursors)
        record = record_from_mapping_row(row, checkpoint=cursor.checkpoint, outcome=outcome)
        record = replace(record, transaction_id=cursor.transaction_id)
        if outcome is BackfillOutcome.ACCEPTED:
            self._require_accepted_shape(partition, record=record, row=row)
        return record

    def _observation_record(
        self,
        partition: tuple[str, str],
        row: Mapping[str, Any],
        *,
        recorded: Mapping[tuple[str, str, str], Mapping[str, Any]],
        cursors: Mapping[str, _PageCursor],
    ) -> BackfillRecord:
        """One changed-digest observation, checked against the mapping it contradicts."""

        key = (
            str(row["source_kind"]),
            str(row["source_id"]),
            str(row["source_schema_version"]),
        )
        mapping = recorded.get(key)
        if mapping is None:
            raise _refuse(
                M04EExportRefusalCode.OBSERVATION_WITHOUT_MAPPING,
                f"a quarantine observation names source {key[1]!r} of kind {key[0]!r} "
                "for which the ledger holds no first mapping",
                "a first mapping for every observed source identity "
                "(uq_m04e_quarantine_observation references one)",
                partition=partition,
            )
        if str(mapping["source_digest"]) != str(row["source_digest"]):
            raise _refuse(
                M04EExportRefusalCode.OBSERVATION_CONTRADICTS_MAPPING,
                f"a quarantine observation for source {key[1]!r} records a different "
                "authoritative source digest than the first mapping it comments on",
                "an observation whose recorded source digest is the first mapping's "
                "(the store derives it from that row)",
                partition=partition,
            )
        status = str(row.get("status") or "")
        if status not in QUARANTINE_STATES:
            raise _refuse(
                M04EExportRefusalCode.OBSERVATION_STATUS_UNKNOWN,
                f"a quarantine observation records status {status!r}, which is outside "
                "the ledger's own vocabulary",
                f"one of {sorted(QUARANTINE_STATES)} (ck_m04e_quarantine_status)",
                partition=partition,
            )
        token = str(row.get("checkpoint") or "")
        cursor = self._cursor_for(partition, token=token, cursors=cursors)
        record = record_from_observation_row(
            row,
            checkpoint=cursor.checkpoint,
            reason_code=str(row["reason_code"]),
        )
        return replace(record, transaction_id=cursor.transaction_id)

    def _cursor_for(
        self,
        partition: tuple[str, str],
        *,
        token: str,
        cursors: Mapping[str, _PageCursor],
    ) -> _PageCursor:
        cursor = cursors.get(token)
        if cursor is None:
            raise _refuse(
                M04EExportRefusalCode.PAGE_AUDIT_MISSING,
                "a ledger row carries a page token no committed page event records, so "
                "the cursor it was classified under cannot be stated",
                "a page_committed audit event for every page token the ledger holds",
                partition=partition,
            )
        return cursor

    def _require_accepted_shape(
        self,
        partition: tuple[str, str],
        *,
        record: BackfillRecord,
        row: Mapping[str, Any],
    ) -> None:
        """Re-assert what an accepted row must carry before it can be counted as proof.

        The database CHECKs already guarantee it (``ck_m04e_mapping_accepted_has_canonical_digest``,
        ``ck_m04e_mapping_canonical_identity_shape``, ``ck_m04e_mapping_proof_evidence_object``).
        Re-asserting it here keeps a row written by a writer that no longer honours those
        constraints from being exported as acceptance: the export is what M05 imports.

        The identity arity differs by phase, and so does what "this partition" means inside
        it: a definition's identity is ``(tenant_id, app_id)`` -- its own id *is* the
        definition -- while a version's is ``(tenant_id, definition_id, manifest_version)``.
        Both are checked exactly as ``M05UpstreamEvidence.accepted_record_is_bound`` checks
        them, because that predicate is what decides an import.
        """

        arity = IDENTITY_ARITY.get(str(record.phase))
        identity = record.canonical_identity
        problem: str | None = None
        if not is_sha256(row.get("canonical_digest")):
            problem = "its canonical digest is not a sha256 digest"
        elif not is_sha256(row.get("source_digest")):
            problem = "its source digest is not a sha256 digest"
        elif arity is None:
            problem = f"its phase {record.phase!r} is neither definition nor version"
        elif len(identity) != arity:
            problem = (
                f"its canonical identity has {len(identity)} parts, and a "
                f"{record.phase} identity must have {arity}"
            )
        elif not identity[0] == partition[0]:
            problem = "its canonical identity is bound to another tenant"
        elif record.phase == "version" and identity[1] != partition[1]:
            problem = "its canonical identity names another definition"
        elif not all(isinstance(part, str) and part.strip() for part in identity):
            problem = "its canonical identity carries an empty part"
        elif not isinstance(row.get("proof_evidence"), Mapping):
            problem = "its proof evidence is not a redacted digest object"
        if problem is not None:
            raise _refuse(
                M04EExportRefusalCode.MAPPING_ACCEPTED_INCOMPLETE,
                f"an accepted mapping row cannot be exported as acceptance because {problem}",
                "an accepted row carrying its canonical digest, typed identity and named "
                "proof digests",
                partition=partition,
            )

    # -- the export against the stored handoff ------------------------------

    def _require_same_evidence(
        self,
        partition: tuple[str, str],
        *,
        export: M05EvidenceExport,
        marker: M05HandoffMarker,
    ) -> None:
        """The recomputed export must *be* the evidence the stored handoff recorded."""

        if marker.canonical_digest != export.canonical_digest:
            raise _refuse(
                M04EExportRefusalCode.DIGEST_MISMATCH,
                "the canonical digest recomputed from the ledger rows is not the digest "
                "the stored handoff marker recorded: the rows behind the handoff are not "
                "the rows in the ledger",
                "a handoff marker recorded over exactly the accepted rows the ledger holds",
                partition=partition,
            )
        if marker.source_snapshot_digest != export.source_snapshot_digest:
            raise _refuse(
                M04EExportRefusalCode.SNAPSHOT_MISMATCH,
                "the stored handoff marker is bound to a different source snapshot than "
                "the ledger's head",
                "a handoff marker of this ledger's snapshot",
                partition=partition,
            )
        if (
            marker.accepted_count != len(export.accepted)
            or marker.quarantined_count != len(export.quarantined)
        ):
            raise _refuse(
                M04EExportRefusalCode.COUNTS_MISMATCH,
                "the stored handoff marker counts "
                f"{marker.accepted_count} accepted / {marker.quarantined_count} "
                f"quarantined rows, but the ledger holds {len(export.accepted)} / "
                f"{len(export.quarantined)}: rows were appended after the handoff",
                "a handoff marker re-recorded over the current ledger",
                partition=partition,
            )


def _source_key(record: BackfillRecord) -> tuple[str, str, str]:
    return (record.source_kind, record.source_id, record.schema_version)


def build_m04e_export_reader(
    *, session_factory: async_sessionmaker[AsyncSession] | None,
) -> SqlM04EEvidenceExportReader | None:
    """The ledger reader for a composition root, or ``None`` when it cannot exist.

    ``None`` is the honest answer for "no session factory": there is no in-memory
    ledger, and an empty export would be the fabrication this port exists to prevent.
    """

    if session_factory is None:
        return None
    return SqlM04EEvidenceExportReader(session_factory=session_factory)


__all__ = [
    "AUDIT_COLUMNS",
    "CHECKPOINT_COLUMNS",
    "CONTEXT_ROLE",
    "IDENTITY_ARITY",
    "MAPPING_COLUMNS",
    "MAPPING_OUTCOMES",
    "M04EEvidenceExportReader",
    "M04EExportLedger",
    "M04EExportRefusal",
    "M04EExportRefusalCode",
    "M04EExportRefused",
    "QUARANTINE_COLUMNS",
    "QUARANTINE_STATES",
    "select_checkpoints",
    "select_mappings",
    "select_observations",
    "select_page_audit",
    "SqlM04EEvidenceExportReader",
    "build_m04e_export_reader",
]
