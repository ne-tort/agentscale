"""Project container lifecycle hooks (L07).

Canonical contract ([14-project-containers/lifecycle.md](docs/target/14-project-containers/lifecycle.md)):
``inert`` (paused | soft_deleted) → sync workspace → **delete Pod**; resume → new Pod + hydrate.

Today ``container_ref`` is object-ws / local-ws — real k8s stop is not wired (P-POD-01),
but the return contract always advertises desired ``pod_stop=True`` so callers and
reconcile can treat inert as «no Pod».
"""

from __future__ import annotations

from typing import Any


async def pause_container(*, container_ref: str) -> dict[str, Any]:
    """Stop project compute for inert transitions; keep workspace volume/blobs.

    Desired state: Pod absent (``pod_stop=True``). Implementation is a stub until
    ContainerRuntimePort is wired — volume/object-ws is always kept.
    """
    ref = (container_ref or "").strip()
    return {
        "ok": True,
        "action": "desired_pod_absent",
        "container_ref": ref,
        "pod_stop": True,
        "pod_stopped": False,
        "runtime": "stub",
        "keep_volume": True,
    }


async def ensure_container_running(*, container_ref: str) -> dict[str, Any]:
    """Desired state after resume: Pod running + hydrate (stub until k8s wired)."""
    ref = (container_ref or "").strip()
    return {
        "ok": True,
        "action": "desired_pod_running",
        "container_ref": ref,
        "pod_start": True,
        "pod_started": False,
        "runtime": "stub",
    }


async def reconcile_container(*, container_ref: str, desired_running: bool) -> dict[str, Any]:
    """Reconcile drift: desired_running False → pause; True → ensure running."""
    if desired_running:
        return await ensure_container_running(container_ref=container_ref)
    return await pause_container(container_ref=container_ref)
