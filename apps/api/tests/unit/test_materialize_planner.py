"""Unit tests for MaterializePlanner helpers."""

from prodavan.application.projects.materialize_planner import _row_matches_filter


def test_row_matches_filter_equality() -> None:
    body = {"profile_id": "p1", "block_type": "rules", "enabled": True}
    assert _row_matches_filter(body, {"block_type": "rules", "enabled": True})
    assert not _row_matches_filter(body, {"block_type": "skills"})


def test_row_matches_filter_profile_placeholder() -> None:
    body = {"profile_id": "profile_default", "block_type": "rules"}
    assert _row_matches_filter(body, {"profile_id": "profile_default", "block_type": "rules"})
