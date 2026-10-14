"""First-party Pod MCP: equipment catalogs + request_lines / found_groups.

Stdio JSON-RPC (MCP tools/list + tools/call).

Env:
  PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, PRODAVAN_PROJECT_ID
  Optional: PRODAVAN_SESSION_ID (chat scope; header X-Prodavan-Session-Id)

Catalog search goes through Pod Bridge → OpenSearch (no local SQLite / EQUIPMENT_*).
SoT rows go through Bridge JWT (:8001).

WAVE7 responsibility split:
  agent — request_lines (customer positions) + found_groups (candidate selection:
           part numbers, aliases, match category); NEVER writes found_offers;
  platform pipeline — materializes found_offers from OpenSearch by group keys,
           refreshes prices, computes best offers / «Закупка» / budget snapshot.
           request_lines status / found_count / selected_offer_id are owned by
           the pipeline too — the agent never sets them.

WAVE12 builds v3.0.0 (каталог «Готовые сборки» + карточки комплектующих):
  - equipment_types are TEMPLATES: fields_json declares each characteristic with
    kind (text|enum|groups), required and compat_domain (socket / ram_type /
    form_factor). Values come from predefined groups — never free-form strings.
  - part_cards («Карточки комплектующих») map a part number (part_number /
    aliases_pn / aliases_hash) to a type + attrs (values from the type groups).
    The catalog pipeline resolves the price leader per card from OpenSearch.
  - ready_builds = slots of {type_id, constraints {field: group value}, qty,
    optional pinned card_id}. The pipeline picks compatible cheapest cards
    (compat matrix = shared compat_domain values) and honors target_price_min /
    target_price_max with a bounded smart combo search.
  - equipment_builds is a GLOBAL LINK to a ready build (ready_build_id) for a
    chat/order: composition and prices mirror the catalog automatically. Never
    assemble builds from scratch in the chat — pick a ready build
    (ready_builds_list) or create one (ready_build_upsert), then attach it
    (ready_build_attach) to the request line.

Linking IDs (visible to the agent — no hidden ids):
  - request_lines.row_id  → pass as found_groups.line_id (позиция заказчика)
  - equipment_types.row_id → part_cards.type_id / ready_build_slots.type_id
  - part_cards.row_id     → ready_build_slots.card_id (manual pin)
  - ready_builds.row_id   → ready_build_attach / equipment_builds.ready_build_id
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
            "(exact|analog|doubt), note, offers_count, face fields (best offer per group). "
            "Build slot groups (owner_kind=build) are NOT writable here: they are a "
            "mirror of the linked ready build maintained by the platform — read them "
            "with the build_id filter to inspect what a linked build resolves to."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "line_id": {
                    "type": "string",
                    "description": "Optional filter: request_lines.row_id",
                },
                "build_id": {
                    "type": "string",
                    "description": (
                        "Optional filter: equipment_builds.row_id — read the mirrored "
                        "slot groups of a linked ready build (read-only for the agent)."
                    ),
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
            "match a customer position (request line). "
            "One row = one part-number group for one request line (line_id). "
            "Build slots are NOT writable here: linked ready builds mirror their "
            "composition automatically (see ready_build_attach / ready_build_upsert). "
            "Record EVERY distinct candidate part number as its own group — the "
            "platform prices them and picks the cheapest accurate one. "
            "On create: line_id (request_lines.row_id) AND at least one of "
            "part_number / aliases_pn / aliases_hash are required. "
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
            "List component types (equipment_types) — the TEMPLATES of the equipment "
            "model: row_id (use as type_id in part_cards / ready_build slots), name, "
            "sort_order, build_scope (all|pc|server) and fields_json — the "
            "characteristics of the type. Each field: key, label, kind "
            "(text | enum | groups), required (must be filled on part cards), "
            "compat_domain (socket | ram_type | form_factor — the compatibility "
            "matrix: fields of different types sharing a domain must MATCH in a "
            "build), values (enum) or groups (grouped values like \"512\" = 512 ГБ). "
            "ALWAYS pick attr/constraint values from these predefined values — "
            "never invent strings."
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
            "List builds of this chat (equipment_builds) — GLOBAL LINKS to ready "
            "builds attached to customer positions: name, line_id, ready_build_id, "
            "build_kind, components_count, price_total (sum of slot bests), "
            "match_kind, is_best, alternatives_count, benefit_label. "
            "One line may have SEVERAL linked builds: the pipeline marks the cheapest "
            "accurate one is_best, the rest are alternatives. Filter by line_id to "
            "see the variants of one position. Composition/prices mirror the catalog: "
            "edit the READY build (ready_build_upsert), not this row."
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
            "Create or update a build LINK (equipment_builds) — attaches a ready "
            "build to this chat/order. On create: name AND ready_build_id "
            "(ready_builds.row_id from ready_builds_list) are required; line_id "
            "(request_lines.row_id) binds the build to a customer position. "
            "There is NO from-scratch composition here: slots/constraints live in "
            "the catalog (ready_build_upsert / ready_build_slot_upsert) and mirror "
            "into the chat automatically. "
            "PATCH merges: omit = leave; null = clear. Optional: name, line_id, "
            "note, is_selected (manual best override for the position), project_ids. "
            "components_count / price_total / match_kind / is_best / "
            "alternatives_count / benefit_* are pipeline-owned — do not set them. "
            "In most cases prefer ready_build_attach (one call: link + pipeline)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "name": {"type": "string"},
                "ready_build_id": {
                    "type": ["string", "null"],
                    "description": "ready_builds.row_id (required on create)",
                },
                "line_id": {
                    "type": ["string", "null"],
                    "description": "request_lines.row_id (customer position)",
                },
                "is_selected": {"type": ["boolean", "null"]},
                "note": {"type": ["string", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "equipment_builds_delete",
        "description": (
            "Delete a build link (equipment_builds) by row_id — detaches the ready "
            "build from this chat. Its mirrored slot groups and materialized offers "
            "are cleaned up by the platform pipeline automatically. The ready build "
            "in the catalog is NOT touched."
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
    # ------------------------------------------------- WAVE12: карточки + сборки
    {
        "name": "part_cards_list",
        "description": (
            "List component cards (part_cards) — the catalog of identified part "
            "numbers: type_id, part_number, aliases, attrs (characteristic values "
            "from the type groups), attrs_label, and the resolved price leader "
            "(best_price / best_title / best_seller / offers_count / in_stock). "
            "Filters: type_id (equipment_types.row_id), constraints (object "
            "{field: group value} — only cards whose attrs cover ALL of them, e.g. "
            "{\"capacity\": \"512\"} for 512 ГБ drives). Use it to choose a card for "
            "a build slot (card_id pin) or to check whether a part number already "
            "has a card before creating one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "type_id": {
                    "type": "string",
                    "description": "Optional filter: equipment_types.row_id",
                },
                "constraints": {
                    "type": ["object", "null"],
                    "description": (
                        "Optional filter: {field_key: group value} — cards whose "
                        "attrs match ALL constraints (e.g. {\"socket\": \"LGA1700\"})"
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "part_card_upsert",
        "description": (
            "Create or update a component card (part_cards) — THE way to register a "
            "part number in the catalog. On create: type_id AND at least one of "
            "part_number / aliases_pn / aliases_hash are required. attrs carries the "
            "characteristics from the type template (equipment_types_list → "
            "fields_json): EVERY required field must be filled with a value FROM the "
            "field's values/groups (e.g. {\"socket\": \"LGA1700\", \"cores\": \"6\"}) — "
            "required fields are what the compatibility matrix and build slots match "
            "on. Unknown values are rejected. Fill attrs from the product title / "
            "specs you saw in catalog hits; when unsure between two groups, pick the "
            "closer one and leave a note. "
            "PATCH merges: omit = leave; null = clear. Optional: part_number, "
            "aliases_pn, aliases_hash, attrs, note, project_ids. "
            "best_price / best_seller / in_stock / offers_count are pipeline-owned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "type_id": {
                    "type": ["string", "null"],
                    "description": "equipment_types.row_id (required on create)",
                },
                "part_number": {"type": ["string", "null"]},
                "aliases_pn": {"type": ["string", "null"]},
                "aliases_hash": {"type": ["string", "null"]},
                "attrs": {
                    "type": ["object", "null"],
                    "description": (
                        "Characteristic values keyed by the type's fields_json keys; "
                        "values from the field's values/groups"
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "note": {"type": ["string", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "part_card_delete",
        "description": (
            "Delete a component card (part_cards) by row_id. Ready build slots pinned "
            "to it fall back to automatic selection on the next catalog sync."
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
        "name": "ready_builds_list",
        "description": (
            "List ready builds (ready_builds) — the reusable, price-auto-updating "
            "build catalog. ALWAYS prefer picking a ready build here over inventing "
            "a configuration: slots encode grouped characteristics (SSD 512 ГБ, "
            "LGA1700, DDR4…) and the pipeline resolves compatible components with "
            "best prices. Returns per build: row_id, name, build_kind, spec_summary "
            "(composition in one line), slots_count, qty_total, price_total "
            "(chosen combination), price_min / price_max (cheapest / priciest "
            "possible combination from current candidates), target_price_min / "
            "target_price_max, on_order, unresolved_count (slots without a priced "
            "card — the catalog needs more part cards there), compat_ok. "
            "Filters: build_kind, only_enabled (default true), limit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "build_kind": {"type": "string", "enum": ["pc", "server"]},
                "only_enabled": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ready_build_get",
        "description": (
            "Get ONE ready build with all its slots: build (full body) + slots "
            "(type_id, constraints, qty, pinned card_id, resolved component "
            "title/part_number/price, alternatives_count). Use it before attaching "
            "or editing a build."
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
        "name": "ready_build_upsert",
        "description": (
            "Create or update a ready build (ready_builds) TOGETHER WITH its slots "
            "in ONE call. On create: name is required. "
            "target_price_min / target_price_max (optional) set a price corridor: "
            "the platform then searches smart combinations of slot candidates "
            "(price leaders per part number) and picks one inside the corridor — "
            "the most complete configuration up to max, the cheapest reaching min. "
            "Each slot: type_id (equipment_types.row_id, required), constraints "
            "({field: group value} from the type template — e.g. "
            "{\"drive_type\": \"SSD\", \"capacity\": \"512\"}; empty = any card of the "
            "type), qty (default 1 — e.g. 2 RAM sticks), card_id (optional pin to a "
            "part_cards.row_id — manual component choice), note. "
            "Passing `slots` REPLACES the build's slot list, but existing slots are "
            "matched by row_id (or reused by type_id) and updated in place — their "
            "identity survives, so linked chats keep their mirror stable. "
            "Compatibility is checked automatically (socket / ram_type / "
            "form_factor domains): conflicts land in compat_ok / compat_issues. "
            "price_total / price_min / price_max / spec_summary / slots_count / "
            "unresolved_count are pipeline-owned — do NOT set them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "name": {"type": "string"},
                "build_kind": {"type": ["string", "null"], "enum": ["pc", "server", None]},
                "target_price_min": {"type": ["number", "integer", "null"]},
                "target_price_max": {"type": ["number", "integer", "null"]},
                "note": {"type": ["string", "null"]},
                "is_enabled": {"type": ["boolean", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
                "slots": {
                    "type": ["array", "null"],
                    "description": "Replaces the build's slot list when provided",
                    "items": {
                        "type": "object",
                        "properties": {
                            "row_id": {"type": "string"},
                            "type_id": {"type": "string"},
                            "qty": {"type": ["number", "integer"]},
                            "constraints": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            },
                            "card_id": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["type_id"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ready_build_attach",
        "description": (
            "Attach a ready build from the catalog to a customer position in THIS "
            "chat — creates a GLOBAL LINK (equipment_builds with ready_build_id), "
            "NOT a copy: the build's composition and prices mirror the catalog from "
            "now on (slot changes, price-leader drift, target corridor re-picks all "
            "flow into the order automatically on pipeline runs). "
            "The mirrored slot components appear as found_groups (owner_kind=build) "
            "and flow into «Найденные товары» / budget / procurement like any "
            "found product. "
            "Returns build_id, groups (per slot: slot_type_id, slot_id, group_id, "
            "part_number) and the pipeline stats. "
            "To change a component AFTER attaching, edit the READY build "
            "(ready_build_slot_upsert) — or create a variant build and attach it "
            "instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "ready_build_id": {
                    "type": "string",
                    "description": "ready_builds.row_id from ready_builds_list",
                },
                "line_id": {
                    "type": "string",
                    "description": "request_lines.row_id (customer position)",
                },
                "note": {"type": "string"},
            },
            "required": ["ready_build_id", "line_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ready_build_slot_upsert",
        "description": (
            "Create or patch ONE slot of a ready build (ready_build_slots) — use "
            "this to change a single slot WITHOUT resending the whole slots array "
            "(ready_build_upsert replaces the list). "
            "On create: build_id AND type_id (equipment_types.row_id) are required. "
            "constraints — {field: group value} from the type template (empty = any "
            "card of the type). card_id pins the slot to a part_cards.row_id "
            "(manual replacement; pick candidates with part_cards_list filtered by "
            "type_id + constraints). qty is a whole number >= 1. "
            "PATCH merges: omit = leave; null = clear (clearing card_id returns the "
            "slot to automatic selection)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "build_id": {
                    "type": "string",
                    "description": "ready_builds.row_id (required on create)",
                },
                "row_id": {
                    "type": "string",
                    "description": "ready_build_slots.row_id — patch an existing slot",
                },
                "type_id": {
                    "type": ["string", "null"],
                    "description": "equipment_types.row_id (required on create)",
                },
                "qty": {"type": ["number", "integer", "null"]},
                "constraints": {
                    "type": ["object", "null"],
                    "additionalProperties": {"type": "string"},
                },
                "card_id": {
                    "type": ["string", "null"],
                    "description": "part_cards.row_id (pinned component)",
                },
                "note": {"type": ["string", "null"]},
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
        # Маркер PRODAVAN_MCP_ENV_MISSING — контракт с API: по нему платформа
        # понимает, что под потерял проектный bind (env применяется только
        # через POST /v1/project/bind и живёт в памяти bridge), и сбрасывает
        # метку bind, чтобы следующий send перепривязал под. Менять текст
        # маркера можно только вместе с `_MCP_ENV_MISSING_MARKERS` в API.
        raise RuntimeError(
            "PRODAVAN_MCP_ENV_MISSING: PRODAVAN_API_BASE_URL, PRODAVAN_AUTH_TOKEN, "
            "and PRODAVAN_PROJECT_ID are required"
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
        if not (isinstance(line_id, str) and line_id.strip()):
            raise RuntimeError(
                "found_groups belongs to a request line: line_id = "
                "request_lines.row_id from request_lines_list. Build slots are "
                "mirror-managed by the platform — edit the ready build instead "
                "(ready_build_slot_upsert)"
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


def _validate_build_body(body: dict[str, Any], *, creating: bool) -> None:
    if creating:
        if not str(body.get("name") or "").strip():
            raise RuntimeError("name is required when creating equipment_builds")
        if not str(body.get("ready_build_id") or "").strip():
            raise RuntimeError(
                "ready_build_id is required when creating equipment_builds: a chat "
                "build is a LINK to a ready build (use ready_builds_list to pick, "
                "ready_build_upsert to create one, or ready_build_attach for a "
                "one-call attach to a position)"
            )
    if "build_kind" in body and body["build_kind"] is not None:
        if str(body["build_kind"]) not in BUILD_KINDS:
            raise RuntimeError(f"build_kind must be one of {sorted(BUILD_KINDS)}")


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


# ------------------------------------------------------- WAVE12: карточки и сборки

# Поля, отдаваемые агенту в компактных списках. Полный список тел съел бы
# контекст на десятках сборок, поэтому компактность — часть контракта.
_CATALOG_COMPACT_FIELDS: dict[str, tuple[str, ...]] = {
    "part_cards": (
        "type_id",
        "type_name",
        "part_number",
        "attrs",
        "attrs_label",
        "best_price",
        "best_seller",
        "best_title",
        "in_stock",
        "offers_count",
    ),
    "ready_builds": (
        "name",
        "build_kind",
        "spec_summary",
        "slots_count",
        "qty_total",
        "ram_sticks",
        "storage_drives",
        "price_total",
        "price_min",
        "price_max",
        "target_price_min",
        "target_price_max",
        "price_note",
        "on_order",
        "unresolved_count",
        "compat_ok",
        "compat_issues",
        "is_enabled",
    ),
    "ready_build_slots": (
        "build_id",
        "type_id",
        "type_name",
        "constraints",
        "constraints_label",
        "qty",
        "card_id",
        "resolved_card_id",
        "resolved_title",
        "resolved_part_number",
        "resolved_price",
        "resolved_seller",
        "in_stock",
        "line_total",
        "alternatives_count",
    ),
}


def _rows_body(row: dict[str, Any]) -> dict[str, Any]:
    body = row.get("body")
    return dict(body) if isinstance(body, dict) else {}


def _project_rows(
    rows: list[dict[str, Any]], table: str, *, full: bool
) -> list[dict[str, Any]]:
    """Сжимает строки до row_id + нужных полей (compact) или отдаёт тело целиком."""
    out: list[dict[str, Any]] = []
    keep = _CATALOG_COMPACT_FIELDS.get(table)
    for row in rows:
        body = _rows_body(row)
        item: dict[str, Any] = {"row_id": str(row.get("row_id") or "")}
        if full or keep is None:
            item["body"] = body
        else:
            for key in keep:
                if key in body:
                    item[key] = body[key]
        out.append(item)
    return out


def _norm_value(value: Any) -> str:
    return str(value or "").strip().casefold()


def _constraints_cover(attrs: Any, constraints: Any) -> bool:
    """attrs карточки покрывают ВСЕ constraint-поля (нормализация регистра)."""
    if not isinstance(constraints, dict) or not constraints:
        return True
    values = attrs if isinstance(attrs, dict) else {}
    for key, want in constraints.items():
        if _norm_value(want) == "":
            continue
        if _norm_value(values.get(key)) != _norm_value(want):
            return False
    return True


def _part_cards_list(mid: str, arguments: dict[str, Any], sid: str | None) -> Any:
    type_id = str(arguments.get("type_id") or "").strip()
    constraints = arguments.get("constraints")
    if constraints is not None and not isinstance(constraints, dict):
        raise RuntimeError("constraints must be an object {field_key: group value}")
    try:
        limit = max(1, min(int(arguments.get("limit") or 100), 500))
    except (TypeError, ValueError):
        limit = 100
    rows = _list_rows(mid, "part_cards", session_id=sid)
    filtered = []
    for row in rows:
        body = _rows_body(row)
        if type_id and str(body.get("type_id") or "") != type_id:
            continue
        if constraints and not _constraints_cover(body.get("attrs"), constraints):
            continue
        filtered.append(row)
    return {
        "total": len(filtered),
        "items": _project_rows(filtered[:limit], "part_cards", full=False),
    }


def _type_schema(mid: str, type_id: str, sid: str | None) -> dict[str, Any]:
    """Шаблон типа для валидации attrs: {field_key: {required, choices}}."""
    for row in _list_rows(mid, "equipment_types", session_id=sid):
        if str(row.get("row_id") or "") != type_id:
            continue
        fields: dict[str, Any] = {}
        raw = _rows_body(row).get("fields_json")
        for f in raw if isinstance(raw, list) else []:
            if not isinstance(f, dict):
                continue
            key = str(f.get("key") or "").strip()
            if not key:
                continue
            choices: set[str] = set()
            for v in f.get("values") if isinstance(f.get("values"), list) else []:
                if str(v).strip():
                    choices.add(str(v).strip())
            for g in f.get("groups") if isinstance(f.get("groups"), list) else []:
                gv = str(g.get("value") if isinstance(g, dict) else g or "").strip()
                if gv:
                    choices.add(gv)
            label = f.get("label")
            if isinstance(label, dict):
                label = str(label.get("ru") or label.get("en") or key)
            fields[key] = {
                "required": bool(f.get("required")),
                "choices": choices,
                "label": str(label or key),
            }
        return {"name": str(_rows_body(row).get("name") or type_id), "fields": fields}
    return {}


def _validate_card_body(
    body: dict[str, Any], *, creating: bool, mid: str, sid: str | None
) -> None:
    if creating:
        if not str(body.get("type_id") or "").strip():
            raise RuntimeError(
                "type_id is required when creating part_cards "
                "(use equipment_types.row_id from equipment_types_list)"
            )
        has_keys = any(
            str(body.get(k) or "").strip()
            for k in ("part_number", "aliases_pn", "aliases_hash")
        )
        if not has_keys:
            raise RuntimeError(
                "at least one of part_number / aliases_pn / aliases_hash is required: "
                "the card stores KEYS, the platform resolves prices from them"
            )
    if "attrs" in body and body["attrs"] is not None:
        attrs = body["attrs"]
        if not isinstance(attrs, dict):
            raise RuntimeError("attrs must be an object {field_key: value}")
        for key, value in attrs.items():
            if not isinstance(value, str):
                attrs[key] = str(value)
        # валидация по шаблону типа: обязательные поля + значения из групп
        type_id = str(body.get("type_id") or "").strip()
        if type_id:
            schema = _type_schema(mid, type_id, sid)
            fields = schema.get("fields") or {}
            if not fields:
                raise RuntimeError(
                    f"equipment_types row not found: {type_id} "
                    f"(see equipment_types_list)"
                )
            errors: list[str] = []
            if creating:
                for key, fdef in fields.items():
                    if fdef.get("required") and _norm_value(attrs.get(key)) == "":
                        errors.append(
                            f"required field '{key}' ({fdef.get('label')}) is missing "
                            f"in attrs"
                        )
            for key, value in attrs.items():
                fdef = fields.get(key)
                if fdef is None:
                    errors.append(
                        f"attrs key '{key}' is not declared in the type template"
                    )
                    continue
                choices = fdef.get("choices") or set()
                if choices and str(value).strip() not in choices:
                    allowed = ", ".join(sorted(choices)[:12])
                    errors.append(
                        f"attrs['{key}'] = {value!r} is not in the type's groups "
                        f"({allowed}…)"
                    )
            if errors:
                raise RuntimeError(
                    "part card does not fit the type template: " + "; ".join(errors)
                )


def _ready_builds_list(mid: str, arguments: dict[str, Any], sid: str | None) -> Any:
    build_kind = str(arguments.get("build_kind") or "").strip()
    only_enabled = arguments.get("only_enabled") is not False
    try:
        limit = max(1, min(int(arguments.get("limit") or 100), 500))
    except (TypeError, ValueError):
        limit = 100
    rows = _list_rows(mid, "ready_builds", session_id=sid)
    filtered = []
    for row in rows:
        body = _rows_body(row)
        if only_enabled and body.get("is_enabled") is False:
            continue
        if build_kind and str(body.get("build_kind") or "") != build_kind:
            continue
        filtered.append(row)
    return {
        "total": len(filtered),
        "items": _project_rows(filtered[:limit], "ready_builds", full=False),
    }


def _ready_build_get(mid: str, arguments: dict[str, Any], sid: str | None) -> Any:
    row_id = str(arguments.get("row_id") or "").strip()
    if not row_id:
        raise RuntimeError("row_id is required for ready_build_get")
    build = next(
        (r for r in _list_rows(mid, "ready_builds", session_id=sid)
         if str(r.get("row_id") or "") == row_id),
        None,
    )
    if build is None:
        raise RuntimeError(f"ready_builds row not found: {row_id}")
    slots = [
        r
        for r in _list_rows(mid, "ready_build_slots", session_id=sid)
        if str(_rows_body(r).get("build_id") or "") == row_id
    ]
    return {
        "build": _project_rows([build], "ready_builds", full=True)[0],
        "slots": _project_rows(slots, "ready_build_slots", full=False),
    }


def _upsert_row(
    mid: str,
    table: str,
    arguments: dict[str, Any],
    sid: str | None,
    *,
    keys: Sequence[str],
    required_on_create: Sequence[str] = (),
    validate: Any = None,
) -> Any:
    """Общий create/update строки каталога (PATCH-мерж: omit = leave, null = clear)."""
    body = _pick_present(arguments, keys)
    row_id = str(arguments.get("row_id") or "").strip() or None
    if validate is not None:
        validate(body, creating=not row_id)
    for field in required_on_create:
        if not row_id and not str(body.get(field) or "").strip():
            raise RuntimeError(f"{field} is required when creating {table}")
    if row_id:
        return _http(
            "PATCH", _data_path(mid, table, row_id), {"body": body}, session_id=sid
        )
    return _http("POST", _data_path(mid, table), {"body": body}, session_id=sid)


_SLOT_KEYS = (
    "type_id",
    "qty",
    "constraints",
    "card_id",
    "note",
)


def _validate_slot(slot: Any) -> dict[str, Any]:
    if not isinstance(slot, dict):
        raise RuntimeError("each slots entry must be an object")
    type_id = str(slot.get("type_id") or "").strip()
    if not type_id:
        raise RuntimeError("slots[].type_id is required (equipment_types.row_id)")
    constraints = slot.get("constraints")
    if constraints is not None:
        if not isinstance(constraints, dict):
            raise RuntimeError(
                "slots[].constraints must be an object {field_key: group value} "
                "from the type template (equipment_types_list → fields_json)"
            )
        for key, value in constraints.items():
            if not isinstance(value, str):
                constraints[key] = str(value)
    qty = slot.get("qty")
    if qty is not None:
        try:
            qty_num = float(qty)
        except (TypeError, ValueError):
            raise RuntimeError("slots[].qty must be a whole number >= 1") from None
        if qty_num < 1 or qty_num != int(qty_num):
            raise RuntimeError("slots[].qty must be a whole number >= 1")
    out = {k: slot[k] for k in _SLOT_KEYS if k in slot}
    if slot.get("row_id"):
        out["row_id"] = str(slot["row_id"]).strip()
    return out


def _ready_build_upsert(mid: str, arguments: dict[str, Any], sid: str | None) -> Any:
    """Сборка + её слоты одним вызовом: ИИ создаёт сборку за один ход, а не за N."""
    keys = (
        "name",
        "build_kind",
        "target_price_min",
        "target_price_max",
        "note",
        "is_enabled",
        "project_ids",
    )
    body = _pick_present(arguments, keys)
    row_id = str(arguments.get("row_id") or "").strip() or None
    _validate_build_meta(body, creating=not row_id)

    raw_slots = arguments.get("slots")
    slots: list[dict[str, Any]] | None = None
    if raw_slots is not None:
        if not isinstance(raw_slots, list):
            raise RuntimeError("slots must be an array")
        slots = [_validate_slot(s) for s in raw_slots]

    if row_id:
        result = _http(
            "PATCH", _data_path(mid, "ready_builds", row_id), {"body": body}, session_id=sid
        )
    else:
        result = _http(
            "POST", _data_path(mid, "ready_builds"), {"body": body}, session_id=sid
        )
    build_id = str(
        (result or {}).get("row_id") or row_id or ""
    ).strip()

    if slots is None:
        return {"build": result, "slots_replaced": False}
    if not build_id:
        raise RuntimeError("cannot replace slots: build row_id is unknown")

    # Слоты заменяются набором из вызова, но идентичность сохраняется: слот с
    # переданным row_id (или того же типа, первый свободный) патчится на месте,
    # а не пересоздаётся — зеркала чатов привязаны к row_id слотов.
    existing = [
        r
        for r in _list_rows(mid, "ready_build_slots", session_id=sid)
        if str(_rows_body(r).get("build_id") or "") == build_id
    ]
    existing_by_id = {str(r.get("row_id") or ""): r for r in existing}
    used: set[str] = set()
    written: list[Any] = []
    for slot in slots:
        slot_body = {k: v for k, v in slot.items() if k != "row_id"}
        target_id = str(slot.get("row_id") or "").strip()
        target = existing_by_id.get(target_id) if target_id else None
        if target is None:
            # переиспользование по типу: первый свободный слот того же type_id
            for cand in existing:
                cid = str(cand.get("row_id") or "")
                if cid in used:
                    continue
                if str(_rows_body(cand).get("type_id") or "") == slot_body.get("type_id"):
                    target = cand
                    target_id = cid
                    break
        if target is not None and target_id not in used:
            used.add(target_id)
            written.append(
                _http(
                    "PATCH",
                    _data_path(mid, "ready_build_slots", target_id),
                    {"body": slot_body},
                    session_id=sid,
                )
            )
            continue
        written.append(
            _http(
                "POST",
                _data_path(mid, "ready_build_slots"),
                {"body": {"build_id": build_id, **slot_body}},
                session_id=sid,
            )
        )
    deleted = 0
    for row in existing:
        old_id = str(row.get("row_id") or "")
        if old_id and old_id not in used:
            _http("DELETE", _data_path(mid, "ready_build_slots", old_id), session_id=sid)
            deleted += 1
    return {
        "build": result,
        "slots_replaced": True,
        "slots_written": len(written),
        "slots_deleted": deleted,
        "slots": written,
    }


def _ready_build_slot_upsert(mid: str, arguments: dict[str, Any], sid: str | None) -> Any:
    """Один слот сборки: создать или патчить, не перезаписывая остальные."""
    row_id = str(arguments.get("row_id") or "").strip() or None
    build_id = str(arguments.get("build_id") or "").strip()
    if not row_id and not build_id:
        raise RuntimeError("build_id is required when creating a ready_build_slots row")
    body = _pick_present(arguments, _SLOT_KEYS)
    if row_id is None:
        body = _validate_slot({**body, "build_id": build_id})
        body["build_id"] = build_id
        return _http(
            "POST",
            _data_path(mid, "ready_build_slots"),
            {"body": body},
            session_id=sid,
        )
    # патч: только переданные поля; qty приводим к целому ≥1
    if "qty" in body and body["qty"] is not None:
        try:
            qty_num = float(body["qty"])
        except (TypeError, ValueError):
            raise RuntimeError("qty must be a whole number >= 1") from None
        if qty_num < 1 or qty_num != int(qty_num):
            raise RuntimeError("qty must be a whole number >= 1")
    if "constraints" in body and body["constraints"] is not None:
        constraints = body["constraints"]
        if not isinstance(constraints, dict):
            raise RuntimeError("constraints must be an object {field_key: group value}")
        for key, value in constraints.items():
            if not isinstance(value, str):
                constraints[key] = str(value)
    return _http(
        "PATCH",
        _data_path(mid, "ready_build_slots", row_id),
        {"body": body},
        session_id=sid,
    )


def _validate_build_meta(body: dict[str, Any], *, creating: bool) -> None:
    if creating:
        if not str(body.get("name") or "").strip():
            raise RuntimeError("name is required when creating ready_builds")
    if "build_kind" in body and body["build_kind"] is not None:
        if str(body["build_kind"]) not in BUILD_KINDS:
            raise RuntimeError(f"build_kind must be one of {sorted(BUILD_KINDS)}")
    for field in ("target_price_min", "target_price_max"):
        if field in body and body[field] is not None:
            try:
                value = float(body[field])
            except (TypeError, ValueError):
                raise RuntimeError(f"{field} must be a number") from None
            if value < 0:
                raise RuntimeError(f"{field} must be >= 0")




def _ready_build_attach(mid: str, arguments: dict[str, Any], sid: str | None) -> Any:
    """Копирование готовой сборки в чат на позицию (серверное действие)."""
    ready_build_id = str(arguments.get("ready_build_id") or "").strip()
    line_id = str(arguments.get("line_id") or "").strip()
    if not ready_build_id:
        raise RuntimeError("ready_build_id is required for ready_build_attach")
    if not line_id:
        raise RuntimeError(
            "line_id is required for ready_build_attach (request_lines.row_id)"
        )
    _, _, project_id = _env()
    payload: dict[str, Any] = {"line_id": line_id}
    note = str(arguments.get("note") or "").strip()
    if note:
        payload["note"] = note
    return _http(
        "POST",
        f"/projects/{project_id}/modules/{mid}/actions/ready_build_attach/invoke",
        {"row_id": ready_build_id, "params": payload},
        session_id=sid,
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
            "ready_build_id",
            "line_id",
            "is_selected",
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

    # ------------------------------------------------- WAVE12: карточки и сборки
    if name == "part_cards_list":
        return _part_cards_list(mid, arguments, sid)
    if name == "part_card_upsert":
        return _upsert_row(
            mid,
            "part_cards",
            arguments,
            sid,
            keys=(
                "type_id",
                "part_number",
                "aliases_pn",
                "aliases_hash",
                "attrs",
                "note",
                "project_ids",
            ),
            required_on_create=("type_id",),
            validate=lambda body, creating: _validate_card_body(
                body, creating=creating, mid=mid, sid=sid
            ),
        )
    if name == "part_card_delete":
        row_id = str(arguments.get("row_id") or "").strip()
        if not row_id:
            raise RuntimeError("row_id is required for part_card_delete")
        return _http(
            "DELETE",
            _data_path(mid, "part_cards", row_id),
            session_id=sid,
        )
    if name == "ready_builds_list":
        return _ready_builds_list(mid, arguments, sid)
    if name == "ready_build_get":
        return _ready_build_get(mid, arguments, sid)
    if name == "ready_build_upsert":
        return _ready_build_upsert(mid, arguments, sid)
    if name == "ready_build_slot_upsert":
        return _ready_build_slot_upsert(mid, arguments, sid)
    if name == "ready_build_attach":
        return _ready_build_attach(mid, arguments, sid)

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
                "serverInfo": {"name": "prodavan-equipment", "version": "3.0.0"},
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
