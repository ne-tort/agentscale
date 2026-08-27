"""Company object-store usage metrics (L04 / C-OBJECT-STORE)."""

from __future__ import annotations

from prodavan.core.infra.object_keys import cabinet_packages_prefix
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.projects.workspace import workspace_tree_bytes


def company_blob_storage_bytes(
    *,
    workspace_keys: list[str],
    cabinet_ids: list[str],
) -> int:
    """Sum project workspace + cabinet MCP package blob sizes for admin metrics."""
    store = ensure_file_store()
    total = sum(workspace_tree_bytes(key) for key in workspace_keys)
    for cabinet_id in cabinet_ids:
        total += store.prefix_size_sync(cabinet_packages_prefix(cabinet_id))
    return total
