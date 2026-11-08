"""F1: KeyRing — kid, JWKS без приватного ключа, ротация, запрет dev-фолбэков.

Тесты **написаны, но не запускаются** (verification freeze).
"""

from __future__ import annotations

import json

import jwt
import pytest

from prodavan.application.plugins.identity import (
    SUPPORTED_ALGORITHMS,
    KeyEntry,
    KeyRing,
    PluginTokenError,
    PluginTokenErrorCode,
    build_payload,
    generate_kid,
    hs256_entry,
    keyring_from_secret,
    kid_from_secret,
    mint_plugin_claims,
    peek_plugin_token,
    verify_plugin_token,
)
from tests.unit.plugins.identity.conftest import TEST_SECRET, FakeGrant, FakeInstance


def test_supported_algorithms_are_hs256_and_eddsa() -> None:
    assert SUPPORTED_ALGORITHMS == frozenset({"HS256", "EdDSA"})


def test_peek_plugin_token_from_keys_module_returns_typ_without_verification() -> None:
    """Родитель импортирует ``peek_plugin_token`` именно из ``keys.py``."""
    from prodavan.application.plugins.identity.keys import peek_plugin_token as peek_from_keys

    token = mint_plugin_claims(grant=FakeGrant(), instance=FakeInstance(), secret=TEST_SECRET)
    assert callable(peek_from_keys)
    payload = peek_from_keys(token)
    assert payload is not None
    assert payload["typ"] == "plugin_capability"
    assert payload["iss"] == "agentscale-plugins"
    assert payload["aud"] == "capability-gateway"


@pytest.mark.parametrize("broken", ["", "   ", "garbage", "a.b", "a.b.c.d", None, 123])
def test_peek_plugin_token_never_raises_on_broken_token(broken) -> None:
    assert peek_plugin_token(broken) is None


def test_peek_plugin_token_returns_none_for_non_mapping_payload() -> None:
    # JWT с payload-массивом: decode вернёт list → None, а не исключение.
    token = jwt.encode(["not", "a", "mapping"], TEST_SECRET, algorithm="HS256")
    assert peek_plugin_token(token) is None


def test_generate_kid_is_prefixed_and_unique() -> None:
    kid = generate_kid("HS256")
    assert kid.startswith("hs256-")
    assert kid != generate_kid("HS256")


def test_kid_from_secret_is_deterministic_and_hides_secret() -> None:
    first = kid_from_secret(TEST_SECRET)
    assert first == kid_from_secret(TEST_SECRET)
    assert TEST_SECRET not in first
    assert first != kid_from_secret("other-secret")


def test_keyring_requires_explicit_key_no_dev_fallback() -> None:
    # Пустой ring = ошибка, а не «подставим settings.auth_test_secret»
    # (ср. bridge.py:111-128).
    with pytest.raises(PluginTokenError) as ei:
        KeyRing([])
    assert ei.value.code == PluginTokenErrorCode.UNKNOWN_KID


def test_keyring_unknown_active_kid_is_rejected() -> None:
    entry = hs256_entry(TEST_SECRET, kid="known-kid")
    with pytest.raises(PluginTokenError) as ei:
        KeyRing([entry], active_kid="missing-kid")
    assert ei.value.code == PluginTokenErrorCode.UNKNOWN_KID


def test_keyring_duplicate_kid_is_rejected() -> None:
    a = hs256_entry(TEST_SECRET, kid="same-kid")
    b = hs256_entry("another", kid="same-kid")
    with pytest.raises(ValueError, match="duplicate"):
        KeyRing([a, b])


def test_empty_secret_is_rejected() -> None:
    with pytest.raises(PluginTokenError) as ei:
        hs256_entry("")
    assert ei.value.code == PluginTokenErrorCode.INVALID_SIGNATURE
    with pytest.raises(PluginTokenError) as ei:
        keyring_from_secret("")
    assert ei.value.code == PluginTokenErrorCode.INVALID_SIGNATURE


def test_jwks_never_contains_private_key() -> None:
    """Главный тест: JWKS не раскрывает симметричный секрет / приватный PEM."""
    ring = keyring_from_secret(TEST_SECRET)
    jwks = ring.jwks()

    assert set(jwks) == {"keys"}
    # HS256 — симметричный: публиковать нечего, секрет не утекает.
    assert jwks["keys"] == []
    serialized = json.dumps(jwks)
    assert TEST_SECRET not in serialized
    assert "secret" not in serialized
    assert "d" not in jwks  # приватная часть OKP-JWK


def test_jwks_with_hs256_and_eddsa_does_not_leak_private_pem() -> None:
    """При наличии EdDSA-ключа JWKS содержит только публичный OKP-JWK."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from prodavan.application.plugins.identity import eddsa_entry

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    ring = KeyRing(
        [
            hs256_entry(TEST_SECRET, kid="hs256-kid"),
            eddsa_entry(private_pem, public_pem, kid="eddsa-kid"),
        ]
    )
    jwks = ring.jwks()
    assert len(jwks["keys"]) == 1  # HS256 не публикуется
    jwk = jwks["keys"][0]
    assert jwk["kty"] == "OKP"
    assert jwk["crv"] == "Ed25519"
    assert jwk["kid"] == "eddsa-kid"
    assert jwk["alg"] == "EdDSA"
    assert "x" in jwk and jwk["x"]
    # Ни приватной координаты ``d``, ни PEM, ни симметричного секрета.
    assert "d" not in jwk
    assert "k" not in jwk  # симметричный JWK (oct) не публикуется
    serialized = json.dumps(jwks)
    assert "BEGIN PRIVATE KEY" not in serialized
    assert TEST_SECRET not in serialized
    assert "secret" not in serialized


def test_eddsa_round_trip_is_supported() -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from prodavan.application.plugins.identity import eddsa_entry

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    ring = KeyRing([eddsa_entry(private_pem, public_pem, kid="eddsa-kid")])
    payload = build_payload(
        grant=FakeGrant(), instance=FakeInstance(), now=1_700_000_000, ttl_seconds=300
    )
    token = ring.sign(payload)
    assert jwt.get_unverified_header(token)["kid"] == "eddsa-kid"
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"
    assert ring.decode(token)["grant_id"] == "grant_01"


def test_eddsa_entry_requires_public_pem() -> None:
    with pytest.raises(ValueError, match="public PEM"):
        KeyEntry(kid="eddsa-no-public", algorithm="EdDSA", secret="private-pem")


def test_unsupported_algorithm_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        KeyEntry(kid="rsa", algorithm="RS256", secret="x")


def test_keyring_sign_uses_kid_header() -> None:
    ring = keyring_from_secret(TEST_SECRET)
    token = ring.sign({"a": 1})
    header = jwt.get_unverified_header(token)
    assert header["kid"] == ring.active_kid
    assert header["alg"] == "HS256"


def test_keyring_decode_rejects_token_signed_by_unknown_key() -> None:
    ring = keyring_from_secret(TEST_SECRET)
    foreign = jwt.encode({"a": 1}, "another-secret", algorithm="HS256", headers={"kid": ring.active_kid})
    with pytest.raises(PluginTokenError) as ei:
        ring.decode(foreign)
    assert ei.value.code == PluginTokenErrorCode.INVALID_SIGNATURE


def test_keyring_rotate_and_retire() -> None:
    old = hs256_entry(TEST_SECRET, kid="kid-old")
    ring = KeyRing([old], active_kid="kid-old")
    old_token = ring.sign({"v": "old"})

    new = hs256_entry("rotated", kid="kid-new")
    assert ring.rotate(new) == "kid-new"
    assert ring.active_kid == "kid-new"
    assert ring.decode(old_token) == {"v": "old"}   # старый kid ещё верифицируется

    with pytest.raises(ValueError, match="already registered"):
        ring.rotate(new)
    with pytest.raises(ValueError, match="active"):
        ring.retire("kid-new")

    ring.retire("kid-old")
    with pytest.raises(PluginTokenError) as ei:
        ring.decode(old_token)
    assert ei.value.code == PluginTokenErrorCode.UNKNOWN_KID


def test_rotate_without_activation_keeps_previous_active() -> None:
    old = hs256_entry(TEST_SECRET, kid="kid-old")
    ring = KeyRing([old], active_kid="kid-old")
    staged = hs256_entry("staged", kid="kid-staged")
    assert ring.rotate(staged, activate=False) == "kid-old"
    assert jwt.get_unverified_header(ring.sign({"v": 1}))["kid"] == "kid-old"


def test_keyring_from_secret_verifies_minted_token(now: int) -> None:
    """Связка mint(secret=...) ↔ verify(keyring_from_secret(secret))."""
    grant = FakeGrant()
    instance = FakeInstance()
    token = mint_plugin_claims(grant=grant, instance=instance, secret=TEST_SECRET)
    claims = verify_plugin_token(token, keyring=keyring_from_secret(TEST_SECRET), now=now)
    assert claims.grant_id == grant.grant_id
    assert claims.plugin_instance_id == instance.plugin_instance_id
