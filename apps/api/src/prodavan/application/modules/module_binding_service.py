"""Module N:M cabinet + project bindings."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_materialize_service import ModuleMaterializeService
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.cabinets.types import CabinetCompanyGrantScope
from prodavan.domain.errors import AppError
from prodavan.domain.modules import ModuleCompanyGrantScope
from prodavan.infrastructure.persistence.models.cabinets import CabinetCompanyGrantRow, CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleCompanyGrantRow,
    ModuleProjectBindingRow,
    ModuleRow,
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
            {
                "cabinet_id": row.cabinet_id,
                "cabinet_name": name,
                "bind_kind": row.bind_kind,
                "child_may_edit": row.child_may_edit,
            }
            for row, name in q.all()
        ]

    async def list_project_ids(self, module_id: str) -> list[str]:
        q = await self._session.execute(
            select(ModuleProjectBindingRow.project_id)
            .where(ModuleProjectBindingRow.module_id == module_id)
            .order_by(ModuleProjectBindingRow.project_id)
        )
        return list(q.scalars().all())

    async def list_module_ids_for_project(self, project_id: str) -> list[str]:
        """Modules explicitly bound to this project (MP). Empty → none materialize."""
        q = await self._session.execute(
            select(ModuleProjectBindingRow.module_id)
            .where(ModuleProjectBindingRow.project_id == project_id)
            .order_by(ModuleProjectBindingRow.module_id)
        )
        return list(q.scalars().all())

    async def get_project_binding(
        self, module_id: str, project_id: str
    ) -> ModuleProjectBindingRow | None:
        q = await self._session.execute(
            select(ModuleProjectBindingRow).where(
                ModuleProjectBindingRow.module_id == module_id,
                ModuleProjectBindingRow.project_id == project_id,
            )
        )
        return q.scalar_one_or_none()

    async def list_project_bindings_for_module(
        self, module_id: str, *, company_id: str | None = None
    ) -> list[dict]:
        """Project binds for a module, enriched with company + usage metrics.

        When ``company_id`` is set, only projects owned by that company are returned.
        """
        from prodavan.application.metrics.overview_merge import overlay_store_counters
        from prodavan.domain.metrics.types import ENTITY_PROJECT
        from prodavan.infrastructure.persistence.models.agent import (
            AgentEventRow,
            AgentSessionRow,
            AgentUsageRow,
        )

        stmt = (
            select(
                ModuleProjectBindingRow,
                ProjectRow.name,
                ProjectRow.company_id,
                ProjectRow.cabinet_id,
                CompanyRow.name,
            )
            .join(ProjectRow, ProjectRow.id == ModuleProjectBindingRow.project_id)
            .outerjoin(CompanyRow, CompanyRow.id == ProjectRow.company_id)
            .where(ModuleProjectBindingRow.module_id == module_id)
        )
        if company_id is not None:
            stmt = stmt.where(ProjectRow.company_id == company_id)
        stmt = stmt.order_by(ProjectRow.name)
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return []

        project_ids = [bind.project_id for bind, *_ in rows]

        usage_q = await self._session.execute(
            select(
                AgentSessionRow.project_id,
                func.coalesce(func.sum(AgentUsageRow.input_tokens), 0),
                func.coalesce(func.sum(AgentUsageRow.output_tokens), 0),
            )
            .select_from(AgentUsageRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentUsageRow.session_id)
            .where(AgentSessionRow.project_id.in_(project_ids))
            .group_by(AgentSessionRow.project_id)
        )
        tokens_by: dict[str, int] = {}
        for pid, inp, out in usage_q.all():
            tokens_by[str(pid)] = int(inp or 0) + int(out or 0)

        msg_q = await self._session.execute(
            select(AgentSessionRow.project_id, func.count())
            .select_from(AgentEventRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentEventRow.session_id)
            .where(
                AgentSessionRow.project_id.in_(project_ids),
                AgentEventRow.event_type == "user_message",
            )
            .group_by(AgentSessionRow.project_id)
        )
        requests_by = {str(pid): int(cnt or 0) for pid, cnt in msg_q.all()}

        out: list[dict] = []
        for bind, project_name, proj_company_id, cabinet_id, company_name in rows:
            pid = bind.project_id
            metrics = {
                "agent_tokens_used": tokens_by.get(pid, 0),
                "agent_requests": requests_by.get(pid, 0),
                "agent_messages": requests_by.get(pid, 0),
            }
            metrics = await overlay_store_counters(
                metrics, entity_type=ENTITY_PROJECT, entity_id=pid
            )
            out.append(
                {
                    "project_id": pid,
                    "project_name": project_name,
                    "company_id": proj_company_id,
                    "company_name": company_name,
                    "cabinet_id": cabinet_id,
                    "bind_kind": bind.bind_kind,
                    "child_may_edit": bind.child_may_edit,
                    "agent_tokens_used": int(metrics.get("agent_tokens_used") or 0),
                    "agent_requests": int(metrics.get("agent_requests") or 0),
                }
            )
        return out

    async def list_cabinets_catalog_for_module(
        self, module_id: str, *, company_id: str | None = None
    ) -> list[dict]:
        """All (or company-owned) cabinets with bound flag + modules_count for UI table."""
        bound_ids = set(await self.list_cabinet_ids(module_id))

        # Prefer owner_company_id, fall back to legacy company_id for display name.
        company_id_col = func.coalesce(
            CabinetInstanceRow.owner_company_id, CabinetInstanceRow.company_id
        )
        modules_count_sq = (
            select(func.count())
            .select_from(ModuleCabinetBindingRow)
            .where(ModuleCabinetBindingRow.cabinet_id == CabinetInstanceRow.id)
            .correlate(CabinetInstanceRow)
            .scalar_subquery()
        )
        stmt = (
            select(
                CabinetInstanceRow.id,
                CabinetInstanceRow.name,
                company_id_col,
                CompanyRow.name,
                modules_count_sq,
            )
            .outerjoin(CompanyRow, CompanyRow.id == company_id_col)
            .where(CabinetInstanceRow.status != CabinetStatus.DELETED)
            .order_by(CabinetInstanceRow.name)
        )
        if company_id is not None:
            # Match company shell org cabinets (owned copies), not platform templates.
            stmt = stmt.where(
                CabinetInstanceRow.owner_company_id == company_id,
                CabinetInstanceRow.owner_scope == "company",
            )

        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "cabinet_id": cab_id,
                "cabinet_name": cab_name,
                "company_id": cid,
                "company_name": cname,
                "modules_count": int(mod_count or 0),
                "bound": cab_id in bound_ids,
            }
            for cab_id, cab_name, cid, cname, mod_count in rows
        ]

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
            self._session.add(self._new_cabinet_binding(module_id=module_id, cabinet_id=cid))
        await self._session.flush()

        added = set(unique) - old_cabinet_ids
        materialize = ModuleMaterializeService(self._session)
        for cid in added:
            await materialize.install(cabinet_id=cid, module_id=module_id)
        for cid in removed:
            await materialize.uninstall(cabinet_id=cid, module_id=module_id)

        return unique

    def _new_cabinet_binding(self, *, module_id: str, cabinet_id: str) -> ModuleCabinetBindingRow:
        from prodavan.domain.modules import default_cabinet_bind_kind, default_child_may_edit

        kind = default_cabinet_bind_kind(module_id)
        return ModuleCabinetBindingRow(
            module_id=module_id,
            cabinet_id=cabinet_id,
            bind_kind=kind,
            child_may_edit=default_child_may_edit(kind),
        )

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
                self._session.add(self._new_cabinet_binding(module_id=mid, cabinet_id=cabinet_id))
        await self._session.flush()

        materialize = ModuleMaterializeService(self._session)
        for mid in added:
            await materialize.install(cabinet_id=cabinet_id, module_id=mid)
        for mid in removed:
            await self._revoke_projects_for_cabinets(mid, {cabinet_id})
            await materialize.uninstall(cabinet_id=cabinet_id, module_id=mid)

        if added or removed:
            from prodavan.application.projects.rematerialize_scheduler import (
                schedule_cabinet_binding_change_rematerialize,
            )

            await schedule_cabinet_binding_change_rematerialize(
                self._session, cabinet_id=cabinet_id
            )

        return unique

    async def bind_project(
        self,
        module_id: str,
        project_id: str,
        *,
        bind_kind: str = "local",
        child_may_edit: bool | None = None,
    ) -> dict:
        from prodavan.domain.modules import ModuleBindKind
        from prodavan.application.modules.module_instance_service import ModuleInstanceService

        if bind_kind not in (ModuleBindKind.LOCAL, ModuleBindKind.GLOBAL):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="bind_kind must be local or global",
            )
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
        may_edit = (
            True
            if child_may_edit is None and bind_kind == ModuleBindKind.LOCAL
            else False
            if child_may_edit is None
            else bool(child_may_edit)
        )
        existing = await self.get_project_binding(module_id, project_id)
        instances = ModuleInstanceService(self._session)
        if existing is None:
            row = ModuleProjectBindingRow(
                module_id=module_id,
                project_id=project_id,
                bind_kind=bind_kind,
                child_may_edit=may_edit,
            )
            self._session.add(row)
            await self._session.flush()
        else:
            existing.bind_kind = bind_kind
            existing.child_may_edit = may_edit
            row = existing
            await self._session.flush()

        if bind_kind == ModuleBindKind.LOCAL:
            await instances.ensure_project_instance(project_id=project_id, module_id=module_id)
        else:
            # Drop stale local leaf if switching to global.
            leaf = await instances.get_instance(
                owner_kind="project", owner_id=project_id, module_id=module_id
            )
            if leaf is not None:
                await instances.delete_instance(instance_id=leaf.id)

        return {
            "module_id": module_id,
            "project_id": project_id,
            "bind_kind": row.bind_kind,
            "child_may_edit": row.child_may_edit,
        }

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
        from prodavan.application.modules.module_instance_service import ModuleInstanceService

        instances = ModuleInstanceService(self._session)
        leaf = await instances.get_instance(
            owner_kind="project", owner_id=project_id, module_id=module_id
        )
        if leaf is not None:
            await instances.delete_instance(instance_id=leaf.id)

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
            # Admin→company is always a local copy (fork). Global grants stay opt-in via API.
            self._session.add(
                ModuleCompanyGrantRow(
                    module_id=module_id,
                    company_id=cid,
                    bind_kind="local",
                    child_may_edit=True,
                )
            )
        await self._session.flush()
        from prodavan.application.modules.module_instance_service import ModuleInstanceService

        instances = ModuleInstanceService(self._session)
        for cid in unique:
            if cid not in old_ids:
                await instances.ensure_company_instance(company_id=cid, module_id=module_id)
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
            self._session.add(self._new_cabinet_binding(module_id=module_id, cabinet_id=cid))
        await self._session.flush()

        materialize = ModuleMaterializeService(self._session)
        for cid in added:
            await materialize.install(cabinet_id=cid, module_id=module_id)
        for cid in removed:
            await materialize.uninstall(cabinet_id=cid, module_id=module_id)

        return unique

    async def has_company_grant(self, module_id: str, company_id: str) -> bool:
        row = await self._session.get(ModuleRow, module_id)
        if row is not None and row.company_grant_scope == ModuleCompanyGrantScope.ALL:
            return True
        q = await self._session.execute(
            select(ModuleCompanyGrantRow.id).where(
                ModuleCompanyGrantRow.module_id == module_id,
                ModuleCompanyGrantRow.company_id == company_id,
                ModuleCompanyGrantRow.status == "active",
            )
        )
        return q.scalar_one_or_none() is not None
