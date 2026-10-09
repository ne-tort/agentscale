"""First-party Pod MCP: equipment catalogs + request_lines / found_groups.

Stdio JSON-RPC (MCP tools/list + tools/call).

Env:
  PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, PRODAVAN_PROJECT_ID
  Optional: PRODAVAN_SESSION_ID (chat scope; header X-Prodavan-Session-Id)

Catalog search goes through Pod Bridge → OpenSearch (no local SQLite / EQUIPMENT_*).
SoT rows go through Bridge JWT (:8001).

WAVE7 responsibility split (v2.1.1):
  agent — request_lines (customer positions) + found_groups (candidate selection:
           part numbers, aliases, match category); NEVER writes found_offers;
  platform pipeline — materializes found_offers from OpenSearch by group keys,
           refreshes prices, computes best offers / «Закупка» / budget snapshot.
           request_lines status / found_count / selected_offer_id are owned by
           the pipeline too — the agent never sets them.

WAVE10 builds (v2.3.0):
  A «Сборка» (equipment_builds) belongs to ONE request line and represents one
  compatible PC/server variant (e.g. Intel vs AMD platform). A build has SLOTS
  keyed by equipment type (equipment_types.row_id). Slot candidates are the
  SAME found_groups rows — a group belongs EITHER to a position (line_id) OR to
  a build slot (build_id + slot_type_id). So recording a build component is
  exactly found_groups_upsert with build_id + slot_type_id instead of line_id.
  The pipeline then materializes offers, prices each slot's best group, sums the
  build total, and picks the best build per line (alternatives + benefit).
  Build fields components_count / price_total / match_kind / is_best /
  alternatives_count / benefit_* are pipeline-owned — do NOT set them.

Linking IDs (visible to the agent — no hidden ids):
  - request_lines.row_id  → pass as found_groups.line_id (позиция заказчика)
                            or equipment_builds.line_id (build → position)
  - equipment_builds.row_id → pass as found_groups.build_id (build slot group)
  - equipment_types.row_id  → pass as found_groups.slot_type_id (which slot)
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
BUILD_KINDS = frozenset({"pc", "server"})


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
            "src_hash — the stable catalog position id (sha1 of supplier|title); "
            "copy src_hash into found_groups.aliases_hash for hits WITHOUT a "
            "part_number, and collect every spelling of the same P/N into "
            "aliases_pn. "
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
            "match a customer position, OR which products fill a build slot. "
            "One row = one part-number group for one request line (line_id) "
            "OR for one build slot (build_id + slot_type_id). "
            "Record EVERY distinct candidate part number as its own group; a slot may "
            "have SEVERAL candidate groups — the platform prices them and picks the "
            "cheapest as the slot's best, so record alternatives, not just one. "
            "On create: EITHER line_id (request_lines.row_id, normal search) OR "
            "build_id + slot_type_id (build component) is required, AND at least one of "
            "part_number / aliases_pn / aliases_hash. "
            "part_number — canonical P/N of the group. aliases_pn — ALL other "
            "spellings of the SAME part number you saw across suppliers in the "
            "catalog hits (comma-separated). aliases_hash — src_hash ids of catalog "
            "hits that belong to this group but carry NO part number (copy the "
            "src_hash from equipment_catalog_search hits). "
            "match_kind — how well THIS group matches the requirement: "
            "'exact' (точное совпадение по P/N), 'analog' (функциональный аналог), "
            "'doubt' (есть сомнения в точности; analog and doubt are DIFFERENT "
            "categories). "
            "The platform materializes ALL catalog offers for the group keys "
            "(P/N + aliases_pn + aliases_hash) into «Найденные товары», refreshes "
            "prices from OpenSearch and auto-selects the best: cheapest offer wins, "
            "but a PRIORITY supplier beats price within the same match tier; "
            "analog/doubt groups never outrank exact ones regardless of supplier. "
            "For builds the same rule applies per slot: the cheapest accurate "
            "candidate becomes the slot's best; the build total is the sum of the "
            "slot bests, and the cheapest accurate build of the line becomes is_best. "
            "The user can override the choice manually in the UI. "
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
                    "description": "request_lines.row_id (normal search; required if no build_id)",
                },
                "build_id": {
                    "type": ["string", "null"],
                    "description": "equipment_builds.row_id (build slot component)",
                },
                "slot_type_id": {
                    "type": ["string", "null"],
                    "description": "equipment_types.row_id — which slot of the build",
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
                    "description": "Match category vs the customer position / requirement",
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
    {
        "name": "equipment_types_list",
        "description": (
            "List component types (equipment_types) — the SLOT vocabulary of a build: "
            "row_id (use as slot_type_id / build.slots key), name, sort_order, "
            "build_scope (all|pc|server — which builds show this slot) and fields_json "
            "(the characteristic keys to fill in equipment_items.attrs). "
            "COMPATIBILITY keys are shared across types on purpose: cpu.socket ↔ "
            "motherboard.socket, ram.ram_type ↔ motherboard.ram_type, "
            "cooling.socket_compat, case.form_factor_support, psu.wattage vs "
            "gpu.recommended_psu_w, storage.interface ↔ motherboard.sata_ports/m2_slots. "
            "Use these keys to verify parts are compatible before adding them to a build."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "build_kind": {
                    "type": "string",
                    "enum": ["pc", "server"],
                    "description": "Optional: only types whose build_scope fits",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "equipment_builds_list",
        "description": (
            "List builds (equipment_builds) — PC/server variants for customer positions. "
            "Each build: name, line_id (request_lines.row_id), build_kind (pc|server), "
            "components_count, price_total (sum of slot best offers), match_kind, "
            "is_best, alternatives_count, benefit_label, slots. "
            "One line may have SEVERAL builds (e.g. Intel and AMD platforms): the "
            "pipeline marks the cheapest accurate one is_best, the rest are "
            "alternatives. Filter by line_id to see the variants of one position."
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
        "name": "equipment_builds_get",
        "description": "Get one equipment_builds row by row_id.",
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
        "name": "equipment_builds_upsert",
        "description": (
            "Create or update a build (equipment_builds) — one compatible PC/server "
            "variant for a customer position. "
            "On create: name AND line_id (request_lines.row_id) are required; "
            "build_kind defaults to 'pc'. "
            "Create SEVERAL builds per position when the platform is not fixed "
            "(e.g. one Intel, one AMD) — each with its own components; the pipeline "
            "then picks the best by accuracy→price and lists the rest as alternatives. "
            "Components are NOT written here: add each part with found_groups_upsert "
            "passing build_id (this build's row_id) + slot_type_id (equipment_types.row_id). "
            "slot_qty sets how many of a component the build needs "
            "({equipment_types.row_id: qty}, e.g. {\"etype_ram\": 2} for two sticks — "
            "use it when 2 cheaper sticks beat 1 larger one). Quantity belongs to the "
            "SLOT, not to a candidate: it holds for any alternative of that slot. "
            "The slot price is multiplied by qty in price_total. "
            "PATCH merges: omit = leave; null = clear. Optional: build_kind, slot_qty, "
            "note, project_ids. components_count / price_total / match_kind / is_best / "
            "alternatives_count / benefit_* are pipeline-owned — do not set them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "name": {"type": "string"},
                "line_id": {
                    "type": ["string", "null"],
                    "description": "request_lines.row_id (required on create)",
                },
                "build_kind": {
                    "type": ["string", "null"],
                    "enum": ["pc", "server", None],
                },
                "slot_qty": {
                    "type": ["object", "null"],
                    "description": (
                        "Quantity per slot: {equipment_types.row_id: int >= 1}. "
                        "Omitted slots default to 1."
                    ),
                    "additionalProperties": {"type": ["number", "integer"]},
                },
                "note": {"type": ["string", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "equipment_builds_delete",
        "description": (
            "Delete a build (equipment_builds) by row_id. Its slot candidate groups "
            "(found_groups with this build_id) and their materialized offers are "
            "cleaned up by the platform pipeline automatically. Use it to drop a "
            "mistaken or non-compatible variant."
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
        build_id = body.get("build_id")
        has_line = isinstance(line_id, str) and bool(line_id.strip())
        has_build = isinstance(build_id, str) and bool(build_id.strip())
        if not has_line and not has_build:
            raise RuntimeError(
                "found_groups must belong to EITHER a request line (line_id = "
                "request_lines.row_id from request_lines_list) OR a build slot "
                "(build_id = equipment_builds.row_id + slot_type_id = "
                "equipment_types.row_id)"
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
    # build_id без slot_type_id недопустим и на PATCH: группа без слота попала бы
    # в фантомный слот "" и молча вошла в цену сборки.
    if "build_id" in body:
        build_id = body.get("build_id")
        if isinstance(build_id, str) and build_id.strip():
            slot = body.get("slot_type_id")
            if not (isinstance(slot, str) and slot.strip()):
                raise RuntimeError(
                    "slot_type_id is required with build_id (use equipment_types.row_id "
                    "from equipment_types_list)"
                )
    if "match_kind" in body and body["match_kind"] is not None:
        if str(body["match_kind"]) not in MATCH_KINDS:
            raise RuntimeError(f"match_kind must be one of {sorted(MATCH_KINDS)}")


def _validate_build_body(body: dict[str, Any], *, creating: bool) -> None:
    if creating:
        if not str(body.get("name") or "").strip():
            raise RuntimeError("name is required when creating equipment_builds")
        if not str(body.get("line_id") or "").strip():
            raise RuntimeError(
                "line_id is required when creating equipment_builds "
                "(use request_lines.row_id from request_lines_list)"
            )
    if "build_kind" in body and body["build_kind"] is not None:
        if str(body["build_kind"]) not in BUILD_KINDS:
            raise RuntimeError(f"build_kind must be one of {sorted(BUILD_KINDS)}")
    if "slot_qty" in body and body["slot_qty"] is not None:
        qty = body["slot_qty"]
        if not isinstance(qty, dict):
            raise RuntimeError(
                "slot_qty must be an object {equipment_types.row_id: quantity}"
            )
        for slot, value in qty.items():
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise RuntimeError(
                    f"slot_qty[{slot!r}] must be a number >= 1"
                ) from None
            if num < 1 or num != int(num):
                raise RuntimeError(
                    f"slot_qty[{slot!r}] must be a whole number >= 1 (got {value!r})"
                )


def _scope_fits(build_scope: str, build_kind: str) -> bool:
    """build_scope all|pc|server matches a requested build_kind (empty = any)."""
    scope = (build_scope or "").strip() or "all"
    if scope == "all" or not build_kind:
        return True
    return scope == build_kind


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
        build_id = str(arguments.get("build_id") or "").strip()
        if line_id or build_id:
            filtered = []
            for r in items:
                body = r.get("body") if isinstance(r.get("body"), dict) else r
                if line_id and str(body.get("line_id") or "") != line_id:
                    continue
                if build_id and str(body.get("build_id") or "") != build_id:
                    continue
                filtered.append(r)
            items = filtered
        return {"items": items}
    if name == "found_groups_get":
        return _get_row(mid, "found_groups", str(arguments.get("row_id") or ""), session_id=sid)
    if name == "found_groups_upsert":
        keys = (
            "line_id",
            "build_id",
            "slot_type_id",
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

    if name == "equipment_types_list":
        items = _list_rows(mid, "equipment_types", session_id=sid)
        build_kind = str(arguments.get("build_kind") or "").strip()
        if build_kind:
            items = [
                r
                for r in items
                if _scope_fits(
                    str((r.get("body") or {}).get("build_scope") or ""), build_kind
                )
            ]
        return {"items": items}

    if name == "equipment_builds_list":
        items = _list_rows(mid, "equipment_builds", session_id=sid)
        line_id = str(arguments.get("line_id") or "").strip()
        if line_id:
            filtered = []
            for r in items:
                body = r.get("body") if isinstance(r.get("body"), dict) else r
                if str(body.get("line_id") or "") == line_id:
                    filtered.append(r)
            items = filtered
        return {"items": items}
    if name == "equipment_builds_get":
        return _get_row(mid, "equipment_builds", str(arguments.get("row_id") or ""), session_id=sid)
    if name == "equipment_builds_upsert":
        keys = (
            "name",
            "line_id",
            "build_kind",
            "slot_qty",
            "note",
            "project_ids",
        )
        body = _pick_present(arguments, keys)
        row_id = str(arguments.get("row_id") or "").strip() or None
        _validate_build_body(body, creating=not row_id)
        if row_id:
            return _http(
                "PATCH",
                _data_path(mid, "equipment_builds", row_id),
                {"body": body},
                session_id=sid,
            )
        return _http(
            "POST",
            _data_path(mid, "equipment_builds"),
            {"body": body},
            session_id=sid,
        )
    if name == "equipment_builds_delete":
        row_id = str(arguments.get("row_id") or "").strip()
        if not row_id:
            raise RuntimeError("row_id is required for equipment_builds_delete")
        return _http(
            "DELETE",
            _data_path(mid, "equipment_builds", row_id),
            session_id=sid,
        )

    raise RuntimeError(f"unknown tool: {name}")


def _load_tool_overrides() -> dict[str, dict[str, str]]:
    """UI-управляемые переопределения инструкций (таблица mcp_tool_overrides).

    Читается на каждый tools/list (подключение/перезагрузка MCP): правки из
    «Управление → Подбор техники → Инструкции MCP» подхватываются без правок
    кода. Сбой чтения никогда не ломает список инструментов.
    """
    try:
        rows = _list_rows(DEFAULT_MODULE_ID, "mcp_tool_overrides")
    except Exception:
        return {}
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        body = r.get("body") if isinstance(r.get("body"), dict) else r
        if not isinstance(body, dict) or body.get("enabled") is False:
            continue
        tool = str(body.get("tool") or "").strip()
        if not tool:
            continue
        out[tool] = {
            "description": str(body.get("description") or "").strip(),
            "extra": str(body.get("extra_instructions") or "").strip(),
        }
    return out


def _tools_with_overrides() -> list[dict[str, Any]]:
    """TOOLS + переопределения описаний из UI (description заменяет,
    extra_instructions дописывается)."""
    overrides = _load_tool_overrides()
    if not overrides:
        return TOOLS
    out: list[dict[str, Any]] = []
    for t in TOOLS:
        ov = overrides.get(str(t.get("name") or ""))
        if not ov:
            out.append(t)
            continue
        desc = ov["description"] or str(t.get("description") or "")
        if ov["extra"]:
            desc = desc.rstrip() + "\n\n" + ov["extra"]
        out.append({**t, "description": desc})
    return out


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
                "serverInfo": {"name": "prodavan-equipment", "version": "2.3.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _tools_with_overrides()}}
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
