"""Offline security checks for GitHub App-backed private GHCR reads."""

from __future__ import annotations

import asyncio
import base64
import hashlib

import httpx
import pytest

from prodavan.application.plugins.artifacts.publication import HttpxOciArtifactLoader
from prodavan.application.plugins.artifacts.publication import _LoaderError
from prodavan.infrastructure.plugins.artifacts import (
    GHCR_HOST,
    GhcrAuthConfigurationError,
    GhcrAuthorizerClosedError,
    GhcrRegistryAuthorizer,
    GhcrRepositoryError,
    GhcrTokenError,
    GhcrTokenProfile,
)

_ORGANIZATION = "ne-tort"
_INSTALLATION_ID = 42
_REPOSITORY = "ne-tort/plugins/hello"
_TOKEN = "ghs_registry_read_001"
_PRIVATE_KEY = "private-key-material-must-not-leak"
_PAYLOAD = "request-payload-must-not-leak"
_RESPONSE_BODY = "response-body-must-not-leak"
_NO_TOKEN_OVERRIDE = object()


class _FakeRegistryTokenProvider:
    """Emulate the provider-owned singleflight/TTL cache, not adapter state."""

    def __init__(self) -> None:
        self.now = 1_700_000_000.0
        self.calls: list[tuple[int, str]] = []
        self.mint_calls = 0
        self.close_calls = 0
        self.error: Exception | None = None
        self.token_override: object = _NO_TOKEN_OVERRIDE
        self._cached_token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def installation_token(
        self,
        installation_id: int,
        *,
        profile: GhcrTokenProfile,
    ) -> str:
        self.calls.append((installation_id, profile))
        if self.error is not None:
            raise self.error
        if self.token_override is not _NO_TOKEN_OVERRIDE:
            return self.token_override  # type: ignore[return-value]

        if self._cached_token is not None and self.now < self._expires_at - 60:
            return self._cached_token
        async with self._lock:
            if self._cached_token is not None and self.now < self._expires_at - 60:
                return self._cached_token
            # A scheduling point makes concurrent callers exercise the fake's
            # singleflight just as they exercise the real provider's shared task.
            await asyncio.sleep(0)
            self.mint_calls += 1
            self._cached_token = f"ghs_registry_read_{self.mint_calls:03d}"
            self._expires_at = self.now + 3600
            return self._cached_token

    async def aclose(self) -> None:
        self.close_calls += 1


def _client_factory(transport: httpx.MockTransport):
    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    return make


def _authorizer(provider: _FakeRegistryTokenProvider) -> GhcrRegistryAuthorizer:
    return GhcrRegistryAuthorizer(
        organization=_ORGANIZATION,
        installation_id=_INSTALLATION_ID,
        token_provider=provider,
    )


def _loader(
    provider: _FakeRegistryTokenProvider,
    handler,
) -> tuple[HttpxOciArtifactLoader, GhcrRegistryAuthorizer]:
    authorizer = _authorizer(provider)
    loader = HttpxOciArtifactLoader(
        client_factory=_client_factory(httpx.MockTransport(handler)),
        authorizer=authorizer,
    )
    return loader, authorizer


def _manifest() -> tuple[bytes, str]:
    content = b'{"schemaVersion":2}'
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    return content, f"{GHCR_HOST}/{_REPOSITORY}@{digest}"


def _basic_credentials(authorization: str) -> str:
    scheme, encoded = authorization.split(" ", 1)
    assert scheme == "Basic"
    return base64.b64decode(encoded, validate=True).decode("ascii")


def _assert_sanitized(error: BaseException) -> None:
    exposed = "\n".join(
        (str(error), repr(error), str(error.__cause__), str(error.__context__))
    )
    for forbidden in (_TOKEN, _PRIVATE_KEY, _PAYLOAD, _RESPONSE_BODY):
        assert forbidden not in exposed


@pytest.mark.asyncio
async def test_basic_header_has_fixed_username_profile_and_no_cookies() -> None:
    provider = _FakeRegistryTokenProvider()
    requests: list[httpx.Request] = []
    content, ref = _manifest()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=content)

    loader, authorizer = _loader(provider, handler)
    loaded = await loader.load_manifest(ref)

    assert loaded.data == content
    assert provider.calls == [(_INSTALLATION_ID, "registry_read")]
    assert provider.mint_calls == 1
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == GHCR_HOST
    assert request.url.username == ""
    assert request.url.password == ""
    assert _basic_credentials(request.headers["Authorization"]) == (
        f"x-access-token:{_TOKEN}"
    )
    assert "Cookie" not in request.headers
    assert "Proxy-Authorization" not in request.headers
    assert "X-GitHub-Api-Version" not in request.headers
    non_authorization_headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() != "authorization"
    }
    assert _TOKEN not in str(non_authorization_headers)
    assert _TOKEN not in str(request.url)
    assert _TOKEN not in repr(authorizer)


@pytest.mark.asyncio
async def test_refresh_and_singleflight_stay_in_provider() -> None:
    provider = _FakeRegistryTokenProvider()
    authorizer = _authorizer(provider)

    first, second = await asyncio.gather(
        authorizer.authorization_for(_REPOSITORY),
        authorizer.authorization_for(_REPOSITORY),
    )
    assert first == second
    assert _basic_credentials(first) == f"x-access-token:{_TOKEN}"
    assert len(provider.calls) == 2
    assert provider.mint_calls == 1
    assert not hasattr(authorizer, "_token_cache")
    assert not hasattr(authorizer, "_refresh_tasks")

    # The provider's one-minute refresh skew controls expiry. The adapter asks
    # it again instead of retaining or independently refreshing either token.
    provider.now += 3541
    refreshed = await authorizer.authorization_for(_REPOSITORY)
    assert provider.mint_calls == 2
    assert _basic_credentials(refreshed) == (
        "x-access-token:ghs_registry_read_002"
    )
    assert refreshed != first
    assert {call[1] for call in provider.calls} == {"registry_read"}


@pytest.mark.asyncio
async def test_missing_or_malformed_auth_never_falls_back_to_anonymous() -> None:
    with pytest.raises(GhcrAuthConfigurationError):
        GhcrRegistryAuthorizer(
            organization=_ORGANIZATION,
            installation_id=_INSTALLATION_ID,
        )

    provider = _FakeRegistryTokenProvider()
    provider.token_override = None
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"anonymous-must-not-be-used")

    loader, _ = _loader(provider, handler)
    _, ref = _manifest()
    with pytest.raises(GhcrTokenError) as caught:
        await loader.load_manifest(ref)
    assert requests == []
    assert caught.value.__context__ is None
    _assert_sanitized(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_token",
    [
        "",
        None,
        "token with whitespace",
        f"{_TOKEN}\r\nCookie: session=stolen",
        "non-ascii-secret-é",
        "x" * 8193,
    ],
)
async def test_malformed_provider_token_is_sanitized(malformed_token: object) -> None:
    provider = _FakeRegistryTokenProvider()
    provider.token_override = malformed_token
    authorizer = _authorizer(provider)

    with pytest.raises(GhcrTokenError) as caught:
        await authorizer.authorization_for(_REPOSITORY)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    _assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_provider_error_chain_cannot_leak_secrets_or_bodies() -> None:
    provider = _FakeRegistryTokenProvider()
    provider.error = RuntimeError(
        f"token={_TOKEN}; key={_PRIVATE_KEY}; payload={_PAYLOAD}; body={_RESPONSE_BODY}"
    )
    authorizer = _authorizer(provider)

    with pytest.raises(GhcrTokenError) as caught:
        await authorizer.authorization_for(_REPOSITORY)
    assert str(caught.value) == (
        "GHCR registry_read token could not be acquired safely"
    )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    _assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_401_is_sanitized_and_never_retried_anonymously() -> None:
    provider = _FakeRegistryTokenProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            401,
            content=f"{_TOKEN} {_PRIVATE_KEY} {_PAYLOAD} {_RESPONSE_BODY}".encode(),
            headers={
                "WWW-Authenticate": f"Basic realm={_TOKEN}",
                "Set-Cookie": f"session={_TOKEN}",
            },
        )

    loader, _ = _loader(provider, handler)
    _, ref = _manifest()
    with pytest.raises(_LoaderError) as caught:
        await loader.load_manifest(ref)

    assert len(requests) == 1
    assert provider.mint_calls == 1
    assert "Authorization" in requests[0].headers
    assert str(caught.value) == "OCI registry request was not accepted"
    _assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_authorization_stays_on_ghcr() -> None:
    provider = _FakeRegistryTokenProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={
                "Location": (
                    f"https://evil.example/collect?token={_TOKEN}&body={_RESPONSE_BODY}"
                )
            },
            content=_RESPONSE_BODY.encode(),
        )

    loader, _ = _loader(provider, handler)
    _, ref = _manifest()
    with pytest.raises(_LoaderError) as caught:
        await loader.load_manifest(ref)

    assert len(requests) == 1
    assert requests[0].url.host == GHCR_HOST
    assert all(request.url.host != "evil.example" for request in requests)
    assert "Authorization" in requests[0].headers
    _assert_sanitized(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref",
    [
        "evil.example/ne-tort/plugins/hello@sha256:" + "a" * 64,
        "https://ghcr.io/ne-tort/plugins/hello@sha256:" + "a" * 64,
        "http://ghcr.io/ne-tort/plugins/hello@sha256:" + "a" * 64,
    ],
)
async def test_other_host_or_explicit_url_is_rejected_before_token_use(ref: str) -> None:
    provider = _FakeRegistryTokenProvider()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"must-not-be-fetched")

    loader, _ = _loader(provider, handler)
    with pytest.raises(_LoaderError):
        await loader.load_manifest(ref)
    assert provider.calls == []
    assert requests == []


@pytest.mark.asyncio
async def test_repository_cannot_cross_organization_or_contain_a_url() -> None:
    provider = _FakeRegistryTokenProvider()
    authorizer = _authorizer(provider)

    for repository in (
        "another-org/plugins/hello",
        f"https://{GHCR_HOST}/{_REPOSITORY}",
        f"{GHCR_HOST}/{_REPOSITORY}",
        "ne-tort",
    ):
        with pytest.raises(GhcrRepositoryError):
            await authorizer.authorization_for(repository)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_close_is_idempotent_and_borrowed_provider_stays_open() -> None:
    provider = _FakeRegistryTokenProvider()
    authorizer = _authorizer(provider)
    assert _basic_credentials(await authorizer.authorization_for(_REPOSITORY)) == (
        f"x-access-token:{_TOKEN}"
    )

    await authorizer.aclose()
    await authorizer.aclose()
    with pytest.raises(GhcrAuthorizerClosedError) as caught:
        await authorizer.authorization_for(_REPOSITORY)
    with pytest.raises(GhcrAuthorizerClosedError):
        await authorizer.__aenter__()

    assert provider.close_calls == 0
    assert len(provider.calls) == 1
    _assert_sanitized(caught.value)
