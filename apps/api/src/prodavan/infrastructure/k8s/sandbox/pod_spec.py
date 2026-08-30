"""Pure Pod manifest builder for project sandboxes."""

from __future__ import annotations

from typing import Any

from prodavan.domain.pods.context import PodRuntimeContext

_MANAGED_BY = "pod-service"
_WORKSPACE_MOUNT = "/workspace"


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
            "containers": [main_container],
            "volumes": [{"name": "workspace", "emptyDir": {}}],
        },
    }
