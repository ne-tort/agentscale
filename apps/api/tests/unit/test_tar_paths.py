"""Unit tests — tar member path normalization (preserve .prodavan)."""

from __future__ import annotations

from prodavan.infrastructure.projects.tar_paths import tar_member_relpath


def test_tar_member_preserves_dot_directories() -> None:
    assert tar_member_relpath(".prodavan/config.yaml") == ".prodavan/config.yaml"
    assert tar_member_relpath("./.prodavan/config.yaml") == ".prodavan/config.yaml"
    assert tar_member_relpath(".openclaw-data/sessions.json") == ".openclaw-data/sessions.json"


def test_tar_member_rejects_parent_segments() -> None:
    assert tar_member_relpath("../evil") is None
    assert tar_member_relpath("a/../b") is None
    assert tar_member_relpath("") is None


def test_lstrip_dot_slash_bug_regression() -> None:
    """Classic bug: str.lstrip('./') turns .prodavan → prodavan."""
    broken = ".prodavan/config.yaml".lstrip("./")
    assert broken == "prodavan/config.yaml"
    assert tar_member_relpath(".prodavan/config.yaml") != broken
