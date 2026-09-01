"""Unit tests for k8s sandbox infrastructure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.domain.pods import PodRuntimeContext, runtime_ref_for, sanitize_dns
from prodavan.infrastructure.k8s.auth import InClusterAuth
from prodavan.infrastructure.k8s.errors import PermanentK8sError
from prodavan.infrastructure.k8s.sandbox.client import (
    K8sSandboxClient,
    _parse_snapshot,
    _pod_fatal_failure,
)
from prodavan.infrastructure.k8s.sandbox.pod_spec import build_pod_body


def test_sanitize_dns_workspace_key() -> None:
    assert sanitize_dns("wk_demo") == "wk-demo"
    assert runtime_ref_for("wk_demo", mode="k8s") == "pod-wk-demo"
    assert runtime_ref_for("wk_demo", mode="stub") == "object-ws:wk_demo"


def test_build_pod_body_labels() -> None:
    ctx = PodRuntimeContext(
        pod_id="pod_abc",
        project_id="prj_abc",
        company_id="cmp_abc",
        workspace_key="wk_demo",
        hydrate_generation=2,
        extra_env=(("LOG_LEVEL", "debug"),),
    )
    body = build_pod_body(
        runtime_ref="pod-wk-demo",
        namespace="prodavan-sandboxes",
        context=ctx,
        image="sandbox:latest",
        hydrate_image="hydrate:latest",
        service_account="prodavan-sandbox",
        cpu_request="100m",
        cpu_limit="1",
        memory_request="256Mi",
        memory_limit="1Gi",
    )
    labels = body["metadata"]["labels"]
    assert labels["prodavan.io/managed-by"] == "pod-service"
    assert labels["prodavan.io/pod-id"] == "pod_abc"
    assert labels["prodavan.io/hydrate-generation"] == "2"
    assert body["spec"]["initContainers"][0]["name"] == "hydrate"
    runtime = body["spec"]["containers"][0]
    assert runtime["name"] == "agent-runtime"
    assert runtime.get("workingDir") == "/workspace"
    runtime_env = {item["name"]: item.get("value") for item in runtime["env"]}
    assert runtime_env["LOG_LEVEL"] == "debug"
    assert runtime["command"] == ["sleep", "infinity"]


def test_build_pod_body_image_pull_secret() -> None:
    ctx = PodRuntimeContext(
        pod_id="pod_abc",
        project_id="prj_abc",
        company_id="cmp_abc",
        workspace_key="wk_demo",
    )
    body = build_pod_body(
        runtime_ref="pod-wk-demo",
        namespace="prodavan-sandboxes",
        context=ctx,
        image="sandbox:latest",
        hydrate_image="hydrate:latest",
        service_account="prodavan-sandbox",
        cpu_request="100m",
        cpu_limit="1",
        memory_request="256Mi",
        memory_limit="1Gi",
        image_pull_secret="ghcr-pull",
    )
    assert body["spec"]["imagePullSecrets"] == [{"name": "ghcr-pull"}]


def test_pod_fatal_failure_image_pull_backoff() -> None:
    status = {
        "initContainerStatuses": [
            {
                "name": "hydrate",
                "state": {
                    "waiting": {
                        "reason": "ImagePullBackOff",
                        "message": 'Back-off pulling image "ghcr.io/ne-tort/prodavan-api:latest"',
                    }
                },
            }
        ]
    }
    fatal = _pod_fatal_failure(status)
    assert fatal is not None
    assert "ImagePullBackOff" in fatal


def test_parse_snapshot_includes_fatal_failure() -> None:
    body = {
        "metadata": {"name": "pod-wk-demo", "labels": {}},
        "status": {
            "phase": "Pending",
            "initContainerStatuses": [
                {
                    "name": "hydrate",
                    "state": {
                        "waiting": {
                            "reason": "ErrImagePull",
                            "message": "pull access denied",
                        }
                    },
                }
            ],
        },
    }
    snap = _parse_snapshot(body)
    assert snap.fatal_failure is not None
    assert "ErrImagePull" in snap.fatal_failure


@pytest.mark.asyncio
async def test_wait_ready_fails_fast_on_image_pull_backoff() -> None:
    auth = MagicMock(spec=InClusterAuth)
    auth.api_base.return_value = "https://k8s.example"
    auth.headers.return_value = {"Authorization": "Bearer x"}
    auth.client_kwargs.return_value = {"verify": False, "timeout": 1.0}
    client = K8sSandboxClient(namespace="prodavan-sandboxes", auth=auth)

    pod_body = {
        "metadata": {"name": "pod-wk-demo", "labels": {}},
        "status": {
            "phase": "Pending",
            "initContainerStatuses": [
                {
                    "name": "hydrate",
                    "state": {
                        "waiting": {
                            "reason": "ImagePullBackOff",
                            "message": "Back-off pulling image",
                        }
                    },
                }
            ],
        },
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = pod_body

    with patch("prodavan.infrastructure.k8s.sandbox.client.httpx.AsyncClient") as ac:
        ac.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        with pytest.raises(PermanentK8sError, match="cannot start"):
            await client.wait_ready("pod-wk-demo", timeout=30.0)


@pytest.mark.asyncio
async def test_wait_ready_tolerates_transient_missing_pod() -> None:
    auth = MagicMock(spec=InClusterAuth)
    auth.api_base.return_value = "https://k8s.example"
    auth.headers.return_value = {"Authorization": "Bearer x"}
    auth.client_kwargs.return_value = {"verify": False, "timeout": 1.0}
    client = K8sSandboxClient(namespace="prodavan-sandboxes", auth=auth)

    missing = MagicMock()
    missing.status_code = 404
    ready_body = {
        "metadata": {"name": "pod-wk-demo", "labels": {}},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }
    ready = MagicMock()
    ready.status_code = 200
    ready.json.return_value = ready_body

    calls = {"n": 0}

    async def get_side_effect(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return missing
        return ready

    with patch("prodavan.infrastructure.k8s.sandbox.client.httpx.AsyncClient") as ac:
        ac.return_value.__aenter__.return_value.get = AsyncMock(side_effect=get_side_effect)
        with patch(
            "prodavan.infrastructure.k8s.sandbox.client.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            snap = await client.wait_ready("pod-wk-demo", timeout=30.0)

    assert snap.ready is True
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_wait_ready_fails_after_missing_retries_exhausted() -> None:
    from prodavan.infrastructure.k8s.errors import K8sNotFoundError

    auth = MagicMock(spec=InClusterAuth)
    auth.api_base.return_value = "https://k8s.example"
    auth.headers.return_value = {"Authorization": "Bearer x"}
    auth.client_kwargs.return_value = {"verify": False, "timeout": 1.0}
    client = K8sSandboxClient(namespace="prodavan-sandboxes", auth=auth)

    missing = MagicMock()
    missing.status_code = 404

    with patch("prodavan.infrastructure.k8s.sandbox.client.httpx.AsyncClient") as ac:
        ac.return_value.__aenter__.return_value.get = AsyncMock(return_value=missing)
        with patch(
            "prodavan.infrastructure.k8s.sandbox.client.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            with pytest.raises(K8sNotFoundError, match="5 retries"):
                await client.wait_ready("pod-wk-demo", timeout=30.0)


def test_build_pod_body_agent_runtime() -> None:
    ctx = PodRuntimeContext(
        pod_id="pod_abc",
        project_id="prj_abc",
        company_id="cmp_abc",
        workspace_key="wk_demo",
    )
    body = build_pod_body(
        runtime_ref="pod-wk-demo",
        namespace="prodavan-sandboxes",
        context=ctx,
        image="sandbox:latest",
        hydrate_image="hydrate:latest",
        service_account="prodavan-sandbox",
        cpu_request="100m",
        cpu_limit="1",
        memory_request="256Mi",
        memory_limit="1Gi",
        agent_runtime_image="prodavan-agent-runtime:latest",
        agent_runtime_port=3921,
        agent_runtime_api_base_url="http://prodavan-api.prodavan.svc:8000/api/v1",
        agent_runtime_auth_secret="prodavan-agent-bridge",
    )
    containers = body["spec"]["containers"]
    assert len(containers) == 1
    runtime = containers[0]
    assert runtime["name"] == "agent-runtime"
    assert runtime["image"] == "prodavan-agent-runtime:latest"
    assert "workingDir" not in runtime
    runtime_env = {item["name"]: item.get("value") for item in runtime["env"]}
    assert runtime_env["PRODAVAN_PROJECT_ID"] == "prj_abc"
    assert runtime_env["PRODAVAN_POD_ID"] == "pod_abc"
    assert runtime_env["PRODAVAN_EVENTS_WRITE"] == "1"
    assert runtime_env["WORKSPACE_ROOT"] == "/workspace"
    token_env = next(e for e in runtime["env"] if e["name"] == "PRODAVAN_AUTH_TOKEN")
    assert token_env["valueFrom"]["secretKeyRef"]["name"] == "prodavan-agent-bridge"
    assert runtime["readinessProbe"]["httpGet"]["path"] == "/health"
    assert runtime_env["OPENCLAW_SESSION_MAP_PATH"] == "/workspace/.openclaw-data/session-map.json"


def test_build_pod_body_without_agent_runtime() -> None:
    ctx = PodRuntimeContext(
        pod_id="pod_abc",
        project_id="prj_abc",
        company_id="cmp_abc",
        workspace_key="wk_demo",
    )
    body = build_pod_body(
        runtime_ref="pod-wk-demo",
        namespace="prodavan-sandboxes",
        context=ctx,
        image="sandbox:latest",
        hydrate_image="hydrate:latest",
        service_account="prodavan-sandbox",
        cpu_request="100m",
        cpu_limit="1",
        memory_request="256Mi",
        memory_limit="1Gi",
    )
    assert len(body["spec"]["containers"]) == 1


def test_build_pod_body_agent_bridge_legacy_alias() -> None:
    """Legacy agent_bridge_* kwargs map to single agent-runtime container."""
    ctx = PodRuntimeContext(
        pod_id="pod_abc",
        project_id="prj_abc",
        company_id="cmp_abc",
        workspace_key="wk_demo",
    )
    body = build_pod_body(
        runtime_ref="pod-wk-demo",
        namespace="prodavan-sandboxes",
        context=ctx,
        image="sandbox:latest",
        hydrate_image="hydrate:latest",
        service_account="prodavan-sandbox",
        cpu_request="100m",
        cpu_limit="1",
        memory_request="256Mi",
        memory_limit="1Gi",
        agent_bridge_image="openclaw-bridge:latest",
        agent_bridge_port=3921,
        agent_bridge_api_base_url="http://prodavan-api.prodavan.svc:8000/api/v1",
        agent_bridge_auth_secret="prodavan-agent-bridge",
    )
    containers = body["spec"]["containers"]
    assert len(containers) == 1
    runtime = containers[0]
    assert runtime["name"] == "agent-runtime"
    assert runtime["image"] == "openclaw-bridge:latest"
    runtime_env = {item["name"]: item.get("value") for item in runtime["env"]}
    assert runtime_env["PRODAVAN_PROJECT_ID"] == "prj_abc"
    assert runtime_env["PRODAVAN_EVENTS_WRITE"] == "1"
    assert runtime_env["WORKSPACE_ROOT"] == "/workspace"
    token_env = next(e for e in runtime["env"] if e["name"] == "PRODAVAN_AUTH_TOKEN")
    assert token_env["valueFrom"]["secretKeyRef"]["name"] == "prodavan-agent-bridge"
    assert runtime["readinessProbe"]["httpGet"]["path"] == "/health"
    assert runtime_env["OPENCLAW_SESSION_MAP_PATH"] == "/workspace/.openclaw-data/session-map.json"


def test_build_pod_body_runtime_kwargs_override_bridge_alias() -> None:
    """When both agent_runtime_* and agent_bridge_* are set, runtime wins for port/api."""
    ctx = PodRuntimeContext(
        pod_id="pod_abc",
        project_id="prj_abc",
        company_id="cmp_abc",
        workspace_key="wk_demo",
    )
    body = build_pod_body(
        runtime_ref="pod-wk-demo",
        namespace="prodavan-sandboxes",
        context=ctx,
        image="sandbox:latest",
        hydrate_image="hydrate:latest",
        service_account="prodavan-sandbox",
        cpu_request="100m",
        cpu_limit="1",
        memory_request="256Mi",
        memory_limit="1Gi",
        agent_runtime_image="prodavan-agent-runtime:v2",
        agent_runtime_port=4000,
        agent_runtime_api_base_url="http://api-runtime:8000/api/v1",
        agent_runtime_auth_secret="runtime-secret",
        agent_bridge_image="openclaw-bridge:legacy",
        agent_bridge_port=3921,
        agent_bridge_api_base_url="http://api-bridge:8000/api/v1",
        agent_bridge_auth_secret="bridge-secret",
    )
    runtime = body["spec"]["containers"][0]
    assert runtime["image"] == "prodavan-agent-runtime:v2"
    runtime_env = {item["name"]: item.get("value") for item in runtime["env"]}
    assert runtime_env["PRODAVAN_API_BASE_URL"] == "http://api-runtime:8000/api/v1"
    assert runtime_env["PORT"] == "4000"
    token_env = next(e for e in runtime["env"] if e["name"] == "PRODAVAN_AUTH_TOKEN")
    assert token_env["valueFrom"]["secretKeyRef"]["name"] == "runtime-secret"
    assert runtime["readinessProbe"]["httpGet"]["port"] == 4000


def test_build_pod_body_bridge_auth_used_when_runtime_auth_missing() -> None:
    ctx = PodRuntimeContext(
        pod_id="pod_abc",
        project_id="prj_abc",
        company_id="cmp_abc",
        workspace_key="wk_demo",
    )
    body = build_pod_body(
        runtime_ref="pod-wk-demo",
        namespace="prodavan-sandboxes",
        context=ctx,
        image="sandbox:latest",
        hydrate_image="hydrate:latest",
        service_account="prodavan-sandbox",
        cpu_request="100m",
        cpu_limit="1",
        memory_request="256Mi",
        memory_limit="1Gi",
        agent_bridge_image="openclaw-bridge:latest",
        agent_bridge_auth_secret="bridge-only-secret",
    )
    token_env = next(e for e in body["spec"]["containers"][0]["env"] if e["name"] == "PRODAVAN_AUTH_TOKEN")
    assert token_env["valueFrom"]["secretKeyRef"]["name"] == "bridge-only-secret"


def test_in_cluster_auth_available(tmp_path: Path) -> None:
    auth = InClusterAuth(token_dir=tmp_path, host="10.0.0.1")
    assert auth.available() is False
    (tmp_path / "token").write_text("tok", encoding="utf-8")
    assert auth.available() is True


@pytest.mark.asyncio
async def test_k8s_client_get_pod_not_found() -> None:
    auth = MagicMock(spec=InClusterAuth)
    auth.api_base.return_value = "https://k8s.example"
    auth.headers.return_value = {"Authorization": "Bearer x"}
    auth.client_kwargs.return_value = {"verify": False, "timeout": 1.0}

    client = K8sSandboxClient(namespace="prodavan-sandboxes", auth=auth)

    mock_response = MagicMock()
    mock_response.status_code = 404

    with patch("prodavan.infrastructure.k8s.sandbox.client.httpx.AsyncClient") as ac:
        ac.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        snap = await client.get_pod("pod-missing")
    assert snap is None


def test_parse_snapshot_hydrating_init_container() -> None:
    from prodavan.infrastructure.k8s.sandbox.client import _parse_snapshot

    body = {
        "metadata": {"name": "pod-wk-demo", "labels": {}},
        "status": {
            "phase": "Pending",
            "initContainerStatuses": [
                {"name": "hydrate", "state": {"running": {"startedAt": "2026-01-01T00:00:00Z"}}},
            ],
        },
    }
    snap = _parse_snapshot(body)
    assert snap.hydrating is True
    assert snap.phase == "Pending"


@pytest.mark.asyncio
async def test_k8s_client_get_pod_metrics_not_found() -> None:
    auth = MagicMock(spec=InClusterAuth)
    auth.api_base.return_value = "https://k8s.example"
    auth.headers.return_value = {"Authorization": "Bearer x"}
    auth.client_kwargs.return_value = {"verify": False, "timeout": 1.0}
    client = K8sSandboxClient(namespace="prodavan-sandboxes", auth=auth)
    mock_response = MagicMock()
    mock_response.status_code = 404
    with patch("prodavan.infrastructure.k8s.sandbox.client.httpx.AsyncClient") as ac:
        ac.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        metrics = await client.get_pod_metrics("pod-missing")
    assert metrics is None
