"""Canonical pod executor acceptance tests; intentionally not run during freeze."""
from __future__ import annotations

import json as json_module
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError
from prodavan.infrastructure.plugins.runtime.k8s_scale import K8sApiClient
from prodavan.infrastructure.plugins.runtime.pod_executor import (
    CanonicalBackendResolver,
    K8sPodExecutor,
    PodExecutorError,
)
from prodavan.infrastructure.plugins.runtime.deployment_admin import (
    BACKEND_ANNOTATION,
    INSTANCE_LABEL,
)
from tests.unit.plugins.runtime.canonical_backend_fixtures import (
    ARTIFACT_DIGEST,
    BACKEND_DIGEST,
    CONTENT_DIGEST,
    EMPTY_DIGEST,
    INSTANCE,
    JOB,
    PROJECT,
    FakeActivator,
    FakeArtifacts,
    FakeInstances,
    FakeK8sApi,
    FakeTokenProvider,
    artifact_record,
    canonical_digest,
    canonical_size,
    instance_record,
    resource_name,
    runtime_config,
)


def identity(**changes: Any) -> dict[str, Any]:
    value = {
        "company_id": "co_123",
        "project_id": PROJECT,
        "plugin_id": "mod_x",
        "plugin_instance_id": INSTANCE,
        "artifact_digest": ARTIFACT_DIGEST,
        "grant_id": "grant_1",
        "run_id": JOB,
        "pod_id": INSTANCE,
        "action": "read_status",
        "target_ref": JOB,
        "arguments_digest": EMPTY_DIGEST,
        "request_id": "req-pod-1",
        "max_result_bytes": 1024,
    }
    value.update(changes)
    return value


def world(
    *, activator: FakeActivator | None = None
) -> tuple[K8sPodExecutor, FakeK8sApi, FakeActivator]:
    tokens = FakeTokenProvider()
    api = FakeK8sApi(tokens)
    activator = activator or FakeActivator()
    resolver = CanonicalBackendResolver(
        config=runtime_config(),
        instance_store=FakeInstances(),
        artifact_store=FakeArtifacts(),
        api=api,
        token_provider=tokens,
    )
    executor = K8sPodExecutor(
        resolver=resolver, activator=activator, max_result_bytes=1024
    )
    return executor, api, activator


def status_response(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
    assert method == "POST"
    assert path.endswith("/v1/status")
    assert body is not None
    result = {"status": "ready", "ready": True}
    return {
        **body,
        "result": result,
        "result_digest": canonical_digest(result),
        "bytes_received": canonical_size(result),
    }


@pytest.mark.asyncio
async def test_read_status_uses_only_canonical_service_and_typed_result() -> None:
    executor, api, _ = world()
    # Kubernetes adds revision annotations; required digest annotations are a subset.
    api.deployment["metadata"]["annotations"]["deployment.kubernetes.io/revision"] = "7"
    api.proxy_handler = status_response
    result = await executor.execute(identity=identity())
    assert result["backend_digest"] == BACKEND_DIGEST
    assert result["result_digest"] == canonical_digest({"status": "ready", "ready": True})
    assert len(api.proxy_calls) == 1
    method, path, payload = api.proxy_calls[0]
    assert method == "POST"
    assert path == (
        f"/api/v1/namespaces/plugins/services/{resource_name()}:8080/"
        "proxy/v1/status"
    )
    assert payload is not None
    assert "command" not in payload and "args" not in payload and "shell" not in payload


@pytest.mark.asyncio
async def test_message_and_artifact_require_digest_bound_job_channels() -> None:
    executor, api, _ = world()

    def message_response(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        assert path.endswith("/v1/messages")
        assert body is not None
        result = {"job_id": JOB, "arguments_digest": CONTENT_DIGEST, "accepted": True}
        return {
            **body,
            "result": result,
            "result_digest": canonical_digest(result),
            "bytes_received": canonical_size(result),
        }

    api.proxy_handler = message_response
    message = await executor.execute(identity=identity(
        action="send_agent_message", arguments_digest=CONTENT_DIGEST
    ))
    assert message["action"] == "send_agent_message"

    def artifact_response(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        assert path.endswith("/v1/artifacts")
        assert body is not None
        result = {"job_id": JOB, "artifact_digest": CONTENT_DIGEST, "byte_count": 128}
        return {
            **body,
            "result": result,
            "result_digest": canonical_digest(result),
            "bytes_received": 128,
        }

    api.proxy_handler = artifact_response
    artifact = await executor.execute(identity=identity(
        action="read_artifact", target_ref=CONTENT_DIGEST
    ))
    assert artifact["bytes_received"] == 128
    with pytest.raises(PodExecutorError, match="pod_message_digest_channel_required"):
        await executor.execute(identity=identity(action="send_agent_message"))
    with pytest.raises(PodExecutorError, match="pod_artifact_digest_channel_required"):
        await executor.execute(identity=identity(action="read_artifact", target_ref="../../secret"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"pod_id": "some-agent-pod"},
        {"project_id": "other-project"},
        {"run_id": "other-job", "target_ref": "other-job"},
        {"action": "exec", "target_ref": JOB},
    ],
)
async def test_arbitrary_agent_project_job_and_shell_are_denied(changes: dict[str, Any]) -> None:
    executor, api, _ = world()
    with pytest.raises(PodExecutorError):
        await executor.execute(identity=identity(**changes))
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_ownership_and_backend_digest_mismatch_fail_closed() -> None:
    executor, api, _ = world()
    api.service["metadata"]["labels"][INSTANCE_LABEL] = "f" * 40
    with pytest.raises(PodExecutorError, match="ownership_mismatch"):
        await executor.execute(identity=identity())
    assert api.proxy_calls == []

    executor, api, _ = world()
    api.deployment["metadata"]["annotations"][BACKEND_ANNOTATION] = CONTENT_DIGEST
    with pytest.raises(PodExecutorError, match="digest_mismatch"):
        await executor.execute(identity=identity())
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_missing_durable_mapping_and_result_digest_mismatch_fail_closed() -> None:
    tokens = FakeTokenProvider()
    api = FakeK8sApi(tokens)
    resolver = CanonicalBackendResolver(
        config=runtime_config(),
        instance_store=FakeInstances(instance_record(company_id="other")),
        artifact_store=FakeArtifacts(artifact_record()),
        api=api,
        token_provider=tokens,
    )
    with pytest.raises(PodExecutorError, match="canonical_instance_binding_mismatch"):
        await K8sPodExecutor(
            resolver=resolver, activator=FakeActivator()
        ).execute(identity=identity())

    executor, api, _ = world()
    api.proxy_handler = lambda method, path, body: {
        **(body or {}),
        "result": {"status": "ready", "ready": True},
        "result_digest": CONTENT_DIGEST,
        "bytes_received": 1,
    }
    with pytest.raises(PodExecutorError, match="result_digest_mismatch"):
        await executor.execute(identity=identity())


@pytest.mark.asyncio
async def test_default_mounted_sa_is_forbidden_and_errors_are_token_redacted() -> None:
    tokens = FakeTokenProvider()
    with pytest.raises(RuntimeConfigError, match="default_mounted_service_account_forbidden"):
        K8sPodExecutor(
            config=runtime_config(),
            instance_store=FakeInstances(),
            artifact_store=FakeArtifacts(),
            api=K8sApiClient(client=object()),
            token_provider=tokens,
        )

    class UnmarkedApi:
        async def request(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("unscoped API must not be called")

    with pytest.raises(RuntimeConfigError, match="default_mounted_service_account_forbidden"):
        K8sPodExecutor(
            config=runtime_config(),
            instance_store=FakeInstances(),
            artifact_store=FakeArtifacts(),
            api=UnmarkedApi(),
            token_provider=tokens,
        )

    executor, api, _ = world()
    api.fail_with = RuntimeError("Bearer super-secret-token and private host")
    with pytest.raises(PodExecutorError) as caught:
        await executor.execute(identity=identity())
    assert "super-secret-token" not in str(caught.value)
    assert "private host" not in str(caught.value)


def test_fresh_activator_is_mandatory_for_pod_runtime() -> None:
    executor, _, _ = world()
    with pytest.raises(RuntimeConfigError, match="fresh PluginActivator"):
        K8sPodExecutor(resolver=executor._resolver)


@pytest.mark.asyncio
async def test_exact_wake_headers_ignore_client_spoof_and_never_echo_token() -> None:
    executor, api, activator = world()
    api.proxy_handler = status_response
    request = identity()
    request["headers"] = {
        "X-Plugin-Capability-Token": "client-spoof-token",
        "X-Plugin-Instance-ID": "other-instance",
        "Authorization": "Bearer client-spoof-token",
    }
    request["capability_token"] = "client-spoof-token"
    result = await executor.execute(identity=request)
    headers = api.proxy_headers[0]
    token = "wake-token-test-only-1"
    assert headers == {
        "X-Plugin-Capability-Token": token,
        "X-Plugin-Instance-ID": INSTANCE,
        "X-Plugin-ID": "mod_x",
        "X-Plugin-Artifact-Digest": ARTIFACT_DIGEST,
    }
    assert activator.calls == [(INSTANCE, None)]
    method, path, payload = api.proxy_calls[0]
    assert method == "POST" and payload is not None
    assert payload["max_result_bytes"] == 1024
    serialized_result = json_module.dumps(result, sort_keys=True)
    serialized_payload = json_module.dumps(payload, sort_keys=True)
    for exposed in (path, serialized_payload, serialized_result):
        assert token not in exposed
        assert "client-spoof-token" not in exposed


@pytest.mark.asyncio
async def test_every_pod_operation_uses_a_new_wake_lease() -> None:
    executor, api, activator = world()
    api.proxy_handler = status_response
    await executor.execute(identity=identity(request_id="req-pod-1"))
    await executor.execute(identity=identity(request_id="req-pod-2"))
    assert activator.calls == [(INSTANCE, None), (INSTANCE, None)]
    tokens = [headers["X-Plugin-Capability-Token"] for headers in api.proxy_headers]
    assert tokens == ["wake-token-test-only-1", "wake-token-test-only-2"]
    assert tokens[0] != tokens[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"company_id": "other"},
        {"project_id": "other"},
        {"plugin_id": "other"},
        {"plugin_instance_id": "other"},
        {"artifact_digest": CONTENT_DIGEST},
        {"grant_id": "other"},
        {"scopes": ["network"]},
    ],
)
async def test_wake_claim_binding_mismatch_never_reaches_proxy(changes: dict[str, Any]) -> None:
    executor, api, _ = world(activator=FakeActivator(**changes))
    with pytest.raises(PodExecutorError, match="pod_backend_activation_failed"):
        await executor.execute(identity=identity())
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_wake_result_instance_mismatch_never_reaches_proxy() -> None:
    executor, api, _ = world(activator=FakeActivator(lease_instance_id="other"))
    with pytest.raises(PodExecutorError, match="pod_backend_activation_failed"):
        await executor.execute(identity=identity())
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_activation_failure_redacts_token_from_error_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "wake-token-that-must-not-leak"
    executor, api, _ = world(activator=FakeActivator(fail_with=RuntimeError(secret)))
    with pytest.raises(PodExecutorError, match="pod_backend_activation_failed") as caught:
        await executor.execute(identity=identity())
    assert secret not in str(caught.value)
    assert secret not in caplog.text
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_concrete_scoped_k8s_client_forwards_fixed_plugin_headers() -> None:
    tokens = FakeTokenProvider()
    resources = FakeK8sApi(tokens)
    activator = FakeActivator()

    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any] | None,
            timeout: float,
        ) -> Any:
            self.calls.append({
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json": json,
                "timeout": timeout,
            })
            path = url.removeprefix("https://k8s.test")
            if method == "GET" and path.endswith(f"/services/{resource_name()}"):
                payload = resources.service
            elif method == "GET" and path.endswith(f"/deployments/{resource_name()}"):
                payload = resources.deployment
            elif method == "POST" and path.endswith("/proxy/v1/status"):
                payload = status_response(method, path, json)
            else:
                raise AssertionError("unexpected fake scoped K8s request")
            return SimpleNamespace(status_code=200, json=lambda: payload)

    client = Client()
    api = K8sApiClient(
        auth=SimpleNamespace(api_base=lambda: "https://k8s.test"),
        client=client,
        token_provider=tokens,
    )
    executor = K8sPodExecutor(
        config=runtime_config(),
        instance_store=FakeInstances(),
        artifact_store=FakeArtifacts(),
        api=api,
        token_provider=tokens,
        activator=activator,
        max_result_bytes=1024,
    )
    await executor.execute(identity=identity())
    proxied = [call for call in client.calls if call["method"] == "POST"]
    assert len(proxied) == 1
    request = proxied[0]
    assert request["headers"] == {
        "Authorization": "Bearer scoped-lifecycle-token-test-only",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Plugin-Capability-Token": "wake-token-test-only-1",
        "X-Plugin-Instance-ID": INSTANCE,
        "X-Plugin-ID": "mod_x",
        "X-Plugin-Artifact-Digest": ARTIFACT_DIGEST,
    }
    assert "wake-token-test-only-1" not in request["url"]
    assert "wake-token-test-only-1" not in json_module.dumps(request["json"])


@pytest.mark.asyncio
async def test_close_is_idempotent_and_does_not_close_shared_lifecycle_api() -> None:
    executor, api, _ = world()
    await executor.aclose()
    await executor.aclose()
    assert api.close_calls == 0
    with pytest.raises(PodExecutorError, match="pod_executor_closed"):
        await executor.execute(identity=identity())
