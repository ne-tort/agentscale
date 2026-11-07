"""«Управление» — the merged product module (prompts + MCP + files).

The three former product modules (``mod_prompts``, ``mod_mcp``, ``mod_files``)
are one module now. These tests pin the two properties that make the merge
safe: nothing the three areas declared was dropped, and the merged module is
reachable — hub first, three sections under it, one tab leading in.
"""

from __future__ import annotations

from prodavan.application.modules.module_meta_validator import (
    validate_document_body,
    validate_merged_slug_map,
)
from prodavan.application.platform.bootstrap_config import DEFAULT_BASIC_MODULE_IDS
from prodavan.application.platform.product_module_seeds import (
    MANAGEMENT_HUB_VIEW_SLUG,
    MANAGEMENT_MODULE_ID,
    PRODUCT_MODULES,
    mod_files_meta,
    mod_management_meta,
    mod_mcp_meta,
    mod_prompts_meta,
)
from prodavan.domain.modules import (
    GLOBAL_DEFAULT_PROJECT_MODULES,
    ModuleBindKind,
    default_project_bind_kind,
)

_ORIGINALS = (mod_prompts_meta, mod_mcp_meta, mod_files_meta)


def _table_slugs(meta: dict) -> set[str]:
    return {table["slug"] for table in meta["tables"]}


def _view_slugs(meta: dict) -> set[str]:
    return {view["slug"] for view in meta["views"]}


def test_merged_meta_carries_every_table_of_the_three_originals() -> None:
    merged = mod_management_meta()
    expected: set[str] = set()
    for meta_fn in _ORIGINALS:
        expected |= _table_slugs(meta_fn())

    assert _table_slugs(merged) == expected
    # The five tables that used to live in three modules are all here — the
    # merge concatenates them, it does not rename or deduplicate anything.
    assert expected == {
        "prompt_profiles",
        "profile_settings",
        "prompt_paths",
        "mcp_packages",
        "files",
    }
    # No slug collisions: table slugs are unique inside the merged document.
    slugs = [table["slug"] for table in merged["tables"]]
    assert len(slugs) == len(set(slugs))


def test_merged_meta_carries_every_view_of_the_three_originals() -> None:
    merged = mod_management_meta()
    expected: set[str] = set()
    for meta_fn in _ORIGINALS:
        expected |= _view_slugs(meta_fn())

    assert _view_slugs(merged) == expected | {MANAGEMENT_HUB_VIEW_SLUG}
    slugs = [view["slug"] for view in merged["views"]]
    assert len(slugs) == len(set(slugs))


def test_merged_meta_keeps_columns_actions_rules_and_seed_rows() -> None:
    merged = mod_management_meta()
    original_columns = [
        (column["table_slug"], column["name"])
        for meta_fn in _ORIGINALS
        for column in meta_fn()["columns"]
    ]
    merged_columns = [(column["table_slug"], column["name"]) for column in merged["columns"]]
    assert merged_columns == original_columns

    original_rules = [
        rule["id"] for meta_fn in _ORIGINALS for rule in meta_fn().get("materialize", [])
    ]
    merged_rules = [rule["id"] for rule in merged["materialize"]]
    assert sorted(merged_rules) == sorted(original_rules)
    assert {"prompt_paths_files", "files_to_workspace", "mcp_packages_enabled"} <= set(
        merged_rules
    )

    original_seeds = [
        (item["table_slug"], item["row_id"])
        for meta_fn in _ORIGINALS
        for item in meta_fn()["seed_rows"]["items"]
    ]
    merged_seeds = [(item["table_slug"], item["row_id"]) for item in merged["seed_rows"]["items"]]
    assert merged_seeds == original_seeds
    assert "profile_default" in {row_id for _, row_id in merged_seeds}

    # The MCP area's chat aliases are the only doc no other area carried.
    assert merged["mcp_aliases"] == mod_mcp_meta()["mcp_aliases"]


def test_merged_meta_keeps_workspace_roots_of_both_areas() -> None:
    """A project sync prunes these prefixes — dropping half would wipe files."""
    roots = mod_management_meta()["materialize_roots"]["workspace_roots"]
    assert set(roots) == {
        "rules/",
        "skills/",
        "prompts/",
        "AGENTS.md",
        "CLAUDE.md",
        "packages/",
    }


def test_management_hub_lists_all_three_areas() -> None:
    merged = mod_management_meta()
    hub = next(view for view in merged["views"] if view["slug"] == MANAGEMENT_HUB_VIEW_SLUG)
    assert hub["kind"] == "hub"
    assert hub["ui_json"]["kind"] == "hub"

    targets = [item["target"]["view"] for item in hub["ui_json"]["items"]]
    assert len(targets) == 3
    assert len(set(targets)) == 3
    # Every tile leads into a declared view of the merged module, and the three
    # areas are each represented by their own list.
    assert set(targets) == {"prompt_profiles_list", "mcp_packages_list", "files_list"}
    assert set(targets) <= _view_slugs(merged)
    assert [item["title"] for item in hub["ui_json"]["items"]] == ["Промпты", "MCP", "Файлы"]


def test_management_hub_is_the_landing_tab() -> None:
    """The shell opens the first enabled tab, so the hub tab has to be first."""
    merged = mod_management_meta()
    first = merged["tabs"][0]
    assert first["view_slug"] == MANAGEMENT_HUB_VIEW_SLUG
    assert first["enabled"] is True
    assert first["default_project_bind"] == "global"
    assert first["nav"] == {"contour": "employee", "placement": "management"}

    # …and the three area tabs follow it, one section each.
    sections = [tab for tab in merged["tabs"][1:]]
    assert [tab["title"] for tab in sections] == ["Промпты", "MCP", "Файлы"]
    assert [tab["view_slug"] for tab in sections] == [
        "prompt_profiles_list",
        "mcp_packages_list",
        "files_list",
    ]
    assert all(tab["nav"]["placement"] == "management" for tab in merged["tabs"])
    ids = [tab["id"] for tab in merged["tabs"]]
    assert len(ids) == len(set(ids))


def test_merged_meta_passes_the_module_meta_validator() -> None:
    merged = mod_management_meta()
    validate_merged_slug_map(merged)
    for slug, body in merged.items():
        validate_document_body(slug, body)


def test_management_is_the_only_default_basic_module() -> None:
    assert DEFAULT_BASIC_MODULE_IDS == ("mod_management",)
    assert MANAGEMENT_MODULE_ID in DEFAULT_BASIC_MODULE_IDS


def test_projects_reach_management_through_the_parent_sot() -> None:
    assert GLOBAL_DEFAULT_PROJECT_MODULES == frozenset({"mod_management"})
    assert default_project_bind_kind("mod_management") == ModuleBindKind.GLOBAL


def test_product_modules_list_is_management_plus_equipment() -> None:
    real_modules = [(mid, name) for mid, name, meta in PRODUCT_MODULES if meta]
    assert real_modules == [("mod_management", "Управление"), ("mod_equipment", "Подбор техники")]
