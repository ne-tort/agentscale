"""Server-side module meta validation — parity with Flutter ModuleMetaValidator."""

from __future__ import annotations

import re
from typing import Any

from prodavan.domain.errors import AppError

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROW_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

COLUMN_TYPES = frozenset(
    {"text", "number", "bool", "datetime", "json", "enum", "ref", "file_ref"}
)
VIEW_KINDS = frozenset({"collection", "form", "hub", "detail", "board", "profile_hub"})
SHELL_NAV_CONTOURS = frozenset({"admin", "company", "employee", "cabinet"})
SHELL_NAV_PLACEMENTS = frozenset({"rail", "management", "none"})

ARRAY_DOCUMENT_SLUGS = frozenset(
    {"tables", "columns", "views", "tabs", "actions", "materialize", "mcp_tools"}
)
META_DOCUMENT_SLUGS = ARRAY_DOCUMENT_SLUGS | {"seed_rows"}


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
    if not isinstance(body, list):
        raise _meta_error(f"{slug} body must be a JSON array")


def manifest_from_slug_map(slug_map: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "tables": _list_of_maps(slug_map.get("tables")),
        "columns": _list_of_maps(slug_map.get("columns")),
        "views": _list_of_maps(slug_map.get("views")),
        "tabs": _list_of_maps(slug_map.get("tabs")),
        "actions": _list_of_maps(slug_map.get("actions")),
        "materialize": _list_of_maps(slug_map.get("materialize")),
        "mcp_tools": _list_of_maps(slug_map.get("mcp_tools")),
        "seed_rows": _parse_seed_items(slug_map.get("seed_rows")),
    }


def validate_manifest(manifest: dict[str, list[dict[str, Any]]]) -> None:
    table_slugs: set[str] = set()
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


def validate_merged_slug_map(slug_map: dict[str, Any]) -> None:
    validate_manifest(manifest_from_slug_map(slug_map))
