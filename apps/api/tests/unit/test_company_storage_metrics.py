"""Unit tests — company blob storage metrics."""

from __future__ import annotations

from pathlib import Path

from prodavan.application.admin.storage_metrics import company_blob_storage_bytes
from prodavan.core.infra.object_storage_manager import ObjectStorageManager, set_object_storage


def test_company_blob_storage_bytes_sums_projects_and_packages(tmp_path: Path) -> None:
    mgr = ObjectStorageManager(backend="local", storage_root=tmp_path)
    mgr._primary = mgr._local
    set_object_storage(mgr)
    try:
        mgr.put_bytes_sync("projects/ws1/workspace/inbox/a.txt", b"abc")
        mgr.put_bytes_sync("cabinet_packages/cab1/pkg-a-1.0.0.zip", b"1234567")
        mgr.put_bytes_sync("cabinet_packages/cab2/pkg-b-2.0.0.zip", b"xy")
        total = company_blob_storage_bytes(workspace_keys=["ws1"], cabinet_ids=["cab1", "cab2"])
        assert total == 3 + 7 + 2
    finally:
        set_object_storage(None)
