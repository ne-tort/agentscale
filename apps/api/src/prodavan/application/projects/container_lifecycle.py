"""Project container lifecycle hooks (L07).

Canonical contract: project pause → pause container (stop compute, keep volume).
Today ``container_ref`` is object-ws / local-ws workspace prefix — pod stop is a hole.
"""

from __future__ import annotations

from typing import Any


async def pause_container(*, container_ref: str) -> dict[str, Any]:
    """Pause project container: stop agent compute; keep workspace volume.

    Current implementation is a documented no-op keep for object-ws / local-ws.
    Ready for a future k8s pod stop without changing ProjectService.pause callers.
    """
    ref = (container_ref or "").strip()
    return {
        "ok": True,
        "action": "keep_volume",
        "container_ref": ref,
        "pod_stop": False,
    }
