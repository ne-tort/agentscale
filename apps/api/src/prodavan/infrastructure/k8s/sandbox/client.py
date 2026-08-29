"""Async k8s Pod client for prodavan-sandboxes (httpx REST, no SDK)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from prodavan.infrastructure.k8s.auth import InClusterAuth
from prodavan.infrastructure.k8s.errors import K8sNotFoundError, classify_http_status

logger = logging.getLogger(__name__)

_MANAGED_BY_LABEL = "prodavan.io/managed-by"
_MANAGED_BY_VALUE = "pod-service"
_READY_PHASES = frozenset({"Running", "Succeeded"})


@dataclass(frozen=True)
class PodSnapshot:
    name: str
    uid: str | None
    phase: str
    restarts: int
    ready: bool
    labels: dict[str, str]
    hydrate_generation: int | None = None
    hydrating: bool = False
    hydrate_failed: bool = False

    def as_status_dict(self) -> dict[str, Any]:
        return {
            "runtime_ref": self.name,
            "phase": self.phase,
            "uid": self.uid,
            "restarts": self.restarts,
            "ready": self.ready,
            "stub": False,
            "hydrating": self.hydrating,
            "hydrate_failed": self.hydrate_failed,
        }


def _init_hydrate_state(status: dict[str, Any]) -> tuple[bool, bool]:
    """Return (hydrating, hydrate_failed) for initContainer ``hydrate``."""
    init_statuses = status.get("initContainerStatuses") or []
    for ics in init_statuses:
        if str(ics.get("name") or "") != "hydrate":
            continue
        state = ics.get("state") or {}
        if state.get("waiting") or state.get("running"):
            return True, False
        terminated = state.get("terminated") or {}
        exit_code = terminated.get("exitCode")
        if exit_code is not None and int(exit_code) != 0:
            return False, True
        return False, False
    return False, False


def _parse_snapshot(body: dict[str, Any]) -> PodSnapshot:
    meta = body.get("metadata") or {}
    status = body.get("status") or {}
    labels = dict(meta.get("labels") or {})
    restarts = 0
    ready = False
    for cs in status.get("containerStatuses") or []:
        restarts += int(cs.get("restartCount") or 0)
        state = cs.get("state") or {}
        if state.get("running"):
            ready = True
    conditions = status.get("conditions") or []
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            ready = True
    gen_raw = labels.get("prodavan.io/hydrate-generation")
    hydrate_gen = int(gen_raw) if gen_raw is not None and str(gen_raw).isdigit() else None
    hydrating, hydrate_failed = _init_hydrate_state(status)
    return PodSnapshot(
        name=str(meta.get("name") or ""),
        uid=meta.get("uid"),
        phase=str(status.get("phase") or "Unknown"),
        restarts=restarts,
        ready=ready,
        labels=labels,
        hydrate_generation=hydrate_gen,
        hydrating=hydrating,
        hydrate_failed=hydrate_failed,
    )


class K8sSandboxClient:
    """CRUD for Pods in a single sandbox namespace."""

    def __init__(
        self,
        *,
        namespace: str,
        auth: InClusterAuth | None = None,
    ) -> None:
        self._namespace = namespace
        self._auth = auth or InClusterAuth()

    @property
    def namespace(self) -> str:
        return self._namespace

    def available(self) -> bool:
        return self._auth.available()

    async def get_pod(self, name: str) -> PodSnapshot | None:
        url = f"{self._auth.api_base()}/api/v1/namespaces/{self._namespace}/pods/{name}"
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.get(url, headers=self._auth.headers())
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise classify_http_status(response.status_code, response.text[:500])
            return _parse_snapshot(response.json())

    async def create_pod(self, body: dict[str, Any]) -> PodSnapshot:
        url = f"{self._auth.api_base()}/api/v1/namespaces/{self._namespace}/pods"
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.post(url, headers=self._auth.headers(), json=body)
            if response.status_code == 409:
                name = (body.get("metadata") or {}).get("name")
                if name:
                    existing = await self.get_pod(str(name))
                    if existing is not None:
                        return existing
            if response.status_code >= 400:
                raise classify_http_status(response.status_code, response.text[:500])
            return _parse_snapshot(response.json())

    async def delete_pod(self, name: str, *, grace_period: int) -> None:
        url = f"{self._auth.api_base()}/api/v1/namespaces/{self._namespace}/pods/{name}"
        params = {"gracePeriodSeconds": str(grace_period)}
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.delete(url, headers=self._auth.headers(), params=params)
            if response.status_code in (200, 202, 404):
                return
            raise classify_http_status(response.status_code, response.text[:500])

    async def list_pods(self, *, label_selector: str | None = None) -> list[PodSnapshot]:
        url = f"{self._auth.api_base()}/api/v1/namespaces/{self._namespace}/pods"
        params: dict[str, str] = {}
        if label_selector:
            params["labelSelector"] = label_selector
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.get(url, headers=self._auth.headers(), params=params)
            if response.status_code >= 400:
                raise classify_http_status(response.status_code, response.text[:500])
            items = (response.json().get("items") or [])
            return [_parse_snapshot(item) for item in items]

    async def list_managed_pods(self) -> list[PodSnapshot]:
        return await self.list_pods(label_selector=f"{_MANAGED_BY_LABEL}={_MANAGED_BY_VALUE}")

    async def wait_ready(self, name: str, *, timeout: float) -> PodSnapshot:
        deadline = time.monotonic() + timeout
        last: PodSnapshot | None = None
        while time.monotonic() < deadline:
            snap = await self.get_pod(name)
            if snap is None:
                raise K8sNotFoundError(f"pod {name} disappeared while waiting")
            last = snap
            if snap.phase in _READY_PHASES and snap.ready:
                return snap
            if snap.phase == "Failed":
                raise classify_http_status(500, f"pod {name} failed")
            await asyncio.sleep(2.0)
        raise classify_http_status(408, f"pod {name} not ready within {timeout}s; last={last}")

    async def get_pod_metrics(self, name: str) -> dict[str, Any] | None:
        url = (
            f"{self._auth.api_base()}/apis/metrics.k8s.io/v1beta1"
            f"/namespaces/{self._namespace}/pods/{name}"
        )
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.get(url, headers=self._auth.headers())
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                return None
            body = response.json()
            containers = body.get("containers") or []
            cpu_nano = 0
            mem_bytes = 0
            for c in containers:
                usage = c.get("usage") or {}
                cpu_raw = str(usage.get("cpu", "0"))
                mem_raw = str(usage.get("memory", "0"))
                if cpu_raw.endswith("n"):
                    cpu_nano += int(cpu_raw[:-1])
                elif cpu_raw.endswith("m"):
                    cpu_nano += int(float(cpu_raw[:-1]) * 1_000_000)
                if mem_raw.endswith("Ki"):
                    mem_bytes += int(mem_raw[:-2]) * 1024
                elif mem_raw.endswith("Mi"):
                    mem_bytes += int(mem_raw[:-2]) * 1024 * 1024
                elif mem_raw.endswith("Gi"):
                    mem_bytes += int(mem_raw[:-2]) * 1024 * 1024 * 1024
            return {
                "cpu_millicores": max(1, cpu_nano // 1_000_000) if cpu_nano else 0,
                "memory_bytes": mem_bytes,
            }

    async def probe_metrics_server(self) -> bool:
        url = f"{self._auth.api_base()}/apis/metrics.k8s.io/v1beta1"
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.get(url, headers=self._auth.headers())
            return response.status_code == 200
