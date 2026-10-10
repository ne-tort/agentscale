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
# WAVE11: каталог «Готовые сборки»
BUDGET_TIERS = frozenset({"budget", "mid", "high"})
SLOT_MODES = frozenset({"dynamic", "fixed"})


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
            "A group belongs EITHER to a request line (line_id) OR to a build slot "
            "(build_id + slot_type_id) — slot groups have NO line_id, so filtering by "
            "line_id returns nothing for a build. To inspect the components of a build "
            "(e.g. the copy made by ready_build_attach) filter by build_id."
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
                        "Optional filter: equipment_builds.row_id — returns the build's "
                        "slot groups (owner_kind=build). This is how you read the "
                        "components of a build copy before pinning a part number or "
                        "adding an alias to a slot."
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
    {
        "name": "ready_builds_catalog",
        "description": (
            "THE read tool for the «Готовые сборки» catalog (WAVE11) — a reusable, "
            "price-auto-updating library of PC/server build templates. "
            "ALWAYS prefer picking a ready build from here over assembling a PC from "
            "scratch: the catalog already encodes verified compatibility. "
            "scope: 'groups' (compatibility domains: socket / ram_type / budget tier), "
            "'items' (group component pool: part numbers + class_key + resolved best "
            "price), 'builds' (ready builds with totals), 'build' (ONE build with all "
            "its slots — pass row_id). "
            "detail: 'compact' (default — ids, names, class, price; use for browsing "
            "dozens of builds) or 'full' (all fields incl. keys and notes). "
            "Filters: group_id, build_id, type_id, class_key, build_kind, budget_tier, "
            "only_enabled. The catalog stores KEYS (part number / aliases / hash), not "
            "supplier offers: prices are re-resolved from OpenSearch automatically, and "
            "dynamic slots may drift to a cheaper compatible component of the same class."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "scope": {
                    "type": "string",
                    "enum": ["groups", "items", "builds", "build"],
                    "default": "builds",
                },
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
                "row_id": {
                    "type": "string",
                    "description": "Required for scope=build",
                },
                "group_id": {"type": "string"},
                "build_id": {"type": "string"},
                "type_id": {"type": "string"},
                "class_key": {"type": "string"},
                "build_kind": {"type": "string", "enum": ["pc", "server"]},
                "budget_tier": {"type": "string", "enum": ["budget", "mid", "high"]},
                "only_enabled": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "build_group_upsert",
        "description": (
            "Create or update a build group (build_groups) — a COMPATIBILITY DOMAIN, "
            "not a convenience folder: inside one group components are interchangeable. "
            "socket / ram_type / form_factor are the compatibility anchors; budget_tier "
            "(budget|mid|high) is a layer ON TOP of them, never a substitute — never put "
            "AM4 and LGA1700 into one group. On create: name is required. "
            "PATCH merges: omit = leave; null = clear."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "name": {"type": "string"},
                "build_kind": {"type": ["string", "null"], "enum": ["pc", "server", None]},
                "socket": {"type": ["string", "null"]},
                "ram_type": {"type": ["string", "null"]},
                "form_factor": {"type": ["string", "null"]},
                "budget_tier": {
                    "type": ["string", "null"],
                    "enum": ["budget", "mid", "high", None],
                },
                "note": {"type": ["string", "null"]},
                "is_enabled": {"type": ["boolean", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "build_group_item_upsert",
        "description": (
            "Create or update one component alternative in a group's pool "
            "(build_group_items). The pool holds KEYS, not supplier offers: "
            "part_number + aliases_pn (other spellings, comma-separated) + aliases_hash "
            "(src_hash ids from equipment_catalog_search for positions WITHOUT a P/N). "
            "At least one key is required on create. "
            "class_key is MANDATORY in practice — it is what stops incompatible "
            "substitution: 'ram_16' and 'ram_32', 'ssd_256' and 'ssd_512', 'hdd_1000' "
            "are DIFFERENT classes and never compete by price. Use stable snake_case "
            "(type + the distinguishing spec). class_label is its human-readable form. "
            "The platform resolves best_price / best_seller / in_stock / "
            "is_cheapest_in_class from OpenSearch — do NOT set them. "
            "qty_default = how many of this component a build typically needs "
            "(e.g. 2 for RAM sticks)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "group_id": {
                    "type": ["string", "null"],
                    "description": "build_groups.row_id (required on create)",
                },
                "type_id": {
                    "type": ["string", "null"],
                    "description": "equipment_types.row_id (required on create)",
                },
                "class_key": {"type": ["string", "null"]},
                "class_label": {"type": ["string", "null"]},
                "part_number": {"type": ["string", "null"]},
                "aliases_pn": {"type": ["string", "null"]},
                "aliases_hash": {"type": ["string", "null"]},
                "qty_default": {"type": ["number", "integer", "null"]},
                "note": {"type": ["string", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ready_build_upsert",
        "description": (
            "Create or update a ready build (ready_builds) TOGETHER WITH its slots in "
            "ONE call — pass `slots` as an array; existing slots of the build are "
            "replaced by it (omit `slots` to leave them untouched). "
            "On create: group_id and name are required. "
            "Class attributes (cpu_cores, ram_gb, storage_kind, storage_gb, gpu_class) "
            "are what a customer request is matched against ('budget PC, 6 cores, "
            "256 GB SSD, 16 GB RAM') and what forms class_signature — two builds with "
            "different RAM are DIFFERENT showcases and never compete by price, so fill "
            "them accurately. "
            "Each slot: type_id (equipment_types.row_id, required), qty (default 1), "
            "mode ('dynamic' = take the cheapest available component of class_key, may "
            "drift as prices change; 'fixed' = pinned to item_id, price changes but the "
            "component does not), class_key (which pool class to draw from), item_id "
            "(build_group_items.row_id — the pinned/default component), or own "
            "part_number/aliases_pn/aliases_hash when the component is not in the pool, "
            "plus note. "
            "price_total / slots_count / is_cheapest_in_class / unresolved_count are "
            "computed by the platform — do NOT set them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "row_id": {"type": "string"},
                "group_id": {
                    "type": ["string", "null"],
                    "description": "build_groups.row_id (required on create)",
                },
                "name": {"type": "string"},
                "build_kind": {"type": ["string", "null"], "enum": ["pc", "server", None]},
                "budget_tier": {
                    "type": ["string", "null"],
                    "enum": ["budget", "mid", "high", None],
                },
                "cpu_cores": {"type": ["number", "integer", "null"]},
                "ram_gb": {"type": ["number", "integer", "null"]},
                "storage_kind": {"type": ["string", "null"]},
                "storage_gb": {"type": ["number", "integer", "null"]},
                "gpu_class": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
                "is_enabled": {"type": ["boolean", "null"]},
                "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
                "slots": {
                    "type": ["array", "null"],
                    "description": "Replaces the build's slot list when provided",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type_id": {"type": "string"},
                            "qty": {"type": ["number", "integer"]},
                            "mode": {"type": "string", "enum": ["dynamic", "fixed"]},
                            "class_key": {"type": "string"},
                            "item_id": {"type": "string"},
                            "part_number": {"type": "string"},
                            "aliases_pn": {"type": "string"},
                            "aliases_hash": {"type": "string"},
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
            "Copy a ready build from the catalog into THIS chat as a working build for a "
            "customer position: creates equipment_builds (line_id = the position) plus "
            "one found_groups candidate per slot, then materializes offers. "
            "KEYS are copied, not prices — the copy immediately lives its own life and "
            "pulls current offers through the normal pipeline, so budget and procurement "
            "work exactly as for a build made from scratch. "
            "After this call, work with the COPY (equipment_builds / found_groups): "
            "you may pin a part number for a slot, add an alias or src_hash of a product "
            "you found, or change a quantity. The catalog itself is not modified — it is "
            "the shop window, the copy belongs to the request. "
            "Returns build_id, groups_created, groups (per slot: slot_type_id, group_id, "
            "part_number, qty — use these group_id values to inspect or pin the copy's "
            "components), slot_qty and slots_skipped_no_keys "
            "(slots whose component had no keys — tell the manager about those)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_id": {"type": "string", "default": DEFAULT_MODULE_ID},
                "ready_build_id": {
                    "type": "string",
                    "description": "ready_builds.row_id from ready_builds_catalog",
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


# ------------------------------------------------------- WAVE11: каталог сборок

# Поля, отдаваемые агенту в compact-режиме. Полный список тел съел бы контекст
# на десятках сборок, поэтому компактность — часть контракта инструмента.
_CATALOG_COMPACT_FIELDS: dict[str, tuple[str, ...]] = {
    "build_groups": (
        "name",
        "build_kind",
        "socket",
        "ram_type",
        "budget_tier",
        "items_count",
        "builds_count",
        "price_min",
        "price_max",
        "is_enabled",
    ),
    "build_group_items": (
        "group_id",
        "type_id",
        "type_name",
        "class_key",
        "class_label",
        "part_number",
        "best_price",
        "best_seller",
        "in_stock",
        "offers_count",
        "is_cheapest_in_class",
        "qty_default",
    ),
    "ready_builds": (
        "group_id",
        "name",
        "build_kind",
        "budget_tier",
        "cpu_cores",
        "ram_gb",
        "storage_kind",
        "storage_gb",
        "gpu_class",
        "slots_count",
        "qty_total",
        "price_total",
        "price_min",
        "price_max",
        "on_order",
        "unresolved_count",
        "is_cheapest_in_class",
        "alternatives_count",
        "is_enabled",
    ),
    "ready_build_slots": (
        "build_id",
        "type_id",
        "type_name",
        "qty",
        "mode",
        "class_key",
        "item_id",
        "resolved_title",
        "resolved_part_number",
        "resolved_price",
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


def _ready_builds_catalog(
    mid: str, arguments: dict[str, Any], sid: str | None
) -> dict[str, Any]:
    """Чтение каталога с уровнями компактности и фильтрами.

    Отдельный инструмент (а не generic module_data_list) потому, что каталог
    рассчитан на десятки-сотни сборок: без compact-режима и фильтров агент
    утонет в контексте раньше, чем выберет сборку.
    """
    scope = str(arguments.get("scope") or "builds").strip() or "builds"
    full = str(arguments.get("detail") or "compact").strip().lower() == "full"
    only_enabled = arguments.get("only_enabled") is not False
    try:
        limit = max(1, min(int(arguments.get("limit") or 100), 500))
    except (TypeError, ValueError):
        limit = 100

    group_id = str(arguments.get("group_id") or "").strip()
    build_id = str(arguments.get("build_id") or "").strip()
    type_id = str(arguments.get("type_id") or "").strip()
    class_key = str(arguments.get("class_key") or "").strip()
    build_kind = str(arguments.get("build_kind") or "").strip()
    budget_tier = str(arguments.get("budget_tier") or "").strip()

    def _matches(body: dict[str, Any]) -> bool:
        if only_enabled and body.get("is_enabled") is False:
            return False
        for field, want in (
            ("group_id", group_id),
            ("build_id", build_id),
            ("type_id", type_id),
            ("class_key", class_key),
            ("build_kind", build_kind),
            ("budget_tier", budget_tier),
        ):
            if want and str(body.get(field) or "") != want:
                return False
        return True

    table = {
        "groups": "build_groups",
        "items": "build_group_items",
        "builds": "ready_builds",
        "build": "ready_builds",
    }.get(scope)
    if table is None:
        raise RuntimeError(
            "scope must be one of groups | items | builds | build"
        )

    rows = _list_rows(mid, table, session_id=sid)
    if scope == "build":
        row_id = str(arguments.get("row_id") or "").strip()
        if not row_id:
            raise RuntimeError("row_id is required for scope=build")
        build = next(
            (r for r in rows if str(r.get("row_id") or "") == row_id), None
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
            "slots": _project_rows(slots, "ready_build_slots", full=full),
        }

    filtered = [r for r in rows if _matches(_rows_body(r))]
    items = _project_rows(filtered[:limit], table, full=full)
    return {
        "scope": scope,
        "detail": "full" if full else "compact",
        "total": len(filtered),
        "returned": len(items),
        "items": items,
    }


def _validate_group_meta(body: dict[str, Any], *, creating: bool) -> None:
    if creating and not str(body.get("name") or "").strip():
        raise RuntimeError("name is required when creating build_groups")
    if "build_kind" in body and body["build_kind"] is not None:
        if str(body["build_kind"]) not in BUILD_KINDS:
            raise RuntimeError(f"build_kind must be one of {sorted(BUILD_KINDS)}")
    if "budget_tier" in body and body["budget_tier"] is not None:
        if str(body["budget_tier"]) not in BUDGET_TIERS:
            raise RuntimeError(f"budget_tier must be one of {sorted(BUDGET_TIERS)}")


def _validate_pool_item(body: dict[str, Any], *, creating: bool) -> None:
    if creating:
        for field in ("group_id", "type_id"):
            if not str(body.get(field) or "").strip():
                raise RuntimeError(
                    f"{field} is required when creating build_group_items"
                )
        has_keys = any(
            str(body.get(k) or "").strip()
            for k in ("part_number", "aliases_pn", "aliases_hash")
        )
        if not has_keys:
            raise RuntimeError(
                "at least one of part_number / aliases_pn / aliases_hash is required: "
                "the pool stores KEYS, the platform resolves prices from them"
            )
        if not str(body.get("class_key") or "").strip():
            raise RuntimeError(
                "class_key is required (e.g. ram_16, ssd_256): it is what stops "
                "incompatible substitution — different classes never compete by price"
            )
    if "qty_default" in body and body["qty_default"] is not None:
        try:
            qty = float(body["qty_default"])
        except (TypeError, ValueError):
            raise RuntimeError("qty_default must be a number >= 1") from None
        if qty < 1:
            raise RuntimeError("qty_default must be >= 1")


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
    "mode",
    "class_key",
    "item_id",
    "part_number",
    "aliases_pn",
    "aliases_hash",
    "note",
)


def _validate_slot(slot: Any) -> dict[str, Any]:
    if not isinstance(slot, dict):
        raise RuntimeError("each slots entry must be an object")
    type_id = str(slot.get("type_id") or "").strip()
    if not type_id:
        raise RuntimeError("slots[].type_id is required (equipment_types.row_id)")
    mode = str(slot.get("mode") or "dynamic").strip() or "dynamic"
    if mode not in SLOT_MODES:
        raise RuntimeError(f"slots[].mode must be one of {sorted(SLOT_MODES)}")
    if mode == "fixed" and not (
        str(slot.get("item_id") or "").strip()
        or any(str(slot.get(k) or "").strip() for k in ("part_number", "aliases_pn", "aliases_hash"))
    ):
        raise RuntimeError(
            "slots[].mode='fixed' needs item_id (pool component) or own "
            "part_number / aliases_pn / aliases_hash to pin"
        )
    if mode == "dynamic" and not (
        str(slot.get("class_key") or "").strip()
        or str(slot.get("item_id") or "").strip()
        or any(str(slot.get(k) or "").strip() for k in ("part_number", "aliases_pn", "aliases_hash"))
    ):
        raise RuntimeError(
            "slots[].mode='dynamic' needs class_key (to draw the cheapest of the "
            "class), item_id, or own part_number / aliases_pn / aliases_hash"
        )
    qty = slot.get("qty")
    if qty is not None:
        try:
            qty_num = float(qty)
        except (TypeError, ValueError):
            raise RuntimeError("slots[].qty must be a whole number >= 1") from None
        if qty_num < 1 or qty_num != int(qty_num):
            raise RuntimeError("slots[].qty must be a whole number >= 1")
    return {k: slot[k] for k in _SLOT_KEYS if k in slot}


def _ready_build_upsert(mid: str, arguments: dict[str, Any], sid: str | None) -> Any:
    """Сборка + её слоты одним вызовом: ИИ создаёт сборку за один ход, а не за N."""
    keys = (
        "group_id",
        "name",
        "build_kind",
        "budget_tier",
        "cpu_cores",
        "ram_gb",
        "storage_kind",
        "storage_gb",
        "gpu_class",
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

    # слоты заменяются набором из вызова: удаляем прежние, пишем новые
    existing = [
        r
        for r in _list_rows(mid, "ready_build_slots", session_id=sid)
        if str(_rows_body(r).get("build_id") or "") == build_id
    ]
    for row in existing:
        old_id = str(row.get("row_id") or "")
        if old_id:
            _http("DELETE", _data_path(mid, "ready_build_slots", old_id), session_id=sid)
    created: list[Any] = []
    for slot in slots:
        created.append(
            _http(
                "POST",
                _data_path(mid, "ready_build_slots"),
                {"body": {"build_id": build_id, **slot}},
                session_id=sid,
            )
        )
    return {"build": result, "slots_replaced": True, "slots": created}


def _validate_build_meta(body: dict[str, Any], *, creating: bool) -> None:
    if creating:
        if not str(body.get("group_id") or "").strip():
            raise RuntimeError(
                "group_id is required when creating ready_builds "
                "(use build_groups.row_id from ready_builds_catalog scope=groups)"
            )
        if not str(body.get("name") or "").strip():
            raise RuntimeError("name is required when creating ready_builds")
    if "build_kind" in body and body["build_kind"] is not None:
        if str(body["build_kind"]) not in BUILD_KINDS:
            raise RuntimeError(f"build_kind must be one of {sorted(BUILD_KINDS)}")
    if "budget_tier" in body and body["budget_tier"] is not None:
        if str(body["budget_tier"]) not in BUDGET_TIERS:
            raise RuntimeError(f"budget_tier must be one of {sorted(BUDGET_TIERS)}")


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
        # Владелец группы проставляется сразу: дефолт колонки "line", поэтому
        # группа слота сборки до прогона пайплайна мелькала бы в общем списке
        # «Найденные товары» (он фильтрует по owner_kind = line).
        if "build_id" in body:
            bid = body.get("build_id")
            body["owner_kind"] = "build" if isinstance(bid, str) and bid.strip() else "line"
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

    # ------------------------------------------------- WAVE11: каталог сборок
    if name == "ready_builds_catalog":
        return _ready_builds_catalog(mid, arguments, sid)
    if name == "build_group_upsert":
        return _upsert_row(
            mid,
            "build_groups",
            arguments,
            sid,
            keys=(
                "name",
                "build_kind",
                "socket",
                "ram_type",
                "form_factor",
                "budget_tier",
                "note",
                "is_enabled",
                "project_ids",
            ),
            required_on_create=("name",),
            validate=_validate_group_meta,
        )
    if name == "build_group_item_upsert":
        return _upsert_row(
            mid,
            "build_group_items",
            arguments,
            sid,
            keys=(
                "group_id",
                "type_id",
                "class_key",
                "class_label",
                "part_number",
                "aliases_pn",
                "aliases_hash",
                "qty_default",
                "note",
                "project_ids",
            ),
            required_on_create=("group_id", "type_id"),
            validate=_validate_pool_item,
        )
    if name == "ready_build_upsert":
        return _ready_build_upsert(mid, arguments, sid)
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
                "serverInfo": {"name": "prodavan-equipment", "version": "2.4.1"},
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
