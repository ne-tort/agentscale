"""Offline rollback tests for the disposable acceptance namespace lifecycle.

No cluster, kubeconfig, subprocess, or real TokenRequest is used: the REST client
and identity factory are replaced with in-memory fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    CORE_API,
    K8sApiError,
    K8sRawResponse,
)
from tests.e2e.k8s.plugin_acceptance.acceptance_config import AcceptanceConfig
from tests.e2e.k8s.plugin_acceptance.cluster import (
    AcceptanceCluster,
    AcceptanceClusterError,
    namespace_manifest,
)
from tests.e2e.k8s.plugin_acceptance import cluster as cluster_module


class _Identity:
    def __init__(self) -> None:
        self.cleanup_calls = 0

    def cleanup(self) -> None:
        self.cleanup_calls += 1


class _FakeClient:
    def __init__(self, handler: Any, calls: list[dict[str, str]]) -> None:
        self._handler = handler
        self._calls = calls

    async def request(self, method: str, path: str, **kwargs: Any) -> K8sRawResponse:
        self._calls.append({"method": method.upper(), "path": path})
        return await self._handler(method.upper(), path)

    async def aclose(self) -> None:
        return None


def _config() -> AcceptanceConfig:
    return AcceptanceConfig(
        kubeconfig_path=Path("unused-offline-kubeconfig"),
        context=None,
        namespace_prefix="acceptance-rollback",
        run_id="run01",
        ttl_seconds=60,
    )


def _wire(monkeypatch: pytest.MonkeyPatch, handler: Any, calls: list[dict[str, str]]) -> _Identity:
    identity = _Identity()
    monkeypatch.setattr(cluster_module, "load_identity", lambda _config: identity)
    monkeypatch.setattr(cluster_module, "KubeconfigAuth", lambda _identity: object())
    monkeypatch.setattr(
        cluster_module,
        "K8sApiClient",
        lambda **_kwargs: _FakeClient(handler, calls),
    )
    return identity


@pytest.mark.asyncio
async def test_already_exists_never_deletes_foreign_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    async def already_exists(method: str, path: str) -> K8sRawResponse:
        if method == "POST" and path == f"{CORE_API}/namespaces":
            raise K8sApiError("409 AlreadyExists: foreign namespace")
        raise AssertionError(f"foreign namespace must not be read or deleted: {method} {path}")

    identity = _wire(monkeypatch, already_exists, calls)
    with pytest.raises(AcceptanceClusterError, match="unique suffix collision"):
        await AcceptanceCluster.create(_config(), "collision")

    assert calls == [{"method": "POST", "path": f"{CORE_API}/namespaces"}]
    assert identity.cleanup_calls == 1


@pytest.mark.asyncio
async def test_post_create_failure_deletes_only_after_owned_marker_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    namespace = config.namespace("owned")
    owned = namespace_manifest(config, namespace)
    calls: list[dict[str, str]] = []

    async def owned_namespace(method: str, path: str) -> K8sRawResponse:
        if method == "POST" and path == f"{CORE_API}/namespaces":
            return K8sRawResponse(status_code=201, body=owned)
        if method == "POST" and "serviceaccounts" in path:
            raise RuntimeError("injected post-create RBAC failure")
        if method == "GET" and path == f"{CORE_API}/namespaces/{namespace}":
            return K8sRawResponse(status_code=200, body=owned)
        if method == "DELETE" and path == f"{CORE_API}/namespaces/{namespace}":
            return K8sRawResponse(status_code=200, body={})
        raise AssertionError(f"unexpected request: {method} {path}")

    identity = _wire(monkeypatch, owned_namespace, calls)
    with pytest.raises(RuntimeError, match="post-create RBAC failure"):
        await AcceptanceCluster.create(config, "owned")

    assert [call["method"] for call in calls[-2:]] == ["GET", "DELETE"]
    assert calls[-1]["path"] == f"{CORE_API}/namespaces/{namespace}"
    assert identity.cleanup_calls == 1


@pytest.mark.asyncio
async def test_post_create_failure_preserves_namespace_with_foreign_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    namespace = config.namespace("foreign")
    created = namespace_manifest(config, namespace)
    foreign = {
        **created,
        "metadata": {
            **created["metadata"],
            "annotations": {
                **created["metadata"]["annotations"],
                "agentscale.ne-tort/acceptance-run-id": "another-run",
            },
        },
    }
    calls: list[dict[str, str]] = []

    async def foreign_namespace(method: str, path: str) -> K8sRawResponse:
        if method == "POST" and path == f"{CORE_API}/namespaces":
            return K8sRawResponse(status_code=201, body=created)
        if method == "POST" and "serviceaccounts" in path:
            raise RuntimeError("injected post-create RBAC failure")
        if method == "GET" and path == f"{CORE_API}/namespaces/{namespace}":
            return K8sRawResponse(status_code=200, body=foreign)
        if method == "DELETE":
            raise AssertionError("namespace with foreign markers must not be deleted")
        raise AssertionError(f"unexpected request: {method} {path}")

    identity = _wire(monkeypatch, foreign_namespace, calls)
    with pytest.raises(RuntimeError, match="post-create RBAC failure"):
        await AcceptanceCluster.create(config, "foreign")

    assert calls[-1] == {"method": "GET", "path": f"{CORE_API}/namespaces/{namespace}"}
    assert all(call["method"] != "DELETE" for call in calls)
    assert identity.cleanup_calls == 1
