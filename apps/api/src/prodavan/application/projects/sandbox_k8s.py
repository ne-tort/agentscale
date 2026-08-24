"""I8: optional in-cluster Job probe. Not invoked from project create (object-ws)."""

from __future__ import annotations

from typing import Any

import httpx

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.sandbox_jobs import (
    SANDBOX_PROBE_JOB_NAME,
    InClusterJobApi,
    pvc_probe_job_body,
)


def sandbox_k8s_status(*, api: InClusterJobApi | None = None) -> dict[str, Any]:
    client = api or InClusterJobApi()
    return {
        "enabled": bool(settings.sandbox_k8s_jobs),
        "in_cluster": client.available(),
        "namespace": settings.sandbox_k8s_namespace,
        "create_path": "object-ws",
        "isolator": "not_wired",
        "probe_job": SANDBOX_PROBE_JOB_NAME,
    }


async def run_pvc_probe_job(*, api: InClusterJobApi | None = None) -> dict[str, Any]:
    if not settings.sandbox_k8s_jobs:
        raise AppError(
            code="SANDBOX_K8S_DISABLED",
            title="Sandbox k8s jobs disabled",
            status=409,
            detail="SANDBOX_K8S_JOBS=false; project create stays object-ws",
        )
    client = api or InClusterJobApi()
    if not client.available():
        raise AppError(
            code="SANDBOX_K8S_UNAVAILABLE",
            title="Not running in-cluster",
            status=503,
            detail="ServiceAccount token / KUBERNETES_SERVICE_HOST missing",
        )
    ns = settings.sandbox_k8s_namespace
    body = pvc_probe_job_body(
        namespace=ns,
        image=settings.sandbox_k8s_job_image,
        pvc_name=settings.sandbox_k8s_pvc,
    )
    try:
        await client.delete_job(ns, SANDBOX_PROBE_JOB_NAME)
        created = await client.create_job(ns, body)
    except httpx.HTTPError as exc:
        raise AppError(
            code="SANDBOX_K8S_API_ERROR",
            title="Kubernetes Job API failed",
            status=502,
            detail=str(exc),
        ) from exc
    return {
        "ok": True,
        "job": created.get("metadata", {}).get("name", SANDBOX_PROBE_JOB_NAME),
        "create_path": "object-ws",
    }
