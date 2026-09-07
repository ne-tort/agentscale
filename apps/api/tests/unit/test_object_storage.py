"""Unit tests — object keys + local ObjectStorageManager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodavan.core.infra.object_keys import (
    cabinet_package_object_key,
    inbox_object_key,
    object_ref,
    parse_storage_ref,
    workspace_object_key,
)
from prodavan.infrastructure.files.manager import FileStoreManager, set_file_store
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter


def test_parse_storage_ref_object_and_file() -> None:
    assert parse_storage_ref("object://projects/a/workspace/inbox/x.txt") == "projects/a/workspace/inbox/x.txt"
    assert parse_storage_ref("file://projects/a/workspace/inbox/x.txt") == "projects/a/workspace/inbox/x.txt"
    assert object_ref("projects/a/b") == "object://projects/a/b"
    assert inbox_object_key(workspace_key="wk", filename="../evil.txt") == "projects/wk/workspace/inbox/evil.txt"
    assert workspace_object_key(workspace_key="wk", relative_path="AGENTS.md") == (
        "projects/wk/workspace/AGENTS.md"
    )
    assert cabinet_package_object_key(cabinet_id="c1", name="p", version="1.0.0") == (
        "cabinet_packages/c1/p-1.0.0.zip"
    )


@pytest.mark.asyncio
async def test_local_object_storage_roundtrip(tmp_path: Path) -> None:
    set_file_store(None)
    mgr = FileStoreManager(backend="local", storage_root=tmp_path)
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


@pytest.mark.asyncio
async def test_workspace_writer_agents_via_object_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_file_store(None)
    mgr = FileStoreManager(backend="local", storage_root=tmp_path)
    await mgr.startup()
    monkeypatch.setattr("prodavan.infrastructure.projects.workspace.settings.storage_root", tmp_path)
    writer = WorkspaceLayoutWriter(workspace_key="wk1")
    writer.ensure_dirs()
    writer.write_agents(cabinet_name="Cab", project_name="Proj", agents_md="# hello\n")
    agents_key = "projects/wk1/workspace/AGENTS.md"
    assert await mgr.get_bytes(agents_key) == b"# hello\n"
    assert (tmp_path / agents_key).read_text(encoding="utf-8") == "# hello\n"
    assert not (tmp_path / "projects/wk1/workspace/CLAUDE.md").exists()
    assert not (tmp_path / "projects/wk1/workspace/rules").exists()
    assert not (tmp_path / "projects/wk1/workspace/skills").exists()
    writer.write_mcp_config(cabinet_id="cab_1", packages=[])
    mcp = json.loads((tmp_path / "projects/wk1/workspace/mcp.json").read_text(encoding="utf-8"))
    assert mcp["platform"]["cabinet_id"] == "cab_1"
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_ensure_package_tree_hydrates_from_object_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zipfile
    from io import BytesIO

    set_file_store(None)
    mgr = FileStoreManager(backend="local", storage_root=tmp_path)
    await mgr.startup()
    monkeypatch.setattr("prodavan.infrastructure.projects.workspace.settings.storage_root", tmp_path)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tool.py", "print('ok')\n")
    zip_bytes = buf.getvalue()
    await mgr.put_bytes("projects/wk2/workspace/packages/demo.zip", zip_bytes, content_type="application/zip")
    writer = WorkspaceLayoutWriter(workspace_key="wk2")
    writer.ensure_dirs()
    assert writer.ensure_package_tree("demo") is True
    assert (tmp_path / "projects/wk2/workspace/packages/demo/tool.py").read_text(encoding="utf-8") == (
        "print('ok')\n"
    )
    assert writer.ensure_package_tree("missing") is False
    await mgr.shutdown()
