"""DigestPayloadResolver integration for network; written, not executed, during freeze."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters.network import K8sEgressNetworkAdapter
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    NetworkEgressRequest,
    NetworkHttpMethod,
)

ARTIFACT_DIGEST = "sha256:" + "a" * 64
RESPONSE_DIGEST = "sha256:" + "c" * 64


def envelope(*, company_id: str = "co_123") -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req-network-payload",
        company_id=company_id,
        project_id="project_1",
        run_id="job_1",
        subject_id=f"plugin:mod_x:{company_id}:inst_abc",
        plugin_instance_id="inst_abc",
        artifact_digest=ARTIFACT_DIGEST,
        grant_id="grant_1",
    )


def request(payload: bytes, *, company_id: str = "co_123") -> NetworkEgressRequest:
    return NetworkEgressRequest(
        host="api.example.com",
        port=443,
        path="/v1/allowed",
        method=NetworkHttpMethod.POST,
        body_digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        envelope=envelope(company_id=company_id),
    )


class Resolver:
    def __init__(self, value: bytes | None) -> None:
        self.value = value
        self.calls: list[tuple[str, str, str, str]] = []

    async def resolve_body_digest(
        self, company_id: str, plugin_id: str, plugin_instance_id: str, digest: str
    ) -> bytes | None:
        self.calls.append((company_id, plugin_id, plugin_instance_id, digest))
        return self.value


class Transport:
    scoped_identity = True
    digest_bound_channel = True
    max_request_bytes = 1024

    def __init__(self) -> None:
        self.identity: dict[str, Any] | None = None
        self.body: bytes | None = None
        self.calls = 0

    async def execute(self, *, identity: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("nonempty payload must not use the empty-body execute path")

    async def execute_with_body(
        self, *, identity: dict[str, Any], body: bytes
    ) -> dict[str, Any]:
        self.calls += 1
        self.identity = dict(identity)
        self.body = body
        return {
            **identity,
            "status_code": 200,
            "bytes_sent": len(body),
            "bytes_received": 17,
            "response_digest": RESPONSE_DIGEST,
            "resolved_host": "api.example.com",
            "hops": ("api.example.com",),
            "redirect_checked": True,
            "tls_verified": True,
        }


class Policy:
    async def allows(self, **kwargs: Any) -> bool:
        return True


class Egress:
    def check(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(allowed=True)


def adapter(resolver: Resolver | None, transport: Transport) -> K8sEgressNetworkAdapter:
    return K8sEgressNetworkAdapter(
        transport=transport,
        egress=Egress(),
        initial_policy=Policy(),
        body_digest_resolver=resolver,
        max_response_bytes=1024,
    )


@pytest.mark.asyncio
async def test_nonempty_payload_success_uses_positional_scoped_resolver() -> None:
    payload = b'{"safe":true}'
    resolver = Resolver(payload)
    transport = Transport()
    result = await adapter(resolver, transport).egress(request(payload))
    assert result.bytes_sent == len(payload)
    assert transport.body == payload
    assert transport.calls == 1
    assert resolver.calls == [
        ("co_123", "mod_x", "inst_abc", "sha256:" + hashlib.sha256(payload).hexdigest())
    ]
    assert transport.identity is not None
    assert "body" not in transport.identity
    assert "body_base64" not in transport.identity


@pytest.mark.asyncio
async def test_missing_nonempty_payload_is_typed_denial_without_proxy() -> None:
    transport = Transport()
    with pytest.raises(Exception, match="network_payload_missing"):
        await adapter(Resolver(None), transport).egress(request(b"missing"))
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_wrong_payload_digest_is_typed_denial_without_proxy() -> None:
    transport = Transport()
    with pytest.raises(Exception, match="network_payload_digest_mismatch"):
        await adapter(Resolver(b"different-bytes"), transport).egress(request(b"expected"))
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_oversize_empty_and_wrong_tenant_payloads_never_reach_transport() -> None:
    transport = Transport()
    with pytest.raises(Exception, match="network_payload_too_large"):
        await adapter(Resolver(b"x" * 1025), transport).egress(request(b"x" * 1025))
    with pytest.raises(Exception, match="network_payload_invalid"):
        await adapter(Resolver(b""), transport).egress(request(b"not-empty-digest"))
    with pytest.raises(Exception, match="network_payload_missing"):
        await adapter(Resolver(None), transport).egress(
            request(b"foreign", company_id="co_other")
        )
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_missing_resolver_never_turns_nonempty_digest_into_empty_body() -> None:
    transport = Transport()
    with pytest.raises(Exception, match="network_payload_resolver_not_configured"):
        await adapter(None, transport).egress(request(b"payload"))
    assert transport.calls == 0
