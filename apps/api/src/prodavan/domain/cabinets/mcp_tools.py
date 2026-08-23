"""Platform cabinet.* MCP tool names and JSON-ish input shapes (L06)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    implemented: bool = True


# Stable names from mcp-contracts.md — implemented subset routes to application services.
PLATFORM_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="cabinet.info",
        description="Cabinet instance summary (name, schema, status).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="cabinet.tables.list",
        description="List active tables in meta catalog.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="cabinet.tables.create",
        description="Create table + controlled DDL for physical storage.",
        input_schema={
            "type": "object",
            "required": ["slug", "label", "columns"],
            "properties": {
                "slug": {"type": "string"},
                "label": {"type": "string"},
                "storage_kind": {"type": "string", "enum": ["physical", "json_document"]},
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "required": {"type": "boolean"},
                            "unique": {"type": "boolean"},
                            "ref_table_slug": {"type": "string"},
                        },
                    },
                },
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cabinet.tabs.list",
        description="List cabinet tabs (including Base system tabs).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="cabinet.rows.query",
        description="Query rows from a physical data table.",
        input_schema={
            "type": "object",
            "required": ["table_slug"],
            "properties": {
                "table_slug": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "offset": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cabinet.rows.upsert",
        description="Insert or update a row (values must match meta columns).",
        input_schema={
            "type": "object",
            "required": ["table_slug", "values"],
            "properties": {
                "table_slug": {"type": "string"},
                "values": {"type": "object"},
                "id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cabinet.rows.delete",
        description="Delete a row by id.",
        input_schema={
            "type": "object",
            "required": ["table_slug", "id"],
            "properties": {
                "table_slug": {"type": "string"},
                "id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cabinet.bundle.export",
        description="Export cabinet as cabinet.bundle-v1.zip (base64).",
        input_schema={
            "type": "object",
            "properties": {"include_data": {"type": "boolean"}},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cabinet.bundle.import",
        description="Import bundle zip → new CabinetInstance + new schema.",
        input_schema={
            "type": "object",
            "required": ["company_id", "zip_base64"],
            "properties": {
                "company_id": {"type": "string"},
                "zip_base64": {"type": "string"},
                "name": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cabinet.mcp_packages.deploy",
        description="Validate and deploy mcp.package-v1.zip into cabinet registry.",
        input_schema={
            "type": "object",
            "required": ["zip_base64"],
            "properties": {
                "zip_base64": {"type": "string"},
                "replace_if_name": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cabinet.mcp_packages.list",
        description="List MCP packages in cabinet registry.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="cabinet.mcp_packages.disable",
        description="Soft-disable a package by name.",
        input_schema={
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="cabinet.mcp_packages.export",
        description="Export a package zip as base64.",
        input_schema={
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
)

TOOL_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in PLATFORM_TOOLS}

# Hard bans — never expose freeform SQL / execute tools
BANNED_TOOL_PREFIXES: frozenset[str] = frozenset(
    {
        "cabinet.sql",
        "cabinet.execute",
        "cabinet.query_raw",
        "cabinet.ddl",
    }
)
