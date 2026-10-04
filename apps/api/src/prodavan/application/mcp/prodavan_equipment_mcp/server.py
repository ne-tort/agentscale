"""First-party Pod MCP: equipment catalogs + request_lines / found_groups.

Stdio JSON-RPC (MCP tools/list + tools/call).

Env:
  PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, PRODAVAN_PROJECT_ID
  Optional: PRODAVAN_SESSION_ID (chat scope; header X-Prodavan-Session-Id)

Catalog search goes through Pod Bridge → OpenSearch (no local SQLite / EQUIPMENT_*).
SoT rows go through Bridge JWT (:8001).

WAVE7 responsibility split (v2.1.0):
  agent — request_lines (customer positions) + found_groups (candidate selection:
           part numbers, aliases, match category); NEVER writes found_offers;
  platform pipeline — materializes found_offers from OpenSearch by group keys,
           refreshes prices, computes best offers / «Закупка» / budget snapshot.
           request_lines status / found_count / selected_offer_id are owned by
           the pipeline too — the agent never sets them.

Linking IDs (visible to the agent — no hidden ids):
  - request_lines.row_id  → pass as found_groups.line_id (позиция заказчика)
  - found_groups.row_id   → group identity (offers link back via group_id)
  - catalog hit.src_hash  → stable catalog position id (supplier+title);
                           use it in aliases_hash when a position has no P/N
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

DEFAULT_MODULE_ID = "mod_equipment"
MATCH_KINDS = frozenset({"exact", "analog", "doubt"})


def _env() -> tuple[str, str, str]:
    api = (os.environ.get("PRODAVAN_API_BASE_URL") or "").rstrip("/")
    token = os.environ.get("PRODAVAN_AUTH_TOKEN") or os.environ.get("BRIDGE_AUTH_TOKEN") or ""
    project_id = os.environ.get("PRODAVAN_PROJECT_ID") or os.environ.get("PROJECT_ID") or ""
    return api, token, project_id


def _session_id(arguments: dict[str, Any] | None = None) -> str | None:
    if arguments:
        raw = arguments.get("session_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    env = (os.environ.get("PRODAVAN_SESSION_ID") or "").strip()
    return env or None


TOOLS: list[dict[str, Any]] = [
    {
        "name": "equipment_catalog_sources",
        "description": (
            "List ready OpenSearch-backed catalog sources for this project "
            "(status=ready, not paused). Canonical columns via column_map at index time."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "equipment_catalog_search",
        "description": (
            "Unified RO search across OpenSearch equipment catalog indexes. "
            "Default query matches title, part_number, brand, supplier, lead_time, price. "
            "Prefer part_number for large catalogs. "
            "brand filter matches keyword brand OR title text (many rows have empty brand). "
            "Default in_stock_only=true (excludes lead_time «нет»/on-order). "
            "If an exact P/N returns 0 hits, retry with in_stock_only=false. "
            "Hits include: part_number, title, brand, price, price_num, supplier, "
            "lead_time, currency, catalog_id, source_catalog, in_stock, "
            "match_rank (exact_pn|pn_prefix|title|other), match_rank_order, and "
            "src_hash — the stable catalog position id (sha1 of supplier|title). "
            "USE the hits to decide candidates: then record your selection with "
            "found_groups_upsert (part_number + aliases + match_kind). "
            "Do NOT copy products/prices into found_offers — the platform "
            "materializes offers and refreshes prices automatically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "part_number": {"type": "string"},
                "query": {
                    "type": "string",
                    "description": "Free text across title / P/N / brand / supplier / lead_time",
                },
                "brand": {
                    "type": "string",
                    "description": "Filter: brand keyword OR phrase in title",
                },
                "price_min": {"type": "number"},
                "price_max": {"type": "number"},
                "in_stock_only": {"type": "boolean", "default": True},
                "catalog_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Subset of source ids; default = all ready catalogs",
                },
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                "offset": {"type": "integer", "default": 0, "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "request_lines_list",
        "description": (
            "List customer request lines (позиции заказчика). "
            "Use each item's row_id as found_groups.line_id when recording candidates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "request_lines_get",
        "description": "Get one request_lines row by row_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
            },
            "required": ["row_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_lines_upsert",
        "description": (
            "Create or update a request_lines row (позиция заказчика). title required on "
            "create. PATCH merges: omit a field to leave it unchanged; pass null to clear. "
            "Optional: part_number, qty, project_ids. "
            "status, found_count and selected_offer_id are computed by the platform "
            "pipeline — do not set them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "title": {"type": "string"},
                "part_number": {"type": ["string", "null"]},
                "qty": {"type": ["number", "integer", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "request_lines_delete",
        "description": (
            "Delete a request_lines row by row_id. Its found_groups and materialized "
            "offers are cleaned up by the platform pipeline automatically. Use it to drop "
            "duplicated or mistaken customer positions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
            },
            "required": ["row_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "found_groups_list",
        "description": (
            "List candidate groups (found_groups) — your selection of part numbers per "
            "request line: line_id, part_number, aliases_pn, aliases_hash, match_kind "
            "(exact|analog|doubt), note, offers_count, face fields (best offer per group)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "line_id": {
                    "type": "string",
                    "description": "Optional filter: request_lines.row_id",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "found_groups_get",
        "description": "Get one found_groups row by row_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
            },
            "required": ["row_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "found_groups_upsert",
        "description": (
            "Create or update a found_groups row — THE tool to record which products "
            "match a customer position. One row = one part-number group for one "
            "request line. On create: line_id MUST be request_lines.row_id "
            "(from request_lines_list) AND at least one of part_number / "
            "aliases_pn / aliases_hash is required. "
            "match_kind — how well THIS group matches the customer position: "
            "'exact' (точное совпадение), 'analog' (аналог), 'doubt' (есть сомнения "
            "в точности; analog and doubt are DIFFERENT categories). "
            "aliases_pn — other spellings of the same part number used by other "
            "suppliers (comma-separated). aliases_hash — catalog src_hash ids of "
            "positions that belong to this group but carry no part number. "
            "The platform materializes ALL catalog offers for the group keys "
            "(part number + aliases), refreshes prices and computes best offers, "
            "«Закупка» and budget — you only pick groups and their match category. "
            "Do NOT write found_offers (no such tool): offers/prices are owned by "
            "the platform. "
            "PATCH merges: omit = leave; null = clear. Optional: note, project_ids."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "line_id": {
                    "type": ["string", "null"],
                    "description": "request_lines.row_id (required on create)",
                },
                "part_number": {
                    "type": ["string", "null"],
                    "description": "Part number of the group (empty for hash-only groups)",
                },
                "aliases_pn": {
                    "type": ["string", "null"],
                    "description": "Comma-separated alias part numbers (other spellings)",
                },
                "aliases_hash": {
                    "type": ["string", "null"],
                    "description": "Comma-separated src_hash ids (positions without P/N)",
                },
                "match_kind": {
                    "type": ["string", "null"],
                    "enum": ["exact", "analog", "doubt", None],
                    "description": "Match category vs the customer position",
                },
                "note": {
                    "type": ["string", "null"],
                    "description": "Why analog/doubt (shown to the user)",
                },
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "found_groups_delete",
        "description": (
            "Delete a found_groups row by row_id. Materialized offers of the group are "
            "removed by the platform pipeline. Use it to drop a mistaken candidate group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
            },
            "required": ["row_id"],
            "additionalProperties": False,
        },
    },
]


def _http(
    method: str,
    path: str,
    payload: Any | None = None,
    *,
    session_id: str | None = None,
) -> Any:
    api_base, token, project_id = _env()
    if not api_base or not token or not project_id:
        raise RuntimeError(
            "PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, and PRODAVAN_PROJECT_ID are required"
        )
    url = f"{api_base}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    sid = session_id or _session_id()
    if sid:
        headers["X-Prodavan-Session-Id"] = sid
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _module_id(arguments: dict[str, Any]) -> str:
    mid = str(arguments.get("module_id") or DEFAULT_MODULE_ID).strip()
    return mid or DEFAULT_MODULE_ID


def _data_path(module_id: str, table: str, row_id: str | None = None) -> str:
    _, _, project_id = _env()
    base = f"/projects/{project_id}/modules/{module_id}/data/{table}"
    if row_id:
        return f"{base}/{row_id}"
    return base


def _list_rows(
    module_id: str, table: str, *, session_id: str | None = None
) -> list[dict[str, Any]]:
    payload = _http("GET", _data_path(module_id, table), session_id=session_id)
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("rows") or []
        if isinstance(items, list):
            return [r for r in items if isinstance(r, dict)]
    return []


def _get_row(
    module_id: str,
    table: str,
    row_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    for row in _list_rows(module_id, table, session_id=session_id):
        if str(row.get("row_id") or "") == row_id:
            return row
    try:
        return _http("GET", _data_path(module_id, table, row_id), session_id=session_id)
    except RuntimeError:
        raise RuntimeError(f"{table} row not found: {row_id}") from None


def _pick_present(arguments: dict[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """Include only keys present in arguments (allows explicit null)."""
    body: dict[str, Any] = {}
    for key in keys:
        if key in arguments:
            body[key] = arguments[key]
    return body


def _validate_line_body(body: dict[str, Any], *, creating: bool) -> None:
    if creating and not str(body.get("title") or "").strip():
        raise RuntimeError("title is required when creating request_lines")


def _validate_group_body(body: dict[str, Any], *, creating: bool) -> None:
    if creating:
        line_id = body.get("line_id")
        if not isinstance(line_id, str) or not line_id.strip():
            raise RuntimeError(
                "line_id is required when creating found_groups "
                "(use request_lines.row_id from request_lines_list)"
            )
        has_keys = any(
            str(body.get(k) or "").strip()
            for k in ("part_number", "aliases_pn", "aliases_hash")
        )
        if not has_keys:
            raise RuntimeError(
                "at least one of part_number / aliases_pn / aliases_hash is required "
                "when creating found_groups (group keys to search the catalog)"
            )
    if "match_kind" in body and body["match_kind"] is not None:
        if str(body["match_kind"]) not in MATCH_KINDS:
            raise RuntimeError(f"match_kind must be one of {sorted(MATCH_KINDS)}")


def _catalog_sources() -> dict[str, Any]:
    _, _, project_id = _env()
    return _http(
        "GET",
        f"/projects/{project_id}/modules/mod_equipment/equipment/catalog-sources",
    )


def _catalog_search(arguments: dict[str, Any]) -> dict[str, Any]:
    _, _, project_id = _env()
    in_stock_only = arguments.get("in_stock_only")
    if in_stock_only is None:
        in_stock_only = True
    catalog_ids = arguments.get("catalog_ids")
    if catalog_ids is not None and not isinstance(catalog_ids, list):
        raise RuntimeError("catalog_ids must be an array of strings")
    payload: dict[str, Any] = {
        "query": arguments.get("query"),
        "part_number": arguments.get("part_number"),
        "brand": arguments.get("brand"),
        "price_min": arguments.get("price_min"),
        "price_max": arguments.get("price_max"),
        "in_stock_only": bool(in_stock_only),
        "limit": int(arguments.get("limit") or 20),
        "offset": int(arguments.get("offset") or 0),
    }
    if catalog_ids is not None:
        payload["catalog_ids"] = [str(x) for x in catalog_ids]
    return _http(
        "POST",
        f"/projects/{project_id}/modules/mod_equipment/equipment/catalog-search",
        payload,
    )


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "equipment_catalog_sources":
        return _catalog_sources()
    if name == "equipment_catalog_search":
        return _catalog_search(arguments)

    mid = _module_id(arguments)
    sid = _session_id(arguments)

    if name == "request_lines_list":
        return {"items": _list_rows(mid, "request_lines", session_id=sid)}
    if name == "request_lines_get":
        return _get_row(mid, "request_lines", str(arguments.get("row_id") or ""), session_id=sid)
    if name == "request_lines_upsert":
        keys = (
            "title",
            "part_number",
            "qty",
            "project_ids",
        )
        body = _pick_present(arguments, keys)
        row_id = str(arguments.get("row_id") or "").strip() or None
        _validate_line_body(body, creating=not row_id)
        if row_id:
            return _http(
                "PATCH",
                _data_path(mid, "request_lines", row_id),
                {"body": body},
                session_id=sid,
            )
        if "title" not in body:
            raise RuntimeError("title is required when creating request_lines")
        return _http(
            "POST",
            _data_path(mid, "request_lines"),
            {"body": body},
            session_id=sid,
        )
    if name == "request_lines_delete":
        row_id = str(arguments.get("row_id") or "").strip()
        if not row_id:
            raise RuntimeError("row_id is required for request_lines_delete")
        return _http(
            "DELETE",
            _data_path(mid, "request_lines", row_id),
            session_id=sid,
        )

    if name == "found_groups_list":
        items = _list_rows(mid, "found_groups", session_id=sid)
        line_id = str(arguments.get("line_id") or "").strip()
        if line_id:
            filtered = []
            for r in items:
                body = r.get("body") if isinstance(r.get("body"), dict) else r
                if str(body.get("line_id") or "") == line_id:
                    filtered.append(r)
            items = filtered
        return {"items": items}
    if name == "found_groups_get":
        return _get_row(mid, "found_groups", str(arguments.get("row_id") or ""), session_id=sid)
    if name == "found_groups_upsert":
        keys = (
            "line_id",
            "part_number",
            "aliases_pn",
            "aliases_hash",
            "match_kind",
            "note",
            "project_ids",
        )
        body = _pick_present(arguments, keys)
        row_id = str(arguments.get("row_id") or "").strip() or None
        _validate_group_body(body, creating=not row_id)
        if row_id:
            return _http(
                "PATCH",
                _data_path(mid, "found_groups", row_id),
                {"body": body},
                session_id=sid,
            )
        return _http(
            "POST",
            _data_path(mid, "found_groups"),
            {"body": body},
            session_id=sid,
        )
    if name == "found_groups_delete":
        row_id = str(arguments.get("row_id") or "").strip()
        if not row_id:
            raise RuntimeError("row_id is required for found_groups_delete")
        return _http(
            "DELETE",
            _data_path(mid, "found_groups", row_id),
            session_id=sid,
        )

    raise RuntimeError(f"unknown tool: {name}")


def _result_text(payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}]}


def _handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "prodavan-equipment", "version": "2.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            out = _call_tool(name, arguments)
            return {"jsonrpc": "2.0", "id": mid, "result": _result_text(out)}
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        resp = _handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
