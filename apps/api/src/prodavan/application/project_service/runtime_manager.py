"""ProjectRuntimeUnit CRUD + ContainerRuntimePort orchestration."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.project_service.adapters.stub_container_runtime import (
    StubContainerRuntimeAdapter,
)
from prodavan.application.project_service.lifecycle_emitter import ProjectLifecycleEmitter
from prodavan.application.project_service.ports.container_runtime import ContainerRuntimePort
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import (
    ProjectRuntimeUnitKind,
    ProjectRuntimeUnitStatus,
    container_ref_for,
    new_runtime_unit_id,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow, ProjectRuntimeUnitRow


class ProjectRuntimeManager:
    def __init__(
        self,
        session: AsyncSession,
        *,
        runtime: ContainerRuntimePort | None = None,
        events: ProjectLifecycleEmitter | None = None,
    ) -> None:
        self._session = session
        self._runtime = runtime or StubContainerRuntimeAdapter()
        self._events = events or ProjectLifecycleEmitter(session)

    async def list_units(self, project_id: str) -> list[dict]:
        q = await self._session.execute(
            select(ProjectRuntimeUnitRow)
            .where(
                ProjectRuntimeUnitRow.project_id == project_id,
                ProjectRuntimeUnitRow.status != ProjectRuntimeUnitStatus.DELETED,
            )
            .order_by(ProjectRuntimeUnitRow.created_at)
        )
        return [self._public(row) for row in q.scalars().all()]

    async def attach_unit(
        self,
        *,
        project: ProjectRow,
        principal: Principal,
        kind: str = ProjectRuntimeUnitKind.PRIMARY,
        start: bool = False,
    ) -> dict:
        if kind not in {k.value for k in ProjectRuntimeUnitKind}:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"invalid runtime unit kind: {kind}",
            )
        runtime_ref = container_ref_for(project.workspace_key)
        status = ProjectRuntimeUnitStatus.RUNNING if start else ProjectRuntimeUnitStatus.PENDING
        unit = ProjectRuntimeUnitRow(
            id=new_runtime_unit_id(),
            project_id=project.id,
            kind=kind,
            status=status,
            runtime_ref=runtime_ref,
        )
        self._session.add(unit)
        await self._session.flush()
        if project.primary_runtime_unit_id is None and kind == ProjectRuntimeUnitKind.PRIMARY:
            project.primary_runtime_unit_id = unit.id
            project.container_ref = runtime_ref
        if start and runtime_ref:
            await self._runtime.ensure_running(runtime_ref=runtime_ref)
            unit.status = ProjectRuntimeUnitStatus.RUNNING
        await self._events.emit(
            event_type="project.runtime_unit.attached",
            company_id=project.company_id,
            project_id=project.id,
            cabinet_id=project.cabinet_id,
            principal=principal,
            payload={"runtime_unit_id": unit.id, "kind": kind, "runtime_ref": runtime_ref},
        )
        if start and unit.status == ProjectRuntimeUnitStatus.RUNNING:
            await self._events.emit(
                event_type="project.started",
                company_id=project.company_id,
                project_id=project.id,
                cabinet_id=project.cabinet_id,
                principal=principal,
                payload={"runtime_unit_id": unit.id},
            )
        return self._public(unit)

    async def detach_unit(
        self,
        *,
        project: ProjectRow,
        unit_id: str,
        principal: Principal,
    ) -> dict:
        unit = await self._session.get(ProjectRuntimeUnitRow, unit_id)
        if unit is None or unit.project_id != project.id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Runtime unit not found")
        if unit.runtime_ref:
            await self._runtime.terminate(runtime_ref=unit.runtime_ref)
        unit.status = ProjectRuntimeUnitStatus.DELETED
        if project.primary_runtime_unit_id == unit.id:
            project.primary_runtime_unit_id = None
        await self._events.emit(
            event_type="project.runtime_unit.detached",
            company_id=project.company_id,
            project_id=project.id,
            cabinet_id=project.cabinet_id,
            principal=principal,
            payload={"runtime_unit_id": unit.id},
        )
        return self._public(unit)

    async def pause_all(self, project: ProjectRow) -> None:
        q = await self._session.execute(
            select(ProjectRuntimeUnitRow).where(
                ProjectRuntimeUnitRow.project_id == project.id,
                ProjectRuntimeUnitRow.status.in_(
                    [ProjectRuntimeUnitStatus.RUNNING, ProjectRuntimeUnitStatus.PENDING]
                ),
            )
        )
        for unit in q.scalars().all():
            if unit.runtime_ref:
                await self._runtime.pause(runtime_ref=unit.runtime_ref)
            unit.status = ProjectRuntimeUnitStatus.PAUSED

    @staticmethod
    def _public(row: ProjectRuntimeUnitRow) -> dict:
        return {
            "id": row.id,
            "project_id": row.project_id,
            "kind": row.kind,
            "status": row.status,
            "runtime_ref": row.runtime_ref,
            "last_error": row.last_error,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
