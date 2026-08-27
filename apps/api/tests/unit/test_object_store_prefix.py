"""Unit tests — local object store delete_prefix."""

from __future__ import annotations

from pathlib import Path

from prodavan.infrastructure.files.local_store import LocalFileStore
from prodavan.infrastructure.files.manager import FileStoreManager, set_file_store


def test_local_delete_prefix_removes_tree(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    store.put_bytes("projects/ws1/workspace/inbox/a.txt", b"a")
    store.put_bytes("projects/ws1/workspace/out/b.txt", b"b")
    store.put_bytes("projects/ws2/workspace/inbox/c.txt", b"c")
    deleted = store.delete_prefix("projects/ws1/")
    assert deleted == 2
    assert not store.exists("projects/ws1/workspace/inbox/a.txt")
    assert store.exists("projects/ws2/workspace/inbox/c.txt")


def test_local_prefix_size_sums_bytes(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    store.put_bytes("projects/ws1/workspace/inbox/a.txt", b"abc")
    store.put_bytes("projects/ws1/workspace/out/b.txt", b"12345")
    store.put_bytes("projects/ws2/workspace/inbox/c.txt", b"c")
    assert store.prefix_size("projects/ws1/") == 8
    assert store.prefix_size("projects/ws2/") == 1


def test_local_list_prefix_lists_keys(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    store.put_bytes("cabinet_packages/cab1/a.zip", b"a")
    store.put_bytes("cabinet_packages/cab1/b.zip", b"b")
    store.put_bytes("cabinet_packages/cab2/c.zip", b"c")
    keys = sorted(store.list_prefix("cabinet_packages/cab1/", limit=10))
    assert keys == [
        "cabinet_packages/cab1/a.zip",
        "cabinet_packages/cab1/b.zip",
    ]
    assert len(store.list_prefix("cabinet_packages/cab1/", limit=1)) == 1


def test_delete_prefix_verified_reports_ok(tmp_path: Path) -> None:
    mgr = FileStoreManager(backend="local", storage_root=tmp_path, mirror_local=False)
    mgr._primary = LocalFileStore(tmp_path)
    mgr.put_bytes_sync("cabinet_packages/cab1/a.zip", b"a")
    out = mgr.delete_prefix_verified_sync("cabinet_packages/cab1/")
    assert out["ok"] is True
    assert out["deleted"] >= 1
    assert out["remaining"] == 0


def test_project_tree_prefix_and_wipe(tmp_path: Path) -> None:
    from prodavan.application.projects.project_wipe import wipe_project_tree
    from prodavan.core.infra.object_keys import project_tree_prefix

    assert project_tree_prefix("ws1") == "projects/ws1/"
    mgr = FileStoreManager(backend="local", storage_root=tmp_path, mirror_local=False)
    mgr._primary = LocalFileStore(tmp_path)
    set_file_store(mgr)
    mgr.put_bytes_sync("projects/ws1/workspace/inbox/a.txt", b"a")
    out = wipe_project_tree("ws1")
    assert out["ok"] is True
    assert out["remaining"] == 0
    set_file_store(None)
