"""Unit tests — in-pod workspace_fs CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodavan.runtime import workspace_fs


def test_workspace_fs_list_and_read(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_fs, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / "AGENTS.md").write_text("hello agents", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "note.txt").write_text("note", encoding="utf-8")

    workspace_fs.cmd_list("")
    # cmd_list prints json — capture via re-call logic
    import io
    import sys

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        workspace_fs.cmd_list("")
    finally:
        sys.stdout = old
    body = json.loads(buf.getvalue())
    names = {e["name"] for e in body["entries"]}
    assert "AGENTS.md" in names
    assert "assets" in names

    out = io.BytesIO()

    class _Stdout:
        buffer = out

    monkeypatch.setattr(sys, "stdout", _Stdout())
    workspace_fs.cmd_read("AGENTS.md", max_bytes=1024)
    assert out.getvalue() == b"hello agents"


def test_workspace_fs_write_reads_stdin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_fs, "WORKSPACE_ROOT", tmp_path)
    import io
    import sys

    class _Stdin:
        buffer = io.BytesIO(b"hello-write")

    monkeypatch.setattr(sys, "stdin", _Stdin())
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        workspace_fs.cmd_write("inbox/note.txt")
    finally:
        sys.stdout = old
    body = json.loads(buf.getvalue())
    assert body["ok"] is True
    assert body["path"] == "inbox/note.txt"
    assert (tmp_path / "inbox" / "note.txt").read_bytes() == b"hello-write"


def test_workspace_fs_write_replaces_atomically_on_commit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_fs, "WORKSPACE_ROOT", tmp_path)
    target = tmp_path / "notes.txt"
    target.write_bytes(b"old-content")

    import io
    import sys

    class _Stdin:
        buffer = io.BytesIO(b"new-content")

    monkeypatch.setattr(sys, "stdin", _Stdin())
    original_replace = workspace_fs.os.replace

    def _fail_replace(source: str, destination: Path) -> None:
        raise OSError("simulated atomic commit failure")

    monkeypatch.setattr(workspace_fs.os, "replace", _fail_replace)
    with pytest.raises(OSError, match="simulated atomic commit failure"):
        workspace_fs.cmd_write("notes.txt")

    assert target.read_bytes() == b"old-content"
    monkeypatch.setattr(workspace_fs.os, "replace", original_replace)
    workspace_fs.cmd_write("notes.txt")
    assert target.read_bytes() == b"new-content"


def test_workspace_fs_mkdir_creates_root_and_nested_directories(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(workspace_fs, "WORKSPACE_ROOT", tmp_path)

    workspace_fs.cmd_mkdir("docs")
    workspace_fs.cmd_mkdir("docs/reference")

    assert (tmp_path / "docs").is_dir()
    assert (tmp_path / "docs" / "reference").is_dir()
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"created": True, "path": "docs/reference"}


@pytest.mark.parametrize("path", ["../outside", "%252e%252e%252fsecret", "/absolute", "docs/../../outside"])
def test_workspace_fs_mkdir_rejects_traversal(tmp_path: Path, monkeypatch, path: str) -> None:
    monkeypatch.setattr(workspace_fs, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(ValueError, match="relative"):
        workspace_fs.cmd_mkdir(path)


def test_workspace_fs_mkdir_rejects_existing_file_or_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_fs, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / "file.txt").write_text("file", encoding="utf-8")
    (tmp_path / "folder").mkdir()

    for path in ("file.txt", "folder"):
        with pytest.raises(FileExistsError):
            workspace_fs.cmd_mkdir(path)
