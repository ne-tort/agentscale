"""Pure Pod manifest builder for project sandboxes."""

from __future__ import annotations

from typing import Any

from prodavan.domain.pods.context import PodRuntimeContext

_MANAGED_BY = "pod-service"
_WORKSPACE_MOUNT = "/workspace"
_AGENT_BRIDGE_CONTAINER = "agent-bridge"


def _build_agent_bridge_container(
    *,
    context: PodRuntimeContext,
    image: str,
    port: int,
    api_base_url: str,
    auth_secret_name: str | None,
) -> dict[str, Any]:
    env: list[dict[str, Any]] = [
        {"name": "WORKSPACE_ROOT", "value": _WORKSPACE_MOUNT},
        {"name": "OPENCLAW_DATA_DIR", "value": f"{_WORKSPACE_MOUNT}/.openclaw-data"},
        {"name": "PRODAVAN_API_BASE_URL", "value": api_base_url},
        {"name": "PRODAVAN_PROJECT_ID", "value": context.project_id},
        {"name": "PRODAVAN_EVENTS_WRITE", "value": "1"},
        {"name": "OPENCLAW_SESSION_MAP_PATH", "value": f"{_WORKSPACE_MOUNT}/.openclaw-data/session-map.json"},
        {"name": "PORT", "value": str(port)},
    ]
    if auth_secret_name:
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
    return {
        "name": _AGENT_BRIDGE_CONTAINER,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "workingDir": _WORKSPACE_MOUNT,
        "env": env,
        "ports": [{"name": "http", "containerPort": port}],
        "volumeMounts": [{"name": "workspace", "mountPath": _WORKSPACE_MOUNT}],
        "resources": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "readinessProbe": {
            "httpGet": {"path": "/health", "port": port},
            "initialDelaySeconds": 3,
            "periodSeconds": 5,
            "failureThreshold": 12,
        },
        "livenessProbe": {
            "httpGet": {"path": "/health", "port": port},
            "initialDelaySeconds": 10,
            "periodSeconds": 20,
            "failureThreshold": 6,
        },
    }


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
    agent_bridge_image: str | None = None,
    agent_bridge_port: int = 3921,
    agent_bridge_api_base_url: str = "http://prodavan-api.prodavan.svc:8000/api/v1",
    agent_bridge_auth_secret: str | None = None,
) -> dict[str, Any]:
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
    ]
    if minio_secret_name:
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
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "-m", "prodavan.runtime.hydrate"],
        "env": init_env,
        "volumeMounts": [{"name": "workspace", "mountPath": _WORKSPACE_MOUNT}],
    }
    main_env: list[dict[str, Any]] = [
        {"name": "WORKSPACE_KEY", "value": context.workspace_key},
        {"name": "PROJECT_ID", "value": context.project_id},
    ]
    for name, value in context.extra_env:
        main_env.append({"name": name, "value": value})
    main_container = {
        "name": "sandbox",
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        # API image defaults to uvicorn; sandbox Pod is an agent workspace holder only.
        "command": ["sleep", "infinity"],
        "workingDir": _WORKSPACE_MOUNT,
        "env": main_env,
        "volumeMounts": [{"name": "workspace", "mountPath": _WORKSPACE_MOUNT}],
        "resources": {
            "requests": {"cpu": cpu_request, "memory": memory_request},
            "limits": {"cpu": cpu_limit, "memory": memory_limit},
        },
        "readinessProbe": {
            "exec": {"command": ["test", "-d", _WORKSPACE_MOUNT]},
            "initialDelaySeconds": 2,
            "periodSeconds": 5,
            "failureThreshold": 6,
        },
    }
    containers: list[dict[str, Any]] = [main_container]
    if agent_bridge_image:
        containers.append(
            _build_agent_bridge_container(
                context=context,
                image=agent_bridge_image,
                port=agent_bridge_port,
                api_base_url=agent_bridge_api_base_url,
                auth_secret_name=agent_bridge_auth_secret,
            ),
        )
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": runtime_ref,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "serviceAccountName": service_account,
            "restartPolicy": "Never",
            "initContainers": [init_container],
            "containers": containers,
            "volumes": [{"name": "workspace", "emptyDir": {}}],
        },
    }
