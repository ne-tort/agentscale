"""F1: mint → verify, подпись, iss/aud/typ, exp, kid, claim, artifact_digest.

Тесты **написаны, но не запускаются** (verification freeze плана миграции §0.2).
"""

from __future__ import annotations

from typing import Any

import jwt
import pytest

from prodavan.application.plugins.identity import (
    DEFAULT_PLUGIN_TOKEN_TTL_SECONDS,
    FORBIDDEN_CLAIMS,
    PLUGIN_AUDIENCE,
    PLUGIN_ISSUER,
    PLUGIN_TOKEN_TYP,
    KeyRing,
    PluginClaims,
    PluginTokenError,
    PluginTokenErrorCode,
    build_payload,
    mint_plugin_claims,
    peek_plugin_token,
    verify_plugin_token,
)
from tests.unit.plugins.identity.conftest import TEST_DIGEST, TEST_SECRET, FakeGrant, FakeInstance

pytestmark = pytest.mark.usefixtures("now")


# --- helpers -----------------------------------------------------------------

def _tamper_payload(token: str, *, changes: dict[str, Any], secret: str = TEST_SECRET) -> str:
    """Переподписать токен тестовым секретом с изменёнными claim.

    Нужно для проверки *семантических* отказов (aud/typ/claim/digest): подпись
    остаётся валидной, чтобы ошибка была именно по коду, а не «битый токен».
    """
    payload = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        options={"verify_aud": False, "verify_exp": False},
    )
    header = jwt.get_unverified_header(token)
    payload.update(changes)
    return jwt.encode(payload, secret, algorithm="HS256", headers={"kid": header["kid"]})


# --- round-trip --------------------------------------------------------------

def test_mint_verify_round_trip(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    assert isinstance(token, str)
    assert token.count(".") == 2

    claims = verify_plugin_token(token, keyring=keyring, now=now)
    assert isinstance(claims, PluginClaims)
    # Выпуск и проверка разделены во времени, потому ``iat`` может отличаться
    # на 1-2 секунды от фикстуры; важна именно дельта TTL.
    assert claims.exp - claims.iat == DEFAULT_PLUGIN_TOKEN_TTL_SECONDS
    assert claims.iss == PLUGIN_ISSUER
    assert claims.aud == PLUGIN_AUDIENCE
    assert claims.typ == PLUGIN_TOKEN_TYP
    assert claims.sub == "plugin:mod_equipment:co_123:inst_abc"
    assert claims.company_id == "co_123"
    assert claims.cabinet_id == "cab_456"
    assert claims.project_id is None
    assert claims.plugin_id == "mod_equipment"
    assert claims.plugin_instance_id == "inst_abc"
    assert claims.manifest_version == "1.2.0"
    assert claims.artifact_digest == TEST_DIGEST
    assert claims.grant_id == "grant_01"
    assert claims.scopes == ("cache", "objects", "events")
    assert claims.quota_ref == "co_123/plan_pro"
    assert claims.jti


def test_default_ttl_is_300_seconds() -> None:
    assert DEFAULT_PLUGIN_TOKEN_TTL_SECONDS == 300


def test_token_ttl_defaults_to_300_seconds(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    claims = verify_plugin_token(token, keyring=keyring, now=now)
    assert claims.exp - claims.iat == 300


def test_custom_ttl_is_honoured(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret, ttl_seconds=42)
    claims = verify_plugin_token(token, keyring=keyring, now=now)
    assert claims.exp - claims.iat == 42


def test_token_payload_never_carries_egress_or_quotas(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
) -> None:
    # Даже если в grant попадают долгоживущие политики, в токен они не идут
    # (критерий №22: egress/quotas живут в CapabilityGrant в БД).
    payload = build_payload(grant=grant, instance=instance, now=1000, ttl_seconds=300)
    for name in FORBIDDEN_CLAIMS:
        assert name not in payload

    hostile = dict(grant.__dict__)
    hostile["egress"] = {"allowed_hosts": ["evil.example.com"]}
    hostile["quotas"] = {"ops_per_minute": 10**9}
    payload2 = build_payload(grant=hostile, instance=instance, now=1000, ttl_seconds=300)
    for name in FORBIDDEN_CLAIMS:
        assert name not in payload2


def test_peek_returns_payload_without_verification(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    peeked = peek_plugin_token(token)
    assert peeked is not None
    assert peeked["typ"] == PLUGIN_TOKEN_TYP
    assert peek_plugin_token("garbage") is None


# --- Подпись -----------------------------------------------------------------

def test_forged_signature_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    forged = jwt.encode(
        jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"verify_aud": False, "verify_exp": False},
        ),
        "another-secret",
        algorithm="HS256",
        headers={"kid": jwt.get_unverified_header(token)["kid"]},
    )
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(forged, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.INVALID_SIGNATURE


def test_tampered_payload_without_valid_signature_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    # Битовая правка последнего сегмента (подпись не пересчитывалась).
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    head, body, sig = token.split(".")
    tampered = f"{head}.{body}.{sig[:-2]}xy"
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(tampered, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.INVALID_SIGNATURE


def test_empty_secret_is_rejected_no_dev_fallback(
    grant: FakeGrant,
    instance: FakeInstance,
) -> None:
    # В отличие от bridge.py:111-128, пустой секрет ≠ «возьмём auth_test_secret».
    with pytest.raises(PluginTokenError) as ei:
        mint_plugin_claims(grant=grant, instance=instance, secret="")
    assert ei.value.code == PluginTokenErrorCode.INVALID_SIGNATURE


def test_algorithm_confusion_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    # Токен с kid валидного HS256-ключа, но alg=none / HS512 → отказ.
    payload = build_payload(grant=grant, instance=instance, now=now, ttl_seconds=300)
    header = {"kid": keyring.active_kid}
    none_token = jwt.encode(payload, key="", algorithm="none", headers=header)
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(none_token, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.INVALID_SIGNATURE


# --- iss / aud / typ ---------------------------------------------------------

def test_wrong_audience_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    bad = _tamper_payload(token, changes={"aud": "prodavan-pod-bridge"})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(bad, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.WRONG_AUDIENCE


def test_wrong_typ_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    # Попытка выдать plugin-токен за агентский pod_bridge.
    bad = _tamper_payload(token, changes={"typ": "pod_bridge"})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(bad, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.WRONG_TYP


def test_wrong_issuer_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    bad = _tamper_payload(token, changes={"iss": "someone-else"})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(bad, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.WRONG_ISSUER


# --- exp ---------------------------------------------------------------------

def test_expired_token_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    # now сильно за exp + leeway.
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(token, keyring=keyring, now=now + 10_000)
    assert ei.value.code == PluginTokenErrorCode.EXPIRED


def test_small_leeway_does_not_resurrect_expired_token(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
) -> None:
    # ``now`` берём из самого payload, чтобы тест не зависел от секунды запуска.
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    payload = peek_plugin_token(token)
    assert payload is not None
    issued_at = int(payload["iat"])
    expires_at = int(payload["exp"])

    # Внутри leeway (exp + 3) — ещё валиден, ровно на границе (exp + 5) — уже нет.
    claims = verify_plugin_token(token, keyring=keyring, now=expires_at + 3)
    assert claims.iat == issued_at
    assert claims.exp == expires_at
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(token, keyring=keyring, now=expires_at + 5)
    assert ei.value.code == PluginTokenErrorCode.EXPIRED


def test_verify_accepts_aware_datetime_now(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
) -> None:
    from datetime import UTC, datetime

    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    claims = verify_plugin_token(token, keyring=keyring, now=datetime.now(UTC))
    assert claims.typ == PLUGIN_TOKEN_TYP


# --- kid ---------------------------------------------------------------------

def test_unknown_kid_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    payload = build_payload(grant=grant, instance=instance, now=now, ttl_seconds=300)
    token = jwt.encode(payload, secret, algorithm="HS256", headers={"kid": "kid-that-does-not-exist"})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(token, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.UNKNOWN_KID


def test_missing_kid_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    payload = build_payload(grant=grant, instance=instance, now=now, ttl_seconds=300)
    token = jwt.encode(payload, secret, algorithm="HS256")  # без kid
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(token, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.UNKNOWN_KID


def test_rotation_keeps_old_kid_verifiable(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    now: int,
) -> None:
    old_entry = hs256_entry(secret, kid="kid-old")
    ring = KeyRing([old_entry], active_kid="kid-old")
    old_token = ring.sign(build_payload(grant=grant, instance=instance, now=now, ttl_seconds=300))

    new_entry = hs256_entry("rotated-secret", kid="kid-new")
    ring.rotate(new_entry)
    assert ring.active_kid == "kid-new"

    # In-flight токен, подписанный старым kid, ещё проверяется.
    claims = verify_plugin_token(old_token, keyring=ring, now=now)
    assert claims.grant_id == "grant_01"

    # Новый токен подписывается новым kid.
    new_token = ring.sign(build_payload(grant=grant, instance=instance, now=now, ttl_seconds=300))
    assert jwt.get_unverified_header(new_token)["kid"] == "kid-new"
    assert verify_plugin_token(new_token, keyring=ring, now=now).grant_id == "grant_01"

    # После retire старый kid → UNKNOWN_KID.
    ring.retire("kid-old")
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(old_token, keyring=ring, now=now)
    assert ei.value.code == PluginTokenErrorCode.UNKNOWN_KID


# --- Обязательные claim ------------------------------------------------------

@pytest.mark.parametrize(
    "missing",
    [
        "sub",
        "company_id",
        "plugin_id",
        "plugin_instance_id",
        "manifest_version",
        "grant_id",
        "jti",
        "iat",
        "exp",
        "scopes",
        "quota_ref",
        "artifact_digest",
    ],
)
def test_missing_required_claim_is_rejected(
    missing: str,
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    payload = build_payload(grant=grant, instance=instance, now=now, ttl_seconds=300)
    del payload[missing]
    token = jwt.encode(
        payload,
        secret,
        algorithm="HS256",
        headers={"kid": keyring.active_kid},
    )
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(token, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.MISSING_CLAIM


def test_empty_required_string_claim_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    bad = _tamper_payload(token, changes={"grant_id": "   "})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(bad, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.MISSING_CLAIM


def test_malformed_jti_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    bad = _tamper_payload(token, changes={"jti": "not-a-jti"})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(bad, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.MALFORMED


def test_sub_mismatch_is_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    bad = _tamper_payload(token, changes={"sub": "pod:some-pod-id"})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(bad, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.MALFORMED


# --- artifact_digest ---------------------------------------------------------

@pytest.mark.parametrize(
    "bad_digest",
    [
        "abc",
        "sha256:XYZ",                       # не hex / uppercase
        "sha256:" + "A" * 64,               # uppercase не допускаем (единый формат с БД)
        "sha256:" + "a" * 63,               # короткий
        "sha256:" + "a" * 65,               # длинный
        "md5:" + "a" * 32,
        "",
    ],
)
def test_malformed_artifact_digest_is_rejected_at_mint(
    bad_digest: str,
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
) -> None:
    broken = grant.replace(artifact_digest=bad_digest)
    with pytest.raises(PluginTokenError) as ei:
        mint_plugin_claims(grant=broken, instance=instance, secret=secret)
    assert ei.value.code == PluginTokenErrorCode.MALFORMED


@pytest.mark.parametrize("bad_digest", ["abc", "sha256:XYZ", "md5:" + "a" * 32])
def test_malformed_artifact_digest_is_rejected_at_verify(
    bad_digest: str,
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    bad = _tamper_payload(token, changes={"artifact_digest": bad_digest})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(bad, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.MALFORMED


def test_long_lived_policy_claims_in_token_are_rejected(
    grant: FakeGrant,
    instance: FakeInstance,
    secret: str,
    keyring: KeyRing,
    now: int,
) -> None:
    token = mint_plugin_claims(grant=grant, instance=instance, secret=secret)
    bad = _tamper_payload(token, changes={"egress": {"allowed_hosts": ["x"]}})
    with pytest.raises(PluginTokenError) as ei:
        verify_plugin_token(bad, keyring=keyring, now=now)
    assert ei.value.code == PluginTokenErrorCode.MALFORMED


# --- PluginClaims методы -----------------------------------------------------

def test_plugin_claims_scope_and_company_helpers(claims: PluginClaims) -> None:
    assert claims.has_scope("cache") is True
    assert claims.has_scope("pods") is False
    claims.require_scope("objects")
    claims.require_company("co_123")

    with pytest.raises(PluginTokenError) as ei:
        claims.require_scope("pods")
    assert ei.value.code == PluginTokenErrorCode.MISSING_CLAIM

    with pytest.raises(PluginTokenError) as ei:
        claims.require_company("co_other")
    assert ei.value.code == PluginTokenErrorCode.WRONG_AUDIENCE


def test_plugin_claims_is_expired(claims: PluginClaims) -> None:
    assert claims.is_expired(claims.exp - 1) is False
    assert claims.is_expired(claims.exp) is True
    assert claims.is_expired(claims.exp + 1) is True


def test_plugin_claims_is_frozen(claims: PluginClaims) -> None:
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        claims.company_id = "co_other"  # type: ignore[misc]


def test_build_sub_is_not_pod_subject() -> None:
    assert PluginClaims.build_sub("mod_equipment", "co_123", "inst_abc").startswith("plugin:")
    assert not PluginClaims.build_sub("mod_equipment", "co_123", "inst_abc").startswith("pod:")
