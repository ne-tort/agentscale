"""Async k8s Pod client for prodavan-sandboxes (httpx REST, no SDK)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from prodavan.infrastructure.k8s.auth import InClusterAuth
from prodavan.infrastructure.k8s.errors import (
    K8sNotFoundError,
    PermanentK8sError,
    classify_http_status,
)

logger = logging.getLogger(__name__)

_MANAGED_BY_LABEL = "prodavan.io/managed-by"
_MANAGED_BY_VALUE = "pod-service"
_READY_PHASES = frozenset({"Running", "Succeeded"})
_POD_MISSING_RETRY_COUNT = 5
_POD_MISSING_RETRY_INTERVAL_SEC = 3.0
_POLL_INTERVAL_SEC = 2.0
_FATAL_WAITING_REASONS = frozenset(
    {
        "ImagePullBackOff",
        "ErrImagePull",
        "InvalidImageName",
        "CreateContainerConfigError",
        "CreateContainerError",
        "CrashLoopBackOff",
        "RunContainerError",
        "ErrImageNeverPull",
    }
)
_FATAL_TERMINATED_REASONS = frozenset({"Error", "OOMKilled", "ContainerCannotRun"})
_PULLING_WAITING_REASONS = frozenset(
    {
        "Pulling",
        "ImagePull",
        "PodInitializing",  # common while main container image is still downloading
    }
)


@dataclass(frozen=True)
class PodSnapshot:
    name: str
    uid: str | None
    phase: str
    restarts: int
    ready: bool
    labels: dict[str, str]
    hydrate_generation: int | None = None
    bridge_generation: int | None = None
    hydrating: bool = False
    hydrate_failed: bool = False
    fatal_failure: str | None = None
    waiting_reason: str | None = None
    pulling: bool = False
    created_at: str | None = None
    started_at: str | None = None
    pod_ip: str | None = None

    def as_status_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "runtime_ref": self.name,
            "phase": self.phase,
            "uid": self.uid,
            "restarts": self.restarts,
            "ready": self.ready,
            "stub": False,
            "hydrating": self.hydrating,
            "hydrate_failed": self.hydrate_failed,
            "pulling": self.pulling,
        }
        if self.waiting_reason:
            out["waiting_reason"] = self.waiting_reason
        if self.created_at:
            out["created_at"] = self.created_at
        if self.started_at:
            out["started_at"] = self.started_at
        if self.pod_ip:
            out["pod_ip"] = self.pod_ip
        if self.fatal_failure:
            out["fatal_failure"] = self.fatal_failure
            out["last_error"] = self.fatal_failure
        return out


def _init_hydrate_state(status: dict[str, Any]) -> tuple[bool, bool]:
    """Return (hydrating, hydrate_failed) for initContainer ``hydrate``.

    Image pull for hydrate counts as pulling (not hydrating) so dual-timeout
    can extend the pull budget.
    """
    init_statuses = status.get("initContainerStatuses") or []
    for ics in init_statuses:
        if str(ics.get("name") or "") != "hydrate":
            continue
        state = ics.get("state") or {}
        waiting = state.get("waiting") or {}
        reason = str(waiting.get("reason") or "")
        if waiting and reason in _PULLING_WAITING_REASONS:
            return False, False
        if waiting or state.get("running"):
            return True, False
        terminated = state.get("terminated") or {}
        exit_code = terminated.get("exitCode")
        if exit_code is not None and int(exit_code) != 0:
            return False, True
        return False, False
    return False, False


def _pod_waiting_reason(status: dict[str, Any]) -> str | None:
    for ics in status.get("initContainerStatuses") or []:
        waiting = (ics.get("state") or {}).get("waiting") or {}
        reason = waiting.get("reason")
        if reason:
            return str(reason)
    for cs in status.get("containerStatuses") or []:
        waiting = (cs.get("state") or {}).get("waiting") or {}
        reason = waiting.get("reason")
        if reason:
            return str(reason)
    return None


def _pod_pulling(status: dict[str, Any], *, hydrating: bool) -> bool:
    if hydrating:
        return False
    reason = _pod_waiting_reason(status)
    if reason and reason in _PULLING_WAITING_REASONS:
        return True
    # No containerStatuses yet while kubelet pulls the first image.
    phase = str(status.get("phase") or "")
    if phase in {"Pending", "ContainerCreating", "PodInitializing"}:
        has_running = False
        for group in ("initContainerStatuses", "containerStatuses"):
            for cs in status.get(group) or []:
                if (cs.get("state") or {}).get("running"):
                    has_running = True
                    break
            if has_running:
                break
        if not has_running and not (status.get("containerStatuses") or []):
            return True
    return False


def _format_container_state(state: dict[str, Any] | None) -> str:
    if not state:
        return "unknown"
    if state.get("waiting"):
        w = state["waiting"]
        reason = w.get("reason") or "Waiting"
        msg = w.get("message") or ""
        return f"waiting:{reason}" + (f" ({msg})" if msg else "")
    if state.get("running"):
        return "running"
    if state.get("terminated"):
        t = state["terminated"]
        reason = t.get("reason") or "Terminated"
        exit_code = t.get("exitCode")
        msg = t.get("message") or ""
        base = f"terminated:{reason}"
        if exit_code is not None:
            base += f" exit={exit_code}"
        if msg:
            base += f" ({msg})"
        return base
    return str(state)


def _pod_fatal_failure(status: dict[str, Any]) -> str | None:
    """Return a fatal container/init state that will not self-heal without intervention."""
    for ics in status.get("initContainerStatuses") or []:
        name = str(ics.get("name") or "init")
        state = ics.get("state") or {}
        waiting = state.get("waiting") or {}
        reason = str(waiting.get("reason") or "")
        if reason in _FATAL_WAITING_REASONS:
            return f"init:{name}={_format_container_state(state)}"
        terminated = state.get("terminated") or {}
        treason = str(terminated.get("reason") or "")
        if treason in _FATAL_TERMINATED_REASONS:
            return f"init:{name}={_format_container_state(state)}"
    for cs in status.get("containerStatuses") or []:
        name = str(cs.get("name") or "container")
        state = cs.get("state") or {}
        waiting = state.get("waiting") or {}
        reason = str(waiting.get("reason") or "")
        if reason in _FATAL_WAITING_REASONS:
            return f"{name}={_format_container_state(state)}"
        terminated = state.get("terminated") or {}
        treason = str(terminated.get("reason") or "")
        if treason in _FATAL_TERMINATED_REASONS:
            return f"{name}={_format_container_state(state)}"
    return None


def _pod_diagnostics(body: dict[str, Any]) -> str:
    status = body.get("status") or {}
    parts: list[str] = [f"phase={status.get('phase') or 'Unknown'}"]
    for ics in status.get("initContainerStatuses") or []:
        name = ics.get("name") or "init"
        state = ics.get("state") or {}
        parts.append(f"init:{name}={_format_container_state(state)}")
    for cs in status.get("containerStatuses") or []:
        name = cs.get("name") or "container"
        state = cs.get("state") or {}
        restarts = cs.get("restartCount")
        detail = _format_container_state(state)
        if restarts:
            detail += f" restarts={restarts}"
        parts.append(f"{name}={detail}")
    return "; ".join(parts)


def _container_started_at(status: dict[str, Any], *, name: str = "agent-runtime") -> str | None:
    for cs in status.get("containerStatuses") or []:
        if str(cs.get("name") or "") != name:
            continue
        state = cs.get("state") or {}
        running = state.get("running") or {}
        started = running.get("startedAt")
        if started:
            return str(started)
    return None


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
    bridge_raw = labels.get("prodavan.io/bridge-generation")
    bridge_gen = int(bridge_raw) if bridge_raw is not None and str(bridge_raw).isdigit() else None
    hydrating, hydrate_failed = _init_hydrate_state(status)
    fatal_failure = _pod_fatal_failure(status)
    waiting_reason = _pod_waiting_reason(status)
    pulling = _pod_pulling(status, hydrating=hydrating)
    return PodSnapshot(
        name=str(meta.get("name") or ""),
        uid=meta.get("uid"),
        phase=str(status.get("phase") or "Unknown"),
        restarts=restarts,
        ready=ready,
        labels=labels,
        hydrate_generation=hydrate_gen,
        bridge_generation=bridge_gen,
        hydrating=hydrating,
        hydrate_failed=hydrate_failed,
        fatal_failure=fatal_failure,
        waiting_reason=waiting_reason,
        pulling=pulling,
        created_at=meta.get("creationTimestamp"),
        started_at=_container_started_at(status),
        pod_ip=status.get("podIP") or None,
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

    @property
    def auth(self) -> InClusterAuth:
        return self._auth

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

    async def _get_pod_body(self, name: str) -> dict[str, Any] | None:
        url = f"{self._auth.api_base()}/api/v1/namespaces/{self._namespace}/pods/{name}"
        async with httpx.AsyncClient(**self._auth.client_kwargs()) as client:
            response = await client.get(url, headers=self._auth.headers())
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise classify_http_status(response.status_code, response.text[:500])
            body = response.json()
            return body if isinstance(body, dict) else None

    async def wait_absent(self, name: str, *, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.get_pod(name) is None:
                return
            await asyncio.sleep(0.5)
        snap = await self.get_pod(name)
        if snap is None:
            return
        raise PermanentK8sError(f"pod {name} still terminating after {timeout}s (phase={snap.phase})")

    async def wait_exists(self, name: str, *, retries: int = _POD_MISSING_RETRY_COUNT) -> PodSnapshot:
        snap = await self._await_pod_visible(
            name,
            retries=retries,
            interval=_POD_MISSING_RETRY_INTERVAL_SEC,
        )
        if snap is None:
            raise K8sNotFoundError(
                f"pod {name} not created after {retries} retries "
                f"({_POD_MISSING_RETRY_INTERVAL_SEC}s apart)"
            )
        return snap

    async def _await_pod_visible(
        self,
        name: str,
        *,
        retries: int,
        interval: float,
    ) -> PodSnapshot | None:
        for attempt in range(retries + 1):
            snap = await self.get_pod(name)
            if snap is not None:
                return snap
            if attempt < retries:
                await asyncio.sleep(interval)
        return None

    def _raise_pod_failure(self, name: str, *, headline: str, body: dict[str, Any] | None) -> None:
        detail = _pod_diagnostics(body) if body else headline
        raise PermanentK8sError(f"pod {name} {headline}; {detail}")

    async def wait_ready(
        self,
        name: str,
        *,
        timeout: float | None = None,
        image_pull_timeout: float | None = None,
    ) -> PodSnapshot:
        """Wait until Ready.

        Dual budget: while ``pulling`` (image download) use
        ``image_pull_timeout`` from wall clock; non-pull phases accumulate
        against ``timeout`` (default 20s).
        """
        from prodavan.config.settings import settings

        ready_budget = float(
            settings.pod_ready_timeout_sec if timeout is None else timeout
        )
        pull_budget = float(
            settings.pod_image_pull_timeout_sec
            if image_pull_timeout is None
            else image_pull_timeout
        )
        started = time.monotonic()
        last_tick = started
        non_pull_elapsed = 0.0
        last: PodSnapshot | None = None
        while True:
            now = time.monotonic()
            dt = max(0.0, now - last_tick)
            last_tick = now
            snap = await self.get_pod(name)
            if snap is None:
                snap = await self._await_pod_visible(
                    name,
                    retries=_POD_MISSING_RETRY_COUNT,
                    interval=_POD_MISSING_RETRY_INTERVAL_SEC,
                )
                if snap is None:
                    raise K8sNotFoundError(
                        f"pod {name} not found during startup after "
                        f"{_POD_MISSING_RETRY_COUNT} retries "
                        f"({_POD_MISSING_RETRY_INTERVAL_SEC}s apart)"
                    )
            last = snap
            if snap.fatal_failure:
                body = await self._get_pod_body(name)
                self._raise_pod_failure(name, headline=f"cannot start: {snap.fatal_failure}", body=body)
            if snap.phase in _READY_PHASES and snap.ready:
                return snap
            if snap.phase == "Failed":
                body = await self._get_pod_body(name)
                self._raise_pod_failure(name, headline="failed", body=body)
            if snap.hydrate_failed:
                body = await self._get_pod_body(name)
                self._raise_pod_failure(name, headline="hydrate failed", body=body)

            if snap.pulling:
                if now - started > pull_budget:
                    break
            else:
                non_pull_elapsed += dt
                if non_pull_elapsed > ready_budget:
                    break
            await asyncio.sleep(_POLL_INTERVAL_SEC)

        body = await self._get_pod_body(name)
        if body:
            fatal = _pod_fatal_failure(body.get("status") or {})
            if fatal:
                self._raise_pod_failure(name, headline=f"cannot start: {fatal}", body=body)
        detail = _pod_diagnostics(body) if body else str(last)
        used = f"ready_budget={ready_budget:.0f}s pull_budget={pull_budget:.0f}s non_pull={non_pull_elapsed:.0f}s"
        raise classify_http_status(
            408, f"pod {name} not ready within timeout ({used}); {detail}"
        )

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

    async def exec_in_container(
        self,
        name: str,
        command: list[str],
        *,
        container: str = "agent-runtime",
        timeout: float = 60.0,
    ):
        from prodavan.infrastructure.k8s.sandbox.exec import exec_in_pod

        return await exec_in_pod(
            auth=self._auth,
            namespace=self._namespace,
            pod_name=name,
            command=command,
            container=container,
            timeout=timeout,
        )
