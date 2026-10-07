"""Company-scoped module catalog — grants + local CRUD + cabinet bind."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_instance_service import (
    OWNER_COMPANY,
    ModuleInstanceService,
)
from prodavan.application.modules.module_materialize_service import ModuleMaterializeService
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.domain.errors import AppError
from prodavan.domain.modules import ModuleBindKind, ModuleCompanyGrantScope, ModuleStatus
from prodavan.domain.ownership import OwnerScope
from prodavan.infrastructure.persistence.models.modules import ModuleCompanyGrantRow, ModuleRow


def _company_public_row(
    row: ModuleRow,
    *,
    company_id: str,
    cabinet_ids: list[str],
    may_edit: bool,
    bind_kind: str | None,
) -> dict:
    if bind_kind == ModuleBindKind.LOCAL and may_edit:
        source = "company_local"
    elif bind_kind == ModuleBindKind.GLOBAL or not may_edit:
        source = "platform_assigned"
    else:
        source = "company_local" if may_edit else "platform_assigned"
    return {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "owner_scope": row.owner_scope,
        "owner_company_id": row.owner_company_id,
        "writable": may_edit,
        "source": source,
        "bind_kind": bind_kind,
        "child_may_edit": may_edit,
        "cabinet_ids": cabinet_ids,
        "cabinet_bindings_count": len(cabinet_ids),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class CompanyModuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._bindings = ModuleBindingService(session)
        self._meta = ModuleMetaDocumentService(session)
        self._instances = ModuleInstanceService(session)

    async def _bind_flags(self, *, company_id: str, module_id: str) -> tuple[bool, str | None]:
        may_edit = await self._instances.sot_may_edit(
            module_id=module_id, owner_kind=OWNER_COMPANY, owner_id=company_id
        )
        q = await self._session.execute(
            select(ModuleCompanyGrantRow).where(
                ModuleCompanyGrantRow.module_id == module_id,
                ModuleCompanyGrantRow.company_id == company_id,
                ModuleCompanyGrantRow.status == "active",
            )
        )
        grant = q.scalar_one_or_none()
        if grant is not None:
            return may_edit, grant.bind_kind
        row = await self._session.get(ModuleRow, module_id)
        if row is not None and row.owner_scope == OwnerScope.COMPANY and row.owner_company_id == company_id:
            return True, ModuleBindKind.LOCAL
        if row is not None and row.company_grant_scope == ModuleCompanyGrantScope.ALL:
            return False, ModuleBindKind.GLOBAL
        return may_edit, None

    async def _public(self, row: ModuleRow, *, company_id: str, cabinet_ids: list[str]) -> dict:
        may_edit, bind_kind = await self._bind_flags(company_id=company_id, module_id=row.id)
        return _company_public_row(
            row,
            company_id=company_id,
            cabinet_ids=cabinet_ids,
            may_edit=may_edit,
            bind_kind=bind_kind,
        )

    async def list_for_company(self, company_id: str) -> list[dict]:
        q = await self._session.execute(
            select(ModuleRow)
            .outerjoin(
                ModuleCompanyGrantRow,
                (ModuleCompanyGrantRow.module_id == ModuleRow.id)
                & (ModuleCompanyGrantRow.company_id == company_id)
                & (ModuleCompanyGrantRow.status == "active"),
            )
            .where(
                or_(
                    ModuleCompanyGrantRow.id.isnot(None),
                    ModuleRow.company_grant_scope == ModuleCompanyGrantScope.ALL,
                    (ModuleRow.owner_scope == OwnerScope.COMPANY) & (ModuleRow.owner_company_id == company_id),
                )
            )
            .order_by(ModuleRow.created_at.desc())
        )
        out: list[dict] = []
        for row in q.scalars().unique().all():
            cabinet_ids = await self._bindings.list_cabinet_ids_for_company(row.id, company_id)
            out.append(await self._public(row, company_id=company_id, cabinet_ids=cabinet_ids))
        return out

    async def get_for_company(self, *, company_id: str, module_id: str) -> dict:
        row = await self._require_visible(company_id, module_id)
        cabinet_ids = await self._bindings.list_cabinet_ids_for_company(module_id, company_id)
        return await self._public(row, company_id=company_id, cabinet_ids=cabinet_ids)

    async def create_local(self, *, company_id: str, name: str) -> dict:
        trimmed = name.strip()
        if not trimmed:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        row = ModuleRow(
            name=trimmed,
            status=ModuleStatus.ACTIVE,
            owner_scope=OwnerScope.COMPANY,
            owner_company_id=company_id,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return await self._public(row, company_id=company_id, cabinet_ids=[])

    async def update_for_company(
        self,
        *,
        company_id: str,
        module_id: str,
        name: str | None = None,
        cabinet_ids: list[str] | None = None,
    ) -> dict:
        row = await self._require_visible(company_id, module_id)
        may_edit, _ = await self._bind_flags(company_id=company_id, module_id=module_id)
        if name is not None:
            if not may_edit:
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="module is read-only")
            trimmed = name.strip()
            if not trimmed:
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
            row.name = trimmed
        if cabinet_ids is not None:
            await self._bindings.replace_cabinet_bindings_for_company(module_id, company_id, cabinet_ids)
        await self._session.commit()
        await self._session.refresh(row)
        bound = await self._bindings.list_cabinet_ids_for_company(module_id, company_id)
        return await self._public(row, company_id=company_id, cabinet_ids=bound)

    async def delete_local(self, *, company_id: str, module_id: str) -> dict:
        row = await self._require_visible(company_id, module_id)
        if row.owner_scope != OwnerScope.COMPANY or row.owner_company_id != company_id:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="module is read-only")
        cabinet_ids = await self._bindings.list_cabinet_ids_for_company(module_id, company_id)
        await ModuleMaterializeService(self._session).uninstall_all_for_module(
            module_id=module_id, cabinet_ids=cabinet_ids
        )
        await self._session.delete(row)
        await self._session.commit()
        return {"id": module_id, "deleted": True}

    async def list_meta_documents(self, *, company_id: str, module_id: str) -> list[dict]:
        await self._require_visible(company_id, module_id)
        return await self._meta.list_documents(module_id=module_id)

    async def get_meta_document(self, *, company_id: str, module_id: str, slug: str) -> dict:
        await self._require_visible(company_id, module_id)
        return await self._meta.get_document(module_id=module_id, slug=slug)

    async def put_meta_document(
        self, *, company_id: str, module_id: str, slug: str, body: object
    ) -> dict:
        await self._require_visible(company_id, module_id)
        may_edit, _ = await self._bind_flags(company_id=company_id, module_id=module_id)
        if not may_edit:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="module meta is read-only")
        return await self._meta.put_document(module_id=module_id, slug=slug, body=body)

    async def copy_module(
        self,
        *,
        company_id: str,
        source_module_id: str,
        name: str | None = None,
    ) -> dict:
        source = await self._require_visible(company_id, source_module_id)
        base_name = (name or f"{source.name} (copy)").strip()
        if not base_name:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        row = ModuleRow(
            name=base_name,
            status=ModuleStatus.ACTIVE,
            owner_scope=OwnerScope.COMPANY,
            owner_company_id=company_id,
        )
        self._session.add(row)
        await self._session.flush()
        docs = await self._meta.list_documents(module_id=source_module_id)
        for doc in docs:
            await self._meta.put_document(
                module_id=row.id,
                slug=doc["slug"],
                body=doc["body"],
            )
        await self._session.commit()
        await self._session.refresh(row)
        return await self._public(row, company_id=company_id, cabinet_ids=[])

    async def _require_visible(self, company_id: str, module_id: str) -> ModuleRow:
        row = await self._session.get(ModuleRow, module_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="module not found")
        if row.owner_scope == OwnerScope.COMPANY and row.owner_company_id == company_id:
            return row
        if row.company_grant_scope == ModuleCompanyGrantScope.ALL:
            return row
        if await self._bindings.has_company_grant(module_id, company_id):
            return row
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="module not found")
