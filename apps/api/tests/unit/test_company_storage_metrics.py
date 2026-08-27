"""Unit tests — company blob storage metrics."""

from __future__ import annotations

from pathlib import Path

from prodavan.application.admin.storage_metrics import company_blob_storage_bytes
from prodavan.infrastructure.files.manager import FileStoreManager, set_file_store


def test_company_blob_storage_bytes_sums_projects_and_packages(tmp_path: Path) -> None:
    mgr = FileStoreManager(backend="local", storage_root=tmp_path)
    mgr._primary = mgr._local
    set_file_store(mgr)
    try:
        mgr.put_bytes_sync("projects/ws1/workspace/inbox/a.txt", b"abc")
        mgr.put_bytes_sync("cabinet_packages/cab1/pkg-a-1.0.0.zip", b"1234567")
        mgr.put_bytes_sync("cabinet_packages/cab2/pkg-b-2.0.0.zip", b"xy")
        total = company_blob_storage_bytes(workspace_keys=["ws1"], cabinet_ids=["cab1", "cab2"])
        assert total == 3 + 7 + 2
    finally:
        set_file_store(None)
