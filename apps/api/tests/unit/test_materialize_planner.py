"""Unit tests for MaterializePlanner helpers."""

from prodavan.application.projects.materialize_planner import (
    _row_applies_to_project,
    _row_matches_filter,
)


def test_row_matches_filter_equality() -> None:
    body = {"profile_id": "p1", "block_type": "rules", "enabled": True}
    assert _row_matches_filter(body, {"block_type": "rules", "enabled": True})
    assert not _row_matches_filter(body, {"block_type": "skills"})


def test_row_matches_filter_profile_placeholder() -> None:
    body = {"profile_id": "profile_default", "block_type": "rules"}
    assert _row_matches_filter(body, {"profile_id": "profile_default", "block_type": "rules"})


def test_row_applies_to_project_empty_means_all() -> None:
    assert _row_applies_to_project({}, "proj_a")
    assert _row_applies_to_project({"project_ids": []}, "proj_a")
    assert _row_applies_to_project({"project_ids": None}, "proj_a")


def test_row_applies_to_project_filters_by_ids() -> None:
    body = {"project_ids": ["proj_a", "proj_b"]}
    assert _row_applies_to_project(body, "proj_a")
    assert _row_applies_to_project(body, "proj_b")
    assert not _row_applies_to_project(body, "proj_c")
