"""Resolve materialize rules from bound module meta + cabinet data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.modules import ModuleMetaDocumentRow

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True, slots=True)
class MaterializeOp:
    rule_id: str
    module_id: str
    workspace_path: str
    format: str
    source_type: str
    priority: int = 100
    row_body: dict[str, Any] | None = None
    field: str | None = None
    static_value: str | None = None
    file_ref: dict[str, Any] | None = None
    mcp_package_name: str | None = None
    rows_bodies: list[dict[str, Any]] | None = None
    template_text: str | None = None


class MaterializePlanner:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._bindings = ModuleBindingService(session)
        self._meta = ModuleMetaDocumentService(session)

    async def plan_for_project(
        self,
        *,
        cabinet_id: str,
        project_id: str,
        when: str = "project.created",
        enabled_module_ids: list[str] | None = None,
    ) -> tuple[list[MaterializeOp], str | None]:
        inst = await self._session.get(CabinetInstanceRow, cabinet_id)
        if inst is None:
            return [], None
        active_profile_id = await self._resolve_active_profile_id(
            schema_name=inst.schema_name,
            project_id=project_id,
        )
        module_ids = enabled_module_ids
        if module_ids is None:
            module_ids = await self._bindings.list_module_ids_for_cabinet(cabinet_id)
        ops: list[MaterializeOp] = []
        for module_id in module_ids:
            bound_projects = await self._bindings.list_project_ids(module_id)
            if bound_projects and project_id not in bound_projects:
                continue
            rules = await self._load_materialize_rules(module_id)
            rules = _merge_materialize_rules(rules, await self._auto_rules_from_columns(module_id))
            for rule in rules:
                if not rule.get("enabled", True):
                    continue
                when_list = rule.get("when") or []
                if when_list and when not in when_list:
                    continue
                source = rule.get("source") or {}
                target = rule.get("target") or {}
                fmt = target.get("format") or "raw"
                source_type = source.get("type") or "row"
                priority = int(rule.get("priority") or 100)
                rule_id = str(rule.get("id") or f"{module_id}_{len(ops)}")
                if source_type == "row":
                    op = await self._plan_row_op(
                        inst=inst,
                        module_id=module_id,
                        rule_id=rule_id,
                        source=source,
                        target=target,
                        fmt=fmt,
                        active_profile_id=active_profile_id,
                        project_id=project_id,
                        priority=priority,
                    )
                    if op:
                        ops.append(op)
                elif source_type == "rows":
                    row_ops = await self._plan_rows_ops(
                        inst=inst,
                        module_id=module_id,
                        rule_id=rule_id,
                        source=source,
                        target=target,
                        fmt=fmt,
                        active_profile_id=active_profile_id,
                        project_id=project_id,
                        priority=priority,
                    )
                    ops.extend(row_ops)
                elif source_type == "static":
                    ctx = {"active_profile_id": active_profile_id}
                    ws_path = self._substitute(str(target.get("workspace_path") or ""), ctx)
                    if ws_path:
                        ops.append(
                            MaterializeOp(
                                rule_id=rule_id,
                                module_id=module_id,
                                workspace_path=ws_path,
                                format=fmt,
                                source_type="static",
                                priority=priority,
                                static_value=str(source.get("value") or ""),
                                template_text=str(target["template"])
                                if isinstance(target.get("template"), str)
                                else None,
                            )
                        )
                elif source_type == "meta_document":
                    op = await self._plan_meta_document_op(
                        module_id=module_id,
                        rule_id=rule_id,
                        source=source,
                        target=target,
                        fmt=fmt,
                        active_profile_id=active_profile_id,
                        priority=priority,
                    )
                    if op:
                        ops.append(op)
        ops.sort(key=lambda o: (o.priority, o.rule_id))
        return ops, active_profile_id

    async def load_workspace_roots(self, module_id: str) -> list[str]:
        q = await self._session.execute(
            select(ModuleMetaDocumentRow.body).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == "materialize_roots",
            )
        )
        body = q.scalar_one_or_none()
        if isinstance(body, dict):
            roots = body.get("workspace_roots")
            if isinstance(roots, list):
                return [str(r) for r in roots if r]
        return []

    async def plan_paths_for_module(
        self,
        *,
        cabinet_id: str,
        project_id: str,
        module_id: str,
        when: str = "project.sync",
    ) -> list[str]:
        """All workspace paths this module would write (for prune of disabled modules)."""
        ops, _ = await self.plan_for_project(
            cabinet_id=cabinet_id,
            project_id=project_id,
            when=when,
            enabled_module_ids=[module_id],
        )
        return [op.workspace_path for op in ops if op.workspace_path]

    async def _load_materialize_rules(self, module_id: str) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(ModuleMetaDocumentRow.body).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == "materialize",
            )
        )
        body = q.scalar_one_or_none()
        if isinstance(body, list):
            return [r for r in body if isinstance(r, dict)]
        return []

    async def _load_columns(self, module_id: str) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(ModuleMetaDocumentRow.body).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == "columns",
            )
        )
        body = q.scalar_one_or_none()
        if isinstance(body, list):
            return [c for c in body if isinstance(c, dict)]
        return []

    async def _auto_rules_from_columns(self, module_id: str) -> list[dict[str, Any]]:
        """P-META-FILE-04: derive copy_blob rules from column file.materialize."""
        rules: list[dict[str, Any]] = []
        for col in await self._load_columns(module_id):
            if col.get("type") != "file_ref":
                continue
            file_block = col.get("file")
            if not isinstance(file_block, dict):
                continue
            mat = file_block.get("materialize")
            if not isinstance(mat, dict) or not mat.get("enabled"):
                continue
            target_tpl = mat.get("target_template")
            if not isinstance(target_tpl, str) or not target_tpl.strip():
                continue
            table_slug = col.get("table_slug")
            name = col.get("name")
            if not isinstance(table_slug, str) or not table_slug:
                continue
            if not isinstance(name, str) or not name:
                continue
            when = mat.get("when")
            rules.append(
                {
                    "id": f"auto_{table_slug}_{name}",
                    "enabled": True,
                    "when": when if isinstance(when, list) and when else ["project.created", "project.resumed"],
                    "priority": int(mat.get("priority") or 200),
                    "source": {
                        "type": "rows",
                        "table_slug": table_slug,
                        "field": name,
                    },
                    "target": {
                        "workspace_path": target_tpl.strip(),
                        "format": "copy_blob",
                    },
                }
            )
        return rules

    async def _resolve_active_profile_id(
        self,
        *,
        schema_name: str,
        project_id: str,
    ) -> str | None:
        qschema = qident(schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT row_id, body
                FROM {qschema}.module_data_rows
                WHERE module_id = 'mod_prompts'
                  AND table_slug = 'prompt_profiles'
                ORDER BY updated_at
                """
            )
        )
        matches: list[tuple[str, bool]] = []
        for row in q.fetchall():
            body = row.body if isinstance(row.body, dict) else {}
            if not _row_applies_to_project(body, project_id):
                continue
            matches.append((str(row.row_id), bool(body.get("is_default"))))
        if not matches:
            return None
        defaults = [rid for rid, is_def in matches if is_def]
        if defaults:
            return defaults[0]
        return matches[0][0]

    async def _fetch_row(
        self,
        *,
        schema_name: str,
        module_id: str,
        table_slug: str,
        row_id: str,
    ) -> dict[str, Any] | None:
        qschema = qident(schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT body FROM {qschema}.module_data_rows
                WHERE module_id = :module_id AND table_slug = :table_slug AND row_id = :row_id
                """
            ),
            {"module_id": module_id, "table_slug": table_slug, "row_id": row_id},
        )
        row = q.fetchone()
        if row is None:
            return None
        body = row.body
        return body if isinstance(body, dict) else {}

    async def _fetch_rows(
        self,
        *,
        schema_name: str,
        module_id: str,
        table_slug: str,
        filt: dict[str, Any] | None,
        project_id: str,
    ) -> list[dict[str, Any]]:
        qschema = qident(schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT row_id, body FROM {qschema}.module_data_rows
                WHERE module_id = :module_id AND table_slug = :table_slug
                ORDER BY updated_at
                """
            ),
            {"module_id": module_id, "table_slug": table_slug},
        )
        out: list[dict[str, Any]] = []
        for r in q.fetchall():
            body = r.body if isinstance(r.body, dict) else {}
            if filt and not _row_matches_filter(body, filt):
                continue
            if not _row_applies_to_project(body, project_id):
                continue
            out.append({"row_id": r.row_id, **body})
        return out

    def _substitute(self, template: str, ctx: dict[str, str | None]) -> str:
        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            val = ctx.get(key)
            return val if val is not None else match.group(0)

        out = _PLACEHOLDER_RE.sub(repl, template)
        out = re.sub(r"\{\{(\w+)\}\}", lambda m: ctx.get(m.group(1), "") or "", out)
        return out

    async def _plan_row_op(
        self,
        *,
        inst: CabinetInstanceRow,
        module_id: str,
        rule_id: str,
        source: dict[str, Any],
        target: dict[str, Any],
        fmt: str,
        active_profile_id: str | None,
        project_id: str,
        priority: int,
    ) -> MaterializeOp | None:
        table_slug = source.get("table_slug") or ""
        row_id_tpl = str(source.get("row_id") or "")
        ctx = {"active_profile_id": active_profile_id}
        row_id = self._substitute(row_id_tpl, ctx)
        body = await self._fetch_row(
            schema_name=inst.schema_name,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
        )
        if body is None:
            return None
        if not _row_applies_to_project(body, project_id):
            return None
        str_body = {k: str(v) for k, v in body.items() if v is not None}
        ws_path = self._substitute(str(target.get("workspace_path") or ""), {**ctx, **str_body})
        field = source.get("field")
        op = MaterializeOp(
            rule_id=rule_id,
            module_id=module_id,
            workspace_path=ws_path,
            format=fmt,
            source_type="row",
            priority=priority,
            row_body=body,
            field=str(field) if field else None,
            template_text=str(target["template"])
            if isinstance(target.get("template"), str)
            else None,
        )
        return op

    async def _plan_rows_ops(
        self,
        *,
        inst: CabinetInstanceRow,
        module_id: str,
        rule_id: str,
        source: dict[str, Any],
        target: dict[str, Any],
        fmt: str,
        active_profile_id: str | None,
        project_id: str,
        priority: int,
    ) -> list[MaterializeOp]:
        table_slug = source.get("table_slug") or ""
        filt_raw = source.get("filter") or {}
        ctx = {"active_profile_id": active_profile_id}
        filt = {
            k: self._substitute(str(v), ctx) if isinstance(v, str) else v
            for k, v in filt_raw.items()
        }
        rows = await self._fetch_rows(
            schema_name=inst.schema_name,
            module_id=module_id,
            table_slug=table_slug,
            filt=filt,
            project_id=project_id,
        )
        field = source.get("field") or target.get("field")
        if fmt == "json_rows":
            ws_path = self._substitute(str(target.get("workspace_path") or ""), ctx)
            if ws_path and rows:
                return [
                    MaterializeOp(
                        rule_id=rule_id,
                        module_id=module_id,
                        workspace_path=ws_path,
                        format="json_rows",
                        source_type="rows",
                        priority=priority,
                        rows_bodies=rows,
                    )
                ]
            return []
        ops: list[MaterializeOp] = []
        for i, body in enumerate(rows):
            str_ctx = _row_path_context(body, field if isinstance(field, str) else None)
            ws_path = self._substitute(str(target.get("workspace_path") or ""), {**ctx, **str_ctx})
            file_ref = body.get(field) if field and fmt in ("copy_blob", "mcp_package") else None
            op = MaterializeOp(
                rule_id=f"{rule_id}_{i}",
                module_id=module_id,
                workspace_path=ws_path,
                format=fmt,
                source_type="rows",
                priority=priority,
                row_body=body,
                field=str(field) if field else None,
                file_ref=file_ref if isinstance(file_ref, dict) else None,
                mcp_package_name=str(body.get("name")) if fmt == "mcp_package" else None,
            )
            ops.append(op)
        return ops

    async def _plan_meta_document_op(
        self,
        *,
        module_id: str,
        rule_id: str,
        source: dict[str, Any],
        target: dict[str, Any],
        fmt: str,
        active_profile_id: str | None,
        priority: int,
    ) -> MaterializeOp | None:
        slug = source.get("slug")
        if not isinstance(slug, str) or not slug:
            return None
        try:
            doc = await self._meta.get_document(module_id=module_id, slug=slug)
        except Exception:
            return None
        body = doc.get("body")
        field = source.get("field")
        value: Any = body
        if field and isinstance(body, dict):
            value = body.get(field)
        ctx = {"active_profile_id": active_profile_id}
        ws_path = self._substitute(str(target.get("workspace_path") or ""), ctx)
        if not ws_path:
            return None
        text = value if isinstance(value, str) else str(value or "")
        return MaterializeOp(
            rule_id=rule_id,
            module_id=module_id,
            workspace_path=ws_path,
            format=fmt,
            source_type="meta_document",
            priority=priority,
            static_value=text if fmt in ("raw", "template") else None,
            row_body={"value": value} if isinstance(value, dict) else {"text": text},
            template_text=str(target["template"])
            if isinstance(target.get("template"), str)
            else None,
        )


def _row_matches_filter(body: dict[str, Any], filt: dict[str, Any]) -> bool:
    for key, expected in filt.items():
        val = body.get(key)
        if isinstance(expected, bool):
            if bool(val) != expected:
                return False
        elif str(val) != str(expected):
            return False
    return True


def _row_applies_to_project(body: dict[str, Any], project_id: str) -> bool:
    pids = body.get("project_ids")
    if pids is None:
        return True
    if not isinstance(pids, list) or len(pids) == 0:
        return True
    return project_id in [str(p) for p in pids]


def _merge_materialize_rules(
    explicit: list[dict[str, Any]],
    auto_generated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    explicit_ids = {
        str(r.get("id"))
        for r in explicit
        if isinstance(r.get("id"), str) and str(r.get("id"))
    }
    merged = list(explicit)
    for rule in auto_generated:
        rid = rule.get("id")
        if isinstance(rid, str) and rid and rid not in explicit_ids:
            merged.append(rule)
    return merged


def _row_path_context(body: dict[str, Any], field: str | None) -> dict[str, str]:
    ctx: dict[str, str] = {}
    row_id = body.get("row_id")
    if row_id is not None:
        ctx["row_id"] = str(row_id)
    for key, val in body.items():
        if val is None or isinstance(val, (dict, list)):
            continue
        ctx[key] = str(val)
    if field:
        ref = body.get(field)
        if isinstance(ref, dict):
            filename = ref.get("filename")
            if isinstance(filename, str) and filename:
                ctx["filename"] = filename
    return ctx
