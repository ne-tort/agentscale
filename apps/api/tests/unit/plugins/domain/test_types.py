"""Unit-тесты доменных типов плагинов (F0).

Проверяются frozen dataclass/StrEnum, инварианты, границы ArtifactDigest,
все значения RegistryOutcome и fail-closed поведение EgressPolicy.
Тесты НЕ запускаются (verification freeze).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from prodavan.domain.plugins import (
    ARTIFACT_DIGEST_PATTERN,
    CAPABILITY_ALLOWLIST,
    PLUGIN_ID_PATTERN,
    PROTOCOL_VERSION,
    SEMVER_PATTERN,
    SELF_REFERENCE_FIELDS,
    ArtifactDigest,
    BackendEntrypoint,
    Capability,
    CapabilityGrant,
    DecisionCode,
    EgressMode,
    EgressPolicy,
    FrontendEntrypoint,
    GatewayDecision,
    MigrationEngine,
    MigrationsDeclaration,
    PluginClaims,
    PluginInstance,
    PluginInstanceStatus,
    PluginManifest,
    QuotaRef,
    RegistryOutcome,
    RegistryResult,
)
from prodavan.domain.plugins.validation import validate_manifest

# Фикстуры — общий модуль каталога; импорт как по имени пакета, так и локальный.
VALID_DIGEST = "sha256:" + "a" * 64

try:
    from ._fixtures import valid_manifest_dict
except ImportError:  # запуск теста как отдельного модуля вне пакета
    from _fixtures import valid_manifest_dict  # type: ignore[no-redef]


def _valid_manifest() -> PluginManifest:
    """Собрать доменный манифест через публичный валидатор."""
    return validate_manifest(valid_manifest_dict())


# ───────────────────────── frozen / enum ─────────────────────────

def test_plugin_manifest_is_frozen_dataclass() -> None:
    manifest = _valid_manifest()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        manifest.plugin_id = "other"  # type: ignore[misc]


def test_plugin_id_pattern_accepts_strict_lowercase() -> None:
    assert PLUGIN_ID_PATTERN.fullmatch("mod_equipment")
    assert PLUGIN_ID_PATTERN.fullmatch("a")
    for bad in ("", "Mod_equipment", "1mod", "mod-equipment", "mod equipment", "mod.equipment"):
        assert PLUGIN_ID_PATTERN.fullmatch(bad) is None


def test_semver_pattern_accepts_and_rejects() -> None:
    for good in ("0.0.0", "1.2.0", "1.2.0-rc.1", "1.2.0+build.5", "10.20.30-alpha.1+sha.abc"):
        assert SEMVER_PATTERN.fullmatch(good)
    for bad in ("1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03", "v1.2.3", "1.2.3-", "1.2.3+"):
        assert SEMVER_PATTERN.fullmatch(bad) is None


def test_protocol_version_constant() -> None:
    assert PROTOCOL_VERSION == "plugin-rpc/1"
    assert _valid_manifest().protocol == PROTOCOL_VERSION


# ───────────────────────── capabilities ─────────────────────────

def test_capability_enum_is_exactly_the_allowlist() -> None:
    expected = {
        "cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network",
    }
    assert {item.value for item in Capability} == expected
    assert CAPABILITY_ALLOWLIST == frozenset(expected)
    # Прямой DSN к БД — не capability (§2 v4).
    assert "db.direct" not in CAPABILITY_ALLOWLIST
    assert "db.migrations" not in CAPABILITY_ALLOWLIST


def test_manifest_capabilities_are_typed_enum() -> None:
    manifest = _valid_manifest()
    assert all(isinstance(item, Capability) for item in manifest.capabilities)
    assert manifest.requested_capabilities == CAPABILITY_ALLOWLIST


def test_duplicate_capabilities_rejected_by_domain_type() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        PluginManifest(
            plugin_id="mod_equipment",
            version="1.2.0",
            protocol=PROTOCOL_VERSION,
            frontend=FrontendEntrypoint(entry="index.html", base_path="/pluginpage/x/"),
            backend=BackendEntrypoint(port=8080, health="/health"),
            capabilities=(Capability.CACHE, Capability.CACHE),
            migrations=MigrationsDeclaration(engine=MigrationEngine.LIQUIBASE, path="migrations/changelog.xml"),
            egress_ref="grant:co_123/x",
            public_pages=(),
            webhooks=(),
            min_platform_version="1.0.0",
        )


# ───────────────────────── ArtifactDigest ─────────────────────────

def test_artifact_digest_valid() -> None:
    digest = ArtifactDigest(VALID_DIGEST)
    assert digest.value == VALID_DIGEST
    assert str(digest) == VALID_DIGEST
    assert digest.hex == "a" * 64
    assert ARTIFACT_DIGEST_PATTERN.fullmatch(VALID_DIGEST)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "sha256:" + "a" * 63,          # слишком короткий
        "sha256:" + "a" * 65,          # слишком длинный
        "sha256:" + "A" * 64,          # верхний регистр запрещён
        "sha256:" + "g" * 64,          # не hex
        "md5:" + "a" * 64,             # неверный алгоритм
        "a" * 64,                      # нет префикса
        "sha256::" + "a" * 64,         # двойное двоеточие
        "sha256:" + "a" * 64 + "\n",   # trailing whitespace
    ],
)
def test_artifact_digest_boundaries_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match=r"sha256"):
        ArtifactDigest(bad)


def test_self_reference_fields_exclude_digest_and_signature() -> None:
    assert SELF_REFERENCE_FIELDS == frozenset({"artifact_digest", "signature"})
    assert "artifact_digest" not in valid_manifest_dict()
    assert "signature" not in valid_manifest_dict()


# ───────────────────────── EgressPolicy ─────────────────────────

def test_egress_mode_none_is_fail_closed() -> None:
    policy = EgressPolicy(mode=EgressMode.NONE)
    assert not policy.allows("webhooks.example.com", "/hook")
    with pytest.raises(ValueError):
        EgressPolicy(mode=EgressMode.NONE, allowed_hosts=("webhooks.example.com",))


def test_egress_mode_webhooks_only_requires_hosts_and_paths() -> None:
    with pytest.raises(ValueError):
        EgressPolicy(mode=EgressMode.WEBHOOKS_ONLY, allowed_hosts=("webhooks.example.com",))
    policy = EgressPolicy(
        mode=EgressMode.WEBHOOKS_ONLY,
        allowed_hosts=("webhooks.example.com",),
        allowed_paths=("/hook",),
    )
    assert policy.allows("webhooks.example.com", "/hook")
    assert not policy.allows("evil.example.com", "/hook")
    assert not policy.allows("webhooks.example.com", "/other")


def test_egress_mode_full_allows_but_is_explicit() -> None:
    assert EgressPolicy(mode=EgressMode.FULL).allows("any.example.com", "/any")
    assert {item.value for item in EgressMode} == {"none", "webhooks-only", "full"}


# ───────────────────────── Grant / instance / claims ─────────────────────────

def _grant(**changes: object) -> CapabilityGrant:
    payload: dict[str, object] = {
        "grant_id": "grant_abc",
        "plugin_id": "mod_equipment",
        "plugin_instance_id": "inst_abc",
        "capabilities": (Capability.CACHE, Capability.EVENTS),
        "egress_ref": "grant:co_123/mod_equipment/inst_abc",
        "egress_policy": EgressPolicy(
            mode=EgressMode.WEBHOOKS_ONLY,
            allowed_hosts=("webhooks.example.com",),
            allowed_paths=("/hook",),
        ),
        "quota_ref": QuotaRef("co_123/plan_pro"),
        "artifact_digest": ArtifactDigest(VALID_DIGEST),
    }
    payload.update(changes)
    return CapabilityGrant(**payload)  # type: ignore[arg-type]


def test_capability_grant_active_and_revocation() -> None:
    grant = _grant()
    assert grant.active
    assert grant.grants(Capability.CACHE)
    assert not grant.grants(Capability.SECRETS)
    revoked = _grant(revoked_at=datetime(2026, 11, 8, tzinfo=UTC))
    assert not revoked.active
    assert not revoked.grants(Capability.CACHE)


def test_capability_grant_rejects_bad_egress_ref() -> None:
    with pytest.raises(ValueError):
        _grant(egress_ref="https://webhooks.example.com")  # не ссылка на grant


def test_plugin_instance_status_all_values() -> None:
    assert {item.value for item in PluginInstanceStatus} == {
        "installed", "active", "suspended", "revoked", "quarantined",
    }


def test_plugin_instance_builds_and_validates() -> None:
    instance = PluginInstance(
        instance_id="inst_abc",
        plugin_id="mod_equipment",
        company_id="co_123",
        manifest_version="1.2.0",
        artifact_digest=ArtifactDigest(VALID_DIGEST),
        status=PluginInstanceStatus.ACTIVE,
    )
    assert instance.status is PluginInstanceStatus.ACTIVE
    with pytest.raises(ValueError):
        PluginInstance(
            instance_id="inst_abc",
            plugin_id="mod_equipment",
            company_id="co_123",
            manifest_version="not-semver",
            artifact_digest=ArtifactDigest(VALID_DIGEST),
            status=PluginInstanceStatus.INSTALLED,
        )


def test_plugin_claims_shape_has_no_crypto_and_no_egress() -> None:
    claims = PluginClaims(
        sub="plugin:mod_equipment:co_123:inst_abc",
        company_id="co_123",
        plugin_id="mod_equipment",
        plugin_instance_id="inst_abc",
        manifest_version="1.2.0",
        artifact_digest=VALID_DIGEST,
        grant_id="grant_abc",
        scopes=(Capability.CACHE,),
        quota_ref="co_123/plan_pro",
        jti="jti_1",
        exp=1_794_163_200,
    )
    payload = claims.to_claims_dict()
    assert payload["iss"] == "agentscale-plugins"
    assert payload["aud"] == "capability-gateway"
    assert payload["typ"] == "plugin_capability"
    # Egress-политика и квоты хранятся в grant в БД, а не в токене.
    assert "allowed_hosts" not in payload
    assert "allowed_paths" not in payload
    assert "quotas" not in payload
    assert "egress" not in payload
    assert "token" not in payload
    assert payload["scopes"] == ["cache"]


def test_plugin_claims_rejects_agent_issuer() -> None:
    with pytest.raises(ValueError):
        PluginClaims(
            iss="prodavan-pod-bridge",
            sub="pod:abc",
            company_id="co_123",
            plugin_id="mod_equipment",
            plugin_instance_id="inst_abc",
            manifest_version="1.2.0",
            artifact_digest=VALID_DIGEST,
            grant_id="grant_abc",
            jti="jti_1",
            exp=1_794_163_200,
        )


# ───────────────────────── RegistryOutcome / DecisionCode ─────────────────────────

def test_all_registry_outcomes_are_covered() -> None:
    """Все значения RegistryOutcome перечислены явно (acceptance #18)."""
    expected = {"applied", "conflict", "quarantined", "missing"}
    assert {item.value for item in RegistryOutcome} == expected
    outcomes = [
        RegistryOutcome.APPLIED,
        RegistryOutcome.CONFLICT,
        RegistryOutcome.QUARANTINED,
        RegistryOutcome.MISSING,
    ]
    for outcome in outcomes:
        result = RegistryResult(
            outcome=outcome,
            artifact_digest=ArtifactDigest(VALID_DIGEST),
            plugin_id="mod_equipment",
            version="1.2.0",
        )
        assert result.outcome is outcome
        assert result.published is (outcome is RegistryOutcome.APPLIED)


def test_decision_code_is_own_enum_and_fail_closed() -> None:
    assert DecisionCode.ALLOWED.value == "allowed"
    assert DecisionCode.ADAPTER_MISSING.value == "adapter_missing"
    assert DecisionCode.GRANT_MISSING.value == "grant_missing"
    assert len({item.value for item in DecisionCode}) == len(list(DecisionCode))
    with pytest.raises(ValueError):
        GatewayDecision(allowed=True, code=DecisionCode.GRANT_MISSING)
    with pytest.raises(ValueError):
        GatewayDecision(allowed=False, code=DecisionCode.ALLOWED)
    assert GatewayDecision(allowed=True, code=DecisionCode.ALLOWED).allowed
