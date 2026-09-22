"""Pure Pod manifest builder for project sandboxes."""

from __future__ import annotations

from typing import Any

from prodavan.domain.pods.context import PodRuntimeContext

_MANAGED_BY = "pod-service"
_WORKSPACE_MOUNT = "/workspace"
_AGENT_RUNTIME_CONTAINER = "agent-runtime"


def _build_agent_runtime_container(
    *,
    context: PodRuntimeContext,
    image: str,
    port: int,
    api_base_url: str,
    auth_secret_name: str | None,
    cpu_request: str,
    cpu_limit: str,
    memory_request: str,
    memory_limit: str,
    stub_holder: bool = False,
    web_search_provider: str = "",
    web_search_url: str = "",
    web_search_api_key: str = "",
) -> dict[str, Any]:
    env: list[dict[str, Any]] = [
        {"name": "WORKSPACE_ROOT", "value": _WORKSPACE_MOUNT},
        {"name": "OPENCLAW_DATA_DIR", "value": f"{_WORKSPACE_MOUNT}/.openclaw-data"},
        {"name": "PRODAVAN_API_BASE_URL", "value": api_base_url},
        {"name": "PRODAVAN_PROJECT_ID", "value": context.project_id},
        {"name": "PRODAVAN_POD_ID", "value": context.pod_id},
        {"name": "PRODAVAN_EVENTS_WRITE", "value": "1"},
        {"name": "OPENCLAW_SESSION_MAP_PATH", "value": f"{_WORKSPACE_MOUNT}/.openclaw-data/session-map.json"},
        {"name": "PORT", "value": str(port)},
        {"name": "WORKSPACE_KEY", "value": context.workspace_key},
        {"name": "PROJECT_ID", "value": context.project_id},
    ]
    for name, value in context.extra_env:
        env.append({"name": name, "value": value})
    if context.pod_auth_token:
        # Per-pod Bridge JWT is currently injected as a literal env value.
        # NOTE(audit API-P1e): a literal env leaks via `kubectl describe pod` /
        # `/proc/1/environ`. The ideal fix is a per-pod k8s Secret created by the
        # runtime adapter and referenced via secretKeyRef — tracked as a follow-up
        # because it requires per-pod Secret lifecycle in the k8s client + adapter.
        env.append({"name": "PRODAVAN_AUTH_TOKEN", "value": context.pod_auth_token})
        if auth_secret_name:
            env.append(
                {
                    "name": "BRIDGE_AUTH_TOKEN",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": auth_secret_name,
                            "key": "bridge-token",
                            "optional": True,
                        },
                    },
                },
            )
    elif auth_secret_name:
        env.append(
            {
                "name": "PRODAVAN_AUTH_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": auth_secret_name,
                        "key": "token",
                        "optional": True,
                    },
                },
            },
        )
        env.append(
            {
                "name": "BRIDGE_AUTH_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": auth_secret_name,
                        "key": "bridge-token",
                        "optional": True,
                    },
                },
            },
        )
    # CLAW-WEB — web.search provider config (passed to the built-in web.search
    # tool in the agent-runtime container). SearxNG is the default self-hosted
    # provider; empty values leave the tool disabled (stub error).
    if web_search_provider:
        env.append({"name": "OPENCLAW_WEB_SEARCH_PROVIDER", "value": web_search_provider})
    if web_search_url:
        env.append({"name": "OPENCLAW_WEB_SEARCH_URL", "value": web_search_url})
    if web_search_api_key:
        env.append({"name": "OPENCLAW_WEB_SEARCH_API_KEY", "value": web_search_api_key})
    container: dict[str, Any] = {
        "name": _AGENT_RUNTIME_CONTAINER,
        "image": image,
        "imagePullPolicy": "Always",
        "env": env,
        "volumeMounts": [{"name": "workspace", "mountPath": _WORKSPACE_MOUNT}],
        "resources": {
            "requests": {"cpu": cpu_request, "memory": memory_request},
            "limits": {"cpu": cpu_limit, "memory": memory_limit},
        },
    }
    if stub_holder:
        container["workingDir"] = _WORKSPACE_MOUNT
        container["command"] = ["sleep", "infinity"]
        container["readinessProbe"] = {
            "exec": {"command": ["test", "-d", _WORKSPACE_MOUNT]},
            "initialDelaySeconds": 2,
            "periodSeconds": 5,
            "failureThreshold": 6,
        }
    else:
        container["ports"] = [{"name": "http", "containerPort": port}]
        container["readinessProbe"] = {
            "httpGet": {"path": "/health", "port": port},
            "initialDelaySeconds": 3,
            "periodSeconds": 5,
            "failureThreshold": 12,
        }
        container["livenessProbe"] = {
            "httpGet": {"path": "/health", "port": port},
            "initialDelaySeconds": 10,
            "periodSeconds": 20,
            "failureThreshold": 6,
        }
    return container


def build_pod_body(
    *,
    runtime_ref: str,
    namespace: str,
    context: PodRuntimeContext,
    image: str,
    hydrate_image: str,
    service_account: str,
    cpu_request: str,
    cpu_limit: str,
    memory_request: str,
    memory_limit: str,
    minio_secret_name: str | None = None,
    agent_runtime_image: str | None = None,
    agent_runtime_port: int = 3921,
    agent_runtime_api_base_url: str = "http://prodavan-api.prodavan.svc:8001/api/v1",
    agent_runtime_auth_secret: str | None = None,
    agent_runtime_web_search_provider: str = "",
    agent_runtime_web_search_url: str = "",
    agent_runtime_web_search_api_key: str = "",
    # Legacy aliases (deprecated)
    agent_bridge_image: str | None = None,
    agent_bridge_port: int = 3921,
    agent_bridge_api_base_url: str = "http://prodavan-api.prodavan.svc:8001/api/v1",
    agent_bridge_auth_secret: str | None = None,
    image_pull_secret: str | None = None,
) -> dict[str, Any]:
    runtime_image = agent_runtime_image or agent_bridge_image
    if agent_runtime_image is not None:
        runtime_port = agent_runtime_port
        runtime_api = agent_runtime_api_base_url
    else:
        runtime_port = agent_bridge_port
        runtime_api = agent_bridge_api_base_url
    runtime_auth = agent_runtime_auth_secret or agent_bridge_auth_secret
    labels = {
        "app.kubernetes.io/part-of": "prodavan",
        "app.kubernetes.io/component": "project-pod",
        "prodavan.io/managed-by": _MANAGED_BY,
        "prodavan.io/pod-id": context.pod_id,
        "prodavan.io/project-id": context.project_id,
        "prodavan.io/company-id": context.company_id,
        "prodavan.io/workspace-key": context.workspace_key,
        "prodavan.io/hydrate-generation": str(context.hydrate_generation),
    }
    init_env: list[dict[str, Any]] = [
        {"name": "WORKSPACE_KEY", "value": context.workspace_key},
        {"name": "HYDRATE_TARGET", "value": _WORKSPACE_MOUNT},
        {"name": "PRODAVAN_POD_ID", "value": context.pod_id},
        {"name": "PRODAVAN_API_BASE_URL", "value": runtime_api},
    ]
    # Prefer API-mediated hydrate (Bridge JWT). MinIO IAM only as explicit legacy fallback
    # when no pod token is minted (local/stub without Bridge).
    if context.pod_auth_token:
        init_env.append({"name": "PRODAVAN_AUTH_TOKEN", "value": context.pod_auth_token})
    elif minio_secret_name:
        init_env.extend(
            [
                {
                    "name": "MINIO_ACCESS_KEY",
                    "valueFrom": {"secretKeyRef": {"name": minio_secret_name, "key": "access-key"}},
                },
                {
                    "name": "MINIO_SECRET_KEY",
                    "valueFrom": {"secretKeyRef": {"name": minio_secret_name, "key": "secret-key"}},
                },
                {
                    "name": "MINIO_ENDPOINT",
                    "valueFrom": {"secretKeyRef": {"name": minio_secret_name, "key": "endpoint"}},
                },
                {
                    "name": "MINIO_BUCKET",
                    "valueFrom": {"secretKeyRef": {"name": minio_secret_name, "key": "bucket"}},
                },
            ]
        )
    init_container = {
        "name": "hydrate",
        "image": hydrate_image,
        "imagePullPolicy": "Always",
        "command": ["python", "-m", "prodavan.runtime.hydrate"],
        "env": init_env,
        "volumeMounts": [{"name": "workspace", "mountPath": _WORKSPACE_MOUNT}],
    }
    containers: list[dict[str, Any]] = [
        _build_agent_runtime_container(
            context=context,
            image=runtime_image or image,
            port=runtime_port,
            api_base_url=runtime_api,
            auth_secret_name=runtime_auth,
            cpu_request=cpu_request,
            cpu_limit=cpu_limit,
            memory_request=memory_request,
            memory_limit=memory_limit,
            stub_holder=runtime_image is None,
            web_search_provider=agent_runtime_web_search_provider,
            web_search_url=agent_runtime_web_search_url,
            web_search_api_key=agent_runtime_web_search_api_key,
        ),
    ]
    spec: dict[str, Any] = {
            "serviceAccountName": service_account,
            "restartPolicy": "Never",
            "initContainers": [init_container],
            "containers": containers,
            "volumes": [{"name": "workspace", "emptyDir": {}}],
        }
    if image_pull_secret:
        spec["imagePullSecrets"] = [{"name": image_pull_secret}]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": runtime_ref,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": spec,
    }
