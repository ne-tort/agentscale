"""Unit tests — workspace root sanitization + wipe guard (audit META-P2b)."""

from __future__ import annotations

from prodavan.application.projects.materialize_planner import _sanitize_workspace_roots


def test_sanitize_keeps_safe_relative_paths() -> None:
    assert _sanitize_workspace_roots(["a/b", "packages", "c"]) == ["a/b", "packages", "c"]


def test_sanitize_rejects_parent_escape() -> None:
    # .. must never reach wipe_prefix — it would prune outside the project workspace.
    assert _sanitize_workspace_roots(["..", "a/../b", "../x"]) == []


def test_sanitize_rejects_workspace_root() -> None:
    # ".", "./", "/" would wipe the whole project workspace.
    assert _sanitize_workspace_roots([".", "./", "/", ""]) == []


def test_sanitize_rejects_non_strings() -> None:
    assert _sanitize_workspace_roots([123, None, {"x": 1}, "ok"]) == ["ok"]


def test_sanitize_normalizes_backslashes_and_leading_slash() -> None:
    assert _sanitize_workspace_roots(["\\a\\b", "/c"]) == ["a/b", "c"]
