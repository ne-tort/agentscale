"""Runtime contracts with untrusted test doubles; no live/production proof."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from .conftest import (
    CAPABILITY_HEADER,
    CAPABILITY_TOKEN,
    load_app_module,
    stub_introspection,
)

EMPTY = "sha256:" + "0" * 64
ARTIFACT = "sha256:" + "a" * 64
BACKEND = "sha256:" + "b" * 64
CONTENT = "sha256:" + "c" * 64
LOG_CONTENT = b"scoped-log-evidence"
INSTANCE = "inst_test"
HOST = "api.example.com"


class UntrustedTestProviders:
    """Explicit test doubles only; never production proof inside hello."""

    def __init__(self) -> None:
        self.egress: Any = None
        self.artifact: Any = None
        self.pod: Any = None
        self.secrets: Any = None


@dataclass
class World:
    module: Any
    runtime: Any
    client: TestClient
    production_client: TestClient
    introspection: Any
    providers: UntrustedTestProviders
    projection_root: Path


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = load_app_module(monkeypatch)
    introspection = stub_introspection(module, monkeypatch)
    (tmp_path / "plugin_instance_id").write_text(INSTANCE, encoding="ascii")
    (tmp_path / "backend_digest").write_text(BACKEND, encoding="ascii")
    monkeypatch.setattr(module, "RUNTIME_DEPLOYMENT_IDENTITY_ROOT", tmp_path)
    import runtime_api

    providers = UntrustedTestProviders()

    async def egress_declaration(identity: dict[str, Any]) -> Any:
        if providers.egress is None:
            raise RuntimeError("untrusted test declaration provider missing")
        return await providers.egress.declaration(identity=identity)

    async def artifact_metadata(identity: dict[str, Any]) -> Any:
        if providers.artifact is None:
            raise RuntimeError("untrusted test artifact provider missing")
        return await providers.artifact.byte_count(identity=identity)

    class PodPort:
        async def _call(self, name: str, identity: dict[str, Any]) -> Any:
            method = getattr(providers.pod, name, None) if providers.pod is not None else None
            if not callable(method):
                raise RuntimeError("untrusted test pod provider missing")
            return await method(identity=identity)

        async def read_status(self, *, identity: dict[str, Any]) -> Any:
            return await self._call("read_status", identity)

        async def read_logs(self, *, identity: dict[str, Any]) -> Any:
            return await self._call("read_logs", identity)

        async def send_agent_message(self, *, identity: dict[str, Any]) -> Any:
            return await self._call("send_agent_message", identity)

    test_app = FastAPI(docs_url=None, redoc_url=None)
    runtime_api.install_runtime_api(
        test_app,
        scope_provider=module._runtime_scope,
        egress_declaration_provider=egress_declaration,
        artifact_metadata_provider=artifact_metadata,
        secret_store_provider=lambda: providers.secrets,
        deployment_identity_provider=module._runtime_deployment_identity,
        pod_action_provider=PodPort(),
    )
    client = TestClient(test_app)
    production_client = TestClient(module.app)
    try:
        yield World(
            module=module,
            runtime=runtime_api,
            client=client,
            production_client=production_client,
            introspection=introspection,
            providers=providers,
            projection_root=tmp_path,
        )
    finally:
        client.close()
        production_client.close()


def headers(instance: str = INSTANCE) -> dict[str, str]:
    return {
        CAPABILITY_HEADER: CAPABILITY_TOKEN,
        "X-Plugin-Instance-ID": instance,
        "X-Plugin-ID": "hello_plugin",
        "X-Plugin-Artifact-Digest": ARTIFACT,
    }


def pod_payload(action: str = "read_status", **changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "company_id": "co_test",
        "project_id": "pr_test",
        "plugin_id": "hello_plugin",
        "plugin_instance_id": INSTANCE,
        "artifact_digest": ARTIFACT,
        "backend_digest": BACKEND,
        "grant_id": "grant:hello_plugin",
        "request_id": "req-runtime-1",
        "pod_id": INSTANCE,
        "job_id": "job_test",
        "action": action,
        "target_ref": "job_test",
        "arguments_digest": EMPTY,
        "max_result_bytes": 1024,
    }
    payload.update(changes)
    return payload


def egress_payload(body: bytes = b"", **changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "company_id": "co_test",
        "plugin_id": "hello_plugin",
        "plugin_instance_id": INSTANCE,
        "artifact_digest": ARTIFACT,
        "backend_digest": BACKEND,
        "grant_id": "grant:hello_plugin",
        "subject_id": "plugin:hello_plugin:co_test:" + INSTANCE,
        "request_id": "req-egress-1",
        "host": HOST,
        "port": 443,
        "path": "/v1/allowed",
        "method": "GET",
        "body_digest": EMPTY if not body else "sha256:" + hashlib.sha256(body).hexdigest(),
        "body_base64": base64.b64encode(body).decode("ascii"),
        "max_request_bytes": 1024,
        "max_response_bytes": 1024,
    }
    payload.update(changes)
    return payload


class EgressProvider:
    def __init__(self, declaration: Any) -> None:
        self.value = declaration
        self.identities: list[dict[str, Any]] = []

    async def declaration(self, *, identity: dict[str, Any]) -> Any:
        self.identities.append(dict(identity))
        return self.value


class ArtifactProvider:
    def __init__(self, size: int | None) -> None:
        self.size = size

    async def byte_count(self, *, identity: dict[str, Any]) -> int | None:
        assert identity["artifact_digest"] == ARTIFACT
        return self.size


class PodActions:
    def __init__(self, runtime: Any, *, found: bool = True) -> None:
        self.runtime = runtime
        self.found = found
        self.operations: list[tuple[str, dict[str, Any]]] = []

    def _record(self, operation: str, identity: dict[str, Any]) -> bool:
        self.operations.append((operation, dict(identity)))
        return self.found

    async def read_status(self, *, identity: dict[str, Any]) -> Any:
        if not self._record("read_status", identity):
            return None
        return self.runtime.StatusResult(status="ready", ready=True)

    async def read_logs(self, *, identity: dict[str, Any]) -> Any:
        if not self._record("read_logs", identity):
            return None
        return self.runtime.LogsResult(
            job_id=identity["job_id"],
            content_digest="sha256:" + hashlib.sha256(LOG_CONTENT).hexdigest(),
            byte_count=len(LOG_CONTENT),
        )

    async def send_agent_message(self, *, identity: dict[str, Any]) -> Any:
        if not self._record("send_agent_message", identity):
            return None
        return self.runtime.MessageResult(
            job_id=identity["job_id"],
            arguments_digest=identity["arguments_digest"],
            accepted=True,
        )


class Secrets:
    def __init__(self, value: str) -> None:
        self.value = value
        self.refs: list[str] = []

    def get(self, secret_ref: str) -> str:
        self.refs.append(secret_ref)
        return self.value


def install_policy(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rules: tuple[Any, ...] | None = None,
    quota: int = 4096,
    secret_refs: tuple[str, ...] = (),
    **identity_changes: Any,
) -> EgressProvider:
    identity = {
        "company_id": "co_test",
        "plugin_id": "hello_plugin",
        "plugin_instance_id": INSTANCE,
        "artifact_digest": ARTIFACT,
        "grant_id": "grant:hello_plugin",
    }
    identity.update(identity_changes)
    declaration = world.runtime.EgressGrantDeclaration(
        **identity,
        rules=rules or (world.runtime.EgressRule(HOST, "/v1/allowed", "GET"),),
        max_egress_bytes=quota,
        secret_refs=secret_refs,
    )
    provider = EgressProvider(declaration)
    monkeypatch.setattr(world.providers, "egress", provider)
    return provider


def install_https(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[Any],
    *,
    addresses: tuple[str, ...] = ("8.8.8.8",),
) -> tuple[list[str], list[dict[str, Any]]]:
    dns: list[str] = []
    requests: list[dict[str, Any]] = []

    def resolve(host: str) -> tuple[str, ...]:
        dns.append(host)
        return addresses

    def perform(**kwargs: Any) -> Any:
        requests.append(dict(kwargs))
        return outcomes.pop(0)

    monkeypatch.setattr(world.runtime, "_resolve_public_addresses", resolve)
    monkeypatch.setattr(world.runtime, "_perform_https_request", perform)
    return dns, requests


def outcome(runtime: Any, status: int, content: bytes = b"", location: str | None = None, tls: bool = True) -> Any:
    return runtime._HttpOutcome(status, location, content, tls)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_status_message_artifact_and_scoped_log_proofs(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    actions = PodActions(world.runtime)
    monkeypatch.setattr(world.providers, "pod", actions)
    status = world.client.post("/v1/status", json=pod_payload(), headers=headers())
    assert status.status_code == 200
    proof = status.json()
    assert proof["result"] == {"ready": True, "status": "ready"}
    assert proof["result_digest"] == "sha256:" + hashlib.sha256(canonical(proof["result"])).hexdigest()
    assert proof["bytes_received"] == len(canonical(proof["result"]))

    message = world.client.post(
        "/v1/messages",
        json=pod_payload("send_agent_message", arguments_digest=CONTENT),
        headers=headers(),
    )
    assert message.status_code == 200
    assert message.json()["result"] == {
        "accepted": True,
        "arguments_digest": CONTENT,
        "job_id": "job_test",
    }

    monkeypatch.setattr(world.providers, "artifact", ArtifactProvider(128))
    artifact = world.client.post(
        "/v1/artifacts",
        json=pod_payload("read_artifact", target_ref=CONTENT),
        headers=headers(),
    )
    assert artifact.status_code == 200
    assert artifact.json()["result"]["artifact_digest"] == CONTENT
    assert artifact.json()["bytes_received"] == 128

    logs = world.client.post("/v1/logs", json=pod_payload("read_logs"), headers=headers())
    assert logs.status_code == 200
    assert logs.json()["result"] == {
        "byte_count": len(LOG_CONTENT),
        "content_digest": "sha256:" + hashlib.sha256(LOG_CONTENT).hexdigest(),
        "job_id": "job_test",
    }
    assert [operation for operation, _ in actions.operations] == [
        "read_status", "send_agent_message", "read_logs"
    ]
    assert all("token" not in identity for _, identity in actions.operations)
    assert "plaintext" not in logs.text and "secret" not in logs.text
    for response in (status, message, artifact, logs):
        assert CAPABILITY_TOKEN not in response.text
    assert len(world.introspection.requests) == 4


@pytest.mark.parametrize(
    "changes",
    (
        {"company_id": "other"},
        {"project_id": "other"},
        {"plugin_id": "other"},
        {"plugin_instance_id": "other"},
        {"artifact_digest": CONTENT},
        {"backend_digest": CONTENT},
        {"grant_id": "other"},
        {"pod_id": "other"},
    ),
)
def test_exact_scope_instance_digest_and_grant_are_forbidden(world: World, changes: dict[str, Any]) -> None:
    response = world.client.post("/v1/status", json=pod_payload(**changes), headers=headers())
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("header", "value"),
    (
        ("X-Plugin-Instance-ID", "other"),
        ("X-Plugin-ID", "other"),
        ("X-Plugin-Artifact-Digest", CONTENT),
    ),
)
def test_spoofed_identity_headers_are_forbidden(
    world: World, header: str, value: str
) -> None:
    request_headers = headers()
    request_headers[header] = value
    response = world.production_client.post(
        "/v1/status", json=pod_payload(), headers=request_headers
    )
    assert response.status_code == 403


def test_missing_headers_and_forbidden_or_arbitrary_action_fail_closed(world: World) -> None:
    missing = world.client.post(
        "/v1/status",
        json=pod_payload(),
        headers={CAPABILITY_HEADER: CAPABILITY_TOKEN},
    )
    assert missing.status_code == 401
    mismatch = world.client.post("/v1/status", json=pod_payload("read_logs"), headers=headers())
    assert mismatch.status_code == 403
    arbitrary = pod_payload("read_status")
    arbitrary["action"] = "exec"
    arbitrary["command"] = "cat /secret"
    denied = world.client.post("/v1/status", json=arbitrary, headers=headers())
    assert denied.status_code == 422
    assert "/secret" not in denied.text


def test_empty_mismatched_digest_and_artifact_size_cap(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = world.client.post("/v1/messages", json=pod_payload("send_agent_message"), headers=headers())
    assert empty.status_code == 403
    mismatch = world.client.post(
        "/v1/messages",
        json=pod_payload("send_agent_message", target_ref="other", arguments_digest=CONTENT),
        headers=headers(),
    )
    assert mismatch.status_code == 403
    bad_artifact = world.client.post(
        "/v1/artifacts",
        json=pod_payload("read_artifact", target_ref="../../secret"),
        headers=headers(),
    )
    assert bad_artifact.status_code == 403
    monkeypatch.setattr(world.providers, "artifact", ArtifactProvider(1024 * 1024 + 1))
    oversized = world.client.post(
        "/v1/artifacts",
        json=pod_payload("read_artifact", target_ref=CONTENT),
        headers=headers(),
    )
    assert oversized.status_code == 413


def test_bounded_body_and_plaintext_secret_are_never_accepted(world: World) -> None:
    too_large = world.client.post(
        "/v1/egress",
        content=b" " * (2 * 1024 * 1024 + 1),
        headers={**headers(), "Content-Type": "application/json"},
    )
    assert too_large.status_code == 413
    payload = egress_payload()
    payload["authorization"] = "Bearer plaintext-secret"
    denied = world.client.post("/v1/egress", json=payload, headers=headers())
    assert denied.status_code == 422
    assert "plaintext-secret" not in denied.text
    with_token = world.client.post(
        "/v1/status",
        json=pod_payload(capability_token=CAPABILITY_TOKEN),
        headers=headers(),
    )
    assert with_token.status_code == 422
    assert CAPABILITY_TOKEN not in with_token.text

    raw = json.dumps(pod_payload(), separators=(",", ":"))
    duplicate = ('{"company_id":"other",' + raw[1:]).encode()
    ambiguous = world.client.post(
        "/v1/status",
        content=duplicate,
        headers={**headers(), "Content-Type": "application/json"},
    )
    assert ambiguous.status_code == 422


def test_egress_success_has_typed_tls_evidence_and_no_response_body(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b'{"safe":true}'
    install_policy(
        world,
        monkeypatch,
        rules=(world.runtime.EgressRule(HOST, "/v1/allowed", "POST"),),
    )
    _, requests = install_https(world, monkeypatch, [outcome(world.runtime, 200, b'{"ok":true}')])
    response = world.client.post(
        "/v1/egress",
        json=egress_payload(body, method="POST"),
        headers=headers(),
    )
    assert response.status_code == 200
    proof = response.json()
    assert proof["bytes_sent"] == len(body)
    assert proof["bytes_received"] == 11
    assert proof["response_digest"] == "sha256:" + hashlib.sha256(b'{"ok":true}').hexdigest()
    assert proof["resolved_host"] == HOST
    assert proof["redirect_checked"] is True and proof["tls_verified"] is True
    assert "body_base64" not in proof and "content" not in proof
    assert '{"ok":true}' not in response.text
    assert requests[0]["body"] == body


@pytest.mark.parametrize(
    "changes",
    (
        {"host": "metadata.internal"},
        {"host": "evil.example.com"},
        {"method": "DELETE"},
        {"path": "/v1/forbidden"},
        {"port": 80},
    ),
)
def test_forbidden_egress_host_method_path_and_port_never_resolve(
    world: World, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    install_policy(world, monkeypatch)
    dns, _ = install_https(world, monkeypatch, [])
    response = world.client.post("/v1/egress", json=egress_payload(**changes), headers=headers())
    assert response.status_code in (403, 422)
    assert dns == []


def test_grant_identity_body_digest_and_response_caps_fail_closed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_policy(world, monkeypatch, grant_id="other")
    dns, _ = install_https(world, monkeypatch, [])
    foreign = world.client.post("/v1/egress", json=egress_payload(), headers=headers())
    assert foreign.status_code == 403 and dns == []

    install_policy(world, monkeypatch)
    body = b"safe"
    empty = world.client.post(
        "/v1/egress",
        json=egress_payload(body, body_digest=EMPTY),
        headers=headers(),
    )
    assert empty.status_code == 403
    mismatch = world.client.post(
        "/v1/egress",
        json=egress_payload(body, body_digest=CONTENT),
        headers=headers(),
    )
    assert mismatch.status_code == 403
    oversize = world.client.post(
        "/v1/egress",
        json=egress_payload(body, max_request_bytes=3),
        headers=headers(),
    )
    assert oversize.status_code == 413

    install_https(world, monkeypatch, [outcome(world.runtime, 200, b"12345")])
    capped = world.client.post(
        "/v1/egress",
        json=egress_payload(max_response_bytes=4),
        headers=headers(),
    )
    assert capped.status_code == 502


def test_redirect_each_hop_and_tls_evidence(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    install_policy(world, monkeypatch, rules=(world.runtime.EgressRule(HOST, "/v1", "GET"),))
    dns, requests = install_https(
        world,
        monkeypatch,
        [
            outcome(world.runtime, 302, location="https://api.example.com/v1/next"),
            outcome(world.runtime, 200, b"ok"),
        ],
    )
    response = world.client.post("/v1/egress", json=egress_payload(), headers=headers())
    assert response.status_code == 200
    assert dns == [HOST, HOST]
    assert len(requests) == 2
    assert response.json()["hops"] == [HOST]
    assert response.json()["resolved_host"] == HOST
    assert response.json()["response_digest"] == "sha256:" + hashlib.sha256(b"ok").hexdigest()


def test_redirect_strips_auth_cross_host_and_uses_only_secret_refs(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_policy(
        world,
        monkeypatch,
        rules=(
            world.runtime.EgressRule(HOST, "/v1/allowed", "GET"),
            world.runtime.EgressRule("cdn.example.com", "/v1/next", "GET"),
        ),
        secret_refs=("vault://plugin/egress/token",),
    )
    _, requests = install_https(
        world,
        monkeypatch,
        [
            outcome(world.runtime, 302, location="https://cdn.example.com/v1/next"),
            outcome(world.runtime, 200, b"ok"),
        ],
    )
    secrets = Secrets("Bearer never-return-this")
    monkeypatch.setattr(world.providers, "secrets", secrets)
    payload = egress_payload(
        secret_refs=[{"header": "Authorization", "secret_ref": "vault://plugin/egress/token"}]
    )
    response = world.client.post("/v1/egress", json=payload, headers=headers())
    assert response.status_code == 200
    assert requests[0]["headers"]["Authorization"] == "Bearer never-return-this"
    assert "Authorization" not in requests[1]["headers"]
    assert secrets.refs == ["vault://plugin/egress/token"]
    assert "never-return-this" not in response.text


def test_http_or_private_redirect_and_missing_tls_are_denied(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    install_policy(world, monkeypatch)
    dns, requests = install_https(
        world,
        monkeypatch,
        [outcome(world.runtime, 302, location="http://169.254.169.254/latest/meta-data")],
    )
    redirect = world.client.post("/v1/egress", json=egress_payload(), headers=headers())
    assert redirect.status_code == 502
    assert dns == [HOST] and len(requests) == 1

    install_https(world, monkeypatch, [outcome(world.runtime, 200, b"ok", tls=False)])
    tls = world.client.post("/v1/egress", json=egress_payload(), headers=headers())
    assert tls.status_code == 502


@pytest.mark.parametrize(
    "address",
    ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fe80::1", "224.0.0.1", "0.0.0.0", "::ffff:127.0.0.1"),
)
def test_non_public_dns_addresses_are_blocked(world: World, address: str) -> None:
    with pytest.raises(HTTPException):
        world.runtime._public_address(address)


def test_unlisted_secret_ref_is_denied_before_store_or_dns(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_policy(world, monkeypatch)
    secrets = Secrets("Bearer never-read-this")
    monkeypatch.setattr(world.providers, "secrets", secrets)
    dns, _ = install_https(world, monkeypatch, [])
    payload = egress_payload(
        secret_refs=[{"header": "Authorization", "secret_ref": "vault://other/tenant/token"}]
    )
    response = world.client.post("/v1/egress", json=payload, headers=headers())
    assert response.status_code == 403
    assert secrets.refs == [] and dns == []
    assert "never-read-this" not in response.text


def test_https_boundary_preserves_host_sni_and_enforces_read_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = world.runtime._tls_context()
    assert context.check_hostname is True
    assert context.verify_mode == world.runtime.ssl.CERT_REQUIRED
    connections: list[Any] = []
    responses: list[Any] = []

    class Response:
        status = 200
        length = None

        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        def read(self, amount: int) -> bytes:
            assert amount > 0
            return self.chunks.pop(0) if self.chunks else b""

        def getheader(self, name: str) -> None:
            assert name == "Location"
            return None

    class Connection:
        runtime_tls_verified = True

        def __init__(self, host: str, addresses: tuple[str, ...], **kwargs: Any) -> None:
            self.host = host
            self.addresses = addresses
            self.kwargs = kwargs
            self.response = responses.pop(0)
            connections.append(self)

        def request(self, method: str, target: str, *, body: bytes, headers: dict[str, str]) -> None:
            assert method == "GET" and target == "/v1/allowed"
            assert body == b"" and headers["Accept-Encoding"] == "identity"

        def getresponse(self) -> Response:
            return self.response

        def close(self) -> None:
            return None

    responses.append(Response([b"ok"]))
    monkeypatch.setattr(world.runtime, "_PinnedHTTPSConnection", Connection)
    evidence = world.runtime._perform_https_request(
        host=HOST,
        addresses=("8.8.8.8",),
        target="/v1/allowed",
        method="GET",
        body=b"",
        headers={},
        response_cap=2,
        timeout=1.0,
    )
    assert evidence.tls_verified is True and evidence.content == b"ok"
    assert connections[0].host == HOST and connections[0].addresses == ("8.8.8.8",)

    responses.append(Response([b"too large"]))
    with pytest.raises(HTTPException, match="byte limit"):
        world.runtime._perform_https_request(
            host=HOST,
            addresses=("8.8.8.8",),
            target="/v1/allowed",
            method="GET",
            body=b"",
            headers={},
            response_cap=2,
            timeout=1.0,
        )


@pytest.mark.parametrize(
    "missing",
    (
        CAPABILITY_HEADER,
        "X-Plugin-Instance-ID",
        "X-Plugin-ID",
        "X-Plugin-Artifact-Digest",
    ),
)
def test_every_runtime_auth_header_is_required(
    world: World, missing: str
) -> None:
    request_headers = headers()
    request_headers.pop(missing)
    response = world.client.post(
        "/v1/status", json=pod_payload(), headers=request_headers
    )
    assert response.status_code == 401
    assert world.introspection.requests == []


@pytest.mark.parametrize(
    ("field", "value"),
    (("plugin_instance_id", "other"), ("backend_digest", CONTENT)),
)
def test_actual_deployment_identity_mismatch_is_forbidden(
    world: World,
    field: str,
    value: str,
) -> None:
    (world.projection_root / field).write_text(value, encoding="ascii")
    response = world.production_client.post(
        "/v1/status", json=pod_payload(), headers=headers()
    )
    assert response.status_code == 403


def test_missing_or_invalid_deployment_projection_fails_closed(
    world: World,
) -> None:
    (world.projection_root / "plugin_instance_id").unlink()
    missing = world.production_client.post(
        "/v1/status", json=pod_payload(), headers=headers()
    )
    assert missing.status_code == 503
    (world.projection_root / "plugin_instance_id").write_text(INSTANCE, encoding="ascii")
    (world.projection_root / "backend_digest").write_text("not-a-digest", encoding="ascii")
    invalid = world.production_client.post(
        "/v1/status", json=pod_payload(), headers=headers()
    )
    assert invalid.status_code == 503


def test_backend_applies_configured_result_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        world.providers, "pod", PodActions(world.runtime)
    )
    capped = world.client.post(
        "/v1/status", json=pod_payload(max_result_bytes=1), headers=headers()
    )
    assert capped.status_code == 413
    monkeypatch.setattr(world.providers, "artifact", ArtifactProvider(128))
    artifact = world.client.post(
        "/v1/artifacts",
        json=pod_payload("read_artifact", target_ref=CONTENT, max_result_bytes=1),
        headers=headers(),
    )
    assert artifact.status_code == 413


def test_missing_pod_provider_is_503_and_missing_scoped_result_is_404(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable = world.production_client.post(
        "/v1/status", json=pod_payload(), headers=headers()
    )
    assert unavailable.status_code == 503
    monkeypatch.setattr(
        world.providers, "pod", PodActions(world.runtime, found=False)
    )
    missing = world.client.post("/v1/status", json=pod_payload(), headers=headers())
    assert missing.status_code == 404


def test_generic_proxy_and_exec_routes_do_not_exist(world: World) -> None:
    for path in ("/v1/proxy", "/v1/exec", "/v1/status/execute"):
        response = world.production_client.post(
            path, json=pod_payload(), headers=headers()
        )
        assert response.status_code == 404
    assert world.introspection.requests == []


def test_production_runtime_without_platform_scoped_api_is_503(world: World) -> None:
    cases = (
        ("/v1/status", pod_payload()),
        ("/v1/logs", pod_payload("read_logs")),
        (
            "/v1/messages",
            pod_payload("send_agent_message", arguments_digest=CONTENT),
        ),
        ("/v1/artifacts", pod_payload("read_artifact", target_ref=CONTENT)),
        ("/v1/egress", egress_payload()),
    )
    for path, payload in cases:
        response = world.production_client.post(path, json=payload, headers=headers())
        assert response.status_code == 503
