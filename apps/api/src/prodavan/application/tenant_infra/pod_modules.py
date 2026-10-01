"""Pod-facing module meta + data access under Bridge scopes."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_instance_service import (
    OWNER_PROJECT,
    ModuleInstanceService,
)
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.modules.module_meta_validator import (
    META_DOCUMENT_SLUGS,
    validate_document_body,
)
from prodavan.application.modules.module_row_helpers import (
    check_table_slug,
    ensure_row_body,
    merge_column_defaults,
    merge_row_patch,
    validate_row_with_columns,
)
from prodavan.application.pod_identity.bridge import (
    PodBridgeClaims,
    module_actions_scope,
    module_meta_scope,
    module_rows_scope,
)
from prodavan.application.projects.project_runtime_module_service import ProjectRuntimeModuleService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_PLATFORM_ADMIN, Principal
from prodavan.domain.modules import ModuleBindKind
from prodavan.infrastructure.persistence.models.modules import ModuleRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

logger = logging.getLogger(__name__)

# Alias kept for existing imports / tests.
PodModuleAccessService = None  # set below after class def


class PodModuleDataService:
    """Meta + rows + actions for Project Pods — Bridge JWT + module scopes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._runtime = ProjectRuntimeModuleService(session)
        self._instances = ModuleInstanceService(session)
        self._bindings = ModuleBindingService(session)
        self._template_meta = ModuleMetaDocumentService(session)

    async def _require_project_row(self, project_id: str, bridge: PodBridgeClaims) -> ProjectRow:
        bridge.require_project(project_id)
        row = await self._session.get(ProjectRow, project_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
        if row.company_id != bridge.company_id or row.cabinet_id != bridge.cabinet_id:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="pod bridge tenancy mismatch",
            )
        return row

    def _require_module_rows(self, bridge: PodBridgeClaims, module_id: str) -> None:
        bridge.require_scope(module_rows_scope(module_id))

    def _require_module_meta(self, bridge: PodBridgeClaims, module_id: str) -> None:
        bridge.require_scope(module_meta_scope(module_id))

    def _require_module_actions(self, bridge: PodBridgeClaims, module_id: str) -> None:
        bridge.require_scope(module_actions_scope(module_id))

    async def _maybe_run_row_actions(
        self,
        *,
        bridge: PodBridgeClaims,
        cabinet_id: str,
        project_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        session_id: str | None = None,
        previous_body: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort auto-actions (equipment.budget_sync) after a bridge write.

        Mirrors the employee contour: agent-written request_lines/found_offers
        rows produce chat-scoped budget rows, same as manual UI writes.
        """
        from prodavan.application.modules.module_action_executor import ModuleActionExecutor

        principal = self._pod_principal(bridge)
        executor = ModuleActionExecutor(self._session)
        try:
            await executor.maybe_auto_index_tabular(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                principal=principal,
                employee=None,
                previous_body=previous_body,
                session_id=session_id,
            )
        except Exception:
            logger.exception(
                "pod auto index failed module=%s table=%s", module_id, table_slug
            )
        try:
            await executor.maybe_auto_budget_sync(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                principal=principal,
                employee=None,
                session_id=session_id,
            )
        except Exception:
            logger.exception(
                "pod auto budget sync failed module=%s table=%s", module_id, table_slug
            )

    async def _resolve_active_agent_session(self, project_id: str) -> str | None:
        """Latest active chat session of the project, or None.

        Platform MCP servers in the pod are pod-scoped processes spawned once
        per runtime (no per-chat env). When a Bridge data call carries no
        X-Prodavan-Session-Id, chats=current tables resolve to the chat the
        agent is currently working in instead of the shared synthetic 'main'
        bucket, so agent-written rows surface in the active chat's UI views.
        """
        from sqlalchemy import select

        from prodavan.domain.agent import AgentSessionStatus
        from prodavan.infrastructure.persistence.models.agent import AgentSessionRow

        q = await self._session.execute(
            select(AgentSessionRow.id)
            .where(
                AgentSessionRow.project_id == project_id,
                AgentSessionRow.status == AgentSessionStatus.ACTIVE,
            )
            .order_by(AgentSessionRow.updated_at.desc())
            .limit(1)
        )
        return q.scalar_one_or_none()

    async def _scoped_session_id(
        self, *, project_id: str, session_id: str | None
    ) -> str | None:
        """Explicit session wins; missing session -> active chat of the project."""
        sid = (session_id or "").strip() or None
        if sid is not None:
            return sid
        return await self._resolve_active_agent_session(project_id)

    def _pod_principal(self, bridge: PodBridgeClaims) -> Principal:
        """Privileged principal for nested ModuleActionExecutor after Bridge ACL."""
        return Principal(
            sub=f"pod:{bridge.pod_id}",
            roles=frozenset({ROLE_PLATFORM_ADMIN}),
        )

    async def _binding_flags(
        self, *, project_id: str, module_id: str
    ) -> tuple[str, bool, bool]:
        binding = await self._bindings.get_project_binding(module_id, project_id)
        if binding is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="module is not bound to project",
            )
        bind_kind = str(binding.bind_kind or ModuleBindKind.LOCAL)
        data_writable = bind_kind == ModuleBindKind.LOCAL or bool(binding.child_may_edit)
        meta_writable = data_writable
        return bind_kind, meta_writable, data_writable

    async def list_bound_modules(
        self, *, bridge: PodBridgeClaims, project_id: str
    ) -> list[dict[str, Any]]:
        await self._require_project_row(project_id, bridge)
        module_ids = await self._bindings.list_module_ids_for_project(project_id)
        out: list[dict[str, Any]] = []
        for mid in module_ids:
            mod = await self._session.get(ModuleRow, mid)
            bind_kind, meta_writable, data_writable = await self._binding_flags(
                project_id=project_id, module_id=mid
            )
            inst = await self._instances.get_instance(
                owner_kind=OWNER_PROJECT, owner_id=project_id, module_id=mid
            )
            if inst is None:
                inst = await self._instances.resolve_sot_instance(
                    module_id=mid,
                    owner_kind=OWNER_PROJECT,
                    owner_id=project_id,
                )
            out.append(
                {
                    "id": mid,
                    "module_id": mid,
                    "name": mod.name if mod is not None else mid,
                    "status": mod.status if mod is not None else None,
                    "bind_kind": bind_kind,
                    "meta_writable": meta_writable,
                    "data_writable": data_writable,
                    "instance_id": inst.id if inst else None,
                }
            )
        return out

    async def list_meta_documents(
        self, *, bridge: PodBridgeClaims, project_id: str, module_id: str
    ) -> list[dict[str, Any]]:
        await self._require_project_row(project_id, bridge)
        self._require_module_meta(bridge, module_id)
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=False
        )
        docs = await self._instances.list_meta_documents(instance_id=inst.id)
        if docs:
            return [
                {"module_id": module_id, "instance_id": inst.id, "slug": d["slug"]}
                for d in docs
            ]
        # Fall back to template slugs when instance has no docs yet.
        template = await self._template_meta.list_documents(module_id=module_id)
        return [
            {"module_id": module_id, "instance_id": inst.id, "slug": d["slug"]}
            for d in template
        ]

    async def get_meta_document(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        slug: str,
    ) -> dict[str, Any]:
        await self._require_project_row(project_id, bridge)
        self._require_module_meta(bridge, module_id)
        slug = (slug or "").strip()
        if slug not in META_DOCUMENT_SLUGS:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail=f"unknown meta document slug: {slug}",
            )
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=False
        )
        try:
            doc = await self._instances.get_meta_document(instance_id=inst.id, slug=slug)
        except AppError:
            doc = await self._template_meta.get_document(module_id=module_id, slug=slug)
        return {"module_id": module_id, "instance_id": inst.id, **doc}

    async def put_meta_document(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        slug: str,
        body: Any,
    ) -> dict[str, Any]:
        await self._require_project_row(project_id, bridge)
        self._require_module_meta(bridge, module_id)
        slug = (slug or "").strip()
        if slug not in META_DOCUMENT_SLUGS:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail=f"unknown meta document slug: {slug}",
            )
        validate_document_body(slug, body)
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
        )
        # Writable SoT must be the project leaf (local copy) or unlocked global —
        # put only on the resolved instance, never the catalog template.
        doc = await self._instances.put_meta_document(
            instance_id=inst.id, slug=slug, body=body
        )
        await self._session.flush()
        from prodavan.application.projects.workspace_outdated import (
            mark_workspace_outdated_for_project,
        )
        from prodavan.application.projects.workspace_sync_policy import (
            defer_or_schedule_project_sync,
        )

        await mark_workspace_outdated_for_project(self._session, project_id=project_id)
        await self._session.commit()
        notification = await defer_or_schedule_project_sync(
            self._session,
            project_id=project_id,
            source="pod_module_meta",
        )
        return {
            "module_id": module_id,
            "instance_id": inst.id,
            **doc,
            "rematerialize": notification.rematerialize_alias(),
        }

    async def list_data_rows(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        table_slug: str,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from prodavan.application.modules.chat_scope_ops import prepare_chat_scoped_list

        await self._require_project_row(project_id, bridge)
        self._require_module_rows(bridge, module_id)
        table_slug = check_table_slug(table_slug)
        session_id = await self._scoped_session_id(
            project_id=project_id, session_id=session_id
        )
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=False
        )
        filter_sid, empty = await prepare_chat_scoped_list(
            self._instances,
            instance_id=inst.id,
            module_id=module_id,
            table_slug=table_slug,
            session_id=session_id,
            db=self._session,
            project_id=project_id,
        )
        if empty:
            return []
        rows = await self._instances.list_data_rows(
            instance_id=inst.id, table_slug=table_slug, session_id=filter_sid
        )
        return [
            {"module_id": module_id, "instance_id": inst.id, **row}
            for row in rows
        ]

    async def create_data_row(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        table_slug: str,
        body: Any,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.modules.chat_scope_ops import prepare_chat_scoped_write

        project = await self._require_project_row(project_id, bridge)
        self._require_module_rows(bridge, module_id)
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        session_id = await self._scoped_session_id(
            project_id=project_id, session_id=session_id
        )
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
        )
        body, stamp_sid = await prepare_chat_scoped_write(
            self._instances,
            instance_id=inst.id,
            module_id=module_id,
            table_slug=table_slug,
            body=body,
            session_id=session_id,
            db=self._session,
            project_id=project_id,
        )
        columns_body = await self._instances.resolve_columns_body(
            instance_id=inst.id, module_id=module_id
        )
        if table_slug == "found_offers":
            # Agent-supplied price is in `currency` — store RUB in `price`,
            # original in `price_orig` (budget math stays single-currency).
            from prodavan.application.modules.equipment_fx import apply_fx_to_offer_body

            body = await apply_fx_to_offer_body(body)
        body = merge_column_defaults(
            columns_body=columns_body, table_slug=table_slug, body=body
        )
        body = validate_row_with_columns(
            columns_body=columns_body,
            table_slug=table_slug,
            body=body,
            cabinet_id=project.cabinet_id,
        )
        row = await self._instances.create_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            body=body,
            created_by=f"pod:{bridge.pod_id}",
            session_id=stamp_sid,
        )
        await self._session.commit()
        row_id = str(row.get("row_id") or "")
        if row_id:
            try:
                await self._maybe_run_row_actions(
                    bridge=bridge,
                    cabinet_id=project.cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    session_id=stamp_sid,
                )
                refreshed = await self._instances.get_data_row(
                    instance_id=inst.id, table_slug=table_slug, row_id=row_id
                )
                if refreshed is not None:
                    row = refreshed
            except Exception:
                logger.exception(
                    "pod module row actions failed project=%s module=%s table=%s",
                    project_id,
                    module_id,
                    table_slug,
                )
        return {"module_id": module_id, "instance_id": inst.id, **row}

    async def update_data_row(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        body: Any,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.modules.chat_scope_ops import prepare_chat_scoped_write

        project = await self._require_project_row(project_id, bridge)
        self._require_module_rows(bridge, module_id)
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        session_id = await self._scoped_session_id(
            project_id=project_id, session_id=session_id
        )
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
        )
        existing = await self._instances.get_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if existing is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body, stamp_sid = await prepare_chat_scoped_write(
            self._instances,
            instance_id=inst.id,
            module_id=module_id,
            table_slug=table_slug,
            body=body,
            session_id=session_id,
            db=self._session,
            project_id=project_id,
            existing_row=existing,
        )
        existing_body = (
            dict(existing["body"]) if isinstance(existing.get("body"), dict) else {}
        )
        body = merge_row_patch(existing_body, body)
        columns_body = await self._instances.resolve_columns_body(
            instance_id=inst.id, module_id=module_id
        )
        body = validate_row_with_columns(
            columns_body=columns_body,
            table_slug=table_slug,
            body=body,
            cabinet_id=project.cabinet_id,
        )
        row = await self._instances.upsert_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            session_id=stamp_sid,
        )
        await self._session.commit()
        try:
            await self._maybe_run_row_actions(
                bridge=bridge,
                cabinet_id=project.cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                session_id=stamp_sid,
                previous_body=(
                    existing.get("body")
                    if isinstance(existing.get("body"), dict)
                    else None
                ),
            )
            refreshed = await self._instances.get_data_row(
                instance_id=inst.id, table_slug=table_slug, row_id=row_id
            )
            if refreshed is not None:
                row = refreshed
        except Exception:
            logger.exception(
                "pod module row actions failed project=%s module=%s table=%s",
                project_id,
                module_id,
                table_slug,
            )
        return {"module_id": module_id, "instance_id": inst.id, **row}

    async def delete_data_row(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
    ) -> dict[str, Any]:
        await self._require_project_row(project_id, bridge)
        self._require_module_rows(bridge, module_id)
        table_slug = check_table_slug(table_slug)
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
        )
        deleted = await self._instances.delete_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if not deleted:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        if module_id == "mod_equipment" and table_slug == "catalogs":
            from prodavan.application.modules.equipment_catalog_opensearch import (
                delete_equipment_catalog_index,
            )
            from prodavan.infrastructure.persistence.models.projects import ProjectRow

            project = await self._session.get(ProjectRow, project_id)
            if project is not None and project.company_id:
                await delete_equipment_catalog_index(
                    row_id=row_id,
                    company_id=str(project.company_id),
                    cabinet_id=str(project.cabinet_id) if project.cabinet_id else None,
                    project_id=project_id,
                )
        await self._session.commit()
        return {"module_id": module_id, "deleted": True, "row_id": row_id}

    async def invoke_action(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        action_id: str,
        row_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        project = await self._require_project_row(project_id, bridge)
        self._require_module_actions(bridge, module_id)
        session_id = await self._scoped_session_id(
            project_id=project_id, session_id=session_id
        )
        binding = await self._bindings.get_project_binding(module_id, project_id)
        if binding is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="module is not bound to project",
            )
        from prodavan.application.modules.module_action_executor import ModuleActionExecutor

        result = await ModuleActionExecutor(self._session).invoke(
            cabinet_id=project.cabinet_id,
            module_id=module_id,
            action_id=action_id,
            principal=self._pod_principal(bridge),
            employee=None,
            row_id=row_id,
            project_id=project_id,
            session_id=session_id,
        )
        return {"module_id": module_id, **result}


PodModuleAccessService = PodModuleDataService
