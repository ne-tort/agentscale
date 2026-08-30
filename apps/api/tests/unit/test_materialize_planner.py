"""Unit tests for MaterializePlanner helpers."""

from prodavan.application.projects.materialize_planner import (
    _merge_materialize_rules,
    _row_applies_to_project,
    _row_matches_filter,
    _row_path_context,
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


def test_merge_materialize_rules_prefers_explicit() -> None:
    explicit = [{"id": "manual_rule", "target": {"format": "raw"}}]
    auto = [
        {"id": "manual_rule", "target": {"format": "copy_blob"}},
        {"id": "auto_suppliers_spec", "target": {"format": "copy_blob"}},
    ]
    merged = _merge_materialize_rules(explicit, auto)
    assert len(merged) == 2
    assert merged[0]["id"] == "manual_rule"
    assert merged[1]["id"] == "auto_suppliers_spec"


def test_row_path_context_extracts_filename() -> None:
    ctx = _row_path_context(
        {
            "row_id": "sup_1",
            "name": "ACME",
            "spec_file": {"filename": "spec.pdf", "storage_key": "blobs/x"},
        },
        "spec_file",
    )
    assert ctx["row_id"] == "sup_1"
    assert ctx["filename"] == "spec.pdf"
    assert ctx["name"] == "ACME"
    assert "spec_file" not in ctx
