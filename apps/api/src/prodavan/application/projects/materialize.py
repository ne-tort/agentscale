"""Materialize project workspace from cabinet module meta rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.materialize_executor import MaterializeExecutor
from prodavan.application.projects.materialize_planner import MaterializePlanner
from prodavan.domain.projects import workspace_key_for
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
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
    ) -> MaterializeResult:
        inst = await session.get(CabinetInstanceRow, cabinet_id)
        cab_name = cabinet_name or (inst.name if inst else cabinet_id)
        proj_name = project_name or project_id
        ws_key = workspace_key_for(project_id)
        writer = WorkspaceLayoutWriter(workspace_key=ws_key)
        writer.ensure_dirs()

        planner = MaterializePlanner(session)
        ops, _active = await planner.plan_for_project(cabinet_id=cabinet_id, when=when)
        executor = MaterializeExecutor(session)
        written, mcp_packages = await executor.execute(
            writer=writer, cabinet_id=cabinet_id, ops=ops
        )

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
        )


_default: ProjectMaterializeService | None = None


def get_materialize_service() -> ProjectMaterializeService:
    global _default
    if _default is None:
        _default = ProjectMaterializeService()
    return _default
