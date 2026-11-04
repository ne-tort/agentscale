"""Allow/deny matrix for the RLS predicates; authored, not executed.

Each case evaluates the *same* predicate text the revision installs (through
``tenant_predicate``), applied by an independent evaluator in
``acceptance_fixtures.scope_visible``.  This is a review artifact: it shows the
intended outcome per context, not a database run.
"""

from __future__ import annotations

import pytest

from prodavan.infrastructure.persistence.rls.policies import CANONICAL_TABLE_POLICIES
from .acceptance_fixtures import (
    CABINET_A,
    CABINET_B,
    CONTEXTS,
    INSTANCE_A,
    PROJECT_A,
    PROJECT_B,
    TENANT_A,
    TENANT_B,
    scope_visible,
    spec_for,
)

# Representative rows for the tables whose scope is inherited or conditional.
# Direct-column tables use their own tenant/cabinet/project values.
PARENTS = {
    "projects": [
        {"id": PROJECT_A, "company_id": TENANT_A, "cabinet_id": CABINET_A},
        {"id": PROJECT_B, "company_id": TENANT_B, "cabinet_id": CABINET_B},
    ],
    "project_runtime_revisions": [
        {"id": "rr_a", "project_id": PROJECT_A},
        {"id": "rr_b", "project_id": PROJECT_B},
    ],
    "scope_registry": [
        {"id": "scope_a", "tenant_id": TENANT_A, "owner_state": "active"},
        {"id": "scope_a_archived", "tenant_id": TENANT_A, "owner_state": "archived"},
        {"id": "scope_b", "tenant_id": TENANT_B, "owner_state": "active"},
    ],
    "dynamic_app_capability_grants": [
        {"id": "cgrant_a", "tenant_id": TENANT_A},
        {"id": "cgrant_b", "tenant_id": TENANT_B},
    ],
}

def _rows_by_table() -> dict[str, list[dict[str, object]]]:
    rows: dict[str, list[dict[str, object]]] = dict(PARENTS)
    rows.update(
        {
            "scope_owner_tenant_anchors": [
                {"tenant_id": TENANT_A},
                {"tenant_id": TENANT_B},
            ],
            "scope_owner_cabinet_anchors": [
                {"tenant_id": TENANT_A, "cabinet_id": CABINET_A},
                {"tenant_id": TENANT_A, "cabinet_id": CABINET_B},
                {"tenant_id": TENANT_B, "cabinet_id": CABINET_B},
            ],
            "scope_owner_project_anchors": [
                {"tenant_id": TENANT_A, "cabinet_id": CABINET_A, "project_id": PROJECT_A},
                {"tenant_id": TENANT_B, "cabinet_id": CABINET_B, "project_id": PROJECT_B},
            ],
            "scope_owner_mappings": [
                {"id": "map_a", "scope_registry_id": "scope_a"},
                {"id": "map_b", "scope_registry_id": "scope_b"},
                {"id": "map_orphan", "scope_registry_id": None},
            ],
            "scope_owner_quarantine": [
                {"id": "q_a", "resolved_tenant_id": TENANT_A},
                {"id": "q_unresolved", "resolved_tenant_id": None},
            ],
            "scope_owner_audit": [{"id": "audit_1"}],
            "m04e_dynamic_app_definitions": [
                {"tenant_id": TENANT_A},
                {"tenant_id": TENANT_B},
            ],
            "m04e_dynamic_app_versions": [{"tenant_id": TENANT_A}, {"tenant_id": TENANT_B}],
            "m04e_definition_version_promotion_evidence": [
                {"tenant_id": TENANT_A},
                {"tenant_id": TENANT_B},
            ],
            "m05_plugin_instances": [
                {"tenant_id": TENANT_A, "cabinet_id": CABINET_A, "project_id": None},
                {"tenant_id": TENANT_A, "cabinet_id": CABINET_B, "project_id": None},
                {"tenant_id": TENANT_B, "cabinet_id": CABINET_B, "project_id": PROJECT_B},
            ],
            "m05_plugin_instance_registry_links": [
                {"tenant_id": TENANT_A, "cabinet_id": CABINET_A, "project_id": None},
            ],
            "m05_plugin_instance_promotion_evidence": [{"tenant_id": TENANT_A}],
            "dynamic_app_capability_grants": [
                {"tenant_id": TENANT_A, "cabinet_id": CABINET_A, "project_id": PROJECT_A},
                {"tenant_id": TENANT_A, "cabinet_id": None, "project_id": None},
                {"tenant_id": TENANT_B, "cabinet_id": CABINET_B, "project_id": None},
            ],
            "dynamic_app_capability_grant_mappings": [
                {"id": "gm_a", "target_grant_id": "cgrant_a"},
                {"id": "gm_b", "target_grant_id": "cgrant_b"},
                {"id": "gm_orphan", "target_grant_id": None},
            ],
            "dynamic_app_capability_grant_quarantine": [
                {"resolved_tenant_id": TENANT_A},
                {"resolved_tenant_id": None},
            ],
            "dynamic_app_capability_audit": [
                {"tenant_id": TENANT_A},
                {"tenant_id": None},
            ],
            "dynamic_app_capability_writer_epochs": [{"tenant_id": TENANT_A}],
            "m06_canonical_grant_promotion_evidence": [{"tenant_id": TENANT_A}],
            "project_runtime_revisions": [
                {"project_id": PROJECT_A},
                {"project_id": PROJECT_B},
                {"project_id": "prj_orphan"},
            ],
            "materialization_entries": [
                {"revision_id": "rr_a"},
                {"revision_id": "rr_b"},
                {"revision_id": "rr_missing"},
            ],
        }
    )
    return rows


ROWS = _rows_by_table()


def test_every_registered_table_has_representative_rows() -> None:
    missing = [spec.table for spec in CANONICAL_TABLE_POLICIES if spec.table not in ROWS]
    assert not missing, missing


def test_no_context_denies_every_row_in_every_table() -> None:
    for spec in CANONICAL_TABLE_POLICIES:
        for row in ROWS[spec.table]:
            assert scope_visible(spec, row, ROWS, CONTEXTS["no_context"]) is False, spec.table
            assert scope_visible(spec, row, ROWS, CONTEXTS["empty_context"]) is False, spec.table


def test_tenant_a_sees_only_its_own_rows() -> None:
    context = CONTEXTS["tenant_a_cabinet_a_project_a"]
    tenants = spec_for("scope_owner_tenant_anchors")
    visible = [row["tenant_id"] for row in ROWS["scope_owner_tenant_anchors"] if scope_visible(tenants, row, ROWS, context)]
    assert visible == [TENANT_A]

    versions = spec_for("m04e_dynamic_app_versions")
    visible = [row["tenant_id"] for row in ROWS["m04e_dynamic_app_versions"] if scope_visible(versions, row, ROWS, context)]
    assert visible == [TENANT_A]


def test_cross_cabinet_rows_are_denied_within_the_same_tenant() -> None:
    context = CONTEXTS["tenant_a_cabinet_a_project_a"]
    anchors = spec_for("scope_owner_cabinet_anchors")
    visible = [
        row["cabinet_id"]
        for row in ROWS["scope_owner_cabinet_anchors"]
        if scope_visible(anchors, row, ROWS, context)
    ]
    assert visible == [CABINET_A]

    instances = spec_for("m05_plugin_instances")
    visible = [
        row["cabinet_id"]
        for row in ROWS["m05_plugin_instances"]
        if scope_visible(instances, row, ROWS, context)
    ]
    assert visible == [CABINET_A]


def test_inherited_scope_requires_the_whole_parent_chain() -> None:
    context_a = CONTEXTS["tenant_a_cabinet_a_project_a"]
    context_b = CONTEXTS["tenant_b"]
    entries = spec_for("materialization_entries")
    visible_a = [row["revision_id"] for row in ROWS["materialization_entries"] if scope_visible(entries, row, ROWS, context_a)]
    visible_b = [row["revision_id"] for row in ROWS["materialization_entries"] if scope_visible(entries, row, ROWS, context_b)]
    assert visible_a == ["rr_a"]
    assert visible_b == ["rr_b"]
    # rr_missing has no reachable parent: fail-closed, not "unknown = allow".
    assert "rr_missing" not in visible_a + visible_b

    revisions = spec_for("project_runtime_revisions")
    visible = [row["project_id"] for row in ROWS["project_runtime_revisions"] if scope_visible(revisions, row, ROWS, context_a)]
    assert visible == [PROJECT_A]


def test_project_context_must_match_exactly() -> None:
    revisions = spec_for("project_runtime_revisions")
    row = {"project_id": PROJECT_A}
    wrong_project = {
        "app.tenant_id": TENANT_A,
        "app.cabinet_id": CABINET_A,
        "app.project_id": PROJECT_B,
    }
    assert scope_visible(revisions, row, ROWS, CONTEXTS["tenant_a_cabinet_a_project_a"]) is True
    assert scope_visible(revisions, row, ROWS, wrong_project) is False


def test_archived_registry_rows_are_invisible_to_tenant_sessions() -> None:
    registry = spec_for("scope_registry")
    context = CONTEXTS["tenant_a_cabinet_a_project_a"]
    visible = [row["id"] for row in ROWS["scope_registry"] if scope_visible(registry, row, ROWS, context)]
    assert visible == ["scope_a"]
    assert "scope_a_archived" not in visible


def test_orphan_mapping_and_quarantine_rows_are_tenant_invisible() -> None:
    mappings = spec_for("scope_owner_mappings")
    quarantine = spec_for("scope_owner_quarantine")
    context = CONTEXTS["tenant_a_cabinet_a_project_a"]
    assert [
        row["id"] for row in ROWS["scope_owner_mappings"] if scope_visible(mappings, row, ROWS, context)
    ] == ["map_a"]
    assert [
        row["id"] for row in ROWS["scope_owner_quarantine"] if scope_visible(quarantine, row, ROWS, context)
    ] == ["q_a"]


def test_global_audit_table_is_never_visible_to_a_tenant_session() -> None:
    audit = spec_for("scope_owner_audit")
    for context in CONTEXTS.values():
        assert scope_visible(audit, {}, ROWS, context) is False


def test_grants_accept_a_tenant_wide_row_only_inside_its_tenant() -> None:
    grants = spec_for("dynamic_app_capability_grants")
    context = CONTEXTS["tenant_a_cabinet_a_project_a"]
    rows = ROWS["dynamic_app_capability_grants"]
    assert scope_visible(grants, rows[1], ROWS, context) is True  # tenant-scoped, no cabinet
    assert scope_visible(grants, rows[2], ROWS, context) is False  # other tenant


def test_plugin_instance_scope_identity_is_not_used_as_tenant_authority() -> None:
    instances = spec_for("m05_plugin_instances")
    row = {"tenant_id": TENANT_B, "cabinet_id": CABINET_B, "project_id": None, "id": INSTANCE_A}
    # A plugin-instance row carrying another tenant stays invisible even if the
    # caller names that instance id; RLS reads only the tenant anchors.
    assert (
        scope_visible(
            instances,
            row,
            ROWS,
            {"app.tenant_id": TENANT_A, "app.cabinet_id": CABINET_B, "app.project_id": None},
        )
        is False
    )
