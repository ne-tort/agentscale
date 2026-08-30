"""Unit tests — in-pod workspace_fs CLI."""

from __future__ import annotations

import json
from pathlib import Path

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
