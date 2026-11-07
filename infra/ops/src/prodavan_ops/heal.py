from __future__ import annotations

import json
import time
from typing import Iterable

from prodavan_ops.dns_check import DnsCheckError, check_dns
from prodavan_ops.k8s import kubectl, require_k8s_api, wait_k3s_api_ready
from prodavan_ops.paths import overlay_dev
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


def reconcile_ingress_route() -> None:
    """Force Traefik to rebuild its config for the dev Ingress objects.

    Traefik drops Ingress path routes that pointed at a Service which had no
    endpoints at the moment it rebuilt its configuration, and does not re-add
    them afterwards — the object in the API server stays correct while the live
    router keeps sending `/api` to the web container. Re-applying the Ingress
    makes the provider recompute it.

    This ran for real: after an API rollout, `https://agentscale.dev` loaded the
    SPA and `POST /api/v1/auth/login` returned nginx's 405, while `:8088` worked.
    The annotations were already correct and changing them no longer helped;
    re-applying the object did.

    The descriptor comes from the repository's own overlay, so what gets applied
    is what Argo would apply — not a snapshot that could carry cluster drift.
    """
    rendered = kubectl(["kustomize", str(overlay_dev())], retries=1)
    if rendered.returncode != 0:
        err = (rendered.stderr or rendered.stdout or "").strip()
        print(f"ingress reconcile skipped: render failed ({err.splitlines()[-1] if err else '?'})")
        return

    docs = [doc for doc in (rendered.stdout or "").split("\n---") if "kind: Ingress" in doc]
    if not docs:
        print("ingress reconcile skipped: overlay renders no Ingress")
        return

    proc = kubectl(
        ["apply", "-f", "-"],
        retries=1,
        subprocess_timeout=40.0,
        stdin_text="\n---".join(docs),
    )
    if proc.returncode == 0:
        print(f"ingress re-applied: {(proc.stdout or '').strip()}")
    else:
        err = (proc.stderr or proc.stdout or "").strip()
        print(f"ingress reconcile failed: {err.splitlines()[-1] if err else proc.returncode}")


def heal_cluster(
    *,
    wait_node_sec: int = 180,
    smoke_attempts: int = 24,
    smoke_sleep_sec: float = 5.0,
    skip_traefik: bool = False,
    skip_smoke: bool = False,
    skip_dns: bool = False,
) -> None:
    """Recover dev cluster after reboot: API stuck pods, Traefik hostPort, DNS, smoke."""
    wait_k3s_api_ready(timeout_sec=min(60, wait_node_sec))
    require_k8s_api(timeout_sec=30)
    wait_node_ready(timeout_sec=wait_node_sec)
    n = delete_stuck_pods()
    print(f"heal: removed {n} stuck pod(s)")
    if not skip_traefik:
        recycle_traefik()
        time.sleep(5)
    # Re-apply the Ingress AFTER anything that recreates the API pod: the router
    # only breaks while the Service has no endpoints, which is exactly the window
    # a recycle opens. Doing this unconditionally is cheap (one server-side
    # apply) and removes a class of "site loads, login 405s" failures nobody
    # would think to attribute to a rollout.
    reconcile_ingress_route()
    # DNS ПОСЛЕ подов, но ДО smoke: после рестарта хоста upstream-резолвер может
    # не подняться, и тогда поды живые, HTTP-пробы зелёные, а внешние имена не
    # резолвятся — агент «не может достучаться до провайдера». Ровно это и
    # случилось на prod 2026-10-22. Падение здесь честнее молчаливого «OK».
    if skip_dns:
        print("heal: dns-check skipped")
    else:
        try:
            resolved = check_dns()
            print(f"heal: dns OK {resolved}")
        except DnsCheckError as exc:
            # Не роняем весь heal: остальные шаги (smoke) полезны и при мёртвом
            # DNS — они показывают, что приложение живо. Но диагноз печатаем
            # целиком, с инструкцией, что чинить.
            print(f"heal: DNS ПРОБЛЕМА\n{exc}")
    if skip_smoke:
        print("heal: smoke skipped")
        return
    smoke(attempts=smoke_attempts, sleep_sec=smoke_sleep_sec, connect_fail_limit=6)
    print("heal OK")