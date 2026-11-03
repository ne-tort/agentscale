"""Version negotiation and legacy UI compatibility tests; not executed in freeze."""
from __future__ import annotations

from copy import deepcopy

from .acceptance_fixtures import normalize_kwargs
from prodavan.application.migration.module_capabilities.contracts import VersionLifecycle, canonical_digest
from prodavan.application.migration.module_capabilities.legacy_adapter import adapt_legacy_module


def _adapter_kwargs(metadata: dict[str, object] | None = None) -> dict[str, object]:
    inputs = normalize_kwargs()
    legacy = metadata if metadata is not None else inputs.pop("slug_map")
    assert isinstance(legacy, dict)
    kwargs = {
        "module_id": inputs["module_id"],
        "definition_id": inputs["definition_id"],
        "version": inputs["version"],
        "metadata": legacy,
        "schema_ids": inputs["schema_ids"],
        "field_ids": inputs["field_ids"],
        "view_ids": inputs["view_ids"],
        "action_operation_ids": {"list_items": "operation:rows.select"},
        "mcp_server_ids": inputs["mcp_server_ids"],
        "mcp_tool_ids": inputs["mcp_tool_ids"],
        "mcp_manifest": inputs["mcp_manifest"],
        "source_digest": canonical_digest(legacy),
    }
    return kwargs


def _adapt(metadata: dict[str, object] | None = None):
    return adapt_legacy_module(**_adapter_kwargs(metadata))


def test_negotiates_legacy_slug_map_and_manifest_syntax_v1() -> None:
    legacy = _adapt()
    assert legacy.accepted
    assert legacy.negotiation is not None
    assert legacy.negotiation.source_format == "legacy-slug-map-v1"
    assert legacy.negotiation.source_version == 1
    assert not legacy.negotiation.explicit_syntax_version
    assert legacy.negotiation.ui_json_versions == (("items_list", None),)

    manifest = _adapter_kwargs()["metadata"]
    assert isinstance(manifest, dict)
    manifest = deepcopy(manifest)
    manifest["syntax_version"] = 1
    manifest["views"][0]["ui_json"]["version"] = 1
    negotiated = _adapt(manifest)
    assert negotiated.accepted
    assert negotiated.negotiation is not None
    assert negotiated.negotiation.source_format == "manifest-v1"
    assert negotiated.negotiation.explicit_syntax_version
    assert negotiated.negotiation.ui_json_versions == (("items_list", 1),)


def test_translates_supported_shapes_without_losing_binding_scope_or_ui_filters() -> None:
    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["tables"][0]["scope"] = {"projects": "all", "chats": "current", "active_chat": "required"}
    metadata["tabs"][0]["default_project_bind"] = "local"
    metadata["actions"][0]["scope"] = {"projects": "bound", "chats": "current"}
    metadata["views"][0]["ui_json"].update({
        "version": 1,
        "data_scope": {"chats": "all"},
        "row_filter": {"status": "active"},
        "columns": [{"field": "name", "label": "Name"}],
    })
    result = _adapt(metadata)
    assert result.accepted
    assert result.candidate is not None
    assert result.candidate.lifecycle is VersionLifecycle.DRAFT
    assert result.candidate.schemas[0].scope == {
        "projects": "all", "chats": "current", "active_chat": "required",
    }
    assert result.candidate.metadata.navigation[0].declaration["default_project_bind"] == "local"
    assert result.candidate.actions[0].declaration["scope"] == {"projects": "bound", "chats": "current"}
    assert result.candidate.metadata.views[0].declaration["ui_json"]["data_scope"] == {"chats": "all"}
    assert result.candidate.metadata.views[0].declaration["ui_json"]["row_filter"] == {"status": "active"}
    assert result.candidate.materialize[0].source_schema_id == "schema:mod_example:items"
    assert result.candidate.mcp_tools[0].schema_id == "schema:mod_example:items"
    assert result.candidate.metadata.seed_rows[0].field_ids == {"name": "field:mod_example:items:name"}


def test_preserves_global_binding_and_project_ids_column_semantics() -> None:
    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["columns"].append({
        "table_slug": "items", "name": "project_ids", "type": "json", "default": [],
    })
    metadata["tabs"][0]["default_project_bind"] = "global"
    result = _adapt(metadata)
    assert result.accepted
    assert result.candidate is not None
    assert result.candidate.metadata.navigation[0].declaration["default_project_bind"] == "global"
    assert result.candidate.schemas[0].field_by_name("project_ids") is not None
    assert result.candidate.schemas[0].field_by_name("project_ids").default_value == ()


def test_rejects_explicitly_empty_source_digest_instead_of_falling_back() -> None:
    kwargs = _adapter_kwargs()
    kwargs["source_digest"] = ""
    result = adapt_legacy_module(**kwargs)
    assert not result.accepted
    assert result.issues[0].code == "invalid_source_digest"


def test_quarantines_unknown_metadata_and_ui_versions_with_exact_paths() -> None:
    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["syntax_version"] = 2
    result = _adapt(metadata)
    assert not result.accepted
    assert result.issues[0].code == "unsupported_meta_version"
    assert result.issues[0].path == "syntax_version"

    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["views"][0]["ui_json"]["version"] = 2
    result = _adapt(metadata)
    assert not result.accepted
    assert result.issues[0].code == "unsupported_ui_json_version"
    assert result.issues[0].path == "views[0].ui_json.version"


def test_quarantines_unsupported_binding_scope_and_unresolved_ui_action() -> None:
    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["tabs"][0]["default_project_bind"] = "inherit"
    result = _adapt(metadata)
    assert not result.accepted
    assert result.issues[0].code == "unsupported_project_binding"
    assert result.issues[0].path == "tabs[0].default_project_bind"

    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["views"][0]["ui_json"]["row_actions"] = [
        {"kind": "invoke_action", "action": "not_registered"},
    ]
    result = _adapt(metadata)
    assert not result.accepted
    assert result.issues[0].code == "unresolved_ui_action_reference"
    assert result.issues[0].path == "views[0].ui_json.row_actions[0].action"


def test_action_adapter_requires_registered_typed_operation_and_never_dispatches() -> None:
    kwargs = _adapter_kwargs()
    kwargs["action_operation_ids"] = {}
    result = adapt_legacy_module(**kwargs)
    assert not result.accepted
    assert result.issues[0].code == "action_operation_unmapped"
    assert result.issues[0].path == "actions[0].id"
    assert not hasattr(result, "dispatch")
    assert not hasattr(result, "invoke")

    metadata = kwargs["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["actions"][0]["kind"] = "python.execute"
    kwargs["metadata"] = metadata
    kwargs["action_operation_ids"] = {"list_items": "operation:rows.select"}
    result = adapt_legacy_module(**kwargs)
    assert not result.accepted
    assert result.issues[0].code == "unsupported_action_kind"
    assert result.issues[0].path == "actions[0].kind"


def test_rejects_unregistered_typed_action_operation_mapping() -> None:
    kwargs = _adapter_kwargs()
    kwargs["action_operation_ids"] = {"list_items": "python.execute"}
    result = adapt_legacy_module(**kwargs)
    assert not result.accepted
    assert result.issues[0].code == "unregistered_typed_action_operation"
    assert result.issues[0].path == "actions[0].id"


def test_legacy_mcp_tool_without_server_is_typed_and_stably_identified() -> None:
    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["mcp_tools"][0].pop("server")
    metadata["mcp_tools"][0]["implementation"]["query"]["filter"] = {"name": "Ada"}
    result = _adapt(metadata)
    assert result.accepted
    assert result.candidate is not None
    tool = result.candidate.mcp_tools[0]
    assert tool.server_id is None
    assert tool.schema_id == "schema:mod_example:items"
    assert tool.implementation_field_ids == {"name": "field:mod_example:items:name"}


def test_quarantines_missing_mcp_tool_identity_and_unsupported_filter_shape() -> None:
    kwargs = _adapter_kwargs()
    kwargs["mcp_tool_ids"] = {}
    result = adapt_legacy_module(**kwargs)
    assert not result.accepted
    assert result.issues[0].code == "mcp_tool_identity_missing"
    assert result.issues[0].path == "mcp_tools[0].name"

    metadata = kwargs["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["mcp_tools"][0]["implementation"]["query"]["filter"] = {"unlisted": "value"}
    kwargs["metadata"] = metadata
    kwargs["mcp_tool_ids"] = {"items_list": "tool:items:list"}
    result = adapt_legacy_module(**kwargs)
    assert not result.accepted
    assert result.issues[0].code == "unresolved_mcp_filter"


def test_adapter_candidate_digest_includes_negotiated_version_and_ui_metadata() -> None:
    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["syntax_version"] = 1
    metadata["views"][0]["ui_json"]["version"] = 1
    kwargs = _adapter_kwargs(metadata)
    first = adapt_legacy_module(**kwargs)
    metadata["views"][0]["ui_json"]["title"] = "changed"
    kwargs = _adapter_kwargs(metadata)
    second = adapt_legacy_module(**kwargs)
    assert first.accepted and second.accepted
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.content_digest != second.candidate.content_digest


def test_candidate_is_draft_only_and_canonical_digest_is_order_independent() -> None:
    first = _adapt()
    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["tables"][0] = dict(reversed(tuple(metadata["tables"][0].items())))
    second = _adapt(metadata)
    assert first.accepted and second.accepted
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.lifecycle is VersionLifecycle.DRAFT
    assert first.candidate.content_digest == second.candidate.content_digest
    assert not hasattr(first, "publish")
    assert not hasattr(first, "runtime")


def test_normalizer_unsupported_shape_is_quarantined_not_dropped() -> None:
    metadata = _adapter_kwargs()["metadata"]
    assert isinstance(metadata, dict)
    metadata = deepcopy(metadata)
    metadata["tables"][0]["scope"] = {"projects": "tenant"}
    result = _adapt(metadata)
    assert not result.accepted
    assert result.issues[0].code == "unsupported_project_scope"
    assert result.issues[0].path == "tables[0].scope.projects"
