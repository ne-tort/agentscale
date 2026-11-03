"""Strict normalization of module authoring documents to typed snapshots.

Legacy documents are input only. Every unsupported or lossy shape is rejected
with stable quarantine reason codes; no compatibility write path is provided.
"""
from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC, Sequence
from typing import Any, Mapping

from .contracts import (
    ActionDeclaration,
    CapabilityVersion,
    DefinitionLifecycle,
    DefinitionNormalizationResult,
    CapabilityDefinition,
    FieldDefinition,
    FieldType,
    MaterializeDeclaration,
    MaterializeFormat,
    MaterializeSource,
    McpAliasDeclaration,
    McpServerDeclaration,
    McpToolDeclaration,
    ModuleMetadata,
    ModuleRelation,
    ModuleSchema,
    NavigationDeclaration,
    NormalizationIssue,
    NormalizationResult,
    SchemaReference,
    SeedRowDeclaration,
    VersionLifecycle,
    ViewDeclaration,
    ViewKind,
    OwnerScope,
    canonical_digest,
)

_SUPPORTED_META = frozenset({
    "tables", "columns", "views", "tabs", "actions", "materialize", "seed_rows",
    "mcp_tools", "mcp_aliases", "materialize_roots", "container_env", "container_env_secrets",
})
_SUPPORTED_ACTIONS = frozenset({
    "data.create_row", "data.delete_row", "data.select_row", "content.index_tabular",
    "content.index_opensearch", "content.probe_remote_sql", "content.list_remote_sql_tables",
    "content.list_remote_sql_databases", "documents.convert", "documents.fill_template",
    "equipment.budget_sync", "equipment.match_to_line", "equipment.master_price",
    "equipment.pipeline", "equipment.offers_refresh", "equipment.ready_builds",
    "equipment.attach_ready_build", "equipment.tender_take_to_work", "equipment.tender_attach",
    "equipment.mail_send", "equipment.master_price_mail", "equipment.procurement_apply",
    "equipment.budget_export", "equipment.kp_export", "equipment.spec_export",
})
_SUPPORTED_MCP = frozenset({"rows_query", "rows_upsert", "rows_delete"})
_SUPPORTED_EVENTS = frozenset({"project.created", "project.resumed", "project.sync"})
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SECRET_MARKERS = ("password", "secret", "token", "api_key", "private_key")
_SECRET_DESCRIPTOR_SUFFIXES = ("_column", "_field", "_name", "_key", "_id")


class _Quarantine(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(message)
        self.issue = NormalizationIssue(code, path, message)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, MappingABC) or any(not isinstance(key, str) for key in value):
        raise _Quarantine("expected_object", path, "expected object with string keys")
    return value


def _array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise _Quarantine("expected_array", path, "expected array")
    return value


def _text(value: object, path: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _Quarantine("invalid_string", path, "expected non-empty string")
    return value.strip()


def _unique(items: Sequence[Any], path: str, identity, code: str) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items):
        key = identity(item)
        if key in seen:
            raise _Quarantine(code, f"{path}[{index}]", f"duplicate identity: {key}")
        seen.add(key)


def _reject_secrets(value: object, path: str = "module") -> None:
    if isinstance(value, MappingABC):
        for key, item in value.items():
            name = str(key).lower()
            marked = any(marker in name for marker in _SECRET_MARKERS)
            external = (
                isinstance(item, str) and item.startswith(("vault://", "secret://", "file://", "${"))
            ) or (
                isinstance(item, MappingABC)
                and bool(item.get("provider"))
                and bool(item.get("locator") or item.get("path"))
            )
            if (marked and not external
                    and not name.endswith(_SECRET_DESCRIPTOR_SUFFIXES)
                    and not (name.endswith("_secrets") and isinstance(item, list))):
                raise _Quarantine("inline_secret_forbidden", f"{path}.{key}", "secret values are forbidden; use a reference")
            if not (isinstance(item, MappingABC) and external):
                _reject_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.lower().startswith(("password=", "secret=", "token=", "api_key=")):
        raise _Quarantine("inline_secret_forbidden", path, "inline credential material is forbidden")


def _json_schema(value: object, path: str) -> Mapping[str, object]:
    schema = _mapping(value, path)
    if schema.get("type") != "object":
        raise _Quarantine("unsupported_json_schema", path, "only object parameter schemas are supported")
    properties = schema.get("properties", {})
    _mapping(properties, f"{path}.properties")
    allowed = {"type", "properties", "required", "additionalProperties", "description", "title"}
    unknown = set(schema) - allowed
    if unknown:
        raise _Quarantine("unsupported_json_schema_keyword", path, f"unsupported schema keys: {sorted(unknown)}")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise _Quarantine("unsupported_json_schema", f"{path}.required", "required must be an array of strings")
    if any(not isinstance(prop, MappingABC) or set(prop) - {"type", "description", "enum", "items", "format", "minimum", "maximum"} for prop in properties.values()):
        raise _Quarantine("unsupported_json_schema", f"{path}.properties", "unsupported property schema shape")
    return dict(schema)


def _field_default_matches(field_type: FieldType, value: object) -> bool:
    if value is None:
        return True
    if field_type is FieldType.TEXT:
        return isinstance(value, str)
    if field_type is FieldType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type is FieldType.BOOL:
        return isinstance(value, bool)
    if field_type in {FieldType.DATETIME, FieldType.REF}:
        return isinstance(value, str)
    if field_type is FieldType.JSON:
        return isinstance(value, (MappingABC, list))
    if field_type is FieldType.ENUM:
        return isinstance(value, (str, int, float, bool))
    if field_type in {FieldType.FILE_REF, FieldType.SECRET_REF}:
        return isinstance(value, MappingABC)
    return False


def _normalize_materialize(
    rule: object,
    index: int,
    schemas_by_slug: Mapping[str, ModuleSchema],
    field_ids: Mapping[tuple[str, str], str],
) -> MaterializeDeclaration:
    path = f"materialize[{index}]"
    item = _mapping(rule, path)
    allowed = {"id", "enabled", "when", "priority", "source", "target"}
    if set(item) - allowed:
        raise _Quarantine("unsupported_materialize_shape", path, f"unsupported keys: {sorted(set(item) - allowed)}")
    source = _mapping(item.get("source"), f"{path}.source")
    target = _mapping(item.get("target"), f"{path}.target")
    source_type = source.get("type", "row")
    try:
        source_kind = MaterializeSource(source_type)
        output_format = MaterializeFormat(target.get("format", "raw"))
    except (ValueError, TypeError) as exc:
        raise _Quarantine("unsupported_materialize_type", path, "unsupported source type or output format") from exc
    source_allowed = {"type", "table_slug", "slug", "value", "text", "filter"}
    target_allowed = {"workspace_path", "format", "template", "field"}
    if set(source) - source_allowed or set(target) - target_allowed:
        raise _Quarantine("unsupported_materialize_shape", path, "unknown source or target properties")
    source_slug: str | None = None
    source_schema: ModuleSchema | None = None
    if source_kind in {MaterializeSource.ROW, MaterializeSource.ROWS}:
        source_slug = _text(source.get("table_slug"), f"{path}.source.table_slug")
        source_schema = schemas_by_slug.get(source_slug or "")
        if source_schema is None:
            raise _Quarantine("unknown_materialize_schema", f"{path}.source.table_slug", f"unknown schema {source_slug}")
        source_schema_id = source_schema.schema_id
        source_doc_slug = None
    elif source_kind is MaterializeSource.META_DOCUMENT:
        source_doc_slug = _text(source.get("slug"), f"{path}.source.slug")
        source_schema_id = None
    else:
        if "value" not in source and "text" not in source:
            raise _Quarantine("missing_materialize_static_value", f"{path}.source", "static source requires value or text")
        source_schema_id = None
        source_doc_slug = None
    raw_filter = source.get("filter", {})
    source_filter = _mapping(raw_filter, f"{path}.source.filter")
    source_filter_field_ids: dict[str, str] = {}
    if source_filter:
        if source_schema is None:
            raise _Quarantine("filter_without_schema", f"{path}.source.filter", "filters require a row source schema")
        for filter_name in source_filter:
            field = source_schema.field_by_name(filter_name)
            if field is None:
                raise _Quarantine("unresolved_materialize_filter", f"{path}.source.filter.{filter_name}", "filter field is not declared")
            field_id = field_ids.get((source_slug or "", filter_name))
            if not isinstance(field_id, str) or field_id != field.field_id:
                raise _Quarantine("materialize_filter_field_identity_mismatch", f"{path}.source.filter.{filter_name}", "filter field ID mapping must match the stable field declaration")
            source_filter_field_ids[filter_name] = field_id
    target_path = _text(target.get("workspace_path"), f"{path}.target.workspace_path") or ""
    normalized_path = target_path.replace("\\", "/")
    if normalized_path.startswith("/") or re.match(r"^[A-Za-z]:", normalized_path) or ".." in normalized_path.split("/") or "\x00" in normalized_path:
        raise _Quarantine("unsafe_materialize_path", f"{path}.target.workspace_path", "target must be a safe relative workspace path")
    template = target.get("template")
    if output_format is MaterializeFormat.TEMPLATE and not isinstance(template, str):
        raise _Quarantine("missing_materialize_template", f"{path}.target.template", "template format requires a string template")
    if template is not None and not isinstance(template, str):
        raise _Quarantine("invalid_materialize_template", f"{path}.target.template", "template must be a string")
    target_field_id = None
    if output_format in {MaterializeFormat.COPY_BLOB, MaterializeFormat.MCP_PACKAGE}:
        field_name = _text(target.get("field"), f"{path}.target.field")
        if source_schema is None:
            raise _Quarantine("materialize_field_without_schema", f"{path}.target.field", "blob field requires a row source schema")
        field = next((f for f in source_schema.fields if f.name == field_name), None)
        if field is None or field.field_type is not FieldType.FILE_REF:
            raise _Quarantine("unknown_materialize_field", f"{path}.target.field", "target field must resolve to a declared file/secret reference")
        target_field_id = field.field_id
    elif target.get("field") is not None:
        raise _Quarantine("unused_materialize_field", f"{path}.target.field", "target field is unsupported for this output format")
    events = item.get("when", ["project.created", "project.resumed", "project.sync"])
    if not isinstance(events, list) or any(not isinstance(event, str) or event not in _SUPPORTED_EVENTS for event in events):
        raise _Quarantine("unsupported_materialize_event", f"{path}.when", "unsupported or malformed materialization event")
    if len(set(events)) != len(events):
        raise _Quarantine("duplicate_materialize_event", f"{path}.when", "materialization events must be unique")
    priority = item.get("priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise _Quarantine("invalid_materialize_priority", f"{path}.priority", "priority must be an integer")
    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _Quarantine("invalid_materialize_enabled", f"{path}.enabled", "enabled must be boolean")
    return MaterializeDeclaration(
        rule_id=_text(item.get("id"), f"{path}.id") or f"rule_{index}", enabled=enabled,
        events=tuple(events), priority=priority, source=source_kind,
        source_schema_id=source_schema_id, source_document_slug=source_doc_slug,
        source_filter=source_filter, source_filter_field_ids=source_filter_field_ids,
        static_value=source.get("value", source.get("text")),
        target_path=target_path, output_format=output_format,
        target_field_id=target_field_id, template=template, declaration=item,
    )


def _normalize_mcp_servers(
    manifest: Mapping[str, object] | None,
    server_ids: Mapping[str, str] | None,
) -> tuple[McpServerDeclaration, ...]:
    if manifest is None:
        return ()
    root = _mapping(manifest, "mcp_manifest")
    if set(root) != {"mcpServers"}:
        raise _Quarantine("unsupported_mcp_manifest_shape", "mcp_manifest", "manifest must contain only mcpServers")
    servers = _mapping(root.get("mcpServers"), "mcp_manifest.mcpServers")
    if not servers:
        raise _Quarantine("empty_mcp_manifest", "mcp_manifest.mcpServers", "at least one MCP server is required")
    if not isinstance(server_ids, MappingABC):
        raise _Quarantine("mcp_server_identity_map_missing", "mcp_manifest.mcpServers", "stable MCP server IDs are required")
    result: list[McpServerDeclaration] = []
    for name, raw in servers.items():
        path = f"mcp_manifest.mcpServers.{name}"
        if not isinstance(name, str) or not name.strip():
            raise _Quarantine("invalid_mcp_server_name", path, "server name must be non-empty")
        config = _mapping(raw, path)
        allowed = {"command", "args", "url", "transport", "env", "headers", "cwd", "tools"}
        unknown = set(config) - allowed
        if unknown:
            raise _Quarantine("unsupported_mcp_server_shape", path, f"unsupported server properties: {sorted(unknown)}")
        command = config.get("command")
        endpoint = config.get("url")
        if (command is None) == (endpoint is None):
            raise _Quarantine("invalid_mcp_server_endpoint", path, "exactly one command or url is required")
        if command is not None and (not isinstance(command, str) or not command.strip()):
            raise _Quarantine("invalid_mcp_server_command", f"{path}.command", "command must be a non-empty string")
        if endpoint is not None and (not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://"))):
            raise _Quarantine("invalid_mcp_server_url", f"{path}.url", "url must be an HTTP(S) endpoint")
        transport = config.get("transport")
        expected = "stdio" if command is not None else "http"
        if transport is None:
            transport = expected
        if transport not in {"stdio", "http", "sse"} or (expected == "stdio" and transport != "stdio") or (expected == "http" and transport not in {"http", "sse"}):
            raise _Quarantine("unsupported_mcp_transport", f"{path}.transport", "transport does not match command/url")
        args = config.get("args", [])
        if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
            raise _Quarantine("invalid_mcp_server_args", f"{path}.args", "args must be an array of strings")
        environment = config.get("env", {})
        if not isinstance(environment, MappingABC):
            raise _Quarantine("invalid_mcp_server_env", f"{path}.env", "env must be an object")
        _reject_secrets(environment, f"{path}.env")
        headers = config.get("headers", {})
        if not isinstance(headers, MappingABC):
            raise _Quarantine("invalid_mcp_server_headers", f"{path}.headers", "headers must be an object")
        for header_name, header_value in headers.items():
            if not isinstance(header_name, str) or not isinstance(header_value, (str, MappingABC)):
                raise _Quarantine("invalid_mcp_server_headers", f"{path}.headers", "header names and values must be strings or references")
            if header_name.lower() in {"authorization", "proxy-authorization", "x-api-key"}:
                if isinstance(header_value, str) and not header_value.startswith(("vault://", "secret://", "file://", "${")):
                    raise _Quarantine("inline_secret_forbidden", f"{path}.headers.{header_name}", "credential headers require an external reference")
                if isinstance(header_value, MappingABC) and not (header_value.get("provider") and (header_value.get("locator") or header_value.get("path"))):
                    raise _Quarantine("invalid_secret_reference", f"{path}.headers.{header_name}", "credential header reference requires provider and locator")
        if config.get("tools"):
            raise _Quarantine("unsupported_mcp_server_tools", f"{path}.tools", "tool declarations must be normalized in mcp_tools")
        stable_id = server_ids.get(name)
        if not isinstance(stable_id, str) or not stable_id.strip():
            raise _Quarantine("mcp_server_identity_missing", path, f"no stable MCP server ID supplied for {name}")
        result.append(McpServerDeclaration(stable_id, name, str(transport), endpoint if isinstance(endpoint, str) else None,
            command if isinstance(command, str) else None, tuple(args), environment, config))
    _unique(result, "mcp_manifest.mcpServers", lambda item: item.server_id, "duplicate_mcp_server_id")
    return tuple(result)


def normalize_legacy_module(
    *, module_id: str,
    definition_id: str,
    version: str,
    source_digest: str,
    slug_map: Mapping[str, object],
    schema_ids: Mapping[str, str],
    field_ids: Mapping[tuple[str, str], str],
    view_ids: Mapping[str, str],
    action_operation_ids: Mapping[str, str],
    mcp_server_ids: Mapping[str, str] | None = None,
    mcp_tool_ids: Mapping[str, str] | None = None,
    mcp_manifest: Mapping[str, object] | None = None,
    dependency_ids: tuple[str, ...] = (),
    conflict_ids: tuple[str, ...] = (),
    schema_version: str = "module-meta-v1",
) -> NormalizationResult:
    """Normalize a full legacy slug-map only when stable schema ID maps are supplied.

    Legacy meta JSON remains untouched. Unsupported authoring forms become a
    `NormalizationResult(candidate=None, issues=(...))` for quarantine evidence.
    """
    try:
        if not isinstance(module_id, str) or not module_id.strip() or not isinstance(definition_id, str) or not definition_id.strip():
            raise _Quarantine("missing_module_identity", "module_id", "module and definition IDs are required")
        if not isinstance(source_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_digest):
            raise _Quarantine("invalid_source_digest", "source_digest", "source snapshot digest must be sha256-prefixed")
        if not isinstance(slug_map, MappingABC):
            raise _Quarantine("invalid_module_meta", "meta", "module meta must be an object")
        if not isinstance(schema_ids, MappingABC) or not isinstance(field_ids, MappingABC):
            raise _Quarantine("schema_identity_map_missing", "schemas", "stable schema and field IDs are required")
        extra_slugs = set(slug_map) - _SUPPORTED_META
        if extra_slugs:
            slug = sorted(extra_slugs)[0]
            raise _Quarantine("unsupported_meta_document", str(slug), f"unsupported legacy meta slug: {slug}")
        for slug in _SUPPORTED_META:
            if slug not in slug_map:
                continue
            body = slug_map[slug]
            if slug == "seed_rows":
                if not isinstance(body, MappingABC) or set(body) - {"items"} or not isinstance(body.get("items", []), list):
                    raise _Quarantine("unsupported_seed_rows_shape", slug, "seed_rows supports only {items: [...]} shape")
            elif slug == "materialize_roots":
                if not isinstance(body, MappingABC) or set(body) - {"workspace_roots"}:
                    raise _Quarantine("unsupported_materialize_roots_shape", slug, "materialize_roots supports workspace_roots only")
            elif not isinstance(body, list):
                raise _Quarantine("expected_array", slug, f"{slug} must be an array")
        _reject_secrets(slug_map)

        raw_tables = _array(slug_map.get("tables", []), "tables")
        raw_columns = _array(slug_map.get("columns", []), "columns")
        _unique(raw_tables, "tables", lambda x: _mapping(x, "tables[]").get("slug", ""), "duplicate_schema_slug")
        table_docs: dict[str, Mapping[str, Any]] = {}
        for index, raw in enumerate(raw_tables):
            item = _mapping(raw, f"tables[{index}]")
            slug = _text(item.get("slug"), f"tables[{index}].slug") or ""
            allowed = {"slug", "label", "storage_kind", "enabled", "scope"}
            if set(item) - allowed:
                raise _Quarantine("unsupported_schema_shape", f"tables[{index}]", f"unsupported table fields: {sorted(set(item) - allowed)}")
            if slug not in schema_ids or not isinstance(schema_ids.get(slug), str) or not schema_ids[slug].strip():
                raise _Quarantine("schema_identity_missing", f"tables[{index}].slug", f"no stable schema ID supplied for {slug}")
            if "row_id" in item:
                raise _Quarantine("legacy_schema_row_id_reserved", f"tables[{index}].slug", "row_id is a platform identity and cannot be redeclared as a legacy field")
            table_docs[slug] = item
        fields_by_table: dict[str, list[FieldDefinition]] = {slug: [] for slug in table_docs}
        raw_fields_by_table: dict[str, dict[str, Mapping[str, Any]]] = {slug: {} for slug in table_docs}
        for index, raw in enumerate(raw_columns):
            item = _mapping(raw, f"columns[{index}]")
            allowed = {"table_slug", "name", "label", "type", "required", "default", "read_only", "hidden", "enum", "ref", "ui", "options"}
            if set(item) - allowed:
                raise _Quarantine("unsupported_field_shape", f"columns[{index}]", f"unsupported field properties: {sorted(set(item) - allowed)}")
            table_slug = _text(item.get("table_slug"), f"columns[{index}].table_slug") or ""
            name = _text(item.get("name"), f"columns[{index}].name") or ""
            if table_slug not in table_docs:
                raise _Quarantine("unknown_field_schema", f"columns[{index}].table_slug", f"unknown schema {table_slug}")
            if name in raw_fields_by_table[table_slug]:
                raise _Quarantine("duplicate_field", f"columns[{index}].name", f"duplicate field {table_slug}.{name}")
            try:
                field_type = FieldType(item.get("type"))
            except (ValueError, TypeError) as exc:
                raise _Quarantine("unsupported_field_type", f"columns[{index}].type", "unknown field type") from exc
            stable_id = field_ids.get((table_slug, name))
            if not isinstance(stable_id, str) or not stable_id.strip():
                raise _Quarantine("field_identity_missing", f"columns[{index}].name", f"no stable field ID supplied for {table_slug}.{name}")
            required = item.get("required", False)
            if not isinstance(required, bool):
                raise _Quarantine("invalid_field_required", f"columns[{index}].required", "required must be boolean")
            if "default" in item and not _field_default_matches(field_type, item["default"]):
                raise _Quarantine("invalid_field_default", f"columns[{index}].default", f"default does not match {field_type.value}")
            ref = item.get("ref")
            reference = None
            if field_type is FieldType.REF:
                ref_map = _mapping(ref, f"columns[{index}].ref")
                if set(ref_map) != {"table_slug"}:
                    raise _Quarantine("unsupported_reference_shape", f"columns[{index}].ref", "references must identify exactly one target table")
                target_slug = _text(ref_map.get("table_slug"), f"columns[{index}].ref.table_slug") or ""
                if target_slug not in table_docs:
                    raise _Quarantine("unresolved_schema_reference", f"columns[{index}].ref", f"unknown target schema {target_slug}")
                target_schema_id = schema_ids[target_slug]
                target_field_id = field_ids.get((target_slug, "row_id"))
                if not isinstance(target_field_id, str) or not target_field_id.strip():
                    raise _Quarantine("reference_field_identity_missing", f"columns[{index}].ref", f"no stable row field ID supplied for {target_slug}")
                reference = SchemaReference(target_schema_id, target_field_id)
            elif ref is not None:
                raise _Quarantine("reference_on_non_ref_field", f"columns[{index}].ref", "only ref fields may declare references")
            enum = item.get("enum")
            if field_type is FieldType.ENUM:
                enum_map = _mapping(enum, f"columns[{index}].enum")
                values = enum_map.get("values")
                if not isinstance(values, list) or any(not isinstance(value, (str, int, float, bool)) for value in values) or len(set(map(str, values))) != len(values):
                    raise _Quarantine("unsupported_enum_shape", f"columns[{index}].enum", "enum values must be unique scalars")
                if set(enum_map) - {"values", "labels"}:
                    raise _Quarantine("unsupported_enum_shape", f"columns[{index}].enum", "unsupported enum attributes")
            for bool_key in ("read_only", "hidden"):
                if bool_key in item and not isinstance(item[bool_key], bool):
                    raise _Quarantine("invalid_field_flag", f"columns[{index}].{bool_key}", f"{bool_key} must be boolean")
            declaration = {key: val for key, val in item.items() if key not in {"table_slug", "name", "type", "required", "ref", "default"}}
            field_def = FieldDefinition(
                stable_id, schema_ids[table_slug], name, field_type, required, reference,
                item.get("default"), declaration,
            )
            fields_by_table[table_slug].append(field_def)
            raw_fields_by_table[table_slug][name] = item

        schemas: list[ModuleSchema] = []
        for slug, doc in table_docs.items():
            enabled = doc.get("enabled", True)
            if not isinstance(enabled, bool):
                raise _Quarantine("invalid_schema_enabled", f"tables.{slug}.enabled", "enabled must be boolean")
            scope = doc.get("scope", {})
            if not isinstance(scope, MappingABC):
                raise _Quarantine("unsupported_schema_scope", f"tables.{slug}.scope", "scope must be an object")
            if set(scope) - {"projects", "chats", "active_chat"}:
                raise _Quarantine("unsupported_schema_scope", f"tables.{slug}.scope", "unsupported scope declaration")
            schemas.append(ModuleSchema(
                schema_ids[slug], slug, doc.get("label", slug), tuple(fields_by_table[slug]),
                str(doc.get("storage_kind", "json_document")), scope, enabled,
            ))
        schemas_by_slug = {schema.slug: schema for schema in schemas}
        schema_slug_by_id = {schema.schema_id: schema.slug for schema in schemas}

        raw_views = _array(slug_map.get("views", []), "views")
        _unique(raw_views, "views", lambda x: _mapping(x, "views[]").get("slug", ""), "duplicate_view_slug")
        views: list[ViewDeclaration] = []
        for index, raw in enumerate(raw_views):
            item = _mapping(raw, f"views[{index}]")
            allowed = {"slug", "table_slug", "kind", "ui_json", "enabled"}
            if set(item) - allowed:
                raise _Quarantine("unsupported_view_shape", f"views[{index}]", f"unsupported view fields: {sorted(set(item) - allowed)}")
            slug = _text(item.get("slug"), f"views[{index}].slug") or ""
            view_kind = item.get("kind")
            ui = item.get("ui_json", {})
            if not isinstance(ui, MappingABC):
                raise _Quarantine("unsupported_view_shape", f"views[{index}].ui_json", "ui_json must be an object")
            ui_kind = ui.get("kind") if isinstance(ui.get("kind"), str) else None
            try:
                kind = ViewKind(view_kind or ui_kind)
            except (ValueError, TypeError) as exc:
                raise _Quarantine("unsupported_view_kind", f"views[{index}].kind", "unknown view kind") from exc
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                raise _Quarantine("invalid_view_enabled", f"views[{index}].enabled", "enabled must be boolean")
            table_slug = _text(item.get("table_slug"), f"views[{index}].table_slug", optional=True)
            if table_slug is not None and table_slug not in schemas_by_slug:
                raise _Quarantine("unresolved_view_schema", f"views[{index}].table_slug", f"unknown schema {table_slug}")
            view_id = (view_ids or {}).get(slug) if isinstance(view_ids, MappingABC) else None
            if not isinstance(view_id, str) or not view_id.strip():
                raise _Quarantine("view_identity_missing", f"views[{index}].slug", f"no stable view ID supplied for {slug}")
            views.append(ViewDeclaration(view_id, slug, kind, schemas_by_slug[table_slug].schema_id if table_slug else None, enabled, item))
        view_by_slug = {view.slug: view for view in views}

        raw_tabs = _array(slug_map.get("tabs", []), "tabs")
        tabs: list[NavigationDeclaration] = []
        tab_ids: set[str] = set()
        for index, raw in enumerate(raw_tabs):
            item = _mapping(raw, f"tabs[{index}]")
            allowed = {"id", "title", "subtitle", "order", "icon", "view_slug", "table_slug", "enabled", "default_project_bind", "nav"}
            if set(item) - allowed:
                raise _Quarantine("unsupported_navigation_shape", f"tabs[{index}]", f"unsupported navigation fields: {sorted(set(item) - allowed)}")
            nav_id = _text(item.get("id"), f"tabs[{index}].id") or ""
            if nav_id in tab_ids:
                raise _Quarantine("duplicate_navigation_id", f"tabs[{index}].id", f"duplicate navigation ID {nav_id}")
            tab_ids.add(nav_id)
            view_slug = _text(item.get("view_slug"), f"tabs[{index}].view_slug", optional=True)
            table_slug = _text(item.get("table_slug"), f"tabs[{index}].table_slug", optional=True)
            if view_slug and view_slug not in view_by_slug:
                raise _Quarantine("unresolved_navigation_view", f"tabs[{index}].view_slug", f"unknown view {view_slug}")
            if table_slug and table_slug not in schemas_by_slug:
                raise _Quarantine("unresolved_navigation_schema", f"tabs[{index}].table_slug", f"unknown schema {table_slug}")
            order = item.get("order", 0)
            enabled = item.get("enabled", True)
            if not isinstance(order, int) or isinstance(order, bool) or not isinstance(enabled, bool):
                raise _Quarantine("invalid_navigation_state", f"tabs[{index}]", "order must be int and enabled must be bool")
            tabs.append(NavigationDeclaration(nav_id, view_by_slug[view_slug].view_id if view_slug else None,
                schemas_by_slug[table_slug].schema_id if table_slug else None, order, enabled, item))

        actions: list[ActionDeclaration] = []
        raw_actions = _array(slug_map.get("actions", []), "actions")
        _unique(raw_actions, "actions", lambda x: _mapping(x, "actions[]").get("id", ""), "duplicate_action_id")
        for index, raw in enumerate(raw_actions):
            item = _mapping(raw, f"actions[{index}]")
            allowed = {"id", "label", "kind", "enabled", "params", "trigger", "ui"}
            if set(item) - allowed:
                raise _Quarantine("unsupported_action_shape", f"actions[{index}]", f"unsupported action fields: {sorted(set(item) - allowed)}")
            action_id = _text(item.get("id"), f"actions[{index}].id") or ""
            operation_id = action_operation_ids.get(action_id) if isinstance(action_operation_ids, MappingABC) else None
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise _Quarantine("action_operation_unmapped", f"actions[{index}].id", f"no registered typed operation for action {action_id}")
            kind = _text(item.get("kind"), f"actions[{index}].kind") or ""
            if kind not in _SUPPORTED_ACTIONS:
                raise _Quarantine("unsupported_action_kind", f"actions[{index}].kind", f"unsupported legacy action kind {kind}")
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                raise _Quarantine("invalid_action_enabled", f"actions[{index}].enabled", "enabled must be boolean")
            params = _mapping(item.get("params", {}), f"actions[{index}].params")
            trigger = _mapping(item.get("trigger", {}), f"actions[{index}].trigger")
            if set(trigger) - {"on", "async"}:
                raise _Quarantine("unsupported_action_trigger", f"actions[{index}].trigger", "unsupported trigger properties")
            events = trigger.get("on", [])
            if not isinstance(events, list) or any(not isinstance(event, str) or event not in {"row.created", "row.updated", "row.deleted"} for event in events):
                raise _Quarantine("invalid_action_trigger", f"actions[{index}].trigger.on", "unsupported or malformed trigger event")
            if len(set(events)) != len(events):
                raise _Quarantine("duplicate_action_trigger", f"actions[{index}].trigger.on", "trigger events must be unique")
            if "async" in trigger and not isinstance(trigger["async"], bool):
                raise _Quarantine("invalid_action_trigger", f"actions[{index}].trigger.async", "async must be boolean")
            schema_refs: set[str] = set()
            for key in ("table_slug", "source_table", "target_table", "lines_table", "groups_table", "offers_table", "budget_table", "procurement_table"):
                value = params.get(key)
                if isinstance(value, str):
                    if value not in schemas_by_slug:
                        raise _Quarantine("unresolved_action_schema", f"actions[{index}].params.{key}", f"unknown schema {value}")
                    schema_refs.add(schemas_by_slug[value].schema_id)
            actions.append(ActionDeclaration(action_id, operation_id, enabled, params, tuple(sorted(schema_refs)), tuple(events), item))

        mcp_servers = list(_normalize_mcp_servers(mcp_manifest, mcp_server_ids))
        declared_server_ids = {server.name: server.server_id for server in mcp_servers}
        mcp_tools: list[McpToolDeclaration] = []
        for index, raw in enumerate(_array(slug_map.get("mcp_tools", []), "mcp_tools")):
            item = _mapping(raw, f"mcp_tools[{index}]")
            allowed = {"id", "name", "server", "label", "description", "enabled", "kind", "params_schema", "implementation"}
            if set(item) - allowed:
                raise _Quarantine("unsupported_mcp_tool_shape", f"mcp_tools[{index}]", f"unsupported MCP tool fields: {sorted(set(item) - allowed)}")
            tool_id = _text(item.get("id"), f"mcp_tools[{index}].id") or ""
            name = _text(item.get("name"), f"mcp_tools[{index}].name") or tool_id
            kind = _text(item.get("kind"), f"mcp_tools[{index}].kind") or ""
            if kind not in _SUPPORTED_MCP:
                raise _Quarantine("unsupported_mcp_tool_kind", f"mcp_tools[{index}].kind", f"unsupported MCP implementation {kind}")
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                raise _Quarantine("invalid_mcp_enabled", f"mcp_tools[{index}].enabled", "enabled must be boolean")
            implementation = _mapping(item.get("implementation"), f"mcp_tools[{index}].implementation")
            if set(implementation) - {"table_slug", "query", "upsert_key"}:
                raise _Quarantine("unsupported_mcp_implementation", f"mcp_tools[{index}].implementation", "unsupported MCP implementation properties")
            table_slug = _text(implementation.get("table_slug"), f"mcp_tools[{index}].implementation.table_slug") or ""
            if table_slug not in schemas_by_slug:
                raise _Quarantine("unresolved_mcp_schema", f"mcp_tools[{index}].implementation.table_slug", f"unknown schema {table_slug}")
            query = implementation.get("query", {})
            if not isinstance(query, MappingABC) or set(query) - {"filter", "limit"}:
                raise _Quarantine("unsupported_mcp_query", f"mcp_tools[{index}].implementation.query", "query supports filter and limit only")
            if "limit" in query and (not isinstance(query["limit"], int) or isinstance(query["limit"], bool) or query["limit"] < 1):
                raise _Quarantine("invalid_mcp_query_limit", f"mcp_tools[{index}].implementation.query.limit", "limit must be a positive integer")
            implementation_field_ids: dict[str, str] = {}
            filter_doc = query.get("filter", {})
            if not isinstance(filter_doc, MappingABC):
                raise _Quarantine("unsupported_mcp_query", f"mcp_tools[{index}].implementation.query.filter", "query filter must be an object")
            for filter_name in filter_doc:
                filter_field = schemas_by_slug[table_slug].field_by_name(filter_name)
                filter_id = field_ids.get((table_slug, filter_name))
                if filter_field is None or not isinstance(filter_id, str) or filter_id != filter_field.field_id:
                    raise _Quarantine("unresolved_mcp_filter", f"mcp_tools[{index}].implementation.query.filter.{filter_name}", "filter field requires a declared stable field ID")
                implementation_field_ids[filter_name] = filter_id
            upsert_key = implementation.get("upsert_key", [])
            if not isinstance(upsert_key, list) or any(not isinstance(key, str) for key in upsert_key):
                raise _Quarantine("unsupported_mcp_upsert_key", f"mcp_tools[{index}].implementation.upsert_key", "upsert_key must be an array of declared field names")
            for key in upsert_key:
                field_def = schemas_by_slug[table_slug].field_by_name(key)
                field_id = field_ids.get((table_slug, key))
                if field_def is None or not isinstance(field_id, str) or field_id != field_def.field_id:
                    raise _Quarantine("unresolved_mcp_upsert_field", f"mcp_tools[{index}].implementation.upsert_key", f"upsert key field {key} requires a declared stable field ID")
                implementation_field_ids[key] = field_id
            stable_tool_id = (mcp_tool_ids or {}).get(name) if isinstance(mcp_tool_ids, MappingABC) else None
            if not isinstance(stable_tool_id, str) or not stable_tool_id.strip():
                raise _Quarantine("mcp_tool_identity_missing", f"mcp_tools[{index}].name", f"no stable MCP tool ID supplied for {name}")
            server_name = _text(item.get("server"), f"mcp_tools[{index}].server")
            server_id = declared_server_ids.get(server_name or "")
            if not isinstance(server_id, str) or not server_id.strip():
                raise _Quarantine("mcp_tool_server_unresolved", f"mcp_tools[{index}].server", "MCP tool must reference a stable declared MCP server ID")
            mcp_tools.append(McpToolDeclaration(stable_tool_id, name, kind, enabled, params_schema,
                {**implementation, "schema_id": schemas_by_slug[table_slug].schema_id},
                schema_id=schemas_by_slug[table_slug].schema_id,
                implementation_field_ids=implementation_field_ids,
                server_id=server_id,
                declaration=item,
            ))
        _unique(mcp_tools, "mcp_tools", lambda item: item.tool_id, "duplicate_mcp_tool_id")
        raw_servers = slug_map.get("mcp_servers", [])
        if raw_servers:
            raise _Quarantine("unsupported_mcp_servers_source", "mcp_servers", "MCP servers come from versioned package manifests, not legacy module meta")
        aliases: list[McpAliasDeclaration] = []
        for index, raw in enumerate(_array(slug_map.get("mcp_aliases", []), "mcp_aliases")):
            item = _mapping(raw, f"mcp_aliases[{index}]")
            if set(item) - {"server", "tool", "label", "description"}:
                raise _Quarantine("unsupported_mcp_alias_shape", f"mcp_aliases[{index}]", "unsupported MCP alias fields")
            server_name = _text(item.get("server"), f"mcp_aliases[{index}].server", optional=True)
            server_id = None
            if server_name:
                server_id = declared_server_ids.get(server_name)
                if not isinstance(server_id, str) or not server_id.strip():
                    raise _Quarantine("mcp_alias_server_unresolved", f"mcp_aliases[{index}].server", f"MCP alias references undeclared server {server_name}")
            aliases.append(McpAliasDeclaration(_text(item.get("tool"), f"mcp_aliases[{index}].tool") or "",
                _text(item.get("label"), f"mcp_aliases[{index}].label") or "",
                server_name, server_id,
                _text(item.get("description"), f"mcp_aliases[{index}].description", optional=True)))
        _unique(aliases, "mcp_aliases", lambda x: f"{x.server_name or ''}::{x.tool_name}", "duplicate_mcp_alias")

        materialize = tuple(_normalize_materialize(rule, index, schemas_by_slug, field_ids)
            for index, rule in enumerate(_array(slug_map.get("materialize", []), "materialize")))
        _unique(list(materialize), "materialize", lambda x: x.rule_id, "duplicate_materialize_id")
        roots: tuple[str, ...] = ()
        if "materialize_roots" in slug_map:
            roots_doc = _mapping(slug_map["materialize_roots"], "materialize_roots")
            raw_roots = _array(roots_doc.get("workspace_roots", []), "materialize_roots.workspace_roots")
            checked: list[str] = []
            for index, root in enumerate(raw_roots):
                root_text = _text(root, f"materialize_roots.workspace_roots[{index}]") or ""
                normalized = root_text.replace("\\", "/").strip("/")
                if normalized in {"", "."} or ".." in normalized.split("/") or re.match(r"^[A-Za-z]:", root_text):
                    raise _Quarantine("unsafe_materialize_root", f"materialize_roots.workspace_roots[{index}]", "root must be a safe non-empty relative path")
                checked.append(root_text)
            roots = tuple(checked)

        seed_rows: list[SeedRowDeclaration] = []
        seed_doc = slug_map.get("seed_rows", {"items": []})
        for index, raw in enumerate(_array(_mapping(seed_doc, "seed_rows").get("items", []), "seed_rows.items")):
            item = _mapping(raw, f"seed_rows.items[{index}]")
            if set(item) - {"table_slug", "row_id", "body"}:
                raise _Quarantine("unsupported_seed_row_shape", f"seed_rows.items[{index}]", "unsupported seed row properties")
            table_slug = _text(item.get("table_slug"), f"seed_rows.items[{index}].table_slug") or ""
            body = _mapping(item.get("body", {}), f"seed_rows.items[{index}].body")
            schema = schemas_by_slug.get(table_slug)
            if schema is None:
                raise _Quarantine("unresolved_seed_schema", f"seed_rows.items[{index}].table_slug", f"unknown schema {table_slug}")
            unknown_fields = set(body) - {field.name for field in schema.fields}
            if unknown_fields:
                raise _Quarantine("unresolved_seed_field", f"seed_rows.items[{index}].body", f"unknown fields: {sorted(unknown_fields)}")
            field_ids_by_name: dict[str, str] = {}
            for field_name in body:
                stable_field_id = field_ids.get((table_slug, field_name))
                if not isinstance(stable_field_id, str) or not stable_field_id.strip():
                    raise _Quarantine("seed_field_identity_missing", f"seed_rows.items[{index}].body.{field_name}", "seed field requires a stable field ID")
                field_ids_by_name[field_name] = stable_field_id
            seed_rows.append(SeedRowDeclaration(
                schema.schema_id,
                _text(item.get("row_id"), f"seed_rows.items[{index}].row_id") or "",
                body,
                field_ids_by_name,
            ))
        _unique(seed_rows, "seed_rows.items", lambda x: f"{x.schema_id}:{x.row_id}", "duplicate_seed_row")

        env_rows = _array(slug_map.get("container_env", []), "container_env")
        normalized_env: list[Mapping[str, object]] = []
        for index, raw in enumerate(env_rows):
            item = _mapping(raw, f"container_env[{index}]")
            if set(item) - {"env_name", "value", "value_from", "when"}:
                raise _Quarantine("unsupported_container_env_shape", f"container_env[{index}]", "unsupported environment declaration fields")
            env_name = _text(item.get("env_name"), f"container_env[{index}].env_name") or ""
            if not _ENV_RE.fullmatch(env_name) or ("value" in item) == ("value_from" in item):
                raise _Quarantine("invalid_container_env", f"container_env[{index}]", "valid env_name and exactly one value/value_from are required")
            if "value" in item and not isinstance(item["value"], str):
                raise _Quarantine("invalid_container_env", f"container_env[{index}].value", "environment values must be strings")
            normalized_env.append(item)
        secret_rows = _array(slug_map.get("container_env_secrets", []), "container_env_secrets")
        normalized_secrets: list[Mapping[str, object]] = []
        for index, raw in enumerate(secret_rows):
            item = _mapping(raw, f"container_env_secrets[{index}]")
            if set(item) - {"env_name", "secret_ref", "secret_ref_from", "foreach_rows", "when"}:
                raise _Quarantine("unsupported_container_secret_shape", f"container_env_secrets[{index}]", "unsupported secret binding fields")
            if "foreach_rows" in item:
                raise _Quarantine("unsupported_legacy_secret_expansion", f"container_env_secrets[{index}].foreach_rows", "legacy foreach_rows secret expansion needs an explicit typed binding adapter")
            env_name = _text(item.get("env_name"), f"container_env_secrets[{index}].env_name") or ""
            ref = item.get("secret_ref")
            if not _ENV_RE.fullmatch(env_name) or ("secret_ref" in item) == ("secret_ref_from" in item):
                raise _Quarantine("invalid_container_secret", f"container_env_secrets[{index}]", "valid env_name and exactly one secret reference form are required")
            if isinstance(ref, str) and not ref.startswith(("vault://", "secret://", "file://", "${")):
                raise _Quarantine("invalid_container_secret_ref", f"container_env_secrets[{index}].secret_ref", "secret reference must be external")
            normalized_secrets.append(item)
        metadata = ModuleMetadata(
            views=tuple(views), navigation=tuple(tabs), seed_rows=tuple(seed_rows),
            mcp_aliases=tuple(aliases), materialize_roots=roots,
            container_env=tuple(normalized_env), container_env_secrets=tuple(normalized_secrets),
            declarations={"source_snapshot_digest": source_digest},
        )
        dependencies = tuple(ModuleRelation(module_id) for module_id in dependency_ids)
        conflicts = tuple(ModuleRelation(module_id) for module_id in conflict_ids)
        candidate_data = {
            "definition_id": definition_id, "module_id": module_id, "version": version,
            "schema_version": schema_version, "metadata": metadata, "schemas": tuple(schemas),
            "actions": tuple(actions), "mcp_servers": tuple(mcp_servers), "mcp_tools": tuple(mcp_tools),
            "materialize": materialize, "dependencies": dependencies, "conflicts": conflicts,
        }
        digest = canonical_digest(candidate_data)
        candidate = CapabilityVersion(**candidate_data, content_digest=digest, lifecycle=VersionLifecycle.DRAFT)
        return NormalizationResult(candidate, ())
    except _Quarantine as exc:
        return NormalizationResult(None, (exc.issue,))
    except (TypeError, ValueError, KeyError) as exc:
        return NormalizationResult(None, (NormalizationIssue("normalization_failed", "module", str(exc)),))


__all__ = ["normalize_legacy_module"]
