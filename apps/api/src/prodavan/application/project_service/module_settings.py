"""Project module enablement + profile selection on project leaf instances."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_instance_service import ModuleInstanceService
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.project_service.access import ProjectAccessPolicy
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import ModuleCabinetBindingRow, ModuleRow
from prodavan.infrastructure.persistence.models.projects import ProjectModuleBindingRow, ProjectRow


def _profile_hub_config(views: list[Any]) -> dict[str, str] | None:
    """Detect prompt-profile tables from profile_hub or prompts_hub markers."""
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
        # Collection hub for path cards still declares profile/settings tables.
        if isinstance(ui.get("profile_table"), str) and ui.get("profile_table").strip():
            return {
                "profile_table": str(ui.get("profile_table")),
                "settings_table": str(ui.get("settings_table") or "profile_settings"),
            }
    return None


def pick_default_profile_row_id(profiles: list[dict[str, Any]]) -> str | None:
    """Default profile for a new project: name Default, else is_default, else alphabetical."""
    if not profiles:
        return None
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for item in profiles:
        body = item["body"]
        name = str(body.get("name") or item["row_id"])
        candidates.append((item["row_id"], name, body))
    for row_id, name, _ in candidates:
        if name.strip().lower() == "default":
            return row_id
    for row_id, _, body in candidates:
        if body.get("is_default"):
            return row_id
    candidates.sort(key=lambda c: c[1].lower())
    return candidates[0][0]


class ProjectModuleSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessPolicy(session)
        self._meta = ModuleMetaDocumentService(session)
        self._instances = ModuleInstanceService(session)

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

    async def _profile_rows_for_project(
        self,
        *,
        project_id: str,
        module_id: str,
        profile_table: str,
    ) -> list[dict[str, Any]]:
        inst = await self._instances.ensure_project_instance(
            project_id=project_id, module_id=module_id
        )
        rows = await self._instances.list_data_rows(
            instance_id=inst.id, table_slug=profile_table
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            body = row.get("body") if isinstance(row.get("body"), dict) else {}
            out.append({"row_id": str(row["row_id"]), "body": body, "instance_id": inst.id})
        return out

    def _resolve_active_profile(
        self,
        profiles: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        if not profiles:
            return None
        defaults = [
            (item["row_id"], str(item["body"].get("name") or item["row_id"]))
            for item in profiles
            if item["body"].get("is_default")
        ]
        if defaults:
            rid, name = sorted(defaults, key=lambda x: x[1].lower())[0]
            return {"profile_id": rid, "profile_name": name}
        picked = pick_default_profile_row_id(profiles)
        if picked is None:
            return None
        for item in profiles:
            if item["row_id"] == picked:
                return {
                    "profile_id": picked,
                    "profile_name": str(item["body"].get("name") or picked),
                }
        return None

    async def ensure_default_profiles_for_project(
        self,
        *,
        project: ProjectRow,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        """Mark default profile as active on the project leaf instance."""
        for module_id, _ in await self._cabinet_modules(project.cabinet_id):
            hub: dict[str, str] | None = None
            try:
                views_doc = await self._meta.get_document(module_id=module_id, slug="views")
                views = views_doc.get("body")
                if isinstance(views, list):
                    hub = _profile_hub_config(views)
            except AppError:
                hub = None
            if hub is None:
                continue
            profiles = await self._profile_rows_for_project(
                project_id=project.id,
                module_id=module_id,
                profile_table=hub["profile_table"],
            )
            if not profiles:
                continue
            if any(p["body"].get("is_default") for p in profiles):
                continue
            profile_id = pick_default_profile_row_id(profiles)
            if profile_id is None:
                continue
            await self._apply_profile_on_instance(
                project_id=project.id,
                module_id=module_id,
                profile_table=hub["profile_table"],
                profile_id=profile_id,
                profiles=profiles,
            )

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
                profiles = await self._profile_rows_for_project(
                    project_id=project.id,
                    module_id=module_id,
                    profile_table=hub["profile_table"],
                )
                picked = self._resolve_active_profile(profiles)
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
            for item in await self._profile_rows_for_project(
                project_id=project.id,
                module_id=module_id,
                profile_table=str(module["profile_table"]),
            ):
                body = item["body"]
                profiles_out.append(
                    {
                        "profile_id": item["row_id"],
                        "name": str(body.get("name") or item["row_id"]),
                        "selected": bool(body.get("is_default")),
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

        profiles = await self._profile_rows_for_project(
            project_id=project.id,
            module_id=module_id,
            profile_table=profile_table,
        )
        await self._apply_profile_on_instance(
            project_id=project.id,
            module_id=module_id,
            profile_table=profile_table,
            profile_id=profile_id,
            profiles=profiles,
        )
        await self._session.commit()
        return await self.get_module(
            project_id=project_id, module_id=module_id, principal=principal, employee=employee
        )

    async def _apply_profile_on_instance(
        self,
        *,
        project_id: str,
        module_id: str,
        profile_table: str,
        profile_id: str,
        profiles: list[dict[str, Any]],
    ) -> None:
        inst = await self._instances.ensure_project_instance(
            project_id=project_id, module_id=module_id
        )
        for item in profiles:
            body = dict(item["body"])
            body["is_default"] = item["row_id"] == profile_id
            # Leaf isolation — clear legacy project_ids filter noise.
            if "project_ids" in body:
                body["project_ids"] = []
            await self._instances.upsert_data_row(
                instance_id=inst.id,
                table_slug=profile_table,
                row_id=item["row_id"],
                body=body,
                created_by=None,
            )
        await self._session.flush()
