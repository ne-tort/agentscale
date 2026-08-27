"""Module N:M cabinet + project bindings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_materialize_service import ModuleMaterializeService
from prodavan.domain.cabinets.types import CabinetCompanyGrantScope
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.cabinets import CabinetCompanyGrantRow, CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleCompanyGrantRow,
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

    async def list_module_ids_for_cabinet(self, cabinet_id: str) -> list[str]:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.module_id)
            .where(ModuleCabinetBindingRow.cabinet_id == cabinet_id)
            .order_by(ModuleCabinetBindingRow.module_id)
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

    async def replace_module_bindings_for_cabinet(
        self, cabinet_id: str, module_ids: list[str]
    ) -> list[str]:
        """Replace all module bindings on a cabinet (admin bidirectional UI)."""
        from prodavan.infrastructure.persistence.models.modules import ModuleRow

        unique = list(dict.fromkeys(module_ids))
        cab = await self._session.get(CabinetInstanceRow, cabinet_id)
        if cab is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="cabinet not found")
        for mid in unique:
            mod = await self._session.get(ModuleRow, mid)
            if mod is None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"unknown module_id: {mid}",
                )

        existing = await self._session.execute(
            select(ModuleCabinetBindingRow).where(ModuleCabinetBindingRow.cabinet_id == cabinet_id)
        )
        existing_rows = list(existing.scalars().all())
        old_ids = {row.module_id for row in existing_rows}
        new_set = set(unique)
        removed = old_ids - new_set
        added = new_set - old_ids

        for row in existing_rows:
            if row.module_id in removed:
                await self._session.delete(row)
        await self._session.flush()

        for mid in unique:
            if mid in added:
                self._session.add(ModuleCabinetBindingRow(module_id=mid, cabinet_id=cabinet_id))
        await self._session.flush()

        materialize = ModuleMaterializeService(self._session)
        for mid in added:
            await materialize.install(cabinet_id=cabinet_id, module_id=mid)
        for mid in removed:
            await self._revoke_projects_for_cabinets(mid, {cabinet_id})
            await materialize.uninstall(cabinet_id=cabinet_id, module_id=mid)

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

    async def list_company_ids(self, module_id: str) -> list[str]:
        q = await self._session.execute(
            select(ModuleCompanyGrantRow.company_id)
            .where(
                ModuleCompanyGrantRow.module_id == module_id,
                ModuleCompanyGrantRow.status == "active",
            )
            .order_by(ModuleCompanyGrantRow.company_id)
        )
        return list(q.scalars().all())

    async def list_companies(self, module_id: str) -> list[dict]:
        q = await self._session.execute(
            select(ModuleCompanyGrantRow, CompanyRow.name)
            .join(CompanyRow, CompanyRow.id == ModuleCompanyGrantRow.company_id)
            .where(
                ModuleCompanyGrantRow.module_id == module_id,
                ModuleCompanyGrantRow.status == "active",
            )
            .order_by(CompanyRow.name)
        )
        return [
            {"company_id": grant.company_id, "company_name": name}
            for grant, name in q.all()
        ]

    async def replace_company_grants(self, module_id: str, company_ids: list[str]) -> list[str]:
        old_ids = set(await self.list_company_ids(module_id))
        unique = list(dict.fromkeys(company_ids))
        for cid in unique:
            co = await self._session.get(CompanyRow, cid)
            if co is None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"unknown company_id: {cid}",
                )
        removed_companies = old_ids - set(unique)
        for cid in removed_companies:
            await self.replace_cabinet_bindings_for_company(module_id, cid, [])

        existing = await self._session.execute(
            select(ModuleCompanyGrantRow).where(ModuleCompanyGrantRow.module_id == module_id)
        )
        for row in existing.scalars().all():
            await self._session.delete(row)
        await self._session.flush()
        for cid in unique:
            self._session.add(ModuleCompanyGrantRow(module_id=module_id, company_id=cid))
        await self._session.flush()
        return unique

    async def org_cabinet_ids(self, company_id: str) -> set[str]:
        all_scope = await self._session.execute(
            select(CabinetInstanceRow.id).where(
                CabinetInstanceRow.company_grant_scope == CabinetCompanyGrantScope.ALL,
                CabinetInstanceRow.status != "deleted",
            )
        )
        ids = set(all_scope.scalars().all())
        granted = await self._session.execute(
            select(CabinetInstanceRow.id)
            .join(
                CabinetCompanyGrantRow,
                CabinetCompanyGrantRow.cabinet_id == CabinetInstanceRow.id,
            )
            .where(
                CabinetCompanyGrantRow.company_id == company_id,
                CabinetCompanyGrantRow.status == "active",
            )
        )
        ids.update(granted.scalars().all())
        owned = await self._session.execute(
            select(CabinetInstanceRow.id).where(
                CabinetInstanceRow.owner_scope == "company",
                CabinetInstanceRow.owner_company_id == company_id,
            )
        )
        ids.update(owned.scalars().all())
        return ids

    async def list_cabinet_ids_for_company(self, module_id: str, company_id: str) -> list[str]:
        org_ids = await self.org_cabinet_ids(company_id)
        if not org_ids:
            return []
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.cabinet_id)
            .where(
                ModuleCabinetBindingRow.module_id == module_id,
                ModuleCabinetBindingRow.cabinet_id.in_(org_ids),
            )
            .order_by(ModuleCabinetBindingRow.cabinet_id)
        )
        return list(q.scalars().all())

    async def replace_cabinet_bindings_for_company(
        self, module_id: str, company_id: str, cabinet_ids: list[str]
    ) -> list[str]:
        org_ids = await self.org_cabinet_ids(company_id)
        unique = list(dict.fromkeys(cabinet_ids))
        for cid in unique:
            if cid not in org_ids:
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail=f"cabinet not in company scope: {cid}",
                )

        existing = await self._session.execute(
            select(ModuleCabinetBindingRow).where(ModuleCabinetBindingRow.module_id == module_id)
        )
        existing_rows = list(existing.scalars().all())
        old_in_org = {row.cabinet_id for row in existing_rows if row.cabinet_id in org_ids}
        new_set = set(unique)
        removed = old_in_org - new_set
        added = new_set - old_in_org

        for row in existing_rows:
            if row.cabinet_id in removed:
                await self._session.delete(row)
        await self._session.flush()

        if removed:
            await self._revoke_projects_for_cabinets(module_id, removed)

        for cid in added:
            self._session.add(ModuleCabinetBindingRow(module_id=module_id, cabinet_id=cid))
        await self._session.flush()

        materialize = ModuleMaterializeService(self._session)
        for cid in added:
            await materialize.install(cabinet_id=cid, module_id=module_id)
        for cid in removed:
            await materialize.uninstall(cabinet_id=cid, module_id=module_id)

        return unique

    async def has_company_grant(self, module_id: str, company_id: str) -> bool:
        q = await self._session.execute(
            select(ModuleCompanyGrantRow.id).where(
                ModuleCompanyGrantRow.module_id == module_id,
                ModuleCompanyGrantRow.company_id == company_id,
                ModuleCompanyGrantRow.status == "active",
            )
        )
        return q.scalar_one_or_none() is not None
