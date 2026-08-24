"""Project workspace blob wipe (C-OBJECT-STORE)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def wipe_project_tree(workspace_key: str) -> dict[str, Any]:
    """Delete object-store prefix for one project; verify no leftovers."""
    from prodavan.core.infra.object_keys import project_tree_prefix
    from prodavan.core.infra.object_storage_manager import ensure_object_storage

    key = (workspace_key or "").strip()
    if not key:
        return {"ok": False, "deleted": 0, "remaining": 0, "reason": "missing_workspace_key"}
    try:
        prefix = project_tree_prefix(key)
    except ValueError as exc:
        return {"ok": False, "deleted": 0, "remaining": 0, "reason": str(exc)}
    result = ensure_object_storage().delete_prefix_verified_sync(prefix)
    result["workspace_key"] = key
    if not result.get("ok"):
        logger.warning(
            "project tree wipe incomplete workspace_key=%s remaining=%s",
            key,
            result.get("remaining"),
        )
    return result
