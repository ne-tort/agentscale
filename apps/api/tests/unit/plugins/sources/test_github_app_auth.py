"""JWT, installation-token lifecycle and shutdown acceptance tests."""

from __future__ import annotations

import asyncio
import json

import httpx
import jwt
import pytest

from prodavan.infrastructure.plugins.sources import (
    GitHubAuthenticationError,
    GitHubClientClosed,
)

from .conftest import APP_ID, DEFAULT_REPO_PAYLOAD, INSTALLATION_ID, FakeGitHub, MutableClock


def _authorization(request: httpx.Request) -> str:
    return request.headers["Authorization"].removeprefix("Bearer ")


@pytest.mark.asyncio
async def test_app_jwt_rs256_and_installation_token_cached_and_refreshed(
    make_client,
    clock: MutableClock,
    rsa_private_key,
) -> None:
    fake = FakeGitHub(clock)
    client = make_client(fake.handler, clock=clock)

    first = await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])
    await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])
    assert first.repo_id == DEFAULT_REPO_PAYLOAD["id"]
    assert fake.token_calls == 1

    token_request = next(
        request
        for request in fake.requests
        if request.url.path == f"/app/installations/{INSTALLATION_ID}/access_tokens"
    )
    app_jwt = _authorization(token_request)
    assert json.loads(token_request.content) == {
        "permissions": {"administration": "write", "metadata": "read"}
    }
    assert jwt.get_unverified_header(app_jwt)["alg"] == "RS256"
    claims = jwt.decode(
        app_jwt,
        rsa_private_key.public_key(),
        algorithms=["RS256"],
        issuer=str(APP_ID),
        options={"require": ["iat", "exp", "iss"], "verify_exp": False},
    )
    assert claims["iat"] == int(clock.now) - 60
    assert claims["exp"] == int(clock.now) + 540
    assert claims["exp"] - int(clock.now) == 9 * 60

    metadata_requests = [
        request for request in fake.requests if request.url.path == "/repos/managed-plugins/plugin-one"
    ]
    assert len(metadata_requests) == 2
    assert _authorization(metadata_requests[0]) == fake.last_token
    assert _authorization(metadata_requests[1]) == fake.last_token

    # The cached value is proactively refreshed one minute before server TTL.
    clock.now += 3541
    await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])
    assert fake.token_calls == 2
    assert client._token_cache

    await client.aclose()
    assert client._token_cache == {}
    with pytest.raises(GitHubClientClosed):
        await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_installation_token_mint(
    make_client,
    clock: MutableClock,
) -> None:
    fake = FakeGitHub(clock)
    original_handler = fake.handler

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            await asyncio.sleep(0.02)
        return await original_handler(request)

    client = make_client(slow_handler, clock=clock)
    first, second = await asyncio.gather(
        client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"]),
        client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"]),
    )
    assert first == second
    assert fake.token_calls == 1


@pytest.mark.asyncio
async def test_invalid_private_key_is_sanitized(make_client, clock: MutableClock) -> None:
    secret_text = "-----BEGIN PRIVATE KEY-----\nnot-a-real-private-key\n-----END PRIVATE KEY-----"

    class BrokenSecretProvider:
        async def read_secret(self, reference: str) -> str:
            raise RuntimeError(f"secret backend failed while reading {secret_text}")

    client = make_client(lambda request: httpx.Response(500), clock=clock)
    client._secret_provider = BrokenSecretProvider()

    with pytest.raises(GitHubAuthenticationError) as caught:
        await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])
    assert secret_text not in str(caught.value)
    assert "private key" not in str(caught.value).lower()
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_close_cancels_in_flight_token_mint_and_rejects_new_operations(
    make_client,
    clock: MutableClock,
) -> None:
    started = asyncio.Event()

    async def blocked_handler(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    client = make_client(blocked_handler, clock=clock)
    operation = asyncio.create_task(client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"]))
    await asyncio.wait_for(started.wait(), timeout=1)

    await client.aclose()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=1)

    with pytest.raises(GitHubClientClosed):
        await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])
    assert client._token_cache == {}
    assert client._refresh_tasks == {}
    await client.aclose()
