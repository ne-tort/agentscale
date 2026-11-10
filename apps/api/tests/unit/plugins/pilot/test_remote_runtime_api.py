"""Hello uses only the protected typed provider client; external IO is faked."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from .test_runtime_api import (
    ARTIFACT,
    CAPABILITY_TOKEN,
    egress_payload,
    headers,
    pod_payload,
    world as _world_fixture,
)
from .conftest import fake_introspection

world = _world_fixture

BASE_URL = "https://plugin-gateway.internal"
DECLARATION_PATH = "/capabilities/v1/runtime/declarations/egress"
ARTIFACT_PATH = "/capabilities/v1/runtime/artifacts/metadata"
STATUS_PATH = "/capabilities/v1/runtime/pods/status"
CONTENT = b"scoped-provider-ok"


class StreamResponse:
    def __init__(self, *, status_code: int, payload: dict[str, Any], headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.payload = payload
        self.headers = headers

    async def aiter_bytes(self):
        import json

        yield json.dumps(self.payload, separators=(",", ":")).encode("utf-8")


class StreamContext:
    def __init__(self, response: StreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> StreamResponse:
        return self.response

    async def __aexit__(self, *args: Any) -> bool:
        return False


class RemoteClient:
    def __init__(self, owner: "RemoteStub", **options: Any) -> None:
        self.owner = owner
        self.options = dict(options)

    async def __aenter__(self) -> "RemoteClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def get(self, url: str, *, headers: dict[str, str]) -> SimpleNamespace:
        self.owner.requests.append({
            "method": "GET",
            "url": url,
            "headers": dict(headers),
            "body": b"",
        })
        if not url.endswith("/capabilities/v1/health"):
            return SimpleNamespace(status_code=404, json=lambda: {})
        payload = self.owner.introspection
        return SimpleNamespace(status_code=200, json=lambda: payload)

    def stream(self, method: str, url: str, *, content: bytes, headers: dict[str, str]):
        self.owner.requests.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": bytes(content),
        })
        outcome = self.owner.outcomes.get(url)
        if outcome is None:
            response = StreamResponse(status_code=503, payload={}, headers={"Content-Type": "application/json"})
        elif isinstance(outcome, Exception):
            raise outcome
        else:
            response = outcome
        return StreamContext(response)


class RemoteStub:
    def __init__(self, introspection: dict[str, Any]) -> None:
        self.introspection = introspection
        self.options: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.outcomes: dict[str, Any] = {}

    @property
    def provider_requests(self) -> list[dict[str, Any]]:
        return [item for item in self.requests if item["method"] == "POST"]

    def install(self, runtime: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        def factory(**options: Any) -> RemoteClient:
            self.options.append(dict(options))
            return RemoteClient(self, **options)

        monkeypatch.setattr(runtime.httpx, "AsyncClient", factory)

    @staticmethod
    def response(
        *,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> StreamResponse:
        return StreamResponse(
            status_code=status_code,
            payload=payload or {},
            headers=headers or {"Content-Type": "application/json"},
        )


def declaration_response(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": body["company_id"],
        "plugin_id": body["plugin_id"],
        "plugin_instance_id": body["plugin_instance_id"],
        "artifact_digest": body["artifact_digest"],
        "backend_digest": body["backend_digest"],
        "grant_id": body["grant_id"],
        "request_id": body["request_id"],
        "rules": [{"host": "api.example.com", "path_prefix": "/v1/allowed", "method": "GET"}],
        "max_egress_bytes": 4096,
        "secret_refs": [],
    }


def artifact_response(body: dict[str, Any], byte_count: int = len(CONTENT)) -> dict[str, Any]:
    return {
        **body,
        "project_id": "pr_test",
        "pod_id": body["plugin_instance_id"],
        "action": "read_artifact",
        "byte_count": byte_count,
    }


@pytest.fixture
def remote(world: Any, monkeypatch: pytest.MonkeyPatch) -> RemoteStub:
    import runtime_api

    scope = fake_introspection()
    stub = RemoteStub(scope)
    stub.install(runtime_api, monkeypatch)
    monkeypatch.setattr(
        runtime_api,
        "_resolve_public_addresses",
        lambda host: ("93.184.216.34",),
    )

    def https(**kwargs: Any):
        del kwargs
        return runtime_api._HttpOutcome(
            status_code=200,
            location=None,
            content=CONTENT,
            tls_verified=True,
        )

    monkeypatch.setattr(runtime_api, "_perform_https_request", https)
    return stub


def test_egress_declaration_is_remote_typed_and_bound(world: Any, remote: RemoteStub) -> None:
    payload = egress_payload()
    identity = {
        name: payload[name]
        for name in (
            "company_id", "plugin_id", "plugin_instance_id", "artifact_digest",
            "backend_digest", "grant_id", "request_id",
        )
    }
    remote.outcomes[BASE_URL + DECLARATION_PATH] = RemoteStub.response(
        payload=declaration_response(identity)
    )
    response = world.production_client.post("/v1/egress", json=payload, headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["bytes_received"] == len(CONTENT)
    assert body["status_code"] == 200
    assert body["max_response_bytes"] <= 4096
    assert remote.provider_requests[0]["url"] == BASE_URL + DECLARATION_PATH
    request_body = remote.provider_requests[0]["body"].decode("utf-8")
    for forbidden in ("company_id", "plugin_id", "grant_id", "artifact_digest"):
        assert forbidden not in request_body
    assert remote.provider_requests[0]["headers"]["Authorization"] == "Bearer " + CAPABILITY_TOKEN
    assert all(
        item["url"] != BASE_URL + "/v1/egress" for item in remote.requests
    )


def test_artifact_metadata_comes_from_remote_provider(world: Any, remote: RemoteStub) -> None:
    payload = pod_payload("read_artifact", target_ref=ARTIFACT)
    identity = {
        name: payload[name]
        for name in (
            "company_id", "plugin_id", "plugin_instance_id", "artifact_digest",
            "backend_digest", "grant_id", "request_id", "project_id", "pod_id",
            "job_id", "target_ref", "arguments_digest", "max_result_bytes",
        )
    }
    remote.outcomes[BASE_URL + ARTIFACT_PATH] = RemoteStub.response(
        payload=artifact_response(identity)
    )
    response = world.production_client.post(
        "/v1/artifacts", json=payload, headers=headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["artifact_digest"] == ARTIFACT
    assert body["result"]["byte_count"] == len(CONTENT)
    assert body["bytes_received"] == len(CONTENT)
    assert remote.provider_requests[0]["url"] == BASE_URL + ARTIFACT_PATH
    assert b"company_id" not in remote.provider_requests[0]["body"]


@pytest.mark.parametrize(
    ("status", "expected"),
    ((403, 403), (404, 404), (413, 413), (503, 503)),
)
def test_remote_failures_keep_typed_status(
    world: Any,
    remote: RemoteStub,
    status: int,
    expected: int,
) -> None:
    payload = pod_payload("read_artifact", target_ref=ARTIFACT)
    remote.outcomes[BASE_URL + ARTIFACT_PATH] = RemoteStub.response(status_code=status)
    response = world.production_client.post(
        "/v1/artifacts", json=payload, headers=headers()
    )
    assert response.status_code == expected


def test_unwired_pod_actions_return_503_without_hello_recursion(
    world: Any,
    remote: RemoteStub,
) -> None:
    remote.outcomes[BASE_URL + STATUS_PATH] = RemoteStub.response(status_code=503)
    response = world.production_client.post("/v1/status", json=pod_payload(), headers=headers())
    assert response.status_code == 503
    assert len(remote.provider_requests) == 1
    assert remote.provider_requests[0]["url"] == BASE_URL + STATUS_PATH
    assert not any(item["url"].endswith(("/v1/status", "/v1/proxy", "/v1/exec")) for item in remote.requests)


def test_compromised_remote_secret_refs_cannot_enable_local_plaintext(
    world: Any,
    remote: RemoteStub,
) -> None:
    payload = egress_payload()
    identity = {
        name: payload[name]
        for name in (
            "company_id", "plugin_id", "plugin_instance_id", "artifact_digest",
            "backend_digest", "grant_id", "request_id",
        )
    }
    declaration = declaration_response(identity)
    declaration["secret_refs"] = ["vault://ai_keys/plugins/test"]
    remote.outcomes[BASE_URL + DECLARATION_PATH] = RemoteStub.response(payload=declaration)
    response = world.production_client.post("/v1/egress", json=payload, headers=headers())
    assert response.status_code == 503
    assert response.json()["detail"] == "remote_provider_secret_refs_forbidden"
    assert "vault://ai_keys/plugins/test" not in response.text
    assert CONTENT.decode("ascii") not in response.text
    assert len(remote.provider_requests) == 1


def test_remote_schema_and_redirects_fail_closed(world: Any, remote: RemoteStub) -> None:
    payload = pod_payload("read_artifact", target_ref=ARTIFACT)
    identity = {
        name: payload[name]
        for name in (
            "company_id", "plugin_id", "plugin_instance_id", "artifact_digest",
            "backend_digest", "grant_id", "request_id", "project_id", "pod_id",
            "job_id", "target_ref", "arguments_digest", "max_result_bytes",
        )
    }
    bad_schema = artifact_response(identity)
    bad_schema["unexpected"] = "field"
    remote.outcomes[BASE_URL + ARTIFACT_PATH] = RemoteStub.response(payload=bad_schema)
    assert world.production_client.post(
        "/v1/artifacts", json=payload, headers=headers()
    ).status_code == 503

    redirect = RemoteStub.response(status_code=307)
    remote.outcomes[BASE_URL + ARTIFACT_PATH] = redirect
    assert world.production_client.post(
        "/v1/artifacts", json=payload, headers=headers()
    ).status_code == 503


def test_remote_client_transport_options_are_strict(world: Any, remote: RemoteStub) -> None:
    payload = pod_payload("read_artifact", target_ref=ARTIFACT)
    identity = {
        name: payload[name]
        for name in (
            "company_id", "plugin_id", "plugin_instance_id", "artifact_digest",
            "backend_digest", "grant_id", "request_id", "project_id", "pod_id",
            "job_id", "target_ref", "arguments_digest", "max_result_bytes",
        )
    }
    remote.outcomes[BASE_URL + ARTIFACT_PATH] = RemoteStub.response(
        payload=artifact_response(identity)
    )
    assert world.production_client.post(
        "/v1/artifacts", json=payload, headers=headers()
    ).status_code == 200
    assert remote.options
    assert all(
        option["trust_env"] is False and option["follow_redirects"] is False
        for option in remote.options
    )
    assert all(
        item["url"].startswith(BASE_URL + "/capabilities/v1/runtime/")
        for item in remote.provider_requests
    )
