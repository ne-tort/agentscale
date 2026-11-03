"""Acceptance checks for typed normalization and quarantine; not executed in freeze."""
from __future__ import annotations

from dataclasses import replace

from .acceptance_fixtures import normalize_kwargs, version
from prodavan.application.migration.module_capabilities.contracts import VersionLifecycle
from prodavan.application.migration.module_capabilities.dependencies import validate_module_dependencies
from prodavan.application.migration.module_capabilities.normalization import normalize_legacy_module


def test_legacy_module_normalizes_to_typed_ids_and_canonical_digest() -> None:
    result = normalize_legacy_module(**normalize_kwargs())
    assert result.accepted
    assert result.candidate is not None
    assert result.candidate.content_digest.startswith("sha256:")
    assert result.candidate.schemas[0].schema_id == "schema:mod_example:items"
    assert result.candidate.schemas[0].fields[0].field_id == "field:mod_example:items:name"
    assert result.candidate.mcp_tools[0].schema_id == "schema:mod_example:items"
    assert result.candidate.materialize[0].source_schema_id == "schema:mod_example:items"
    assert len(result.candidate.mcp_servers) == 1
    assert result.candidate.mcp_servers[0].server_id == "server:example"
    assert result.candidate.mcp_tools[0].server_id == "server:example"
    assert result.candidate.mcp_tools[0].implementation_field_ids == {}


def test_canonical_digest_is_stable_for_object_key_order() -> None:
    args = normalize_kwargs()
    first_meta = args["slug_map"]["tables"][0]
    args["slug_map"]["tables"][0] = {key: first_meta[key] for key in reversed(tuple(first_meta))}
    result = normalize_legacy_module(**args)
    assert result.accepted
    assert result.candidate is not None
    baseline = normalize_legacy_module(**normalize_kwargs())
    assert baseline.accepted and baseline.candidate is not None
    assert result.candidate.content_digest == baseline.candidate.content_digest


def test_missing_schema_identity_is_quarantined_with_reason() -> None:
    args = normalize_kwargs()
    args["schema_ids"] = {}
    result = normalize_legacy_module(**args)
    assert not result.accepted
    assert result.issues[0].code == "schema_identity_missing"
    assert result.issues[0].path == "tables[0].slug"


def test_missing_reference_field_identity_is_quarantined() -> None:
    args = normalize_kwargs()
    args["slug_map"]["tables"].append({"slug": "groups", "label": "Groups"})
    args["slug_map"]["columns"].append({"table_slug": "items", "name": "group_id", "type": "ref", "ref": {"table_slug": "groups"}})
    args["schema_ids"]["groups"] = "schema:mod_example:groups"
    result = normalize_legacy_module(**args)
    assert not result.accepted
    assert result.issues[0].code == "reference_field_identity_missing"


def test_unsupported_legacy_action_kind_is_quarantined() -> None:
    args = normalize_kwargs()
    args["slug_map"]["actions"][0]["kind"] = "python.execute"
    result = normalize_legacy_module(**args)
    assert not result.accepted
    assert result.issues[0].code == "unsupported_action_kind"


def test_unsupported_legacy_foreach_secret_shape_is_quarantined() -> None:
    args = normalize_kwargs()
    args["slug_map"]["container_env_secrets"] = [{
        "foreach_rows": {"table_slug": "items", "field": "secret", "env_name_prefix": "ITEM_"},
    }]
    result = normalize_legacy_module(**args)
    assert not result.accepted
    assert result.issues[0].code == "unsupported_legacy_secret_expansion"


def test_mcp_server_requires_external_credential_header_reference() -> None:
    args = normalize_kwargs()
    args["mcp_manifest"] = {
        "mcpServers": {
            "example": {"url": "https://mcp.invalid", "headers": {"Authorization": "Bearer raw-token"}},
        }
    }
    args["mcp_server_ids"] = {"example": "server:example"}
    result = normalize_legacy_module(**args)
    assert not result.accepted
    assert result.issues[0].code == "inline_secret_forbidden"


def test_mcp_server_requires_first_class_server_id() -> None:
    args = normalize_kwargs()
    args["mcp_server_ids"] = {}
    result = normalize_legacy_module(**args)
    assert not result.accepted
    assert result.issues[0].code == "mcp_server_identity_map_missing"


def test_dependency_missing_and_conflict_are_not_publishable() -> None:
    candidate = version(dependencies=("missing",), conflicts=("conflicting",))
    conflict = version(module_id="conflicting", lifecycle=VersionLifecycle.PUBLISHED)
    result = validate_module_dependencies(candidate=candidate, available={"conflicting": conflict})
    assert not result.valid
    assert {issue.code for issue in result.issues} == {"missing_dependency", "published_conflict"}


def test_semver_constraints_are_bounded_and_fail_closed() -> None:
    from prodavan.application.migration.module_capabilities.dependencies import version_satisfies

    assert version_satisfies("1.4.0", "^1.2.0")
    assert not version_satisfies("2.0.0", "^1.2.0")
    assert version_satisfies("0.2.4", "^0.2.0")
    assert not version_satisfies("0.3.0", "^0.2.0")
    assert not version_satisfies("1.0.0", "* ")

def test_dependency_cycle_is_quarantined() -> None:
    first = version(module_id="first", dependencies=("second",))
    second = version(module_id="second", dependencies=("first",))
    result = validate_module_dependencies(candidate=first, available={"second": second, "first": first})
    assert not result.valid
    assert any(issue.code == "dependency_cycle" for issue in result.issues)
