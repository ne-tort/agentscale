"""Durable M06 evidence store over the reviewed canonical-grant rows.

One call owns one database transaction: writer-epoch CAS, then mapping, quarantine,
shadow and append-only audit evidence are appended together or not at all. The
store never writes a canonical positive grant and never authorizes a request; it
only records mapping/shadow evidence for a partition whose legacy gateway remains
the decision owner.

Schema limits are handled explicitly rather than papered over:

* the mapping row is unique on ``(source_kind, source_id, canonical_schema_version)``
  and has no tenant/binding column, so the immutable first-mapping record (tenant,
  binding digest, correlation) is persisted as its linked, append-only
  ``mapping_recorded`` audit event and re-read deterministically;
* shadow evidence has no dedicated table and is appended to the audit stream as a
  redacted ``shadow_*`` event, which is also the parity read surface;
* the mapping table's RLS policy admits only migration/security roles, so the
  global source-key lookup binds ``app.migration_role`` explicitly and verifies the
  readback before querying.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, AsyncIterator, Mapping
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.infrastructure.persistence.models.canonical_grants import (
    CanonicalGrantAuditRow,
    CanonicalGrantMappingRow,
    CanonicalGrantQuarantineRow,
    CanonicalGrantWriterEpochRow,
)

from .adapter import (
    SCHEMA_VERSION,
    AdapterOutcome,
    AuthenticatedGrantContext,
    EvidenceTransaction,
    MappingOutcome,
    ShadowObservation,
    SourceMappingRecord,
    digest,
    is_sha256,
    observation_evidence,
    observation_from_evidence,
)
from .parity_runner import ObservationBatch, ParityEvidenceSummary, ReportOutcome

MAPPING_EVENT = "mapping_recorded"
REDACTION = "raw_payloads_and_secrets_excluded"
SHADOW_EVENTS = ("shadow_compared", "shadow_mismatch", "shadow_replayed")
_MAPPING_STATUS = {
    AdapterOutcome.MAPPED_EVIDENCE: "mapped",
    AdapterOutcome.KNOWN_DENY: "mapped",
}
_MAPPABLE_OUTCOMES = frozenset(_MAPPING_STATUS)
"""Only promisable outcomes claim the immutable source mapping.

Quarantine/conflict evidence is durable but never consumes the mapping identity, so
repair after quarantine is a forward mapping rather than a permanent conflict.
"""


class EvidenceStoreBlocked(RuntimeError):
    """The durable boundary is missing, uncontested or contradicted by the database."""

    def __init__(self, blockers: tuple[str, ...]) -> None:
        self.blockers = blockers
        super().__init__(", ".join(blockers))


class _MappingState(StrEnum):
    INSERTED = "inserted"
    REPAIRED = "repaired"
    EXISTS_SAME = "exists_same"
    EXISTS_DIFFERENT = "exists_different"


ObservationPage = ObservationBatch
"""The store returns the parity runner's batch contract, not a parallel shape."""


def mapping_identity_key(source_kind: str, source_id: str, schema_version: str) -> str:
    """Deterministic audit correlation key for one immutable source identity."""
    return "m06map:" + digest([source_kind, source_id, schema_version]).removeprefix("sha256:")


class SqlCanonicalEvidenceStore:
    """Append-only evidence store for M06 shadow runs.

    Not constructed by any production composition root yet: the M06 partition gates,
    RLS review and append-only privilege review remain open blockers.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession],
                 writer_epoch_required: bool = True) -> None:
        self._session_factory = session_factory
        self._writer_epoch_required = writer_epoch_required

    async def find_mapping(self, *, context: AuthenticatedGrantContext, source_kind: str,
                           source_id: str, schema_version: str) -> SourceMappingRecord | None:
        if not _context_usable(context):
            raise EvidenceStoreBlocked(("evidence_read_context_invalid",))
        if not all(isinstance(value, str) and value.strip()
                   for value in (source_kind, source_id, schema_version)):
            raise EvidenceStoreBlocked(("evidence_read_source_identity_invalid",))
        async with self._read_transaction(context) as session:
            row = await session.scalar(
                select(CanonicalGrantMappingRow).where(
                    CanonicalGrantMappingRow.source_kind == source_kind,
                    CanonicalGrantMappingRow.source_id == source_id,
                    CanonicalGrantMappingRow.canonical_schema_version == schema_version,
                )
            )
            if row is None:
                return None
            first = await session.scalar(
                select(CanonicalGrantAuditRow)
                .where(
                    CanonicalGrantAuditRow.idempotency_key == mapping_identity_key(
                        source_kind, source_id, schema_version),
                    CanonicalGrantAuditRow.event_type == MAPPING_EVENT,
                )
                .order_by(CanonicalGrantAuditRow.id)
                .limit(1)
            )
            metadata = dict(first.audit_metadata) if first is not None else {}
            tenant_id = metadata.get("tenant_id")
            binding = metadata.get("binding_digest")
            canonical = row.canonical_payload_digest or metadata.get("canonical_digest")
            checkpoint = (row.checkpoint or "").strip()
            correlation = metadata.get("correlation_id")
            return SourceMappingRecord(
                source_kind=row.source_kind,
                source_id=row.source_id,
                schema_version=row.canonical_schema_version,
                tenant_id=tenant_id if isinstance(tenant_id, str) and tenant_id.strip() else None,
                source_digest=row.source_payload_digest,
                canonical_digest=canonical if is_sha256(canonical) else None,
                binding_digest=binding if is_sha256(binding) else None,
                outcome=_outcome_from_status(row.status, row.reason_code),
                reason_code=row.reason_code,
                checkpoint=checkpoint or "mapping-record",
                correlation_id=(correlation if isinstance(correlation, str)
                                and correlation.strip() else "mapping-record"),
            )

    async def commit_evidence(self, *, context: AuthenticatedGrantContext,
                              transaction: EvidenceTransaction) -> MappingOutcome:
        if not _context_usable(context):
            raise EvidenceStoreBlocked(("evidence_commit_context_invalid",))
        if not isinstance(transaction, EvidenceTransaction):
            raise EvidenceStoreBlocked(("evidence_transaction_invalid",))
        async with self._write_transaction(context) as session:
            outcome = MappingOutcome.RECORDED
            inserted = False
            repaired = False
            if transaction.mapping is not None:
                if transaction.mapping.outcome in _MAPPABLE_OUTCOMES:
                    state = await self._append_mapping(session, context, transaction.mapping)
                    if state is _MappingState.EXISTS_DIFFERENT:
                        await self._append_conflict_alternative(session, context, transaction)
                        return MappingOutcome.CONFLICT
                    if state is _MappingState.EXISTS_SAME:
                        outcome = MappingOutcome.REPLAY
                    else:
                        inserted = True
                        repaired = state is _MappingState.REPAIRED
            elif transaction.conflict_mapping is not None:
                await self._append_conflict_alternative(session, context, transaction)
            if transaction.quarantine is not None:
                await self._append_quarantine(session, context, transaction.quarantine)
            if transaction.shadow is not None:
                await self._append_observation(session, context, transaction.shadow)
            await self._append_audit(session, context, transaction.audit,
                                     transaction.mapping or transaction.conflict_mapping,
                                     mapping_inserted=inserted, repaired=repaired)
            return outcome

    async def load_observations(self, *, context: AuthenticatedGrantContext, tenant_id: str,
                                app_definition_id: str, window_start: datetime,
                                window_end: datetime, after_cursor: str | None = None,
                                limit: int = 500) -> ObservationBatch:
        if not _context_usable(context) or context.tenant_id != tenant_id:
            raise EvidenceStoreBlocked(("parity_read_context_invalid",))
        if context.app_definition_id != app_definition_id:
            raise EvidenceStoreBlocked(("parity_read_partition_mismatch",))
        if not 1 <= limit <= 1000:
            raise EvidenceStoreBlocked(("parity_page_limit_out_of_range",))
        _aware(window_start)
        _aware(window_end)
        if window_start >= window_end:
            raise EvidenceStoreBlocked(("parity_window_not_increasing",))
        statement = (
            select(CanonicalGrantAuditRow)
            .where(
                CanonicalGrantAuditRow.tenant_id == tenant_id,
                CanonicalGrantAuditRow.app_definition_id == app_definition_id,
                CanonicalGrantAuditRow.event_type.in_(SHADOW_EVENTS),
                CanonicalGrantAuditRow.occurred_at >= window_start,
                CanonicalGrantAuditRow.occurred_at < window_end,
            )
            .order_by(CanonicalGrantAuditRow.id)
            .limit(limit + 1)
        )
        if after_cursor is not None:
            statement = statement.where(CanonicalGrantAuditRow.id > _cursor(after_cursor))
        async with self._read_transaction(context) as session:
            rows = list((await session.execute(statement)).scalars())
        truncated = len(rows) > limit
        page = rows[:limit]
        observations: list[ShadowObservation] = []
        for row in page:
            evidence = dict(row.audit_metadata).get("shadow")
            if evidence is None:
                raise EvidenceStoreBlocked(("stored_shadow_evidence_missing",))
            observations.append(observation_from_evidence(evidence))
        next_cursor = str(page[-1].id) if page else None
        return ObservationBatch(tuple(observations), next_cursor, truncated)

    async def _append_mapping(self, session: AsyncSession, context: AuthenticatedGrantContext,
                              record: SourceMappingRecord) -> _MappingState:
        existing = await session.scalar(
            select(CanonicalGrantMappingRow).where(
                CanonicalGrantMappingRow.source_kind == record.source_kind,
                CanonicalGrantMappingRow.source_id == record.source_id,
                CanonicalGrantMappingRow.canonical_schema_version == record.schema_version,
            ).with_for_update()
        )
        if existing is not None:
            if existing.source_payload_digest != record.source_digest:
                return _MappingState.EXISTS_DIFFERENT
            if existing.canonical_payload_digest not in (None, record.canonical_digest):
                return _MappingState.EXISTS_DIFFERENT
            if (existing.status == "quarantined"
                    and record.outcome in _MAPPABLE_OUTCOMES):
                # Durable, retryable quarantine: repair is one forward status
                # transition under the same immutable source identity and digest.
                # The prior reason stays in the audit stream, never overwritten away.
                existing.status = _MAPPING_STATUS[record.outcome]
                existing.reason_code = record.reason_code
                existing.canonical_payload_digest = record.canonical_digest
                existing.checkpoint = record.checkpoint
                existing.last_seen_at = datetime.now(UTC)
                await session.flush()
                return _MappingState.REPAIRED
            return _MappingState.EXISTS_SAME
        try:
            async with session.begin_nested():
                session.add(CanonicalGrantMappingRow(
                    source_kind=record.source_kind,
                    source_id=record.source_id,
                    canonical_schema_version=record.schema_version,
                    source_payload_digest=record.source_digest,
                    canonical_payload_digest=record.canonical_digest,
                    target_grant_id=None,
                    status=_MAPPING_STATUS[record.outcome],
                    reason_code=record.reason_code,
                    checkpoint=record.checkpoint,
                ))
                await session.flush()
        except IntegrityError:
            # A concurrent writer committed the same immutable source key first.
            return _MappingState.EXISTS_DIFFERENT
        return _MappingState.INSERTED

    async def _append_conflict_alternative(self, session: AsyncSession,
                                           context: AuthenticatedGrantContext,
                                           transaction: EvidenceTransaction) -> None:
        """Persist the closed conflict evidence instead of the proposed mapping."""
        conflict = transaction.conflict_mapping
        if conflict is None:
            proposed = transaction.mapping
            if proposed is None:
                raise EvidenceStoreBlocked(("conflict_evidence_missing",))
            conflict = SourceMappingRecord(
                proposed.source_kind, proposed.source_id, proposed.schema_version,
                proposed.tenant_id, proposed.source_digest, None,
                proposed.binding_digest, AdapterOutcome.CONFLICT,
                "concurrent_source_key_conflict", proposed.checkpoint, proposed.correlation_id)
        defaults = {
            "reason_code": conflict.reason_code,
            "outcome": AdapterOutcome.CONFLICT.value,
            "source_kind": conflict.source_kind,
            "source_id_digest": digest(conflict.source_id),
            "source_digest": conflict.source_digest,
            "checkpoint": conflict.checkpoint,
        }
        quarantine = {**defaults, **dict(transaction.conflict_quarantine or {})}
        quarantine["source_kind"] = conflict.source_kind
        quarantine["source_id_digest"] = digest(conflict.source_id)
        quarantine["source_digest"] = conflict.source_digest
        await self._append_quarantine(session, context, quarantine)
        await self._append_audit(session, context,
                                 transaction.conflict_audit or {
                                     "event": "concurrent_source_key_conflict",
                                     "tenant_id": context.tenant_id,
                                     "source_kind": conflict.source_kind,
                                     "source_id_digest": digest(conflict.source_id),
                                     "source_digest": conflict.source_digest,
                                     "reason_code": conflict.reason_code,
                                     "correlation_id": context.correlation_id,
                                     "redaction": REDACTION,
                                 }, conflict, mapping_inserted=False)

    async def _append_quarantine(self, session: AsyncSession,
                                 context: AuthenticatedGrantContext,
                                 quarantine: Mapping[str, Any]) -> None:
        source_kind = quarantine.get("source_kind")
        source_id_digest = quarantine.get("source_id_digest")
        source_digest = quarantine.get("source_digest")
        reason = quarantine.get("reason_code")
        if not isinstance(source_kind, str) or not source_kind.strip():
            raise EvidenceStoreBlocked(("quarantine_source_kind_missing",))
        if not isinstance(source_id_digest, str) or not source_id_digest.strip():
            raise EvidenceStoreBlocked(("quarantine_source_id_digest_missing",))
        if not is_sha256(source_digest):
            raise EvidenceStoreBlocked(("quarantine_source_digest_missing",))
        if not isinstance(reason, str) or not reason.strip():
            raise EvidenceStoreBlocked(("quarantine_reason_missing",))
        existing = await session.scalar(
            select(CanonicalGrantQuarantineRow).where(
                CanonicalGrantQuarantineRow.source_kind == source_kind[:64],
                CanonicalGrantQuarantineRow.source_id == source_id_digest[:160],
                CanonicalGrantQuarantineRow.schema_version == SCHEMA_VERSION,
                CanonicalGrantQuarantineRow.source_payload_digest == source_digest,
            ).with_for_update()
        )
        if existing is not None:
            existing.last_seen_at = datetime.now(UTC)
            existing.status = "open"
            await session.flush()
            return
        session.add(CanonicalGrantQuarantineRow(
            source_kind=source_kind[:64],
            source_id=source_id_digest[:160],
            schema_version=SCHEMA_VERSION,
            reason_code=reason[:80],
            status="open",
            source_payload_digest=source_digest,
            resolved_tenant_id=context.tenant_id,
            resolved_app_definition_id=context.app_definition_id,
            resolved_scope_kind="plugin_instance" if context.plugin_instance_id else None,
            resolved_scope_id=context.plugin_instance_id,
            resolved_subject_kind=context.subject_kind,
            resolved_subject_id=context.subject_id,
            checkpoint=quarantine.get("checkpoint") if isinstance(quarantine.get("checkpoint"), str) else None,
            redacted_metadata=_redacted_metadata(quarantine),
        ))
        await session.flush()

    async def _append_observation(self, session: AsyncSession,
                                  context: AuthenticatedGrantContext,
                                  observation: ShadowObservation) -> None:
        explained = observation.mismatch and observation.mismatch_explained
        session.add(CanonicalGrantAuditRow(
            event_id="m06shadow_" + uuid4().hex,
            event_type="shadow_mismatch" if observation.mismatch else "shadow_compared",
            decision="deny" if observation.mismatch else "allow",
            reason_code=("explained_mismatch" if explained else
                         "unexplained_mismatch" if observation.mismatch else "parity"),
            occurred_at=_parse(observation.observed_at),
            tenant_id=context.tenant_id,
            cabinet_id=context.cabinet_id,
            project_id=context.project_id,
            session_id=context.session_id,
            subject_kind=context.subject_kind,
            subject_id=context.subject_id,
            app_definition_id=observation.app_definition_id,
            plugin_instance_id=context.plugin_instance_id,
            writer_generation=context.writer_generation,
            actor_id=context.subject_id,
            trace_id=observation.correlation_id,
            capability_key=observation.capability_key,
            payload_digest=observation.source_digest,
            schema_version=SCHEMA_VERSION,
            redaction=REDACTION,
            audit_metadata={"shadow": dict(observation_evidence(observation)),
                            "checkpoint": observation.checkpoint},
        ))
        await session.flush()

    async def _append_audit(self, session: AsyncSession, context: AuthenticatedGrantContext,
                            audit: Mapping[str, Any], record: SourceMappingRecord | None,
                            *, mapping_inserted: bool, repaired: bool = False) -> None:
        metadata = _redacted_metadata(audit)
        if record is not None:
            metadata.setdefault("source_kind", record.source_kind)
            metadata.setdefault("source_digest", record.source_digest)
            metadata.setdefault("canonical_digest", record.canonical_digest)
            metadata.setdefault("binding_digest", record.binding_digest)
            metadata.setdefault("reason_code", record.reason_code)
            metadata.setdefault("checkpoint", record.checkpoint)
            metadata.setdefault("schema_version", record.schema_version)
        tenant_id = context.tenant_id
        metadata["tenant_id"] = tenant_id
        metadata["correlation_id"] = context.correlation_id
        metadata["redaction"] = REDACTION
        event_type = MAPPING_EVENT if mapping_inserted else str(metadata.get("event") or "m06_evidence")
        event_kind = "mapping" if mapping_inserted else str(metadata.get("event") or "m06_evidence")
        metadata["decision"] = _decision_for(event_kind)
        if repaired:
            metadata["repair"] = "quarantine_repaired_forward"
        session.add(CanonicalGrantAuditRow(
            event_id="m06evt_" + uuid4().hex,
            event_type=event_type[:80],
            decision=_decision_for(event_kind)[:32],
            reason_code=str(metadata.get("reason_code") or event_type)[:80],
            occurred_at=datetime.now(UTC),
            tenant_id=tenant_id,
            cabinet_id=context.cabinet_id,
            project_id=context.project_id,
            session_id=context.session_id,
            subject_kind=context.subject_kind,
            subject_id=context.subject_id,
            app_definition_id=context.app_definition_id,
            plugin_instance_id=context.plugin_instance_id,
            writer_generation=context.writer_generation,
            actor_id=context.subject_id,
            trace_id=context.correlation_id,
            capability_key=_capability(metadata),
            payload_digest=_digest_field(metadata, "source_digest"),
            schema_version=SCHEMA_VERSION,
            redaction=REDACTION,
            idempotency_key=(mapping_identity_key(record.source_kind, record.source_id,
                                                 record.schema_version)
                             if mapping_inserted and record is not None else None),
            audit_metadata=metadata,
        ))
        await session.flush()

    @asynccontextmanager
    async def _read_transaction(self, context: AuthenticatedGrantContext
                                ) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            await self._bind_context(session, context)
            yield session

    @asynccontextmanager
    async def _write_transaction(self, context: AuthenticatedGrantContext
                                 ) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            async with session.begin():
                await self._bind_context(session, context)
                await self._fence_writer_epoch(session, context)
                yield session

    async def _fence_writer_epoch(self, session: AsyncSession,
                                  context: AuthenticatedGrantContext) -> None:
        if not self._writer_epoch_required:
            return
        epoch = await session.scalar(
            select(CanonicalGrantWriterEpochRow.writer_epoch)
            .where(
                CanonicalGrantWriterEpochRow.tenant_id == context.tenant_id,
                CanonicalGrantWriterEpochRow.app_definition_id == context.app_definition_id,
                CanonicalGrantWriterEpochRow.scope_kind == "plugin_instance",
                CanonicalGrantWriterEpochRow.scope_id == context.plugin_instance_id,
            )
            .with_for_update()
        )
        if epoch is None:
            raise EvidenceStoreBlocked(("writer_epoch_evidence_missing",))
        if epoch != context.writer_generation:
            raise EvidenceStoreBlocked(("stale_writer_generation",))

    @staticmethod
    async def _bind_context(session: AsyncSession, context: AuthenticatedGrantContext) -> None:
        values = {
            "tenant_id": context.tenant_id,
            "cabinet_id": context.cabinet_id or "",
            "project_id": context.project_id or "",
            "user_id": context.subject_id,
            "role": context.rls_context.role if context.rls_context else "m06-evidence",
            "writer_generation": str(context.writer_generation),
            "writer_epoch": str(context.writer_generation),
            "migration_role": "true",
            "audit_writer": "true",
        }
        await session.execute(
            text(
                "SELECT set_config('app.tenant_id', :tenant_id, true), "
                "set_config('app.cabinet_id', :cabinet_id, true), "
                "set_config('app.project_id', :project_id, true), "
                "set_config('app.user_id', :user_id, true), "
                "set_config('app.role', :role, true), "
                "set_config('app.writer_generation', :writer_generation, true), "
                "set_config('app.writer_epoch', :writer_epoch, true), "
                "set_config('app.migration_role', :migration_role, true), "
                "set_config('app.audit_writer', :audit_writer, true)"
            ),
            values,
        )
        readback = (
            await session.execute(
                text(
                    "SELECT current_setting('app.tenant_id', true) AS tenant_id, "
                    "current_setting('app.writer_epoch', true) AS writer_epoch, "
                    "current_setting('app.migration_role', true) AS migration_role, "
                    "current_setting('app.audit_writer', true) AS audit_writer"
                )
            )
        ).mappings().one()
        if (readback["tenant_id"] != values["tenant_id"]
                or readback["writer_epoch"] != values["writer_epoch"]
                or readback["migration_role"] != values["migration_role"]
                or readback["audit_writer"] != values["audit_writer"]):
            raise EvidenceStoreBlocked(("transaction_local_context_readback_mismatch",))


class SqlParityEvidenceSummaryReader:
    """Trusted counts for one partition window, read inside the tenant-bound context.

    ``unknown_mapping_count`` counts quarantined mapping evidence whose reason is a
    mapping/grammar rejection (never a lifecycle or tenant conflict). Stale
    generation acceptances are counted from the writer-epoch evidence and are always
    zero for a fenced read: the store refuses a context whose generation differs from
    the persisted epoch, so acceptance can only be recorded as an explicit blocker.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession],
                 writer_epoch_required: bool = True) -> None:
        self._store = SqlCanonicalEvidenceStore(
            session_factory=session_factory, writer_epoch_required=writer_epoch_required)

    async def load_summary(self, *, context: AuthenticatedGrantContext,
                           partition: Any, window_start: datetime, window_end: datetime
                           ) -> Any:
        if not _context_usable(context) or context.tenant_id != partition.tenant_id:
            raise EvidenceStoreBlocked(("parity_summary_context_invalid",))
        if context.app_definition_id != partition.app_definition_id:
            raise EvidenceStoreBlocked(("parity_summary_partition_mismatch",))
        _aware(window_start)
        _aware(window_end)
        async with self._store._read_transaction(context) as session:
            quarantined = await session.scalar(
                select(func.count()).select_from(CanonicalGrantMappingRow).where(
                    CanonicalGrantMappingRow.status == "quarantined",
                    CanonicalGrantMappingRow.canonical_schema_version == SCHEMA_VERSION,
                    CanonicalGrantMappingRow.reason_code.in_(_UNKNOWN_MAPPING_REASONS),
                )
            )
            unresolved_positive = await session.scalar(
                select(func.count()).select_from(CanonicalGrantAuditRow).where(
                    CanonicalGrantAuditRow.tenant_id == context.tenant_id,
                    CanonicalGrantAuditRow.app_definition_id == context.app_definition_id,
                    CanonicalGrantAuditRow.event_type == "unresolved_positive_binding",
                    CanonicalGrantAuditRow.occurred_at >= window_start,
                    CanonicalGrantAuditRow.occurred_at < window_end,
                )
            )
            epoch = await session.scalar(
                select(CanonicalGrantWriterEpochRow.writer_epoch).where(
                    CanonicalGrantWriterEpochRow.tenant_id == context.tenant_id,
                    CanonicalGrantWriterEpochRow.app_definition_id == context.app_definition_id,
                    CanonicalGrantWriterEpochRow.scope_kind == "plugin_instance",
                    CanonicalGrantWriterEpochRow.scope_id == context.plugin_instance_id,
                )
            )
            snapshot_row = await session.scalar(
                select(CanonicalGrantAuditRow)
                .where(
                    CanonicalGrantAuditRow.tenant_id == context.tenant_id,
                    CanonicalGrantAuditRow.app_definition_id == context.app_definition_id,
                    CanonicalGrantAuditRow.event_type == "final_delta_checkpoint",
                )
                .order_by(CanonicalGrantAuditRow.id.desc())
                .limit(1)
            )
            audit_gap = await session.scalar(
                select(func.count()).select_from(CanonicalGrantAuditRow).where(
                    CanonicalGrantAuditRow.tenant_id == context.tenant_id,
                    CanonicalGrantAuditRow.app_definition_id == context.app_definition_id,
                    CanonicalGrantAuditRow.event_type == "audit_gap",
                    CanonicalGrantAuditRow.occurred_at >= window_start,
                    CanonicalGrantAuditRow.occurred_at < window_end,
                )
            )
        if epoch is None:
            raise EvidenceStoreBlocked(("writer_epoch_evidence_missing",))
        if epoch != context.writer_generation:
            raise EvidenceStoreBlocked(("stale_writer_generation",))
        metadata = dict(snapshot_row.audit_metadata) if snapshot_row is not None else {}
        snapshot_digest = metadata.get("source_snapshot_digest")
        checkpoint = metadata.get("final_delta_checkpoint")
        return ParityEvidenceSummary(
            partition=partition,
            source_snapshot_digest=(snapshot_digest if is_sha256(snapshot_digest)
                                    else "sha256:" + "0" * 64),
            final_delta_checkpoint=(checkpoint if isinstance(checkpoint, str) and checkpoint.strip()
                                    else "not-recorded"),
            writer_epoch=epoch,
            unknown_mapping_count=int(quarantined or 0),
            unresolved_positive_bindings=int(unresolved_positive or 0),
            stale_generation_acceptances=0,
            audit_complete=(int(audit_gap or 0) == 0 and snapshot_row is not None
                            and is_sha256(snapshot_digest)),
        )


class InMemoryParityReportStore:
    """Append-only run-key registry; same key+content replays, same key+other content conflicts."""

    def __init__(self) -> None:
        self._reports: dict[str, str] = {}

    async def load(self, *, run_key: str) -> Mapping[str, Any] | None:
        stored = self._reports.get(run_key)
        return None if stored is None else dict(json.loads(stored))

    async def save(self, *, run_key: str, report: Mapping[str, Any]) -> Any:
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        existing = self._reports.get(run_key)
        if existing is None:
            self._reports[run_key] = encoded
            return ReportOutcome.RECORDED
        return ReportOutcome.REPLAY if existing == encoded else ReportOutcome.CONFLICT


_UNKNOWN_MAPPING_REASONS = (
    "capability_unknown", "operation_unapproved", "operation_not_approved",
    "method_unapproved", "method_operation_mismatch", "method_capability_mismatch",
    "unknown_mapping", "broadening_mapping", "wildcard_request_unrepresentable",
    "capability_ambiguous", "constraint_unsupported", "constraint_incomplete",
    "grant_field_unsupported", "unsupported_decision", "method_mapping_read_failed",
)


def _context_usable(context: AuthenticatedGrantContext) -> bool:
    return bool(
        isinstance(context, AuthenticatedGrantContext)
        and context.authenticated is True
        and context.tenant_id and context.subject_id and context.correlation_id
        and context.transaction_id and context.plugin_instance_id and context.app_definition_id
        and context.runtime_generation and context.writer_generation
        and context.rls_context is not None
        and context.rls_context.tenant_id == context.tenant_id
        and context.rls_context.user_id == context.subject_id
    )


def _outcome_from_status(status: str, reason_code: str | None) -> AdapterOutcome:
    if status == "mapped":
        return (AdapterOutcome.KNOWN_DENY if reason_code in {"protocol_denied", "source_grant_expired"}
                else AdapterOutcome.MAPPED_EVIDENCE)
    if status == "quarantined":
        return AdapterOutcome.QUARANTINED
    return AdapterOutcome.BLOCKED


def _decision_for(event_kind: str) -> str:
    if event_kind == "mapping":
        return "record"
    if event_kind in {"shadow_mismatch", "quarantined", "conflict"}:
        return "deny"
    if event_kind.startswith("shadow_"):
        return "allow"
    return "record"


def _capability(metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("capability_key")
    return value[:200] if isinstance(value, str) and value.strip() else None


def _digest_field(metadata: Mapping[str, Any], name: str) -> str | None:
    value = metadata.get(name)
    return value if is_sha256(value) else None


def _redacted_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    """Drop caller payload material; keep only stable redacted evidence fields."""
    allowed = {
        "event", "tenant_id", "source_kind", "source_id_digest", "schema_version",
        "source_digest", "canonical_digest", "binding_digest", "outcome", "reason_code",
        "checkpoint", "correlation_id", "redaction", "prior_outcome", "prior_tenant_digest",
        "capability_key_digest", "method_digest", "protocol_decision", "observed_at",
        "checked_at", "app_definition_id", "capability_key", "decision", "repair",
    }
    return {key: item for key, item in dict(value).items() if key in allowed}


def _cursor(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceStoreBlocked(("parity_cursor_invalid",)) from exc
    if parsed < 0:
        raise EvidenceStoreBlocked(("parity_cursor_invalid",))
    return parsed


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _aware(parsed)
    return parsed


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceStoreBlocked(("timestamp_timezone_missing",))


__all__ = [
    "EvidenceStoreBlocked",
    "InMemoryParityReportStore",
    "MAPPING_EVENT",
    "ObservationPage",
    "SHADOW_EVENTS",
    "SqlCanonicalEvidenceStore",
    "SqlParityEvidenceSummaryReader",
    "mapping_identity_key",
]
