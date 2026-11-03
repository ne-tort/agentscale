"""Acceptance fixtures for MIG-MODULE-01; intentionally not executed during freeze."""
from __future__ import annotations

from typing import Any

from prodavan.application.migration.module_capabilities.contracts import (
    CapabilityDefinition,
    CapabilityVersion,
    DefinitionLifecycle,
    FieldDefinition,
    FieldType,
    ModuleMetadata,
    ModuleRelation,
    ModuleSchema,
    OwnerScope,
    PublicationGateEvidence,
    ScopeKind,
    VersionLifecycle,
    canonical_digest,
)

TENANT = "tenant-module"
DEFINITION_ID = "definition-module"
MODULE_ID = "mod_example"
OWNER = OwnerScope(TENANT, ScopeKind.TENANT, TENANT, "owner-evidence")
SCHEMA_ID = "schema:mod_example:items"
FIELD_ID = "field:mod_example:items:name"


def base_slug_map() -> dict[str, Any]:
    return {
        "tables": [{"slug": "items", "label": "Items", "storage_kind": "json_document", "enabled": True}],
        "columns": [{"table_slug": "items", "name": "name", "type": "text", "required": True}],
        "views": [{"slug": "items_list", "table_slug": "items", "kind": "collection", "ui_json": {"kind": "collection"}}],
        "tabs": [{"id": "tab_items", "view_slug": "items_list", "table_slug": "items", "order": 1, "enabled": True}],
        "actions": [{"id": "list_items", "kind": "data.select_row", "enabled": True, "params": {"table_slug": "items"}, "trigger": {"on": []}}],
        "mcp_tools": [{"id": "items_list_legacy", "name": "items_list", "server": "example", "kind": "rows_query", "enabled": True, "params_schema": {"type": "object", "properties": {}}, "implementation": {"table_slug": "items", "query": {"limit": 50}}}],
        "materialize": [{"id": "items_manifest", "enabled": True, "when": ["project.sync"], "source": {"type": "rows", "table_slug": "items"}, "target": {"workspace_path": "items.json", "format": "json_rows"}}],
        "seed_rows": {"items": [{"table_slug": "items", "row_id": "item-1", "body": {"name": "one"}}]},
                "mcp_aliases": [{"server": "example", "tool": "items_list", "label": "Items"}],
        "container_env": [],
        "container_env_secrets": [],
    }


def normalize_kwargs(slug_map: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "module_id": MODULE_ID,
        "definition_id": DEFINITION_ID,
        "version": "1.0.0",
        "source_digest": "sha256:" + "a" * 64,
        "slug_map": slug_map or base_slug_map(),
        "schema_ids": {"items": SCHEMA_ID},
        "field_ids": {("items", "name"): FIELD_ID, ("items", "row_id"): "field:items:row_id"},
        "view_ids": {"items_list": "view:items:list"},
        "action_operation_ids": {"list_items": "operation:rows.select"},
        "mcp_server_ids": {"example": "server:example"},
        "mcp_tool_ids": {"items_list": "tool:items:list"},
        "mcp_manifest": {"mcpServers": {"example": {"url": "https://mcp.invalid"}}},
    }


def definition(*, lifecycle: DefinitionLifecycle = DefinitionLifecycle.DRAFT, owner: OwnerScope | None = OWNER, current_version: str | None = None) -> CapabilityDefinition:
    return CapabilityDefinition(DEFINITION_ID, MODULE_ID, "Example module", lifecycle, owner, current_version)


def schema() -> ModuleSchema:
    return ModuleSchema(SCHEMA_ID, "items", "Items", (FieldDefinition(FIELD_ID, SCHEMA_ID, "name", FieldType.TEXT, True),))


def version(*, module_id: str = MODULE_ID, lifecycle: VersionLifecycle = VersionLifecycle.DRAFT, dependencies=(), conflicts=()) -> CapabilityVersion:
    payload = {
        "definition_id": DEFINITION_ID,
        "module_id": module_id,
        "version": "1.0.0",
        "schema_version": "module-meta-v1",
        "metadata": ModuleMetadata(),
        "schemas": (schema(),),
        "actions": (),
        "mcp_servers": (),
        "mcp_tools": (),
        "materialize": (),
        "dependencies": tuple(ModuleRelation(item) for item in dependencies),
        "conflicts": tuple(ModuleRelation(item) for item in conflicts),
    }
    return CapabilityVersion(**payload, content_digest=canonical_digest(payload), lifecycle=lifecycle)


def gate(*, ready: bool = True, version: str = "1.0.0") -> PublicationGateEvidence:
    return PublicationGateEvidence(
        definition_id=DEFINITION_ID,
        module_id=MODULE_ID,
        version=version,
        artifact_digest=version_digest(),
        owner_scope=OWNER,
        owner_evidence_id="owner-proof",
        version_evidence_id="version-proof",
        scope_evidence_id="scope-proof",
        transaction_id="publication-tx",
        actor_id="operator",
        decision_owner="migration-coordinator",
        writer_generation=1,
        correlation_id="correlation-module-1",
        owner_durable=ready,
        version_durable=ready,
        scope_durable=ready,
        artifact_immutable=ready,
        dependency_graph_valid=ready,
        dependency_graph_evidence_id="dependency-graph-proof",
        dependency_graph_digest=canonical_digest({"dependencies": [], "conflicts": []}),
    )


def version_digest() -> str:
    return version().content_digest
