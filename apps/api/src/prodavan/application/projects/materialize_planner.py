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
    merge_params: dict[str, Any] | None = None


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
        from prodavan.application.modules.module_instance_service import ModuleInstanceService

        inst = await self._session.get(CabinetInstanceRow, cabinet_id)
        if inst is None:
            return [], None
        # Ensure leaf project instances exist before planning (copy-on-bind cascade).
        try:
            await ModuleInstanceService(self._session).ensure_project_instances_for_cabinet_modules(
                project_id=project_id
            )
        except Exception:
            pass
        active_profile_id = await self._resolve_active_profile_id(
            schema_name=inst.schema_name,
            project_id=project_id,
            cabinet_id=cabinet_id,
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
                        cabinet_id=cabinet_id,
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
                        cabinet_id=cabinet_id,
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
        cabinet_id: str | None = None,
    ) -> str | None:
        from prodavan.application.modules.module_instance_service import (
            OWNER_CABINET,
            OWNER_PROJECT,
            ModuleInstanceService,
        )

        instances = ModuleInstanceService(self._session)
        # Cabinet-owned prompts: SoT is cabinet instance.
        if cabinet_id:
            cab = await instances.get_instance(
                owner_kind=OWNER_CABINET, owner_id=cabinet_id, module_id="mod_prompts"
            )
            if cab is not None:
                rows = await instances.list_data_rows(
                    instance_id=cab.id, table_slug="prompt_profiles"
                )
                rid = _pick_active_profile(rows, project_id)
                if rid:
                    return rid

        inst = await instances.get_instance(
            owner_kind=OWNER_PROJECT, owner_id=project_id, module_id="mod_prompts"
        )
        if inst is not None:
            rows = await instances.list_data_rows(
                instance_id=inst.id, table_slug="prompt_profiles"
            )
            rid = _pick_active_profile(rows, project_id)
            if rid:
                return rid

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
        legacy_rows = [
            {"row_id": str(row.row_id), "body": row.body if isinstance(row.body, dict) else {}}
            for row in q.fetchall()
        ]
        return _pick_active_profile(legacy_rows, project_id)

    async def _fetch_row(
        self,
        *,
        schema_name: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        project_id: str | None = None,
        cabinet_id: str | None = None,
    ) -> dict[str, Any] | None:
        from prodavan.application.modules.module_instance_service import (
            OWNER_CABINET,
            OWNER_PROJECT,
            ModuleInstanceService,
        )

        instances = ModuleInstanceService(self._session)
        owner = await self._instance_owner_for_module(module_id)

        if owner == "cabinet" and cabinet_id:
            cab = await instances.get_instance(
                owner_kind=OWNER_CABINET, owner_id=cabinet_id, module_id=module_id
            )
            if cab is not None:
                row = await instances.get_data_row(
                    instance_id=cab.id, table_slug=table_slug, row_id=row_id
                )
                if row is not None:
                    body = row.get("body")
                    return body if isinstance(body, dict) else {}

        if project_id:
            inst = await instances.get_instance(
                owner_kind=OWNER_PROJECT, owner_id=project_id, module_id=module_id
            )
            if inst is not None:
                row = await instances.get_data_row(
                    instance_id=inst.id, table_slug=table_slug, row_id=row_id
                )
                if row is not None:
                    body = row.get("body")
                    return body if isinstance(body, dict) else {}

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
        cabinet_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from prodavan.application.modules.module_instance_service import (
            OWNER_CABINET,
            OWNER_PROJECT,
            ModuleInstanceService,
        )

        instances = ModuleInstanceService(self._session)
        owner = await self._instance_owner_for_module(module_id)

        async def _from_instance(instance_id: str) -> list[dict[str, Any]]:
            rows = await instances.list_data_rows(instance_id=instance_id, table_slug=table_slug)
            out: list[dict[str, Any]] = []
            for r in rows:
                body = r.get("body") if isinstance(r.get("body"), dict) else {}
                if filt and not _row_matches_filter(body, filt):
                    continue
                if not _row_applies_to_project(body, project_id):
                    continue
                out.append({"row_id": r["row_id"], **body})
            return out

        if owner == "cabinet" and cabinet_id:
            cab = await instances.get_instance(
                owner_kind=OWNER_CABINET, owner_id=cabinet_id, module_id=module_id
            )
            if cab is not None:
                return await _from_instance(cab.id)

        inst = await instances.get_instance(
            owner_kind=OWNER_PROJECT, owner_id=project_id, module_id=module_id
        )
        if inst is not None:
            return await _from_instance(inst.id)

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
        out = []
        for r in q.fetchall():
            body = r.body if isinstance(r.body, dict) else {}
            if filt and not _row_matches_filter(body, filt):
                continue
            if not _row_applies_to_project(body, project_id):
                continue
            out.append({"row_id": r.row_id, **body})
        return out

    async def _instance_owner_for_module(self, module_id: str) -> str:
        """Read instance_owner from tabs meta; default project."""
        try:
            doc = await self._meta.get_document(module_id=module_id, slug="tabs")
        except Exception:
            return "project"
        body = doc.get("body") if isinstance(doc, dict) else None
        if isinstance(body, dict) and "items" in body:
            items = body.get("items")
        elif isinstance(body, list):
            items = body
        else:
            items = None
        if not isinstance(items, list):
            return "project"
        for tab in items:
            if not isinstance(tab, dict):
                continue
            raw = tab.get("instance_owner")
            if isinstance(raw, str) and raw in ("cabinet", "project"):
                return raw
            nav = tab.get("nav")
            if isinstance(nav, dict):
                nested = nav.get("instance_owner")
                if isinstance(nested, str) and nested in ("cabinet", "project"):
                    return nested
        return "project"

    def _substitute(self, template: str, ctx: dict[str, str | None]) -> str:
        # {{var}} row-field templates first — {var} would otherwise match the inner
        # `{target_path}` inside `{{target_path}}` and leave stray braces.
        out = re.sub(r"\{\{(\w+)\}\}", lambda m: ctx.get(m.group(1), "") or "", template)

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            val = ctx.get(key)
            return val if val is not None else match.group(0)

        return _PLACEHOLDER_RE.sub(repl, out)

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
        cabinet_id: str,
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
            project_id=project_id,
            cabinet_id=cabinet_id,
        )
        if body is None:
            return None
        # Always honor row project_ids (empty/missing = all projects).
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
        cabinet_id: str,
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
            cabinet_id=cabinet_id,
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
        if fmt == "merge_mapped_sqlite":
            ws_path = self._substitute(str(target.get("workspace_path") or ""), ctx)
            if not ws_path:
                return []
            return [
                MaterializeOp(
                    rule_id=rule_id,
                    module_id=module_id,
                    workspace_path=ws_path,
                    format="merge_mapped_sqlite",
                    source_type="rows",
                    priority=priority,
                    rows_bodies=rows,
                    merge_params={
                        "artifact_field": str(
                            target.get("artifact_field") or field or "artifact_ref"
                        ),
                        "map_field": str(target.get("map_field") or "column_map"),
                        "schema": list(target.get("schema") or []),
                        "required_map_keys": list(
                            target.get("required_map_keys") or ["title", "price"]
                        ),
                        "provenance": target.get("provenance")
                        if isinstance(target.get("provenance"), dict)
                        else {"target": "source_catalog", "from": "name"},
                    },
                )
            ]
        if fmt == "prompt_paths":
            return _expand_prompt_path_ops(
                rows=rows,
                rule_id=rule_id,
                module_id=module_id,
                priority=priority,
            )
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


def _pick_active_profile(rows: list[dict[str, Any]], project_id: str) -> str | None:
    matches: list[tuple[str, bool]] = []
    for row in rows:
        body = row.get("body") if isinstance(row.get("body"), dict) else {}
        if not _row_applies_to_project(body, project_id):
            continue
        matches.append((str(row["row_id"]), bool(body.get("is_default"))))
    if not matches:
        return None
    defaults = [rid for rid, is_def in matches if is_def]
    if defaults:
        return defaults[0]
    return matches[0][0]


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


def _join_prompt_file_path(base: str, file_name: str) -> str:
    """Join prompt_paths.path with a file name → workspace-relative path."""
    name = file_name.strip().lstrip("/")
    if not name:
        return ""
    if not name.endswith(".md"):
        name = f"{name}.md"
    base_norm = (base or "").strip().replace("\\", "/").lstrip("/")
    if not base_norm or base_norm == ".":
        return name
    return f"{base_norm.rstrip('/')}/{name}"


def _file_entry_body(entry: dict[str, Any]) -> str | None:
    for key in ("body", "body_md", "content"):
        val = entry.get(key)
        if isinstance(val, str):
            return val
    return None


def _expand_prompt_path_ops(
    *,
    rows: list[dict[str, Any]],
    rule_id: str,
    module_id: str,
    priority: int,
) -> list[MaterializeOp]:
    """Expand prompt_paths.files_json into raw write ops; skip empty files_json."""
    ops: list[MaterializeOp] = []
    for i, body in enumerate(rows):
        files = body.get("files_json")
        if not isinstance(files, list) or not files:
            continue
        path_base = str(body.get("path") or "")
        for j, entry in enumerate(files):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            text = _file_entry_body(entry)
            if not name or text is None:
                continue
            ws_path = _join_prompt_file_path(path_base, name)
            if not ws_path:
                continue
            ops.append(
                MaterializeOp(
                    rule_id=f"{rule_id}_{i}_{j}",
                    module_id=module_id,
                    workspace_path=ws_path,
                    format="raw",
                    source_type="rows",
                    priority=priority,
                    row_body={"body_md": text},
                    field="body_md",
                )
            )
    return ops