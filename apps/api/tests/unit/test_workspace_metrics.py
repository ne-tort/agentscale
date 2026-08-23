"""Workspace size helper for admin metrics (L04)."""

from __future__ import annotations

from pathlib import Path

from prodavan.infrastructure.projects.workspace import workspace_tree_bytes


def test_workspace_tree_bytes_sums_files(tmp_path: Path, monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "storage_root", tmp_path)
    root = tmp_path / "projects" / "ws_test"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "AGENTS.md").write_text("hello", encoding="utf-8")
    (root / "workspace" / "inbox").mkdir()
    (root / "workspace" / "inbox" / "a.bin").write_bytes(b"\x00" * 10)

    assert workspace_tree_bytes("ws_test") == len("hello".encode()) + 10


def test_workspace_tree_bytes_missing_returns_zero(tmp_path: Path, monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "storage_root", tmp_path)
    assert workspace_tree_bytes("missing") == 0
