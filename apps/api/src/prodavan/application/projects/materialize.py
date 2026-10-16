"""Materialize project workspace from cabinet module meta rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.ai_keys.probe.provider_resolver import ProviderResolver
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.application.projects.materialize_executor import MaterializeExecutor
from prodavan.application.projects.materialize_planner import MaterializePlanner
from prodavan.application.projects.openclaw_config_materializer import (
    build_openclaw_config,
    filter_mcp_packages_by_policy,
    openclaw_config_relative_path,
    provider_to_default_api_kind,
    render_openclaw_config_yaml,
)
from prodavan.domain.agent import default_tool_policy
from prodavan.domain.agent.turn_limits import UNLIMITED_MAX_TURNS
from prodavan.domain.ai_keys import is_http_probe_kind
from prodavan.domain.projects import workspace_key_for
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow
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
        # Empty list is intentional (no MP binds) — never fall back to all cabinet modules.
        enabled = all_ids if enabled_module_ids is None else enabled_module_ids
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

        from prodavan.application.mcp.platform_equipment_mcp import (
            materialize_platform_equipment_mcp,
            merge_platform_equipment_mcp,
        )
        from prodavan.application.mcp.platform_modules_mcp import (
            materialize_platform_modules_mcp,
            merge_platform_modules_mcp,
        )

        platform_pkg = materialize_platform_modules_mcp(writer)
        mcp_packages = merge_platform_modules_mcp(mcp_packages, platform_pkg=platform_pkg)
        written.append(f"packages/{platform_pkg['name']}/server.py")

        # Zip from equipment_mcp.file_ref wins; code copy is fallback only.
        has_equipment_zip = any(
            isinstance(p, dict) and p.get("name") == "prodavan-equipment" for p in mcp_packages
        )
        if not has_equipment_zip:
            equipment_pkg = materialize_platform_equipment_mcp(writer)
            mcp_packages = merge_platform_equipment_mcp(mcp_packages, platform_pkg=equipment_pkg)
            written.append(f"packages/{equipment_pkg['name']}/server.py")

        module_paths: dict[str, list[str]] = dict(manifest or {})
        for op in ops:
            if op.workspace_path:
                module_paths.setdefault(op.module_id, [])
                if op.workspace_path not in module_paths[op.module_id]:
                    module_paths[op.module_id].append(op.workspace_path)

        agents_md = None
        agents_source = "none"
        # After planner stitch there is at most one raw op per path (incl. AGENTS.md).
        for op in ops:
            if op.workspace_path == "AGENTS.md" and op.row_body is not None:
                agents_md = op.row_body.get(op.field or "body_md")
                agents_source = "module"
                break

        writer.write_agents(cabinet_name=cab_name, project_name=proj_name, agents_md=agents_md)
        filtered_packages = await self._filter_mcp_packages(session, project_id, mcp_packages)
        writer.write_mcp_config(cabinet_id=cabinet_id, packages=filtered_packages)

        await self._write_openclaw_config(
            session=session,
            project_id=project_id,
            writer=writer,
            mcp_packages=filtered_packages,
        )

        config_rel = openclaw_config_relative_path()
        all_written = list(written)
        if config_rel not in all_written:
            all_written.append(config_rel)

        pkg_names = tuple(p.get("name", "") for p in filtered_packages if p.get("name"))
        root = writer.workspace_root
        frozen_module_paths = {mid: tuple(paths) for mid, paths in module_paths.items()}
        return MaterializeResult(
            project_id=project_id,
            cabinet_id=cabinet_id,
            workspace_root=str(root),
            mcp_config_path=str(writer.mcp_config_path),
            status="materialized",
            package_names=pkg_names,
            sandbox_packages=tuple(filtered_packages),
            agents_source=agents_source,
            written_paths=tuple(all_written),
            module_paths=frozen_module_paths,
        )

    async def _write_openclaw_config(
        self,
        *,
        session: AsyncSession,
        project_id: str,
        writer: WorkspaceLayoutWriter,
        mcp_packages: list[dict],
    ) -> None:
        project = await session.get(ProjectRow, project_id)
        if project is None:
            return
        company_policy = await AdminCompanyService(session).get_agent_policy(project.company_id)
        api_kind: str | None = None
        provider_endpoint: dict[str, Any] | None = None
        provider_key_id = getattr(project, "resolved_ai_key_id", None)
        if provider_key_id:
            key_row = await session.get(AiProviderKeyRow, provider_key_id)
            if key_row is not None and (key_row.api_kind or "").strip():
                api_kind = str(key_row.api_kind).strip()
            # Resolve the HTTP provider endpoint (base_url / auth_scheme /
            # chat_completions_path / models_path) from the ai.http_providers
            # catalog so the bridge reaches the actual provider (cheapai.lol /
            # ollama / ...) instead of the env default (api.openai.com).
            if api_kind and is_http_probe_kind(api_kind):
                try:
                    secret = await AiKeysService(session).resolve_secret_for_key(provider_key_id)
                except Exception:
                    secret = None
                endpoint = await ProviderResolver(session).resolve(
                    api_kind=api_kind,
                    provider=str(key_row.provider) if key_row is not None else "",
                    secret=secret,
                    catalog_entry_id=getattr(key_row, "catalog_entry_id", None) if key_row is not None else None,
                )
                if endpoint is not None:
                    provider_endpoint = {
                        "base_url": endpoint.base_url,
                        "auth_scheme": endpoint.auth_scheme,
                        "chat_completions_path": endpoint.chat_completions_path,
                        "models_path": endpoint.models_path,
                    }
        if not api_kind:
            api_kind = provider_to_default_api_kind(project.agent_provider)
        cfg = build_openclaw_config(
            company_policy=company_policy,
            api_kind=api_kind,
            provider_key_id=provider_key_id,
            provider_endpoint=provider_endpoint,
            mcp_packages=mcp_packages,
            # По умолчанию БЕЗ ограничений: 12 хватало только на 4-6 позиций
            # подбора, а жёсткие 50 обрывали содержательные прогоны
            # (reason=max_turns) без внятной подписи в чате. Лимит — настройка
            # чата (agent_sessions.max_turns, уходит в теле send), страж расхода
            # — budget.max_tokens (company policy), а не турн-кап.
            max_turns=UNLIMITED_MAX_TURNS,
        )
        writer.write_text_file(
            relative_path=openclaw_config_relative_path(),
            text=render_openclaw_config_yaml(cfg),
        )

    async def _filter_mcp_packages(
        self,
        session: AsyncSession,
        project_id: str,
        mcp_packages: list[dict],
    ) -> list[dict]:
        """Apply company MCP allowlist to mcp.json packages (CLAW-P1b).

        ``mcp.json`` on disk and the OpenClaw ``config.yaml`` servers map
        must both honor the company tool policy; otherwise a Pod agent
        runtime reading the file directly bypasses the allowlist. The
        OpenClaw config is filtered inside ``build_openclaw_config``; this
        mirrors the filter for the raw ``mcp.json`` file.
        """
        project = await session.get(ProjectRow, project_id)
        if project is None:
            return mcp_packages
        company_policy = await AdminCompanyService(session).get_agent_policy(project.company_id)
        tool_policy = default_tool_policy(company_policy.tool_preset)
        return filter_mcp_packages_by_policy(mcp_packages, tool_policy)


_default: ProjectMaterializeService | None = None


def get_materialize_service() -> ProjectMaterializeService:
    global _default
    if _default is None:
        _default = ProjectMaterializeService()
    return _default
