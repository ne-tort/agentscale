"""Module N:M cabinet + project bindings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_materialize_service import ModuleMaterializeService
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleProjectBindingRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class ModuleBindingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_cabinet_ids(self, module_id: str) -> list[str]:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.cabinet_id)
            .where(ModuleCabinetBindingRow.module_id == module_id)
            .order_by(ModuleCabinetBindingRow.cabinet_id)
        )
        return list(q.scalars().all())

    async def list_cabinets(self, module_id: str) -> list[dict]:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow, CabinetInstanceRow.name)
            .join(CabinetInstanceRow, CabinetInstanceRow.id == ModuleCabinetBindingRow.cabinet_id)
            .where(ModuleCabinetBindingRow.module_id == module_id)
            .order_by(CabinetInstanceRow.name)
        )
        return [
            {"cabinet_id": row.cabinet_id, "cabinet_name": name}
            for row, name in q.all()
        ]

    async def list_project_ids(self, module_id: str) -> list[str]:
        q = await self._session.execute(
            select(ModuleProjectBindingRow.project_id)
            .where(ModuleProjectBindingRow.module_id == module_id)
            .order_by(ModuleProjectBindingRow.project_id)
        )
        return list(q.scalars().all())

    async def has_cabinet_binding(self, module_id: str, cabinet_id: str) -> bool:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.id).where(
                ModuleCabinetBindingRow.module_id == module_id,
                ModuleCabinetBindingRow.cabinet_id == cabinet_id,
            )
        )
        return q.scalar_one_or_none() is not None

    async def replace_cabinet_bindings(self, module_id: str, cabinet_ids: list[str]) -> list[str]:
        unique = list(dict.fromkeys(cabinet_ids))
        for cid in unique:
            cab = await self._session.get(CabinetInstanceRow, cid)
            if cab is None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"unknown cabinet_id: {cid}",
                )

        existing = await self._session.execute(
            select(ModuleCabinetBindingRow).where(ModuleCabinetBindingRow.module_id == module_id)
        )
        existing_rows = list(existing.scalars().all())
        old_cabinet_ids = {row.cabinet_id for row in existing_rows}
        removed = old_cabinet_ids - set(unique)

        for row in existing_rows:
            await self._session.delete(row)
        await self._session.flush()

        if removed:
            await self._revoke_projects_for_cabinets(module_id, removed)

        for cid in unique:
            self._session.add(ModuleCabinetBindingRow(module_id=module_id, cabinet_id=cid))
        await self._session.flush()

        added = set(unique) - old_cabinet_ids
        materialize = ModuleMaterializeService(self._session)
        for cid in added:
            await materialize.install(cabinet_id=cid, module_id=module_id)
        for cid in removed:
            await materialize.uninstall(cabinet_id=cid, module_id=module_id)

        return unique

    async def bind_project(self, module_id: str, project_id: str) -> None:
        project = await self._session.get(ProjectRow, project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
        if not await self.has_cabinet_binding(module_id, project.cabinet_id):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="module is not bound to project's cabinet",
            )
        existing = await self._session.execute(
            select(ModuleProjectBindingRow).where(
                ModuleProjectBindingRow.module_id == module_id,
                ModuleProjectBindingRow.project_id == project_id,
            )
        )
        if existing.scalar_one_or_none() is None:
            self._session.add(
                ModuleProjectBindingRow(module_id=module_id, project_id=project_id)
            )
            await self._session.flush()

    async def revoke_project(self, module_id: str, project_id: str) -> None:
        existing = await self._session.execute(
            select(ModuleProjectBindingRow).where(
                ModuleProjectBindingRow.module_id == module_id,
                ModuleProjectBindingRow.project_id == project_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project binding not found")
        await self._session.delete(row)
        await self._session.flush()

    async def _revoke_projects_for_cabinets(self, module_id: str, cabinet_ids: set[str]) -> None:
        if not cabinet_ids:
            return
        q = await self._session.execute(
            select(ModuleProjectBindingRow, ProjectRow.cabinet_id)
            .join(ProjectRow, ProjectRow.id == ModuleProjectBindingRow.project_id)
            .where(
                ModuleProjectBindingRow.module_id == module_id,
                ProjectRow.cabinet_id.in_(cabinet_ids),
            )
        )
        for row, _cab_id in q.all():
            await self._session.delete(row)
        await self._session.flush()

    async def cabinet_binding_count(self, module_id: str) -> int:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.id).where(ModuleCabinetBindingRow.module_id == module_id)
        )
        return len(list(q.scalars().all()))

    async def project_binding_count(self, module_id: str) -> int:
        q = await self._session.execute(
            select(ModuleProjectBindingRow.id).where(ModuleProjectBindingRow.module_id == module_id)
        )
        return len(list(q.scalars().all()))
