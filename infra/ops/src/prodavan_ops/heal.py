from __future__ import annotations

import json
import time
from typing import Iterable

from prodavan_ops.k8s import kubectl, require_k8s_api, wait_k3s_api_ready
from prodavan_ops.smoke import smoke

_HEAL_NAMESPACES = ("agentscale-dev", "agentscale-dev-sandboxes")
_STUCK_PHASES = frozenset({"Unknown", "Failed"})
_STUCK_CONTAINER_REASONS = frozenset({"Init:Error", "Error", "CrashLoopBackOff"})


def _pod_stuck(item: dict) -> bool:
    meta = item.get("metadata") or {}
    if meta.get("deletionTimestamp"):
        return True
    status = item.get("status") or {}
    phase = status.get("phase") or ""
    if phase in _STUCK_PHASES:
        return True
    for cs in status.get("containerStatuses") or []:
        state = cs.get("state") or {}
        waiting = state.get("waiting") or {}
        if waiting.get("reason") in _STUCK_CONTAINER_REASONS:
            return True
    for cs in status.get("initContainerStatuses") or []:
        state = cs.get("state") or {}
        waiting = state.get("waiting") or {}
        terminated = state.get("terminated") or {}
        if waiting.get("reason") in _STUCK_CONTAINER_REASONS:
            return True
        if terminated.get("exitCode", 0) != 0 and phase == "Pending":
            return True
    return False


def list_stuck_pods(namespace: str) -> list[str]:
    """Return pod names that block rollouts or clutter sandboxes after node flaps."""
    proc = kubectl(
        ["get", "pods", "-n", namespace, "-o", "json"],
        retries=4,
        retry_sleep=2.0,
        subprocess_timeout=45.0,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        print(f"list pods skipped {namespace}: {err.splitlines()[-1] if err else proc.returncode}")
        return []
    data = json.loads(proc.stdout or "{}")
    names: list[str] = []
    for item in data.get("items") or []:
        meta = item.get("metadata") or {}
        name = meta.get("name")
        if name and _pod_stuck(item):
            names.append(name)
    return names


def delete_stuck_pods(
    namespaces: Iterable[str] = _HEAL_NAMESPACES,
    *,
    grace_period: int = 0,
) -> int:
    """Force-delete Terminating / Unknown / Failed pods so Recreate deployments can progress."""
    deleted = 0
    for ns in namespaces:
        for name in list_stuck_pods(ns):
            proc = kubectl(
                [
                    "delete",
                    "pod",
                    name,
                    "-n",
                    ns,
                    "--grace-period",
                    str(grace_period),
                    "--force",
                    "--wait=false",
                ],
                retries=3,
                retry_sleep=1.5,
            )
            if proc.returncode == 0:
                deleted += 1
                print(f"deleted stuck pod {ns}/{name}")
            else:
                err = (proc.stderr or proc.stdout or "").strip()
                print(f"delete failed {ns}/{name}: {err.splitlines()[-1] if err else proc.returncode}")
    return deleted


def wait_node_ready(*, timeout_sec: int = 180) -> None:
    """Block until at least one node reports Ready (post-reboot CNI/kubelet)."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        proc = kubectl(["get", "nodes", "-o", "json"], retries=3, retry_sleep=2.0)
        if proc.returncode == 0:
            data = json.loads(proc.stdout or "{}")
            for item in data.get("items") or []:
                for cond in (item.get("status") or {}).get("conditions") or []:
                    if cond.get("type") == "Ready" and cond.get("status") == "True":
                        name = (item.get("metadata") or {}).get("name", "?")
                        print(f"node ready: {name}")
                        return
        print("waiting for node Ready...")
        time.sleep(3)
    raise TimeoutError(f"no Ready node within {timeout_sec}s")


def recycle_traefik() -> None:
    """Recreate Traefik so hostPort :8088 binds again after NodeNotReady flaps."""
    proc = kubectl(
        [
            "delete",
            "pod",
            "-n",
            "kube-system",
            "-l",
            "app.kubernetes.io/name=traefik",
            "--wait=false",
        ],
        retries=3,
        retry_sleep=2.0,
    )
    if proc.returncode == 0:
        print("traefik pod recycle requested")
    else:
        err = (proc.stderr or proc.stdout or "").strip()
        print(f"traefik recycle skipped: {err.splitlines()[-1] if err else proc.returncode}")


def heal_cluster(
    *,
    wait_node_sec: int = 180,
    smoke_attempts: int = 24,
    smoke_sleep_sec: float = 5.0,
    skip_traefik: bool = False,
    skip_smoke: bool = False,
) -> None:
    """Recover dev cluster after WSL reboot: API stuck pods, Traefik hostPort, smoke."""
    wait_k3s_api_ready(timeout_sec=min(60, wait_node_sec))
    require_k8s_api(timeout_sec=30)
    wait_node_ready(timeout_sec=wait_node_sec)
    n = delete_stuck_pods()
    print(f"heal: removed {n} stuck pod(s)")
    if not skip_traefik:
        recycle_traefik()
        time.sleep(5)
    if skip_smoke:
        print("heal: smoke skipped")
        return
    smoke(attempts=smoke_attempts, sleep_sec=smoke_sleep_sec, connect_fail_limit=6)
    print("heal OK")
