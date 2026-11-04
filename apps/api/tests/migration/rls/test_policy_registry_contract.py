"""Static contract tests for the RLS policy registry; authored, not executed.

These assertions are the review surface for revision ``2026110307``: they prove
coverage, uniqueness, deny-by-default shape, idempotence and the guard against a
partially applied schema - by inspecting the registry that the revision itself
walks, so the two cannot diverge.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

from prodavan.infrastructure.persistence.rls import policies
from prodavan.infrastructure.persistence.rls.policies import (
    CABINET_SETTING,
    CANONICAL_TABLE_POLICIES,
    EXCLUDED_TABLES,
    PROJECT_SETTING,
    TENANT_SETTING,
    TRUSTED_SERVICE_ROLE,
    TRUSTED_SERVICE_SETTING,
    TablePolicySpec,
    create_policy_statements,
    downgrade_guard_sql,
    drop_policy_statements,
    enable_statements,
    excluded_table_names,
    policy_inventory,
    registry_tables,
    tenant_predicate,
    uncovered_relations,
    upgrade_guard_sql,
)
from .acceptance_fixtures import BOUNDARY, NEGATIVE_CASES, restricted_tables

#: Every table the wave creates, grouped by the revision that creates it.  The
#: registry must cover exactly this set; adding a table to the schema without a
#: registry spec (or the reverse) fails these tests.
#:
#: Kept explicit rather than derived from metadata on purpose: deriving it from
#: metadata would make the test agree with any registry drift instead of catching
#: it. The list is extended by hand when a revision lands.
WAVE_TABLES = {
    "2026110301": (
        "scope_owner_tenant_anchors",
        "scope_owner_cabinet_anchors",
        "scope_owner_project_anchors",
        "scope_registry",
        "scope_owner_mappings",
        "scope_owner_quarantine",
        "scope_owner_audit",
    ),
    "2026110302": (
        "m04e_dynamic_app_definitions",
        "m04e_dynamic_app_versions",
        "m04e_definition_version_promotion_evidence",
    ),
    "2026110303": (
        "m05_plugin_instances",
        "m05_plugin_instance_registry_links",
        "m05_plugin_instance_promotion_evidence",
    ),
    "2026110304": (
        "dynamic_app_capability_grants",
        "dynamic_app_capability_grant_mappings",
        "dynamic_app_capability_grant_quarantine",
        "dynamic_app_capability_audit",
        "dynamic_app_capability_writer_epochs",
        "m06_canonical_grant_promotion_evidence",
    ),
    "2026110305": (
        "equipment_v2_ingestion_runs",
        "equipment_v2_catalog_snapshots",
        "equipment_v2_catalog_offers",
        "equipment_v2_requests",
        "equipment_v2_request_lines",
        "equipment_v2_candidate_groups",
        "equipment_v2_budget_allocations",
        "equipment_v2_tenders",
    ),
    "2026110306": (
        "project_runtime_revisions",
        "materialization_entries",
        "runtime_revision_heads",
        "runtime_materialization_attempts",
        "runtime_materialization_audit",
    ),
    "2026110309": ("agent_run_leases",),
    "2026110311": ("agent_run_provider_attempts",),
    "2026110313": ("m04_scope_promotion_evidence",),
    "2026110315": (
        "m04e_definition_version_mappings",
        "m04e_definition_version_quarantine",
        "m04e_definition_version_checkpoints",
        "m04e_definition_version_audit",
    ),
}

REVISION_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "alembic" / "versions" / "2026110307_rls_policies.py"
)


def _load_revision_module():
    """Load the revision by path: ``alembic/versions`` is not an import package."""

    spec = importlib.util.spec_from_file_location("rls_revision_2026110307", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _specs_by_table() -> dict[str, TablePolicySpec]:
    return {spec.table: spec for spec in CANONICAL_TABLE_POLICIES}


def test_revision_chain_is_linear_and_last() -> None:
    module = _load_revision_module()
    assert module.revision == "2026110307"
    assert module.down_revision == "2026110306"
    assert module.branch_labels is None
    assert module.depends_on is None
    # The revision imports the same registry the tests assert on.
    assert module.policies is policies


def test_every_wave_table_is_registered_exactly_once() -> None:
    tables = registry_tables()
    assert len(tables) == len(set(tables))
    assert set(tables) == {table for owned in WAVE_TABLES.values() for table in owned}
    owners = {spec.owner_revision for spec in CANONICAL_TABLE_POLICIES}
    assert owners == set(WAVE_TABLES), "owner revisions must match the wave's DDL chain"


def test_each_owner_revision_owns_exactly_its_declared_tables() -> None:
    for revision, declared in WAVE_TABLES.items():
        owned = {spec.table for spec in CANONICAL_TABLE_POLICIES if spec.owner_revision == revision}
        assert owned == set(declared), revision


def test_policy_names_are_unique_per_table_and_per_registry() -> None:
    names = [name for spec in CANONICAL_TABLE_POLICIES for name in spec.policy_names]
    assert len(names) == len(set(names))
    for spec in CANONICAL_TABLE_POLICIES:
        assert spec.tenant_policy == f"{spec.table}_tenant_isolation"
        assert spec.trusted_policy == f"{spec.table}_trusted_service_access"


def test_exactly_one_tenant_policy_and_one_trusted_policy_per_table() -> None:
    for spec in CANONICAL_TABLE_POLICIES:
        tenant, trusted = create_policy_statements(spec)
        assert tenant.startswith(f"CREATE POLICY {spec.tenant_policy} ON {spec.table} FOR ALL"), spec.table
        assert f"USING ({tenant_predicate(spec)}) WITH CHECK ({tenant_predicate(spec)})" in tenant
        assert f"CREATE POLICY {spec.trusted_policy} ON {spec.table} FOR " in trusted
        # A trusted policy never replaces the tenant predicate: it is a second,
        # separately named policy scoped to the service role.
        assert TRUSTED_SERVICE_ROLE in trusted
        assert TRUSTED_SERVICE_SETTING in trusted


def test_no_policy_reads_a_caller_supplied_value() -> None:
    for spec in CANONICAL_TABLE_POLICIES:
        for statement in create_policy_statements(spec):
            assert "current_setting(" in statement
            assert ":tenant" not in statement and "%s" not in statement and "${" not in statement
            # Every read must be the two-argument (missing_ok) form: unset -> NULL.
            for match in re.finditer(r"current_setting\('([^']+)'([^)]*)\)", statement):
                assert match.group(2).strip() == ", true", statement


def test_predicates_are_deny_by_default() -> None:
    for spec in CANONICAL_TABLE_POLICIES:
        predicate = tenant_predicate(spec)
        if spec.tenant is None:
            assert predicate == "false"
            continue
        assert predicate != "true"
        assert TENANT_SETTING in predicate
        assert "::uuid" not in predicate, "comparison must not cast caller-controlled text to uuid"
        if spec.cabinet is not None:
            assert CABINET_SETTING in predicate
        if spec.project is not None:
            assert PROJECT_SETTING in predicate


def test_every_scope_column_exists_on_the_declared_model() -> None:
    """Catch a registry/model drift: a column the table does not have cannot be policied."""

    from prodavan.infrastructure.persistence.models import Base

    for spec in CANONICAL_TABLE_POLICIES:
        table = Base.metadata.tables.get(spec.table)
        assert table is not None, f"{spec.table} is not registered in Base.metadata"
        for scoped in (spec.tenant, spec.cabinet, spec.project):
            if scoped is not None and scoped.column is not None:
                assert scoped.column in table.c, f"{spec.table}.{scoped.column} does not exist"
            for hop in (() if scoped is None else scoped.chain):
                parent = Base.metadata.tables.get(hop.table)
                assert parent is not None, f"{hop.table} is not registered in Base.metadata"
                assert hop.key in parent.c, f"{hop.table}.{hop.key} does not exist"
                assert hop.value_column in parent.c, f"{hop.table}.{hop.value_column} does not exist"


def test_model_metadata_has_no_uncovered_canonical_table() -> None:
    from prodavan.infrastructure.persistence.models import Base

    assert uncovered_relations(Base.metadata.tables.keys()) == ()


@pytest.mark.parametrize("table", sorted(t for t in registry_tables() if t != "scope_registry"))
def test_non_registry_tables_do_not_filter_on_owner_state(table: str) -> None:
    spec = _specs_by_table()[table]
    assert spec.tenant_restriction is None
    assert "owner_state" not in tenant_predicate(spec)


def test_registry_visibility_requires_active_owner_state() -> None:
    spec = _specs_by_table()["scope_registry"]
    predicate = tenant_predicate(spec)
    assert "scope_registry.owner_state = 'active'" in predicate
    assert restricted_tables() == {"scope_registry"}


def test_global_evidence_tables_deny_tenant_sessions() -> None:
    # scope_owner_audit carries no tenant anchor at all; tenant access is denied
    # and only the trusted service policy may read it.
    audit = _specs_by_table()["scope_owner_audit"]
    assert audit.tenant is None
    assert tenant_predicate(audit) == "false"
    assert audit.trusted_command == "ALL"


def test_child_tables_inherit_scope_through_typed_parents() -> None:
    entries = _specs_by_table()["materialization_entries"]
    assert entries.tenant is not None and entries.tenant.column is None
    assert [hop.table for hop in entries.tenant.chain] == [
        "project_runtime_revisions",
        "projects",
    ]
    assert entries.tenant.chain[-1].value_column == "company_id"
    assert entries.cabinet is not None and entries.cabinet.chain[-1].value_column == "cabinet_id"

    revisions = _specs_by_table()["project_runtime_revisions"]
    assert revisions.tenant is not None and revisions.tenant.chain[0].table == "projects"
    assert revisions.project is not None and revisions.project.column == "project_id"

    predicate = tenant_predicate(revisions)
    assert "EXISTS (SELECT 1 FROM projects AS rls_0" in predicate
    assert "rls_0.company_id = project_runtime_revisions.project_id" in predicate


def test_mapping_and_quarantine_resolve_through_their_owner() -> None:
    mappings = _specs_by_table()["scope_owner_mappings"]
    assert mappings.tenant is not None and mappings.tenant.column is None
    assert "scope_registry" in tenant_predicate(mappings)

    grant_mappings = _specs_by_table()["dynamic_app_capability_grant_mappings"]
    assert "dynamic_app_capability_grants" in tenant_predicate(grant_mappings)

    quarantine = _specs_by_table()["scope_owner_quarantine"]
    assert quarantine.tenant is not None and quarantine.tenant.column == "resolved_tenant_id"


def test_optional_cabinet_scope_accepts_null_but_not_another_tenant() -> None:
    grants = _specs_by_table()["dynamic_app_capability_grants"]
    predicate = tenant_predicate(grants)
    assert "dynamic_app_capability_grants.cabinet_id IS NULL OR dynamic_app_capability_grants.cabinet_id =" in predicate
    tenant_clause = "dynamic_app_capability_grants.tenant_id = nullif(current_setting('app.tenant_id', true), '')"
    assert tenant_clause in predicate
    assert "tenant_id IS NULL" not in predicate


def test_upgrade_is_idempotent_and_guarded() -> None:
    for spec in CANONICAL_TABLE_POLICIES:
        sql = upgrade_guard_sql(spec)
        enable, force = enable_statements(spec)
        assert sql.count("DO $rls$") == 1
        assert f"to_regclass('{spec.table}') IS NULL" in sql
        assert "RAISE NOTICE" in sql
        assert enable + ";" in sql
        assert force + ";" in sql
        for drop, create in zip(drop_policy_statements(spec), create_policy_statements(spec), strict=True):
            assert sql.index(drop + ";") < sql.index(create + ";")
        # Re-applying the same block twice is a no-op, not a duplicate_object error.
        assert "DROP POLICY IF EXISTS" in sql and "CREATE POLICY" in sql


def test_downgrade_removes_policies_and_rls_without_touching_data() -> None:
    for spec in CANONICAL_TABLE_POLICIES:
        sql = downgrade_guard_sql(spec)
        assert f"DROP POLICY IF EXISTS {spec.tenant_policy} ON {spec.table};" in sql
        assert f"DROP POLICY IF EXISTS {spec.trusted_policy} ON {spec.table};" in sql
        assert f"ALTER TABLE {spec.table} NO FORCE ROW LEVEL SECURITY;" in sql
        assert f"ALTER TABLE {spec.table} DISABLE ROW LEVEL SECURITY;" in sql
        assert "DROP TABLE" not in sql
        assert "DELETE" not in sql
        assert "TRUNCATE" not in sql


def test_guarded_blocks_never_swallow_an_absent_relation_silently() -> None:
    for spec in CANONICAL_TABLE_POLICIES:
        for sql in (upgrade_guard_sql(spec), downgrade_guard_sql(spec)):
            assert "RETURN;" in sql
            assert spec.table in sql
            assert "EXCEPTION" not in sql, "a real DDL failure must abort, not be swallowed"


def test_exclusions_are_documented_and_do_not_overlap_the_registry() -> None:
    assert EXCLUDED_TABLES, "exclusions must be explicit review input"
    for names, reason in EXCLUDED_TABLES:
        assert names, "an exclusion must name the concrete relations it covers"
        assert all(name == name.lower() and name == name.strip() for name in names), names
        assert len(reason.split()) >= 8, "each exclusion needs a reviewed justification"
    excluded = excluded_table_names()
    assert not excluded & set(registry_tables())
    # Tables created by sibling revisions that must not be silently ignored.
    assert "capability_bundles" in excluded
    assert {"resources", "resource_versions", "resource_bindings"} <= excluded


def test_uncovered_canonical_relations_are_reported_not_skipped() -> None:
    canonical_relations = (
        *registry_tables(),
        *sorted(excluded_table_names()),
        "scope_owner_new_evidence",  # hypothetical sibling table without a policy
        "projects",  # legacy anchor handled by the documented exclusion
        "unrelated_legacy_table",
    )
    assert uncovered_relations(canonical_relations) == ("scope_owner_new_evidence",)
    # A registry-only catalog is fully covered.
    assert uncovered_relations(registry_tables()) == ()


def test_registry_policy_text_has_no_dbapi_placeholders() -> None:
    for spec in CANONICAL_TABLE_POLICIES:
        for sql in (
            upgrade_guard_sql(spec),
            downgrade_guard_sql(spec),
            *create_policy_statements(spec),
        ):
            assert "%s" not in sql
            assert "%(" not in sql


def test_policy_inventory_covers_two_policies_per_table() -> None:
    inventory = policy_inventory()
    assert len(inventory) == len(CANONICAL_TABLE_POLICIES) * 2
    per_table = {}
    for entry in inventory:
        per_table.setdefault(entry["table"], []).append(entry["policy"])
    assert set(per_table) == set(registry_tables())
    assert all(len(names) == 2 for names in per_table.values())


def test_registry_rejects_inconsistent_declarations() -> None:
    from prodavan.infrastructure.persistence.rls.policies import RlsRegistryError

    bad = TablePolicySpec(table="x", owner_revision="2026110301", tenant=None, cabinet=None)
    assert tenant_predicate(bad) == "false"
    with pytest.raises(RlsRegistryError):
        TablePolicySpec(table="Bad Name", owner_revision="2026110301", tenant=None)
    with pytest.raises(RlsRegistryError):
        TablePolicySpec(
            table="x",
            owner_revision="2026110301",
            tenant=None,
            cabinet=policies.ScopedColumn(column="cabinet_id"),
        )
    with pytest.raises(RlsRegistryError):
        policies.validate_registry((CANONICAL_TABLE_POLICIES[0], CANONICAL_TABLE_POLICIES[0]))


def test_acceptance_catalog_lists_every_negative_case() -> None:
    cases = {entry["case"] for entry in NEGATIVE_CASES}
    assert {
        "no_tenant_context",
        "empty_tenant_context",
        "cross_tenant_select",
        "cross_tenant_insert",
        "cross_tenant_update",
        "set_outside_transaction",
        "caller_supplied_tenant",
        "principal_not_server_resolved",
        "expired_principal",
        "readback_mismatch",
        "bypassrls_role",
        "tenant_session_sets_trusted_flag",
        "archived_registry_row",
        "orphan_child_scope",
        "session_level_set",
    } <= cases
    ids = [entry["id"] for entry in NEGATIVE_CASES]
    assert len(ids) == len(set(ids))


def test_boundary_states_no_positive_enablement() -> None:
    assert BOUNDARY == {
        "execution": "not run",
        "alembic_upgrade": False,
        "database": False,
        "positive_canonical_writes": False,
        "verification_freeze": True,
    }
