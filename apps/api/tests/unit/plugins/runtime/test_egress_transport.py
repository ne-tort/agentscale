"""Scoped canonical Service egress tests; intentionally not run during freeze."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressMode,
    EgressPolicy,
    GrantStatus,
    QuotaSet,
)
from prodavan.infrastructure.plugins.runtime.deployment_admin import BACKEND_ANNOTATION
from prodavan.infrastructure.plugins.runtime.egress_transport import (
    DurableGrantEgressPolicy,
    K8sEgressTransport,
)
from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError
from prodavan.infrastructure.plugins.runtime.k8s_scale import K8sApiClient
from prodavan.infrastructure.plugins.runtime.pod_executor import (
    CanonicalBackendResolver,
)
from tests.unit.plugins.runtime.canonical_backend_fixtures import (
    ARTIFACT_DIGEST,
    BACKEND_DIGEST,
    CONTENT_DIGEST,
    COMPANY,
    EMPTY_DIGEST,
    INSTANCE,
    PLUGIN,
    FakeActivator,
    FakeArtifacts,
    FakeInstances,
    FakeK8sApi,
    FakeTokenProvider,
    resource_name,
    runtime_config,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
HOST = "api.example.com"
PATH = "/v1/allowed"
BODY = b'{"safe":true}'


def grant_record(**changes: Any) -> CapabilityGrantRecord:
    values = {
        "grant_id": "grant_1",
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "plugin_instance_id": INSTANCE,
        "manifest_version": "1.2.3",
        "artifact_digest": ARTIFACT_DIGEST,
        "capabilities": ("network",),
        "egress_policy": EgressPolicy(
            mode=EgressMode.WEBHOOKS_ONLY,
            allowed_hosts=(HOST,),
            allowed_paths=(PATH,),
        ),
        "quotas": QuotaSet(max_egress_bytes=4096),
        "status": GrantStatus.ACTIVE,
        "created_at": NOW,
    }
    values.update(changes)
    return CapabilityGrantRecord(**values)


class GrantStore:
    def __init__(self, record: CapabilityGrantRecord) -> None:
        self.record = record

    async def get_by_grant_id(self, *, grant_id: str) -> CapabilityGrantRecord | None:
        return self.record if self.record.grant_id == grant_id else None


def identity(body: bytes, *, host: str = HOST, path: str = PATH, method: str = "POST") -> dict[str, Any]:
    return {
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "plugin_instance_id": INSTANCE,
        "artifact_digest": ARTIFACT_DIGEST,
        "grant_id": "grant_1",
        "subject_id": f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
        "request_id": "req-egress-1",
        "run_id": "job_1",
        "host": host,
        "port": 443,
        "path": path,
        "method": method,
        "body_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        "max_request_bytes": 1024,
        "max_response_bytes": 1024,
    }


def world(
    *,
    record: CapabilityGrantRecord | None = None,
    request_limit: int = 1024,
    activator: FakeActivator | None = None,
) -> tuple[K8sEgressTransport, FakeK8sApi, DurableGrantEgressPolicy]:
    tokens = FakeTokenProvider()
    api = FakeK8sApi(tokens)
    store = GrantStore(record or grant_record())
    policy = DurableGrantEgressPolicy(store)
    resolver = CanonicalBackendResolver(
        config=runtime_config(),
        instance_store=FakeInstances(),
        artifact_store=FakeArtifacts(),
        api=api,
        token_provider=tokens,
    )
    transport = K8sEgressTransport(
        resolver=resolver,
        declarations=policy,
        activator=activator or FakeActivator(),
        max_request_bytes=request_limit,
        max_response_bytes=1024,
    )
    return transport, api, policy


def typed_response(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
    del method
    assert path.endswith("/v1/egress")
    assert body is not None
    sent = base64.b64decode(body["body_base64"], validate=True)
    return {
        **body,
        "status_code": 200,
        "bytes_sent": len(sent),
        "bytes_received": 17,
        "response_digest": "sha256:" + "c" * 64,
        "resolved_host": HOST,
        "hops": (HOST,),
        "redirect_checked": True,
        "tls_verified": True,
    }


@pytest.mark.asyncio
async def test_nonempty_body_success_uses_canonical_service_and_declaration() -> None:
    transport, api, policy = world()
    api.proxy_handler = typed_response
    result = await transport.execute_with_body(identity=identity(BODY), body=BODY)
    assert result["backend_digest"] == BACKEND_DIGEST
    assert result["bytes_sent"] == len(BODY)
    assert result["bytes_received"] == 17
    assert api.proxy_calls == [(
        "POST",
        f"/api/v1/namespaces/plugins/services/{resource_name()}:8080/proxy/v1/egress",
        api.proxy_calls[0][2],
    )]
    payload = api.proxy_calls[0][2]
    assert payload is not None
    assert HOST not in api.proxy_calls[0][1]
    assert base64.b64decode(payload["body_base64"], validate=True) == BODY
    assert "body" not in payload and "raw_payload" not in payload
    assert "body_base64" not in result
    assert await policy.allows(grant_id="grant_1", host=HOST, port=443, path=PATH) is True


@pytest.mark.asyncio
async def test_empty_digest_is_sent_exactly_once_without_payload_substitution() -> None:
    transport, api, _ = world()
    request = identity(b"")
    request["body_digest"] = EMPTY_DIGEST
    api.proxy_handler = typed_response
    result = await transport.execute(identity=request)
    assert result["bytes_sent"] == 0
    assert api.proxy_calls[0][2]["body_base64"] == ""
    assert result["body_digest"] == EMPTY_DIGEST


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"path": "/v1/forbidden"}, {"method": "OPTIONS"}])
async def test_undeclared_path_method_and_host_never_reach_proxy(changes: dict[str, Any]) -> None:
    transport, api, _ = world()
    with pytest.raises(Exception, match="egress_"):
        await transport.execute_with_body(identity=identity(BODY, **changes), body=BODY)
    assert api.proxy_calls == []
    with pytest.raises(Exception, match="egress_route_not_declared"):
        await transport.execute_with_body(
            identity=identity(BODY, host="metadata.internal"), body=BODY
        )
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_wrong_or_oversize_payload_is_denied_before_canonical_proxy() -> None:
    transport, api, _ = world()
    wrong_request = identity(BODY)
    with pytest.raises(Exception, match="egress_body_digest_binding_mismatch"):
        await transport.execute_with_body(
            identity=wrong_request, body=b"different-payload"
        )
    with pytest.raises(Exception, match="egress_body_digest_binding_mismatch"):
        await transport.execute(identity=identity(BODY))
    small, small_api, _ = world(request_limit=4)
    with pytest.raises(Exception, match="egress_body_byte_limit_exceeded"):
        await small.execute_with_body(identity=identity(b"12345"), body=b"12345")
    assert api.proxy_calls == []
    assert small_api.proxy_calls == []


@pytest.mark.asyncio
async def test_quota_bound_and_body_binding_reported_by_backend_are_enforced() -> None:
    record = replace(grant_record(), quotas=QuotaSet(max_egress_bytes=8))
    transport, api, _ = world(record=record)
    api.proxy_handler = typed_response
    small_body = b"1234"
    with pytest.raises(Exception, match="egress_backend_quota_bounds_invalid"):
        await transport.execute_with_body(
            identity=identity(small_body), body=small_body
        )

    transport, api, _ = world()
    api.proxy_handler = lambda method, path, body: {
        **typed_response(method, path, body),
        "bytes_sent": len(BODY) + 1,
    }
    with pytest.raises(Exception, match="egress_body_digest_binding_mismatch"):
        await transport.execute_with_body(identity=identity(BODY), body=BODY)


@pytest.mark.asyncio
async def test_grant_identity_and_backend_ownership_mismatches_fail_closed() -> None:
    foreign = grant_record(artifact_digest=CONTENT_DIGEST)
    transport, api, _ = world(record=foreign)
    with pytest.raises(Exception, match="egress_grant_identity_mismatch"):
        await transport.execute_with_body(identity=identity(BODY), body=BODY)
    assert api.proxy_calls == []

    tokens = FakeTokenProvider()
    api = FakeK8sApi(tokens)
    api.deployment["metadata"]["annotations"][BACKEND_ANNOTATION] = CONTENT_DIGEST
    resolver = CanonicalBackendResolver(
        config=runtime_config(),
        instance_store=FakeInstances(),
        artifact_store=FakeArtifacts(),
        api=api,
        token_provider=tokens,
    )
    transport = K8sEgressTransport(
        resolver=resolver,
        declarations=DurableGrantEgressPolicy(GrantStore(grant_record())),
        activator=FakeActivator(),
    )
    with pytest.raises(Exception, match="canonical_backend_digest_mismatch"):
        await transport.execute_with_body(identity=identity(BODY), body=BODY)
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_default_sa_is_forbidden_and_api_failures_are_token_redacted() -> None:
    with pytest.raises(RuntimeConfigError, match="default_mounted_service_account_forbidden"):
        K8sEgressTransport(
            config=runtime_config(),
            instance_store=FakeInstances(),
            artifact_store=FakeArtifacts(),
            grant_store=GrantStore(grant_record()),
            api=K8sApiClient(client=object()),
            token_provider=FakeTokenProvider(),
        )

    transport, api, _ = world()
    api.fail_with = RuntimeError("Bearer super-secret-token https://private-host")
    with pytest.raises(Exception) as caught:
        await transport.execute_with_body(identity=identity(BODY), body=BODY)
    assert "super-secret-token" not in str(caught.value)
    assert "private-host" not in str(caught.value)


@pytest.mark.asyncio
async def test_backend_sensitive_response_field_is_rejected_and_redacted() -> None:
    transport, api, _ = world()
    api.proxy_handler = lambda method, path, body: {
        **typed_response(method, path, body),
        "token": "super-secret-backend-token",
    }
    with pytest.raises(Exception) as caught:
        await transport.execute_with_body(identity=identity(BODY), body=BODY)
    assert "backend_sensitive_response_field" in str(caught.value)
    assert "super-secret-backend-token" not in str(caught.value)



@pytest.mark.asyncio
async def test_bytes_sent_proves_original_payload_not_backend_response_mapping() -> None:
    transport, api, _ = world()
    api.proxy_handler = typed_response
    request_body = b"x"
    result = await transport.execute_with_body(
        identity=identity(request_body), body=request_body
    )
    assert result["bytes_sent"] == len(request_body) == 1
    assert len(api.proxy_calls) == 1


@pytest.mark.asyncio
async def test_exact_wake_headers_ignore_spoof_and_token_stays_out_of_json() -> None:
    activator = FakeActivator()
    transport, api, _ = world(activator=activator)
    api.proxy_handler = typed_response
    request = identity(BODY)
    request["headers"] = {
        "X-Plugin-Capability-Token": "client-spoof-token",
        "X-Plugin-Artifact-Digest": CONTENT_DIGEST,
        "X-Plugin-Unknown": "client-spoof-token",
    }
    request["capability_token"] = "client-spoof-token"
    result = await transport.execute_with_body(identity=request, body=BODY)
    token = "wake-token-test-only-1"
    assert api.proxy_headers[0] == {
        "X-Plugin-Capability-Token": token,
        "X-Plugin-Instance-ID": INSTANCE,
        "X-Plugin-ID": PLUGIN,
        "X-Plugin-Artifact-Digest": ARTIFACT_DIGEST,
    }
    assert activator.calls == [(INSTANCE, None)]
    method, path, payload = api.proxy_calls[0]
    assert method == "POST" and payload is not None
    for exposed in (
        path,
        json.dumps(payload, sort_keys=True),
        json.dumps(result, sort_keys=True),
    ):
        assert token not in exposed
        assert "client-spoof-token" not in exposed


@pytest.mark.asyncio
async def test_every_egress_operation_mints_a_fresh_wake_token() -> None:
    activator = FakeActivator()
    transport, api, _ = world(activator=activator)
    api.proxy_handler = typed_response
    await transport.execute_with_body(identity=identity(BODY), body=BODY)
    await transport.execute_with_body(identity=identity(BODY), body=BODY)
    assert activator.calls == [(INSTANCE, None), (INSTANCE, None)]
    tokens = [headers["X-Plugin-Capability-Token"] for headers in api.proxy_headers]
    assert tokens == ["wake-token-test-only-1", "wake-token-test-only-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"company_id": "other"},
        {"plugin_id": "other"},
        {"plugin_instance_id": "other"},
        {"artifact_digest": CONTENT_DIGEST},
        {"grant_id": "other"},
        {"scopes": ["pods"]},
    ],
)
async def test_egress_wake_binding_mismatch_never_reaches_proxy(
    changes: dict[str, Any],
) -> None:
    transport, api, _ = world(activator=FakeActivator(**changes))
    with pytest.raises(Exception, match="egress_backend_activation_failed"):
        await transport.execute_with_body(identity=identity(BODY), body=BODY)
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_egress_activation_failure_redacts_wake_token(caplog: pytest.LogCaptureFixture) -> None:
    secret = "egress-wake-token-that-must-not-leak"
    transport, api, _ = world(activator=FakeActivator(fail_with=RuntimeError(secret)))
    with pytest.raises(Exception, match="egress_backend_activation_failed") as caught:
        await transport.execute_with_body(identity=identity(BODY), body=BODY)
    assert secret not in str(caught.value)
    assert secret not in caplog.text
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_close_is_idempotent_and_does_not_close_shared_lifecycle_api() -> None:
    transport, api, _ = world()
    await transport.aclose()
    await transport.aclose()
    assert api.close_calls == 0
    with pytest.raises(Exception, match="egress_transport_closed"):
        await transport.execute_with_body(identity=identity(BODY), body=BODY)
