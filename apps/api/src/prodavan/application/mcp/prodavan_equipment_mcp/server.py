"""First-party Pod MCP: equipment catalogs + request_lines / found_offers.

Stdio JSON-RPC (MCP tools/list + tools/call).

Env:
  PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, PRODAVAN_PROJECT_ID
  Optional: PRODAVAN_SESSION_ID (chat scope; header X-Prodavan-Session-Id)

Catalog search goes through Pod Bridge → OpenSearch (no local SQLite / EQUIPMENT_*).
SoT rows go through Bridge JWT (:8001).

Linking IDs (visible to the agent — no hidden ids):
  - request_lines.row_id  → pass as found_offers.line_id (Запрос)
  - found_offers.row_id   → pass as request_lines.selected_offer_id when selecting
  - catalog hit.catalog_id / source_catalog → provenance on found_offers.catalog_id
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Sequence

DEFAULT_MODULE_ID = "mod_equipment"
LINE_STATUSES = frozenset({"open", "matched", "selected"})
MATCH_KINDS = frozenset({"exact", "analog"})


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
            "brand filter matches keyword brand OR title text (many S4B rows have empty brand). "
            "Default in_stock_only=true (excludes lead_time «нет»/on-order). "
            "If an exact P/N returns 0 hits, retry with in_stock_only=false. "
            "Hits include: part_number, title, brand, price, price_num, supplier, lead_time, "
            "catalog_id, source_catalog, match_rank (exact_pn|pn_prefix|title|other), "
            "match_rank_order, in_stock. "
            "When writing found_offers, copy present fields: title, part_number, brand, "
            "price (prefer price_num), catalog_id; set match_kind=exact for exact_pn else analog; "
            "score from match_rank_order (lower is better) or omit."
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
            "Use each item's row_id as found_offers.line_id when adding candidates."
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
            "Create or update a request_lines row. title required on create. "
            "PATCH merges: omit a field to leave it unchanged; pass null to clear nullable. "
            "Optional: part_number, qty, status (open|matched|selected), "
            "found_count, selected_offer_id (= found_offers.row_id), project_ids. "
            "Pass row_id to update. Do not re-send qty/found_count unless changing them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "title": {"type": "string"},
                "part_number": {"type": ["string", "null"]},
                "qty": {"type": ["number", "integer", "null"]},
                "status": {
                    "type": ["string", "null"],
                    "enum": ["open", "matched", "selected", None],
                },
                "found_count": {"type": ["number", "integer", "null"]},
                "selected_offer_id": {"type": ["string", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "found_offers_list",
        "description": (
            "List found_offers. Optional filter line_id = request_lines.row_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "line_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "found_offers_get",
        "description": "Get one found_offers row by row_id.",
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
        "name": "found_offers_upsert",
        "description": (
            "Create or update a found_offers (кандидат) row. "
            "On create: title AND line_id are required. "
            "line_id MUST be request_lines.row_id from request_lines_list/get "
            "(links the offer to Позиции заказчика — NOT a catalog/DB name). "
            "source_title is auto-filled from that request line's title; "
            "do NOT put catalog names (s4b, source_catalog) into source_title or line_id. "
            "Copy from catalog search when present: part_number, brand, price, "
            "seller (supplier), catalog_id; "
            "match_kind=exact if match_rank=exact_pn else analog; "
            "score optional (e.g. match_rank_order). "
            "PATCH merges: omit = leave; null = clear. "
            "Optional: is_selected, project_ids. "
            "After write with line_id, bumps request_lines.found_count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "title": {"type": "string"},
                "line_id": {
                    "type": ["string", "null"],
                    "description": "request_lines.row_id (required on create)",
                },
                "part_number": {"type": ["string", "null"]},
                "brand": {"type": ["string", "null"]},
                "seller": {
                    "type": ["string", "null"],
                    "description": (
                        "Supplier company name from catalog search (supplier field). "
                        "Used for supplier registry, margin defaults and priority."
                    ),
                },
                "price": {"type": ["number", "integer", "null"]},
                "score": {"type": ["number", "integer", "null"]},
                "match_kind": {
                    "type": ["string", "null"],
                    "enum": ["exact", "analog", None],
                },
                "is_selected": {"type": ["boolean", "null"]},
                "catalog_id": {"type": ["string", "null"]},
                "source_title": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional override; server auto-fills from request_lines.title "
                        "when line_id is set. Never a catalog/DB name."
                    ),
                },
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
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
    if "status" in body and body["status"] is not None:
        if str(body["status"]) not in LINE_STATUSES:
            raise RuntimeError(f"status must be one of {sorted(LINE_STATUSES)}")


def _validate_offer_body(body: dict[str, Any], *, creating: bool) -> None:
    if creating and not str(body.get("title") or "").strip():
        raise RuntimeError("title is required when creating found_offers")
    if creating:
        line_id = body.get("line_id")
        if not isinstance(line_id, str) or not line_id.strip():
            raise RuntimeError(
                "line_id is required when creating found_offers "
                "(use request_lines.row_id from request_lines_list)"
            )
    if "match_kind" in body and body["match_kind"] is not None:
        if str(body["match_kind"]) not in MATCH_KINDS:
            raise RuntimeError(f"match_kind must be one of {sorted(MATCH_KINDS)}")


def _fill_source_title_from_line(
    module_id: str,
    body: dict[str, Any],
    *,
    session_id: str | None,
) -> None:
    """source_title = request_lines.title for line_id — never catalog/source name."""
    line_id = body.get("line_id")
    if not isinstance(line_id, str) or not line_id.strip():
        return
    try:
        line = _get_row(module_id, "request_lines", line_id.strip(), session_id=session_id)
    except RuntimeError:
        return
    lb = line.get("body") if isinstance(line.get("body"), dict) else line
    title = str(lb.get("title") or "").strip()
    if title:
        body["source_title"] = title


def _bump_found_count(
    module_id: str, line_id: str, *, session_id: str | None = None
) -> None:
    if not line_id:
        return
    count = 0
    for r in _list_rows(module_id, "found_offers", session_id=session_id):
        body = r.get("body") if isinstance(r.get("body"), dict) else r
        if str(body.get("line_id") or "") == line_id:
            count += 1
    try:
        _http(
            "PATCH",
            _data_path(module_id, "request_lines", line_id),
            {"body": {"found_count": count}},
            session_id=session_id,
        )
    except RuntimeError:
        # Best-effort — offer write already succeeded; merge PATCH should not 422.
        pass


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
            "status",
            "found_count",
            "selected_offer_id",
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

    if name == "found_offers_list":
        items = _list_rows(mid, "found_offers", session_id=sid)
        line_id = str(arguments.get("line_id") or "").strip()
        if line_id:
            filtered = []
            for r in items:
                body = r.get("body") if isinstance(r.get("body"), dict) else r
                if str(body.get("line_id") or "") == line_id:
                    filtered.append(r)
            items = filtered
        return {"items": items}
    if name == "found_offers_get":
        return _get_row(mid, "found_offers", str(arguments.get("row_id") or ""), session_id=sid)
    if name == "found_offers_upsert":
        keys = (
            "title",
            "line_id",
            "part_number",
            "brand",
            "seller",
            "price",
            "score",
            "match_kind",
            "is_selected",
            "catalog_id",
            "source_title",
            "project_ids",
        )
        body = _pick_present(arguments, keys)
        row_id = str(arguments.get("row_id") or "").strip() or None
        _validate_offer_body(body, creating=not row_id)
        # Always prefer request-line title over agent-supplied catalog nicknames.
        _fill_source_title_from_line(mid, body, session_id=sid)
        if row_id:
            result = _http(
                "PATCH",
                _data_path(mid, "found_offers", row_id),
                {"body": body},
                session_id=sid,
            )
        else:
            result = _http(
                "POST",
                _data_path(mid, "found_offers"),
                {"body": body},
                session_id=sid,
            )
        line_id = body.get("line_id")
        if not line_id and row_id:
            try:
                existing = _get_row(mid, "found_offers", row_id, session_id=sid)
                eb = existing.get("body") if isinstance(existing.get("body"), dict) else existing
                line_id = eb.get("line_id")
            except RuntimeError:
                line_id = None
        if isinstance(line_id, str) and line_id.strip():
            _bump_found_count(mid, line_id.strip(), session_id=sid)
        return result

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
                "serverInfo": {"name": "prodavan-equipment", "version": "1.2.1"},
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
