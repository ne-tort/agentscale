"""Unit-тесты согласованности контрактов F0.

schema.json, protocol.json и types.ts читаются КАК ДАННЫЕ и сверяются с
доменными типами types.py / validation.py: capability, plugin_id, semver,
artifact_digest, protocol, запрещённые поля, RegistryOutcome, коды ошибок.
Тесты НЕ запускаются (verification freeze).
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pytest

from prodavan.domain.plugins import (
    CAPABILITY_ALLOWLIST,
    DecisionCode,
    EgressMode,
    MigrationEngine,
    PluginInstanceStatus,
    RegistryOutcome,
)
from prodavan.domain.plugins.types import (
    ARTIFACT_DIGEST_PATTERN,
    EGRESS_REF_PATTERN,
    EMBEDDED_EGRESS_FIELDS,
    PATH_PATTERN,
    PLUGIN_ID_PATTERN,
    PROTOCOL_VERSION,
    SEMVER_PATTERN,
    SELF_REFERENCE_FIELDS,
)
from prodavan.domain.plugins.validation import (
    MANIFEST_ALLOWED_FIELDS,
    MANIFEST_REQUIRED_FIELDS,
    ManifestValidationErrorCode,
    load_schema,
    validate_manifest,
)
try:
    from tests.unit.plugins.domain._fixtures import (
        VALID_DIGEST,
        protocol_path,
        schema_path,
        types_ts_path,
        valid_manifest_dict,
    )
except ImportError:  # запуск pytest из каталога domain без настроенного PYTHONPATH
    from _fixtures import (  # type: ignore[no-redef]
        VALID_DIGEST,
        protocol_path,
        schema_path,
        types_ts_path,
        valid_manifest_dict,
    )


def _read_json(path: Path) -> dict[str, Any]:
    """Прочитать JSON-контракт как данные."""
    assert path.is_file(), f"missing contract file: {path}"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _read_text(path: Path) -> str:
    """Прочитать текстовый контракт как данные."""
    assert path.is_file(), f"missing contract file: {path}"
    return path.read_text(encoding="utf-8")


# ───────────────────────── schema.json ─────────────────────────

def test_schema_uses_draft_2020_12_and_closed_root() -> None:
    schema = _read_json(schema_path())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False


def test_schema_required_fields_match_validator() -> None:
    schema = _read_json(schema_path())
    assert set(schema["required"]) == set(MANIFEST_REQUIRED_FIELDS)
    assert set(schema["properties"]) == set(MANIFEST_ALLOWED_FIELDS)


def test_schema_plugin_id_pattern_matches_types_py() -> None:
    schema = _read_json(schema_path())
    assert schema["properties"]["plugin_id"]["pattern"] == PLUGIN_ID_PATTERN.pattern


def test_schema_semver_patterns_match_types_py() -> None:
    schema = _read_json(schema_path())
    for field_name in ("version", "min_platform_version"):
        assert schema["properties"][field_name]["pattern"] == SEMVER_PATTERN.pattern


def test_schema_protocol_const_matches_types_py() -> None:
    schema = _read_json(schema_path())
    assert schema["properties"]["protocol"]["const"] == PROTOCOL_VERSION


def test_schema_capability_enum_matches_allowlist() -> None:
    schema = _read_json(schema_path())
    enum_values = set(schema["properties"]["capabilities"]["items"]["enum"])
    assert enum_values == CAPABILITY_ALLOWLIST
    assert "db.direct" not in enum_values
    assert "db.migrations" not in enum_values


def test_schema_migration_engines_match_str_enum() -> None:
    schema = _read_json(schema_path())
    engines = set(schema["properties"]["migrations"]["properties"]["engine"]["enum"])
    assert engines == {item.value for item in MigrationEngine}


def test_schema_forbids_self_reference_and_embedded_egress() -> None:
    schema = _read_json(schema_path())
    forbidden = {
        entry["required"][0]
        for entry in schema["not"]["anyOf"]
    }
    assert forbidden == SELF_REFERENCE_FIELDS | EMBEDDED_EGRESS_FIELDS
    assert "artifact_digest" in forbidden
    assert "signature" in forbidden


def test_schema_egress_ref_is_a_grant_reference_not_allowlist() -> None:
    schema = _read_json(schema_path())
    egress_ref = schema["properties"]["egress_ref"]
    assert egress_ref["type"] == "string"
    assert egress_ref["pattern"] == EGRESS_REF_PATTERN.pattern
    assert "allowed_hosts" not in schema["properties"]
    assert "allowed_paths" not in schema["properties"]
    assert "egress" not in schema["properties"]


def test_schema_closed_nested_objects() -> None:
    schema = _read_json(schema_path())
    for field_name in ("frontend", "backend", "migrations"):
        assert schema["properties"][field_name]["additionalProperties"] is False


def test_schema_accepts_valid_manifest_and_rejects_bad_ones() -> None:
    """Сверка живой схемы с валидатором (если jsonschema установлен)."""
    schema = _read_json(schema_path())
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(valid_manifest_dict())) == []

    bad = valid_manifest_dict()
    bad["version"] = "1.2"
    assert list(validator.iter_errors(bad))
    bad2 = valid_manifest_dict()
    bad2["artifact_digest"] = VALID_DIGEST
    assert list(validator.iter_errors(bad2))
    bad3 = valid_manifest_dict()
    bad3["allowed_hosts"] = ["webhooks.example.com"]
    assert list(validator.iter_errors(bad3))
    bad4 = valid_manifest_dict()
    bad4["capabilities"] = ["db.direct"]
    assert list(validator.iter_errors(bad4))


def test_load_schema_returns_same_payload_as_file() -> None:
    assert load_schema() == _read_json(schema_path())


# ───────────────────────── protocol.json ─────────────────────────

def test_protocol_declares_all_four_message_types() -> None:
    protocol = _read_json(protocol_path())
    defs = protocol["$defs"]
    expected = {
        "plugin.ready": defs["pluginReady"],
        "plugin.rpc.request": defs["pluginRpcRequest"],
        "plugin.rpc.response": defs["pluginRpcResponse"],
        "plugin.error": defs["pluginError"],
    }
    for message_type, definition in expected.items():
        assert definition["properties"]["type"]["const"] == message_type
        assert definition["additionalProperties"] is False
    assert len(protocol["oneOf"]) == 4


def test_protocol_requires_plugin_id_version_and_digest() -> None:
    protocol = _read_json(protocol_path())
    base_fields = {"protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce"}
    for def_name in ("pluginReady", "pluginRpcRequest", "pluginRpcResponse", "pluginError"):
        definition = protocol["$defs"][def_name]
        assert base_fields <= set(definition["required"]), def_name
        assert definition["properties"]["protocol"]["const"] == PROTOCOL_VERSION
        assert definition["properties"]["artifact_digest"]["pattern"] == ARTIFACT_DIGEST_PATTERN.pattern
        assert definition["properties"]["plugin_id"]["pattern"] == PLUGIN_ID_PATTERN.pattern


def test_protocol_requires_event_origin_check() -> None:
    protocol = _read_json(protocol_path())
    origin = protocol["$defs"]["eventOrigin"]
    assert "origin" in origin["required"]
    assert origin["additionalProperties"] is False
    assert "https://" in origin["properties"]["origin"]["pattern"]
    assert "event" in protocol["$defs"]["pluginReady"]["properties"]
    assert "event" in protocol["$defs"]["pluginRpcRequest"]["properties"]


def test_protocol_handshake_requirements_documented() -> None:
    protocol = _read_json(protocol_path())
    comment = protocol["$comment"]
    for needle in ("origin", "WindowProxy", "artifact_digest", "nonce", "timeout", "token"):
        assert needle in comment


def test_protocol_rpc_response_error_shape_is_closed() -> None:
    protocol = _read_json(protocol_path())
    response = protocol["$defs"]["pluginRpcResponse"]
    assert "if" in response and "then" in response and "else" in response
    assert "error" in response["then"]["required"]
    assert protocol["$defs"]["rpcError"]["additionalProperties"] is False


# ───────────────────────── types.ts ─────────────────────────

def _union_values(source: str, type_name: str) -> set[str]:
    """Извлечь строковые литералы из TS type alias / union как данные."""
    match = re.search(
        rf"export type {re.escape(type_name)}\s*=\s*([\s\S]*?);",
        source,
    )
    assert match, f"type {type_name} not found in types.ts"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_types_ts_has_no_imports() -> None:
    source = _read_text(types_ts_path())
    assert not re.search(r"^\s*(import|export)\b[^\n]*\bfrom\b", source, flags=re.MULTILINE)
    assert "from \"" not in source


def test_types_ts_capability_enum_matches_python() -> None:
    source = _read_text(types_ts_path())
    assert _union_values(source, "Capability") == CAPABILITY_ALLOWLIST


def test_types_ts_egress_mode_matches_python() -> None:
    source = _read_text(types_ts_path())
    assert _union_values(source, "EgressMode") == {item.value for item in EgressMode}


def test_types_ts_instance_status_matches_python() -> None:
    source = _read_text(types_ts_path())
    assert _union_values(source, "PluginInstanceStatus") == {
        item.value for item in PluginInstanceStatus
    }


def test_types_ts_registry_outcome_matches_python() -> None:
    source = _read_text(types_ts_path())
    assert _union_values(source, "RegistryOutcome") == {item.value for item in RegistryOutcome}


def test_types_ts_decision_code_matches_python() -> None:
    source = _read_text(types_ts_path())
    assert _union_values(source, "DecisionCode") == {item.value for item in DecisionCode}


def test_types_ts_validation_codes_match_python() -> None:
    source = _read_text(types_ts_path())
    assert _union_values(source, "ManifestValidationErrorCode") == {
        item.value for item in ManifestValidationErrorCode
    }


def test_types_ts_protocol_const_matches_python() -> None:
    source = _read_text(types_ts_path())
    match = re.search(r'PLUGIN_RPC_PROTOCOL\s*=\s*"([^"]+)"', source)
    assert match and match.group(1) == PROTOCOL_VERSION


def test_types_ts_manifest_fields_match_python_and_schema() -> None:
    source = _read_text(types_ts_path())
    match = re.search(
        r"export interface PluginManifest\s*\{([\s\S]*?)\n\}",
        source,
    )
    assert match
    fields = set(re.findall(r"^\s*([a-z_]+)\??:", match.group(1), flags=re.MULTILINE))
    assert fields == set(MANIFEST_ALLOWED_FIELDS)
    assert fields == set(_read_json(schema_path())["properties"])


def test_types_ts_manifest_has_no_self_reference_or_egress_allowlist() -> None:
    source = _read_text(types_ts_path())
    match = re.search(
        r"export interface PluginManifest\s*\{([\s\S]*?)\n\}",
        source,
    )
    assert match
    body = match.group(1)
    assert "artifact_digest" not in body
    assert "signature" not in body
    assert "allowed_hosts" not in body
    assert "allowed_paths" not in body
    assert "egress_ref:" in body


def test_types_ts_message_types_match_protocol_json() -> None:
    source = _read_text(types_ts_path())
    protocol = _read_json(protocol_path())
    for literal in ("plugin.ready", "plugin.rpc.request", "plugin.rpc.response", "plugin.error"):
        assert f'"{literal}"' in source
    assert set(_union_values(source, "PluginRpcMethod")) == set(
        protocol["$defs"]["pluginRpcRequest"]["properties"]["method"]["enum"]
    )
    assert set(_union_values(source, "PluginRpcErrorCode")) == set(
        protocol["$defs"]["rpcError"]["properties"]["code"]["enum"]
    )


def test_artifact_digest_appears_in_protocol_but_not_in_manifest() -> None:
    """artifact_digest — поле сообщений handshake, но не манифеста."""
    schema = _read_json(schema_path())
    protocol = _read_json(protocol_path())
    assert "artifact_digest" not in schema["properties"]
    assert "artifact_digest" in protocol["$defs"]["pluginReady"]["required"]
    assert "artifact_digest" not in valid_manifest_dict()


def test_validator_and_schema_agree_on_valid_manifest() -> None:
    """Сквозная проверка: validate_manifest + живая JSON Schema + типы."""
    manifest = validate_manifest(valid_manifest_dict())
    schema = _read_json(schema_path())
    assert manifest.plugin_id and PLUGIN_ID_PATTERN.fullmatch(manifest.plugin_id)
    assert SEMVER_PATTERN.fullmatch(manifest.version)
    assert SEMVER_PATTERN.fullmatch(manifest.min_platform_version)
    assert manifest.protocol == schema["properties"]["protocol"]["const"]
    assert manifest.requested_capabilities >= {"cache", "objects"}
    assert all(PATH_PATTERN.fullmatch(page) for page in manifest.public_pages)
    assert all(PATH_PATTERN.fullmatch(hook) for hook in manifest.webhooks)
    assert EGRESS_REF_PATTERN.fullmatch(manifest.egress_ref)
