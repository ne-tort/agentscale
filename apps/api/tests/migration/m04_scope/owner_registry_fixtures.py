"""Static M04-01 acceptance cases; these fixtures are design evidence only."""

from __future__ import annotations

from dataclasses import dataclass


TENANT_A = "co_alpha"
TENANT_B = "co_beta"
CABINET_A = "cab_alpha"
CABINET_B = "cab_beta"
PROJECT_A = "proj_alpha"
PROJECT_B = "proj_beta"


@dataclass(frozen=True)
class ScopeShapeFixture:
    case: str
    scope_kind: str
    scope_id: str
    tenant_id: str | None
    tenant_type: str | None
    cabinet_id: str | None
    cabinet_membership_state: str | None
    project_id: str | None
    project_status: str | None
    plugin_instance_id: str | None
    expected: str


VALID_TENANT = ScopeShapeFixture(
    "valid_tenant", "tenant", TENANT_A, TENANT_A, "company", None, None,
    None, None, None, "accepted by typed shape when tenant anchor resolves",
)
VALID_CABINET = ScopeShapeFixture(
    "valid_cabinet", "cabinet", CABINET_A, TENANT_A, "company", CABINET_A,
    "active", None, None, None, "requires active retained cabinet/company membership",
)
VALID_PROJECT = ScopeShapeFixture(
    "valid_project", "project", PROJECT_A, TENANT_A, "company", CABINET_A,
    "active", PROJECT_A, "active", None,
    "requires exact project/company/cabinet tuple and active membership",
)

MALFORMED_SCOPE_ID = ScopeShapeFixture(
    "malformed_scope_id", "tenant", "../co_alpha", "../co_alpha", "company",
    None, None, None, None, None, "ck_scope_registry_scope_id_well_formed",
)
OVERLENGTH_TENANT_ID = ScopeShapeFixture(
    "overlength_tenant_id", "tenant", "x" * 41, "x" * 41, "company",
    None, None, None, None, None, "storage/type rejection; quarantine before conversion",
)
OVERLENGTH_PROJECT_ID = ScopeShapeFixture(
    "overlength_project_id", "project", "x" * 41, TENANT_A, "company", CABINET_A,
    "active", "x" * 41, "active", None, "storage/type rejection; no truncation",
)
ORPHAN_TENANT = ScopeShapeFixture(
    "orphan_tenant", "tenant", "co_missing", "co_missing", "company", None, None,
    None, None, None, "fk_scope_registry_tenant RESTRICT",
)
ORPHAN_CABINET = ScopeShapeFixture(
    "orphan_cabinet", "cabinet", "cab_missing", TENANT_A, "company", "cab_missing",
    None, None, None, None, "fk_scope_registry_cabinet_typed_parent RESTRICT",
)
ORPHAN_PROJECT = ScopeShapeFixture(
    "orphan_project", "project", "proj_missing", TENANT_A, "company", CABINET_A,
    "active", "proj_missing", "active", None, "typed project parent FK rejection",
)
CROSS_TENANT_CABINET = ScopeShapeFixture(
    "cross_tenant_cabinet", "cabinet", CABINET_A, TENANT_B, "company", CABINET_A,
    "active", None, None, None, "cabinet/company grant composite FK rejection",
)
PROJECT_COMPANY_CABINET_MISMATCH = ScopeShapeFixture(
    "project_company_cabinet_mismatch", "project", PROJECT_A, TENANT_B, "company",
    CABINET_A, "active", PROJECT_A, "active", None,
    "projects (id, company_id, cabinet_id) composite FK rejection",
)
PROJECT_CABINET_MISMATCH = ScopeShapeFixture(
    "project_cabinet_mismatch", "project", PROJECT_A, TENANT_A, "company", CABINET_B,
    "active", PROJECT_A, "active", None,
    "projects (id, company_id, cabinet_id) composite FK rejection",
)
REVOKED_MEMBERSHIP = ScopeShapeFixture(
    "revoked_membership", "cabinet", CABINET_A, TENANT_A, "company", CABINET_A,
    "revoked", None, None, None,
    "deny activation after trusted transaction checks live cabinet_company_grants.status",
)
PROJECT_WITHOUT_CABINET = ScopeShapeFixture(
    "project_without_cabinet", "project", PROJECT_A, TENANT_A, "company", None,
    None, PROJECT_A, "active", None, "ck_scope_registry_typed_shape",
)
TENANT_WITH_CABINET = ScopeShapeFixture(
    "tenant_with_cabinet", "tenant", TENANT_A, TENANT_A, "company", CABINET_A,
    "active", None, None, None, "ck_scope_registry_typed_shape",
)

VALID_SHAPES = (VALID_TENANT, VALID_CABINET, VALID_PROJECT)
INVALID_OR_BLOCKED_SHAPES = (
    MALFORMED_SCOPE_ID,
    OVERLENGTH_TENANT_ID,
    OVERLENGTH_PROJECT_ID,
    ORPHAN_TENANT,
    ORPHAN_CABINET,
    ORPHAN_PROJECT,
    CROSS_TENANT_CABINET,
    PROJECT_COMPANY_CABINET_MISMATCH,
    PROJECT_CABINET_MISMATCH,
    REVOKED_MEMBERSHIP,
    PROJECT_WITHOUT_CABINET,
    TENANT_WITH_CABINET,
)

RLS_ACCEPTANCE_CASES = (
    {
        "case": "no_tenant_context",
        "context": {},
        "expected": "deny using transaction-local app.tenant_id; ENABLE+FORCE RLS required",
    },
    {
        "case": "empty_tenant_context",
        "context": {"app.tenant_id": ""},
        "expected": "deny, never platform/unrestricted fallback",
    },
    {
        "case": "cross_tenant_context",
        "context": {"app.tenant_id": TENANT_B},
        "row_tenant_id": TENANT_A,
        "expected": "deny by USING and WITH CHECK",
    },
    {
        "case": "cross_cabinet_context",
        "context": {"app.tenant_id": TENANT_A, "app.cabinet_id": CABINET_B},
        "row_cabinet_id": CABINET_A,
        "expected": "deny; require matching cabinet anchor and active membership EXISTS",
    },
    {
        "case": "cross_project_context",
        "context": {
            "app.tenant_id": TENANT_A,
            "app.cabinet_id": CABINET_A,
            "app.project_id": PROJECT_B,
        },
        "row_project_id": PROJECT_A,
        "expected": "deny; require exact project/company/cabinet parent tuple",
    },
)

ACCEPTANCE_BOUNDARY = {
    "execution": "not run",
    "backfill": False,
    "positive_canonical_writes": False,
    "plugin_instance_positive_branch": False,
    "rls_ddl_installed": False,
}
