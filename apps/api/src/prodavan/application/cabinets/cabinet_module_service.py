"""Cabinet runtime — bound modules; data/meta via cabinet module instances."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.modules.module_instance_service import (
    ModuleInstanceService,
)
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.modules.module_row_helpers import (
    check_table_slug,
    ensure_row_body,
    merge_column_defaults,
    merge_row_patch,
    validate_row_with_columns,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleRow,
)

logger = logging.getLogger(__name__)


def _attach_rematerialize(row: dict[str, Any], remat: dict[str, Any]) -> dict[str, Any]:
    from prodavan.application.projects.workspace_sync_policy import (
        WorkspaceSyncNotification,
        attach_workspace_sync,
    )

    mode = remat.get("mode")
    if mode not in ("deferred", "scheduled"):
        mode = "deferred" if int(remat.get("marked_outdated") or 0) > 0 else "scheduled"
    notification = WorkspaceSyncNotification(
        mode=mode,
        marked_outdated=int(remat.get("marked_outdated") or 0),
        scheduled=int(remat.get("scheduled") or 0),
        cabinet_id=remat.get("cabinet_id"),
        module_id=remat.get("module_id"),
        source=remat.get("source"),
        enqueued=tuple(remat.get("enqueued") or ()),
        sync=tuple(remat.get("sync") or ()),
        skipped=bool(remat.get("skipped")),
        reason=remat.get("reason"),
    )
    if (
        notification.scheduled <= 0
        and notification.marked_outdated <= 0
        and not notification.skipped
    ):
        return dict(row)
    return attach_workspace_sync(row, notification)


class CabinetModuleService:
    async def _chat_scope_session(
        self,
        *,
        inst,
        module_id: str,
        table_slug: str,
        session_id: str | None,
    ) -> str | None:
        """Resolved session filter for chats=current tables (None = shared).

        The cabinet contour has no project, so real sessions are not
        re-validated against a project here — the header comes from the
        employee UI (workContext active chat).
        """
        from prodavan.application.modules.chat_scope import (
            CHAT_SCOPE_CURRENT,
            chats_scope_from_tables_body,
            resolve_session_id,
        )

        tables_body = await self._instances.resolve_tables_body(
            instance_id=inst.id, module_id=module_id
        )
        if chats_scope_from_tables_body(tables_body, table_slug) != CHAT_SCOPE_CURRENT:
            return None
        return resolve_session_id(session_id)
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)
        self._meta = ModuleMetaDocumentService(session)
        self._instances = ModuleInstanceService(session)

    async def list_modules(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        q = await self._session.execute(
            select(ModuleCabinetBindingRow, ModuleRow)
            .join(ModuleRow, ModuleRow.id == ModuleCabinetBindingRow.module_id)
            .where(ModuleCabinetBindingRow.cabinet_id == cabinet_id)
            .order_by(ModuleRow.name)
        )
        return [
            {
                "id": mod.id,
                "name": mod.name,
                "status": mod.status,
                "bind_kind": bind.bind_kind,
                "child_may_edit": bind.child_may_edit,
            }
            for bind, mod in q.all()
        ]

    async def get_meta_document(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._require_module_binding(cabinet_id=cabinet_id, module_id=module_id)
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        inst = await self._cabinet_sot(cabinet_id=cabinet_id, module_id=module_id, write=False)
        try:
            doc = await self._instances.get_meta_document(instance_id=inst.id, slug=slug)
        except AppError:
            doc = await self._meta.get_document(module_id=module_id, slug=slug)
        return {"module_id": module_id, "instance_id": inst.id, **doc}

    async def list_data_rows(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        session_id: str | None = None,
    ) -> list[dict]:
        table_slug = check_table_slug(table_slug)
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        await self._require_module_binding(cabinet_id=cabinet_id, module_id=module_id)
        inst = await self._cabinet_sot(cabinet_id=cabinet_id, module_id=module_id, write=False)
        filter_session = await self._chat_scope_session(
            inst=inst, module_id=module_id, table_slug=table_slug, session_id=session_id
        )
        rows = await self._instances.list_data_rows(
            instance_id=inst.id, table_slug=table_slug, session_id=filter_session
        )
        return [
            {
                "module_id": module_id,
                "instance_id": inst.id,
                **row,
            }
            for row in rows
        ]

    async def create_data_row(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        body: Any,
        principal: Principal,
        employee: EmployeeRow | None,
        session_id: str | None = None,
    ) -> dict:
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._require_module_binding(cabinet_id=cabinet_id, module_id=module_id)
        inst = await self._cabinet_sot(cabinet_id=cabinet_id, module_id=module_id, write=True)
        stamp_session = await self._chat_scope_session(
            inst=inst, module_id=module_id, table_slug=table_slug, session_id=session_id
        )
        if stamp_session is None:
            body = {k: v for k, v in dict(body or {}).items() if k != "session_id"}
        else:
            body = dict(body or {})
            body["session_id"] = stamp_session
        columns_body = await self._instances.resolve_columns_body(
            instance_id=inst.id, module_id=module_id
        )
        body = merge_column_defaults(
            columns_body=columns_body, table_slug=table_slug, body=body
        )
        body = validate_row_with_columns(
            columns_body=columns_body,
            table_slug=table_slug,
            body=body,
            cabinet_id=cabinet_id,
        )
        created_by = employee.id if employee is not None else principal.sub
        row = await self._instances.create_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            body=body,
            created_by=created_by,
            session_id=stamp_session,
        )
        await self._session.commit()
        remat = await self._schedule_rematerialize(cabinet_id=cabinet_id, module_id=module_id)
        out = _attach_rematerialize(
            {"module_id": module_id, "instance_id": inst.id, **row},
            remat,
        )
        row_id = str(row.get("row_id") or "")
        action_error: AppError | None = None
        try:
            await self._maybe_run_row_actions(
                session_id=stamp_session,
                cabinet_id=cabinet_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                principal=principal,
                employee=employee,
            )
        except AppError as exc:
            action_error = exc
        if row_id:
            refreshed = await self._instances.get_data_row(
                instance_id=inst.id, table_slug=table_slug, row_id=row_id
            )
            if refreshed is not None:
                out = _attach_rematerialize(
                    {"module_id": module_id, "instance_id": inst.id, **refreshed},
                    remat,
                )
        if action_error is not None:
            raise action_error
        return out

    async def update_data_row(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        body: Any,
        principal: Principal,
        employee: EmployeeRow | None,
        run_actions: bool = True,
        session_id: str | None = None,
    ) -> dict:
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._require_module_binding(cabinet_id=cabinet_id, module_id=module_id)
        inst = await self._cabinet_sot(cabinet_id=cabinet_id, module_id=module_id, write=True)
        existing = await self._instances.get_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if existing is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        update_session = await self._chat_scope_session(
            inst=inst, module_id=module_id, table_slug=table_slug, session_id=session_id
        )
        if update_session is not None and (existing.get("session_id") or "").strip() != update_session:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
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
            cabinet_id=cabinet_id,
        )
        row = await self._instances.upsert_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            session_id=update_session,
        )
        await self._session.flush()
        # Avoid double-commit when nested from set_profile; callers that need
        # rematerialize still commit. Prefer commit for HTTP handlers.
        await self._session.commit()
        remat = await self._schedule_rematerialize(cabinet_id=cabinet_id, module_id=module_id)
        out = _attach_rematerialize(
            {"module_id": module_id, "instance_id": inst.id, **row},
            remat,
        )
        if run_actions:
            action_error: AppError | None = None
            try:
                await self._maybe_run_row_actions(
                session_id=update_session,
                    cabinet_id=cabinet_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    principal=principal,
                    employee=employee,
                    previous_body=(
                        existing.get("body")
                        if isinstance(existing.get("body"), dict)
                        else None
                    ),
                )
            except AppError as exc:
                action_error = exc
            refreshed = await self._instances.get_data_row(
                instance_id=inst.id, table_slug=table_slug, row_id=row_id
            )
            if refreshed is not None:
                out = _attach_rematerialize(
                    {"module_id": module_id, "instance_id": inst.id, **refreshed},
                    remat,
                )
            if action_error is not None:
                raise action_error
        return out

    async def delete_data_row(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        session_id: str | None = None,
    ) -> dict:
        table_slug = check_table_slug(table_slug)
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._require_module_binding(cabinet_id=cabinet_id, module_id=module_id)
        inst = await self._cabinet_sot(cabinet_id=cabinet_id, module_id=module_id, write=True)
        delete_session = await self._chat_scope_session(
            inst=inst, module_id=module_id, table_slug=table_slug, session_id=session_id
        )
        if delete_session is not None:
            existing = await self._instances.get_data_row(
                instance_id=inst.id, table_slug=table_slug, row_id=row_id
            )
            if existing is None or (existing.get("session_id") or "").strip() != delete_session:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        ok = await self._instances.delete_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if not ok:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        if module_id == "mod_equipment" and table_slug == "catalogs":
            try:
                from prodavan.application.modules.equipment_catalog_opensearch import (
                    delete_equipment_catalog_index,
                )

                cab = await self._session.get(CabinetInstanceRow, cabinet_id)
                company_id = str(cab.company_id) if cab is not None and cab.company_id else ""
                if company_id:
                    await delete_equipment_catalog_index(
                        row_id=row_id,
                        company_id=company_id,
                        cabinet_id=cabinet_id,
                    )
            except Exception:
                logger.exception(
                    "equipment catalog OS delete failed cabinet=%s row=%s",
                    cabinet_id,
                    row_id,
                )
        # Commit the deletion: create/update commit in this service, delete
        # used to rely on the request teardown — which rolls back, so the
        # flushed DELETE was silently discarded and the row "came back"
        # on the next list/poll (row survived with 200 OK).
        await self._session.commit()

        from prodavan.application.projects.rematerialize_scheduler import schedule_cabinet_rematerialize

        return await schedule_cabinet_rematerialize(
            self._session,
            cabinet_id=cabinet_id,
            module_id=module_id,
        )

    async def _schedule_rematerialize(
        self, *, cabinet_id: str, module_id: str
    ) -> dict[str, Any]:
        from prodavan.application.projects.rematerialize_scheduler import (
            schedule_cabinet_rematerialize,
        )

        try:
            return await schedule_cabinet_rematerialize(
                self._session,
                cabinet_id=cabinet_id,
                module_id=module_id,
            )
        except Exception:
            logger.exception(
                "cabinet rematerialize failed cabinet=%s module=%s",
                cabinet_id,
                module_id,
            )
            return {"mode": "deferred", "error": "sync_failed", "scheduled": 0}

    async def _maybe_run_row_actions(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        previous_body: dict | None = None,
        session_id: str | None = None,
    ) -> None:
        if not row_id:
            return
        from prodavan.application.modules.module_action_executor import ModuleActionExecutor

        await ModuleActionExecutor(self._session).maybe_auto_index_tabular(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            principal=principal,
            employee=employee,
            previous_body=previous_body,
            session_id=session_id,
        )
        await ModuleActionExecutor(self._session).maybe_auto_budget_sync(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
        )

    async def _require_module_binding(self, *, cabinet_id: str, module_id: str) -> None:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.id).where(
                ModuleCabinetBindingRow.cabinet_id == cabinet_id,
                ModuleCabinetBindingRow.module_id == module_id,
            )
        )
        if q.scalar_one_or_none() is None:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="module is not bound to cabinet",
            )

    async def _cabinet_sot(self, *, cabinet_id: str, module_id: str, write: bool):
        from prodavan.application.modules.module_instance_service import OWNER_CABINET

        inst = await self._instances.ensure_cabinet_instance(
            cabinet_id=cabinet_id, module_id=module_id
        )
        if write:
            may = await self._instances.sot_may_edit(
                module_id=module_id,
                owner_kind=OWNER_CABINET,
                owner_id=cabinet_id,
            )
            if not may:
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail="global cabinet module bind is read-only",
                )
        return inst

