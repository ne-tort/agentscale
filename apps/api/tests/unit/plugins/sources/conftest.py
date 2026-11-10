"""Deterministic fake HTTP fixtures; no test in this package uses the network."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from prodavan.infrastructure.plugins.sources import (
    GitHubAppClient,
    ManagedOrganization,
    RepositoryTemplate,
)

APP_ID = 987654321
INSTALLATION_ID = 42
ORGANIZATION = "managed-plugins"
TEMPLATE = RepositoryTemplate(
    owner=ORGANIZATION,
    name="private-plugin-template",
    agents_revision="a" * 40,
    security_revision="b" * 40,
)
DEFAULT_REPO_PAYLOAD = {
    "id": 7654321,
    "full_name": f"{ORGANIZATION}/plugin-one",
    "default_branch": "main",
    "private": True,
}


class MutableClock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


class FakeGitHub:
    """Route the four implemented operations and retain request evidence."""

    def __init__(
        self,
        clock: MutableClock,
        *,
        template_payload: dict[str, Any] | None = None,
        repository_payload: dict[str, Any] | None = None,
    ) -> None:
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.tokens: list[str] = []
        self.token_calls = 0
        self.token_permission_requests: list[dict[str, str]] = []
        self.template_checked = False
        self.generate_payload: dict[str, Any] | None = None
        self.protection_path: str | None = None
        self.protection_raw_path: bytes | None = None
        self.protection_payload: dict[str, Any] | None = None
        self._template_payload = template_payload or {
            "id": 123,
            "full_name": f"{TEMPLATE.owner}/{TEMPLATE.name}",
            "private": True,
            "is_template": True,
        }
        self._repository_payload = repository_payload or dict(DEFAULT_REPO_PAYLOAD)

    @property
    def last_token(self) -> str:
        return self.tokens[-1]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        method = request.method

        if method == "POST" and path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
            self.token_calls += 1
            token_payload = json.loads(request.content)
            permissions = token_payload["permissions"]
            self.token_permission_requests.append(permissions)
            token = f"ghs_test_token_{self.token_calls:02d}"
            self.tokens.append(token)
            expires_at = datetime.fromtimestamp(self.clock.now + 3600, tz=UTC)
            return httpx.Response(
                201,
                json={
                    "token": token,
                    "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                    "permissions": permissions,
                },
            )

        if method == "GET" and path == f"/repos/{TEMPLATE.owner}/{TEMPLATE.name}":
            self.template_checked = True
            return httpx.Response(200, json=self._template_payload)

        if (
            method == "POST"
            and path == f"/repos/{TEMPLATE.owner}/{TEMPLATE.name}/generate"
        ):
            self.generate_payload = json.loads(request.content)
            return httpx.Response(201, json=self._repository_payload)

        if method == "GET" and path == f"/repos/{DEFAULT_REPO_PAYLOAD['full_name']}":
            return httpx.Response(200, json=self._repository_payload)

        if method == "PUT" and path.startswith(
            f"/repos/{DEFAULT_REPO_PAYLOAD['full_name']}/branches/"
        ) and path.endswith("/protection"):
            self.protection_path = path
            self.protection_raw_path = request.url.raw_path
            self.protection_payload = json.loads(request.content)
            return httpx.Response(200, json={"ok": True})

        return httpx.Response(404, json={"message": "unrouted fake GitHub request"})


@pytest.fixture(scope="session")
def rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def private_key_pem(rsa_private_key: rsa.RSAPrivateKey) -> bytes:
    return rsa_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def managed_organization() -> ManagedOrganization:
    return ManagedOrganization(
        name=ORGANIZATION,
        installation_id=INSTALLATION_ID,
        template=TEMPLATE,
    )


@pytest_asyncio.fixture
async def make_client(private_key_pem: bytes, managed_organization: ManagedOrganization):
    """Create a client with MockTransport and always close it at teardown."""

    created: list[GitHubAppClient] = []

    def factory(
        handler: Any,
        *,
        clock: MutableClock | None = None,
        organizations: tuple[ManagedOrganization, ...] | None = None,
        timeout_seconds: float = 10.0,
        platform_owner_allow_workflow_changes: bool = False,
    ) -> GitHubAppClient:
        mutable_clock = clock or MutableClock()
        client = GitHubAppClient(
            app_id=APP_ID,
            private_key_secret_ref="secret://agentscale/github-app-private-key",
            secret_provider=_StaticSecretProvider(private_key_pem),
            organizations=organizations or (managed_organization,),
            transport=httpx.MockTransport(handler),
            clock=mutable_clock,
            timeout_seconds=timeout_seconds,
            platform_owner_allow_workflow_changes=platform_owner_allow_workflow_changes,
        )
        created.append(client)
        return client

    yield factory

    for client in created:
        await client.aclose()


class _StaticSecretProvider:
    def __init__(self, value: bytes) -> None:
        self._value = value
        self.read_references: list[str] = []

    async def read_secret(self, reference: str) -> bytes:
        self.read_references.append(reference)
        return self._value
