"""Unit-тесты валидатора манифеста плагина (F0).

Валидный манифест, невалидный semver, неизвестная capability, самореференция
digest, пустой plugin_id, egress_ref вместо встроенного allowlist, границы
ArtifactDigest, все значения RegistryOutcome. Тесты НЕ запускаются.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from prodavan.domain.plugins import ArtifactDigest, Capability, PluginManifest, validate_manifest
from prodavan.domain.plugins.validation import (
    MANIFEST_ALLOWED_FIELDS,
    MANIFEST_REQUIRED_FIELDS,
    ManifestValidationErrorCode,
    PluginValidationError,
)
try:
    from tests.unit.plugins.domain._fixtures import (
        VALID_DIGEST,
        valid_manifest_dict,
        with_patch,
        without_field,
    )
except ImportError:  # запуск pytest из каталога domain без настроенного PYTHONPATH
    from _fixtures import (  # type: ignore[no-redef]
        VALID_DIGEST,
        valid_manifest_dict,
        with_patch,
        without_field,
    )


def _expect_code(data: Any, code: ManifestValidationErrorCode) -> PluginValidationError:
    """Утвердить, что валидатор отказал с конкретным типизированным кодом."""
    with pytest.raises(PluginValidationError) as exc_info:
        validate_manifest(data)
    assert isinstance(exc_info.value, PluginValidationError)
    assert exc_info.value.code is code, (
        f"expected {code.value}, got {exc_info.value.code.value}: {exc_info.value.message}"
    )
    assert exc_info.value.code.value in str(exc_info.value)
    return exc_info.value


# ───────────────────────── валидный манифест ─────────────────────────

def test_valid_manifest_is_accepted() -> None:
    manifest = validate_manifest(valid_manifest_dict())
    assert isinstance(manifest, PluginManifest)
    assert manifest.plugin_id == "mod_equipment"
    assert manifest.version == "1.2.0"
    assert manifest.protocol == "plugin-rpc/1"
    assert manifest.frontend.entry == "index.html"
    assert manifest.backend.port == 8080
    assert manifest.migrations.engine.value == "liquibase"
    assert manifest.capabilities == tuple(Capability)
    assert manifest.public_pages == ()
    assert manifest.webhooks == ("/webhook/external-system",)
    assert manifest.min_platform_version == "1.0.0"


def test_valid_manifest_accepts_semver_prerelease_and_build() -> None:
    manifest = validate_manifest(with_patch(version="2.0.0-rc.1+build.7"))
    assert manifest.version == "2.0.0-rc.1+build.7"


def test_valid_manifest_accepts_all_migration_engines() -> None:
    for engine in ("liquibase", "flyway", "alembic"):
        payload = valid_manifest_dict()
        payload["migrations"] = {"engine": engine, "path": "migrations/changelog.xml"}
        assert validate_manifest(payload).migrations.engine.value == engine


def test_valid_manifest_accepts_empty_capabilities() -> None:
    assert validate_manifest(with_patch(capabilities=[])).capabilities == ()


# ───────────────────────── semver ─────────────────────────

@pytest.mark.parametrize(
    "bad_version",
    ["1.2", "1.2.3.4", "01.2.3", "v1.2.3", "1.2.3-", "1.2.3+", "", 123, None],
)
def test_invalid_semver_rejected(bad_version: Any) -> None:
    _expect_code(with_patch(version=bad_version), ManifestValidationErrorCode.VERSION_INVALID)


def test_invalid_min_platform_version_rejected() -> None:
    _expect_code(
        with_patch(min_platform_version="latest"),
        ManifestValidationErrorCode.MIN_PLATFORM_VERSION_INVALID,
    )


# ───────────────────────── capability allowlist ─────────────────────────

def test_unknown_capability_rejected() -> None:
    _expect_code(
        with_patch(capabilities=["cache", "telepathy"]),
        ManifestValidationErrorCode.CAPABILITY_UNKNOWN,
    )


def test_db_direct_capability_rejected() -> None:
    """БД плагина доступна по прямому DSN, а не через gateway (§2 v4)."""
    _expect_code(
        with_patch(capabilities=["cache", "db.direct"]),
        ManifestValidationErrorCode.CAPABILITY_UNKNOWN,
    )


def test_duplicate_capabilities_rejected() -> None:
    _expect_code(
        with_patch(capabilities=["cache", "cache"]),
        ManifestValidationErrorCode.CAPABILITY_DUPLICATE,
    )


def test_capabilities_must_be_array() -> None:
    _expect_code(with_patch(capabilities="cache"), ManifestValidationErrorCode.CAPABILITY_UNKNOWN)


# ───────────────────────── самореференция digest / signature ─────────────────────────

@pytest.mark.parametrize("field_name", ["artifact_digest", "signature"])
def test_self_reference_digest_or_signature_forbidden(field_name: str) -> None:
    payload = valid_manifest_dict()
    payload[field_name] = VALID_DIGEST
    _expect_code(payload, ManifestValidationErrorCode.SELF_REFERENCE_FORBIDDEN)


def test_digest_in_nested_object_is_not_smuggled() -> None:
    """Неизвестное поле в закрытом объекте frontend = отказ, а не успех."""
    payload = copy.deepcopy(valid_manifest_dict())
    payload["frontend"]["artifact_digest"] = VALID_DIGEST
    _expect_code(payload, ManifestValidationErrorCode.FRONTEND_INVALID)


# ───────────────────────── plugin_id ─────────────────────────

@pytest.mark.parametrize("bad_id", ["", "   ", None, 42])
def test_empty_plugin_id_rejected(bad_id: Any) -> None:
    _expect_code(with_patch(plugin_id=bad_id), ManifestValidationErrorCode.PLUGIN_ID_EMPTY)


@pytest.mark.parametrize("bad_id", ["Mod_equipment", "1mod", "mod-equipment", "mod.equipment", "mod equipment"])
def test_invalid_plugin_id_pattern_rejected(bad_id: str) -> None:
    _expect_code(with_patch(plugin_id=bad_id), ManifestValidationErrorCode.PLUGIN_ID_INVALID)


def test_plugin_id_normalization_is_strict() -> None:
    """Нормализация strip/lower не должна молча «исправлять» идентификатор."""
    _expect_code(with_patch(plugin_id=" mod_equipment "), ManifestValidationErrorCode.PLUGIN_ID_INVALID)
    _expect_code(with_patch(plugin_id="MOD_EQUIPMENT"), ManifestValidationErrorCode.PLUGIN_ID_INVALID)
    assert validate_manifest(with_patch(plugin_id="a")).plugin_id == "a"


# ───────────────────────── egress_ref вместо allowlist ─────────────────────────

def test_egress_ref_present_in_valid_manifest() -> None:
    manifest = validate_manifest(valid_manifest_dict())
    assert manifest.egress_ref.startswith("grant:")
    assert "egress" not in valid_manifest_dict()
    assert "allowed_hosts" not in valid_manifest_dict()
    assert "allowed_paths" not in valid_manifest_dict()


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("egress", {"allowed_hosts": ["webhooks.example.com"]}),
        ("egress_policy", {"mode": "full"}),
        ("allowed_hosts", ["webhooks.example.com"]),
        ("allowed_paths", ["/hook"]),
    ],
)
def test_embedded_egress_allowlist_forbidden(field_name: str, value: Any) -> None:
    payload = valid_manifest_dict()
    payload[field_name] = value
    _expect_code(payload, ManifestValidationErrorCode.EMBEDDED_EGRESS_FORBIDDEN)


@pytest.mark.parametrize(
    "bad_ref",
    ["", "webhooks.example.com", "https://grant", "egress:co_123", "grant:", None, 42],
)
def test_invalid_egress_ref_rejected(bad_ref: Any) -> None:
    _expect_code(with_patch(egress_ref=bad_ref), ManifestValidationErrorCode.EGRESS_REF_INVALID)


# ───────────────────────── закрытая структура ─────────────────────────

def test_missing_required_field_rejected() -> None:
    for field_name in MANIFEST_REQUIRED_FIELDS:
        _expect_code(without_field(field_name), ManifestValidationErrorCode.MISSING_FIELD)


def test_unknown_top_level_field_rejected() -> None:
    payload = valid_manifest_dict()
    payload["artifact_url"] = "ghcr.io/ne-tort/plugins/mod_equipment:1.2.0"
    _expect_code(payload, ManifestValidationErrorCode.UNKNOWN_FIELD)


def test_not_an_object_rejected() -> None:
    for bad in ([], "manifest", None, 42):
        _expect_code(bad, ManifestValidationErrorCode.NOT_AN_OBJECT)


def test_allowed_fields_are_exactly_the_schema_properties() -> None:
    assert MANIFEST_ALLOWED_FIELDS == frozenset(MANIFEST_REQUIRED_FIELDS)


def test_protocol_must_be_plugin_rpc_1() -> None:
    _expect_code(with_patch(protocol="1.0"), ManifestValidationErrorCode.PROTOCOL_INVALID)
    _expect_code(with_patch(protocol="plugin-rpc/2"), ManifestValidationErrorCode.PROTOCOL_INVALID)


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"frontend": {"entry": "../index.html", "base_path": "/p/"}}, ManifestValidationErrorCode.FRONTEND_INVALID),
        ({"frontend": {"entry": "index.html", "base_path": "relative"}}, ManifestValidationErrorCode.FRONTEND_INVALID),
        ({"frontend": {"entry": "index.html"}}, ManifestValidationErrorCode.FRONTEND_INVALID),
        ({"backend": {"port": 0, "health": "/health"}}, ManifestValidationErrorCode.BACKEND_INVALID),
        ({"backend": {"port": 70000, "health": "/health"}}, ManifestValidationErrorCode.BACKEND_INVALID),
        ({"backend": {"port": "8080", "health": "/health"}}, ManifestValidationErrorCode.BACKEND_INVALID),
        ({"migrations": {"engine": "django", "path": "m/x.xml"}}, ManifestValidationErrorCode.MIGRATIONS_INVALID),
        ({"migrations": {"engine": "liquibase", "path": "../changelog.xml"}}, ManifestValidationErrorCode.MIGRATIONS_INVALID),
        ({"public_pages": ["/ok", "../escape"]}, ManifestValidationErrorCode.PUBLIC_PAGES_INVALID),
        ({"public_pages": ["/dup", "/dup"]}, ManifestValidationErrorCode.PUBLIC_PAGES_INVALID),
        ({"webhooks": ["relative/path"]}, ManifestValidationErrorCode.WEBHOOKS_INVALID),
        ({"webhooks": "one"}, ManifestValidationErrorCode.WEBHOOKS_INVALID),
    ],
)
def test_nested_structure_validation(patch: dict[str, Any], code: ManifestValidationErrorCode) -> None:
    _expect_code(with_patch(**patch), code)


# ───────────────────────── ArtifactDigest ─────────────────────────

def test_artifact_digest_boundaries() -> None:
    assert ArtifactDigest(VALID_DIGEST).hex == "a" * 64
    for bad in ("sha256:" + "a" * 63, "sha256:" + "A" * 64, "md5:" + "a" * 64, "a" * 64):
        with pytest.raises(ValueError):
            ArtifactDigest(bad)


# ───────────────────────── RegistryOutcome ─────────────────────────

def test_all_registry_outcome_values_exist() -> None:
    from prodavan.domain.plugins import RegistryOutcome

    assert {item.value for item in RegistryOutcome} == {
        "applied", "conflict", "quarantined", "missing",
    }
