"""GitHub Actions OIDC validation with lazy asynchronous JWKS rotation.

This validator is deliberately independent from the global Keycloak validator.
It performs no I/O at import time. JWKS is fetched on first token validation,
cached for a bounded TTL, and refreshed once for an unknown kid. Only asymmetric
allowlisted algorithms are accepted; symmetric keys and none are never used.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import jwt

from prodavan.application.plugins.sources.types import ModuleSource

GITHUB_ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_ACTIONS_JWKS_URL = f"{GITHUB_ACTIONS_ISSUER}/.well-known/jwks"
DEFAULT_PLUGIN_AUDIENCE = "agentscale-plugin-ci"
DEFAULT_ALLOWED_ALGORITHMS = frozenset({"RS256"})
_MAX_JWKS_BYTES = 1024 * 1024
_REQUIRED_CLAIMS = (
    "iss",
    "aud",
    "exp",
    "iat",
    "nbf",
    "repository",
    "repository_id",
    "ref",
    "sha",
    "event_name",
    "job_workflow_ref",
    "ref_protected",
)


class OidcErrorCode(StrEnum):
    TOKEN_MALFORMED = "OIDC_TOKEN_MALFORMED"
    ALGORITHM_FORBIDDEN = "OIDC_ALGORITHM_FORBIDDEN"
    JWKS_UNAVAILABLE = "OIDC_JWKS_UNAVAILABLE"
    UNKNOWN_KEY = "OIDC_UNKNOWN_KEY"
    INVALID_SIGNATURE = "OIDC_INVALID_SIGNATURE"
    INVALID_AUDIENCE = "OIDC_INVALID_AUDIENCE"
    INVALID_ISSUER = "OIDC_INVALID_ISSUER"
    TOKEN_EXPIRED = "OIDC_TOKEN_EXPIRED"
    TOKEN_NOT_ACTIVE = "OIDC_TOKEN_NOT_ACTIVE"
    INVALID_CLAIM = "OIDC_INVALID_CLAIM"
    SOURCE_BINDING_MISMATCH = "OIDC_SOURCE_BINDING_MISMATCH"


class OidcValidationError(ValueError):
    """Safe typed denial; never includes token, JWK or upstream response text."""

    def __init__(self, code: OidcErrorCode, *, claim: str | None = None) -> None:
        self.code = code
        self.claim = claim
        super().__init__(code.value if claim is None else f"{code.value}:{claim}")


@dataclass(frozen=True, slots=True)
class GitHubActionsClaims:
    repository: str
    repository_id: str
    ref: str
    sha: str
    event_name: str
    job_workflow_ref: str
    run_id: str = ""
    run_attempt: str = ""

    @classmethod
    def from_mapping(cls, claims: Mapping[str, Any]) -> GitHubActionsClaims:
        values: dict[str, str] = {}
        for name in (
            "repository",
            "repository_id",
            "ref",
            "sha",
            "event_name",
            "job_workflow_ref",
            "run_id",
            "run_attempt",
        ):
            value = claims.get(name, "")
            if not isinstance(value, str):
                raise OidcValidationError(OidcErrorCode.INVALID_CLAIM, claim=name)
            values[name] = value
        for name in (
            "repository",
            "repository_id",
            "ref",
            "sha",
            "event_name",
            "job_workflow_ref",
        ):
            if not values[name]:
                raise OidcValidationError(OidcErrorCode.INVALID_CLAIM, claim=name)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class _SigningKey:
    key: object
    algorithm: str


JwksFetcher = Callable[[str], Awaitable[Mapping[str, object]]]


class GitHubActionsOidcValidator:
    def __init__(
        self,
        *,
        audience: str = DEFAULT_PLUGIN_AUDIENCE,
        jwks_url: str = GITHUB_ACTIONS_JWKS_URL,
        allowed_algorithms: frozenset[str] = DEFAULT_ALLOWED_ALGORITHMS,
        cache_ttl_seconds: float = 300.0,
        leeway_seconds: float = 0.0,
        jwks_fetcher: JwksFetcher | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(audience, str) or not audience.strip() or audience != audience.strip():
            raise ValueError("dedicated GitHub Actions audience is required")
        if not jwks_url.startswith("https://"):
            raise ValueError("GitHub Actions JWKS URL must use https")
        if (
            not allowed_algorithms
            or any(alg.upper().startswith("HS") or alg.lower() == "none" for alg in allowed_algorithms)
            or not allowed_algorithms
            <= {
                "RS256", "RS384", "RS512", "ES256", "ES384", "ES512",
                "PS256", "PS384", "PS512", "EdDSA",
            }
        ):
            raise ValueError("unsafe or unsupported OIDC algorithm allowlist")
        if cache_ttl_seconds <= 0 or not 0 <= leeway_seconds <= 30:
            raise ValueError("invalid OIDC cache/leeway configuration")
        self._audience = audience
        self._jwks_url = jwks_url
        self._algorithms = frozenset(allowed_algorithms)
        self._cache_ttl = float(cache_ttl_seconds)
        self._leeway = float(leeway_seconds)
        self._fetcher = jwks_fetcher or self._http_fetch_jwks
        self._clock = clock
        self._monotonic = monotonic
        self._keys: dict[str, _SigningKey] = {}
        self._fetched_at = float("-inf")
        self._lock = asyncio.Lock()

    async def validate(self, token: str) -> GitHubActionsClaims:
        """Validate signature, issuer/audience and all time claims."""
        header = self._header(token)
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm not in self._algorithms:
            raise OidcValidationError(OidcErrorCode.ALGORITHM_FORBIDDEN)
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > 256:
            raise OidcValidationError(OidcErrorCode.UNKNOWN_KEY)
        signing_key = await self._signing_key(kid, algorithm)
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=GITHUB_ACTIONS_ISSUER,
                leeway=self._leeway,
                options={
                    "require": list(_REQUIRED_CLAIMS),
                    "verify_signature": True,
                    # PyJWT always reads the system clock. Time claims are
                    # verified below with the injected clock and strict binding.
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise OidcValidationError(OidcErrorCode.TOKEN_EXPIRED) from exc
        except jwt.ImmatureSignatureError as exc:
            raise OidcValidationError(OidcErrorCode.TOKEN_NOT_ACTIVE) from exc
        except jwt.InvalidAudienceError as exc:
            raise OidcValidationError(OidcErrorCode.INVALID_AUDIENCE) from exc
        except jwt.InvalidIssuerError as exc:
            raise OidcValidationError(OidcErrorCode.INVALID_ISSUER) from exc
        except jwt.InvalidSignatureError as exc:
            raise OidcValidationError(OidcErrorCode.INVALID_SIGNATURE) from exc
        except jwt.PyJWTError as exc:
            raise OidcValidationError(OidcErrorCode.TOKEN_MALFORMED) from exc
        self._validate_time_and_audience(claims)
        # GitHub exposes this claim as the string "true", not a JSON boolean.
        # Push publication is accepted only for the protected default ref.
        if claims.get("ref_protected") != "true":
            raise OidcValidationError(
                OidcErrorCode.INVALID_CLAIM,
                claim="ref_protected",
            )
        if claims.get("event_name") != "push":
            raise OidcValidationError(
                OidcErrorCode.SOURCE_BINDING_MISMATCH,
                claim="event_name",
            )
        return GitHubActionsClaims.from_mapping(claims)

    async def validate_for_source(
        self,
        token: str,
        *,
        source: ModuleSource,
        commit_sha: str,
        ref: str,
    ) -> GitHubActionsClaims:
        """Cryptographic validation followed by exact source/workflow binding."""
        if not isinstance(commit_sha, str) or re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None:
            raise OidcValidationError(OidcErrorCode.INVALID_CLAIM, claim="sha")
        claims = await self.validate(token)
        expected = {
            "repository": source.full_name,
            "repository_id": str(source.repo_id),
            "ref": source.expected_ref,
            "sha": commit_sha,
            "event_name": "push",
            "job_workflow_ref": source.trusted_workflow_ref,
        }
        actual = {
            "repository": claims.repository,
            "repository_id": claims.repository_id,
            "ref": claims.ref,
            "sha": claims.sha,
            "event_name": claims.event_name,
            "job_workflow_ref": claims.job_workflow_ref,
        }
        for name, expected_value in expected.items():
            if actual[name] != expected_value:
                raise OidcValidationError(
                    OidcErrorCode.SOURCE_BINDING_MISMATCH,
                    claim=name,
                )
        if ref != source.expected_ref:
            raise OidcValidationError(OidcErrorCode.SOURCE_BINDING_MISMATCH, claim="ref")
        return claims

    def _validate_time_and_audience(self, claims: Mapping[str, Any]) -> None:
        audience = claims.get("aud")
        if audience != self._audience and audience != [self._audience]:
            raise OidcValidationError(OidcErrorCode.INVALID_AUDIENCE)
        numeric: dict[str, float] = {}
        for name in ("exp", "iat", "nbf"):
            value = claims.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise OidcValidationError(OidcErrorCode.INVALID_CLAIM, claim=name)
            numeric[name] = float(value)
            if not math.isfinite(numeric[name]):
                raise OidcValidationError(OidcErrorCode.INVALID_CLAIM, claim=name)
        now = float(self._clock())
        if numeric["exp"] <= now - self._leeway:
            raise OidcValidationError(OidcErrorCode.TOKEN_EXPIRED)
        if numeric["iat"] > now + self._leeway:
            raise OidcValidationError(OidcErrorCode.INVALID_CLAIM, claim="iat")
        if numeric["nbf"] > now + self._leeway:
            raise OidcValidationError(OidcErrorCode.TOKEN_NOT_ACTIVE)
        if numeric["exp"] <= numeric["iat"] or numeric["nbf"] >= numeric["exp"]:
            raise OidcValidationError(OidcErrorCode.INVALID_CLAIM, claim="exp")

    @staticmethod
    def _header(token: str) -> Mapping[str, Any]:
        if not isinstance(token, str) or not token.strip() or token != token.strip():
            raise OidcValidationError(OidcErrorCode.TOKEN_MALFORMED)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OidcValidationError(OidcErrorCode.TOKEN_MALFORMED) from exc
        if not isinstance(header, Mapping):
            raise OidcValidationError(OidcErrorCode.TOKEN_MALFORMED)
        return header

    async def _signing_key(self, kid: str, algorithm: str) -> _SigningKey:
        keys = await self._get_keys(force_refresh=False)
        entry = keys.get(kid)
        if entry is None:
            # One forced refresh supports normal JWKS key rotation; no key or
            # algorithm fallback is attempted if the new set still lacks kid.
            keys = await self._get_keys(force_refresh=True)
            entry = keys.get(kid)
        if entry is None or entry.algorithm != algorithm:
            raise OidcValidationError(OidcErrorCode.UNKNOWN_KEY)
        return entry

    async def _get_keys(self, *, force_refresh: bool) -> Mapping[str, _SigningKey]:
        now = self._monotonic()
        if (
            not force_refresh
            and self._keys
            and now - self._fetched_at < self._cache_ttl
        ):
            return self._keys
        async with self._lock:
            now = self._monotonic()
            if (
                not force_refresh
                and self._keys
                and now - self._fetched_at < self._cache_ttl
            ):
                return self._keys
            try:
                document = await self._fetcher(self._jwks_url)
                keys = self._parse_jwks(document)
            except OidcValidationError:
                raise
            except Exception as exc:
                raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE) from exc
            self._keys = keys
            self._fetched_at = self._monotonic()
            return self._keys

    def _parse_jwks(self, document: Mapping[str, object]) -> dict[str, _SigningKey]:
        if not isinstance(document, Mapping):
            raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE)
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE)
        parsed: dict[str, _SigningKey] = {}
        for raw in raw_keys:
            if not isinstance(raw, Mapping):
                raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE)
            kid = raw.get("kid")
            algorithm = raw.get("alg")
            kty = raw.get("kty")
            if (
                not isinstance(kid, str)
                or not kid
                or kid in parsed
                or kty == "oct"
                or "d" in raw
                or raw.get("use") not in (None, "sig")
                or algorithm not in self._algorithms
            ):
                raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE)
            key_ops = raw.get("key_ops")
            if key_ops is not None and (
                not isinstance(key_ops, list) or "verify" not in key_ops
            ):
                raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE)
            try:
                key = jwt.PyJWK.from_dict(dict(raw), algorithm=algorithm)
            except (jwt.PyJWTError, TypeError, ValueError) as exc:
                raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE) from exc
            parsed[kid] = _SigningKey(key=key.key, algorithm=algorithm)
        if not parsed:
            raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE)
        return parsed

    @staticmethod
    async def _http_fetch_jwks(url: str) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(5.0),
            ) as client:
                response = await client.get(
                    url,
                    headers={"Accept": "application/json"},
                )
            if response.status_code != 200 or len(response.content) > _MAX_JWKS_BYTES:
                raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE)
            document = response.json()
        except OidcValidationError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE) from exc
        if not isinstance(document, Mapping):
            raise OidcValidationError(OidcErrorCode.JWKS_UNAVAILABLE)
        return document
