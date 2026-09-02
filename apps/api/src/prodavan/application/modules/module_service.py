"""Module lifecycle — platform catalog CRUD."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_materialize_service import ModuleMaterializeService
from prodavan.domain.errors import AppError
from prodavan.domain.modules import ModuleCompanyGrantScope, ModuleStatus
from prodavan.infrastructure.persistence.models.modules import ModuleRow


async def _public_row(
    session: AsyncSession,
    row: ModuleRow,
    *,
    bindings: ModuleBindingService,
) -> dict:
    cabinets = await bindings.list_cabinets(row.id)
    cabinet_ids = [c["cabinet_id"] for c in cabinets]
    project_ids = await bindings.list_project_ids(row.id)
    company_ids = await bindings.list_company_ids(row.id)
    companies = await bindings.list_companies(row.id)
    return {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "owner_scope": row.owner_scope,
        "owner_company_id": row.owner_company_id,
        "cabinet_ids": cabinet_ids,
        "cabinets": cabinets,
        "company_ids": company_ids,
        "companies": companies,
        "project_ids": project_ids,
        "cabinet_bindings_count": len(cabinet_ids),
        "project_bindings_count": len(project_ids),
        "company_grants_count": len(company_ids),
        "company_grant_scope": row.company_grant_scope,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class ModuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._bindings = ModuleBindingService(session)

    async def list_all_admin(self) -> list[dict]:
        q = await self._session.execute(select(ModuleRow).order_by(ModuleRow.created_at.desc()))
        out: list[dict] = []
        for row in q.scalars().all():
            out.append(await _public_row(self._session, row, bindings=self._bindings))
        return out

    async def get_admin(self, *, module_id: str) -> dict:
        row = await self._get_row(module_id)
        return await _public_row(self._session, row, bindings=self._bindings)

    async def create_for_admin(self, *, name: str) -> dict:
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        row = ModuleRow(name=name.strip(), status=ModuleStatus.ACTIVE)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return await _public_row(self._session, row, bindings=self._bindings)

    async def update_admin(
        self,
        *,
        module_id: str,
        name: str | None = None,
        company_ids: list[str] | None = None,
        cabinet_ids: list[str] | None = None,
    ) -> dict:
        row = await self._get_row(module_id)
        if name is not None:
            trimmed = name.strip()
            if not trimmed:
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
            row.name = trimmed
        if company_ids is not None:
            await self._bindings.replace_company_grants(module_id, company_ids)
        if cabinet_ids is not None:
            await self._bindings.replace_cabinet_bindings(module_id, cabinet_ids)
        await self._session.commit()
        await self._session.refresh(row)
        return await _public_row(self._session, row, bindings=self._bindings)

    async def delete_admin(self, *, module_id: str) -> dict:
        row = await self._get_row(module_id)
        if row.company_grant_scope == ModuleCompanyGrantScope.ALL:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="product module cannot be deleted",
            )
        cabinet_ids = await self._bindings.list_cabinet_ids(module_id)
        await ModuleMaterializeService(self._session).uninstall_all_for_module(
            module_id=module_id, cabinet_ids=cabinet_ids
        )
        await self._session.delete(row)
        await self._session.commit()
        return {"id": module_id, "deleted": True}

    async def copy_admin(self, *, module_id: str, name: str | None = None) -> dict:
        from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService

        row = await self._get_row(module_id)
        meta = ModuleMetaDocumentService(self._session)
        copy_name = (name or f"{row.name} (copy)").strip()
        if not copy_name:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        new_row = ModuleRow(name=copy_name, status=ModuleStatus.ACTIVE)
        self._session.add(new_row)
        await self._session.flush()
        docs = await meta.list_documents(module_id=module_id)
        for doc in docs:
            await meta.put_document(module_id=new_row.id, slug=doc["slug"], body=doc["body"])
        await self._session.commit()
        await self._session.refresh(new_row)
        return await _public_row(self._session, new_row, bindings=self._bindings)

    async def bind_project(self, *, module_id: str, project_id: str) -> dict:
        await self._get_row(module_id)
        await self._bindings.bind_project(module_id, project_id)
        await self._session.commit()
        from prodavan.application.projects.workspace_sync_policy import (
            attach_workspace_sync,
            defer_or_schedule_project_sync,
        )

        notification = await defer_or_schedule_project_sync(
            self._session,
            project_id=project_id,
            source="module_bind",
        )
        await self._session.commit()
        return attach_workspace_sync(
            {"module_id": module_id, "project_id": project_id, "status": "active"},
            notification,
        )

    async def revoke_project(self, *, module_id: str, project_id: str) -> dict:
        await self._get_row(module_id)
        await self._bindings.revoke_project(module_id, project_id)
        await self._session.commit()
        from prodavan.application.projects.workspace_sync_policy import (
            attach_workspace_sync,
            defer_or_schedule_project_sync,
        )

        notification = await defer_or_schedule_project_sync(
            self._session,
            project_id=project_id,
            source="module_revoke",
        )
        await self._session.commit()
        return attach_workspace_sync(
            {"module_id": module_id, "project_id": project_id, "status": "revoked"},
            notification,
        )

    async def _get_row(self, module_id: str) -> ModuleRow:
        row = await self._session.get(ModuleRow, module_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="module not found")
        return row
