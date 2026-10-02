"""Server-side module meta validation — parity with Flutter ModuleMetaValidator."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from prodavan.application.projects.template_substitute import (
    deprecated_placeholder_names,
    has_deprecated_placeholders,
)
from prodavan.domain.errors import AppError

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROW_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

COLUMN_TYPES = frozenset(
    {"text", "number", "bool", "datetime", "json", "enum", "ref", "file_ref", "secret_ref"}
)
VIEW_KINDS = frozenset({"collection", "form", "hub", "detail", "board", "profile_hub"})
SHELL_NAV_CONTOURS = frozenset({"admin", "company", "employee", "cabinet"})
SHELL_NAV_PLACEMENTS = frozenset({"rail", "management", "data", "none"})

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_LIFECYCLE_WHEN = frozenset({"project.launch", "project.sync", "project.resumed", "project.reload"})
_MATERIALIZE_WHEN = frozenset({"project.created", "project.resumed", "project.sync"})
_MATERIALIZE_SOURCE_TYPES = frozenset({"row", "rows", "static", "meta_document"})
_MATERIALIZE_FORMATS = frozenset(
    {
        "raw",
        "json_rows",
        "json_single",
        "template",
        "copy_blob",
        "mcp_package",
        "merge_mapped_sqlite",
        "prompt_paths",
    }
)
_BLOB_MATERIALIZE_FORMATS = frozenset({"copy_blob", "mcp_package"})

ARRAY_DOCUMENT_SLUGS = frozenset(
    {
        "tables",
        "columns",
        "views",
        "tabs",
        "actions",
        "materialize",
        "mcp_tools",
        "mcp_aliases",
        "container_env",
        "container_env_secrets",
    }
)
META_DOCUMENT_SLUGS = ARRAY_DOCUMENT_SLUGS | {"seed_rows", "materialize_roots"}


def _meta_error(detail: str) -> AppError:
    return AppError(
        code="META_VALIDATION",
        title="Meta validation error",
        status=422,
        detail=detail,
    )


def _list_of_maps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _parse_seed_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return _list_of_maps(value["items"])
    return _list_of_maps(value)


def validate_document_body(slug: str, body: Any) -> None:
    if slug not in META_DOCUMENT_SLUGS:
        raise _meta_error(f"unknown meta document slug: {slug}")
    if slug == "seed_rows":
        if not isinstance(body, dict):
            raise _meta_error("seed_rows body must be a JSON object")
        items = body.get("items")
        if items is not None and not isinstance(items, list):
            raise _meta_error("seed_rows.items must be an array")
        return
    if slug == "materialize_roots":
        _validate_materialize_roots(body)
        return
    if not isinstance(body, list):
        raise _meta_error(f"{slug} body must be a JSON array")
    if slug == "mcp_aliases":
        _validate_mcp_aliases(body)


def _validate_mcp_aliases(items: list[Any]) -> None:
    """Chat display aliases for MCP tools: [{tool, label, server?, description?}].

    Keys the chat matches on are the canonical wire names
    ``mcp.{server}.{tool}`` (server optional — a server-less alias matches
    any server exposing that tool name).
    """
    seen: set[str] = set()
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise _meta_error(f"mcp_aliases[{idx}] must be an object")
        tool = item.get("tool")
        if not isinstance(tool, str) or not tool.strip() or len(tool) > 200:
            raise _meta_error(f"mcp_aliases[{idx}].tool must be a non-empty string (<=200)")
        server = item.get("server")
        if server is not None and (not isinstance(server, str) or not server.strip() or len(server) > 100):
            raise _meta_error(f"mcp_aliases[{idx}].server must be a non-empty string (<=100)")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise _meta_error(f"mcp_aliases[{idx}].label must be a non-empty string (<=200)")
        description = item.get("description")
        if description is not None and (not isinstance(description, str) or len(description) > 500):
            raise _meta_error(f"mcp_aliases[{idx}].description must be a string (<=500)")
        key = f"{(server or '').strip()}::{tool.strip()}"
        if key in seen:
            raise _meta_error(f"mcp_aliases: duplicate alias for {key}")
        seen.add(key)


def _validate_materialize_roots(body: Any) -> None:
    """Audit META-P2b: ``workspace_roots`` must be relative paths with no ``..``.

    A malicious or buggy meta-slug listing ``..`` / ``/`` / ``.`` would let
    sync_project ``wipe_prefix`` prune outside the project workspace. Reject
    at save time so the bad meta never reaches materialize.
    """
    if not isinstance(body, dict):
        raise _meta_error("materialize_roots body must be a JSON object")
    roots = body.get("workspace_roots")
    if roots is None:
        return  # empty is allowed (no roots → no prune)
    if not isinstance(roots, list):
        raise _meta_error("materialize_roots.workspace_roots must be an array")
    for idx, raw in enumerate(roots):
        if not isinstance(raw, str):
            raise _meta_error(
                f"materialize_roots.workspace_roots[{idx}] must be a string"
            )
        candidate = raw.strip()
        if not candidate:
            continue
        norm = candidate.replace("\\", "/").lstrip("/")
        if not norm or norm in {".", "./"}:
            raise _meta_error(
                f"materialize_roots.workspace_roots[{idx}] cannot be the workspace root"
            )
        if ".." in PurePosixPath(norm).parts:
            raise _meta_error(
                f"materialize_roots.workspace_roots[{idx}] must not contain '..': {raw!r}"
            )


def manifest_from_slug_map(slug_map: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "tables": _list_of_maps(slug_map.get("tables")),
        "columns": _list_of_maps(slug_map.get("columns")),
        "views": _list_of_maps(slug_map.get("views")),
        "tabs": _list_of_maps(slug_map.get("tabs")),
        "actions": _list_of_maps(slug_map.get("actions")),
        "materialize": _list_of_maps(slug_map.get("materialize")),
        "mcp_tools": _list_of_maps(slug_map.get("mcp_tools")),
        "mcp_aliases": _list_of_maps(slug_map.get("mcp_aliases")),
        "seed_rows": _parse_seed_items(slug_map.get("seed_rows")),
    }


def validate_manifest(manifest: dict[str, list[dict[str, Any]]]) -> None:
    table_slugs: set[str] = set()
    column_names_by_table: dict[str, set[str]] = {}
    for table in manifest["tables"]:
        slug = table.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.match(slug):
            raise _meta_error(f"invalid table slug: {slug!r}")
        if slug in table_slugs:
            raise _meta_error(f"duplicate table slug: {slug}")
        table_slugs.add(slug)

    for column in manifest["columns"]:
        table_slug = column.get("table_slug")
        if not isinstance(table_slug, str) or table_slug not in table_slugs:
            raise _meta_error(f"column references unknown table: {table_slug!r}")
        name = column.get("name")
        if not isinstance(name, str) or not _SLUG_RE.match(name):
            raise _meta_error(f"invalid column name: {name!r}")
        col_type = column.get("type")
        if not isinstance(col_type, str) or col_type not in COLUMN_TYPES:
            raise _meta_error(f"invalid column type: {col_type!r}")
        column_names_by_table.setdefault(table_slug, set()).add(name)

    view_slugs: set[str] = set()
    for view in manifest["views"]:
        slug = view.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.match(slug):
            raise _meta_error(f"invalid view slug: {slug!r}")
        if slug in view_slugs:
            raise _meta_error(f"duplicate view slug: {slug}")
        view_slugs.add(slug)
        table_slug = view.get("table_slug")
        if isinstance(table_slug, str) and table_slug not in table_slugs:
            raise _meta_error(f"view references unknown table: {table_slug}")
        ui = view.get("ui_json")
        if isinstance(ui, dict):
            kind = ui.get("kind")
            if isinstance(kind, str) and kind not in VIEW_KINDS:
                raise _meta_error(f"invalid view kind: {kind}")

    for tab in manifest["tabs"]:
        view_slug = tab.get("view_slug")
        if isinstance(view_slug, str) and view_slug not in view_slugs:
            raise _meta_error(f"tab references unknown view: {view_slug}")
        table_slug = tab.get("table_slug")
        if isinstance(table_slug, str) and table_slug not in table_slugs:
            raise _meta_error(f"tab references unknown table: {table_slug}")
        nav = tab.get("nav")
        if nav is not None:
            if not isinstance(nav, dict):
                raise _meta_error("tab nav must be an object")
            contour = nav.get("contour")
            if contour is not None and (
                not isinstance(contour, str) or contour not in SHELL_NAV_CONTOURS
            ):
                raise _meta_error(f"invalid tab nav.contour: {contour!r}")
            placement = nav.get("placement")
            if placement is not None and (
                not isinstance(placement, str) or placement not in SHELL_NAV_PLACEMENTS
            ):
                raise _meta_error(f"invalid tab nav.placement: {placement!r}")
        subtitle = tab.get("subtitle")
        if subtitle is not None and not isinstance(subtitle, str):
            raise _meta_error("tab subtitle must be a string when set")

    for item in manifest["seed_rows"]:
        table_slug = item.get("table_slug")
        if not isinstance(table_slug, str) or table_slug not in table_slugs:
            raise _meta_error(f"seed_rows references unknown table: {table_slug!r}")
        row_id = item.get("row_id")
        if not isinstance(row_id, str) or not _ROW_ID_RE.match(row_id):
            raise _meta_error(f"invalid seed_rows row_id: {row_id!r}")
        body = item.get("body")
        if body is not None and not isinstance(body, dict):
            raise _meta_error(f"seed_rows body must be an object for {row_id}")

    _validate_materialize_rules(manifest["materialize"], table_slugs, column_names_by_table)


def _validate_workspace_path(path: str, *, rule_label: str) -> None:
    if not path.strip():
        raise _meta_error(f"{rule_label} target.workspace_path required")
    if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/"):
        raise _meta_error(f"{rule_label} target.workspace_path must be relative: {path!r}")


def _validate_template_dialect(template: str, *, rule_label: str, field: str) -> None:
    """Reject the deprecated single-brace ``{var}`` dialect (audit META-P1c).

    ``{{var}}`` is the only supported placeholder syntax. A single-brace
    ``{var}`` used to also work via a fragile second regex pass that matched
    the inner braces of ``{{target_path}}``; rejecting it at validation time
    keeps meta authors from introducing mixed-dialect templates that break
    under substitution ordering changes.
    """
    if not template:
        return
    if has_deprecated_placeholders(template):
        names = deprecated_placeholder_names(template)
        raise _meta_error(
            f"{rule_label} {field} uses deprecated {{{{ {names[0]} }}}}-style "
            f"placeholder(s): {names}; use {{{{var}}}} instead"
        )


def _validate_materialize_rules(
    rules: list[dict[str, Any]],
    table_slugs: set[str],
    column_names_by_table: dict[str, set[str]],
) -> None:
    rule_ids: set[str] = set()
    for idx, rule in enumerate(rules):
        label = f"materialize[{idx}]"
        rid = rule.get("id")
        if rid is not None:
            if not isinstance(rid, str) or not rid.strip():
                raise _meta_error(f"{label} id must be a non-empty string")
            if rid in rule_ids:
                raise _meta_error(f"duplicate materialize rule id: {rid}")
            rule_ids.add(rid)
            label = f"materialize[{rid}]"

        when = rule.get("when")
        if when is not None:
            if not isinstance(when, list) or not when:
                raise _meta_error(f"{label} when must be a non-empty array")
            for event in when:
                if not isinstance(event, str) or event not in _MATERIALIZE_WHEN:
                    raise _meta_error(f"{label} invalid when event: {event!r}")

        priority = rule.get("priority")
        if priority is not None and not isinstance(priority, int):
            raise _meta_error(f"{label} priority must be an integer")

        source = rule.get("source")
        if not isinstance(source, dict):
            raise _meta_error(f"{label} source must be an object")
        source_type = source.get("type") or "row"
        if not isinstance(source_type, str) or source_type not in _MATERIALIZE_SOURCE_TYPES:
            raise _meta_error(f"{label} invalid source.type: {source_type!r}")

        table_slug = source.get("table_slug")
        if source_type in {"row", "rows"}:
            if not isinstance(table_slug, str) or table_slug not in table_slugs:
                raise _meta_error(f"{label} source references unknown table: {table_slug!r}")
        elif source_type == "meta_document":
            doc_slug = source.get("slug")
            if not isinstance(doc_slug, str) or not doc_slug.strip():
                raise _meta_error(f"{label} meta_document source requires slug")
        elif source_type == "static":
            if source.get("value") is None and source.get("text") is None:
                raise _meta_error(f"{label} static source requires value or text")

        target = rule.get("target")
        if not isinstance(target, dict):
            raise _meta_error(f"{label} target must be an object")
        ws_path = target.get("workspace_path")
        if not isinstance(ws_path, str):
            raise _meta_error(f"{label} target.workspace_path must be a string")
        _validate_workspace_path(ws_path, rule_label=label)
        _validate_template_dialect(ws_path, rule_label=label, field="target.workspace_path")

        fmt = target.get("format") or "raw"
        if not isinstance(fmt, str) or fmt not in _MATERIALIZE_FORMATS:
            raise _meta_error(f"{label} invalid target.format: {fmt!r}")

        if fmt == "template" and not isinstance(target.get("template"), str):
            raise _meta_error(f"{label} template format requires target.template string")
        if fmt == "template" and isinstance(target.get("template"), str):
            _validate_template_dialect(
                target["template"], rule_label=label, field="target.template"
            )

        # source.filter values also go through substitute() at plan time — they
        # must use the same {{var}} dialect, not the deprecated {var}.
        filt = source.get("filter")
        if isinstance(filt, dict):
            for key, val in filt.items():
                if isinstance(val, str):
                    _validate_template_dialect(
                        val, rule_label=label, field=f"source.filter.{key}"
                    )

        if fmt in _BLOB_MATERIALIZE_FORMATS:
            field = target.get("field")
            if not isinstance(field, str) or not field.strip():
                raise _meta_error(f"{label} target.field required for format {fmt}")
            if source_type in {"row", "rows"} and isinstance(table_slug, str):
                cols = column_names_by_table.get(table_slug, set())
                if field not in cols:
                    raise _meta_error(f"{label} target.field references unknown column: {field!r}")


def validate_merged_slug_map(slug_map: dict[str, Any]) -> None:
    validate_manifest(manifest_from_slug_map(slug_map))
    _validate_container_env_doc(slug_map.get("container_env"))
    _validate_container_env_secrets_doc(slug_map.get("container_env_secrets"))


def _validate_container_env_doc(body: Any) -> None:
    if body is None:
        return
    if not isinstance(body, list):
        raise _meta_error("container_env body must be a JSON array")
    for idx, entry in enumerate(body):
        if not isinstance(entry, dict):
            raise _meta_error(f"container_env[{idx}] must be an object")
        env_name = entry.get("env_name")
        if not isinstance(env_name, str) or not _ENV_NAME_RE.match(env_name):
            raise _meta_error(f"container_env[{idx}] invalid env_name: {env_name!r}")
        has_value = isinstance(entry.get("value"), str)
        has_value_from = isinstance(entry.get("value_from"), dict)
        if not has_value and not has_value_from:
            raise _meta_error(f"container_env[{idx}] requires value or value_from")
        when = entry.get("when")
        if when is not None:
            if not isinstance(when, list):
                raise _meta_error(f"container_env[{idx}] when must be an array")
            for item in when:
                if not isinstance(item, str) or item not in _LIFECYCLE_WHEN:
                    raise _meta_error(f"container_env[{idx}] invalid when: {item!r}")


def _validate_container_env_secrets_doc(body: Any) -> None:
    if body is None:
        return
    if not isinstance(body, list):
        raise _meta_error("container_env_secrets body must be a JSON array")
    for idx, entry in enumerate(body):
        if not isinstance(entry, dict):
            raise _meta_error(f"container_env_secrets[{idx}] must be an object")
        foreach = entry.get("foreach_rows")
        if isinstance(foreach, dict):
            table_slug = foreach.get("table_slug")
            field = foreach.get("field")
            prefix = foreach.get("env_name_prefix")
            if not isinstance(table_slug, str) or not table_slug.strip():
                raise _meta_error(f"container_env_secrets[{idx}] foreach_rows.table_slug required")
            if not isinstance(field, str) or not field.strip():
                raise _meta_error(f"container_env_secrets[{idx}] foreach_rows.field required")
            if not isinstance(prefix, str) or not prefix.strip():
                raise _meta_error(
                    f"container_env_secrets[{idx}] foreach_rows.env_name_prefix required"
                )
            # Prefix must yield valid env names when a suffix is appended.
            if not re.match(r"^[A-Z][A-Z0-9_]*_$", prefix) and not _ENV_NAME_RE.match(
                prefix.rstrip("_")
            ):
                raise _meta_error(
                    f"container_env_secrets[{idx}] invalid foreach_rows.env_name_prefix: {prefix!r}"
                )
            registry = foreach.get("registry_env_name")
            if registry is not None and (
                not isinstance(registry, str) or not _ENV_NAME_RE.match(registry)
            ):
                raise _meta_error(
                    f"container_env_secrets[{idx}] invalid foreach_rows.registry_env_name"
                )
        else:
            env_name = entry.get("env_name")
            if not isinstance(env_name, str) or not _ENV_NAME_RE.match(env_name):
                raise _meta_error(f"container_env_secrets[{idx}] invalid env_name: {env_name!r}")
            has_ref = isinstance(entry.get("secret_ref"), str) and bool(
                entry.get("secret_ref", "").strip()
            )
            has_ref_from = isinstance(entry.get("secret_ref_from"), dict)
            if not has_ref and not has_ref_from:
                raise _meta_error(
                    f"container_env_secrets[{idx}] requires secret_ref or secret_ref_from"
                )
        when = entry.get("when")
        if when is not None:
            if not isinstance(when, list):
                raise _meta_error(f"container_env_secrets[{idx}] when must be an array")
            for item in when:
                if not isinstance(item, str) or item not in _LIFECYCLE_WHEN:
                    raise _meta_error(f"container_env_secrets[{idx}] invalid when: {item!r}")
