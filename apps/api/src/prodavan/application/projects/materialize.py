"""Materialize project workspace from cabinet module meta rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.materialize_executor import MaterializeExecutor
from prodavan.application.projects.materialize_planner import MaterializePlanner
from prodavan.domain.projects import workspace_key_for
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter


@dataclass(frozen=True, slots=True)
class MaterializeResult:
    project_id: str
    cabinet_id: str
    workspace_root: str
    mcp_config_path: str
    status: str = "materialized"
    package_names: tuple[str, ...] = ()
    sandbox_packages: tuple[dict, ...] = ()
    agents_source: str = "default"
    written_paths: tuple[str, ...] = ()
    module_paths: dict[str, tuple[str, ...]] | None = None


class MaterializeProjectPort(Protocol):
    async def materialize_project(
        self,
        *,
        session: AsyncSession,
        project_id: str,
        cabinet_id: str,
        cabinet_name: str | None = None,
        project_name: str | None = None,
        when: str = "project.created",
        enabled_module_ids: list[str] | None = None,
    ) -> MaterializeResult: ...

    async def sync_project(
        self,
        *,
        session: AsyncSession,
        project: ProjectRow,
        cabinet_name: str | None = None,
        when: str = "project.sync",
        enabled_module_ids: list[str] | None = None,
        all_cabinet_module_ids: list[str] | None = None,
    ) -> MaterializeResult: ...


class ProjectMaterializeService:
    async def materialize_project(
        self,
        *,
        session: AsyncSession,
        project_id: str,
        cabinet_id: str,
        cabinet_name: str | None = None,
        project_name: str | None = None,
        when: str = "project.created",
        enabled_module_ids: list[str] | None = None,
    ) -> MaterializeResult:
        return await self._run_materialize(
            session=session,
            project_id=project_id,
            cabinet_id=cabinet_id,
            cabinet_name=cabinet_name,
            project_name=project_name,
            when=when,
            enabled_module_ids=enabled_module_ids,
            prune_before=False,
            manifest=None,
        )

    async def sync_project(
        self,
        *,
        session: AsyncSession,
        project: ProjectRow,
        cabinet_name: str | None = None,
        when: str = "project.sync",
        enabled_module_ids: list[str] | None = None,
        all_cabinet_module_ids: list[str] | None = None,
    ) -> MaterializeResult:
        from prodavan.application.modules.module_binding_service import ModuleBindingService

        cabinet_id = project.cabinet_id
        all_ids = all_cabinet_module_ids
        if all_ids is None:
            all_ids = await ModuleBindingService(session).list_module_ids_for_cabinet(cabinet_id)
        enabled = enabled_module_ids or all_ids
        enabled_set = set(enabled)
        disabled = [mid for mid in all_ids if mid not in enabled_set]
        manifest: dict[str, Any] = dict(project.materialize_manifest or {})
        planner = MaterializePlanner(session)
        ws_key = project.workspace_key or workspace_key_for(project.id)
        writer = WorkspaceLayoutWriter(workspace_key=ws_key)

        for mid in disabled:
            for root in await planner.load_workspace_roots(mid):
                writer.wipe_prefix(root)
            for path in manifest.get(mid) or []:
                if isinstance(path, str):
                    writer.remove_relative_path(path)
            manifest.pop(mid, None)

        for mid in enabled:
            for root in await planner.load_workspace_roots(mid):
                writer.wipe_prefix(root)

        result = await self._run_materialize(
            session=session,
            project_id=project.id,
            cabinet_id=cabinet_id,
            cabinet_name=cabinet_name,
            project_name=project.name,
            when=when,
            enabled_module_ids=enabled,
            prune_before=False,
            manifest=manifest,
        )
        project.materialize_manifest = result.module_paths or {}
        return result

    async def _run_materialize(
        self,
        *,
        session: AsyncSession,
        project_id: str,
        cabinet_id: str,
        cabinet_name: str | None,
        project_name: str | None,
        when: str,
        enabled_module_ids: list[str] | None,
        prune_before: bool,
        manifest: dict[str, Any] | None,
    ) -> MaterializeResult:
        inst = await session.get(CabinetInstanceRow, cabinet_id)
        cab_name = cabinet_name or (inst.name if inst else cabinet_id)
        proj_name = project_name or project_id
        ws_key = workspace_key_for(project_id)
        writer = WorkspaceLayoutWriter(workspace_key=ws_key)
        writer.ensure_dirs()

        planner = MaterializePlanner(session)
        ops, _active = await planner.plan_for_project(
            cabinet_id=cabinet_id,
            project_id=project_id,
            when=when,
            enabled_module_ids=enabled_module_ids,
        )
        executor = MaterializeExecutor(session)
        written, mcp_packages = await executor.execute(
            writer=writer, cabinet_id=cabinet_id, ops=ops
        )

        module_paths: dict[str, list[str]] = dict(manifest or {})
        for op in ops:
            if op.workspace_path:
                module_paths.setdefault(op.module_id, [])
                if op.workspace_path not in module_paths[op.module_id]:
                    module_paths[op.module_id].append(op.workspace_path)

        agents_md = None
        agents_source = "materialize"
        for op in ops:
            if op.workspace_path == "AGENTS.md" and op.row_body is not None:
                agents_md = op.row_body.get(op.field or "body_md")
                break
        if agents_md is None:
            agents_source = "default"

        writer.write_agents(cabinet_name=cab_name, project_name=proj_name, agents_md=agents_md)
        if not mcp_packages:
            writer.write_mcp_config(cabinet_id=cabinet_id, packages=[])

        pkg_names = tuple(p.get("name", "") for p in mcp_packages if p.get("name"))
        root = writer.workspace_root
        frozen_module_paths = {mid: tuple(paths) for mid, paths in module_paths.items()}
        return MaterializeResult(
            project_id=project_id,
            cabinet_id=cabinet_id,
            workspace_root=str(root),
            mcp_config_path=str(writer.mcp_config_path),
            status="materialized",
            package_names=pkg_names,
            sandbox_packages=tuple(mcp_packages),
            agents_source=agents_source,
            written_paths=tuple(written),
            module_paths=frozen_module_paths,
        )


_default: ProjectMaterializeService | None = None


def get_materialize_service() -> ProjectMaterializeService:
    global _default
    if _default is None:
        _default = ProjectMaterializeService()
    return _default
