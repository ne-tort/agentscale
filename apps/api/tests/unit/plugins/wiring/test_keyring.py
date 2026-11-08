"""F11: сборка ``KeyRing`` — активный ключ, ротация, JWKS, запрет dev-фолбэков.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from jwt import get_unverified_header

from prodavan.application.plugins.identity.keys import KeyEntry, kid_from_secret
from prodavan.application.plugins.identity.mint import (
    build_payload,
    mint_plugin_claims,
    verify_plugin_token,
)
from prodavan.application.plugins.wiring.config import (
    PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV,
    PluginPlatformConfig,
    PluginPlatformConfigError,
)
from prodavan.application.plugins.wiring.keyring import (
    ALGORITHM,
    active_kid_for,
    build_keyring,
    jwks_for,
    verification_kids,
)
from tests.unit.plugins.wiring.conftest import (
    TEST_DIGEST,
    TEST_OLD_SECRET_1,
    TEST_OLD_SECRET_2,
    TEST_SECRET,
    FakeSettings,
)


@dataclass(frozen=True)
class FakeGrant:
    grant_id: str = "grant_01"
    plugin_id: str = "mod_equipment"
    company_id: str = "co_123"
    scopes: tuple[str, ...] = ("cache", "objects")
    manifest_version: str = "1.2.0"
    artifact_digest: str = TEST_DIGEST
    quota_ref: str = "co_123/plan_pro"


@dataclass(frozen=True)
class FakeInstance:
    plugin_instance_id: str = "inst_abc"
    cabinet_id: str | None = None
    project_id: str | None = None


def _rotation_config(monkeypatch: pytest.MonkeyPatch) -> PluginPlatformConfig:
    monkeypatch.setenv(
        PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV,
        f"{TEST_OLD_SECRET_1},{TEST_OLD_SECRET_2}",
    )
    return PluginPlatformConfig.from_settings(FakeSettings())


def test_build_keyring_active_kid_matches_mint(enabled_config: PluginPlatformConfig) -> None:
    ring = build_keyring(enabled_config)
    assert ring.active_kid == active_kid_for(enabled_config)
    assert ring.active_kid == kid_from_secret(TEST_SECRET, ALGORITHM)
    assert ring.active.algorithm == ALGORITHM

    # Токен, выпущенный mint_plugin_claims(secret=...), верифицируется этим ring.
    token = mint_plugin_claims(grant=FakeGrant(), instance=FakeInstance(), secret=TEST_SECRET)
    assert get_unverified_header(token)["kid"] == ring.active_kid
    claims = verify_plugin_token(token, keyring=ring)
    assert claims.grant_id == "grant_01"


def test_build_keyring_registers_rotation_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _rotation_config(monkeypatch)
    ring = build_keyring(config)
    kids = verification_kids(config)

    assert kids == (
        kid_from_secret(TEST_OLD_SECRET_1, ALGORITHM),
        kid_from_secret(TEST_OLD_SECRET_2, ALGORITHM),
        kid_from_secret(TEST_SECRET, ALGORITHM),
    )
    # Активным обязан остаться НОВЫЙ секрет (последний в списке).
    assert ring.active_kid == kids[-1]
    assert ring.active_kid == active_kid_for(config)
    for kid in kids:
        assert ring.get(kid).algorithm == ALGORITHM


def test_old_kid_still_verifies_in_flight_token_after_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Критерий: старый kid верифицирует in-flight токен после ротации."""
    # 1) Токен выпущен СТАРЫМ секретом (до ротации).
    old_token = mint_plugin_claims(
        grant=FakeGrant(), instance=FakeInstance(), secret=TEST_OLD_SECRET_1
    )
    assert get_unverified_header(old_token)["kid"] == kid_from_secret(
        TEST_OLD_SECRET_1, ALGORITHM
    )

    # 2) Платформа собрана ПОСЛЕ ротации: активен новый секрет, старые — в ring.
    config = _rotation_config(monkeypatch)
    ring = build_keyring(config)
    assert ring.active_kid == active_kid_for(config)

    # 3) In-flight токен со старым kid всё ещё проверяется (подпись + claims).
    claims = verify_plugin_token(old_token, keyring=ring)
    assert claims.plugin_id == "mod_equipment"
    assert claims.artifact_digest == TEST_DIGEST

    # 4) Новые токены подписываются ТОЛЬКО активным (новым) ключом.
    fresh_payload = build_payload(
        grant=FakeGrant(), instance=FakeInstance(), now=1_700_000_000, ttl_seconds=300
    )
    fresh = ring.sign(fresh_payload)
    assert get_unverified_header(fresh)["kid"] == ring.active_kid


def test_rotation_kid_that_was_retired_would_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """После ``retire`` старого ключа in-flight токен закономерно отклоняется."""
    config = _rotation_config(monkeypatch)
    ring = build_keyring(config)
    old_token = mint_plugin_claims(
        grant=FakeGrant(), instance=FakeInstance(), secret=TEST_OLD_SECRET_1
    )
    ring.retire(kid_from_secret(TEST_OLD_SECRET_1, ALGORITHM))
    with pytest.raises(Exception) as ei:
        verify_plugin_token(old_token, keyring=ring)
    assert "UNKNOWN_KID" in str(ei.value) or getattr(ei.value, "code", "") == "UNKNOWN_KID"


def test_no_dev_fallback_on_empty_secret() -> None:
    config = PluginPlatformConfig(
        enabled=True,
        claims_secret="",
        static_origin="https://plugins.example.test",
    )
    # Пустой секрет ловит ДУБЛЬ guard'а в validate(), до обращения к KeyRing:
    # это и есть «ошибка сборки», а не «возьмём AUTH_TEST_SECRET».
    with pytest.raises(PluginPlatformConfigError) as ei:
        build_keyring(config)
    assert "AUTH_TEST_SECRET" in str(ei.value) or "PLUGIN_CLAIMS_SECRET" in str(ei.value)


def test_disabled_platform_refuses_keyring(disabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(PluginPlatformConfigError, match="disabled"):
        build_keyring(disabled_config)


def test_duplicate_rotation_secret_rejected() -> None:
    config = PluginPlatformConfig(
        enabled=True,
        claims_secret=TEST_SECRET,
        static_origin="https://plugins.example.test",
        previous_claims_secrets=(TEST_OLD_SECRET_1, TEST_OLD_SECRET_1),
    )
    with pytest.raises(PluginPlatformConfigError, match="duplicate"):
        build_keyring(config)


def test_jwks_never_exposes_hs256_secret(enabled_config: PluginPlatformConfig) -> None:
    ring = build_keyring(enabled_config)
    jwks = jwks_for(ring)
    # HS256-ключи НЕ публикуются (симметричный секрет нельзя отдавать наружу).
    assert jwks == {"keys": []}
    assert TEST_SECRET not in repr(jwks)
    assert TEST_SECRET not in repr(ring.jwks())


def test_keyring_report_has_no_secrets(enabled_config: PluginPlatformConfig) -> None:
    from prodavan.application.plugins.wiring.keyring import keyring_report

    report = keyring_report(enabled_config, build_keyring(enabled_config))
    assert TEST_SECRET not in repr(report)
    assert report["algorithm"] == ALGORITHM
    assert report["active_kid"] == active_kid_for(enabled_config)
    assert report["rotation_keys"] == 0


def test_key_entry_requires_explicit_secret() -> None:
    """На уровне KeyEntry тоже нет dev-фолбэка (задача 02)."""
    with pytest.raises(Exception):
        KeyEntry(kid="hs256-empty", algorithm="HS256", secret="")
