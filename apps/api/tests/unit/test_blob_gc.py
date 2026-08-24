"""Unit tests — orphan blob prefix GC."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.infra import blob_gc
from prodavan.core.infra.object_storage_manager import ObjectStorageManager, set_object_storage
from prodavan.core.infra.object_store_backends import LocalFsObjectStore


def test_list_child_prefixes_local(tmp_path: Path) -> None:
    store = LocalFsObjectStore(tmp_path)
    store.put_bytes("cabinet_packages/cab_live/a.zip", b"a")
    store.put_bytes("cabinet_packages/cab_dead/b.zip", b"b")
    store.put_bytes("projects/ws1/workspace/x.txt", b"x")
    kids = store.list_child_prefixes("cabinet_packages/", limit=10)
    assert "cabinet_packages/cab_live/" in kids
    assert "cabinet_packages/cab_dead/" in kids


def test_child_id_helper() -> None:
    assert blob_gc._child_id("cabinet_packages/cab_abc/", root="cabinet_packages") == "cab_abc"
    assert blob_gc._child_id("projects/ws1/", root="projects") == "ws1"
    assert blob_gc._child_id("projects/../evil/", root="projects") is None


@pytest.mark.asyncio
async def test_gc_orphan_blobs_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = ObjectStorageManager(backend="local", storage_root=tmp_path, mirror_local=False)
    mgr._primary = LocalFsObjectStore(tmp_path)
    mgr._local = LocalFsObjectStore(tmp_path)
    set_object_storage(mgr)
    mgr.put_bytes_sync("cabinet_packages/cab_orphan/a.zip", b"a")
    mgr.put_bytes_sync("projects/ws_orphan/workspace/a.txt", b"a")

    session = AsyncMock()

    cab = MagicMock()
    cab.scalars.return_value.all.return_value = ["cab_live"]
    proj = MagicMock()
    proj.scalars.return_value.all.return_value = ["ws_live"]
    session.execute = AsyncMock(side_effect=[cab, proj])

    out = await blob_gc.gc_orphan_blobs(session, dry_run=True, limit=10)
    assert "cabinet_packages/cab_orphan/" in out["cabinet_packages"]
    assert "projects/ws_orphan/" in out["projects"]
    assert out["wiped"] == []
    assert mgr.exists_sync("cabinet_packages/cab_orphan/a.zip")

    set_object_storage(None)


@pytest.mark.asyncio
async def test_gc_orphan_blobs_wipes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = ObjectStorageManager(backend="local", storage_root=tmp_path, mirror_local=False)
    mgr._primary = LocalFsObjectStore(tmp_path)
    mgr._local = LocalFsObjectStore(tmp_path)
    set_object_storage(mgr)
    mgr.put_bytes_sync("cabinet_packages/cab_orphan/a.zip", b"a")

    session = AsyncMock()
    cab = MagicMock()
    cab.scalars.return_value.all.return_value = []
    proj = MagicMock()
    proj.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(side_effect=[cab, proj])

    out = await blob_gc.gc_orphan_blobs(session, dry_run=False, limit=10)
    assert out["wiped"]
    assert out["wiped"][0]["ok"] is True
    assert not mgr.exists_sync("cabinet_packages/cab_orphan/a.zip")
    set_object_storage(None)
