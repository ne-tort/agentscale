"""Unit tests for MaterializePlanner helpers."""

from prodavan.application.projects.materialize_planner import (
    MaterializePlanner,
    _expand_prompt_path_ops,
    _join_prompt_file_path,
    _merge_materialize_rules,
    _row_applies_to_project,
    _row_matches_filter,
    _row_path_context,
)


def test_substitute_double_brace_before_single() -> None:
    planner = MaterializePlanner(session=None)  # type: ignore[arg-type]
    out = planner._substitute("{{target_path}}", {"target_path": "assets/hello.txt"})
    assert out == "assets/hello.txt"
    out2 = planner._substitute("packages/{{name}}", {"name": "demo"})
    assert out2 == "packages/demo"
    out3 = planner._substitute("{active_profile_id}/rules", {"active_profile_id": "prof_1"})
    assert out3 == "prof_1/rules"


def test_row_matches_filter_equality() -> None:
    body = {"profile_id": "p1", "block_type": "rules", "enabled": True}
    assert _row_matches_filter(body, {"block_type": "rules", "enabled": True})
    assert not _row_matches_filter(body, {"block_type": "skills"})


def test_row_matches_filter_profile_placeholder() -> None:
    body = {"profile_id": "profile_default", "block_type": "rules"}
    assert _row_matches_filter(body, {"profile_id": "profile_default", "block_type": "rules"})


def test_row_matches_filter_bool_missing_is_false() -> None:
    from prodavan.application.projects.materialize_planner import _row_matches_filter

    assert _row_matches_filter({"status": "ready"}, {"status": "ready", "paused": False})
    assert not _row_matches_filter(
        {"status": "ready", "paused": True},
        {"status": "ready", "paused": False},
    )

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


def test_substitute_double_brace_row_fields() -> None:
    planner = MaterializePlanner(session=None)  # type: ignore[arg-type]
    assert planner._substitute("{{target_path}}", {"target_path": "assets/hello.txt"}) == "assets/hello.txt"
    assert planner._substitute("packages/{{name}}", {"name": "demo"}) == "packages/demo"


def test_substitute_single_brace_placeholders() -> None:
    planner = MaterializePlanner(session=None)  # type: ignore[arg-type]
    assert planner._substitute("{active_profile_id}", {"active_profile_id": "profile_default"}) == "profile_default"


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


def test_join_prompt_file_path() -> None:
    assert _join_prompt_file_path("", "AGENTS.md") == "AGENTS.md"
    assert _join_prompt_file_path("/", "AGENTS.md") == "AGENTS.md"
    assert _join_prompt_file_path("rules/", "style") == "rules/style.md"
    assert _join_prompt_file_path("\\skills\\", "x") == "skills/x.md"
    assert _join_prompt_file_path("/skills/", "x.md") == "skills/x.md"
    assert _join_prompt_file_path("../evil", "x") == ""
    assert _join_prompt_file_path("prompts/examples/", "sample.md") == "prompts/examples/sample.md"
    assert _join_prompt_file_path("skills", "virus.py") == "skills/virus.py.md"
    assert _join_prompt_file_path("", "prompt") == "prompt.md"


def test_expand_prompt_path_ops_skips_empty_files() -> None:
    ops = _expand_prompt_path_ops(
        rows=[
            {"row_id": "path_agents", "path": "", "files_json": []},
            {
                "row_id": "path_rules",
                "path": "rules/",
                "files_json": [
                    {"name": "style", "body": "# Style\n"},
                    {"name": "skip-me"},  # no body
                ],
            },
        ],
        rule_id="prompt_paths_files",
        module_id="mod_prompts",
        priority=10,
    )
    assert len(ops) == 1
    assert ops[0].workspace_path == "rules/style.md"
    assert ops[0].format == "raw"
    assert ops[0].row_body == {"body_md": "# Style\n"}
    assert ops[0].field == "body_md"
