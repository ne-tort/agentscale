"""Scoped SQL persistence over the registered runtime revision ORM rows.

This adapter persists desired snapshots and materialization entries only. The
current schema has no durable runtime-attempt/receipt or audit rows and no
runtime-writer epoch, so this module cannot acknowledge a runtime apply as ready.
"""
from __future__ import annotations

import re
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.runtime_materialization.state_machine import (
    DependencyEvidence,
    RuntimeAuditRecord,
    canonical_digest,
)
from prodavan.application.runtime_revision import (
    CapabilityBundle,
    MaterializationEntry,
    MaterializationStatus,
    ProjectRuntimeRevision,
    RevisionState,
)
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow
from prodavan.infrastructure.persistence.models.runtime_revisions import (
    CapabilityBundleRow,
    MaterializationEntryRow,
    ProjectRuntimeRevisionRow,
)

if TYPE_CHECKING:
    from prodavan.application.migration.runtime_api.contracts import RuntimeApiProjectScope

_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SECRET_MARKERS = ("password", "secret", "token", "credential", "private_key", "api_key")


class PersistenceStatus(StrEnum):
    WRITTEN = "written"
    REPLAY = "replay"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    status: PersistenceStatus
    reason_code: str
    revision: ProjectRuntimeRevision | None = None
    entries: tuple[MaterializationEntry, ...] = ()
    affected_rows: int | None = None


class TransactionalRuntimeAuditPort(Protocol):
    """Append audit using the exact SQLAlchemy session/transaction supplied."""

    async def append(self, *, session: AsyncSession, record: RuntimeAuditRecord) -> None: ...


class _StaleGeneration(Exception):
    def __init__(self, affected_rows: int) -> None:
        self.affected_rows = affected_rows


class _PersistenceConflict(Exception):
    def __init__(self, reason_code: str, affected_rows: int | None = None) -> None:
        self.reason_code = reason_code
        self.affected_rows = affected_rows


class _AuditWriteFailed(Exception):
    pass


class SqlAlchemyRuntimeRevisionRepository:
    """Durable subset of runtime revision persistence supported by current ORM.

    Every operation requires the authenticated project's exact company, cabinet
    and Pod tuple. Mutations own their transaction using the repository's
    ``async_sessionmaker``. Runtime readiness and operational execution are not
    implemented here.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(
        self, *, scope: RuntimeApiProjectScope, revision_id: str
    ) -> ProjectRuntimeRevision | None:
        async with self._session_factory() as session:
            if not await self._scope_exists(session, scope):
                return None
            row = await session.scalar(select(ProjectRuntimeRevisionRow).where(
                ProjectRuntimeRevisionRow.id == revision_id,
                ProjectRuntimeRevisionRow.project_id == scope.project_id,
            ))
            return await self._to_revision(session, row) if row is not None else None

    async def get_latest(self, *, scope: RuntimeApiProjectScope) -> ProjectRuntimeRevision | None:
        async with self._session_factory() as session:
            if not await self._scope_exists(session, scope):
                return None
            row = await session.scalar(
                select(ProjectRuntimeRevisionRow)
                .where(
                    ProjectRuntimeRevisionRow.project_id == scope.project_id,
                    or_(ProjectRuntimeRevisionRow.pod_id.is_(None),
                        ProjectRuntimeRevisionRow.pod_id == scope.pod_id),
                )
                .order_by(ProjectRuntimeRevisionRow.revision.desc())
                .limit(1)
            )
            return await self._to_revision(session, row) if row is not None else None

    async def create_revision(
        self, *, scope: RuntimeApiProjectScope, revision: ProjectRuntimeRevision
    ) -> PersistenceResult:
        if not self._valid_new_revision(scope, revision):
            return self._result(PersistenceStatus.BLOCKED, "revision_snapshot_invalid")
        try:
            async with self._session_factory() as session, session.begin():
                if not await self._lock_scope(session, scope):
                    return self._result(PersistenceStatus.BLOCKED, "exact_project_scope_unresolved")
                existing = await session.scalar(select(ProjectRuntimeRevisionRow).where(
                    ProjectRuntimeRevisionRow.id == revision.revision_id,
                ).with_for_update())
                if existing is not None:
                    stored_bundle = await session.get(CapabilityBundleRow, existing.bundle_id)
                    if self._same_snapshot(existing, stored_bundle, revision):
                        domain = await self._to_revision(session, existing)
                        return self._result(PersistenceStatus.REPLAY, "revision_snapshot_replay", revision=domain)
                    return self._result(PersistenceStatus.CONFLICT, "revision_id_payload_conflict")
                number_owner = await session.scalar(select(ProjectRuntimeRevisionRow.id).where(
                    ProjectRuntimeRevisionRow.project_id == scope.project_id,
                    ProjectRuntimeRevisionRow.revision == revision.revision,
                ).limit(1))
                if number_owner is not None:
                    return self._result(PersistenceStatus.CONFLICT, "project_revision_number_conflict")

                bundle_row = await session.get(CapabilityBundleRow, revision.bundle.bundle_id)
                if bundle_row is None:
                    digest_owner = await session.scalar(select(CapabilityBundleRow.id).where(
                        CapabilityBundleRow.digest == revision.bundle.digest,
                    ).limit(1))
                    if digest_owner is not None:
                        return self._result(PersistenceStatus.CONFLICT, "bundle_digest_identity_conflict")
                    bundle_row = CapabilityBundleRow(
                        id=revision.bundle.bundle_id,
                        manifest=_json_value(revision.bundle.manifest),
                        digest=revision.bundle.digest,
                    )
                    session.add(bundle_row)
                elif (bundle_row.digest != revision.bundle.digest
                      or _json_value(bundle_row.manifest) != _json_value(revision.bundle.manifest)):
                    return self._result(PersistenceStatus.CONFLICT, "bundle_id_payload_conflict")

                row = ProjectRuntimeRevisionRow(
                    id=revision.revision_id,
                    project_id=revision.project_id,
                    revision=revision.revision,
                    bundle_id=revision.bundle.bundle_id,
                    state=revision.state.value,
                    applied_revision_id=revision.applied_revision_id,
                    pod_id=revision.pod_id,
                    pod_uid=revision.pod_uid,
                    hydrate_generation=revision.hydrate_generation,
                    bridge_generation=revision.bridge_generation,
                    runtime_image_digest=revision.runtime_image_digest,
                    failure_code=revision.failure_code,
                    diagnostics=_safe_diagnostics(revision.diagnostics),
                    created_by=revision.created_by,
                    source_event=revision.source_event,
                )
                session.add(row)
                await session.flush()
                saved = await self._to_revision(session, row)
                return self._result(PersistenceStatus.WRITTEN, "revision_snapshot_written", revision=saved)
        except IntegrityError:
            return self._result(PersistenceStatus.CONFLICT, "revision_snapshot_unique_conflict")

    async def list_entries(
        self, *, scope: RuntimeApiProjectScope, revision_id: str
    ) -> list[MaterializationEntry]:
        async with self._session_factory() as session:
            if not await self._scoped_revision(session, scope, revision_id):
                return []
            rows = (await session.scalars(
                select(MaterializationEntryRow)
                .where(MaterializationEntryRow.revision_id == revision_id)
                .order_by(MaterializationEntryRow.workspace_path)
            )).all()
            return [_to_entry(row) for row in rows]

    async def begin_apply(
        self,
        *,
        scope: RuntimeApiProjectScope,
        revision_id: str,
        expected_state: RevisionState,
        expected_generation: int,
        entries: list[MaterializationEntry],
        evidence: DependencyEvidence | None,
        at: datetime | None,
        audit_writer: TransactionalRuntimeAuditPort | None,
        audit_record: RuntimeAuditRecord,
    ) -> PersistenceResult:
        """Claim one entry generation and persist its plan atomically.

        Idempotent replay is resolved before CAS. The SQL state transition and
        conditional entry-generation update both execute in the same transaction;
        a zero-row generation CAS rolls back the entire attempt.
        """
        reason = self._validate_apply_input(
            scope, revision_id, expected_generation, entries, evidence, at, audit_writer, audit_record
        )
        if reason:
            return self._result(PersistenceStatus.BLOCKED, reason)
        next_generation = expected_generation + 1
        try:
            async with self._session_factory() as session, session.begin():
                if not await self._lock_scope(session, scope):
                    return self._result(PersistenceStatus.BLOCKED, "exact_project_scope_unresolved")
                revision_row = await self._revision_row(session, scope, revision_id, lock=True)
                if revision_row is None:
                    return self._result(PersistenceStatus.BLOCKED, "runtime_revision_missing")
                existing_rows = list((await session.scalars(select(MaterializationEntryRow).where(
                    MaterializationEntryRow.revision_id == revision_id,
                ).order_by(MaterializationEntryRow.workspace_path).with_for_update())).all())
                replay = self._resolve_replay(entries, existing_rows)
                if replay is not None:
                    status, code = replay
                    return self._result(status, code, revision=await self._to_revision(session, revision_row),
                                         entries=tuple(_to_entry(row) for row in existing_rows))
                evidence_blocker = _evidence_blocker(
                    evidence, at, revision_row.runtime_image_digest,
                    await session.get(CapabilityBundleRow, revision_row.bundle_id), entries,
                )
                if evidence_blocker:
                    return self._result(PersistenceStatus.BLOCKED, evidence_blocker)

                if existing_rows:
                    if len({row.generation for row in existing_rows}) != 1:
                        return self._result(PersistenceStatus.CONFLICT, "materialization_generation_inconsistent", affected_rows=0)
                    generation_cas = await session.execute(update(MaterializationEntryRow).where(
                        MaterializationEntryRow.revision_id == revision_id,
                        MaterializationEntryRow.generation == expected_generation,
                    ).values(generation=expected_generation))
                    if generation_cas.rowcount != len(existing_rows):
                        return self._result(PersistenceStatus.CONFLICT, "stale_generation_cas", affected_rows=0)
                    return self._result(PersistenceStatus.CONFLICT, "revision_materialization_snapshot_immutable",
                                        affected_rows=generation_cas.rowcount)
                if expected_generation != 0:
                    return self._result(PersistenceStatus.CONFLICT, "stale_generation_cas", affected_rows=0)
                if expected_state not in {RevisionState.PLANNED, RevisionState.VALIDATED}:
                    return self._result(PersistenceStatus.CONFLICT, "revision_state_not_claimable", affected_rows=0)
                claimed = await session.execute(update(ProjectRuntimeRevisionRow).where(
                    ProjectRuntimeRevisionRow.id == revision_id,
                    ProjectRuntimeRevisionRow.project_id == scope.project_id,
                    ProjectRuntimeRevisionRow.state == expected_state.value,
                ).values(state=RevisionState.APPLYING.value, failure_code=None, diagnostics=None))
                if claimed.rowcount != 1:
                    raise _PersistenceConflict("stale_revision_state_cas", claimed.rowcount)

                await self._replace_entry_rows(session, revision_id, existing_rows, entries, next_generation)
                await session.flush()
                await self._append_audit(audit_writer, session, audit_record)
                saved_revision = await self._to_revision(session, revision_row)
                saved_entries = tuple(_to_entry(row) for row in (await session.scalars(
                    select(MaterializationEntryRow)
                    .where(MaterializationEntryRow.revision_id == revision_id)
                    .order_by(MaterializationEntryRow.workspace_path)
                )).all())
                return self._result(PersistenceStatus.WRITTEN, "materialization_generation_claimed",
                                    revision=saved_revision, entries=saved_entries,
                                    affected_rows=len(existing_rows) if existing_rows else len(entries))
        except _StaleGeneration as exc:
            return self._result(PersistenceStatus.CONFLICT, "stale_generation_cas", affected_rows=exc.affected_rows)
        except _PersistenceConflict as exc:
            return self._result(PersistenceStatus.CONFLICT, exc.reason_code, affected_rows=exc.affected_rows)
        except _AuditWriteFailed:
            return self._result(PersistenceStatus.BLOCKED, "audit_write_failed")
        except IntegrityError:
            return self._result(PersistenceStatus.CONFLICT, "materialization_unique_conflict")

    async def finish_failed(
        self,
        *,
        scope: RuntimeApiProjectScope,
        revision_id: str,
        expected_generation: int,
        failure_code: str,
        diagnostics: Mapping[str, object],
        entry_updates: list[MaterializationEntry] | None,
        audit_writer: TransactionalRuntimeAuditPort | None,
        audit_record: RuntimeAuditRecord,
    ) -> PersistenceResult:
        """Persist a terminal failed attempt; this adapter has no ready method."""
        if (not _scope_values(scope) or not revision_id.strip()
                or not isinstance(expected_generation, int) or isinstance(expected_generation, bool)
                or expected_generation < 0 or not _CODE.fullmatch(failure_code)
                or audit_writer is None or audit_record.reason_code != failure_code
                or not self._valid_audit(audit_record, scope, expected_generation, "failed")):
            return self._result(PersistenceStatus.BLOCKED, "failed_transition_input_invalid")
        try:
            safe_diagnostics = _safe_diagnostics(diagnostics)
            updates = entry_updates or []
            if any(
                item.revision_id != revision_id
                or item.generation != expected_generation
                or item.status not in {
                    MaterializationStatus.APPLIED,
                    MaterializationStatus.FAILED,
                    MaterializationStatus.PLANNED,
                    MaterializationStatus.STAGED,
                }
                or (item.content_hash is not None and not _DIGEST.fullmatch(item.content_hash))
                or (item.error is not None and not _CODE.fullmatch(item.error))
                or _contains_inline_secret(item.provenance)
                for item in updates
            ):
                return self._result(PersistenceStatus.BLOCKED, "failed_entry_update_invalid")
            if len({item.workspace_path for item in updates}) != len(updates):
                return self._result(PersistenceStatus.BLOCKED, "failed_entry_update_duplicate")
            async with self._session_factory() as session, session.begin():
                if not await self._lock_scope(session, scope):
                    return self._result(PersistenceStatus.BLOCKED, "exact_project_scope_unresolved")
                revision_row = await self._revision_row(session, scope, revision_id, lock=True)
                if revision_row is None:
                    return self._result(PersistenceStatus.BLOCKED, "runtime_revision_missing")
                rows = list((await session.scalars(select(MaterializationEntryRow).where(
                    MaterializationEntryRow.revision_id == revision_id,
                ).order_by(MaterializationEntryRow.workspace_path).with_for_update())).all())
                generations = {row.generation for row in rows}
                if not rows or len(generations) != 1 or next(iter(generations)) != expected_generation:
                    return self._result(PersistenceStatus.CONFLICT, "stale_generation_cas", affected_rows=0)
                generation_cas = await session.execute(update(MaterializationEntryRow).where(
                    MaterializationEntryRow.revision_id == revision_id,
                    MaterializationEntryRow.generation == expected_generation,
                ).values(generation=expected_generation))
                if generation_cas.rowcount != len(rows):
                    raise _StaleGeneration(generation_cas.rowcount or 0)
                changed = await session.execute(update(ProjectRuntimeRevisionRow).where(
                    ProjectRuntimeRevisionRow.id == revision_id,
                    ProjectRuntimeRevisionRow.project_id == scope.project_id,
                    ProjectRuntimeRevisionRow.state == RevisionState.APPLYING.value,
                ).values(state=RevisionState.FAILED.value, failure_code=failure_code,
                         diagnostics=safe_diagnostics))
                if changed.rowcount != 1:
                    raise _PersistenceConflict("stale_revision_state_cas", changed.rowcount)
                by_path = {row.workspace_path: row for row in rows}
                for item in updates:
                    row = by_path.get(item.workspace_path)
                    if row is None or row.id != item.entry_id or not _same_entry_plan(row, item):
                        raise _PersistenceConflict("failed_entry_identity_conflict")
                    _update_entry_row(row, item, expected_generation)
                await session.flush()
                await self._append_audit(audit_writer, session, audit_record)
                saved_revision = await self._to_revision(session, revision_row)
                saved_entries = tuple(_to_entry(row) for row in rows)
                return self._result(PersistenceStatus.WRITTEN, "runtime_revision_failed",
                                    revision=saved_revision, entries=saved_entries,
                                    affected_rows=changed.rowcount)
        except _StaleGeneration as exc:
            return self._result(PersistenceStatus.CONFLICT, "stale_generation_cas", affected_rows=exc.affected_rows)
        except _PersistenceConflict as exc:
            return self._result(PersistenceStatus.CONFLICT, exc.reason_code, affected_rows=exc.affected_rows)
        except _AuditWriteFailed:
            return self._result(PersistenceStatus.BLOCKED, "audit_write_failed")
        except IntegrityError:
            return self._result(PersistenceStatus.CONFLICT, "failed_materialization_unique_conflict")
        except ValueError:
            return self._result(PersistenceStatus.BLOCKED, "failed_diagnostics_invalid")

    async def _scope_exists(self, session: AsyncSession, scope: RuntimeApiProjectScope) -> bool:
        return await self._scope_query(session, scope, lock=False)

    async def _lock_scope(self, session: AsyncSession, scope: RuntimeApiProjectScope) -> bool:
        return await self._scope_query(session, scope, lock=True)

    @staticmethod
    async def _scope_query(session: AsyncSession, scope: RuntimeApiProjectScope, *, lock: bool) -> bool:
        query = select(ProjectRow.id).join(
            ProjectPodRow, ProjectPodRow.project_id == ProjectRow.id
        ).where(
            ProjectRow.id == scope.project_id,
            ProjectRow.company_id == scope.company_id,
            ProjectRow.cabinet_id == scope.cabinet_id,
            ProjectPodRow.id == scope.pod_id,
            ProjectPodRow.project_id == scope.project_id,
        )
        if lock:
            query = query.with_for_update()
        return (await session.scalar(query)) is not None

    async def _revision_row(
        self, session: AsyncSession, scope: RuntimeApiProjectScope, revision_id: str, *, lock: bool
    ) -> ProjectRuntimeRevisionRow | None:
        query = select(ProjectRuntimeRevisionRow).where(
            ProjectRuntimeRevisionRow.id == revision_id,
            ProjectRuntimeRevisionRow.project_id == scope.project_id,
            or_(ProjectRuntimeRevisionRow.pod_id.is_(None),
                ProjectRuntimeRevisionRow.pod_id == scope.pod_id),
        )
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def _scoped_revision(
        self, session: AsyncSession, scope: RuntimeApiProjectScope, revision_id: str
    ) -> ProjectRuntimeRevisionRow | None:
        if not await self._scope_exists(session, scope):
            return None
        return await self._revision_row(session, scope, revision_id, lock=False)

    async def _to_revision(
        self, session: AsyncSession, row: ProjectRuntimeRevisionRow
    ) -> ProjectRuntimeRevision:
        bundle = await session.get(CapabilityBundleRow, row.bundle_id)
        if bundle is None:
            raise RuntimeError("runtime revision references a missing capability bundle")
        return ProjectRuntimeRevision(
            revision_id=row.id,
            project_id=row.project_id,
            revision=row.revision,
            bundle=CapabilityBundle(bundle.id, row.project_id, row.revision,
                                    _json_dict(bundle.manifest), bundle.digest),
            state=RevisionState(row.state),
            applied_revision_id=row.applied_revision_id,
            pod_id=row.pod_id,
            pod_uid=row.pod_uid,
            hydrate_generation=row.hydrate_generation,
            bridge_generation=row.bridge_generation,
            runtime_image_digest=row.runtime_image_digest,
            failure_code=row.failure_code,
            diagnostics=_json_dict(row.diagnostics or {}),
            created_by=row.created_by,
            source_event=row.source_event,
        )

    @staticmethod
    def _valid_new_revision(scope: RuntimeApiProjectScope, revision: ProjectRuntimeRevision) -> bool:
        try:
            manifest_digest_valid = canonical_digest(revision.bundle.manifest) == revision.bundle.digest
            diagnostics_valid = _safe_diagnostics_valid(revision.diagnostics)
        except (TypeError, ValueError):
            return False
        return bool(
            bool(_scope_values(scope))
            and revision.project_id == scope.project_id
            and revision.revision_id.strip()
            and len(revision.revision_id) <= 64
            and revision.revision > 0
            and revision.bundle.project_id == revision.project_id
            and revision.bundle.revision == revision.revision
            and revision.bundle.bundle_id.strip()
            and len(revision.bundle.bundle_id) <= 128
            and _DIGEST.fullmatch(revision.bundle.digest)
            and len(revision.bundle.digest) <= 128
            and manifest_digest_valid
            and (revision.pod_id is None or revision.pod_id == scope.pod_id)
            and revision.state in {RevisionState.PLANNED, RevisionState.VALIDATED}
            and revision.hydrate_generation >= 0
            and revision.bridge_generation >= 0
            and (revision.runtime_image_digest is None or _DIGEST.fullmatch(revision.runtime_image_digest))
        ) and not _contains_inline_secret(revision.bundle.manifest) and diagnostics_valid

    @staticmethod
    def _same_snapshot(
        row: ProjectRuntimeRevisionRow, bundle_row: CapabilityBundleRow | None,
        revision: ProjectRuntimeRevision,
    ) -> bool:
        return bool(
            bundle_row is not None
            and row.project_id == revision.project_id
            and row.revision == revision.revision
            and row.bundle_id == revision.bundle.bundle_id
            and bundle_row.digest == revision.bundle.digest
            and _json_value(bundle_row.manifest) == _json_value(revision.bundle.manifest)
            and row.pod_id == revision.pod_id
            and row.pod_uid == revision.pod_uid
            and row.hydrate_generation == revision.hydrate_generation
            and row.bridge_generation == revision.bridge_generation
            and row.runtime_image_digest == revision.runtime_image_digest
            and row.created_by == revision.created_by
            and row.source_event == revision.source_event
        )

    @staticmethod
    def _validate_apply_input(
        scope: RuntimeApiProjectScope, revision_id: str, expected_generation: int,
        entries: list[MaterializationEntry], evidence: DependencyEvidence | None, at: datetime | None,
        audit_writer: TransactionalRuntimeAuditPort | None,
        audit_record: RuntimeAuditRecord,
    ) -> str | None:
        if (not _scope_values(scope) or not revision_id.strip()
                or not isinstance(expected_generation, int) or isinstance(expected_generation, bool)
                or expected_generation < 0 or audit_writer is None
                or not SqlAlchemyRuntimeRevisionRepository._valid_audit(
                    audit_record, scope, expected_generation + 1, "started"
                )):
            return "materialization_apply_input_invalid"
        if evidence is None or at is None or at.tzinfo is None or at.utcoffset() is None:
            return "runtime_dependency_evidence_missing"
        if not entries:
            return "empty_materialization_generation_unfenced_by_schema"
        if len({item.workspace_path for item in entries}) != len(entries):
            return "materialization_workspace_path_duplicate"
        if len({item.idempotency_key for item in entries}) != len(entries):
            return "materialization_idempotency_key_duplicate"
        for item in entries:
            path = PurePosixPath(item.workspace_path)
            try:
                _json_value(item.provenance)
            except (TypeError, ValueError):
                return "materialization_entry_input_invalid"
            if (item.revision_id != revision_id or item.generation != expected_generation + 1
                    or item.status is not MaterializationStatus.PLANNED
                    or not item.idempotency_key or len(item.idempotency_key) > 128
                    or not _DIGEST.fullmatch(item.operation_digest or "")
                    or not item.workspace_path or path.is_absolute() or ".." in path.parts or chr(92) in item.workspace_path
                    or len(item.workspace_path) > 1024 or not item.kind or len(item.kind) > 32
                    or (item.content_hash is not None and not _DIGEST.fullmatch(item.content_hash))
                    or _contains_inline_secret(item.provenance)):
                return "materialization_entry_input_invalid"
            if not item.entry_id.strip() or len(item.entry_id) > 64:
                return "materialization_entry_id_invalid"
        return None

    @staticmethod
    def _resolve_replay(
        entries: list[MaterializationEntry], existing_rows: list[MaterializationEntryRow]
    ) -> tuple[PersistenceStatus, str] | None:
        if not existing_rows:
            return None
        by_key = {row.idempotency_key: row for row in existing_rows}
        collisions = [item for item in entries if item.idempotency_key in by_key]
        if not collisions:
            return None
        if len(collisions) != len(entries) or len(by_key) != len(entries):
            return PersistenceStatus.CONFLICT, "idempotency_key_plan_conflict"
        for item in entries:
            row = by_key.get(item.idempotency_key)
            if row is None or not _same_entry_plan(row, item):
                return PersistenceStatus.CONFLICT, "idempotency_key_plan_conflict"
        return PersistenceStatus.REPLAY, "materialization_exact_plan_replay"

    @staticmethod
    async def _replace_entry_rows(
        session: AsyncSession, revision_id: str, existing_rows: list[MaterializationEntryRow],
        entries: list[MaterializationEntry], generation: int,
    ) -> None:
        by_path = {row.workspace_path: row for row in existing_rows}
        wanted = {item.workspace_path for item in entries}
        for row in existing_rows:
            if row.workspace_path not in wanted:
                await session.delete(row)
        for item in entries:
            row = by_path.get(item.workspace_path)
            if row is None:
                session.add(MaterializationEntryRow(
                    id=item.entry_id,
                    revision_id=revision_id,
                    workspace_path=item.workspace_path,
                    kind=item.kind,
                    idempotency_key=item.idempotency_key,
                    operation_digest=item.operation_digest,
                    generation=generation,
                    status=MaterializationStatus.PLANNED.value,
                    content_hash=item.content_hash,
                    source_asset_id=item.source_asset_id,
                    source_capability_id=item.source_capability_id,
                    provenance=_json_value(item.provenance),
                    error=None,
                ))
            else:
                _update_entry_row(row, item, generation)

    @staticmethod
    def _valid_audit(record: RuntimeAuditRecord, scope: RuntimeApiProjectScope,
                     generation: int, outcome: str) -> bool:
        return bool(
            isinstance(record, RuntimeAuditRecord)
            and _CODE.fullmatch(record.event)
            and _CODE.fullmatch(record.outcome)
            and _CODE.fullmatch(record.reason_code)
            and all(_DIGEST.fullmatch(value) for value in (
                record.resource_digest, record.correlation_digest, record.plan_digest,
            ))
            and record.generation == generation
            and record.outcome == outcome
            and record.resource_digest == canonical_digest(scope.resource_key)
        )

    @staticmethod
    async def _append_audit(
        audit_writer: TransactionalRuntimeAuditPort,
        session: AsyncSession,
        record: RuntimeAuditRecord,
    ) -> None:
        try:
            await audit_writer.append(session=session, record=record)
        except Exception as exc:
            raise _AuditWriteFailed from exc

    @staticmethod
    def _result(
        status: PersistenceStatus, reason_code: str, *,
        revision: ProjectRuntimeRevision | None = None,
        entries: tuple[MaterializationEntry, ...] = (),
        affected_rows: int | None = None,
    ) -> PersistenceResult:
        return PersistenceResult(status, reason_code, revision, entries, affected_rows)


def _to_entry(row: MaterializationEntryRow) -> MaterializationEntry:
    return MaterializationEntry(
        entry_id=row.id,
        revision_id=row.revision_id,
        workspace_path=row.workspace_path,
        kind=row.kind,
        generation=row.generation,
        status=MaterializationStatus(row.status),
        content_hash=row.content_hash,
        source_asset_id=row.source_asset_id,
        source_capability_id=row.source_capability_id,
        provenance=_json_dict(row.provenance or {}),
        error=row.error,
        idempotency_key=row.idempotency_key,
        operation_digest=row.operation_digest,
    )


def _same_entry_plan(row: MaterializationEntryRow, entry: MaterializationEntry) -> bool:
    return bool(
        row.workspace_path == entry.workspace_path
        and row.id == entry.entry_id
        and row.kind == entry.kind
        and row.idempotency_key == entry.idempotency_key
        and row.operation_digest == entry.operation_digest
        and row.source_asset_id == entry.source_asset_id
        and row.source_capability_id == entry.source_capability_id
        and _json_value(row.provenance or {}) == _json_value(entry.provenance)
    )


def _update_entry_row(row: MaterializationEntryRow, entry: MaterializationEntry, generation: int) -> None:
    row.kind = entry.kind
    row.idempotency_key = entry.idempotency_key
    row.operation_digest = entry.operation_digest
    row.generation = generation
    row.status = entry.status.value
    row.content_hash = entry.content_hash
    row.source_asset_id = entry.source_asset_id
    row.source_capability_id = entry.source_capability_id
    row.provenance = _json_value(entry.provenance)
    row.error = entry.error if entry.error and _CODE.fullmatch(entry.error) else None


def _scope_values(scope: object) -> tuple[str, str, str, str, str] | None:
    from prodavan.application.migration.runtime_api.contracts import RuntimeApiProjectScope

    if not isinstance(scope, RuntimeApiProjectScope):
        return None
    values = (scope.project_id, scope.company_id, scope.cabinet_id, scope.pod_id, scope.resource_key)
    return values if all(isinstance(value, str) and value.strip() for value in values) else None


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("runtime persistence JSON number must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("runtime persistence JSON value is unsupported")


def _json_dict(value: object) -> dict[str, Any]:
    converted = _json_value(value)
    if not isinstance(converted, dict):
        raise ValueError("runtime persistence object value is invalid")
    return converted


def _contains_inline_secret(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            marked = any(marker in lowered for marker in _SECRET_MARKERS)
            is_ref = lowered.endswith(("_ref", "_reference")) and isinstance(item, str) and item.startswith(
                ("vault://", "secret://", "file://", "${")
            )
            if marked and not is_ref:
                return True
            if isinstance(item, str) and item.lower().startswith(("password=", "secret=", "token=", "api_key=")):
                return True
            if _contains_inline_secret(item):
                return True
    elif isinstance(value, (tuple, list)):
        return any(_contains_inline_secret(item) for item in value)
    return False


def _safe_diagnostics(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("diagnostics must be a mapping")
    clean: dict[str, object] = {}
    for key, item in value.items():
        name = str(key)
        if not _CODE.fullmatch(name):
            raise ValueError("diagnostic key must be a safe code")
        if isinstance(item, str):
            if not _CODE.fullmatch(item):
                raise ValueError("diagnostic value must be a safe code")
            clean[name] = item
        elif isinstance(item, (int, bool)) or item is None:
            clean[name] = item
        elif isinstance(item, (tuple, list)) and all(isinstance(code, str) and _CODE.fullmatch(code) for code in item):
            clean[name] = list(item)
        else:
            raise ValueError("diagnostics may contain only redacted codes and scalar counts")
    return clean


def _safe_diagnostics_valid(value: Mapping[str, object]) -> bool:
    try:
        _safe_diagnostics(value)
    except (TypeError, ValueError):
        return False
    return True


def _evidence_blocker(
    evidence: DependencyEvidence | None,
    at: datetime | None,
    runtime_image_digest: str | None,
    bundle: CapabilityBundleRow | None,
    entries: list[MaterializationEntry],
) -> str | None:
    if evidence is None or at is None or at.tzinfo is None or at.utcoffset() is None or bundle is None:
        return "runtime_dependency_evidence_missing"
    if not evidence.contracts_ready:
        return "upstream_resource_contract_missing"
    if (not evidence.bridge_lease_ref or not evidence.bridge_lease_ref.startswith(("lease://", "bridge-lease://"))
            or evidence.bridge_lease_generation < 1 or evidence.bridge_lease_expires_at is None):
        return "bridge_lease_evidence_missing"
    if evidence.revoked:
        return "bridge_lease_revoked"
    if evidence.bridge_lease_expires_at <= at:
        return "bridge_lease_expired"
    if (runtime_image_digest is None or not _DIGEST.fullmatch(runtime_image_digest)
            or evidence.image_digest != runtime_image_digest):
        return "immutable_image_digest_mismatch"
    manifest = _json_dict(bundle.manifest)
    desired_image = manifest.get("runtime_image_digest", manifest.get("image_digest"))
    if desired_image != runtime_image_digest:
        return "desired_immutable_image_digest_mismatch"
    if not evidence.agent_run_id:
        return "agent_run_link_missing"
    if not evidence.approval_id:
        return "approval_link_missing"
    if not evidence.event_id:
        return "event_link_missing"
    required_refs = _secret_refs(manifest)
    for entry in entries:
        if _contains_inline_secret(entry.provenance):
            return "inline_secret_material_forbidden"
        required_refs.update(_secret_refs(entry.provenance))
    if not evidence.projected_secret_refs:
        return "projected_secret_reference_missing"
    if not required_refs.issubset(set(evidence.projected_secret_refs)):
        return "projected_secret_reference_missing"
    return None


def _secret_refs(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if (any(marker in lowered for marker in _SECRET_MARKERS)
                    and lowered.endswith(("_ref", "_reference"))
                    and isinstance(item, str)
                    and item.startswith(("vault://", "secret://", "file://", "${"))):
                refs.add(item)
            refs.update(_secret_refs(item))
    elif isinstance(value, (tuple, list)):
        for item in value:
            refs.update(_secret_refs(item))
    return refs


__all__ = [
    "PersistenceResult",
    "PersistenceStatus",
    "SqlAlchemyRuntimeRevisionRepository",
    "TransactionalRuntimeAuditPort",
]
