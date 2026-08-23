"""Unit tests — local object store delete_prefix."""

from __future__ import annotations

from pathlib import Path

from prodavan.core.infra.object_store_backends import LocalFsObjectStore


def test_local_delete_prefix_removes_tree(tmp_path: Path) -> None:
    store = LocalFsObjectStore(tmp_path)
    store.put_bytes("projects/ws1/workspace/inbox/a.txt", b"a")
    store.put_bytes("projects/ws1/workspace/out/b.txt", b"b")
    store.put_bytes("projects/ws2/workspace/inbox/c.txt", b"c")
    deleted = store.delete_prefix("projects/ws1/")
    assert deleted == 2
    assert not store.exists("projects/ws1/workspace/inbox/a.txt")
    assert store.exists("projects/ws2/workspace/inbox/c.txt")


def test_local_prefix_size_sums_bytes(tmp_path: Path) -> None:
    store = LocalFsObjectStore(tmp_path)
    store.put_bytes("projects/ws1/workspace/inbox/a.txt", b"abc")
    store.put_bytes("projects/ws1/workspace/out/b.txt", b"12345")
    store.put_bytes("projects/ws2/workspace/inbox/c.txt", b"c")
    assert store.prefix_size("projects/ws1/") == 8
    assert store.prefix_size("projects/ws2/") == 1
