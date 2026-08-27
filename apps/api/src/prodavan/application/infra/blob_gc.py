"""Garbage-collect orphan object-store prefixes (C-OBJECT-STORE / P0).

Finds ``cabinet_packages/{cabinet_id}/`` and ``projects/{workspace_key}/`` trees
with no matching live DB row (leftovers after partial hard-delete / wipe).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.content import ContentBlobVersionRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

logger = logging.getLogger(__name__)

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _child_id(prefix: str, *, root: str) -> str | None:
    """Extract first path segment under ``root/`` from a child prefix."""
    raw = (prefix or "").replace("\\", "/").strip("/")
    root = root.strip("/")
    if not raw.startswith(root + "/"):
        return None
    rest = raw[len(root) + 1 :]
    segment = rest.split("/", 1)[0]
    if not segment or not _SAFE_SEGMENT.match(segment):
        return None
    return segment


async def list_orphan_blob_prefixes(
    session: AsyncSession,
    *,
    scan_limit: int = 500,
) -> dict[str, list[str]]:
    """Return orphan package and project prefixes (not deleted)."""
    store = ensure_file_store()
    live_cabinets = {
        str(x) for x in (await session.execute(select(CabinetInstanceRow.id))).scalars().all() if x
    }
    live_workspaces = {
        str(x)
        for x in (await session.execute(select(ProjectRow.workspace_key))).scalars().all()
        if x
    }

    package_orphans: list[str] = []
    for child in store.list_child_prefixes_sync("cabinet_packages/", limit=scan_limit):
        cid = _child_id(child, root="cabinet_packages")
        if cid and cid not in live_cabinets:
            package_orphans.append(child if child.endswith("/") else f"{child.rstrip('/')}/")

    project_orphans: list[str] = []
    for child in store.list_child_prefixes_sync("projects/", limit=scan_limit):
        ws = _child_id(child, root="projects")
        if ws and ws not in live_workspaces:
            project_orphans.append(child if child.endswith("/") else f"{child.rstrip('/')}/")

    return {
        "cabinet_packages": sorted(set(package_orphans)),
        "projects": sorted(set(project_orphans)),
    }


async def list_orphan_content_blob_keys(
    session: AsyncSession,
    *,
    scan_limit: int = 5000,
) -> list[str]:
    """Return ``blobs/*`` keys with no row in ``content_blob_versions``."""
    store = ensure_file_store()
    q = await session.execute(select(ContentBlobVersionRow.storage_key))
    live = {str(k) for k in q.scalars().all() if k}
    orphans: list[str] = []
    for key in store.list_prefix_sync("blobs/", limit=scan_limit):
        if key not in live:
            orphans.append(key)
    return sorted(orphans)


async def gc_orphan_blobs(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    limit: int = 50,
    scan_limit: int = 500,
) -> dict[str, Any]:
    """Wipe orphan package/project prefixes and orphan content blob keys."""
    inventory = await list_orphan_blob_prefixes(session, scan_limit=scan_limit)
    content_orphans = await list_orphan_content_blob_keys(session, scan_limit=max(500, scan_limit * 10))
    packages = inventory["cabinet_packages"]
    projects = inventory["projects"]
    planned_prefixes = (packages + projects)[: max(0, int(limit))]
    planned_content = content_orphans[: max(0, int(limit))]
    wiped: list[dict[str, Any]] = []
    if not dry_run:
        store = ensure_file_store()
        for prefix in planned_prefixes:
            try:
                result = store.delete_prefix_verified_sync(prefix)
                wiped.append(result)
                if not result.get("ok"):
                    logger.warning("orphan blob GC incomplete prefix=%s", prefix)
            except Exception:
                logger.exception("orphan blob GC failed prefix=%s", prefix)
                wiped.append({"ok": False, "prefix": prefix, "error": "wipe_failed"})
        for key in planned_content:
            try:
                ok = store.delete_sync(key)
                wiped.append({"ok": ok, "deleted": 1 if ok else 0, "key": key, "kind": "content_blob"})
            except Exception:
                logger.exception("orphan content blob GC failed key=%s", key)
                wiped.append({"ok": False, "key": key, "kind": "content_blob", "error": "delete_failed"})
    return {
        "ok": True,
        "dry_run": dry_run,
        "orphans_found": len(packages) + len(projects) + len(content_orphans),
        "cabinet_packages": packages,
        "projects": projects,
        "content_blobs": content_orphans,
        "considered": planned_prefixes,
        "considered_content_blobs": planned_content,
        "wiped": wiped,
    }
