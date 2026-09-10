"""Execute declarative module actions from meta slug `actions`."""

from __future__ import annotations

import json
import logging
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
from prodavan.infrastructure.secrets.cabinet_secret_store import assert_cabinet_secret_scope
from prodavan.infrastructure.secrets.store import get_secret_store

logger = logging.getLogger(__name__)


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
            )

        if kind == "content.index_tabular":
            return await self._index_tabular(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
            )

        if kind == "content.probe_remote_sql":
            return await self._probe_remote_sql(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
            )

        if kind == "content.list_remote_sql_tables":
            return await self._list_remote_sql_tables(
                cabinet_id=cabinet_id,
                module_id=module_id,
                params=params,
                row_id=row_id,
                principal=principal,
                employee=employee,
            )

        raise AppError(
            code="NOT_IMPLEMENTED",
            title="Not Implemented",
            status=501,
            detail=f"action kind not supported: {kind}",
        )

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
        )

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
            rows = await self._list_module_rows(
                cabinet_id=cabinet_id,
                project_id=project_id,
                module_id=module_id,
                table_slug=table_slug,
                principal=principal,
                employee=employee,
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
            # remote_table may be empty when DSN already has /dbname (default SQL table).
            status = str(body.get(status_col) or "")
            if status == "indexing":
                continue
            probe_key = (
                f"{field_value_as_secret_ref(body.get(dsn_col))}|"
                f"{str(body.get(table_col) or '').strip()}|"
                f"{body.get('remote_dsn_has_database')}"
            )
            if status == "ready" and probe_key == str(body.get("probed_remote_key") or ""):
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

    async def _list_module_rows(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str | None = None,
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
            )
        return await self._modules.list_data_rows(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
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
                        )
                        break

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

        rows = await self._list_module_rows(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
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
            await self._update_module_row(
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
            return {"kind": "content.index_tabular", "status": "draft", "row_id": row_id}

        body[status_col] = "indexing"
        body[error_col] = None
        await self._update_module_row(
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
            await self._update_module_row(
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
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"index_tabular failed: {exc}",
            ) from exc

        await self._update_module_row(
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
        return {
            "kind": "content.index_tabular",
            "status": "ready",
            "row_id": row_id,
            "row_count": body.get(row_count_col),
            "columns": indexed_columns,
        }

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
    ) -> dict[str, Any]:
        from prodavan.application.content.remote_sql_probe import (
            check_remote_postgres_connect,
            is_simple_database_name,
            normalize_remote_db_and_table,
            postgres_dsn_database_name,
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

        rows = await self._list_module_rows(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
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
        remote_table = str(body.get(table_col) or "").strip()
        remote_database = str(body.get(db_col) or "").strip()
        if not secret_ref:
            body[status_col] = "draft"
            body[error_col] = None
            await self._update_module_row(
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
            return {"kind": "content.probe_remote_sql", "status": "draft", "row_id": row_id}

        # Remote catalogs are live — drop local file artifacts.
        body["source_file"] = None
        body["artifact_ref"] = None
        body.pop("indexed_source_key", None)

        try:
            if secret_ref.startswith(("file://cabinet_secrets/", "vault://cabinet_secrets/")):
                assert_cabinet_secret_scope(secret_ref, cabinet_id)
            dsn = get_secret_store().get(secret_ref)
            # Mis-filed schema.table in «Имя БД» / URL path → remote_table.
            remote_database, remote_table = normalize_remote_db_and_table(
                dsn=dsn,
                remote_database_field=remote_database,
                remote_table_field=remote_table,
            )
            body[db_col] = remote_database or None
            if remote_table:
                body[table_col] = remote_table
            body["remote_dsn_has_database"] = bool(
                postgres_dsn_database_name(dsn) or is_simple_database_name(remote_database)
            )
            # Convenience: persist ?table= from DSN into body, then strip on connect.
            q_table = postgres_dsn_table_query(dsn)
            if q_table and not remote_table:
                remote_table = q_table
                body[table_col] = remote_table

            if not remote_table:
                await check_remote_postgres_connect(
                    dsn=dsn,
                    remote_database_field=remote_database,
                )
                body[status_col] = "draft"
                body[error_col] = None
                body[row_count_col] = 0
                body[columns_col] = None
                body.pop("probed_remote_key", None)
                await self._update_module_row(
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
                return {
                    "kind": "content.probe_remote_sql",
                    "status": "draft",
                    "needs_table": True,
                    "row_id": row_id,
                }

            body[status_col] = "indexing"
            body[error_col] = None
            await self._update_module_row(
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

            probed = await probe_remote_postgres(
                dsn=dsn,
                remote_table=remote_table,
                remote_database_field=remote_database,
            )
            body[columns_col] = json.dumps(probed.columns, ensure_ascii=False)
            body[row_count_col] = probed.row_count
            body[status_col] = "ready"
            body[error_col] = None
            body[table_col] = f"{probed.schema}.{probed.table}"
            body["probed_remote_key"] = (
                f"{secret_ref}|{body[table_col]}|{body.get('remote_dsn_has_database')}"
            )
        except AppError as exc:
            body[status_col] = "error"
            body[error_col] = str(exc.detail or exc)[:500]
            await self._update_module_row(
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
            raise
        except Exception as exc:
            body[status_col] = "error"
            body[error_col] = str(exc)[:500]
            await self._update_module_row(
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
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"probe_remote_sql failed: {exc}",
            ) from exc

        await self._update_module_row(
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
        return {
            "kind": "content.probe_remote_sql",
            "status": "ready",
            "row_id": row_id,
            "row_count": body.get(row_count_col),
            "columns": probed.columns,
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
    ) -> dict[str, Any]:
        from prodavan.application.content.remote_sql_probe import (
            list_remote_tables,
            normalize_remote_db_and_table,
        )

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

        rows = await self._list_module_rows(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
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
        if secret_ref.startswith(("file://cabinet_secrets/", "vault://cabinet_secrets/")):
            assert_cabinet_secret_scope(secret_ref, cabinet_id)
        dsn = get_secret_store().get(secret_ref)
        remote_database = str(body.get(db_col) or "").strip()
        remote_table = str(body.get(table_col) or "").strip()
        remote_database, remote_table = normalize_remote_db_and_table(
            dsn=dsn,
            remote_database_field=remote_database,
            remote_table_field=remote_table,
        )
        # Persist coerce so «Имя БД» is not left holding schema.table.
        if body.get(db_col) != (remote_database or None) or (
            remote_table and body.get(table_col) != remote_table
        ):
            body[db_col] = remote_database or None
            if remote_table:
                body[table_col] = remote_table
            body["remote_dsn_has_database"] = bool(remote_database)
            await self._update_module_row(
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
        if not remote_database:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=(
                    "database name is required (e.g. s4b_catalog in the URL path "
                    "or the Database name field) — schema.table belongs in Table"
                ),
            )
        tables = await list_remote_tables(dsn=dsn, remote_database_field=remote_database)
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
        q = await self._session.execute(
            select(ModuleMetaDocumentRow.body).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == "actions",
            )
        )
        body = q.scalar_one_or_none()
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, dict)]

    async def _load_action(self, *, module_id: str, action_id: str) -> dict[str, Any]:
        for item in await self._list_actions(module_id=module_id):
            if str(item.get("id")) == action_id:
                return item
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="action not found")
