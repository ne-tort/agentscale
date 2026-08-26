"""Materialize project workspace from cabinet (minimal — no MCP packages)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

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


class MaterializeProjectPort(Protocol):
    async def materialize_project(
        self,
        *,
        session: AsyncSession,
        project_id: str,
        cabinet_id: str,
        cabinet_name: str | None = None,
        project_name: str | None = None,
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
    ) -> MaterializeResult:
        inst = await session.get(CabinetInstanceRow, cabinet_id)
        cab_name = cabinet_name or (inst.name if inst else cabinet_id)
        proj_name = project_name or project_id
        ws_key = workspace_key_for(project_id)
        writer = WorkspaceLayoutWriter(workspace_key=ws_key)
        writer.ensure_dirs()
        writer.write_agents(cabinet_name=cab_name, project_name=proj_name, agents_md=None)
        writer.write_mcp_config(cabinet_id=cabinet_id, packages=[])
        root = writer.workspace_root
        return MaterializeResult(
            project_id=project_id,
            cabinet_id=cabinet_id,
            workspace_root=str(root),
            mcp_config_path=str(writer.mcp_config_path),
            status="materialized",
            package_names=(),
            sandbox_packages=(),
            agents_source="default",
        )


_default: ProjectMaterializeService | None = None


def get_materialize_service() -> ProjectMaterializeService:
    global _default
    if _default is None:
        _default = ProjectMaterializeService()
    return _default
