"""Closed pods/secret_delivery manifest declarations.

Tests are intentionally not executed during the migration verification freeze.
"""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import json
import re
from typing import Any

import pytest

from prodavan.domain.plugins.types import (
    POD_MAX_ALLOWLIST,
    POD_SCOPE_MAX_LENGTH,
    POD_SCOPE_PATTERN,
    SECRET_DELIVERY_MAX_ALLOWLIST,
    SECRET_DELIVERY_MODE,
    SECRET_REF_MAX_LENGTH,
    SECRET_REF_PATTERN,
    PodAllowlistEntry,
    PodsDeclaration,
    SecretDeliveryDeclaration,
)
from prodavan.domain.plugins.validation import (
    ManifestValidationErrorCode,
    PluginValidationError,
    validate_manifest,
)

try:
    from ._fixtures import schema_path, types_ts_path, valid_manifest_dict, with_patch
except ImportError:  # standalone module invocation
    from _fixtures import schema_path, types_ts_path, valid_manifest_dict, with_patch  # type: ignore[no-redef]


POD_ENTRY = {
    "plugin_instance_id": "pinst_01",
    "project_id": "proj_01",
    "job_id": "job_01",
}
VAULT_REF = "vault://ai_keys/plugins/capabilities/secrets/co_123/mod_x/pinst_01/api-key"
FILE_REF = "file://ai_keys/plugins/webhooks/co_123/mod_x/WEBHOOK_SECRET.secret"


def _pods(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"allowlist": copy.deepcopy([POD_ENTRY] if entries is None else entries)}


def _secret_delivery(refs: list[Any] | None = None) -> dict[str, Any]:
    return {
        "mode": "k8s_secret",
        "allowlist": [VAULT_REF] if refs is None else refs,
    }


def _expect_code(payload: Any, code: ManifestValidationErrorCode) -> None:
    with pytest.raises(PluginValidationError) as exc_info:
        validate_manifest(payload)
    assert exc_info.value.code is code, exc_info.value.message


def test_optional_declarations_may_be_omitted() -> None:
    manifest = validate_manifest(valid_manifest_dict())
    assert manifest.pods is None
    assert manifest.secret_delivery is None


def test_valid_pods_declaration_converts_to_frozen_typed_entries() -> None:
    second = {"plugin_instance_id": "pinst_01", "project_id": "proj_02", "job_id": "job-02"}
    manifest = validate_manifest(with_patch(pods=_pods([POD_ENTRY, second])))
    assert isinstance(manifest.pods, PodsDeclaration)
    assert manifest.pods.allowlist[0] == PodAllowlistEntry(**POD_ENTRY)
    assert manifest.pods.allowlist[1].job_id == "job-02"
    with pytest.raises((FrozenInstanceError, AttributeError)):
        manifest.pods.allowlist[0].project_id = "other"  # type: ignore[misc]


def test_valid_secret_delivery_converts_to_frozen_reference_tuple() -> None:
    manifest = validate_manifest(
        with_patch(secret_delivery=_secret_delivery([VAULT_REF, FILE_REF]))
    )
    assert isinstance(manifest.secret_delivery, SecretDeliveryDeclaration)
    assert manifest.secret_delivery.mode == "k8s_secret"
    assert manifest.secret_delivery.allowlist == (VAULT_REF, FILE_REF)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        manifest.secret_delivery.mode = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "declaration",
    [
        None,
        [],
        {},
        {"allowlist": []},
        {"allowlist": [POD_ENTRY], "override": True},
        {"allowlist": [{"project_id": "proj_01", "job_id": "job_01"}]},
        {"allowlist": [{**POD_ENTRY, "pod_id": " arbitrary-pod"}]},
        {"allowlist": [{**POD_ENTRY, "command": "ls"}]},
        {"allowlist": [{**POD_ENTRY, "args": ["--help"]}]},
        {"allowlist": [{**POD_ENTRY, "action": "read_status"}]},
        {"allowlist": [{**POD_ENTRY, "target_ref": "job_01"}]},
    ],
)
def test_pods_closed_shape_rejects_missing_unknown_and_executable_fields(
    declaration: Any,
) -> None:
    _expect_code(with_patch(pods=declaration), ManifestValidationErrorCode.PODS_INVALID)


@pytest.mark.parametrize("field_name", ["plugin_instance_id", "project_id", "job_id"])
@pytest.mark.parametrize("bad", ["*", "proj/*", "../proj", "proj/../job", "two words", "x?y", ""])
def test_pods_rejects_wildcards_traversal_and_noncanonical_scopes(
    field_name: str, bad: str
) -> None:
    entry = {**POD_ENTRY, field_name: bad}
    _expect_code(with_patch(pods=_pods([entry])), ManifestValidationErrorCode.PODS_INVALID)


def test_pods_rejects_duplicates_and_oversized_allowlist() -> None:
    _expect_code(
        with_patch(pods=_pods([POD_ENTRY, POD_ENTRY])),
        ManifestValidationErrorCode.PODS_INVALID,
    )
    oversized = [
        {**POD_ENTRY, "job_id": f"job_{index}"}
        for index in range(POD_MAX_ALLOWLIST + 1)
    ]
    _expect_code(with_patch(pods=_pods(oversized)), ManifestValidationErrorCode.PODS_INVALID)


@pytest.mark.parametrize(
    "declaration",
    [
        None,
        [],
        {},
        {"mode": "k8s_secret"},
        {"mode": "env", "allowlist": [VAULT_REF]},
        {"mode": "k8s_secret", "allowlist": []},
        {"mode": "k8s_secret", "allowlist": VAULT_REF},
        {"mode": "k8s_secret", "allowlist": [VAULT_REF], "secret_name": "delivery"},
        {"mode": "k8s_secret", "allowlist": [VAULT_REF], "data": {"key": "plaintext"}},
        {"mode": "k8s_secret", "allowlist": [VAULT_REF], "value": "plaintext"},
    ],
)
def test_secret_delivery_closed_shape_and_mode(declaration: Any) -> None:
    _expect_code(
        with_patch(secret_delivery=declaration),
        ManifestValidationErrorCode.SECRET_DELIVERY_INVALID,
    )


@pytest.mark.parametrize(
    "bad_ref",
    [
        "",
        "plaintext-secret-value",
        "https://example.invalid/secret",
        "secret://ai_keys/key",
        "k8s-secret://namespace/name/key",
        "vault://",
        "file://",
        "vault:///absolute",
        "vault://ai_keys//key",
        "vault://ai_keys/",
        "vault://../key",
        "vault://ai_keys/../key",
        "file://ai_keys/./key",
        "file://ai_keys/%2e%2e/key",
        "vault://ai_keys/*",
        "vault://ai_keys/key?version=1",
        "vault://ai_keys/key#fragment",
        "vault://ai_keys/key with space",
        "-----BEGIN PRIVATE KEY-----not-a-ref",
        {"value": "plaintext"},
    ],
)
def test_secret_delivery_rejects_invalid_schemes_traversal_query_wildcard_and_plaintext(
    bad_ref: Any,
) -> None:
    _expect_code(
        with_patch(secret_delivery=_secret_delivery([bad_ref])),
        ManifestValidationErrorCode.SECRET_DELIVERY_INVALID,
    )


def test_secret_delivery_rejects_duplicates_oversized_list_and_ref() -> None:
    _expect_code(
        with_patch(secret_delivery=_secret_delivery([VAULT_REF, VAULT_REF])),
        ManifestValidationErrorCode.SECRET_DELIVERY_INVALID,
    )
    oversized = [f"vault://ai_keys/key_{index}" for index in range(SECRET_DELIVERY_MAX_ALLOWLIST + 1)]
    _expect_code(
        with_patch(secret_delivery=_secret_delivery(oversized)),
        ManifestValidationErrorCode.SECRET_DELIVERY_INVALID,
    )
    too_long = "vault://" + "a" * SECRET_REF_MAX_LENGTH
    _expect_code(
        with_patch(secret_delivery=_secret_delivery([too_long])),
        ManifestValidationErrorCode.SECRET_DELIVERY_INVALID,
    )


def test_schema_declaration_shapes_match_python_contracts() -> None:
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    assert "pods" not in schema["required"]
    assert "secret_delivery" not in schema["required"]

    pods = schema["properties"]["pods"]
    assert pods["additionalProperties"] is False
    assert set(pods["required"]) == {"allowlist"}
    assert pods["properties"]["allowlist"]["minItems"] == 1
    assert pods["properties"]["allowlist"]["maxItems"] == POD_MAX_ALLOWLIST
    assert pods["properties"]["allowlist"]["uniqueItems"] is True
    pod_entry = schema["$defs"]["podAllowlistEntry"]
    assert pod_entry["additionalProperties"] is False
    assert set(pod_entry["required"]) == {"plugin_instance_id", "project_id", "job_id"}
    assert set(pod_entry["properties"]) == {"plugin_instance_id", "project_id", "job_id"}
    assert schema["$defs"]["podScope"]["pattern"] == POD_SCOPE_PATTERN.pattern
    assert schema["$defs"]["podScope"]["maxLength"] == POD_SCOPE_MAX_LENGTH

    delivery = schema["properties"]["secret_delivery"]
    assert delivery["additionalProperties"] is False
    assert set(delivery["required"]) == {"mode", "allowlist"}
    assert delivery["properties"]["mode"]["const"] == SECRET_DELIVERY_MODE
    assert delivery["properties"]["allowlist"]["minItems"] == 1
    assert delivery["properties"]["allowlist"]["maxItems"] == SECRET_DELIVERY_MAX_ALLOWLIST
    assert delivery["properties"]["allowlist"]["uniqueItems"] is True
    assert schema["$defs"]["secretRef"]["pattern"] == SECRET_REF_PATTERN.pattern
    assert schema["$defs"]["secretRef"]["maxLength"] == SECRET_REF_MAX_LENGTH


def test_jsonschema_and_python_representative_parity() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    valid = with_patch(
        pods=_pods([POD_ENTRY]),
        secret_delivery=_secret_delivery([VAULT_REF, FILE_REF]),
    )
    assert list(validator.iter_errors(valid)) == []
    assert validate_manifest(valid).pods is not None

    invalid_payloads = [
        with_patch(pods=_pods([POD_ENTRY, POD_ENTRY])),
        with_patch(pods=_pods([{**POD_ENTRY, "project_id": "*"}])),
        with_patch(pods=_pods([{**POD_ENTRY, "command": "ls"}])),
        with_patch(secret_delivery=_secret_delivery([VAULT_REF, VAULT_REF])),
        with_patch(secret_delivery=_secret_delivery(["vault://ai_keys/../key"])),
        with_patch(secret_delivery={**_secret_delivery(), "data": "plaintext"}),
    ]
    for payload in invalid_payloads:
        assert list(validator.iter_errors(payload))
        with pytest.raises(PluginValidationError):
            validate_manifest(payload)


def test_typescript_mirrors_closed_declaration_shapes() -> None:
    source = types_ts_path().read_text(encoding="utf-8")
    expected = {
        "PodAllowlistEntry": {"plugin_instance_id", "project_id", "job_id"},
        "PodsDeclaration": {"allowlist"},
        "SecretDeliveryDeclaration": {"mode", "allowlist"},
    }
    for interface_name, fields in expected.items():
        match = re.search(
            rf"export interface {interface_name}\s*\{{([\s\S]*?)\n\}}",
            source,
        )
        assert match, interface_name
        body = match.group(1)
        assert set(re.findall(
            r"^\s*readonly\s+([a-z_]+)\??:", body, flags=re.MULTILINE,
        )) == fields
        for forbidden in ("pod_id", "command", "args", "secret_name", "data", "value"):
            assert forbidden not in body
    assert "pods?: PodsDeclaration;" in source
    assert "secret_delivery?: SecretDeliveryDeclaration;" in source
    assert 'type SecretDeliveryMode = "k8s_secret";' in source
