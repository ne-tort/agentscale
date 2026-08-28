"""Build and (optionally) create PVC-probe Jobs via the in-cluster Kubernetes API.

Product create stays ``object-ws`` when pod_runtime_mode=stub.
Keep the Job spec in sync with ``infra/k3s/base/prodavan-sandbox/probe-job.yaml``.
"""

from __future__ import annotations

from typing import Any

import httpx

from prodavan.infrastructure.k8s.auth import InClusterAuth
from prodavan.infrastructure.k8s.errors import classify_http_status

SANDBOX_PROBE_JOB_NAME = "prodavan-sandbox-probe"
PROBE_SNIPPET = (
    "import os, pathlib\n"
    "root = pathlib.Path('/data/storage')\n"
    "assert root.is_dir(), root\n"
    "projects = root / 'projects'\n"
    "print('ok pvc', root, 'projects_exists', projects.is_dir())\n"
)


def pvc_probe_job_body(
    *,
    namespace: str,
    image: str,
    pvc_name: str,
    service_account: str = "prodavan-sandbox",
) -> dict[str, Any]:
    """Job spec: read-only mount of the API PVC (same contract as materialize mirror)."""
    labels = {
        "app.kubernetes.io/name": SANDBOX_PROBE_JOB_NAME,
        "app.kubernetes.io/part-of": "prodavan",
        "app.kubernetes.io/component": "sandbox-probe",
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": SANDBOX_PROBE_JOB_NAME,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "ttlSecondsAfterFinished": 120,
            "backoffLimit": 1,
            "activeDeadlineSeconds": 90,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": service_account,
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "probe",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["python", "-c", PROBE_SNIPPET],
                            "volumeMounts": [
                                {
                                    "name": "storage",
                                    "mountPath": "/data/storage",
                                    "readOnly": True,
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "storage",
                            "persistentVolumeClaim": {"claimName": pvc_name},
                        }
                    ],
                },
            },
        },
    }


class InClusterJobApi:
    """POST Jobs using the pod ServiceAccount token (no kubernetes SDK)."""

    def __init__(self, *, auth: InClusterAuth | None = None) -> None:
        self._auth = auth or InClusterAuth()

    def available(self) -> bool:
        return self._auth.available()

    async def create_job(self, namespace: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._auth.api_base()}/apis/batch/v1/namespaces/{namespace}/jobs"
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.post(url, headers=self._auth.headers(), json=body)
            if response.status_code >= 400:
                raise classify_http_status(response.status_code, response.text[:500])
            return response.json()

    async def delete_job(self, namespace: str, name: str) -> None:
        url = f"{self._auth.api_base()}/apis/batch/v1/namespaces/{namespace}/jobs/{name}"
        params = {"propagationPolicy": "Background"}
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.delete(url, headers=self._auth.headers(), params=params)
            if response.status_code not in (200, 202, 404):
                raise classify_http_status(response.status_code, response.text[:500])
