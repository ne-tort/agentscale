"""Манифест пилота: контракт задачи 01, semver, protocol, capability allowlist,
запрет самореференции, запрет :latest, egress_ref, отсутствие версии в схеме.

Тесты написаны, но НЕ запускаются (verification freeze).
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

from .conftest import (
    APP_PATH,
    CAPABILITY_ALLOWLIST,
    DOCKERFILE_PATH,
    ENTRYPOINT_PATH,
    MANIFEST_PATH,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PROTOCOL,
    README_PATH,
    SCHEMA_PATH,
    manifest,
    read_text,
    schema,
)

# semver 2.0.0 (тот же паттерн, что в packages/schemas/plugin-manifest/v1/schema.json).
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

SELF_REFERENCE_FIELDS = frozenset({"artifact_digest", "signature"})
EMBEDDED_EGRESS_FIELDS = frozenset({"egress", "allowed_hosts", "allowed_paths"})


# --- базовый контракт ---------------------------------------------------------

def test_manifest_is_valid_json_and_has_exact_required_keys(manifest: dict[str, Any]) -> None:
    required = {
        "plugin_id", "version", "protocol", "frontend", "backend", "capabilities",
        "migrations", "egress_ref", "public_pages", "webhooks", "min_platform_version",
    }
    optional = {"rpc_operations", "mcp_servers"}
    assert required <= set(manifest)
    assert set(manifest) == required | optional, (
        "additionalProperties=false: обязательные поля и только явные optional declarations"
    )


def test_manifest_identity(manifest: dict[str, Any]) -> None:
    assert manifest["plugin_id"] == PLUGIN_ID
    assert manifest["version"] == PLUGIN_VERSION
    assert manifest["protocol"] == PROTOCOL
    assert manifest["min_platform_version"] == "4.0.0"
    assert re.fullmatch(r"[a-z][a-z0-9_]*", manifest["plugin_id"])


def test_manifest_frontend_backend(manifest: dict[str, Any]) -> None:
    assert manifest["frontend"] == {"entry": "index.html", "base_path": "/"}
    assert manifest["backend"] == {"port": 8080, "health": "/health"}
    assert manifest["frontend"]["entry"] == "index.html"
    assert manifest["backend"]["port"] == 8080


def test_manifest_migrations(manifest: dict[str, Any]) -> None:
    assert manifest["migrations"] == {"engine": "liquibase", "path": "migrations/changelog.xml"}
    assert not manifest["migrations"]["path"].startswith("/")
    assert ".." not in manifest["migrations"]["path"]


def test_manifest_egress_ref_is_grant_reference(manifest: dict[str, Any]) -> None:
    # §6.1: egress-политика живёт в CapabilityGrant в БД, а не в манифесте/JWT.
    assert manifest["egress_ref"] == "grant:hello_plugin"
    assert re.fullmatch(r"grant:[A-Za-z0-9_.:/-]+", manifest["egress_ref"])


def test_manifest_public_pages_and_webhooks(manifest: dict[str, Any]) -> None:
    assert manifest["public_pages"] == []
    assert manifest["webhooks"] == ["/webhook/ping"]
    for path in (*manifest["webhooks"], *manifest["public_pages"]):
        assert path.startswith("/")
        assert ".." not in path


# --- semver / protocol / allowlist --------------------------------------------

@pytest.mark.parametrize("field", ["version", "min_platform_version"])
def test_manifest_semver(manifest: dict[str, Any], field: str) -> None:
    assert SEMVER_RE.fullmatch(manifest[field]), f"{field} должен быть semver 2.0.0"


@pytest.mark.parametrize("bad", ["1.2", "v1.2.0", "01.2.3", "1.2.3.4", "", "latest"])
def test_semver_rejects_invalid(bad: str) -> None:
    assert SEMVER_RE.fullmatch(bad) is None


def test_protocol_is_exactly_plugin_rpc_1(manifest: dict[str, Any]) -> None:
    assert manifest["protocol"] == "plugin-rpc/1"
    assert manifest["protocol"].endswith("/1")


def test_capabilities_exactly_allowlist_no_db_direct(manifest: dict[str, Any]) -> None:
    caps = manifest["capabilities"]
    assert isinstance(caps, list)
    assert len(caps) == len(set(caps)), "uniqueItems: дубликаты запрещены"
    assert set(caps) == CAPABILITY_ALLOWLIST
    # §2/§4.4: db.direct и db.migrations НЕ capability — БД по прямому DSN.
    assert "db.direct" not in caps
    assert "db.migrations" not in caps
    assert not any(c.startswith("db.") for c in caps)


# --- запрет самореференции / встроенного egress -------------------------------

def test_manifest_has_no_self_reference(manifest: dict[str, Any]) -> None:
    """§5.1: artifact_digest/signature вычисляются платформой из OCI/cosign."""
    assert not (SELF_REFERENCE_FIELDS & set(manifest))
    raw = read_text(MANIFEST_PATH)
    assert "artifact_digest" not in raw
    assert "signature" not in raw


def test_manifest_has_no_embedded_egress_allowlist(manifest: dict[str, Any]) -> None:
    assert not (EMBEDDED_EGRESS_FIELDS & set(manifest))


def test_manifest_file_has_no_token_or_latest(manifest: dict[str, Any]) -> None:
    raw = read_text(MANIFEST_PATH)
    assert "?token=" not in raw
    assert ":latest" not in raw
    assert "TENANT_DB_DSN" not in raw, "DSN приходит из Secret при grant, не из манифеста"
    assert "password" not in raw.lower()


# --- проекция на JSON Schema задачи 01 ----------------------------------------

def test_manifest_validates_against_task01_schema(manifest: dict[str, Any], schema: dict[str, Any] | None) -> None:
    """Прямая jsonschema-валидация, если схема уже выложена задачей 01.

    Если параллельный субагент ещё не создал файл, проверяем контракт вручную
    (ниже) и НЕ считаем отсутствие схемы успехом «просто так»: тест явно
    помечает это через pytest.skip с пояснением, чтобы родитель видел долг.
    """
    if schema is None:
        pytest.skip(
            "JSON Schema задачи 01 ещё не создана "
            f"(ожидается {SCHEMA_PATH}); ручные проверки контракта выполнены"
        )
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema не установлен; ручные проверки контракта выполнены")

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
    assert not errors, "манифест не проходит schema.json задачи 01: " + "; ".join(
        f"{list(e.path)}: {e.message}" for e in errors
    )


def test_typed_rpc_operations_are_canonical_unique_and_match_backend(manifest: dict[str, Any]) -> None:
    from prodavan.application.plugins.bootstrap.service import _rpc_operations

    operations = _rpc_operations(manifest).operations
    assert len(operations) == 2
    assert {item.capability_id for item in operations} == {"hello.greetings"}
    assert {item.capability_version for item in operations} == {"1.0"}
    assert len({item.backend_path for item in operations}) == len(operations)
    assert {
        (item.operation, item.http_method, item.backend_path, item.public)
        for item in operations
    } == {
        ("list", "GET", "/api/greetings/list", False),
        ("greet", "POST", "/api/greetings", False),
    }
    backend = read_text(APP_PATH)
    assert '@app.get("/api/greetings/list")' in backend
    assert '@app.post("/api/greetings")' in backend


def test_manifest_contains_no_webhook_secret_or_secret_reference() -> None:
    raw = read_text(MANIFEST_PATH)
    for forbidden in ("webhook_secret", "webhook_secret_ref", "WEBHOOK_SECRET", "PLUGIN_WEBHOOK_SECRET", "file://", "vault://"):
        assert forbidden not in raw


def test_schema_forbids_self_reference_when_present(schema: dict[str, Any] | None) -> None:
    if schema is None:
        pytest.skip("JSON Schema задачи 01 ещё не создана")
    forbidden = schema.get("not", {}).get("anyOf", [])
    required_names = {entry["required"][0] for entry in forbidden if "required" in entry}
    assert SELF_REFERENCE_FIELDS <= required_names
    assert EMBEDDED_EGRESS_FIELDS <= required_names


def test_schema_capability_enum_matches_allowlist(schema: dict[str, Any] | None) -> None:
    if schema is None:
        pytest.skip("JSON Schema задачи 01 ещё не создана")
    enum = set(schema["properties"]["capabilities"]["items"]["enum"])
    assert enum == CAPABILITY_ALLOWLIST


# --- критерий 18: pin по digest, запрет :latest ------------------------------

def test_dockerfile_and_readme_forbid_latest_image() -> None:
    """Критерий 18: `:latest` как тег образа запрещён (§5.1)."""
    # Проверяем Dockerfile/entrypoint на отсутствие тега образа; упоминание
    # в комментарии-ЗАПРЕТЕ не считается нарушением.
    for path in (DOCKERFILE_PATH, ENTRYPOINT_PATH):
        raw = read_text(path)
        assert not re.search(r"(?:FROM|ARG|image:)\s+\S*:latest\b", raw), (
            f"{path.name}: образ с тегом :latest найден"
        )
    assert "`:latest` запрещён" in read_text(README_PATH)


def test_readme_documents_digest_pin_and_cosign() -> None:
    raw = read_text(README_PATH)
    assert "sha256:" in raw
    assert "cosign" in raw
    assert "IfNotPresent" in raw
    assert "задача 16" in raw
    assert "задача 05" in raw
    assert "задача 09" in raw
    assert "задача 07" in raw


# --- критерий 16: strangler / фича-флаг, без переноса данных ----------------

def test_readme_is_reference_not_mod_equipment_migration() -> None:
    raw = read_text(README_PATH)
    assert "эталон" in raw.lower()
    assert "strangler" in raw.lower()
    assert "plugin_enabled_modules[]" in raw
    assert "переноса" in raw or "бэкап" in raw
    assert "эфемерн" in raw.lower()
    # Явное указание, что mod_equipment мигрирует позже, а не сейчас.
    assert "mod_equipment" in raw
    assert "позже" in raw.lower()


def test_manifest_version_not_used_as_db_schema_name(manifest: dict[str, Any]) -> None:
    """§3.3: версия живёт в DATABASECHANGELOG и в пути статики, не в схеме."""
    from .conftest import CHANGELOG_PATH

    changelog = read_text(CHANGELOG_PATH)
    assert manifest["version"] not in changelog, "версия плагина не должна попадать в имена схем/таблиц"
    assert f"module_{manifest['version']}" not in changelog
    assert f"schema_{manifest['version']}" not in changelog
    assert f"v{manifest['version']}" not in changelog.replace("liquibase-4.24", "")


def test_manifest_json_is_stable_and_utf8() -> None:
    raw = read_text(MANIFEST_PATH)
    assert raw.strip().startswith("{")
    assert json.loads(raw) == json.loads(json.dumps(json.loads(raw), ensure_ascii=False))
