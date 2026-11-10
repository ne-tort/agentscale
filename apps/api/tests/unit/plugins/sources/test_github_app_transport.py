"""HTTP transport hardening, response bounds and sanitized error tests."""

from __future__ import annotations

import logging

import httpx
import pytest

from prodavan.infrastructure.plugins.sources import (
    CreatePrivateRepositoryRequest,
    GitHubAPIError,
    GitHubResponseError,
    GitHubResponseTooLarge,
    GitHubTransportError,
)
from prodavan.infrastructure.plugins.sources.github_app import MAX_RESPONSE_BYTES

from .conftest import DEFAULT_REPO_PAYLOAD, ORGANIZATION, FakeGitHub, MutableClock


@pytest.mark.asyncio
async def test_http_client_has_no_proxies_redirects_and_has_explicit_timeouts(
    make_client,
    clock: MutableClock,
) -> None:
    client = make_client(lambda request: httpx.Response(404), clock=clock)
    http_client = client._http
    assert http_client.trust_env is False
    assert http_client.follow_redirects is False
    assert http_client.timeout.connect == 5.0
    assert http_client.timeout.read == 10.0
    assert http_client.timeout.write == 10.0
    assert http_client.timeout.pool == 5.0


@pytest.mark.asyncio
async def test_redirect_is_not_followed(make_client, clock: MutableClock) -> None:
    fake = FakeGitHub(clock)
    original_handler = fake.handler
    observed_requests: list[httpx.Request] = []

    async def redirect_handler(request: httpx.Request) -> httpx.Response:
        observed_requests.append(request)
        if request.url.path == f"/repos/{DEFAULT_REPO_PAYLOAD['full_name']}" and request.method == "GET":
            return httpx.Response(302, headers={"Location": "https://attacker.invalid/repo"})
        return await original_handler(request)

    client = make_client(redirect_handler, clock=clock)
    with pytest.raises(GitHubAPIError) as caught:
        await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])
    assert caught.value.status_code == 302
    assert [
        (request.method, request.url.host, request.url.path)
        for request in observed_requests
    ] == [
        ("POST", "api.github.com", "/app/installations/42/access_tokens"),
        ("GET", "api.github.com", f"/repos/{DEFAULT_REPO_PAYLOAD['full_name']}"),
    ]


@pytest.mark.asyncio
async def test_api_error_is_sanitized_and_never_logs_token_private_key_or_payload(
    make_client,
    clock: MutableClock,
    caplog: pytest.LogCaptureFixture,
    private_key_pem: bytes,
) -> None:
    secret_token = "ghs_test_token_01"
    payload_marker = "payload-secret-marker"
    private_key_marker = private_key_pem.decode()
    fake = FakeGitHub(clock)
    original_handler = fake.handler

    async def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generate"):
            return httpx.Response(
                422,
                json={
                    "message": f"invalid repository {payload_marker}",
                    "leaked_request_token": secret_token,
                    "private_key": private_key_marker,
                    "documentation_url": "https://docs.github.com/invalid",
                },
            )
        return await original_handler(request)

    client = make_client(failing_handler, clock=clock)
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        with pytest.raises(GitHubAPIError) as caught:
            await client.create_private_repository(
                CreatePrivateRepositoryRequest(
                    organization=ORGANIZATION,
                    name="plugin-one",
                    description=payload_marker,
                )
            )

    error_text = f"{caught.value} / {repr(caught.value)}"
    assert caught.value.status_code == 422
    assert "GitHub operation 'create_private_repository' failed with HTTP 422" in error_text
    for leaked in (secret_token, payload_marker, private_key_marker, "documentation_url"):
        assert leaked not in error_text
        assert leaked not in caplog.text


@pytest.mark.asyncio
async def test_transport_failure_does_not_leak_underlying_message(
    make_client,
    clock: MutableClock,
) -> None:
    underlying = "connection failed; token ghs_test_token_01 leaked"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(underlying, request=request)

    client = make_client(handler, clock=clock)
    with pytest.raises(GitHubTransportError) as caught:
        await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])
    assert underlying not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_factory",
    [
        lambda: httpx.Response(201, content=b"x" * (MAX_RESPONSE_BYTES + 1)),
        lambda: httpx.Response(
            201,
            json={"tiny": True},
            headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)},
        ),
    ],
)
async def test_generated_repository_response_is_bounded(
    make_client,
    clock: MutableClock,
    response_factory,
) -> None:
    fake = FakeGitHub(clock)
    original_handler = fake.handler

    async def oversized_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generate"):
            return response_factory()
        return await original_handler(request)

    client = make_client(oversized_handler, clock=clock)
    with pytest.raises(GitHubResponseTooLarge) as caught:
        await client.create_private_repository(
            CreatePrivateRepositoryRequest(organization=ORGANIZATION, name="plugin-one")
        )
    assert "not json" not in str(caught.value)
    assert "ghs_test_token_01" not in str(caught.value)
    assert MAX_RESPONSE_BYTES == 1_048_576


@pytest.mark.asyncio
async def test_malformed_success_response_is_sanitized(
    make_client,
    clock: MutableClock,
) -> None:
    fake = FakeGitHub(clock)
    original_handler = fake.handler

    async def malformed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generate"):
            return httpx.Response(201, content=b"<html>not json ghs_test_token_01</html>")
        return await original_handler(request)

    client = make_client(malformed_handler, clock=clock)
    with pytest.raises(GitHubResponseError) as caught:
        await client.create_private_repository(
            CreatePrivateRepositoryRequest(organization=ORGANIZATION, name="plugin-one")
        )
    assert "not json" not in str(caught.value)
    assert "ghs_test_token_01" not in str(caught.value)
