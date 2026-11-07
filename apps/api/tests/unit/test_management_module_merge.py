"""The one-time fold of mod_prompts / mod_mcp / mod_files into «Управление».

The migration helper is split into a pure planner and a thin SQL applier so the
decisions can be tested without Postgres. What matters here:

* no data row of any of the three areas is left behind — including the areas
  that resolved to an inherited instance rather than a local copy;
* bindings are *moved*, because an archived module that kept its binding would
  keep materializing over the merged module's workspace;
* a second run changes nothing (idempotent), and a database that never had the
  old modules is a no-op.
"""

from __future__ import annotations

from typing import Any

from prodavan.application.platform.management_module_merge import (
    DataRow,
    Instance,
    MergeState,
    apply_plan,
    declared_grant_scope,
    plan_merge,
)
from prodavan.application.platform.product_module_seeds import (
    LEGACY_MERGED_MODULE_IDS,
    MANAGEMENT_MODULE_ID,
)
from prodavan.application.platform.product_module_upsert import upsert_product_modules

_PROMPTS, _MCP, _FILES = LEGACY_MERGED_MODULE_IDS


class _FakeResult:
    def fetchall(self) -> list[tuple]:
        return []

    def fetchone(self) -> None:
        return None

    def scalar_one(self) -> int:
        return 0


class _RecordingConn:
    """Minimal ``sa.Connection`` stand-in: records (sql, params) in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, stmt: Any, params: dict[str, Any] | None = None, **_: Any) -> _FakeResult:
        self.calls.append((" ".join(str(stmt).split()), dict(params or {})))
        return _FakeResult()


def _state_before_merge() -> MergeState:
    """A deployment that ran with the three separate modules.

    * platform holds one instance per area with the area's rows;
    * cabinet ``cab_1`` kept a *local* prompts copy (someone edited prompts
      there) and resolves MCP/files through the cabinet→company→platform walk;
    * the three modules are granted to every company (``company_grant_scope =
      'all'`` — what revision 2026082714 installed);
    * every cabinet/project binding of the three is ``global`` and read-only.
    """
    state = MergeState()
    for module_id in LEGACY_MERGED_MODULE_IDS:
        state.modules[module_id] = {"status": "active", "company_grant_scope": "all"}
    state.modules[MANAGEMENT_MODULE_ID] = {
        "status": "active",
        "company_grant_scope": "all",
    }

    state.instances = [
        Instance(id="minst_plat_prompts", module_id=_PROMPTS, owner_kind="platform", owner_id="platform"),
        Instance(id="minst_plat_mcp", module_id=_MCP, owner_kind="platform", owner_id="platform"),
        Instance(id="minst_plat_files", module_id=_FILES, owner_kind="platform", owner_id="platform"),
        Instance(
            id="minst_cab_prompts",
            module_id=_PROMPTS,
            owner_kind="cabinet",
            owner_id="cab_1",
            parent_instance_id="minst_plat_prompts",
        ),
    ]

    state.data_rows = {
        "minst_plat_prompts": [
            DataRow("minst_plat_prompts", "prompt_profiles", "profile_default", {"name": "Default", "is_default": True}),
            DataRow("minst_plat_prompts", "prompt_paths", "path_agents", {"path": "", "files_json": []}),
        ],
        "minst_plat_mcp": [
            DataRow("minst_plat_mcp", "mcp_packages", "pkg_demo", {"name": "demo", "version": "1.0.0"}),
        ],
        "minst_plat_files": [
            DataRow("minst_plat_files", "files", "file_readme", {"name": "README", "target_path": "README.md"}),
        ],
        "minst_cab_prompts": [
            DataRow(
                "minst_cab_prompts",
                "prompt_profiles",
                "profile_default",
                {"name": "Default (cabinet)", "is_default": True},
            ),
            DataRow("minst_cab_prompts", "prompt_profiles", "profile_ops", {"name": "Ops"}),
        ],
    }

    state.instance_meta = {
        "minst_plat_prompts": {"materialize_roots": {"workspace_roots": ["rules/", "prompts/"]}},
        "minst_plat_mcp": {"materialize_roots": {"workspace_roots": ["packages/"]}},
    }

    state.cabinet_bindings = {
        (_PROMPTS, "cab_1"): {"bind_kind": "global", "child_may_edit": False},
        (_MCP, "cab_1"): {"bind_kind": "global", "child_may_edit": False},
        (_FILES, "cab_1"): {"bind_kind": "global", "child_may_edit": False},
    }
    state.project_bindings = {
        (_PROMPTS, "proj_1"): {"bind_kind": "global", "child_may_edit": False},
        (_MCP, "proj_1"): {"bind_kind": "global", "child_may_edit": True},
    }
    state.company_grants = {}
    state.cabinet_company = {"cab_1": "co_1"}
    state.project_cabinet = {"proj_1": "cab_1"}
    return state


def _rows(plan: Any) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {(row.instance_id, row.table_slug, row.row_id): row.body for row in plan.copy_rows}


def test_plan_creates_one_instance_per_owner_scope() -> None:
    plan = plan_merge(_state_before_merge())
    owners = {(inst.owner_kind, inst.owner_id) for inst in plan.create_instances}
    # The platform scope always gets one (it is the root global binds walk to),
    # and so does every scope that held an instance of any of the three modules.
    assert owners == {("platform", "platform"), ("cabinet", "cab_1")}
    assert all(inst.module_id == MANAGEMENT_MODULE_ID for inst in plan.create_instances)
    cabinet_instance = next(i for i in plan.create_instances if i.owner_kind == "cabinet")
    assert cabinet_instance.parent_instance_id is None  # no company instance existed


def test_plan_copies_every_area_including_inherited_ones() -> None:
    state = _state_before_merge()
    plan = plan_merge(state)
    platform_dest = next(
        inst.id for inst in plan.create_instances if inst.owner_kind == "platform"
    )
    cabinet_dest = next(
        inst.id for inst in plan.create_instances if inst.owner_kind == "cabinet"
    )
    rows = _rows(plan)

    # Platform: prompts (2) + mcp (1) + files (1) — nothing dropped.
    assert {
        (table, row_id) for (inst, table, row_id) in rows if inst == platform_dest
    } == {
        ("prompt_profiles", "profile_default"),
        ("prompt_paths", "path_agents"),
        ("mcp_packages", "pkg_demo"),
        ("files", "file_readme"),
    }
    assert rows[(platform_dest, "files", "file_readme")]["target_path"] == "README.md"
    assert rows[(platform_dest, "mcp_packages", "pkg_demo")]["version"] == "1.0.0"

    # Cabinet: its own edited prompts win, and MCP/files still arrive through
    # the inherited (global) binding — otherwise they would vanish for a cabinet
    # that happened to keep a local prompts copy.
    cabinet_rows = {
        (table, row_id): body
        for (inst, table, row_id), body in rows.items()
        if inst == cabinet_dest
    }
    assert set(cabinet_rows) == {
        ("prompt_profiles", "profile_default"),
        ("prompt_profiles", "profile_ops"),
        ("mcp_packages", "pkg_demo"),
        ("files", "file_readme"),
    }
    assert cabinet_rows[("prompt_profiles", "profile_default")]["name"] == "Default (cabinet)"


def test_plan_merges_workspace_roots_and_skips_slugs_the_seed_owns() -> None:
    state = _state_before_merge()
    plan = plan_merge(state)
    platform_dest = next(
        inst.id for inst in plan.create_instances if inst.owner_kind == "platform"
    )
    roots = [
        meta
        for meta in plan.copy_instance_meta
        if meta["instance_id"] == platform_dest and meta["slug"] == "materialize_roots"
    ]
    assert len(roots) == 1
    assert set(roots[0]["body"]["workspace_roots"]) == {"rules/", "prompts/", "packages/"}
    # Per-area `tables`/`views`/`tabs` documents are never copied: the merged
    # seed declares the union, and a single area's document would drop two areas.
    copied_slugs = {meta["slug"] for meta in plan.copy_instance_meta}
    assert copied_slugs == {"materialize_roots"}


def test_plan_moves_bindings_and_archives_the_old_modules() -> None:
    plan = plan_merge(_state_before_merge())

    assert {b["cabinet_id"] for b in plan.copy_cabinet_bindings} == {"cab_1"}
    assert plan.copy_cabinet_bindings[0]["bind_kind"] == "global"
    assert plan.copy_cabinet_bindings[0]["child_may_edit"] is False
    assert {b["project_id"] for b in plan.copy_project_bindings} == {"proj_1"}
    # Two legacy modules disagreed about child_may_edit — the merged bind keeps
    # the permissive reading so folding them together cannot lock an edit.
    assert plan.copy_project_bindings[0]["child_may_edit"] is True

    assert {e["module_id"] for e in plan.revoke_cabinet_bindings} == set(
        LEGACY_MERGED_MODULE_IDS
    )
    assert {e["module_id"] for e in plan.revoke_project_bindings} == {_PROMPTS, _MCP}
    assert sorted(plan.archive_modules) == sorted(LEGACY_MERGED_MODULE_IDS)


def test_nothing_is_deleted_only_archived() -> None:
    """Rows stay recoverable: the plan never drops instances, rows or meta."""
    plan = plan_merge(_state_before_merge())
    counts = plan.counts()
    assert counts["archived_modules"] == 3
    # No "delete instance"/"delete row" concept exists in the plan at all.
    assert not hasattr(plan, "delete_instances")
    assert not hasattr(plan, "delete_rows")


def test_second_run_plans_nothing() -> None:
    state = _state_before_merge()
    first = plan_merge(state)

    # Apply the plan to the in-memory state exactly as the SQL would.
    for inst in first.create_instances:
        state.instances.append(
            Instance(
                id=inst.id,
                module_id=inst.module_id,
                owner_kind=inst.owner_kind,
                owner_id=inst.owner_id,
                parent_instance_id=inst.parent_instance_id,
            )
        )
    for row in first.copy_rows:
        state.data_rows.setdefault(row.instance_id, []).append(row)
    for meta in first.copy_instance_meta:
        state.instance_meta.setdefault(meta["instance_id"], {})[meta["slug"]] = meta["body"]
    for binding in first.copy_cabinet_bindings:
        state.cabinet_bindings[(binding["module_id"], binding["cabinet_id"])] = {
            "bind_kind": binding["bind_kind"],
            "child_may_edit": binding["child_may_edit"],
        }
    for binding in first.copy_project_bindings:
        state.project_bindings[(binding["module_id"], binding["project_id"])] = {
            "bind_kind": binding["bind_kind"],
            "child_may_edit": binding["child_may_edit"],
        }
    for entry in first.revoke_cabinet_bindings:
        state.cabinet_bindings.pop((entry["module_id"], entry["owner_id"]), None)
    for entry in first.revoke_project_bindings:
        state.project_bindings.pop((entry["module_id"], entry["owner_id"]), None)
    for module_id in first.archive_modules:
        state.modules[module_id]["status"] = "archived"

    second = plan_merge(state)
    assert second.counts() == {
        "instances": 0,
        "rows": 0,
        "instance_meta": 0,
        "cabinet_bindings": 0,
        "project_bindings": 0,
        "company_grants": 0,
        "revoked_bindings": 0,
        "archived_modules": 0,
    }


def test_database_without_the_old_modules_is_a_no_op() -> None:
    state = MergeState()
    state.modules[MANAGEMENT_MODULE_ID] = {"status": "active", "company_grant_scope": "all"}
    state.modules["mod_equipment"] = {"status": "active", "company_grant_scope": "selected"}
    plan = plan_merge(state)
    assert plan.counts()["instances"] == 0
    assert plan.archive_modules == []


def test_existing_merged_bindings_are_not_overwritten() -> None:
    """An admin may already have edited «Управление»'s binding before the merge ran."""
    state = _state_before_merge()
    state.cabinet_bindings[(MANAGEMENT_MODULE_ID, "cab_1")] = {
        "bind_kind": "local",
        "child_may_edit": True,
    }
    plan = plan_merge(state)
    assert plan.copy_cabinet_bindings == []
    # …but the archived module's binding is still revoked: two modules bound to
    # one cabinet is exactly the double-materialize the merge removes.
    assert {e["module_id"] for e in plan.revoke_cabinet_bindings} == set(
        LEGACY_MERGED_MODULE_IDS
    )


def test_apply_plan_copies_before_it_revokes_and_archives_last() -> None:
    conn = _RecordingConn()
    plan = plan_merge(_state_before_merge())
    apply_plan(conn, plan)

    statements = [sql for sql, _ in conn.calls]
    first_delete = next(i for i, sql in enumerate(statements) if sql.startswith("DELETE"))
    last_insert = max(i for i, sql in enumerate(statements) if sql.startswith("INSERT"))
    archive_at = [i for i, sql in enumerate(statements) if sql.startswith("UPDATE modules")]
    assert last_insert < first_delete
    assert archive_at and archive_at[-1] > first_delete

    # The instance copy also seeds the merged meta for every new instance.
    assert any("module_meta_documents" in sql for sql in statements)
    # …and seeds template rows (a from-base install has no legacy rows to copy).
    assert sum("seed_rows" in sql for sql in statements) == len(plan.create_instances)
    # Data rows keep their table_slug and body verbatim.
    row_params = [
        params
        for sql, params in conn.calls
        if sql.startswith("INSERT INTO module_instance_data_rows")
        and "table_slug" in params
    ]
    assert {p["table_slug"] for p in row_params} == {
        "prompt_profiles",
        "prompt_paths",
        "mcp_packages",
        "files",
    }
    assert any(p["row_id"] == "pkg_demo" for p in row_params)


def test_apply_plan_seeds_template_rows_only_after_the_copied_ones() -> None:
    """Order is data safety, not style.

    Both writes are ``ON CONFLICT DO NOTHING``, so a template row inserted first
    would shadow the real row with the same id — a renamed ``profile_default``
    would silently revert to the seed body. The template fill must therefore run
    after the copy.
    """
    conn = _RecordingConn()
    plan = plan_merge(_state_before_merge())
    apply_plan(conn, plan)

    copied = [
        i
        for i, (sql, params) in enumerate(conn.calls)
        if sql.startswith("INSERT INTO module_instance_data_rows") and "table_slug" in params
    ]
    seeded = [
        i
        for i, (sql, _params) in enumerate(conn.calls)
        if "seed_rows" in sql
    ]
    assert copied and seeded
    assert max(copied) < min(seeded)


def test_upsert_hides_the_archived_shells_from_companies() -> None:
    """An archived shell must not stay granted to every company.

    Revision 2026082714 had set ``company_grant_scope='all'`` on exactly these
    three ids. Left alone, `CompanyModuleService` would keep showing the
    archived «Промпты / MCP / Файлы» next to «Управление» for every company.
    """
    conn = _RecordingConn()
    upsert_product_modules(conn)
    inserts = [
        params
        for sql, params in conn.calls
        if sql.startswith("INSERT INTO modules")
    ]
    by_id = {params["id"]: params for params in inserts}
    for module_id in LEGACY_MERGED_MODULE_IDS:
        # Legacy shells take status + scope as SQL literals, never as params.
        assert "status" not in by_id[module_id]
        assert "company_grant_scope" not in by_id[module_id]
    legacy_sql = [
        sql
        for sql, params in conn.calls
        if sql.startswith("INSERT INTO modules") and params.get("id") in set(LEGACY_MERGED_MODULE_IDS)
    ]
    assert all("'selected'" in sql and "status = 'archived'" in sql for sql in legacy_sql)


def test_merge_applies_the_scope_the_seed_declares() -> None:
    """«Управление» must reach every company on both kinds of installation.

    Neither existing path sets it. On a from-base install the row is created by
    revision 2026082711, which predates the ``company_grant_scope`` column; on an
    upgraded deployment the upsert's ON CONFLICT branch deliberately leaves the
    stored scope alone (it must not undo an administrator's choice). That left
    the merged module on the schema default — hidden from exactly the companies
    that had all three modules it replaces.
    """
    conn = _RecordingConn()
    plan = plan_merge(_state_before_merge())
    assert plan.inherit_grant_scope == "all"
    apply_plan(conn, plan)

    scope_updates = [
        (sql, params) for sql, params in conn.calls if "company_grant_scope = :scope" in sql
    ]
    assert scope_updates, "the merge must set the scope the product seed declares"
    sql, params = scope_updates[0]
    assert params == {"scope": "all", "module_id": MANAGEMENT_MODULE_ID}
    # Guarded on the untouched default: an administrator's choice is never
    # `selected`-as-default, and a re-run must not undo it.
    assert "company_grant_scope = 'selected'" in sql
    # Set before the legacy rows are revoked and archived, so the guard reads the
    # value nobody has touched yet.
    scope_at = next(i for i, (text_, _) in enumerate(conn.calls) if "company_grant_scope = :scope" in text_)
    later = [
        i
        for i, (text_, _) in enumerate(conn.calls)
        if text_.lstrip().startswith(("DELETE", "UPDATE modules SET status"))
    ]
    assert later, "the merge must revoke and archive the legacy modules"
    for index in later:
        assert scope_at < index


def test_merge_without_declared_scope_leaves_it_alone() -> None:
    """A product module the seed says nothing about keeps whatever it has."""
    assert declared_grant_scope("mod_equipment") is None
    assert declared_grant_scope(MANAGEMENT_MODULE_ID) == "all"


def test_upsert_grants_management_to_every_company_by_default() -> None:
    """`company_grant_scope='all'` on insert only — a stored choice survives."""
    conn = _RecordingConn()
    upsert_product_modules(conn)
    product_inserts = [
        (sql, params)
        for sql, params in conn.calls
        if sql.startswith("INSERT INTO modules")
        and params.get("id") not in set(LEGACY_MERGED_MODULE_IDS)
    ]
    by_id = {params["id"]: params for _, params in product_inserts}
    assert by_id[MANAGEMENT_MODULE_ID]["company_grant_scope"] == "all"
    assert by_id["mod_equipment"]["company_grant_scope"] == "selected"

    # The conflict clause must not touch `company_grant_scope`, otherwise the
    # next seed update would silently undo an administrator's scope change.
    for sql, _ in product_inserts:
        assert "ON CONFLICT (id) DO UPDATE" in sql
        assert "company_grant_scope" not in sql.split("DO UPDATE", 1)[1]


def test_upsert_writes_no_meta_for_the_archived_shells() -> None:
    conn = _RecordingConn()
    upsert_product_modules(conn)
    meta_writes = [
        params
        for sql, params in conn.calls
        if "INSERT INTO module_meta_documents" in sql
    ]
    written_for = {params["module_id"] for params in meta_writes}
    assert written_for == {MANAGEMENT_MODULE_ID, "mod_equipment"}
    assert not written_for & set(LEGACY_MERGED_MODULE_IDS)
    merged_docs = {p["slug"] for p in meta_writes if p["module_id"] == MANAGEMENT_MODULE_ID}
    assert merged_docs == {
        "tables",
        "columns",
        "views",
        "tabs",
        "materialize",
        "mcp_aliases",
        "materialize_roots",
        "seed_rows",
    }
