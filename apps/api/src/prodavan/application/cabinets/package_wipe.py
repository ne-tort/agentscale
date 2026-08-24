"""Cabinet MCP package blob wipe (C-OBJECT-STORE)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def wipe_cabinet_packages(cabinet_id: str) -> dict[str, Any]:
    """Delete all package zips under the cabinet prefix; verify no leftovers."""
    from prodavan.core.infra.object_keys import cabinet_packages_prefix
    from prodavan.core.infra.object_storage_manager import ensure_object_storage

    cid = (cabinet_id or "").strip()
    if not cid:
        return {"ok": False, "deleted": 0, "remaining": 0, "reason": "missing_cabinet_id"}
    prefix = cabinet_packages_prefix(cid)
    result = ensure_object_storage().delete_prefix_verified_sync(prefix)
    result["cabinet_id"] = cid
    if not result.get("ok"):
        logger.warning(
            "cabinet packages wipe incomplete cabinet_id=%s remaining=%s",
            cid,
            result.get("remaining"),
        )
    return result
