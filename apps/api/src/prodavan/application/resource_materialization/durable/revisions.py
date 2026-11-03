"""Immutable desired-revision snapshots written in the apply transaction.

``runtime_materialization_attempts.applied_revision_id`` is a foreign key to
``project_runtime_revisions`` and is mandatory for a positive outcome, so an apply
must persist the exact desired snapshot it advanced to. This module writes it with
the caller's session, which keeps the snapshot, the attempt receipt, the head CAS
and the audit row in one transaction.

The snapshot is content-addressed: ``capability_bundles.digest`` is the canonical
digest of the manifest, and the manifest carries only digests, counts and the
applied plan identity of the resource set — never a payload. The revision row stays
in ``validated``: publishing ``ready`` is a runtime-readback decision that this
adapter deliberately does not take.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.migration.runtime_materialization.state_machine import (
    canonical_digest,
)
from prodavan.application.runtime_revision import RevisionState
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow
from prodavan.infrastructure.persistence.models.runtime_revisions import (
    CapabilityBundleRow,
    ProjectRuntimeRevisionRow,
)

from .contracts import ApplyPlan, RevisionScopeUnavailable


@dataclass(frozen=True, slots=True)
class RevisionApplicationScope:
    """Exact project runtime scope, resolved from the database, never from input."""

    project_id: str
    company_id: str
    cabinet_id: str
    pod_id: str

    def __post_init__(self) -> None:
        for value in (self.project_id, self.company_id, self.cabinet_id, self.pod_id):
            if not isinstance(value, str) or not value.strip():
                raise RevisionScopeUnavailable("complete project runtime scope is required")


class SqlAlchemyRevisionSnapshotStore:
    """Session-bound writer of immutable desired snapshots."""

    async def resolve_scope(
        self, session: AsyncSession, *, project_id: str
    ) -> RevisionApplicationScope:
        project = await session.scalar(
            select(ProjectRow).where(ProjectRow.id == project_id)
        )
        if project is None:
            raise RevisionScopeUnavailable("project_not_found")
        if not project.company_id or not project.cabinet_id:
            raise RevisionScopeUnavailable("project_scope_incomplete")
        pod_ids = (
            await session.scalars(
                select(ProjectPodRow.id).where(ProjectPodRow.project_id == project_id)
            )
        ).all()
        if not pod_ids:
            raise RevisionScopeUnavailable("project_runtime_pod_missing")
        if len(set(pod_ids)) != 1:
            # Fail closed: two runtime pods would make "the" exact scope ambiguous.
            raise RevisionScopeUnavailable("project_runtime_pod_ambiguous")
        return RevisionApplicationScope(
            project_id=project.id,
            company_id=project.company_id,
            cabinet_id=project.cabinet_id,
            pod_id=pod_ids[0],
        )

    async def persist_desired(
        self,
        session: AsyncSession,
        *,
        scope: RevisionApplicationScope,
        plan: ApplyPlan,
        revision_number: int,
    ) -> str:
        """Persist the desired snapshot of ``plan`` and return its revision id.

        Replaying the same (project, revision number) with the same bundle digest
        returns the stored row; a different digest is a conflict, so a revision
        number can never be rewritten with another snapshot.
        """
        if revision_number < 1:
            raise ValueError("revision number must be positive")
        manifest = self._manifest(plan, revision_number)
        digest = canonical_digest(manifest)
        bundle_id = f"bundle_{digest[7:39]}"

        existing = await session.scalar(
            select(ProjectRuntimeRevisionRow).where(
                ProjectRuntimeRevisionRow.project_id == scope.project_id,
                ProjectRuntimeRevisionRow.revision == revision_number,
            )
        )
        if existing is not None:
            bundle = await session.get(CapabilityBundleRow, existing.bundle_id)
            if bundle is None or bundle.digest != digest:
                raise RevisionScopeUnavailable("project_revision_number_conflict")
            return existing.id

        bundle = await session.get(CapabilityBundleRow, bundle_id)
        if bundle is None:
            owner = await session.scalar(
                select(CapabilityBundleRow.id).where(CapabilityBundleRow.digest == digest)
            )
            if owner is not None:
                bundle = await session.get(CapabilityBundleRow, owner)
            else:
                try:
                    async with session.begin_nested():
                        session.add(CapabilityBundleRow(id=bundle_id, manifest=manifest, digest=digest))
                        await session.flush()
                except IntegrityError as exc:
                    raise RevisionScopeUnavailable("bundle_digest_identity_conflict") from exc
        elif bundle.digest != digest:
            raise RevisionScopeUnavailable("bundle_id_payload_conflict")

        row = ProjectRuntimeRevisionRow(
            id=f"rr_{uuid.uuid4().hex[:16]}",
            project_id=scope.project_id,
            revision=revision_number,
            bundle_id=bundle_id,
            state=RevisionState.VALIDATED.value,
            applied_revision_id=None,
            pod_id=scope.pod_id,
            pod_uid=None,
            hydrate_generation=0,
            bridge_generation=0,
            runtime_image_digest=None,
            failure_code=None,
            diagnostics=None,
            created_by=plan.created_by,
            source_event=plan.source_event or "resource-materialization",
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError as exc:
            raise RevisionScopeUnavailable("project_revision_number_conflict") from exc
        return row.id

    @staticmethod
    def _manifest(plan: ApplyPlan, revision_number: int) -> dict[str, object]:
        return {
            "kind": "resource-materialization",
            "project_id": plan.project_id,
            "resource_key": plan.resource_key,
            "revision": revision_number,
            "snapshot_digest": plan.snapshot_digest,
            "operation_set_digest": plan.operation_set_digest,
            "operation_count": len(plan.operations),
            "rollback_of_generation": plan.rollback_of_generation,
        }


__all__ = ["RevisionApplicationScope", "SqlAlchemyRevisionSnapshotStore"]
