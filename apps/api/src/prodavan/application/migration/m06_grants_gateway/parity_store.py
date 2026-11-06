"""Durable run-key registry for M06 parity reports.

A parity report is review evidence, never an authorization: it records what one
bounded shadow window observed for one partition and cannot promote anything by
itself. The registry therefore lives in the already reviewed immutable
review-evidence table instead of a new table:

``m06_canonical_grant_promotion_evidence`` (revision ``2026110304``,
``models/canonical_grants.py::M06CanonicalGrantPromotionEvidenceRow``).

* ``uq_m06_grant_promotion_revision`` on
  ``(tenant_id, definition_id, partition_id, revision_id)`` *is* the run-key
  registry: ``revision_id`` is the deterministic run key and ``partition_id`` is
  derived from ``(tenant_id, app_definition_id)``, so one run key can only ever
  have one row and a second insert fails in the database, not in a
  select-then-insert race;
* ``gate_evidence`` (JSONB) carries the byte-exact ``ParityRunReport.export()``
  text under ``export`` next to the report digest, so a read returns the original
  bytes instead of a re-rendered approximation;
* ``ck_m06_grant_no_positive_writes`` plus ``decision_owner`` keep the ownership
  invariant inside the database: a parity row cannot claim canonical positive
  writes;
* ``outcome`` mirrors the report status (``ready`` for ``ready_for_review``,
  ``blocked`` for ``blocked``). ``incomplete_partition`` is refused outright: a
  partial traversal is never a partition conclusion;
* ``reviewer`` stays NULL and ``gate_evidence.promotion_decision`` stays false:
  a parity row is evidence *for* a later review, not the review.

Why not the audit stream: the audit table is the shadow-observation read surface
and stores redacted fragments, while a parity report is one document that must be
returned byte-identically. A separate immutable row keeps both evidence streams
readable without mixing them.

No Alembic revision is required: the table, its unique key and its checks already
exist. Everything the store cannot prove - context usability, the
transaction-local RLS readback, the writer epoch, the partition identity, the
report digest, and the integrity of the stored export - fails closed.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.infrastructure.persistence.models.canonical_grants import (
    M06CanonicalGrantPromotionEvidenceRow,
)

from .adapter import SCHEMA_VERSION, AuthenticatedGrantContext, digest, is_sha256
from .evidence_store import (
    EvidenceStoreBlocked,
    bind_transaction_context,
    durable_context_usable,
    fence_writer_epoch,
)
from .parity_runner import REPORT_SCHEMA, ReportOutcome

REPORT_STORE_VERSION = "m06-parity-report-store-v1"
PARTITION_PREFIX = "parity:"
PARTITION_ID_MAX = 160
REVISION_ID_MAX = 80
EXPORT_INDENT = 2
ROLLBACK_GATE = "legacy remains authoritative; preserve parity evidence and repair forward"
_NOT_COLLECTED = "not_collected_by_parity_run"
_ROW_OUTCOME = {"ready_for_review": "ready", "blocked": "blocked"}
_RECORDABLE_STATUSES = frozenset(_ROW_OUTCOME)
_REQUIRED_DIGESTS = ("source", "canonical", "binding", "operation_mapping")
_EXCLUDED_REPORT_KEYS = ("report_digest", "report_schema_version")


class ParityReportStoreBlocked(EvidenceStoreBlocked):
    """The durable report registry cannot honour the write or the read.

    Subclasses :class:`EvidenceStoreBlocked` so one ``blockers`` tuple covers the
    whole durable M06 boundary; callers that catch the evidence-store error keep
    working, and the report-specific reason codes stay greppable.
    """


@dataclass(frozen=True, slots=True)
class ParityReportRecord:
    """Validated, store-ready projection of one parity report.

    ``export`` is the authoritative byte image of the report; every other field
    is derived from it and is used for the row keys and for cross-checking.
    """

    run_key: str
    tenant_id: str
    app_definition_id: str
    partition_id: str
    revision_id: str
    status: str
    report_digest: str
    export: str
    request_digest: str
    source_digest: str
    canonical_digest: str
    binding_digest: str
    operation_mapping_digest: str
    source_snapshot_digest: str
    final_delta_checkpoint: str
    writer_epoch: int
    observation_count: int


def parity_partition_id(tenant_id: str, app_definition_id: str) -> str:
    """Review-evidence partition id for the ``(tenant, definition)`` parity key."""
    for name, value in (("tenant_id", tenant_id), ("app_definition_id", app_definition_id)):
        if not isinstance(value, str) or not value.strip():
            raise ParityReportStoreBlocked(("parity_report_partition_identity_missing",))
    value = f"{PARTITION_PREFIX}{tenant_id}:{app_definition_id}"
    if len(value) > PARTITION_ID_MAX:
        raise ParityReportStoreBlocked(("parity_report_partition_id_too_long",))
    return value


def parity_revision_id(run_key: str) -> str:
    """Revision id of one run key; the run key is already a stable sha256."""
    if not is_sha256(run_key):
        raise ParityReportStoreBlocked(("parity_report_run_key_invalid",))
    if len(run_key) > REVISION_ID_MAX:
        raise ParityReportStoreBlocked(("parity_report_run_key_too_long",))
    return run_key


def report_content_digest(report: Mapping[str, Any]) -> str:
    """Recompute ``ParityRunReport.report_digest`` from its exported content."""
    if not isinstance(report, Mapping) or not report:
        raise ParityReportStoreBlocked(("parity_report_payload_invalid",))
    body = {key: value for key, value in report.items() if key not in _EXCLUDED_REPORT_KEYS}
    return digest(body)


def canonical_report_export(report: Mapping[str, Any], *, indent: int = EXPORT_INDENT) -> str:
    """Byte image of a report mapping; equal content yields equal bytes."""
    if not isinstance(report, Mapping) or not report:
        raise ParityReportStoreBlocked(("parity_report_payload_invalid",))
    try:
        return json.dumps(dict(report), sort_keys=True, indent=indent, ensure_ascii=True)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ParityReportStoreBlocked(("parity_report_payload_not_serialisable",)) from exc


def build_report_record(*, run_key: str, report: Mapping[str, Any],
                        context: AuthenticatedGrantContext) -> ParityReportRecord:
    """Validate one report against the durable contract, or refuse with codes."""
    context = require_store_context(context)
    parity_revision_id(run_key)
    if not isinstance(report, Mapping) or not report:
        raise ParityReportStoreBlocked(("parity_report_payload_invalid",))
    blockers: list[str] = []
    if report.get("run_key") != run_key:
        blockers.append("parity_report_run_key_mismatch")
    if report.get("schema") != REPORT_SCHEMA:
        blockers.append("parity_report_schema_unsupported")
    if report.get("report_schema_version") != SCHEMA_VERSION:
        blockers.append("parity_report_schema_version_unsupported")
    if report.get("tenant_id") != context.tenant_id:
        blockers.append("parity_report_partition_mismatch")
    if report.get("app_definition_id") != context.app_definition_id:
        blockers.append("parity_report_partition_mismatch")
    status = report.get("status")
    if status not in _RECORDABLE_STATUSES:
        blockers.append("parity_report_status_not_recordable")
    checkpoint = report.get("checkpoint")
    if not isinstance(checkpoint, Mapping) or checkpoint.get("complete_window") is not True:
        blockers.append("parity_report_window_incomplete")
    ownership = report.get("ownership")
    if not isinstance(ownership, Mapping):
        blockers.append("parity_report_ownership_missing")
    else:
        if (ownership.get("decision_owner") != "legacy"
                or ownership.get("canonical_read_owner") != "legacy"):
            blockers.append("parity_report_ownership_not_legacy")
        if ownership.get("canonical_positive_writes") is not False:
            blockers.append("parity_report_positive_writes_forbidden")
    digests = report.get("digests")
    if not isinstance(digests, Mapping):
        blockers.append("parity_report_digests_missing")
    else:
        for name in _REQUIRED_DIGESTS:
            if not is_sha256(digests.get(name)):
                blockers.append(f"parity_report_digest_{name}_invalid")
    report_digest = report.get("report_digest")
    if not is_sha256(report_digest):
        blockers.append("parity_report_digest_missing")
    elif report_content_digest(report) != report_digest:
        blockers.append("parity_report_digest_mismatch")
    if not is_sha256(report.get("source_snapshot_digest")):
        blockers.append("parity_report_source_snapshot_missing")
    checkpoint_text = report.get("final_delta_checkpoint")
    if not isinstance(checkpoint_text, str) or not checkpoint_text.strip():
        blockers.append("parity_report_final_delta_missing")
    writer_epoch = report.get("writer_epoch")
    if (not isinstance(writer_epoch, int) or isinstance(writer_epoch, bool)
            or writer_epoch <= 0):
        blockers.append("parity_report_writer_epoch_invalid")
    observation_count = report.get("observation_count")
    if (not isinstance(observation_count, int) or isinstance(observation_count, bool)
            or observation_count < 0):
        # A complete window that observed nothing is still a valid blocked
        # conclusion; a *partial* window is refused above by ``complete_window``.
        blockers.append("parity_report_observation_count_invalid")
    if blockers:
        raise ParityReportStoreBlocked(tuple(sorted(set(blockers))))
    digests = dict(digests or {})
    return ParityReportRecord(
        run_key=run_key,
        tenant_id=context.tenant_id or "",
        app_definition_id=context.app_definition_id or "",
        partition_id=parity_partition_id(context.tenant_id or "", context.app_definition_id or ""),
        revision_id=parity_revision_id(run_key),
        status=str(status),
        report_digest=str(report_digest),
        export=canonical_report_export(report),
        request_digest=str(report.get("request_digest") or ""),
        source_digest=str(digests.get("source")),
        canonical_digest=str(digests.get("canonical")),
        binding_digest=str(digests.get("binding")),
        operation_mapping_digest=str(digests.get("operation_mapping")),
        source_snapshot_digest=str(report.get("source_snapshot_digest")),
        final_delta_checkpoint=str(checkpoint_text),
        writer_epoch=int(writer_epoch),
        observation_count=int(observation_count),
    )


def stored_report_export(gate_evidence: Mapping[str, Any], *, run_key: str) -> str:
    """Return the stored byte image after proving it still matches its digest."""
    if not isinstance(gate_evidence, Mapping):
        raise ParityReportStoreBlocked(("stored_parity_report_payload_invalid",))
    if gate_evidence.get("run_key") != run_key:
        raise ParityReportStoreBlocked(("stored_parity_report_key_mismatch",))
    export = gate_evidence.get("export")
    if not isinstance(export, str) or not export.strip():
        raise ParityReportStoreBlocked(("stored_parity_report_export_missing",))
    stored_digest = gate_evidence.get("report_digest")
    if not is_sha256(stored_digest):
        raise ParityReportStoreBlocked(("stored_parity_report_digest_missing",))
    try:
        decoded = json.loads(export)
    except ValueError as exc:
        raise ParityReportStoreBlocked(("stored_parity_report_export_corrupt",)) from exc
    if not isinstance(decoded, dict) or decoded.get("run_key") != run_key:
        raise ParityReportStoreBlocked(("stored_parity_report_key_mismatch",))
    if report_content_digest(decoded) != stored_digest:
        raise ParityReportStoreBlocked(("stored_parity_report_digest_mismatch",))
    if decoded.get("report_digest") != stored_digest:
        raise ParityReportStoreBlocked(("stored_parity_report_digest_mismatch",))
    return export


def stored_report_outcome(gate_evidence: Mapping[str, Any], *, run_key: str,
                          incoming_digest: str) -> ReportOutcome:
    """Classify a duplicate write: identical digest replays, changed digest conflicts."""
    if not is_sha256(incoming_digest):
        raise ParityReportStoreBlocked(("parity_report_digest_missing",))
    stored_report_export(gate_evidence, run_key=run_key)
    stored_digest = gate_evidence.get("report_digest")
    return ReportOutcome.REPLAY if stored_digest == incoming_digest else ReportOutcome.CONFLICT


def require_store_context(context: Any) -> AuthenticatedGrantContext:
    """Reject a context that cannot prove tenant, definition, RLS and generations."""
    if not durable_context_usable(context):
        raise ParityReportStoreBlocked(("parity_report_context_invalid",))
    return context


def report_key(*, run_key: str, context: Any) -> tuple[str, str, str, str]:
    """The four review-evidence columns that make one run key unique."""
    usable = require_store_context(context)
    return (
        usable.tenant_id or "",
        usable.app_definition_id or "",
        parity_partition_id(usable.tenant_id or "", usable.app_definition_id or ""),
        parity_revision_id(run_key),
    )


class SqlParityReportStore:
    """Append-only run-key registry over the reviewed review-evidence table.

    Writes and reads happen inside a tenant-bound transaction: the transaction-local
    GUCs are set *and read back* before any statement, the writer epoch is fenced,
    and every query carries the tenant key explicitly on top of the row-level
    policy. A cross-tenant read therefore has no code path, no matter which key is
    supplied.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession],
                 writer_epoch_required: bool = True) -> None:
        if session_factory is None:
            raise ValueError("a database session factory is required for durable parity reports")
        self._session_factory = session_factory
        self._writer_epoch_required = writer_epoch_required

    async def save(self, *, run_key: str, report: Mapping[str, Any],
                   context: AuthenticatedGrantContext | None = None) -> ReportOutcome:
        usable = require_store_context(context)
        record = build_report_record(run_key=run_key, report=report, context=usable)
        async with self._write_transaction(usable) as session:
            existing = await self._load_row(session, record)
            if existing is not None:
                return stored_report_outcome(dict(existing.gate_evidence or {}),
                                             run_key=record.run_key,
                                             incoming_digest=record.report_digest)
            try:
                async with session.begin_nested():
                    session.add(M06CanonicalGrantPromotionEvidenceRow(
                        **_row_values(record, usable)))
                    await session.flush()
            except IntegrityError:
                # A concurrent writer claimed this run key first: never overwrite
                # it, classify the duplicate from the row that won.
                existing = await self._load_row(session, record)
                if existing is None:
                    raise ParityReportStoreBlocked(("parity_report_conflict_unresolved",))
                return stored_report_outcome(dict(existing.gate_evidence or {}),
                                             run_key=record.run_key,
                                             incoming_digest=record.report_digest)
        return ReportOutcome.RECORDED

    async def load(self, *, run_key: str,
                   context: AuthenticatedGrantContext | None = None
                   ) -> Mapping[str, Any] | None:
        export = await self.load_export(run_key=run_key, context=context)
        return None if export is None else json.loads(export)

    async def load_export(self, *, run_key: str,
                          context: AuthenticatedGrantContext | None = None) -> str | None:
        """Stored byte image of one report, or ``None`` when the run key is unknown."""
        tenant_id, app_definition_id, partition_id, revision_id = report_key(
            run_key=run_key, context=context)
        async with self._read_transaction(require_store_context(context)) as session:
            row = await session.scalar(
                select(M06CanonicalGrantPromotionEvidenceRow).where(
                    M06CanonicalGrantPromotionEvidenceRow.tenant_id == tenant_id,
                    M06CanonicalGrantPromotionEvidenceRow.definition_id == app_definition_id,
                    M06CanonicalGrantPromotionEvidenceRow.partition_id == partition_id,
                    M06CanonicalGrantPromotionEvidenceRow.revision_id == revision_id,
                )
            )
        if row is None:
            return None
        return stored_report_export(dict(row.gate_evidence or {}), run_key=run_key)

    @staticmethod
    async def _load_row(session: AsyncSession, record: ParityReportRecord
                        ) -> M06CanonicalGrantPromotionEvidenceRow | None:
        return await session.scalar(
            select(M06CanonicalGrantPromotionEvidenceRow).where(
                M06CanonicalGrantPromotionEvidenceRow.tenant_id == record.tenant_id,
                M06CanonicalGrantPromotionEvidenceRow.definition_id == record.app_definition_id,
                M06CanonicalGrantPromotionEvidenceRow.partition_id == record.partition_id,
                M06CanonicalGrantPromotionEvidenceRow.revision_id == record.revision_id,
            ).with_for_update()
        )

    @asynccontextmanager
    async def _read_transaction(self, context: AuthenticatedGrantContext
                                ) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            await bind_transaction_context(session, context)
            yield session

    @asynccontextmanager
    async def _write_transaction(self, context: AuthenticatedGrantContext
                                 ) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            async with session.begin():
                await bind_transaction_context(session, context)
                await fence_writer_epoch(session, context,
                                         required=self._writer_epoch_required)
                yield session


class FailClosedParityReportStore:
    """Default registry when no durable store is configured: every call refuses.

    Composed instead of ``InMemoryParityReportStore`` (a test double) so a missing
    durable binding can never turn into a silent in-process record.
    """

    IS_TEST_DOUBLE = False

    def __init__(self, *, blockers: tuple[str, ...] = ("durable_parity_report_store_not_configured",)
                 ) -> None:
        self.blockers = blockers

    async def load(self, *, run_key: str, context: Any | None = None) -> Mapping[str, Any] | None:
        raise ParityReportStoreBlocked(self.blockers)

    async def save(self, *, run_key: str, report: Mapping[str, Any],
                   context: Any | None = None) -> ReportOutcome:
        raise ParityReportStoreBlocked(self.blockers)


def _row_values(record: ParityReportRecord,
                context: AuthenticatedGrantContext) -> dict[str, Any]:
    """Columns for one immutable review-evidence row; nothing is left implicit."""
    return {
        "id": "m06parity_" + uuid4().hex[:24],
        "tenant_id": record.tenant_id,
        "definition_id": record.app_definition_id,
        "partition_id": record.partition_id,
        "revision_id": record.revision_id,
        "outcome": _ROW_OUTCOME[record.status],
        "source_snapshot_id": record.source_snapshot_digest,
        "source_schema_version": REPORT_SCHEMA,
        "source_digest": record.source_digest,
        "target_digest": record.canonical_digest,
        "dependency_digests": {
            "binding": record.binding_digest,
            "operation_mapping": record.operation_mapping_digest,
        },
        "constraint_inventory": {"state": _NOT_COLLECTED},
        "index_inventory": {"state": _NOT_COLLECTED},
        "policy_inventory": {"state": _NOT_COLLECTED},
        "trusted_role_epoch_evidence": {
            "writer_epoch": record.writer_epoch,
            "writer_generation": context.writer_generation,
            "plugin_instance_id": context.plugin_instance_id,
            "tenant_id": record.tenant_id,
        },
        "gate_evidence": {
            "report_store_version": REPORT_STORE_VERSION,
            "report_schema": REPORT_SCHEMA,
            "report_schema_version": SCHEMA_VERSION,
            "run_key": record.run_key,
            "request_digest": record.request_digest,
            "report_digest": record.report_digest,
            "status": record.status,
            "observation_count": record.observation_count,
            "final_delta_checkpoint": record.final_delta_checkpoint,
            "checkpoint_complete": True,
            "promotion_decision": False,
            "export": record.export,
        },
        "canonical_positive_writes": False,
        "writer_epoch": record.writer_epoch,
        "correlation_id": context.correlation_id,
        "decision_owner": "legacy",
        "reviewer": None,
        "rollback_gate": ROLLBACK_GATE,
    }


__all__ = [
    "EXPORT_INDENT",
    "FailClosedParityReportStore",
    "PARTITION_ID_MAX",
    "PARTITION_PREFIX",
    "ParityReportRecord",
    "ParityReportStoreBlocked",
    "REPORT_STORE_VERSION",
    "REVISION_ID_MAX",
    "ROLLBACK_GATE",
    "SqlParityReportStore",
    "build_report_record",
    "canonical_report_export",
    "parity_partition_id",
    "parity_revision_id",
    "report_content_digest",
    "report_key",
    "require_store_context",
    "stored_report_export",
    "stored_report_outcome",
]
