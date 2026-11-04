"""M04-01 ORM contract assertions; authored but intentionally not executed."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from prodavan.infrastructure.persistence.models.owner_registry import (
    CanonicalTenantAnchorRow,
    ScopeOwnerCabinetAnchorRow,
    ScopeOwnerProjectAnchorRow,
    ScopeRegistryRow,
)
from .owner_registry_fixtures import (
    ACCEPTANCE_BOUNDARY,
    INVALID_OR_BLOCKED_SHAPES,
    RLS_ACCEPTANCE_CASES,
    VALID_SHAPES,
)


def _constraints(model):
    return model.__table__.constraints


def test_tenant_key_is_typed_alias_of_company_id_and_restrictive() -> None:
    table = CanonicalTenantAnchorRow.__table__
    tenant_id = table.c.tenant_id
    assert tenant_id.type.length == 40
    assert tenant_id.nullable is False
    assert isinstance(tenant_id.type.python_type, type)
    assert any(
        isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_scope_owner_tenant_company"
        and constraint.ondelete == "RESTRICT"
        for constraint in _constraints(CanonicalTenantAnchorRow)
    )
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_scope_owner_tenant_type"
        for constraint in _constraints(CanonicalTenantAnchorRow)
    )


def test_registry_has_typed_shape_unique_keys_and_deferred_plugin_gate() -> None:
    constraints = _constraints(ScopeRegistryRow)
    names = {constraint.name for constraint in constraints}
    assert "uq_scope_registry_kind_id" in names
    assert "uq_scope_registry_typed_anchor" in names
    assert "ck_scope_registry_typed_shape" in names
    assert "ck_scope_registry_plugin_deferred" in names
    typed_shape = next(
        constraint for constraint in constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_scope_registry_typed_shape"
    )
    assert "scope_kind = 'tenant'" in str(typed_shape.sqltext)
    assert "scope_kind = 'cabinet'" in str(typed_shape.sqltext)
    assert "scope_kind = 'project'" in str(typed_shape.sqltext)
    assert "scope_kind = 'plugin_instance'" in str(typed_shape.sqltext)
    assert ScopeRegistryRow.__table__.c.tenant_id.type.length == 40
    assert ScopeRegistryRow.__table__.c.scope_id.type.length == 40
    assert ScopeRegistryRow.__table__.c.owner_state.default.arg == "quarantined"


def test_scope_parents_are_restrictive_and_project_parent_tuple_is_composite() -> None:
    constraints = _constraints(ScopeRegistryRow)
    foreign_keys = {
        constraint.name: constraint
        for constraint in constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys["fk_scope_registry_project_typed_parent"].ondelete == "RESTRICT"
    project_fk = foreign_keys["fk_scope_registry_project_typed_parent"]
    # The registry parent FK is the *state-bearing* anchor key, not just the three
    # identity columns: it additionally pins tenant_type, project_status and
    # owner_state, so a registry row can only reference a lifecycle-matched anchor
    # (revision 2026110301 and ``ScopeOwnerProjectAnchorRow`` define this tuple).
    assert len(project_fk.columns) == 6
    assert [element.target_fullname for element in project_fk.elements] == [
        "scope_owner_project_anchors.project_id",
        "scope_owner_project_anchors.tenant_id",
        "scope_owner_project_anchors.tenant_type",
        "scope_owner_project_anchors.cabinet_id",
        "scope_owner_project_anchors.project_status",
        "scope_owner_project_anchors.owner_state",
    ]
    cabinet_fk = foreign_keys["fk_scope_registry_cabinet_typed_parent"]
    assert cabinet_fk.ondelete == "RESTRICT"
    assert len(cabinet_fk.columns) == 4
    assert [element.target_fullname for element in cabinet_fk.elements] == [
        "scope_owner_cabinet_anchors.cabinet_id",
        "scope_owner_cabinet_anchors.tenant_id",
        "scope_owner_cabinet_anchors.tenant_type",
        "scope_owner_cabinet_anchors.owner_state",
    ]


def test_cabinet_anchor_restricts_both_typed_parents_and_scope_fk() -> None:
    anchor_constraints = _constraints(ScopeOwnerCabinetAnchorRow)
    anchor_fks = {
        constraint.name: constraint
        for constraint in anchor_constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert anchor_fks["fk_scope_owner_cabinet_anchor_cabinet"].ondelete == "RESTRICT"
    assert anchor_fks["fk_scope_owner_cabinet_anchor_tenant"].ondelete == "RESTRICT"
    assert any(
        isinstance(constraint, UniqueConstraint)
        # Name as defined by the model and revision 2026110301: the unique key is
        # the typed (cabinet, tenant, tenant_type) tuple *plus* owner_state.
        and constraint.name == "uq_scope_owner_cabinet_anchor_typed_tuple_state"
        for constraint in anchor_constraints
    )


def test_project_anchor_proves_company_cabinet_tuple() -> None:
    constraints = _constraints(ScopeOwnerProjectAnchorRow)
    foreign_keys = {
        constraint.name: constraint
        for constraint in constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    project_fk = foreign_keys["fk_scope_owner_project_anchor_project_parent"]
    assert project_fk.ondelete == "RESTRICT"
    assert [element.target_fullname for element in project_fk.elements] == [
        "projects.id", "projects.company_id", "projects.cabinet_id",
    ]


def test_fixture_matrix_covers_valid_invalid_and_rls_context_cases() -> None:
    assert {fixture.case for fixture in VALID_SHAPES} == {
        "valid_tenant", "valid_cabinet", "valid_project",
    }
    cases = {fixture.case for fixture in INVALID_OR_BLOCKED_SHAPES}
    assert {
        "malformed_scope_id", "overlength_tenant_id", "orphan_tenant",
        "orphan_cabinet", "orphan_project", "cross_tenant_cabinet",
        "project_company_cabinet_mismatch", "project_cabinet_mismatch",
        "revoked_membership", "project_without_cabinet", "tenant_with_cabinet",
    } <= cases
    rls_cases = {fixture["case"] for fixture in RLS_ACCEPTANCE_CASES}
    assert {
        "no_tenant_context", "empty_tenant_context", "cross_tenant_context",
        "cross_cabinet_context", "cross_project_context",
    } <= rls_cases
    assert ACCEPTANCE_BOUNDARY == {
        "execution": "not run",
        "backfill": False,
        "positive_canonical_writes": False,
        "plugin_instance_positive_branch": False,
        "rls_ddl_installed": False,
    }
