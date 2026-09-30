"""Execute declarative module actions from meta slug `actions`."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService
from prodavan.application.content.tabular_index import index_tabular_bytes
from prodavan.application.content.upload_service import UploadService
from prodavan.application.pod_service.container_env_resolver import field_value_as_secret_ref
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.content import ContentBlobVersionRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import ModuleMetaDocumentRow
from prodavan.infrastructure.secrets.owner_module_secret_store import assert_module_secret_ref_scope
from prodavan.infrastructure.secrets.store import get_secret_store

logger = logging.getLogger(__name__)

_OWNER_REMOTE_KINDS = frozenset(
    {
        "content.probe_remote_sql",
        "content.list_remote_sql_databases",
        "content.list_remote_sql_tables",
        "content.index_opensearch",
    }
)


def remote_probe_connection_key(body: dict[str, Any], params: dict[str, Any]) -> str:
    """Stable key of fields that actually affect remote SQL connect/probe.

    UI-only flags (``remote_dsn_has_*``, ``remote_dsn_reachable``, …) are excluded so
    partial client patches do not re-probe the previous DSN.
    """
    dsn_col = str(params.get("dsn_column") or "remote_dsn")
    table_col = str(params.get("remote_table_column") or "remote_table")
    db_col = str(params.get("remote_database_column") or "remote_database")
    user_col = str(params.get("remote_user_column") or "remote_user")
    password_col = str(params.get("remote_password_column") or "remote_password")
    return "|".join(
        [
            field_value_as_secret_ref(body.get(dsn_col)) or "",
            str(body.get(db_col) or "").strip(),
            str(body.get(table_col) or "").strip(),
            str(body.get(user_col) or "").strip(),
            field_value_as_secret_ref(body.get(password_col)) or "",
        ]
    )


def _product_seed_actions(module_id: str) -> list[dict[str, Any]]:
    """Return the product seed ``actions`` list for ``module_id`` (META-P1b).

    Single source of truth for the seed fallback: when DB meta is empty (fresh
    install / unseeded module), the UI picker and the action executor both
    see the same list — no two contracts. ``upsert_product_modules`` (Alembic)
    mirrors seeds into DB meta on migration, so this fallback is only hit on
    un-migrated / partial envs; it is the safety net, not the primary SoT.
    """
    from prodavan.application.platform.product_module_seeds import PRODUCT_MODULES

    for mid, _name, slugs in PRODUCT_MODULES:
        if mid != module_id:
            continue
        actions = slugs.get("actions")
        if isinstance(actions, list):
            return [dict(item) for item in actions if isinstance(item, dict)]
        return []
    return []


class ModuleActionExecutor:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._modules = CabinetModuleService(session)

    async def invoke(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        action_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        row_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        action = await self._load_action(module_id=module_id, action_id=action_id)
        if action.get("enabled") is False:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="action is disabled",
            )
        kind = str(action.get("kind") or "")
        params = action.get("params") if isinstance(action.get("params"), dict) else {}
        project_id = (project_id or "").strip() or None
        if project_id:
            await self._assert_project_in_cabinet(
                cabinet_id=cabinet_id,
                project_id=project_id,
                principal=principal,
                employee=employee,
            )

        if kind == "data.create_row":
            table_slug = params.get("table_slug")
            if not isinstance(table_slug, str) or not table_slug:
                raise AppError(
                    code="META_VALIDATION",
                    title="Meta validation error",
                    status=422,
                    detail="data.create_row requires params.table_slug",
                )
            defaults = params.get("defaults")
            body = dict(defaults) if isinstance(defaults, dict) else {}
            row = await self._modules.create_data_row(
                cabinet_id=cabinet_id,
                module_id=module_id,
                table_slug=table_slug,
                body=body,
                principal=principal,
                employee=employee,
                session_id=session_id,
            )
            return {"kind": kind, "row": row}

        if kind == "data.delete_row":
            table_slug = params.get("table_slug")
            if not isinstance(table_slug, str) or not table_slug:
                raise AppError(
                    code="META_VALIDATION",
                    title="Meta validation error",
                    status=422,
                    detail="data.delete_row requires params.table_slug",
                )
            if not row_id:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="row_id required for data.delete_row",
                )
            await self._modules.delete_data_row(
                cabinet_id=cabinet_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                principal=principal,
                employee=employee,
                session_id=session_id,
            )
            return {"kind": kind, "deleted_row_id": row_id}

        if kind == "data.select_row":
            return await self._select_row(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
                session_id=session_id,
            )

        if kind == "content.index_tabular":
            return await self._index_tabular(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
                project_id=project_id,
            )

        if kind == "content.index_opensearch":
            return await self._index_opensearch(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
                project_id=project_id,
            )

        if kind == "content.probe_remote_sql":
            return await self._probe_remote_sql(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
                project_id=project_id,
            )

        if kind == "content.list_remote_sql_tables":
            return await self._list_remote_sql_tables(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
                project_id=project_id,
            )

        if kind == "content.list_remote_sql_databases":
            return await self._list_remote_sql_databases(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
                project_id=project_id,
            )

        if kind == "documents.convert":
            return await self._documents_convert(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
                project_id=project_id,
            )

        if kind == "documents.fill_template":
            return await self._documents_fill_template(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
                project_id=project_id,
            )

        if kind == "equipment.budget_sync":
            return await self._budget_sync(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                principal=principal,
                employee=employee,
                project_id=project_id,
                session_id=session_id,
            )

        if kind == "equipment.budget_export":
            return await self._budget_export(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                principal=principal,
                employee=employee,
                project_id=project_id,
                session_id=session_id,
            )

        raise AppError(
            code="NOT_IMPLEMENTED",
            title="Not Implemented",
            status=501,
            detail=f"action kind not supported: {kind}",
        )

    async def invoke_owner(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        module_id: str,
        action_id: str,
        row_id: str | None = None,
        principal: Principal,
    ) -> dict[str, Any]:
        """Platform/company module instance — remote SQL + OpenSearch index actions."""
        action = await self._load_action(module_id=module_id, action_id=action_id)
        if action.get("enabled") is False:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="action is disabled",
            )
        kind = str(action.get("kind") or "")
        if kind not in _OWNER_REMOTE_KINDS:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="action not available for owner module scope",
            )
        params = action.get("params") if isinstance(action.get("params"), dict) else {}
        common = dict(
            module_id=module_id,
            params=params,
            row_id=row_id,
            principal=principal,
            employee=None,
            owner_kind=owner_kind,
            owner_id=owner_id,
            cabinet_id="",
            project_id=None,
        )
        if kind == "content.probe_remote_sql":
            return await self._probe_remote_sql(**common)
        if kind == "content.list_remote_sql_databases":
            return await self._list_remote_sql_databases(**common)
        if kind == "content.list_remote_sql_tables":
            return await self._list_remote_sql_tables(**common)
        if kind == "content.index_opensearch":
            return await self._index_opensearch(**common)
        raise AppError(
            code="NOT_IMPLEMENTED",
            title="Not Implemented",
            status=501,
            detail=f"action kind not supported: {kind}",
        )

    async def _budget_sync(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Sync budget_lines rows from request_lines + their selected offers.

        Snapshot fields (title/part_number/qty/price_in/seller) follow the
        best offer; user fields (vat/markup/comment) are never overwritten.
        """
        lines_table = str(params.get("lines_table") or "request_lines")
        offers_table = str(params.get("offers_table") or "found_offers")
        budget_table = str(params.get("budget_table") or "budget_lines")
        catalogs_table = str(params.get("catalogs_table") or "catalogs")

        lines = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=lines_table,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )
        offers = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=offers_table,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )
        budget_rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=budget_table,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )
        try:
            catalogs = await self._list_rows_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=catalogs_table,
                principal=principal,
                employee=employee,
                session_id=session_id,
            )
        except Exception:
            catalogs = []

        catalog_names: dict[str, str] = {}
        for c in catalogs:
            if not isinstance(c, dict):
                continue
            name = str((c.get("body") or {}).get("name") or "").strip()
            if name:
                catalog_names[str(c.get("row_id"))] = name
        offers_by_id: dict[str, dict[str, Any]] = {}
        for o in offers:
            if isinstance(o, dict):
                offers_by_id[str(o.get("row_id"))] = o.get("body") or {}
        offers_by_line: dict[str, dict[str, Any]] = {}
        for o in offers:
            if not isinstance(o, dict):
                continue
            body = o.get("body") or {}
            if body.get("is_selected") is True:
                offers_by_line[str(body.get("line_id") or "")] = body
        budget_by_line: dict[str, dict[str, Any]] = {}
        for b in budget_rows:
            if not isinstance(b, dict):
                continue
            body = b.get("body") or {}
            lid = str(body.get("line_id") or "")
            if lid:
                budget_by_line[lid] = b

        created = 0
        updated = 0
        for line in lines:
            if not isinstance(line, dict):
                continue
            line_id = str(line.get("row_id") or "")
            if not line_id:
                continue
            line_body = line.get("body") or {}
            offer = offers_by_id.get(str(line_body.get("selected_offer_id") or "")) or {}
            if not offer:
                offer = offers_by_line.get(line_id) or {}

            seller = str(catalog_names.get(str(offer.get("catalog_id") or "")) or "").strip()
            if not seller:
                seller = str(offer.get("brand") or "").strip()
            snapshot = {
                "line_id": line_id,
                "title": str(offer.get("title") or line_body.get("title") or "").strip()
                or "Не найден",
                "part_number": str(
                    line_body.get("part_number") or offer.get("part_number") or ""
                ).strip()
                or "Не определен",
                "qty": line_body.get("qty") or 1,
                "price_in": offer.get("price") or 0,
                "seller": seller or "Не найден",
            }
            existing = budget_by_line.get(line_id)
            if existing is None:
                body = dict(snapshot)
                body.setdefault("vat", 0.22)
                body.setdefault("markup", 0.1)
                await self._modules.create_data_row(
                    cabinet_id=cabinet_id,
                    module_id=module_id,
                    table_slug=budget_table,
                    body=body,
                    principal=principal,
                    employee=employee,
                    session_id=session_id,
                )
                created += 1
                continue
            row_id = str(existing.get("row_id") or "")
            if not row_id:
                continue
            body = dict(existing.get("body") or {})
            body.update(snapshot)
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=budget_table,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                run_actions=False,
                session_id=session_id,
            )
            updated += 1

        return {
            "kind": "equipment.budget_sync",
            "created": created,
            "updated": updated,
            "lines": len(lines),
        }

    async def _budget_export(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Export budget rows as the filled Commerce КП xlsx template."""
        from prodavan.application.documents.service import DocumentsService
        from prodavan.application.modules.equipment_budget import (
            fill_budget_workbook,
            load_budget_template,
        )

        budget_table = str(params.get("budget_table") or "budget_lines")
        rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=budget_table,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )
        bodies = [r.get("body") or {} for r in rows if isinstance(r, dict)]
        if not bodies:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="no budget lines to export (run budget sync first)",
            )
        template = load_budget_template()
        data = fill_budget_workbook(template, bodies, {})

        company_id = await self._resolve_documents_company_id(
            cabinet_id=cabinet_id, project_id=project_id
        )
        if not company_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="no company tenancy for budget export",
            )
        service = DocumentsService(self._session)
        ref = await service.save_document(
            data,
            filename="budget.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            company_id=company_id,
            cabinet_id=cabinet_id or None,
            project_id=project_id,
            principal=principal,
            employee=employee,
        )
        return {"kind": "equipment.budget_export", "file_ref": ref, "rows": len(bodies)}

    async def maybe_auto_budget_sync(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Best-effort: equipment.budget_sync after request_lines/found_offers writes.

        ``session_id`` scopes source reads and stamps budget rows with the chat
        session of the triggering write (None -> shared 'main' bucket).
        """
        try:
            matched_params: dict[str, Any] | None = None
            for action in await self._list_actions(module_id=module_id):
                if action.get("enabled") is False:
                    continue
                if str(action.get("kind") or "") != "equipment.budget_sync":
                    continue
                params = action.get("params") if isinstance(action.get("params"), dict) else {}
                watched = {
                    str(params.get("lines_table") or "request_lines"),
                    str(params.get("offers_table") or "found_offers"),
                }
                if table_slug not in watched:
                    continue
                trigger = action.get("trigger") if isinstance(action.get("trigger"), dict) else {}
                on = trigger.get("on") if isinstance(trigger.get("on"), list) else []
                if "row.created" not in on and "row.updated" not in on:
                    continue
                matched_params = params
                break
            if matched_params is None:
                return
            await self._budget_sync(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=matched_params,
                principal=principal,
                employee=employee,
                project_id=project_id,
                session_id=session_id,
            )
        except AppError:
            raise
        except Exception:
            logger.exception("auto budget sync failed module=%s table=%s", module_id, table_slug)

    async def maybe_auto_index_tabular(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        previous_body: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> None:
        """Best-effort: run content.index_tabular actions matching table after row write."""
        for action in await self._list_actions(module_id=module_id):
            if action.get("enabled") is False:
                continue
            if str(action.get("kind") or "") != "content.index_tabular":
                continue
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            if str(params.get("table_slug") or "") != table_slug:
                continue
            trigger = action.get("trigger") if isinstance(action.get("trigger"), dict) else {}
            on = trigger.get("on") if isinstance(trigger.get("on"), list) else ["row.created", "row.updated"]
            if "row.created" not in on and "row.updated" not in on:
                continue
            source_column = str(params.get("source_column") or "source_file")
            status_col = str(params.get("status_column") or "status")
            rows = await self._list_module_rows(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                principal=principal,
                employee=employee,
                session_id=session_id,
            )
            target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
            if target is None:
                continue
            body = target.get("body") if isinstance(target.get("body"), dict) else {}
            kind_col = str(params.get("source_kind_column") or "source_kind")
            expected_kind = str(params.get("expected_source_kind") or "local").strip().lower()
            actual_kind = str(body.get(kind_col) or "local").strip().lower()
            if actual_kind != expected_kind:
                continue
            if not isinstance(body.get(source_column), dict):
                continue
            status = str(body.get(status_col) or "")
            if status == "indexing":
                continue
            artifact_col = str(params.get("artifact_column") or "artifact_ref")
            source_ref = body.get(source_column)
            storage_key = (
                str(source_ref.get("storage_key") or "")
                if isinstance(source_ref, dict)
                else ""
            )
            # Skip only when already indexed for the same source blob.
            if (
                status == "ready"
                and isinstance(body.get(artifact_col), dict)
                and storage_key
                and storage_key == str(body.get("indexed_source_key") or "")
            ):
                continue
            try:
                await self._index_tabular(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    params=params,
                    row_id=row_id,
                    principal=principal,
                    employee=employee,
                )
            except AppError:
                # Domain failure already persisted as status=error — surface to client.
                raise
            except Exception as exc:
                logger.exception(
                    "auto index_tabular failed module=%s table=%s row=%s",
                    module_id,
                    table_slug,
                    row_id,
                )
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"index_tabular failed: {exc}",
                ) from exc
        await self.maybe_auto_probe_remote_sql(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            principal=principal,
            employee=employee,
            project_id=project_id,
            previous_body=previous_body,
        )
        await self.maybe_auto_index_opensearch(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            principal=principal,
            employee=employee,
            project_id=project_id,
            previous_body=previous_body,
        )

    async def maybe_auto_index_opensearch(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        previous_body: dict[str, Any] | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> None:
        """Best-effort: enqueue content.index_opensearch after catalog row write."""
        _ = previous_body
        for action in await self._list_actions(module_id=module_id):
            if action.get("enabled") is False:
                continue
            if str(action.get("kind") or "") != "content.index_opensearch":
                continue
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            if str(params.get("table_slug") or "") != table_slug:
                continue
            trigger = action.get("trigger") if isinstance(action.get("trigger"), dict) else {}
            on = trigger.get("on") if isinstance(trigger.get("on"), list) else ["row.created", "row.updated"]
            if "row.created" not in on and "row.updated" not in on:
                continue
            rows = await self._list_rows_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
            )
            target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
            if target is None:
                continue
            body = target.get("body") if isinstance(target.get("body"), dict) else {}
            status_col = str(params.get("status_column") or "status")
            if str(body.get(status_col) or "") == "indexing":
                continue
            try:
                await self._index_opensearch(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    params=params,
                    row_id=row_id,
                    principal=principal,
                    employee=employee,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                )
            except AppError:
                raise
            except Exception as exc:
                logger.exception(
                    "auto index_opensearch failed module=%s table=%s row=%s",
                    module_id,
                    table_slug,
                    row_id,
                )
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"index_opensearch failed: {exc}",
                ) from exc

    async def maybe_auto_probe_remote_sql(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        previous_body: dict[str, Any] | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> None:
        """Best-effort: run content.probe_remote_sql after remote catalog row write."""
        for action in await self._list_actions(module_id=module_id):
            if action.get("enabled") is False:
                continue
            if str(action.get("kind") or "") != "content.probe_remote_sql":
                continue
            params = action.get("params") if isinstance(action.get("params"), dict) else {}
            if str(params.get("table_slug") or "") != table_slug:
                continue
            trigger = action.get("trigger") if isinstance(action.get("trigger"), dict) else {}
            on = trigger.get("on") if isinstance(trigger.get("on"), list) else ["row.created", "row.updated"]
            if "row.created" not in on and "row.updated" not in on:
                continue
            rows = await self._list_rows_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
            )
            target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
            if target is None:
                continue
            body = target.get("body") if isinstance(target.get("body"), dict) else {}
            kind_col = str(params.get("source_kind_column") or "source_kind")
            expected_kind = str(params.get("expected_source_kind") or "remote").strip().lower()
            actual_kind = str(body.get(kind_col) or "local").strip().lower()
            if actual_kind != expected_kind:
                continue
            dsn_col = str(params.get("dsn_column") or "remote_dsn")
            table_col = str(params.get("remote_table_column") or "remote_table")
            status_col = str(params.get("status_column") or "status")
            if field_value_as_secret_ref(body.get(dsn_col)) is None:
                continue
            # Skip when only UI flags changed — do not re-probe the previous DSN.
            conn_key = remote_probe_connection_key(body, params)
            if previous_body is not None:
                prev = previous_body if isinstance(previous_body, dict) else {}
                if remote_probe_connection_key(prev, params) == conn_key:
                    continue
            # remote_table may be empty when DSN already has /dbname (default SQL table).
            status = str(body.get(status_col) or "")
            if status == "indexing":
                continue
            probe_key = (
                f"{field_value_as_secret_ref(body.get(dsn_col))}|"
                f"{str(body.get(table_col) or '').strip()}|"
                f"{body.get('remote_dsn_has_database')}"
            )
            # Schema already probed for this DSN/table (status stays draft until OS index).
            if probe_key == str(body.get("probed_remote_key") or ""):
                continue
            try:
                await self._probe_remote_sql(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    params=params,
                    row_id=row_id,
                    principal=principal,
                    employee=employee,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                )
            except AppError:
                raise
            except Exception as exc:
                logger.exception(
                    "auto probe_remote_sql failed module=%s table=%s row=%s",
                    module_id,
                    table_slug,
                    row_id,
                )
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"probe_remote_sql failed: {exc}",
                ) from exc

    async def _assert_project_in_cabinet(
        self,
        *,
        cabinet_id: str,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        from prodavan.application.project_service.access import ProjectAccessPolicy

        project = await ProjectAccessPolicy(self._session).require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=False,
            allow_paused=True,
        )
        if str(project.cabinet_id) != cabinet_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="project_id does not belong to cabinet",
            )

    async def _list_module_rows(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if project_id:
            from prodavan.application.projects.project_runtime_module_service import (
                ProjectRuntimeModuleService,
            )

            return await ProjectRuntimeModuleService(self._session).list_data_rows(
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                principal=principal,
                employee=employee,
                session_id=session_id,
            )
        return await self._modules.list_data_rows(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )

    async def _list_rows_for_scope(
        self,
        *,
        module_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        cabinet_id: str = "",
        project_id: str | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if owner_kind and owner_id:
            from prodavan.application.modules.owner_module_data_service import (
                OwnerModuleDataService,
            )

            return await OwnerModuleDataService(self._session).list_data_rows(
                owner_kind=owner_kind,
                owner_id=owner_id,
                module_id=module_id,
                table_slug=table_slug,
            )
        return await self._list_module_rows(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )

    async def _update_module_row(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        body: dict[str, Any],
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        run_actions: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if project_id:
            from prodavan.application.projects.project_runtime_module_service import (
                ProjectRuntimeModuleService,
            )

            return await ProjectRuntimeModuleService(self._session).update_data_row(
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                run_actions=run_actions,
                session_id=session_id,
            )
        return await self._modules.update_data_row(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=principal,
            employee=employee,
            run_actions=run_actions,
            session_id=session_id,
        )

    async def _update_row_for_scope(
        self,
        *,
        module_id: str,
        table_slug: str,
        row_id: str,
        body: dict[str, Any],
        principal: Principal,
        employee: EmployeeRow | None,
        cabinet_id: str = "",
        project_id: str | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
        run_actions: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if owner_kind and owner_id:
            from prodavan.application.modules.owner_module_data_service import (
                OwnerModuleDataService,
            )

            return await OwnerModuleDataService(self._session).update_data_row(
                owner_kind=owner_kind,
                owner_id=owner_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                run_actions=run_actions,
            )
        return await self._update_module_row(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=principal,
            employee=employee,
            run_actions=run_actions,
            session_id=session_id,
        )

    async def _select_row(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        table_slug = params.get("table_slug")
        select_field = str(params.get("select_field") or "is_selected")
        group_by = params.get("group_by")
        if not isinstance(table_slug, str) or not table_slug:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="data.select_row requires params.table_slug",
            )
        if not row_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="row_id required for data.select_row",
            )
        if not isinstance(group_by, str) or not group_by:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="data.select_row requires params.group_by",
            )

        rows = await self._modules.list_data_rows(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )
        target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body = dict(target.get("body") or {})
        group_val = body.get(group_by)
        updated = 0
        for row in rows:
            b = dict(row.get("body") or {})
            if b.get(group_by) != group_val:
                continue
            rid = str(row.get("row_id"))
            want = rid == row_id
            if bool(b.get(select_field)) is want:
                continue
            b[select_field] = want
            await self._modules.update_data_row(
                cabinet_id=cabinet_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=rid,
                body=b,
                principal=principal,
                employee=employee,
                run_actions=False,
                session_id=session_id,
            )
            updated += 1

        parent = params.get("parent") if isinstance(params.get("parent"), dict) else None
        if parent:
            parent_table = parent.get("table_slug")
            id_from = parent.get("id_from") or group_by
            set_field = parent.get("set_field") or "selected_offer_id"
            parent_id = body.get(id_from) if isinstance(id_from, str) else None
            if isinstance(parent_table, str) and parent_table and parent_id:
                parent_rows = await self._modules.list_data_rows(
                    cabinet_id=cabinet_id,
                    module_id=module_id,
                    table_slug=parent_table,
                    principal=principal,
                    employee=employee,
                    session_id=session_id,
                )
                for prow in parent_rows:
                    if str(prow.get("row_id")) == str(parent_id):
                        pb = dict(prow.get("body") or {})
                        pb[str(set_field)] = row_id
                        pb["status"] = "selected"
                        await self._modules.update_data_row(
                            cabinet_id=cabinet_id,
                            module_id=module_id,
                            table_slug=parent_table,
                            row_id=str(prow.get("row_id")),
                            body=pb,
                            principal=principal,
                            employee=employee,
                            run_actions=False,
                            session_id=session_id,
                        )
                        break

        # Best-offer swap: keep the budget snapshot in sync (equipment
        # budget_sync action watches the offers table).
        await self.maybe_auto_budget_sync(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )
        return {"kind": "data.select_row", "row_id": row_id, "updated": updated}

    async def _index_tabular(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        table_slug = params.get("table_slug")
        source_column = str(params.get("source_column") or "source_file")
        if not isinstance(table_slug, str) or not table_slug:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="content.index_tabular requires params.table_slug",
            )
        if not row_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="row_id required for content.index_tabular",
            )

        rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body = dict(target.get("body") or {})
        kind_col = str(params.get("source_kind_column") or "source_kind")
        expected_kind = str(params.get("expected_source_kind") or "local").strip().lower()
        actual_kind = str(body.get(kind_col) or "local").strip().lower()
        if actual_kind != expected_kind:
            return {
                "kind": "content.index_tabular",
                "status": "skipped",
                "reason": f"source_kind={actual_kind}",
                "row_id": row_id,
            }
        status_col = str(params.get("status_column") or "status")
        error_col = str(params.get("error_column") or "error")
        artifact_col = str(params.get("artifact_column") or "artifact_ref")
        row_count_col = str(params.get("row_count_column") or "row_count")
        columns_col = str(params.get("columns_json_column") or "columns_json")

        file_ref = body.get(source_column)
        if not isinstance(file_ref, dict):
            body[status_col] = "draft"
            body[error_col] = None
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )
            return {"kind": "content.index_tabular", "status": "draft", "row_id": row_id}

        body[status_col] = "indexing"
        body[error_col] = None
        # Progress heartbeat fields (rendered by UI as «В процессе (x из y)»
        # and used by the beat sweep to heal rows stuck after a deploy).
        body["indexed_count"] = 0
        body["total_rows"] = None
        body["indexing_started_at"] = datetime.now(UTC).isoformat()
        await self._update_row_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
            run_actions=False,
        )

        indexed_columns: list[str] = []
        try:
            storage_key = str(file_ref.get("storage_key") or "")
            filename = str(file_ref.get("filename") or "data.csv")
            if not storage_key:
                raise ValueError("file_ref.storage_key missing")
            raw = ensure_file_store().get_bytes_sync(storage_key)
            indexed = index_tabular_bytes(raw, filename=filename)
            indexed_columns = list(indexed.columns)

            cab = await self._session.get(CabinetInstanceRow, cabinet_id)
            if cab is None or not cab.company_id:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="cabinet has no company for content upload",
                )
            asset_id, version_id = await UploadService(self._session).upload_bytes_as_asset(
                data=indexed.sqlite_bytes,
                owner_company_id=cab.company_id,
                principal=principal,
                employee=employee,
                mime="application/x-sqlite3",
                title=f"{filename}.sqlite",
                link_kind="module_catalog",
                link_id=row_id,
            )
            ver = await self._session.get(ContentBlobVersionRow, version_id)
            if ver is None:
                raise RuntimeError("blob version missing after upload")
            body[artifact_col] = {
                "asset_id": asset_id,
                "version_id": version_id,
                "storage_key": ver.storage_key,
                "filename": f"{row_id}.sqlite",
            }
            body["indexed_source_key"] = storage_key
            body[row_count_col] = indexed.row_count
            body[columns_col] = json.dumps(indexed.columns, ensure_ascii=False)
            body[status_col] = "ready"
            body[error_col] = None
        except AppError:
            raise
        except Exception as exc:
            body[status_col] = "error"
            body[error_col] = str(exc)[:500]
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"index_tabular failed: {exc}",
            ) from exc

        await self._update_row_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
            run_actions=False,
        )
        return {
            "kind": "content.index_tabular",
            "status": "ready",
            "row_id": row_id,
            "row_count": body.get(row_count_col),
            "columns": indexed_columns,
        }

    async def _index_opensearch(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.modules.equipment_catalog_opensearch import (
            column_map_ready,
            extract_local_columns,
            resolve_equipment_catalog_tenancy,
            run_index_equipment_catalog,
        )
        from prodavan.application.modules.module_instance_service import ModuleInstanceService
        from prodavan.core.jobs.enqueue import enqueue_index_equipment_catalog

        table_slug = params.get("table_slug")
        if not isinstance(table_slug, str) or not table_slug:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="content.index_opensearch requires params.table_slug",
            )
        if not row_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="row_id required for content.index_opensearch",
            )

        rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body = dict(target.get("body") or {})
        instance_id = str(target.get("instance_id") or "").strip()
        if not instance_id:
            inst_svc = ModuleInstanceService(self._session)
            if owner_kind and owner_id:
                from prodavan.application.modules.owner_module_data_service import (
                    OwnerModuleDataService,
                )

                # Resolve SoT the same way owner data CRUD does.
                owner_rows = await OwnerModuleDataService(self._session).list_data_rows(
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    module_id=module_id,
                    table_slug=table_slug,
                )
                hit = next((r for r in owner_rows if str(r.get("row_id")) == row_id), None)
                instance_id = str((hit or {}).get("instance_id") or "").strip()
                if not instance_id:
                    raise AppError(
                        code="VALIDATION_ERROR",
                        title="Validation Error",
                        status=422,
                        detail="module instance missing for OpenSearch index",
                    )
            elif project_id:
                inst = await inst_svc.ensure_project_instance(
                    project_id=project_id, module_id=module_id
                )
                instance_id = str(inst.id)
            else:
                inst = await inst_svc.ensure_cabinet_instance(
                    cabinet_id=cabinet_id, module_id=module_id
                )
                instance_id = str(inst.id)

        status_col = str(params.get("status_column") or "status")
        error_col = str(params.get("error_column") or "error")
        columns_col = str(params.get("columns_json_column") or "columns_json")
        file_col = str(params.get("file_column") or params.get("source_column") or "source_file")
        kind_col = str(params.get("source_kind_column") or "source_kind")
        source_kind = str(body.get(kind_col) or "local").strip().lower()

        company_id, os_cabinet_id, os_project_id = await resolve_equipment_catalog_tenancy(
            self._session, instance_id=instance_id
        )
        if not company_id:
            # Legacy cabinet path when instance tenancy is incomplete.
            cab = await self._session.get(CabinetInstanceRow, cabinet_id) if cabinet_id else None
            if cab is not None and cab.company_id:
                company_id = str(cab.company_id)
                os_cabinet_id = str(cab.id)
                os_project_id = project_id
        if not company_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="no company tenancy for OpenSearch index",
            )
        # Prefer SoT tenancy; fall back to caller scope for cabinet/project leaves.
        index_cabinet_id = os_cabinet_id if os_cabinet_id is not None else (cabinet_id or None)
        index_project_id = os_project_id if os_project_id is not None else project_id
        if index_cabinet_id == "":
            index_cabinet_id = None

        # Local: light header probe (sync) for column_map UI.
        if source_kind == "local" and isinstance(body.get(file_col), dict):
            try:
                cols = await extract_local_columns(body)
                if cols:
                    body[columns_col] = json.dumps(cols, ensure_ascii=False)
                    await self._update_row_for_scope(
                        cabinet_id=cabinet_id,
                        project_id=project_id,
                        module_id=module_id,
                        table_slug=table_slug,
                        row_id=row_id,
                        body=body,
                        principal=principal,
                        employee=employee,
                        owner_kind=owner_kind,
                        owner_id=owner_id,
                        run_actions=False,
                    )
            except Exception as exc:
                body[status_col] = "error"
                body[error_col] = str(exc)[:500]
                await self._update_row_for_scope(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    body=body,
                    principal=principal,
                    employee=employee,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    run_actions=False,
                )
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"index_opensearch header probe failed: {exc}",
                ) from exc

        if not column_map_ready(body):
            if str(body.get(status_col) or "") != "error":
                body[status_col] = "draft"
                body[error_col] = None
                await self._update_row_for_scope(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    body=body,
                    principal=principal,
                    employee=employee,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    run_actions=False,
                )
            return {
                "kind": "content.index_opensearch",
                "status": "draft",
                "row_id": row_id,
                "reason": "column_map incomplete",
            }

        body[status_col] = "indexing"
        body[error_col] = None
        await self._update_row_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
            run_actions=False,
        )

        enq = enqueue_index_equipment_catalog(
            instance_id=instance_id,
            row_id=row_id,
            company_id=company_id,
            cabinet_id=index_cabinet_id,
            project_id=index_project_id,
        )
        if enq.get("inline"):
            result = await run_index_equipment_catalog(
                self._session,
                instance_id=instance_id,
                row_id=row_id,
                company_id=company_id,
                cabinet_id=index_cabinet_id,
                project_id=index_project_id,
            )
            return {
                "kind": "content.index_opensearch",
                "status": "ready" if result.get("ok") else "error",
                "row_id": row_id,
                "inline": True,
                **result,
            }
        return {
            "kind": "content.index_opensearch",
            "status": "indexing",
            "row_id": row_id,
            "enqueued": bool(enq.get("enqueued")),
            "task_id": enq.get("task_id"),
        }

    async def _documents_convert(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.documents.service import DocumentsService

        table_slug = params.get("table_slug")
        if not isinstance(table_slug, str) or not table_slug:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="documents.convert requires params.table_slug",
            )
        file_col = str(params.get("file_column") or "").strip()
        if not file_col:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="documents.convert requires params.file_column",
            )
        target_format = str(params.get("target_format") or "").strip().lower()
        if not target_format:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="documents.convert requires params.target_format",
            )
        output_column = str(params.get("output_column") or "").strip()
        if not output_column:
            output_column = f"{file_col}_pdf" if target_format == "pdf" else f"{file_col}_converted"
        if not row_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="row_id required for documents.convert",
            )

        rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
        )
        target_row = next((r for r in rows if str(r.get("row_id")) == row_id), None)
        if target_row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body = dict(target_row.get("body") or {})
        file_ref = body.get(file_col)
        if not isinstance(file_ref, dict):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"row {file_col!r} has no file_ref to convert",
            )
        filename = str(file_ref.get("filename") or "").strip()
        if not filename:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"row {file_col!r} file_ref has no filename",
            )
        company_id = await self._resolve_documents_company_id(
            cabinet_id=cabinet_id, project_id=project_id
        )
        if not company_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="no company tenancy for documents action",
            )

        service = DocumentsService(self._session)
        result_ref = await service.convert(
            file_ref,
            filename=filename,
            target_format=target_format,
            company_id=company_id,
            cabinet_id=cabinet_id or None,
            project_id=project_id,
            principal=principal,
            employee=employee,
            session=self._session,
        )
        body[output_column] = result_ref
        await self._update_row_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=principal,
            employee=employee,
            run_actions=False,
        )
        return {"kind": "documents.convert", "row_id": row_id, "file_ref": result_ref}

    async def _documents_fill_template(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.documents.service import DocumentsService

        table_slug = params.get("table_slug")
        if not isinstance(table_slug, str) or not table_slug:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="documents.fill_template requires params.table_slug",
            )
        template_col = str(params.get("template_column") or "").strip()
        template_file = params.get("template_file")
        if not template_col and not isinstance(template_file, dict):
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="documents.fill_template requires params.template_column or params.template_file",
            )
        data = params.get("data") if isinstance(params.get("data"), dict) else {}
        output_column = str(params.get("output_column") or "").strip()
        if not output_column:
            output_column = f"{template_col}_filled" if template_col else "filled_file"
        output_format = params.get("output_format")
        output_format = str(output_format).strip().lower() if output_format else None
        if not row_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="row_id required for documents.fill_template",
            )

        rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
        )
        target_row = next((r for r in rows if str(r.get("row_id")) == row_id), None)
        if target_row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body = dict(target_row.get("body") or {})
        if template_col:
            template_ref = body.get(template_col)
            if not isinstance(template_ref, dict):
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"row {template_col!r} has no template file_ref",
                )
        else:
            template_ref = dict(template_file)  # type: ignore[arg-type]
        filename = str(template_ref.get("filename") or "").strip()
        if not filename:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="template file_ref has no filename",
            )
        company_id = await self._resolve_documents_company_id(
            cabinet_id=cabinet_id, project_id=project_id
        )
        if not company_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="no company tenancy for documents action",
            )

        # docx context values may name row columns - resolve them from the row
        # body so the mapping can be built "from row fields" (see docs).
        context = data.get("context")
        if isinstance(context, dict):
            resolved = {
                key: (body.get(value) if isinstance(value, str) and value in body else value)
                for key, value in context.items()
            }
            data = {**data, "context": resolved}

        service = DocumentsService(self._session)
        result_ref = await service.fill_template(
            template_ref,
            filename=filename,
            data=data,
            output_format=output_format,
            company_id=company_id,
            cabinet_id=cabinet_id or None,
            project_id=project_id,
            principal=principal,
            employee=employee,
            session=self._session,
        )
        body[output_column] = result_ref
        await self._update_row_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=principal,
            employee=employee,
            run_actions=False,
        )
        return {"kind": "documents.fill_template", "row_id": row_id, "file_ref": result_ref}

    async def _resolve_documents_company_id(
        self,
        *,
        cabinet_id: str,
        project_id: str | None,
    ) -> str | None:
        """Company tenancy for documents actions: project row, else cabinet row."""
        if project_id:
            from prodavan.infrastructure.persistence.models.projects import ProjectRow

            project = await self._session.get(ProjectRow, project_id)
            if project is not None and project.company_id:
                return str(project.company_id)
        if cabinet_id:
            cab = await self._session.get(CabinetInstanceRow, cabinet_id)
            if cab is not None and cab.company_id:
                return str(cab.company_id)
        return None

    def _remote_sql_row_auth(
        self,
        *,
        body: dict[str, Any],
        params: dict[str, Any],
        dsn: str,
        cabinet_id: str = "",
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> tuple[str, str, str, str]:
        """Return (remote_database, remote_table, remote_user, remote_password)."""
        from prodavan.application.content.remote_sql_probe import (
            normalize_remote_db_and_table,
            postgres_dsn_has_password,
            postgres_dsn_has_user,
        )

        db_col = str(params.get("remote_database_column") or "remote_database")
        table_col = str(params.get("remote_table_column") or "remote_table")
        user_col = str(params.get("remote_user_column") or "remote_user")
        password_col = str(params.get("remote_password_column") or "remote_password")
        remote_database = str(body.get(db_col) or "").strip()
        remote_table = str(body.get(table_col) or "").strip()
        remote_database, remote_table = normalize_remote_db_and_table(
            dsn=dsn,
            remote_database_field=remote_database,
            remote_table_field=remote_table,
        )
        remote_user = ""
        if not postgres_dsn_has_user(dsn):
            remote_user = str(body.get(user_col) or "").strip()
        remote_password = ""
        if not postgres_dsn_has_password(dsn):
            pwd_ref = field_value_as_secret_ref(body.get(password_col))
            if pwd_ref:
                assert_module_secret_ref_scope(
                    pwd_ref,
                    cabinet_id=cabinet_id or None,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                )
                remote_password = get_secret_store().get(pwd_ref)
            else:
                # Plain text fallback (should not persist; UI uses secret_ref).
                raw = body.get(password_col)
                if isinstance(raw, str):
                    remote_password = raw.strip()
        return remote_database, remote_table, remote_user, remote_password

    async def _probe_remote_sql(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.content.remote_sql_probe import (
            check_remote_postgres_connect,
            is_simple_database_name,
            postgres_dsn_database_name,
            postgres_dsn_has_password,
            postgres_dsn_has_user,
            postgres_dsn_table_query,
            probe_remote_postgres,
        )

        table_slug = params.get("table_slug")
        if not isinstance(table_slug, str) or not table_slug:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="content.probe_remote_sql requires params.table_slug",
            )
        if not row_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="row_id required for content.probe_remote_sql",
            )

        rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body = dict(target.get("body") or {})
        kind_col = str(params.get("source_kind_column") or "source_kind")
        expected_kind = str(params.get("expected_source_kind") or "remote").strip().lower()
        actual_kind = str(body.get(kind_col) or "local").strip().lower()
        if actual_kind != expected_kind:
            return {
                "kind": "content.probe_remote_sql",
                "status": "skipped",
                "reason": f"source_kind={actual_kind}",
                "row_id": row_id,
            }

        status_col = str(params.get("status_column") or "status")
        error_col = str(params.get("error_column") or "error")
        row_count_col = str(params.get("row_count_column") or "row_count")
        columns_col = str(params.get("columns_json_column") or "columns_json")
        dsn_col = str(params.get("dsn_column") or "remote_dsn")
        table_col = str(params.get("remote_table_column") or "remote_table")
        db_col = str(params.get("remote_database_column") or "remote_database")

        secret_ref = field_value_as_secret_ref(body.get(dsn_col))
        if not secret_ref:
            body[status_col] = "draft"
            body[error_col] = None
            body["remote_dsn_reachable"] = False
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )
            return {"kind": "content.probe_remote_sql", "status": "draft", "row_id": row_id}

        # Remote catalogs are live — drop local file artifacts.
        body["source_file"] = None
        body["artifact_ref"] = None
        body.pop("indexed_source_key", None)

        remote_database = str(body.get(db_col) or "").strip()
        try:
            assert_module_secret_ref_scope(
                secret_ref,
                cabinet_id=cabinet_id or None,
                owner_kind=owner_kind,
                owner_id=owner_id,
            )
            dsn = get_secret_store().get(secret_ref)
            remote_database, remote_table, remote_user, remote_password = self._remote_sql_row_auth(
                body=body,
                params=params,
                dsn=dsn,
                cabinet_id=cabinet_id,
                owner_kind=owner_kind,
                owner_id=owner_id,
            )
            body[db_col] = remote_database or None
            if remote_table:
                body[table_col] = remote_table
            body["remote_dsn_has_database"] = bool(
                postgres_dsn_database_name(dsn) or is_simple_database_name(remote_database)
            )
            body["remote_dsn_url_has_database"] = bool(postgres_dsn_database_name(dsn))
            body["remote_dsn_has_user"] = postgres_dsn_has_user(dsn) or bool(remote_user)
            body["remote_dsn_has_password"] = postgres_dsn_has_password(dsn) or bool(
                field_value_as_secret_ref(body.get(str(params.get("remote_password_column") or "remote_password")))
            )
            q_table = postgres_dsn_table_query(dsn)
            if q_table and not remote_table:
                remote_table = q_table
                body[table_col] = remote_table

            if not remote_database:
                await check_remote_postgres_connect(
                    dsn=dsn,
                    remote_database_field="",
                    remote_user_field=remote_user,
                    remote_password_field=remote_password,
                    allow_missing_database=True,
                )
                body["remote_auth_failed"] = False
                body["remote_dsn_reachable"] = True
                body[status_col] = "draft"
                body[error_col] = None
                body[row_count_col] = 0
                body[columns_col] = None
                body.pop("probed_remote_key", None)
                await self._update_row_for_scope(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    body=body,
                    principal=principal,
                    employee=employee,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    run_actions=False,
                )
                return {
                    "kind": "content.probe_remote_sql",
                    "status": "draft",
                    "needs_database": True,
                    "row_id": row_id,
                }

            if not remote_table:
                await check_remote_postgres_connect(
                    dsn=dsn,
                    remote_database_field=remote_database,
                    remote_user_field=remote_user,
                    remote_password_field=remote_password,
                )
                body["remote_auth_failed"] = False
                body["remote_dsn_reachable"] = True
                body[status_col] = "draft"
                body[error_col] = None
                body[row_count_col] = 0
                body[columns_col] = None
                body.pop("probed_remote_key", None)
                await self._update_row_for_scope(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    body=body,
                    principal=principal,
                    employee=employee,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    run_actions=False,
                )
                return {
                    "kind": "content.probe_remote_sql",
                    "status": "draft",
                    "needs_table": True,
                    "row_id": row_id,
                }

            body[status_col] = "draft"
            body[error_col] = None
            body["remote_dsn_reachable"] = True
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )

            probed = await probe_remote_postgres(
                dsn=dsn,
                remote_table=remote_table,
                remote_database_field=remote_database,
                remote_user_field=remote_user,
                remote_password_field=remote_password,
            )
            body[columns_col] = json.dumps(probed.columns, ensure_ascii=False)
            body[row_count_col] = probed.row_count
            # Schema probe only — OpenSearch index status stays draft until Celery finishes.
            body[status_col] = "draft"
            body[error_col] = None
            body["remote_auth_failed"] = False
            body["remote_dsn_reachable"] = True
            body[table_col] = f"{probed.schema}.{probed.table}"
            body["probed_remote_key"] = (
                f"{secret_ref}|{body[table_col]}|{body.get('remote_dsn_has_database')}|"
                f"{remote_database}"
            )
        except AppError as exc:
            if exc.code == "REMOTE_AUTH_FAILED":
                body["remote_auth_failed"] = True
                body[status_col] = "draft"
                body[error_col] = None
                # Host is reachable; wrong creds for selected DB keep pickers visible.
                body["remote_dsn_reachable"] = bool(remote_database)
            else:
                body[status_col] = "error"
                body[error_col] = str(exc.detail or exc)[:500]
                body["remote_dsn_reachable"] = False
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )
            raise
        except Exception as exc:
            body[status_col] = "error"
            body[error_col] = str(exc)[:500]
            body["remote_dsn_reachable"] = False
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"probe_remote_sql failed: {exc}",
            ) from exc

        await self._update_row_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
            run_actions=False,
        )
        return {
            "kind": "content.probe_remote_sql",
            "status": "draft",
            "row_id": row_id,
            "row_count": body.get(row_count_col),
            "columns": probed.columns,
        }

    async def _list_remote_sql_databases(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.content.remote_sql_probe import list_remote_databases

        table_slug = params.get("table_slug")
        if not isinstance(table_slug, str) or not table_slug:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="content.list_remote_sql_databases requires params.table_slug",
            )
        if not row_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="row_id required for content.list_remote_sql_databases",
            )

        rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body = dict(target.get("body") or {})
        dsn_col = str(params.get("dsn_column") or "remote_dsn")
        secret_ref = field_value_as_secret_ref(body.get(dsn_col))
        if not secret_ref:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="remote_dsn is required",
            )
        assert_module_secret_ref_scope(
            secret_ref,
            cabinet_id=cabinet_id or None,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        dsn = get_secret_store().get(secret_ref)
        _db, _table, remote_user, remote_password = self._remote_sql_row_auth(
            body=body,
            params=params,
            dsn=dsn,
            cabinet_id=cabinet_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        try:
            databases = await list_remote_databases(
                dsn=dsn,
                remote_user_field=remote_user,
                remote_password_field=remote_password,
            )
        except AppError as exc:
            if exc.code == "REMOTE_AUTH_FAILED":
                body["remote_auth_failed"] = True
                body["status"] = "draft"
                body["error"] = None
                await self._update_row_for_scope(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    body=body,
                    principal=principal,
                    employee=employee,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    run_actions=False,
                )
            raise
        if body.get("remote_auth_failed"):
            body["remote_auth_failed"] = False
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )
        return {
            "kind": "content.list_remote_sql_databases",
            "row_id": row_id,
            "databases": [{"name": d.name} for d in databases],
        }

    async def _list_remote_sql_tables(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        params: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.content.remote_sql_probe import list_remote_tables

        table_slug = params.get("table_slug")
        if not isinstance(table_slug, str) or not table_slug:
            raise AppError(
                code="META_VALIDATION",
                title="Meta validation error",
                status=422,
                detail="content.list_remote_sql_tables requires params.table_slug",
            )
        if not row_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="row_id required for content.list_remote_sql_tables",
            )

        rows = await self._list_rows_for_scope(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        target = next((r for r in rows if str(r.get("row_id")) == row_id), None)
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body = dict(target.get("body") or {})
        dsn_col = str(params.get("dsn_column") or "remote_dsn")
        db_col = str(params.get("remote_database_column") or "remote_database")
        table_col = str(params.get("remote_table_column") or "remote_table")
        secret_ref = field_value_as_secret_ref(body.get(dsn_col))
        if not secret_ref:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="remote_dsn is required",
            )
        assert_module_secret_ref_scope(
            secret_ref,
            cabinet_id=cabinet_id or None,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        dsn = get_secret_store().get(secret_ref)
        remote_database, remote_table, remote_user, remote_password = self._remote_sql_row_auth(
            body=body,
            params=params,
            dsn=dsn,
            cabinet_id=cabinet_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        if body.get(db_col) != (remote_database or None) or (
            remote_table and body.get(table_col) != remote_table
        ):
            body[db_col] = remote_database or None
            if remote_table:
                body[table_col] = remote_table
            body["remote_dsn_has_database"] = bool(remote_database)
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )
        if not remote_database:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=(
                    "database name is required — pick a database first "
                    "(schema.table belongs in Table)"
                ),
            )
        try:
            tables = await list_remote_tables(
                dsn=dsn,
                remote_database_field=remote_database,
                remote_user_field=remote_user,
                remote_password_field=remote_password,
            )
        except AppError as exc:
            if exc.code == "REMOTE_AUTH_FAILED":
                body["remote_auth_failed"] = True
                body["status"] = "draft"
                body["error"] = None
                await self._update_row_for_scope(
                    cabinet_id=cabinet_id,
                    project_id=project_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    body=body,
                    principal=principal,
                    employee=employee,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    run_actions=False,
                )
            raise
        if body.get("remote_auth_failed"):
            body["remote_auth_failed"] = False
            await self._update_row_for_scope(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=principal,
                employee=employee,
                owner_kind=owner_kind,
                owner_id=owner_id,
                run_actions=False,
            )
        return {
            "kind": "content.list_remote_sql_tables",
            "row_id": row_id,
            "tables": [
                {
                    "name": t.name,
                    "schema": t.schema,
                    "table": t.table,
                    "row_count": t.row_count,
                }
                for t in tables
            ],
        }

    async def _list_actions(self, *, module_id: str) -> list[dict[str, Any]]:
        """List actions for a module from DB meta, falling back to seeds (META-P1b).

        Single SoT: the UI picker and the executor both go through this method,
        so a fresh install / unseeded module sees the same actions in both
        places — no two contracts. ``upsert_product_modules`` mirrors seeds
        into DB meta on migration; the seed fallback only fires when DB meta
        is empty or absent.
        """
        q = await self._session.execute(
            select(ModuleMetaDocumentRow.body).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == "actions",
            )
        )
        body = q.scalar_one_or_none()
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        # DB meta empty/absent — fall back to product seeds so UI picker and
        # executor agree on the action list before the next Alembic mirror.
        return _product_seed_actions(module_id)

    async def _load_action(self, *, module_id: str, action_id: str) -> dict[str, Any]:
        for item in await self._list_actions(module_id=module_id):
            if str(item.get("id")) == action_id:
                return item
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="action not found")
