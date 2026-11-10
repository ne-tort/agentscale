"""GitHub Actions OIDC/JWKS unit tests; all keys and JWKS are local fakes."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from prodavan.application.plugins.artifacts import oidc as oidc_module
from prodavan.application.plugins.artifacts.oidc import GitHubActionsOidcValidator
from prodavan.application.plugins.artifacts.oidc import OidcErrorCode
from prodavan.application.plugins.artifacts.oidc import OidcValidationError
from prodavan.application.plugins.sources.types import ModuleSource, ModuleSourceStatus

NOW = 1_800_000_000
COMMIT = "c" * 40
WORKFLOW = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40
AUDIENCE = "agentscale-plugin-ci"


def _b64_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _key(kid: str = "kid-1") -> tuple[Any, dict[str, Any]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    numbers = private.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64_uint(numbers.n),
        "e": _b64_uint(numbers.e),
    }
    return pem, jwk


@pytest.fixture(scope="module")
def key_material() -> tuple[Any, dict[str, Any]]:
    return _key()


class FakeJwks:
    def __init__(self, *documents: dict[str, Any]) -> None:
        self.documents = documents
        self.calls = 0
        self.urls: list[str] = []

    async def __call__(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        index = min(self.calls, len(self.documents) - 1)
        self.calls += 1
        return self.documents[index]


def _source() -> ModuleSource:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    return ModuleSource(
        id="psrc_0123456789abcdef",
        company_id="company_1",
        plugin_id="hello_plugin",
        repo_id=123456,
        full_name="ne-tort/example",
        private=True,
        default_branch="main",
        installation_credential_ref="vault://github_app/company_1/hello_plugin/installation",
        trusted_workflow_ref=WORKFLOW,
        status=ModuleSourceStatus.READY,
        created_at=now,
        updated_at=now,
    )


def _claims() -> dict[str, Any]:
    return {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": AUDIENCE,
        "exp": NOW + 600,
        "iat": NOW - 10,
        "nbf": NOW - 20,
        "repository": "ne-tort/example",
        "repository_id": "123456",
        "ref": "refs/heads/main",
        "sha": COMMIT,
        "event_name": "push",
        "job_workflow_ref": WORKFLOW,
        "ref_protected": "true",
        "run_id": "99",
        "run_attempt": "1",
    }


def _token(
    key: Any,
    claims: dict[str, Any] | None = None,
    *,
    kid: str = "kid-1",
) -> str:
    return jwt.encode(
        claims or _claims(),
        key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _validator(fetcher: FakeJwks) -> GitHubActionsOidcValidator:
    return GitHubActionsOidcValidator(
        audience=AUDIENCE,
        jwks_fetcher=fetcher,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_valid_oidc_binds_source_and_lazy_jwks(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    fetcher = FakeJwks({"keys": [jwk]})
    validator = _validator(fetcher)
    assert fetcher.calls == 0, "constructing the validator must not perform network/JWKS I/O"

    claims = await validator.validate_for_source(
        _token(key),
        source=_source(),
        commit_sha=COMMIT,
        ref="refs/heads/main",
    )
    assert claims.repository == "ne-tort/example"
    assert claims.repository_id == "123456"
    assert claims.run_id == "99"
    assert fetcher.calls == 1

    await validator.validate(_token(key))
    assert fetcher.calls == 1, "fresh cached JWKS must be reused"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("repository", "ne-tort/other"),
        ("repository_id", "999999"),
        ("ref", "refs/heads/feature"),
        ("sha", "d" * 40),
        ("event_name", "workflow_dispatch"),
        ("job_workflow_ref", WORKFLOW[:-40] + "b" * 40),
    ],
)
async def test_wrong_source_binding_claim_is_denied(
    key_material: tuple[Any, dict[str, Any]], claim: str, value: Any
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims[claim] = value
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate_for_source(
            _token(key, claims),
            source=_source(),
            commit_sha=COMMIT,
            ref="refs/heads/main",
        )
    assert caught.value.code is OidcErrorCode.SOURCE_BINDING_MISMATCH
    assert caught.value.claim == claim


@pytest.mark.asyncio
async def test_body_ref_must_equal_protected_default_ref(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate_for_source(
            _token(key),
            source=_source(),
            commit_sha=COMMIT,
            ref="refs/heads/other",
        )
    assert caught.value.claim == "ref"


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", ["other", [AUDIENCE, "other"]])
async def test_wrong_audience_is_denied(
    key_material: tuple[Any, dict[str, Any]], audience: Any
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["aud"] = audience
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, claims))
    assert caught.value.code is OidcErrorCode.INVALID_AUDIENCE


@pytest.mark.asyncio
async def test_wrong_issuer_is_denied(key_material: tuple[Any, dict[str, Any]]) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["iss"] = "https://issuer.example.test"
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, claims))
    assert caught.value.code is OidcErrorCode.INVALID_ISSUER


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", ["HS256", "none"])
async def test_symmetric_and_none_algorithms_are_denied_before_jwks(
    key_material: tuple[Any, dict[str, Any]], algorithm: str
) -> None:
    _, jwk = key_material
    fetcher = FakeJwks({"keys": [jwk]})
    token = jwt.encode(
        _claims(),
        "not-a-platform-secret" if algorithm == "HS256" else "",
        algorithm=algorithm,
        headers={"kid": "kid-1"},
    )
    with pytest.raises(OidcValidationError) as caught:
        await _validator(fetcher).validate(token)
    assert caught.value.code is OidcErrorCode.ALGORITHM_FORBIDDEN
    assert fetcher.calls == 0


@pytest.mark.asyncio
async def test_expired_token_is_denied(key_material: tuple[Any, dict[str, Any]]) -> None:
    key, jwk = key_material
    claims = _claims()
    claims.update(exp=NOW - 1, iat=NOW - 30, nbf=NOW - 40)
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, claims))
    assert caught.value.code is OidcErrorCode.TOKEN_EXPIRED


@pytest.mark.asyncio
async def test_future_iat_and_nbf_are_denied(key_material: tuple[Any, dict[str, Any]]) -> None:
    key, jwk = key_material
    future_iat = _claims()
    future_iat["iat"] = NOW + 60
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, future_iat))
    assert caught.value.claim == "iat"

    future_nbf = _claims()
    future_nbf["nbf"] = NOW + 60
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, future_nbf))
    assert caught.value.code is OidcErrorCode.TOKEN_NOT_ACTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [True, "false", "1"])
async def test_ref_protected_must_be_github_true_string(
    key_material: tuple[Any, dict[str, Any]], protected: Any
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["ref_protected"] = protected
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, claims))
    assert caught.value.claim == "ref_protected"


@pytest.mark.asyncio
async def test_unknown_kid_forces_one_jwks_rotation() -> None:
    old_key, old_jwk = _key("kid-old")
    new_key, new_jwk = _key("kid-new")
    fetcher = FakeJwks({"keys": [old_jwk]}, {"keys": [new_jwk]})
    validator = _validator(fetcher)

    await validator.validate(_token(old_key, kid="kid-old"))
    assert fetcher.calls == 1
    claims = await validator.validate(_token(new_key, kid="kid-new"))
    assert claims.sha == COMMIT
    assert fetcher.calls == 2, "unknown kid must refresh the key set exactly once"


@pytest.mark.asyncio
async def test_expired_cache_is_refetched(key_material: tuple[Any, dict[str, Any]]) -> None:
    key, jwk = key_material
    fetcher = FakeJwks({"keys": [jwk]})
    ticks = [0.0]
    validator = GitHubActionsOidcValidator(
        audience=AUDIENCE,
        jwks_fetcher=fetcher,
        cache_ttl_seconds=10,
        clock=lambda: NOW,
        monotonic=lambda: ticks[0],
    )
    await validator.validate(_token(key))
    ticks[0] = 11.0
    await validator.validate(_token(key))
    assert fetcher.calls == 2


def test_import_does_not_construct_http_client_or_fetch_jwks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForbiddenClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("network client constructed at import")

    monkeypatch.setattr(httpx, "AsyncClient", ForbiddenClient)
    name = "_agentscale_oidc_import_probe"
    path = Path(oidc_module.__file__)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
