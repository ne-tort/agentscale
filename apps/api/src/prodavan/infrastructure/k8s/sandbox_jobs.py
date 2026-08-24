"""Build and (optionally) create PVC-probe Jobs via the in-cluster Kubernetes API.

Product create stays ``object-ws`` — this client is not on the project path.
Keep the Job spec in sync with ``infra/k3s/base/prodavan-sandbox/probe-job.yaml``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

SANDBOX_PROBE_JOB_NAME = "prodavan-sandbox-probe"
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
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

    def __init__(
        self,
        *,
        token_dir: Path | None = None,
        host: str | None = None,
        port: str | None = None,
    ) -> None:
        self._token_dir = token_dir or SA_DIR
        self._host = host if host is not None else os.environ.get("KUBERNETES_SERVICE_HOST")
        self._port = port if port is not None else os.environ.get("KUBERNETES_SERVICE_PORT", "443")

    def available(self) -> bool:
        return bool(self._host) and (self._token_dir / "token").is_file()

    def _headers(self) -> dict[str, str]:
        token = (self._token_dir / "token").read_text(encoding="utf-8").strip()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _client_kwargs(self) -> dict[str, Any]:
        ca = self._token_dir / "ca.crt"
        return {"verify": str(ca) if ca.is_file() else False, "timeout": 30.0}

    async def create_job(self, namespace: str, body: dict[str, Any]) -> dict[str, Any]:
        url = (
            f"https://{self._host}:{self._port}"
            f"/apis/batch/v1/namespaces/{namespace}/jobs"
        )
        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            response = await client.post(url, headers=self._headers(), json=body)
            response.raise_for_status()
            return response.json()

    async def delete_job(self, namespace: str, name: str) -> None:
        url = (
            f"https://{self._host}:{self._port}"
            f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}"
        )
        params = {"propagationPolicy": "Background"}
        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            response = await client.delete(url, headers=self._headers(), params=params)
            if response.status_code not in (200, 202, 404):
                response.raise_for_status()
