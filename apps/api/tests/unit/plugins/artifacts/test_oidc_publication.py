"""GitHub Actions OIDC/JWKS unit tests; all keys and JWKS are local fakes."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from prodavan.application.plugins.artifacts import oidc as oidc_module
from prodavan.application.plugins.artifacts.oidc import GitHubActionsOidcValidator, OidcErrorCode, OidcValidationError
from prodavan.application.plugins.sources.types import ModuleSource, ModuleSourceStatus

NOW = 1_800_000_000
COMMIT = "c" * 40
WORKFLOW = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40
DIRECT_WORKFLOW = (
    "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main"
)
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
    def __init__(
        self,
        *documents: dict[str, Any],
        failures: dict[int, Exception] | None = None,
        yield_control: bool = False,
    ) -> None:
        self.documents = documents
        self.failures = failures or {}
        self.yield_control = yield_control
        self.calls = 0
        self.urls: list[str] = []

    async def __call__(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        index = min(self.calls, len(self.documents) - 1)
        self.calls += 1
        if self.yield_control:
            await asyncio.sleep(0)
        failure = self.failures.get(self.calls)
        if failure is not None:
            raise failure
        return self.documents[index]


def _source(
    *, full_name: str = "ne-tort/example", workflow: str = WORKFLOW
) -> ModuleSource:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    return ModuleSource(
        id="psrc_0123456789abcdef",
        company_id="company_1",
        plugin_id="hello_plugin",
        repo_id=123456,
        full_name=full_name,
        private=True,
        default_branch="main",
        installation_credential_ref="vault://github_app/company_1/hello_plugin/installation",
        trusted_workflow_ref=workflow,
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

    token_claims = _claims()
    token_claims["event_name"] = "workflow_call"
    claims = await validator.validate_for_source(
        _token(key, token_claims),
        source=_source(),
        commit_sha=COMMIT,
        ref="refs/heads/main",
    )
    assert claims.repository == "ne-tort/example"
    assert claims.repository_id == "123456"
    assert claims.run_id == "99"
    assert fetcher.calls == 1

    await validator.validate(_token(key, token_claims))
    assert fetcher.calls == 1, "fresh cached JWKS must be reused"


@pytest.mark.asyncio
async def test_reusable_workflow_call_binds_full_sha_workflow(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["event_name"] = "workflow_call"
    validated = await _validator(FakeJwks({"keys": [jwk]})).validate_for_source(
        _token(key, claims),
        source=_source(),
        commit_sha=COMMIT,
        ref="refs/heads/main",
    )
    assert validated.event_name == "workflow_call"
    assert validated.job_workflow_ref == WORKFLOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_name", "full_name", "workflow"),
    [
        ("push", "ne-tort/example", WORKFLOW),
        ("workflow_dispatch", "ne-tort/example", WORKFLOW),
        ("workflow_call", "ne-tort/agentscale", DIRECT_WORKFLOW),
    ],
)
async def test_event_must_match_workflow_identity_kind(
    key_material: tuple[Any, dict[str, Any]],
    event_name: str,
    full_name: str,
    workflow: str,
) -> None:
    key, jwk = key_material
    source = _source(full_name=full_name, workflow=workflow)
    claims = _claims()
    claims.update(
        repository=source.full_name,
        event_name=event_name,
        job_workflow_ref=workflow,
    )
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate_for_source(
            _token(key, claims),
            source=source,
            commit_sha=COMMIT,
            ref=source.expected_ref,
        )
    assert caught.value.code is OidcErrorCode.INVALID_CLAIM
    assert caught.value.claim == "job_workflow_ref"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_name", "full_name", "source_workflow", "token_workflow"),
    [
        ("workflow_call", "ne-tort/agentscale", DIRECT_WORKFLOW, WORKFLOW),
        ("workflow_call", "ne-tort/example", WORKFLOW, DIRECT_WORKFLOW),
        ("push", "ne-tort/agentscale", DIRECT_WORKFLOW, WORKFLOW),
        ("push", "ne-tort/example", WORKFLOW, DIRECT_WORKFLOW),
    ],
)
async def test_workflow_source_mismatch_is_denied_before_mode_matrix(
    key_material: tuple[Any, dict[str, Any]],
    event_name: str,
    full_name: str,
    source_workflow: str,
    token_workflow: str,
) -> None:
    key, jwk = key_material
    source = _source(full_name=full_name, workflow=source_workflow)
    claims = _claims()
    claims.update(
        repository=source.full_name,
        event_name=event_name,
        job_workflow_ref=token_workflow,
    )
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate_for_source(
            _token(key, claims),
            source=source,
            commit_sha=COMMIT,
            ref=source.expected_ref,
        )
    assert caught.value.code is OidcErrorCode.SOURCE_BINDING_MISMATCH
    assert caught.value.claim == "job_workflow_ref"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch"])
async def test_direct_workflow_binds_canonical_protected_branch_identity(
    key_material: tuple[Any, dict[str, Any]], event_name: str
) -> None:
    key, jwk = key_material
    source = _source(full_name="ne-tort/agentscale", workflow=DIRECT_WORKFLOW)
    claims = _claims()
    claims.update(
        repository=source.full_name,
        event_name=event_name,
        job_workflow_ref=DIRECT_WORKFLOW,
    )
    validated = await _validator(FakeJwks({"keys": [jwk]})).validate_for_source(
        _token(key, claims),
        source=source,
        commit_sha=COMMIT,
        ref=source.expected_ref,
    )
    assert validated.event_name == event_name
    assert validated.job_workflow_ref == DIRECT_WORKFLOW


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch", "workflow_call"])
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("repository", "ne-tort/other"),
        ("repository_id", "999999"),
        ("ref", "refs/heads/feature"),
        ("sha", "d" * 40),
        ("job_workflow_ref", WORKFLOW[:-40] + "b" * 40),
    ],
)
async def test_wrong_source_binding_claim_is_denied_for_every_event(
    key_material: tuple[Any, dict[str, Any]], event_name: str, claim: str, value: Any
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["event_name"] = event_name
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
@pytest.mark.parametrize(
    "actual_workflow",
    [
        DIRECT_WORKFLOW.replace("refs/heads/main", "refs/heads/release"),
        DIRECT_WORKFLOW.replace("ne-tort/agentscale", "ne-tort/other"),
        DIRECT_WORKFLOW.replace("plugin-artifacts.yml", "other.yml"),
        DIRECT_WORKFLOW.replace("refs/heads/main", "refs/heads/*"),
        WORKFLOW,
    ],
)
async def test_direct_workflow_rejects_wrong_identity(
    key_material: tuple[Any, dict[str, Any]], actual_workflow: str
) -> None:
    key, jwk = key_material
    source = _source(full_name="ne-tort/agentscale", workflow=DIRECT_WORKFLOW)
    claims = _claims()
    claims.update(
        repository=source.full_name,
        event_name="workflow_dispatch",
        job_workflow_ref=actual_workflow,
    )
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate_for_source(
            _token(key, claims),
            source=source,
            commit_sha=COMMIT,
            ref=source.expected_ref,
        )
    assert caught.value.code is OidcErrorCode.SOURCE_BINDING_MISMATCH
    assert caught.value.claim == "job_workflow_ref"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", ["pull_request", "pull_request_target", "schedule", ""])
async def test_unsupported_event_is_denied(
    key_material: tuple[Any, dict[str, Any]], event_name: str
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["event_name"] = event_name
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate_for_source(
            _token(key, claims),
            source=_source(),
            commit_sha=COMMIT,
            ref="refs/heads/main",
        )
    assert caught.value.code is OidcErrorCode.SOURCE_BINDING_MISMATCH
    assert caught.value.claim == "event_name"


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
@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
async def test_huge_numeric_date_is_typed_invalid_claim(
    key_material: tuple[Any, dict[str, Any]], claim: str
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims[claim] = 10**400
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, claims))
    assert caught.value.code is OidcErrorCode.INVALID_CLAIM
    assert caught.value.claim == claim


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch", "workflow_call"])
@pytest.mark.parametrize("protected", [True, "false", "1"])
async def test_ref_protected_must_be_github_true_string_for_every_event(
    key_material: tuple[Any, dict[str, Any]], event_name: str, protected: Any
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["event_name"] = event_name
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "kty_oct",
        "private_d",
        "use_enc",
        "duplicate_kid",
        "key_ops_sign",
        "alg_outside_allowlist",
        "empty_keys",
    ],
)
async def test_malicious_or_empty_jwks_is_unavailable(
    key_material: tuple[Any, dict[str, Any]], mutation: str
) -> None:
    key, jwk = key_material
    candidate = dict(jwk)
    document: dict[str, Any] = {"keys": [candidate]}
    if mutation == "kty_oct":
        candidate["kty"] = "oct"
    elif mutation == "private_d":
        candidate["d"] = "private-key-material"
    elif mutation == "use_enc":
        candidate["use"] = "enc"
    elif mutation == "duplicate_kid":
        document = {"keys": [candidate, dict(candidate)]}
    elif mutation == "key_ops_sign":
        candidate["key_ops"] = ["sign"]
    elif mutation == "alg_outside_allowlist":
        candidate["alg"] = "RS512"
    elif mutation == "empty_keys":
        document = {"keys": []}
    else:
        raise AssertionError(f"unknown JWKS mutation: {mutation}")

    fetcher = FakeJwks(document)
    with pytest.raises(OidcValidationError) as caught:
        await _validator(fetcher).validate(_token(key))
    assert caught.value.code is OidcErrorCode.JWKS_UNAVAILABLE
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_unknown_kid_is_denied_after_exactly_one_refresh(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    fetcher = FakeJwks({"keys": [jwk]})
    with pytest.raises(OidcValidationError) as caught:
        await _validator(fetcher).validate(_token(key, kid="kid-absent"))
    assert caught.value.code is OidcErrorCode.UNKNOWN_KEY
    assert fetcher.calls == 2


@pytest.mark.asyncio
async def test_kid_algorithm_mismatch_is_denied_without_fallback(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    mismatched_jwk = dict(jwk)
    mismatched_jwk["alg"] = "RS512"
    fetcher = FakeJwks({"keys": [mismatched_jwk]})
    validator = GitHubActionsOidcValidator(
        audience=AUDIENCE,
        jwks_fetcher=fetcher,
        clock=lambda: NOW,
        allowed_algorithms=frozenset({"RS256", "RS512"}),
    )
    with pytest.raises(OidcValidationError) as caught:
        await validator.validate(_token(key))
    assert caught.value.code is OidcErrorCode.UNKNOWN_KEY
    assert fetcher.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kid", "expected_code"),
    [
        (None, OidcErrorCode.UNKNOWN_KEY),
        (17, OidcErrorCode.TOKEN_MALFORMED),
        ("x" * 257, OidcErrorCode.UNKNOWN_KEY),
    ],
)
async def test_missing_nonstring_or_oversized_kid_is_denied_before_fetch(
    key_material: tuple[Any, dict[str, Any]], kid: Any, expected_code: OidcErrorCode
) -> None:
    key, jwk = key_material
    # PyJWT validates its own header before signing, so malformed/non-string
    # kid values are encoded manually: the validator must reject them before
    # JWKS I/O, without relying on a signature that is already valid.
    header = {"alg": "RS256", "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    encoded_header = base64.urlsafe_b64encode(
        json.dumps(header, separators=(",", ":")).encode("ascii")
    ).rstrip(b"=").decode("ascii")
    signed = jwt.encode(_claims(), key, algorithm="RS256")
    token = f"{encoded_header}.{signed.split('.', 1)[1]}"
    fetcher = FakeJwks({"keys": [jwk]})
    with pytest.raises(OidcValidationError) as caught:
        await _validator(fetcher).validate(token)
    assert caught.value.code is expected_code
    assert fetcher.calls == 0


@pytest.mark.asyncio
async def test_wrong_signature_with_same_kid_is_denied(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    other_key, _ = _key("kid-1")
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(
            _token(other_key, kid="kid-1")
        )
    assert caught.value.code is OidcErrorCode.INVALID_SIGNATURE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": NOW + 10, "iat": NOW + 10, "nbf": NOW - 20},
        {"exp": NOW, "iat": NOW + 10, "nbf": NOW - 20},
        {"exp": NOW + 10, "iat": NOW - 10, "nbf": NOW + 10},
        {"exp": NOW + 10, "iat": NOW - 10, "nbf": NOW + 20},
    ],
)
async def test_invalid_numeric_date_order_is_typed_invalid_claim(
    key_material: tuple[Any, dict[str, Any]], overrides: dict[str, int]
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims.update(overrides)
    validator = GitHubActionsOidcValidator(
        audience=AUDIENCE,
        jwks_fetcher=FakeJwks({"keys": [jwk]}),
        clock=lambda: NOW,
        leeway_seconds=30,
    )
    with pytest.raises(OidcValidationError) as caught:
        await validator.validate(_token(key, claims))
    assert caught.value.code is OidcErrorCode.INVALID_CLAIM
    assert caught.value.claim == "exp"


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
@pytest.mark.parametrize("value", [True, "1800000000", float("nan")])
async def test_bool_string_and_nan_numeric_dates_are_typed_invalid_claims(
    key_material: tuple[Any, dict[str, Any]], claim: str, value: Any
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims[claim] = value
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, claims))
    assert caught.value.code is OidcErrorCode.INVALID_CLAIM
    assert caught.value.claim == claim


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["job_workflow_ref", "sha"])
async def test_missing_required_source_claim_is_token_malformed(
    key_material: tuple[Any, dict[str, Any]], claim: str
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims.pop(claim)
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, claims))
    assert caught.value.code is OidcErrorCode.TOKEN_MALFORMED


@pytest.mark.asyncio
async def test_nonstring_repository_id_is_denied(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["repository_id"] = 123456
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(_token(key, claims))
    assert caught.value.code is OidcErrorCode.INVALID_CLAIM
    assert caught.value.claim == "repository_id"


@pytest.mark.asyncio
async def test_single_element_audience_list_is_accepted(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    claims = _claims()
    claims["aud"] = [AUDIENCE]
    validated = await _validator(FakeJwks({"keys": [jwk]})).validate(
        _token(key, claims)
    )
    assert validated.sha == COMMIT


@pytest.mark.asyncio
async def test_short_commit_is_denied_before_token_or_jwks_io(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    fetcher = FakeJwks({"keys": [jwk]})
    with pytest.raises(OidcValidationError) as caught:
        await _validator(fetcher).validate_for_source(
            _token(key),
            source=_source(),
            commit_sha="c" * 39,
            ref="refs/heads/main",
        )
    assert caught.value.code is OidcErrorCode.INVALID_CLAIM
    assert caught.value.claim == "sha"
    assert fetcher.calls == 0


@pytest.mark.asyncio
async def test_fetch_failure_is_not_cached_and_retry_succeeds(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    fetcher = FakeJwks(
        {"keys": [jwk]},
        failures={1: httpx.ConnectError("temporary JWKS outage")},
    )
    validator = _validator(fetcher)
    token = _token(key)
    with pytest.raises(OidcValidationError) as caught:
        await validator.validate(token)
    assert caught.value.code is OidcErrorCode.JWKS_UNAVAILABLE

    claims = await validator.validate(token)
    assert claims.sha == COMMIT
    assert fetcher.calls == 2


@pytest.mark.asyncio
async def test_concurrent_cold_start_fetches_jwks_once(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    fetcher = FakeJwks({"keys": [jwk]}, yield_control=True)
    validator = _validator(fetcher)
    token = _token(key)
    results = await asyncio.gather(*(validator.validate(token) for _ in range(8)))
    assert [claims.sha for claims in results] == [COMMIT] * 8
    assert fetcher.calls == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"audience": ""},
        {"audience": " agentscale-plugin-ci "},
        {"jwks_url": "http://token.actions.githubusercontent.com/.well-known/jwks"},
        {"allowed_algorithms": frozenset()},
        {"allowed_algorithms": frozenset({"HS256"})},
        {"allowed_algorithms": frozenset({"none"})},
        {"allowed_algorithms": frozenset({"ROT13"})},
        {"cache_ttl_seconds": 0},
        {"leeway_seconds": -1},
        {"leeway_seconds": 31},
    ],
)
def test_unsafe_constructor_configuration_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        GitHubActionsOidcValidator(**overrides)


@pytest.mark.asyncio
async def test_denial_messages_redact_token_jwk_and_upstream_body(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    malformed = "not-a-jwt-with-secret-token-material"
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [jwk]})).validate(malformed)
    assert malformed not in str(caught.value)

    private_jwk = dict(jwk)
    private_jwk["d"] = "secret-jwk-private-material"
    with pytest.raises(OidcValidationError) as caught:
        await _validator(FakeJwks({"keys": [private_jwk]})).validate(_token(key))
    assert private_jwk["d"] not in str(caught.value)

    upstream_body = "secret-upstream-response-body"
    fetcher = FakeJwks(
        {"keys": [jwk]},
        failures={1: ValueError(upstream_body)},
    )
    with pytest.raises(OidcValidationError) as caught:
        await _validator(fetcher).validate(_token(key))
    assert upstream_body not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [None, 17, "", "   ", "not-a-jwt", "a.b", "a.b.c.d"],
)
async def test_malformed_token_shapes_are_denied_before_fetch(
    key_material: tuple[Any, dict[str, Any]], token: Any
) -> None:
    _, jwk = key_material
    fetcher = FakeJwks({"keys": [jwk]})
    with pytest.raises(OidcValidationError) as caught:
        await _validator(fetcher).validate(token)
    assert caught.value.code is OidcErrorCode.TOKEN_MALFORMED
    assert fetcher.calls == 0


@pytest.mark.asyncio
async def test_whitespace_wrapped_valid_token_is_denied_before_fetch(
    key_material: tuple[Any, dict[str, Any]],
) -> None:
    key, jwk = key_material
    fetcher = FakeJwks({"keys": [jwk]})
    with pytest.raises(OidcValidationError) as caught:
        await _validator(fetcher).validate(f"  {_token(key)}  ")
    assert caught.value.code is OidcErrorCode.TOKEN_MALFORMED
    assert fetcher.calls == 0


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
