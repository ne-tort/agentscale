"""Unit tests — hydrate ownership fix for agent-runtime uid."""

from __future__ import annotations

import os
from pathlib import Path

from prodavan.runtime.hydrate import fix_workspace_ownership


def test_fix_workspace_ownership_chowns_tree(tmp_path: Path, monkeypatch) -> None:
    nested = tmp_path / ".openclaw-data" / "transcripts"
    nested.mkdir(parents=True)
    sample = nested / "a.jsonl"
    sample.write_text("x\n", encoding="utf-8")

    calls: list[tuple[str, int, int]] = []

    def fake_chown(path, uid, gid):  # noqa: ANN001
        calls.append((str(path), uid, gid))

    monkeypatch.setattr(os, "chown", fake_chown, raising=False)
    monkeypatch.setenv("WORKSPACE_UID", "1000")
    monkeypatch.setenv("WORKSPACE_GID", "1000")

    fix_workspace_ownership(tmp_path)

    assert any(p.endswith("a.jsonl") and uid == 1000 for p, uid, _gid in calls)
    assert any(Path(p) == tmp_path and uid == 1000 for p, uid, _gid in calls)
