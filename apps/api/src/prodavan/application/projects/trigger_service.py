"""Project trigger ingress queue (L07) — claimable outbox lite."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.projects import PROJECT_TRIGGER_KINDS, ProjectStatus, TriggerStatus
from prodavan.domain.projects.types import (
    PAUSE_EXEMPT_TRIGGER_KINDS,
    SUBSCRIPTION_EXEMPT_TRIGGER_KINDS,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow, ProjectTriggerRow


def _trigger_public(row: ProjectTriggerRow) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "kind": row.kind,
        "payload": row.payload,
        "status": row.status,
        "attempts": row.attempts,
        "lease_until": row.lease_until.isoformat() if row.lease_until else None,
        "leased_by": row.leased_by,
        "available_at": row.available_at.isoformat() if row.available_at else None,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class ProjectTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, *, project_id: str, kind: str, payload: dict | None = None) -> dict:
        if kind not in PROJECT_TRIGGER_KINDS:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"unsupported trigger kind: {kind}",
            )
        project = await self._session.get(ProjectRow, project_id)
        if project is None or project.status == ProjectStatus.DELETED:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        if kind not in SUBSCRIPTION_EXEMPT_TRIGGER_KINDS:
            await CompanySubscriptionGate(self._session).require_active(project.company_id)
        if project.status == ProjectStatus.PAUSED and kind not in PAUSE_EXEMPT_TRIGGER_KINDS:
            raise AppError(
                code="PROJECT_PAUSED",
                title="Project paused",
                status=409,
                detail="project is paused",
            )
        row = ProjectTriggerRow(
            project_id=project_id,
            kind=kind,
            payload=payload or {},
            status=TriggerStatus.QUEUED,
        )
        self._session.add(row)
        await self._session.flush()
        public = _trigger_public(row)
        from prodavan.core.events.deferred import schedule_project_trigger_publish

        schedule_project_trigger_publish(
            self._session,
            event_id=row.id,
            kind=row.kind,
            project_id=row.project_id,
            company_id=project.company_id,
            cabinet_id=project.cabinet_id,
            payload=row.payload if isinstance(row.payload, dict) else {},
            occurred_at=public.get("created_at"),
        )
        return public

    async def list_for_project(self, *, project_id: str, limit: int = 50) -> list[dict]:
        q = await self._session.execute(
            select(ProjectTriggerRow)
            .where(ProjectTriggerRow.project_id == project_id)
            .order_by(ProjectTriggerRow.created_at.desc())
            .limit(limit)
        )
        return [_trigger_public(r) for r in q.scalars().all()]

    async def list_active_project_ids_with_queued(self, *, limit: int = 50) -> list[str]:
        """Distinct active projects with at least one claimable queued trigger."""
        now = datetime.now(UTC)
        q = await self._session.execute(
            select(ProjectTriggerRow.project_id)
            .join(ProjectRow, ProjectRow.id == ProjectTriggerRow.project_id)
            .where(
                ProjectTriggerRow.status == TriggerStatus.QUEUED,
                ProjectRow.status == ProjectStatus.ACTIVE,
                or_(ProjectTriggerRow.available_at.is_(None), ProjectTriggerRow.available_at <= now),
                or_(ProjectTriggerRow.lease_until.is_(None), ProjectTriggerRow.lease_until < now),
            )
            .distinct()
            .limit(limit)
        )
        return list(q.scalars().all())

    async def claim_next(
        self,
        *,
        project_id: str,
        worker_id: str | None = None,
    ) -> ProjectTriggerRow | None:
        """Claim next claimable queued trigger (SKIP LOCKED + lease).

        Refuses to claim when the project is not ACTIVE (paused/deleted backlog stays queued).
        """
        now = datetime.now(UTC)
        project = await self._session.get(ProjectRow, project_id)
        if project is None or project.status != ProjectStatus.ACTIVE:
            return None
        lease_sec = max(15, int(settings.trigger_outbox_lease_sec))
        q = await self._session.execute(
            select(ProjectTriggerRow)
            .where(
                ProjectTriggerRow.project_id == project_id,
                ProjectTriggerRow.status == TriggerStatus.QUEUED,
                or_(ProjectTriggerRow.available_at.is_(None), ProjectTriggerRow.available_at <= now),
                or_(ProjectTriggerRow.lease_until.is_(None), ProjectTriggerRow.lease_until < now),
            )
            .order_by(ProjectTriggerRow.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = q.scalar_one_or_none()
        if row is None:
            return None
        row.attempts = int(row.attempts or 0) + 1
        row.lease_until = now + timedelta(seconds=lease_sec)
        row.leased_by = (worker_id or f"worker_{uuid.uuid4().hex[:8]}")[:64]
        row.last_error = None
        await self._session.flush()
        return row

    async def claim_by_id(
        self,
        *,
        trigger_id: str,
        worker_id: str | None = None,
    ) -> ProjectTriggerRow | None:
        """Claim a specific queued trigger by id (Kafka per-id cutover path)."""
        now = datetime.now(UTC)
        lease_sec = max(15, int(settings.trigger_outbox_lease_sec))
        q = await self._session.execute(
            select(ProjectTriggerRow)
            .where(
                ProjectTriggerRow.id == trigger_id,
                ProjectTriggerRow.status == TriggerStatus.QUEUED,
                or_(ProjectTriggerRow.available_at.is_(None), ProjectTriggerRow.available_at <= now),
                or_(ProjectTriggerRow.lease_until.is_(None), ProjectTriggerRow.lease_until < now),
            )
            .with_for_update(skip_locked=True)
        )
        row = q.scalar_one_or_none()
        if row is None:
            return None
        project = await self._session.get(ProjectRow, row.project_id)
        if project is None or project.status != ProjectStatus.ACTIVE:
            return None
        row.attempts = int(row.attempts or 0) + 1
        row.lease_until = now + timedelta(seconds=lease_sec)
        row.leased_by = (worker_id or f"worker_{uuid.uuid4().hex[:8]}")[:64]
        row.last_error = None
        await self._session.flush()
        return row

    async def mark_done(self, row: ProjectTriggerRow) -> None:
        row.status = TriggerStatus.DONE
        row.lease_until = None
        row.leased_by = None
        row.available_at = None
        row.last_error = None
        await self._session.flush()

    async def mark_failed(self, row: ProjectTriggerRow, *, reason: str) -> None:
        row.status = TriggerStatus.FAILED
        row.lease_until = None
        row.leased_by = None
        row.available_at = None
        row.last_error = reason[:2000]
        await self._session.flush()

    async def release_for_retry(self, row: ProjectTriggerRow, *, reason: str) -> bool:
        """Re-queue with backoff if attempts remain; otherwise fail. Returns True if retried."""
        max_attempts = max(1, int(settings.trigger_outbox_max_attempts))
        detail = reason[:2000]
        if int(row.attempts or 0) >= max_attempts:
            await self.mark_failed(row, reason=detail)
            return False
        backoff = max(0.0, float(settings.trigger_outbox_backoff_sec))
        # Linear backoff by attempt count.
        delay = backoff * max(1, int(row.attempts or 1))
        row.status = TriggerStatus.QUEUED
        row.lease_until = None
        row.leased_by = None
        row.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        row.last_error = detail
        await self._session.flush()
        return True
