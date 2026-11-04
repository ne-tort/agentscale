"""Version negotiation and loss-aware translation for legacy module metadata.

This adapter produces draft capability snapshots only. It does not persist,
publish, execute actions, or invoke UI/runtime interpreters. A caller must still
pass the durable owner/version/scope gate in the capability lifecycle pipeline.
"""
from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, replace
from typing import Any, Mapping, TypeVar

from .contracts import (
    ActionDeclaration,
    CapabilityVersion,
    FieldDefinition,
    McpToolDeclaration,
    MaterializeDeclaration,
    ModuleSchema,
    ModuleMetadata,
    NavigationDeclaration,
    NormalizationIssue,
    ViewDeclaration,
    canonical_digest,
)
from .normalization import _Quarantine, _SUPPORTED_ACTIONS, _reject_secrets, normalize_legacy_module

_SUPPORTED_META = frozenset({
    "tables", "columns", "views", "tabs", "actions", "materialize", "seed_rows",
    "mcp_tools", "mcp_aliases", "materialize_roots", "container_env", "container_env_secrets",
})
_SUPPORTED_SYNTAX_VERSIONS = frozenset({1})
_SUPPORTED_UI_VERSIONS = frozenset({1})
_SCOPED_COLLECTIONS = ("tables", "views", "tabs", "actions", "materialize", "mcp_tools")
_SUPPORTED_MCP_QUERY_OPERATORS = frozenset({"eq", "ne", "in", "contains", "starts_with"})
_SUPPORTED_MCP_KINDS = frozenset({"rows_query", "rows_upsert", "rows_delete"})
_SUPPORTED_PROJECT_BINDINGS = frozenset({"local", "global"})
_SUPPORTED_PROJECT_SCOPES = frozenset({"all", "bound", "none"})
_SUPPORTED_CHAT_SCOPES = frozenset({"all", "current"})
_SUPPORTED_ACTIVE_CHAT = frozenset({"optional", "required"})
_SUPPORTED_SCHEMA_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object", "null"})
_SUPPORTED_SCHEMA_PROPERTY_KEYS = frozenset({
    "type", "description", "enum", "items", "format", "minimum", "maximum", "minLength", "maxLength", "pattern",
})
_SUPPORTED_SCHEMA_KEYS = frozenset({"type", "properties", "required", "additionalProperties", "description", "title"})
_SUPPORTED_UI_ROOT_KEYS = frozenset({
    "version", "kind", "title", "title_field", "title_template", "subtitle_fields", "columns",
    "fields", "sections", "save", "mode", "primary_action", "inline_add", "row_tap", "row_actions",
    "empty", "toolbar", "scaffold", "context_bind", "row_filter", "row_filter_from_context",
    "row_matches_context_map", "selection", "items", "data_scope", "search", "sort", "row_style",
    "row_min_height", "on_load", "poll_while", "server_paged", "chat_header", "summary", "doc_fields",
    "context_header", "profile_table", "settings_table", "add_view", "list_header", "dash_empty_fields",
    "no_offers_placeholder", "stock_filter", "links", "actions",
})
_SUPPORTED_UI_KINDS = frozenset({"collection", "form", "hub", "detail", "board", "profile_hub"})
_SUPPORTED_UI_SCALAR_KEYS = frozenset({
    "version", "kind", "title", "title_field", "title_template", "subtitle_fields", "columns", "fields",
    "sections", "save", "mode", "primary_action", "inline_add", "row_tap", "row_actions", "empty",
    "toolbar", "scaffold", "context_bind", "row_filter", "row_filter_from_context", "row_matches_context_map",
    "selection", "items", "data_scope", "search", "sort", "row_style", "row_min_height", "on_load",
    "poll_while", "server_paged", "chat_header", "summary", "doc_fields", "context_header", "profile_table",
    "settings_table", "add_view", "list_header", "dash_empty_fields", "no_offers_placeholder", "stock_filter",
    "links", "actions", "accent", "accent_map", "accept", "action", "action_id", "active_chat", "after_field",
    "align", "also_copy", "any", "auto", "build_id", "cards_table", "chats", "clear_fields", "column",
    "company_fields", "company_table", "company_title", "contacts_table", "context", "context_field", "control",
    "copy_on_tap", "current_field", "deal_fields", "deal_table", "deal_title", "dir", "disable_row_tap", "draft",
    "editable", "empty_hint", "empty_label", "en", "ensure_row", "eq", "equals", "equals_any", "error", "field",
    "fields_from", "format", "from", "group_field", "group_id", "hint", "icon", "in", "indexing", "is_enabled",
    "label", "label_field", "line_id", "list_action", "lookup_table", "match_context_field", "max_lines", "max_width",
    "mode", "not_empty", "open_view", "owner_kind", "pause_icon", "pause_label", "pick_view", "placement", "pop_after",
    "price_field", "profile_id", "queued", "read_only", "ready", "resume_icon", "resume_label", "row_actions",
    "row_id_from_field", "schema", "scope", "seller", "section_title", "secret", "selection", "slots_table",
    "all", "interval_ms", "set_on_context", "source_columns_from", "subject", "subtitle", "subtitle_from", "target", "text",
    "tone_field", "tooltip", "trailing_action", "type_id", "type_id_field", "types_table", "value_from", "variant",
    "view", "visible_when", "warning_when_match", "when", "widget", "width", "table_slug", "table", "row", "value",
    "ru", "l10n", "args", "label", "placeholder", "title_template", "title", "column", "columns", "source",
    "field", "action", "confirm", "schema_table", "context_header", "context_field", "title_field", "empty_style",
    "tone_map", "labels", "to", "text_from_field",
})
_SUPPORTED_UI_WIDGETS = frozenset({
    "value", "choice", "switch", "file", "file_upload", "number", "text", "markdown_editor", "text_editor",
    "prompt_files_editor", "project_multiselect", "ref", "pick", "column_map", "pause_toggle", "type_ref_picker",
    "schema_attrs", "fields_schema_editor", "build_spec_editor", "ready_build_slots_table", "tender_lines_table",
    "constraint_attrs", "file_upload", "contact_picker", "remote_database_picker", "remote_table_picker",
    "target_price_range",
})
_UI_DYNAMIC_VALUE_KEYS = frozenset({"op", "value", "field", "eq", "in", "not_empty", "all", "any"})
# `row_filter` and `row_matches_context_map` are keyed by *data field name*, not by
# UI vocabulary: `{"owner_kind": "line"}` is a legitimate legacy filter. Treating
# their keys as ui_json properties rejected every module that filters rows by a
# declared field, so the field-keyed containers are enumerated explicitly here.
_UI_FIELD_KEYED_MAPS = frozenset({"row_filter", "row_matches_context_map"})
_UI_DYNAMIC_VALUE_TOKENS = frozenset({"contextRowId", "true", "false", "null"})
_SUPPORTED_UI_NESTED_KEYS = _SUPPORTED_UI_ROOT_KEYS | _SUPPORTED_UI_SCALAR_KEYS
_TYPED_ACTION_OPERATION_ALIASES = {
    "data.create_row": frozenset({"operation:rows.create"}),
    "data.delete_row": frozenset({"operation:rows.delete"}),
    "data.select_row": frozenset({"operation:rows.select"}),
}
_SUPPORTED_ACTION_OPERATIONS = _SUPPORTED_ACTIONS | frozenset(
    operation for operations in _TYPED_ACTION_OPERATION_ALIASES.values() for operation in operations
)


@dataclass(frozen=True, slots=True)
class LegacyNegotiation:
    """The source dialect selected before any declarations are translated."""

    source_format: str
    source_version: int
    explicit_syntax_version: bool
    ui_json_versions: tuple[tuple[str, int | None], ...]


@dataclass(frozen=True, slots=True)
class LegacyAdapterResult:
    candidate: CapabilityVersion | None
    issues: tuple[NormalizationIssue, ...]
    negotiation: LegacyNegotiation | None

    @property
    def accepted(self) -> bool:
        return self.candidate is not None and not self.issues


@dataclass(frozen=True, slots=True)
class _TranslatedPayload:
    metadata: ModuleMetadata
    schemas: tuple[ModuleSchema, ...]
    actions: tuple[ActionDeclaration, ...]
    materialize: tuple[MaterializeDeclaration, ...]
    field_declarations: Mapping[tuple[str, str], Mapping[str, object]]
    action_scopes: Mapping[int, Mapping[str, object]]


class _AdapterQuarantine(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(message)
        self.issue = NormalizationIssue(code, path, message)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, MappingABC) or any(not isinstance(key, str) for key in value):
        raise _AdapterQuarantine("expected_object", path, "expected object with string keys")
    return value


def _sequence(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise _AdapterQuarantine("expected_array", path, "expected array")
    return value


def _scope(value: object, path: str) -> Mapping[str, object]:
    scope = _mapping(value, path)
    allowed = {"projects", "chats", "active_chat", "requires_assignment", "module_binding"}
    unknown = set(scope) - allowed
    if unknown:
        raise _AdapterQuarantine("unsupported_scope_property", path, f"unsupported scope properties: {sorted(unknown)}")
    if "projects" in scope and (not isinstance(scope["projects"], str) or scope["projects"] not in _SUPPORTED_PROJECT_SCOPES):
        raise _AdapterQuarantine("unsupported_project_scope", f"{path}.projects", "projects must be all, bound, or none")
    if "chats" in scope and (not isinstance(scope["chats"], str) or scope["chats"] not in _SUPPORTED_CHAT_SCOPES):
        raise _AdapterQuarantine("unsupported_chat_scope", f"{path}.chats", "chats must be all or current")
    if "active_chat" in scope and (not isinstance(scope["active_chat"], str) or scope["active_chat"] not in _SUPPORTED_ACTIVE_CHAT):
        raise _AdapterQuarantine("unsupported_active_chat_scope", f"{path}.active_chat", "active_chat must be optional or required")
    if "requires_assignment" in scope and not isinstance(scope["requires_assignment"], bool):
        raise _AdapterQuarantine("invalid_requires_assignment", f"{path}.requires_assignment", "requires_assignment must be boolean")
    if "module_binding" in scope and scope["module_binding"] != "required":
        raise _AdapterQuarantine("unsupported_module_binding_scope", f"{path}.module_binding", "module_binding only supports required")
    return dict(scope)


def _validate_ui_action_references(value: object, path: str, action_ids: set[str]) -> None:
    if isinstance(value, MappingABC):
        action = value.get("action")
        if action is not None:
            if not isinstance(action, str) or not action.strip():
                raise _AdapterQuarantine("invalid_ui_action_reference", f"{path}.action", "action reference must be a non-empty action ID")
            if action not in action_ids:
                raise _AdapterQuarantine("unresolved_ui_action_reference", f"{path}.action", f"no declared action with ID {action}")
        for key, child in value.items():
            _validate_ui_action_references(child, f"{path}.{key}", action_ids)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_ui_action_references(child, f"{path}[{index}]", action_ids)


def _validate_ui_semantics(value: object, path: str) -> None:
    if isinstance(value, MappingABC):
        kind = value.get("kind")
        if path.endswith(".row_tap") and kind is not None and (not isinstance(kind, str) or kind not in {"open_form", "open_view", "invoke_action", "match_product"}):
            raise _AdapterQuarantine("unsupported_ui_navigation_kind", f"{path}.kind", f"unsupported row_tap kind {kind}")
        if path.endswith(".primary_action") and kind is not None and (not isinstance(kind, str) or kind not in {"create_row", "invoke_action"}):
            raise _AdapterQuarantine("unsupported_ui_primary_action", f"{path}.kind", f"unsupported primary action kind {kind}")
        if path.endswith(".selection") and kind is not None and (not isinstance(kind, str) or kind != "single"):
            raise _AdapterQuarantine("unsupported_ui_selection_kind", f"{path}.kind", f"unsupported selection kind {kind}")
        for key, child in value.items():
            _validate_ui_semantics(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_ui_semantics(child, f"{path}[{index}]")


def _validate_ui_tree(value: object, path: str, action_ids: set[str], parent_key: str = "") -> None:
    if isinstance(value, MappingABC):
        for key, child in value.items():
            if not isinstance(key, str):
                raise _AdapterQuarantine("invalid_ui_json_key", path, "ui_json object keys must be strings")
            child_path = f"{path}.{key}"
            if key not in _SUPPORTED_UI_NESTED_KEYS:
                if parent_key in _UI_DYNAMIC_VALUE_KEYS and (
                    isinstance(key, str) and (key in _UI_DYNAMIC_VALUE_TOKENS or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key))
                ):
                    continue
                if parent_key in _UI_FIELD_KEYED_MAPS and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                    # A field-keyed filter/map: the key is a declared data field and
                    # the value is its literal or dynamic comparison, so only its own
                    # subtree is validated further.
                    _validate_ui_tree(child, child_path, action_ids, key)
                    continue
                raise _AdapterQuarantine("unsupported_ui_json_property", child_path, f"unsupported nested ui_json key {key}")
            if key == "widget" and isinstance(child, str) and child not in _SUPPORTED_UI_WIDGETS:
                raise _AdapterQuarantine("unsupported_ui_widget", child_path, f"unsupported UI widget {child}")
            if key in {"action", "action_id"} and isinstance(child, str) and child not in action_ids:
                raise _AdapterQuarantine("unresolved_ui_action_reference", child_path, f"no declared action with ID {child}")
            if key == "action" and not isinstance(child, str):
                raise _AdapterQuarantine("invalid_ui_action_reference", child_path, "action reference must be an action ID")
            if key == "kind" and isinstance(child, str):
                if path.endswith("ui_json") and child not in _SUPPORTED_UI_KINDS:
                    raise _AdapterQuarantine("unsupported_ui_json_kind", child_path, f"unsupported ui_json kind {child}")
            _validate_ui_tree(child, child_path, action_ids, key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_ui_tree(child, f"{path}[{index}]", action_ids, parent_key)


def _validate_ui_view_references(value: object, path: str, view_slugs: set[str]) -> None:
    if isinstance(value, MappingABC):
        kind = value.get("kind")
        reference = value.get("view")
        is_view_reference = kind in {"open_form", "open_view"} or (path.endswith(".target") and kind == "view")
        if is_view_reference:
            if not isinstance(reference, str) or not reference.strip():
                raise _AdapterQuarantine("invalid_ui_view_reference", f"{path}.view", "view navigation requires a view slug")
            if reference not in view_slugs:
                raise _AdapterQuarantine("unresolved_ui_view_reference", f"{path}.view", f"unknown view {reference}")
        for key, child in value.items():
            _validate_ui_view_references(child, f"{path}.{key}", view_slugs)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_ui_view_references(child, f"{path}[{index}]", view_slugs)


def _strip_secret_flags(value: object) -> object:
    if isinstance(value, MappingABC):
        return {
            key: _strip_secret_flags(child)
            for key, child in value.items()
            if not (key == "secret" and isinstance(child, bool))
        }
    if isinstance(value, list):
        return [_strip_secret_flags(child) for child in value]
    return value


def _prepare_source(
    metadata: object,
    *,
    action_operation_ids: Mapping[str, str] | None,
) -> tuple[
    dict[str, object],
    LegacyNegotiation,
    dict[tuple[str, int], Mapping[str, object]],
    dict[int, Mapping[str, object]],
    dict[tuple[str, str], Mapping[str, object]],
    dict[int, Mapping[str, object]],
]:
    root = _mapping(metadata, "metadata")
    explicit = "syntax_version" in root
    if explicit:
        raw_version = root.get("syntax_version")
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise _AdapterQuarantine("invalid_meta_version", "syntax_version", "syntax_version must be an integer")
        if raw_version not in _SUPPORTED_SYNTAX_VERSIONS:
            raise _AdapterQuarantine("unsupported_meta_version", "syntax_version", f"unsupported syntax_version {raw_version}")
        source_format = f"manifest-v{raw_version}"
    else:
        raw_version = 1
        source_format = "legacy-slug-map-v1"

    unknown = set(root) - _SUPPORTED_META - {"syntax_version"}
    if unknown:
        key = sorted(unknown)[0]
        raise _AdapterQuarantine("unsupported_meta_document", key, f"unsupported legacy metadata field: {key}")

    slug_map: dict[str, object] = {key: value for key, value in root.items() if key != "syntax_version"}
    try:
        _reject_secrets(_strip_secret_flags(slug_map))
    except _Quarantine as exc:
        raise _AdapterQuarantine(exc.issue.code, exc.issue.path, exc.issue.message) from exc
    field_declarations: dict[tuple[str, str], Mapping[str, object]] = {}
    action_scopes: dict[int, Mapping[str, object]] = {}
    scopes: dict[tuple[str, int], Mapping[str, object]] = {}
    raw_actions = slug_map.get("actions", [])
    action_ids: set[str] = set()
    if isinstance(raw_actions, list):
        for index, raw_action in enumerate(raw_actions):
            action = _mapping(raw_action, f"actions[{index}]")
            action_id = action.get("id")
            kind = action.get("kind")
            if not isinstance(action_id, str) or not action_id.strip():
                raise _AdapterQuarantine("invalid_action_id", f"actions[{index}].id", "action ID must be a non-empty string")
            if not isinstance(kind, str) or kind not in _SUPPORTED_ACTIONS:
                raise _AdapterQuarantine("unsupported_action_kind", f"actions[{index}].kind", f"unsupported legacy action kind {kind}")
            operation_id = (action_operation_ids or {}).get(action_id)
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise _AdapterQuarantine("action_operation_unmapped", f"actions[{index}].id", "no registered typed operation for this action")
            if operation_id not in _SUPPORTED_ACTION_OPERATIONS:
                raise _AdapterQuarantine("unregistered_typed_action_operation", f"actions[{index}].id", "operation ID is not registered as a typed capability operation")
            if operation_id != kind and operation_id not in _TYPED_ACTION_OPERATION_ALIASES.get(kind, frozenset()):
                raise _AdapterQuarantine("action_operation_kind_mismatch", f"actions[{index}].id", "typed operation mapping does not match the declared action kind")
            action_ids.add(action_id)

    raw_views = slug_map.get("views", [])
    view_slugs: set[str] = set()
    if isinstance(raw_views, list):
        for raw_view in raw_views:
            if isinstance(raw_view, MappingABC) and isinstance(raw_view.get("slug"), str):
                view_slugs.add(raw_view["slug"])
    columns_input = _sequence(slug_map.get("columns", []), "columns")
    slug_map["columns"] = columns_input
    for index, raw_column in enumerate(columns_input):
        column = _mapping(raw_column, f"columns[{index}]")
        table_slug = column.get("table_slug")
        name = column.get("name")
        if not isinstance(table_slug, str) or not isinstance(name, str):
            continue
        declaration = {key: value for key, value in column.items() if key not in {"table_slug", "name", "type", "required", "ref", "default"}}
        field_declarations[(table_slug, name)] = declaration
        cleaned_column = _strip_secret_flags(column)
        if not isinstance(cleaned_column, MappingABC):
            raise _AdapterQuarantine("invalid_field_ui_declaration", f"columns[{index}]", "column declaration must be an object")
        slug_map["columns"][index] = dict(cleaned_column)
    ui_declarations: dict[int, Mapping[str, object]] = {}
    ui_versions: list[tuple[str, int | None]] = []
    for collection in _SCOPED_COLLECTIONS:
        raw_items = slug_map.get(collection)
        if not isinstance(raw_items, list):
            continue
        prepared: list[object] = []
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, MappingABC) or any(not isinstance(key, str) for key in raw):
                prepared.append(raw)
                continue
            item = dict(raw)
            if "scope" in item:
                raw_scope = item["scope"]
                scopes[(collection, index)] = _scope(raw_scope, f"{collection}[{index}].scope")
                if collection == "actions":
                    action_scopes[index] = scopes[(collection, index)]
                item.pop("scope")
            if collection == "tabs" and "default_project_bind" in item:
                binding = item["default_project_bind"]
                if not isinstance(binding, str) or binding not in _SUPPORTED_PROJECT_BINDINGS:
                    raise _AdapterQuarantine("unsupported_project_binding", f"tabs[{index}].default_project_bind", "default_project_bind must be local or global")
            if collection == "views":
                slug = item.get("slug")
                slug_path = f"views[{index}].ui_json"
                ui = item.get("ui_json", {})
                if not isinstance(ui, MappingABC):
                    raise _AdapterQuarantine("unsupported_ui_json_shape", slug_path, "ui_json must be an object")
                ui_version: int | None = None
                if "version" in ui:
                    ui_version = ui["version"]
                    if not isinstance(ui_version, int) or isinstance(ui_version, bool):
                        raise _AdapterQuarantine("invalid_ui_json_version", f"{slug_path}.version", "ui_json.version must be an integer")
                    if ui_version not in _SUPPORTED_UI_VERSIONS:
                        raise _AdapterQuarantine("unsupported_ui_json_version", f"{slug_path}.version", f"unsupported ui_json.version {ui_version}")
                ui_kind = ui.get("kind")
                view_kind = item.get("kind")
                if view_kind is not None and (not isinstance(view_kind, str) or view_kind not in _SUPPORTED_UI_KINDS):
                    raise _AdapterQuarantine("unsupported_view_kind", f"views[{index}].kind", f"unsupported view kind {view_kind}")
                if ui_kind is not None and (not isinstance(ui_kind, str) or not ui_kind.strip()):
                    raise _AdapterQuarantine("invalid_ui_json_kind", f"{slug_path}.kind", "ui_json.kind must be a non-empty string")
                if view_kind is not None and ui_kind is not None and view_kind != ui_kind:
                    raise _AdapterQuarantine("ui_kind_mismatch", f"{slug_path}.kind", "view kind and ui_json.kind must match")
                if "data_scope" in ui:
                    data_scope = _scope(ui["data_scope"], f"{slug_path}.data_scope")
                    if set(data_scope) - {"chats"}:
                        raise _AdapterQuarantine("unsupported_view_data_scope", f"{slug_path}.data_scope", "view data_scope only supports chats")
                unknown_ui = set(ui) - _SUPPORTED_UI_ROOT_KEYS
                if unknown_ui:
                    raise _AdapterQuarantine("unsupported_ui_json_property", slug_path, f"unsupported ui_json keys: {sorted(unknown_ui)}")
                if ui_kind is not None and (not isinstance(ui_kind, str) or ui_kind not in _SUPPORTED_UI_KINDS):
                    raise _AdapterQuarantine("unsupported_ui_json_kind", f"{slug_path}.kind", f"unsupported ui_json kind {ui_kind}")
                if "kind" not in ui and "kind" not in item:
                    raise _AdapterQuarantine("missing_ui_json_kind", f"{slug_path}.kind", "a view kind is required")
                _validate_ui_semantics(ui, slug_path)
                _validate_ui_tree(ui, slug_path, action_ids)
                _validate_ui_view_references(ui, slug_path, view_slugs)
                _validate_ui_action_references(ui, slug_path, action_ids)
                ui_declarations[index] = dict(ui)
                ui_versions.append((str(slug) if isinstance(slug, str) else f"views[{index}]", ui_version))
            prepared.append(item)
        slug_map[collection] = prepared

    # The central normalizer predates first-party declarative tools without an
    # external MCP server. Translate those tools here, retaining server_id=None.
    if "mcp_tools" in slug_map:
        _sequence(slug_map["mcp_tools"], "mcp_tools")
        slug_map["mcp_tools"] = []

    negotiation = LegacyNegotiation(
        source_format=source_format,
        source_version=raw_version,
        explicit_syntax_version=explicit,
        ui_json_versions=tuple(ui_versions),
    )
    return slug_map, negotiation, scopes, ui_declarations, field_declarations, action_scopes


def _validate_json_schema_node(value: object, path: str) -> None:
    if isinstance(value, MappingABC):
        for key, child in value.items():
            if key == "type" and (not isinstance(child, str) or child not in _SUPPORTED_SCHEMA_TYPES):
                raise _AdapterQuarantine("unsupported_mcp_input_schema", f"{path}.type", f"unsupported property type {child}")
            if key in {"enum", "required"} and (
                not isinstance(child, list)
                or any(not isinstance(item, (str, int, float, bool)) for item in child)
            ):
                raise _AdapterQuarantine("invalid_mcp_input_schema", f"{path}.{key}", f"{key} must be an array of scalar values")
            if key == "items" and not isinstance(child, MappingABC):
                raise _AdapterQuarantine("invalid_mcp_input_schema", f"{path}.items", "items must be a schema object")
            _validate_json_schema_node(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_schema_node(child, f"{path}[{index}]")


def _validate_input_schema(value: object, path: str) -> Mapping[str, object]:
    schema = _mapping(value, path)
    unknown = set(schema) - _SUPPORTED_SCHEMA_KEYS
    if unknown:
        raise _AdapterQuarantine("unsupported_mcp_schema_keyword", path, f"unsupported schema keys: {sorted(unknown)}")
    if schema.get("type") != "object":
        raise _AdapterQuarantine("unsupported_mcp_input_schema", path, "MCP input schema must have type object")
    _validate_json_schema_node(schema, path)
    properties = _mapping(schema.get("properties", {}), f"{path}.properties")
    for name, raw_property in properties.items():
        prop_path = f"{path}.properties.{name}"
        prop = _mapping(raw_property, prop_path)
        extra = set(prop) - _SUPPORTED_SCHEMA_PROPERTY_KEYS
        if extra:
            raise _AdapterQuarantine("unsupported_mcp_schema_keyword", prop_path, f"unsupported property schema keys: {sorted(extra)}")
        prop_type = prop.get("type")
        if prop_type is not None and (not isinstance(prop_type, str) or prop_type not in _SUPPORTED_SCHEMA_TYPES):
            raise _AdapterQuarantine("unsupported_mcp_input_schema", f"{prop_path}.type", f"unsupported property type {prop_type}")
        if "items" in prop:
            _mapping(prop["items"], f"{prop_path}.items")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise _AdapterQuarantine("invalid_mcp_input_schema", f"{path}.required", "required must be an array of strings")
    if len(set(required)) != len(required) or set(required) - set(properties):
        raise _AdapterQuarantine("invalid_mcp_input_schema", f"{path}.required", "required entries must uniquely name declared properties")
    return dict(schema)


def _translate_mcp_tools(
    raw_tools: object,
    *,
    schemas: Mapping[str, ModuleSchema],
    field_ids: Mapping[tuple[str, str], str],
    stable_ids: Mapping[str, str] | None,
    server_ids: Mapping[str, str],
    scopes: Mapping[tuple[str, int], Mapping[str, object]],
) -> tuple[McpToolDeclaration, ...]:
    tools = _sequence(raw_tools, "mcp_tools")
    result: list[McpToolDeclaration] = []
    for index, raw in enumerate(tools):
        path = f"mcp_tools[{index}]"
        item = _mapping(raw, path)
        allowed = {"id", "name", "server", "label", "description", "enabled", "kind", "params_schema", "implementation", "scope"}
        unknown = set(item) - allowed
        if unknown:
            raise _AdapterQuarantine("unsupported_mcp_tool_shape", path, f"unsupported MCP tool fields: {sorted(unknown)}")
        legacy_id = item.get("id")
        name = item.get("name")
        if not isinstance(legacy_id, str) or not legacy_id.strip() or not isinstance(name, str) or not name.strip():
            raise _AdapterQuarantine("invalid_mcp_tool_identity", path, "MCP tool id and name are required")
        if stable_ids is None or not isinstance(stable_ids.get(name), str) or not stable_ids[name].strip():
            raise _AdapterQuarantine("mcp_tool_identity_missing", f"{path}.name", f"no stable MCP tool ID supplied for {name}")
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in _SUPPORTED_MCP_KINDS:
            raise _AdapterQuarantine("unsupported_mcp_tool_kind", f"{path}.kind", f"unsupported MCP implementation {kind}")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise _AdapterQuarantine("invalid_mcp_enabled", f"{path}.enabled", "enabled must be boolean")
        input_schema = _validate_input_schema(item.get("params_schema"), f"{path}.params_schema")
        implementation = _mapping(item.get("implementation"), f"{path}.implementation")
        extra_impl = set(implementation) - {"table_slug", "query", "upsert_key"}
        if extra_impl:
            raise _AdapterQuarantine("unsupported_mcp_implementation", f"{path}.implementation", f"unsupported MCP implementation properties: {sorted(extra_impl)}")
        table_slug = implementation.get("table_slug")
        if not isinstance(table_slug, str) or table_slug not in schemas:
            raise _AdapterQuarantine("unresolved_mcp_schema", f"{path}.implementation.table_slug", f"unknown schema {table_slug}")
        schema = schemas[table_slug]
        query = implementation.get("query", {})
        query = _mapping(query, f"{path}.implementation.query")
        if set(query) - {"filter", "limit"}:
            raise _AdapterQuarantine("unsupported_mcp_query", f"{path}.implementation.query", "query supports filter and limit only")
        if "limit" in query and (not isinstance(query["limit"], int) or isinstance(query["limit"], bool) or query["limit"] < 1):
            raise _AdapterQuarantine("invalid_mcp_query_limit", f"{path}.implementation.query.limit", "limit must be a positive integer")
        filters = _mapping(query.get("filter", {}), f"{path}.implementation.query.filter")
        implementation_field_ids: dict[str, str] = {}
        for field_name, filter_value in filters.items():
            field = schema.field_by_name(field_name)
            stable_field_id = field_ids.get((table_slug, field_name))
            if field is None or not isinstance(stable_field_id, str) or stable_field_id != field.field_id:
                raise _AdapterQuarantine("unresolved_mcp_filter", f"{path}.implementation.query.filter.{field_name}", "filter field requires a declared stable field ID")
            if isinstance(filter_value, MappingABC):
                if set(filter_value) != {"op", "value"} or filter_value.get("op") not in _SUPPORTED_MCP_QUERY_OPERATORS:
                    raise _AdapterQuarantine("unsupported_mcp_filter_operator", f"{path}.implementation.query.filter.{field_name}", "filter objects require a supported op and value")
                filter_value = dict(filter_value)
            elif not isinstance(filter_value, (str, int, float, bool, list)) and filter_value is not None:
                raise _AdapterQuarantine("invalid_mcp_filter_value", f"{path}.implementation.query.filter.{field_name}", "filter value must be a JSON scalar or array")
            implementation_field_ids[field_name] = stable_field_id
        upsert_key = implementation.get("upsert_key", [])
        if not isinstance(upsert_key, list) or any(not isinstance(field_name, str) for field_name in upsert_key):
            raise _AdapterQuarantine("unsupported_mcp_upsert_key", f"{path}.implementation.upsert_key", "upsert_key must be an array of declared field names")
        for field_name in upsert_key:
            field = schema.field_by_name(field_name)
            stable_field_id = field_ids.get((table_slug, field_name))
            if field is None or not isinstance(stable_field_id, str) or stable_field_id != field.field_id:
                raise _AdapterQuarantine("unresolved_mcp_upsert_field", f"{path}.implementation.upsert_key", f"upsert key field {field_name} requires a declared stable field ID")
            implementation_field_ids[field_name] = stable_field_id
        server_name = item.get("server")
        server_id = None
        if server_name is not None:
            if not isinstance(server_name, str) or not server_name.strip() or server_name not in server_ids:
                raise _AdapterQuarantine("mcp_tool_server_unresolved", f"{path}.server", "MCP tool server must resolve to a stable declared server ID")
            server_id = server_ids[server_name]
        declaration = dict(item)
        scope = scopes.get(("mcp_tools", index))
        if scope is not None:
            declaration["scope"] = scope
        result.append(McpToolDeclaration(
            tool_id=stable_ids[name],
            name=name,
            kind=kind,
            enabled=enabled,
            input_schema=input_schema,
            implementation={**implementation, "schema_id": schema.schema_id},
            schema_id=schema.schema_id,
            implementation_field_ids=implementation_field_ids,
            server_id=server_id,
            declaration=declaration,
        ))
    if len({tool.tool_id for tool in result}) != len(result):
        raise _AdapterQuarantine("duplicate_mcp_tool_id", "mcp_tools", "stable MCP tool IDs must be unique")
    if len({tool.name for tool in result}) != len(result):
        raise _AdapterQuarantine("duplicate_mcp_tool_name", "mcp_tools", "MCP tool names must be unique")
    return tuple(result)


_DeclarationT = TypeVar("_DeclarationT", ViewDeclaration, NavigationDeclaration, ActionDeclaration, MaterializeDeclaration)


def _restore_declaration_scope(
    item: _DeclarationT,
    scope: Mapping[str, object] | None,
) -> _DeclarationT:
    if scope is None:
        return item
    declaration = dict(item.declaration)
    declaration["scope"] = scope
    return replace(item, declaration=declaration)


def _restore_scopes(
    candidate: CapabilityVersion,
    scopes: Mapping[tuple[str, int], Mapping[str, object]],
    ui_declarations: Mapping[int, Mapping[str, object]],
    action_scopes: Mapping[int, Mapping[str, object]],
    schema_ids: Mapping[str, str],
    field_declarations: Mapping[tuple[str, str], Mapping[str, object]],
) -> _TranslatedPayload:
    schemas: list[ModuleSchema] = []
    scope_by_schema_slug: dict[str, Mapping[str, object]] = {}
    schema_id_to_slug = {stable_id: slug for slug, stable_id in schema_ids.items()}
    for (collection, index), scope in scopes.items():
        if collection == "tables" and index < len(candidate.schemas):
            table_slug = schema_id_to_slug.get(candidate.schemas[index].schema_id)
            if table_slug is not None:
                scope_by_schema_slug[table_slug] = scope
    for schema in candidate.schemas:
        scope = scope_by_schema_slug.get(schema.slug)
        fields = tuple(
            replace(field, declaration=field_declarations.get((schema.slug, field.name), field.declaration))
            for field in schema.fields
        )
        schemas.append(replace(schema, fields=fields, scope=scope) if scope is not None else replace(schema, fields=fields))

    views = tuple(
        replace(item, declaration={**dict(item.declaration), "ui_json": ui_declarations.get(i, item.declaration.get("ui_json", {}))})
        if i in ui_declarations else item
        for i, item in enumerate(candidate.metadata.views)
    )
    views = tuple(_restore_declaration_scope(item, scopes.get(("views", i))) for i, item in enumerate(views))
    navigation = tuple(_restore_declaration_scope(item, scopes.get(("tabs", i))) for i, item in enumerate(candidate.metadata.navigation))
    actions = tuple(
        _restore_declaration_scope(item, action_scopes.get(i, scopes.get(("actions", i))))
        for i, item in enumerate(candidate.actions)
    )
    materialize = tuple(_restore_declaration_scope(item, scopes.get(("materialize", i))) for i, item in enumerate(candidate.materialize))
    return _TranslatedPayload(
        metadata=replace(candidate.metadata, views=views, navigation=navigation),
        schemas=tuple(schemas),
        actions=actions,
        materialize=materialize,
        # The translated field/action declarations are produced here and must travel
        # with the payload: dropping them made every adaptation fail with a TypeError
        # ("missing field_declarations and action_scopes") instead of a typed result,
        # and silently lost the UI declarations the adapter exists to preserve.
        field_declarations=field_declarations,
        action_scopes=action_scopes,
    )


class LegacyMetadataAdapter:
    """Negotiate legacy syntax v1 and translate it into a draft capability."""

    def adapt(
        self,
        *,
        module_id: str,
        definition_id: str,
        version: str,
        metadata: Mapping[str, object],
        source_digest: str,
        schema_ids: Mapping[str, str] | None = None,
        field_ids: Mapping[tuple[str, str], str] | None = None,
        view_ids: Mapping[str, str] | None = None,
        action_operation_ids: Mapping[str, str] | None = None,
        mcp_server_ids: Mapping[str, str] | None = None,
        mcp_tool_ids: Mapping[str, str] | None = None,
        mcp_manifest: Mapping[str, object] | None = None,
        dependency_ids: tuple[str, ...] = (),
        conflict_ids: tuple[str, ...] = (),
        schema_version: str = "module-meta-v1",
    ) -> LegacyAdapterResult:
        try:
            slug_map, negotiation, scopes, ui_declarations, field_declarations, action_scopes = _prepare_source(
                metadata, action_operation_ids=action_operation_ids,
            )
            actual_source_digest = source_digest
            normalized = normalize_legacy_module(
                module_id=module_id,
                definition_id=definition_id,
                version=version,
                source_digest=actual_source_digest,
                slug_map=slug_map,
                schema_ids=schema_ids or {},
                field_ids=field_ids or {},
                view_ids=view_ids or {},
                action_operation_ids=action_operation_ids or {},
                mcp_server_ids=mcp_server_ids,
                mcp_tool_ids=mcp_tool_ids,
                mcp_manifest=mcp_manifest,
                dependency_ids=dependency_ids,
                conflict_ids=conflict_ids,
                schema_version=schema_version,
            )
            if normalized.candidate is None:
                return LegacyAdapterResult(None, normalized.issues, negotiation)

            base = normalized.candidate
            translated = _restore_scopes(base, scopes, ui_declarations, action_scopes, schema_ids or {}, field_declarations)
            server_ids = {server.name: server.server_id for server in base.mcp_servers}
            mcp_tools = _translate_mcp_tools(
                metadata.get("mcp_tools", []),
                schemas={schema.slug: schema for schema in translated.schemas},
                field_ids=field_ids or {},
                stable_ids=mcp_tool_ids,
                server_ids=server_ids,
                scopes=scopes,
            )
            declarations = dict(translated.metadata.declarations)
            declarations["legacy_adapter"] = {
                "source_format": negotiation.source_format,
                "source_version": negotiation.source_version,
                "explicit_syntax_version": negotiation.explicit_syntax_version,
                "ui_json_versions": negotiation.ui_json_versions,
            }
            adapted_metadata = replace(translated.metadata, declarations=declarations)
            payload = {
                "definition_id": base.definition_id,
                "module_id": base.module_id,
                "version": base.version,
                "schema_version": base.schema_version,
                "metadata": adapted_metadata,
                "schemas": translated.schemas,
                "actions": translated.actions,
                "mcp_servers": base.mcp_servers,
                "mcp_tools": mcp_tools,
                "materialize": translated.materialize,
                "dependencies": base.dependencies,
                "conflicts": base.conflicts,
            }
            digest = canonical_digest(payload)
            candidate = CapabilityVersion(**payload, content_digest=digest, lifecycle=base.lifecycle)
            return LegacyAdapterResult(candidate, (), negotiation)
        except _AdapterQuarantine as exc:
            return LegacyAdapterResult(None, (exc.issue,), None)
        except (TypeError, ValueError, KeyError) as exc:
            issue = NormalizationIssue("legacy_adapter_failed", "metadata", str(exc))
            return LegacyAdapterResult(None, (issue,), None)


def adapt_legacy_module(**kwargs: object) -> LegacyAdapterResult:
    """Convenience API for callers that do not need an adapter instance."""
    return LegacyMetadataAdapter().adapt(**kwargs)  # type: ignore[arg-type]


__all__ = [
    "LegacyAdapterResult", "LegacyMetadataAdapter", "LegacyNegotiation", "adapt_legacy_module",
]
