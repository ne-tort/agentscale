"""Unit tests — object keys + local ObjectStorageManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.core.infra.object_keys import (
    cabinet_package_object_key,
    inbox_object_key,
    object_ref,
    parse_storage_ref,
)
from prodavan.core.infra.object_storage_manager import (
    ObjectStorageManager,
    set_object_storage,
)


def test_parse_storage_ref_object_and_file() -> None:
    assert parse_storage_ref("object://projects/a/workspace/inbox/x.txt") == "projects/a/workspace/inbox/x.txt"
    assert parse_storage_ref("file://projects/a/workspace/inbox/x.txt") == "projects/a/workspace/inbox/x.txt"
    assert object_ref("projects/a/b") == "object://projects/a/b"
    assert inbox_object_key(workspace_key="wk", filename="../evil.txt") == "projects/wk/workspace/inbox/evil.txt"
    assert cabinet_package_object_key(cabinet_id="c1", name="p", version="1.0.0") == (
        "cabinet_packages/c1/p-1.0.0.zip"
    )


@pytest.mark.asyncio
async def test_local_object_storage_roundtrip(tmp_path: Path) -> None:
    set_object_storage(None)
    mgr = ObjectStorageManager(backend="local", storage_root=tmp_path)
    await mgr.startup()
    key = "projects/w1/workspace/inbox/note.txt"
    await mgr.put_bytes(key, b"hello", content_type="text/plain")
    assert await mgr.exists(key)
    assert await mgr.get_bytes(key) == b"hello"
    assert (tmp_path / key).read_bytes() == b"hello"
    assert await mgr.delete(key) is True
    assert await mgr.exists(key) is False
    assert await mgr.health() is True
    await mgr.shutdown()
