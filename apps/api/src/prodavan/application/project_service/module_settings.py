"""Project module enablement + profile selection (project_ids on profile rows)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService
from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.project_service.access import ProjectAccessPolicy
from prodavan.application.projects.materialize_planner import _row_applies_to_project
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import ModuleCabinetBindingRow, ModuleRow
from prodavan.infrastructure.persistence.models.projects import ProjectModuleBindingRow, ProjectRow


def _profile_hub_config(views: list[Any]) -> dict[str, str] | None:
    for view in views:
        if not isinstance(view, dict):
            continue
        ui = view.get("ui_json")
        if not isinstance(ui, dict):
            continue
        if ui.get("kind") == "profile_hub" or view.get("kind") == "profile_hub":
            return {
                "profile_table": str(ui.get("profile_table") or "prompt_profiles"),
                "settings_table": str(ui.get("settings_table") or "profile_settings"),
            }
    return None


class ProjectModuleSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessPolicy(session)
        self._meta = ModuleMetaDocumentService(session)

    async def _require_project(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool,
    ) -> ProjectRow:
        return await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=write,
            allow_paused=True,
        )

    async def _enabled_module_ids(self, project: ProjectRow) -> set[str]:
        q = await self._session.execute(
            select(ProjectModuleBindingRow.module_id).where(
                ProjectModuleBindingRow.project_id == project.id
            )
        )
        bound = set(q.scalars().all())
        if bound:
            return bound
        return set(await ModuleBindingService(self._session).list_module_ids_for_cabinet(project.cabinet_id))

    async def _cabinet_modules(self, cabinet_id: str) -> list[tuple[str, str]]:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.module_id, ModuleRow.name)
            .join(ModuleRow, ModuleRow.id == ModuleCabinetBindingRow.module_id)
            .where(ModuleCabinetBindingRow.cabinet_id == cabinet_id)
            .order_by(ModuleRow.name)
        )
        return [(mid, name) for mid, name in q.all()]

    async def _profile_rows(
        self,
        *,
        schema_name: str,
        module_id: str,
        profile_table: str,
    ) -> list[dict[str, Any]]:
        qschema = qident(schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT row_id, body
                FROM {qschema}.module_data_rows
                WHERE module_id = :module_id AND table_slug = :table_slug
                ORDER BY updated_at
                """
            ),
            {"module_id": module_id, "table_slug": profile_table},
        )
        out: list[dict[str, Any]] = []
        for row in q.fetchall():
            body = row.body if isinstance(row.body, dict) else {}
            out.append({"row_id": str(row.row_id), "body": body})
        return out

    def _resolve_profile_for_project(
        self,
        profiles: list[dict[str, Any]],
        project_id: str,
    ) -> dict[str, str] | None:
        explicit: dict[str, str] | None = None
        matches: list[tuple[str, str, bool]] = []
        for item in profiles:
            row_id = item["row_id"]
            body = item["body"]
            name = str(body.get("name") or row_id)
            pids = body.get("project_ids")
            if isinstance(pids, list) and pids and project_id in [str(p) for p in pids]:
                explicit = {"profile_id": row_id, "profile_name": name}
            if _row_applies_to_project(body, project_id):
                matches.append((row_id, name, bool(body.get("is_default"))))
        if explicit is not None:
            return explicit
        if not matches:
            return None
        defaults = [(rid, name) for rid, name, is_def in matches if is_def]
        if defaults:
            rid, name = defaults[0]
            return {"profile_id": rid, "profile_name": name}
        rid, name, _ = matches[0]
        return {"profile_id": rid, "profile_name": name}

    async def list_modules(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        project = await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        enabled = await self._enabled_module_ids(project)
        inst = await self._session.get(CabinetInstanceRow, project.cabinet_id)
        if inst is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="cabinet not found")

        items: list[dict[str, Any]] = []
        for module_id, name in await self._cabinet_modules(project.cabinet_id):
            hub: dict[str, str] | None = None
            try:
                views_doc = await self._meta.get_document(module_id=module_id, slug="views")
                views = views_doc.get("body")
                if isinstance(views, list):
                    hub = _profile_hub_config(views)
            except AppError:
                hub = None

            profile_id: str | None = None
            profile_name: str | None = None
            if hub is not None:
                profiles = await self._profile_rows(
                    schema_name=inst.schema_name,
                    module_id=module_id,
                    profile_table=hub["profile_table"],
                )
                picked = self._resolve_profile_for_project(profiles, project.id)
                if picked is not None:
                    profile_id = picked["profile_id"]
                    profile_name = picked["profile_name"]

            items.append(
                {
                    "module_id": module_id,
                    "name": name,
                    "enabled": module_id in enabled,
                    "has_profiles": hub is not None,
                    "profile_table": hub["profile_table"] if hub else None,
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                }
            )

        return {"module_ids": sorted(enabled), "items": items}

    async def get_module(
        self,
        *,
        project_id: str,
        module_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        listed = await self.list_modules(
            project_id=project_id, principal=principal, employee=employee
        )
        for item in listed["items"]:
            if item["module_id"] == module_id:
                module = dict(item)
                break
        else:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="module not found")

        profiles_out: list[dict[str, Any]] = []
        if module.get("has_profiles") and module.get("profile_table"):
            project = await self._require_project(
                project_id=project_id, principal=principal, employee=employee, write=False
            )
            inst = await self._session.get(CabinetInstanceRow, project.cabinet_id)
            if inst is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="cabinet not found")
            for item in await self._profile_rows(
                schema_name=inst.schema_name,
                module_id=module_id,
                profile_table=str(module["profile_table"]),
            ):
                body = item["body"]
                pids = body.get("project_ids")
                pid_list = [str(p) for p in pids] if isinstance(pids, list) else []
                profiles_out.append(
                    {
                        "profile_id": item["row_id"],
                        "name": str(body.get("name") or item["row_id"]),
                        "project_ids": pid_list,
                        "selected": project.id in pid_list,
                    }
                )
        module["profiles"] = profiles_out
        return module

    async def set_profile(
        self,
        *,
        project_id: str,
        module_id: str,
        profile_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        project = await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        await CabinetAccessService(self._session).require_access(
            cabinet_id=project.cabinet_id, principal=principal, employee=employee, write=True
        )
        detail = await self.get_module(
            project_id=project_id, module_id=module_id, principal=principal, employee=employee
        )
        if not detail.get("has_profiles"):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="module has no profiles",
            )
        profile_table = detail.get("profile_table")
        if not isinstance(profile_table, str):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="profile_table missing",
            )
        valid_ids = {p["profile_id"] for p in detail.get("profiles") or []}
        if profile_id not in valid_ids:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="unknown profile_id",
            )

        inst = await self._session.get(CabinetInstanceRow, project.cabinet_id)
        if inst is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="cabinet not found")
        rows = await self._profile_rows(
            schema_name=inst.schema_name, module_id=module_id, profile_table=profile_table
        )
        cabinet_modules = CabinetModuleService(self._session)
        for item in rows:
            row_id = item["row_id"]
            body = dict(item["body"])
            pids = body.get("project_ids")
            current = [str(p) for p in pids] if isinstance(pids, list) else []
            if row_id == profile_id:
                if project.id not in current:
                    current.append(project.id)
            else:
                current = [p for p in current if p != project.id]
            body["project_ids"] = current
            await cabinet_modules.update_data_row(
                cabinet_id=project.cabinet_id,
                module_id=module_id,
                table_slug=profile_table,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
            )
        return await self.get_module(
            project_id=project_id, module_id=module_id, principal=principal, employee=employee
        )
