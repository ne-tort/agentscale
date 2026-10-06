"""Unit tests for MaterializePlanner helpers."""

from prodavan.application.projects.materialize_planner import (
    MaterializePlanner,
    _expand_prompt_path_ops,
    _join_prompt_file_path,
    _merge_materialize_rules,
    _pick_active_profile,
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
    # META-P1c: single-brace {var} is no longer a placeholder. It is left
    # verbatim so meta authors cannot rely on the deprecated fragile dialect
    # that matched the inner braces of {{target_path}}.
    out3 = planner._substitute("{{active_profile_id}}/rules", {"active_profile_id": "prof_1"})
    assert out3 == "prof_1/rules"
    out4 = planner._substitute("{active_profile_id}/rules", {"active_profile_id": "prof_1"})
    assert out4 == "{active_profile_id}/rules"


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
    # META-P1c: single-brace {var} is deprecated and no longer substituted.
    # Only {{var}} resolves; {var} is left verbatim (use {{active_profile_id}}).
    assert (
        planner._substitute("{active_profile_id}", {"active_profile_id": "profile_default"})
        == "{active_profile_id}"
    )
    assert (
        planner._substitute("{{active_profile_id}}", {"active_profile_id": "profile_default"})
        == "profile_default"
    )


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
                    {"name": "style", "body": "# Style\n", "priority": 100},
                    {"name": "skip-me"},  # no body
                ],
            },
        ],
        rule_id="prompt_paths_files",
        module_id="mod_prompts",
    )
    assert len(ops) == 1
    assert ops[0].workspace_path == "rules/style.md"
    assert ops[0].format == "prompt_fragment"
    assert ops[0].priority == 100
    assert ops[0].row_body == {"body_md": "# Style\n", "fragment_id": "prompt_paths_files_1_0"}
    assert ops[0].field == "body_md"


def test_expand_prompt_path_ops_reads_per_file_priority() -> None:
    from prodavan.application.projects.materialize_planner import _stitch_prompt_fragment_ops

    fragments = _expand_prompt_path_ops(
        rows=[
            {
                "row_id": "path_root",
                "path": "",
                "files_json": [
                    {"id": "f2", "name": "AGENTS", "body": "later", "priority": 200},
                    {"id": "f1", "name": "AGENTS", "body": "earlier", "priority": 100},
                ],
            },
        ],
        rule_id="prompt_paths_files",
        module_id="mod_prompts",
    )
    assert {o.priority for o in fragments} == {100, 200}
    stitched = _stitch_prompt_fragment_ops(fragments)
    assert len(stitched) == 1
    assert stitched[0].format == "raw"
    assert stitched[0].workspace_path == "AGENTS.md"
    assert stitched[0].priority == 100
    assert stitched[0].row_body == {"body_md": "earlier\n\nlater"}


def test_stitch_prompt_fragments_cross_module() -> None:
    from prodavan.application.projects.materialize_planner import (
        MaterializeOp,
        _stitch_prompt_fragment_ops,
    )

    ops = [
        MaterializeOp(
            rule_id="a",
            module_id="mod_a",
            workspace_path="AGENTS.md",
            format="prompt_fragment",
            source_type="rows",
            priority=100,
            row_body={"body_md": "from A", "fragment_id": "a"},
            field="body_md",
        ),
        MaterializeOp(
            rule_id="b",
            module_id="mod_b",
            workspace_path="AGENTS.md",
            format="prompt_fragment",
            source_type="rows",
            priority=101,
            row_body={"body_md": "from B", "fragment_id": "b"},
            field="body_md",
        ),
        MaterializeOp(
            rule_id="other",
            module_id="mod_x",
            workspace_path="other.txt",
            format="raw",
            source_type="static",
            priority=50,
            static_value="keep",
        ),
    ]
    out = _stitch_prompt_fragment_ops(ops)
    by_path = {o.workspace_path: o for o in out}
    assert set(by_path) == {"AGENTS.md", "other.txt"}
    assert by_path["AGENTS.md"].format == "raw"
    assert by_path["AGENTS.md"].row_body == {"body_md": "from A\n\nfrom B"}
    assert by_path["other.txt"].static_value == "keep"


def test_pick_active_profile_filters_by_project_ids() -> None:
    rows = [
        {
            "row_id": "prof_a",
            "body": {"name": "A", "project_ids": ["proj1"], "is_default": True},
        },
        {
            "row_id": "prof_b",
            "body": {"name": "B", "project_ids": ["proj2"], "is_default": True},
        },
        {
            "row_id": "prof_all",
            "body": {"name": "All", "project_ids": [], "is_default": False},
        },
    ]
    assert _pick_active_profile(rows, "proj1") == "prof_a"
    assert _pick_active_profile(rows, "proj2") == "prof_b"
    # Empty project_ids = all projects; no is_default among matches that prefer
    # explicit defaults first — prof_a/prof_b win when listed; for proj3 only
    # prof_all matches.
    assert _pick_active_profile(rows, "proj3") == "prof_all"


def test_merge_mapped_sqlite_skips_remote_source_kind() -> None:
    """Remote catalogs stay live — merge ops must only include local rows."""
    import asyncio
    from unittest.mock import AsyncMock

    planner = MaterializePlanner(session=None)  # type: ignore[arg-type]
    planner._fetch_rows = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"name": "local", "source_kind": "local", "status": "ready", "paused": False},
            {"name": "legacy", "status": "ready", "paused": False},  # missing kind = local
            {"name": "remote", "source_kind": "remote", "status": "ready", "paused": False},
        ]
    )
    ops = asyncio.run(
        planner._plan_rows_ops(
            module_id="mod_equipment",
            rule_id="catalogs_merge",
            source={"type": "rows", "table_slug": "catalogs", "filter": {"status": "ready"}},
            target={
                "workspace_path": "catalogs/catalog.sqlite",
                "format": "merge_mapped_sqlite",
                "artifact_field": "artifact_ref",
                "map_field": "column_map",
                "schema": [{"key": "title"}],
                "required_map_keys": ["title"],
            },
            fmt="merge_mapped_sqlite",
            active_profile_id=None,
            project_id="proj_1",
            cabinet_id="cab_1",
            priority=10,
        )
    )
    assert len(ops) == 1
    names = [b.get("name") for b in ops[0].rows_bodies or []]
    assert names == ["local", "legacy"]


def test_active_profile_paths_only_expand_matching_profile() -> None:
    """Simulate materialize filter: only active profile's prompt_paths land in ops."""
    active = _pick_active_profile(
        [
            {"row_id": "prof_a", "body": {"project_ids": ["proj1"], "is_default": True}},
            {"row_id": "prof_b", "body": {"project_ids": ["proj2"], "is_default": True}},
        ],
        "proj1",
    )
    assert active == "prof_a"
    path_rows = [
        {
            "row_id": "pa",
            "profile_id": "prof_a",
            "path": "rules/",
            "files_json": [{"name": "a", "body": "from A"}],
        },
        {
            "row_id": "pb",
            "profile_id": "prof_b",
            "path": "rules/",
            "files_json": [{"name": "b", "body": "from B"}],
        },
    ]
    filtered = [r for r in path_rows if _row_matches_filter(r, {"profile_id": active})]
    ops = _expand_prompt_path_ops(
        rows=filtered,
        rule_id="prompt_paths_files",
        module_id="mod_prompts",
    )
    assert len(ops) == 1
    assert ops[0].workspace_path == "rules/a.md"
    assert ops[0].format == "prompt_fragment"
    assert ops[0].row_body == {
        "body_md": "from A",
        "fragment_id": "prompt_paths_files_0_0",
    }


def test_prompt_paths_without_profile_placeholder_expand() -> None:
    """equipment_prompts (без профиля): prompt_paths правила без плейсхолдера
    {{active_profile_id}} разворачиваются и без активного профиля."""
    import asyncio
    from unittest.mock import AsyncMock

    planner = MaterializePlanner(session=None)  # type: ignore[arg-type]
    planner._fetch_rows = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "row_id": "ep1",
                "path": "prompts/equipment",
                "files_json": [{"name": "selection", "body": "Инструкция подбора"}],
                "enabled": True,
            }
        ]
    )
    ops = asyncio.run(
        planner._plan_rows_ops(
            module_id="mod_equipment",
            rule_id="equipment_prompts_files",
            source={
                "type": "rows",
                "table_slug": "equipment_prompts",
                "filter": {"enabled": True},
            },
            target={"workspace_path": ".", "format": "prompt_paths"},
            fmt="prompt_paths",
            active_profile_id=None,
            project_id="proj_1",
            cabinet_id="cab_1",
            priority=11,
        )
    )
    assert len(ops) == 1
    assert ops[0].format == "prompt_fragment"
    assert ops[0].workspace_path == "prompts/equipment/selection.md"


def test_prompt_paths_with_profile_placeholder_need_profile() -> None:
    """mod_prompts: правило с {{active_profile_id}} без активного профиля → []."""
    import asyncio
    from unittest.mock import AsyncMock

    planner = MaterializePlanner(session=None)  # type: ignore[arg-type]
    planner._fetch_rows = AsyncMock(return_value=[])  # type: ignore[method-assign]
    ops = asyncio.run(
        planner._plan_rows_ops(
            module_id="mod_prompts",
            rule_id="prompt_paths_files",
            source={
                "type": "rows",
                "table_slug": "prompt_paths",
                "filter": {"profile_id": "{{active_profile_id}}"},
            },
            target={"workspace_path": ".", "format": "prompt_paths"},
            fmt="prompt_paths",
            active_profile_id=None,
            project_id="proj_1",
            cabinet_id="cab_1",
            priority=10,
        )
    )
    assert ops == []
