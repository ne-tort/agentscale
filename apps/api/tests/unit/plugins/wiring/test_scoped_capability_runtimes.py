"""Installer wiring for scoped pod/network runtimes; not executed during freeze."""
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressMode,
    EgressPolicy,
    GrantStatus,
    QuotaSet,
)
from prodavan.application.plugins.capabilities.adapters.network import K8sEgressNetworkAdapter
from prodavan.application.plugins.capabilities.adapters.pods import ScopedPodsAdapter
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.wiring.adapters import (
    UnavailableAdapter,
    aclose_adapter_registry,
)
from prodavan.application.plugins.wiring.install import (
    PluginCompositionError,
    _capability_runtime_bindings,
    _validate_scoped_capability_transports,
)
from prodavan.infrastructure.plugins.runtime.egress_transport import (
    DurableGrantEgressPolicy,
    K8sEgressTransport,
)
from prodavan.infrastructure.plugins.runtime.pod_executor import K8sPodExecutor
from tests.unit.plugins.runtime.canonical_backend_fixtures import (
    ARTIFACT_DIGEST,
    COMPANY,
    CONTENT_DIGEST,
    EMPTY_DIGEST,
    INSTANCE,
    JOB,
    PLUGIN,
    PROJECT,
    FakeActivator,
    FakeArtifacts,
    FakeInstances,
    FakeK8sApi,
    FakeTokenProvider,
    canonical_digest,
    canonical_size,
    runtime_config,
)


class Grants:
    def __init__(self) -> None:
        self.record = CapabilityGrantRecord(
            grant_id="grant_1",
            company_id=COMPANY,
            plugin_id=PLUGIN,
            plugin_instance_id=INSTANCE,
            manifest_version="1.2.3",
            artifact_digest=ARTIFACT_DIGEST,
            capabilities=("network",),
            egress_policy=EgressPolicy(
                mode=EgressMode.WEBHOOKS_ONLY,
                allowed_hosts=("api.example.com",),
                allowed_paths=("/v1/allowed",),
            ),
            quotas=QuotaSet(max_egress_bytes=4096),
            status=GrantStatus.ACTIVE,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    async def get_by_grant_id(self, *, grant_id: str) -> CapabilityGrantRecord | None:
        return self.record if self.record.grant_id == grant_id else None


class Resolver:
    async def resolve_body_digest(
        self, company_id: str, plugin_id: str, plugin_instance_id: str, digest: str
    ) -> bytes | None:
        return None


class Policy:
    async def allows(self, **kwargs: Any) -> bool:
        return True

    def check(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(allowed=True)


def world() -> tuple[Any, Any, FakeK8sApi]:
    tokens = FakeTokenProvider()
    api = FakeK8sApi(tokens)
    runtime = SimpleNamespace(
        config=runtime_config(),
        lifecycle_api=api,
        token_provider=tokens,
        activator=FakeActivator(),
    )
    repos = SimpleNamespace(
        instance_store=FakeInstances(),
        artifact_store=FakeArtifacts(),
        grant_store=Grants(),
    )
    return runtime, repos, api


def test_install_constructs_scoped_digest_bound_runtimes_without_placeholders() -> None:
    runtime, repos, api = world()
    pod, egress, policy = _capability_runtime_bindings(runtime, repos, {})
    assert isinstance(pod, K8sPodExecutor)
    assert isinstance(egress, K8sEgressTransport)
    assert isinstance(policy, DurableGrantEgressPolicy)
    assert not isinstance(pod, UnavailableAdapter)
    assert not isinstance(egress, UnavailableAdapter)
    assert pod.scoped_identity is True and egress.scoped_identity is True
    assert pod.digest_bound_channel is True and egress.digest_bound_channel is True


@pytest.mark.asyncio
async def test_composed_activator_mints_fresh_leases_for_both_runtimes() -> None:
    runtime, repos, api = world()
    runtime_activator = runtime.activator
    shared_activator = FakeActivator()
    pod, egress, _ = _capability_runtime_bindings(
        runtime, repos, {}, {"activator": shared_activator}
    )
    assert pod._activator is shared_activator
    assert egress._activator is shared_activator

    def proxy_response(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        assert method == "POST" and body is not None
        if path.endswith("/v1/status"):
            result = {"status": "ready", "ready": True}
            return {
                **body,
                "result": result,
                "result_digest": canonical_digest(result),
                "bytes_received": canonical_size(result),
            }
        assert path.endswith("/v1/egress")
        sent = base64.b64decode(body["body_base64"], validate=True)
        return {
            **body,
            "status_code": 200,
            "bytes_sent": len(sent),
            "bytes_received": 1,
            "response_digest": CONTENT_DIGEST,
            "resolved_host": "api.example.com",
            "hops": ("api.example.com",),
            "redirect_checked": True,
            "tls_verified": True,
        }

    api.proxy_handler = proxy_response
    await pod.execute(identity={
        "company_id": COMPANY,
        "project_id": PROJECT,
        "plugin_id": PLUGIN,
        "plugin_instance_id": INSTANCE,
        "artifact_digest": ARTIFACT_DIGEST,
        "grant_id": "grant_1",
        "run_id": JOB,
        "pod_id": INSTANCE,
        "action": "read_status",
        "target_ref": JOB,
        "arguments_digest": EMPTY_DIGEST,
        "request_id": "req-composition-pod",
        "max_result_bytes": 1024,
    })
    body = b"{}"
    await egress.execute_with_body(
        identity={
            "company_id": COMPANY,
            "plugin_id": PLUGIN,
            "plugin_instance_id": INSTANCE,
            "artifact_digest": ARTIFACT_DIGEST,
            "grant_id": "grant_1",
            "subject_id": f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
            "request_id": "req-composition-egress",
            "run_id": JOB,
            "host": "api.example.com",
            "port": 443,
            "path": "/v1/allowed",
            "method": "POST",
            "body_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
            "max_request_bytes": 1024,
            "max_response_bytes": 1024,
        },
        body=body,
    )

    # Both calls consume new leases from the exact zone-composed activator.
    assert shared_activator.calls == [(INSTANCE, None), (INSTANCE, None)]
    assert runtime_activator.calls == []
    wake_tokens = [
        headers["X-Plugin-Capability-Token"] for headers in api.proxy_headers
    ]
    assert wake_tokens == ["wake-token-test-only-1", "wake-token-test-only-2"]
    # The custom scoped API sees only fixed wake headers, never a mounted token.
    assert api._token_provider.calls == 0
    assert all(
        set(headers or {}) == {
            "X-Plugin-Capability-Token",
            "X-Plugin-Instance-ID",
            "X-Plugin-ID",
            "X-Plugin-Artifact-Digest",
        }
        and "scoped-lifecycle-token-test-only" not in str(headers)
        for headers in api.proxy_headers
    )


def test_unscoped_override_and_unavailable_registered_adapter_fail_closed() -> None:
    runtime, repos, _ = world()

    class Unscoped:
        scoped_identity = False
        digest_bound_channel = True
        async def execute(self, *, identity: dict[str, Any]) -> dict[str, Any]:
            return identity
        async def aclose(self) -> None:
            return None

    with pytest.raises(PluginCompositionError, match="ServiceAccount"):
        _capability_runtime_bindings(runtime, repos, {"pod_executor": Unscoped()})

    pod, egress, policy = _capability_runtime_bindings(runtime, repos, {})
    registry = AdapterRegistry()
    registry.register("pods", ScopedPodsAdapter(executor=pod))
    registry.register("network", UnavailableAdapter("network"))
    with pytest.raises(PluginCompositionError, match="scoped runtimes"):
        _validate_scoped_capability_transports(SimpleNamespace(registry=registry))


@pytest.mark.asyncio
async def test_registered_adapters_require_body_resolver_and_close_once() -> None:
    runtime, repos, api = world()
    pod, egress, policy = _capability_runtime_bindings(runtime, repos, {})
    registry = AdapterRegistry()
    pods = ScopedPodsAdapter(executor=pod)
    network = K8sEgressNetworkAdapter(
        transport=egress,
        egress=policy,
        initial_policy=policy,
        body_digest_resolver=Resolver(),
    )
    registry.register("pods", pods)
    registry.register("network", network)
    platform = SimpleNamespace(registry=registry)
    _validate_scoped_capability_transports(platform)
    await aclose_adapter_registry(registry)
    await aclose_adapter_registry(registry)
    assert pods._closed is True and network._closed is True
    assert pod._closed is True and egress._closed is True
    assert api.close_calls == 0


@pytest.mark.asyncio
async def test_enabled_network_without_singleton_body_resolver_is_startup_error() -> None:
    runtime, repos, _ = world()
    pod, egress, policy = _capability_runtime_bindings(runtime, repos, {})
    registry = AdapterRegistry()
    registry.register("pods", ScopedPodsAdapter(executor=pod))
    registry.register(
        "network",
        K8sEgressNetworkAdapter(
            transport=egress,
            egress=policy,
            initial_policy=policy,
            body_digest_resolver=None,
        ),
    )
    with pytest.raises(PluginCompositionError, match="body digest resolver"):
        _validate_scoped_capability_transports(SimpleNamespace(registry=registry))
