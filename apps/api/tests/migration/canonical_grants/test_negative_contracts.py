"""Negative and boundary contracts for the canonical grant tables.

Each check below pins the *structural* enforcement of a negative case that the
MIG-DDL-M06-01 acceptance requires. The database behaviour itself is described
in ``acceptance.md`` and will be exercised once the migration verification
freeze is lifted; while the freeze holds these tests assert that the schema
which must produce that behaviour is really the one we created.

Cases covered:

1. a grant bound to a foreign ``plugin_instance`` (other tenant, scope, version
   or definition) cannot be inserted;
2. a stale writer generation cannot be accepted (fence state is a real table
   and generations are positive and non-null; the comparison itself belongs to
   the revision-``2026110307`` policy, because a CHECK cannot read another
   table);
3. a duplicate *active* grant is rejected, while a revoked or expired one is
   kept as evidence;
4. no cascade path may delete canonical authorization evidence; dependent
   evidence tables degrade to ``SET NULL`` instead.
"""

from __future__ import annotations

import sqlalchemy as sa

from prodavan.infrastructure.persistence.models import canonical_grants
from prodavan.infrastructure.persistence.models import plugin_instance_registry
from prodavan.infrastructure.persistence.models.base import Base


GRANTS = Base.metadata.tables["dynamic_app_capability_grants"]
AUDIT = Base.metadata.tables["dynamic_app_capability_audit"]
MAPPINGS = Base.metadata.tables["dynamic_app_capability_grant_mappings"]
QUARANTINE = Base.metadata.tables["dynamic_app_capability_grant_quarantine"]
EPOCHS = Base.metadata.tables["dynamic_app_capability_writer_epochs"]
PROMOTION = Base.metadata.tables["m06_canonical_grant_promotion_evidence"]
INSTANCES = Base.metadata.tables["m05_plugin_instances"]


def _foreign_keys(table: sa.Table) -> dict[str, sa.ForeignKeyConstraint]:
    return {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    }


def _unique_keys(table: sa.Table) -> dict[str, tuple[str, ...]]:
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def _index(name: str) -> sa.Index:
    return next(index for index in GRANTS.indexes if index.name == name)


# 1. Grant on a foreign instance ------------------------------------------------

def test_instance_binding_is_a_six_column_composite_foreign_key() -> None:
    constraint = _foreign_keys(GRANTS)["fk_cap_grant_instance_exact"]

    assert tuple(element.parent.name for element in constraint.elements) == (
        "plugin_instance_id",
        "app_definition_id",
        "app_version_id",
        "tenant_id",
        "scope_kind",
        "scope_id",
    )
    assert tuple(element.target_fullname for element in constraint.elements) == (
        "m05_plugin_instances.id",
        "m05_plugin_instances.definition_id",
        "m05_plugin_instances.version_id",
        "m05_plugin_instances.tenant_id",
        "m05_plugin_instances.scope_kind",
        "m05_plugin_instances.scope_id",
    )
    assert constraint.ondelete == "RESTRICT"


def test_instance_target_key_exists_on_the_instance_table() -> None:
    # A composite FK is only valid against a real unique key on the parent.
    assert _unique_keys(INSTANCES)["uq_m05_plugin_instance_grant_identity"] == (
        "id",
        "definition_id",
        "version_id",
        "tenant_id",
        "scope_kind",
        "scope_id",
    )
    assert plugin_instance_registry.PluginInstanceRegistryRow.__tablename__ == "m05_plugin_instances"


def test_there_is_no_single_column_instance_or_version_shortcut() -> None:
    for constraint in _foreign_keys(GRANTS).values():
        parents = tuple(element.parent.name for element in constraint.elements)
        assert parents not in {("plugin_instance_id",), ("app_version_id",), ("app_id",)}


def test_plugin_instance_scope_must_name_the_bound_instance() -> None:
    shape = next(
        constraint
        for constraint in GRANTS.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name == "ck_cap_grant_scope_shape"
    )
    sql = str(shape.sqltext)

    assert "scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id" in sql
    assert "plugin_instance_id IS NOT NULL" in sql
    # An instance-independent row may not smuggle in a session binding.
    binding = next(
        constraint
        for constraint in GRANTS.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name == "ck_cap_grant_plugin_binding"
    )
    assert str(binding.sqltext) == "plugin_instance_id IS NOT NULL OR session_id IS NULL"


def test_version_binding_proves_definition_and_artifact_together() -> None:
    constraint = _foreign_keys(GRANTS)["fk_cap_grant_version_definition_artifact"]

    assert tuple(element.parent.name for element in constraint.elements) == (
        "app_version_id",
        "app_definition_id",
        "app_artifact_digest",
    )
    assert tuple(element.target_fullname for element in constraint.elements) == (
        "m04e_dynamic_app_versions.id",
        "m04e_dynamic_app_versions.definition_id",
        "m04e_dynamic_app_versions.artifact_digest",
    )
    assert constraint.ondelete == "RESTRICT"


# 2. Stale writer generation ----------------------------------------------------

def test_generations_are_positive_and_never_nullable() -> None:
    for column_name in ("grant_generation", "writer_generation"):
        column = GRANTS.c[column_name]
        assert column.nullable is False
        assert isinstance(column.type, sa.BigInteger)

    check = next(
        constraint
        for constraint in GRANTS.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name == "ck_cap_grant_generation"
    )
    assert str(check.sqltext) == "grant_generation > 0 AND writer_generation > 0"


def test_fence_epoch_is_bound_to_one_partition_identity() -> None:
    assert _unique_keys(EPOCHS)["uq_cap_grant_writer_epoch_partition"] == (
        "tenant_id",
        "app_definition_id",
        "scope_kind",
        "scope_id",
    )
    assert _unique_keys(EPOCHS)["uq_cap_grant_writer_epoch_partition_id"] == ("partition_id",)

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in EPOCHS.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert checks["ck_cap_grant_writer_epoch_positive"] == "writer_epoch > 0"
    assert "partition_id = tenant_id || ':' || app_definition_id" in (
        checks["ck_cap_grant_writer_epoch_partition_identity"]
    )


def test_fence_state_is_bound_to_a_real_scope_and_definition() -> None:
    constraints = _foreign_keys(EPOCHS)

    assert tuple(element.parent.name for element in constraints["fk_cap_grant_writer_epoch_scope"].elements) == (
        "scope_kind",
        "scope_id",
    )
    assert tuple(
        element.target_fullname
        for element in constraints["fk_cap_grant_writer_epoch_scope"].elements
    ) == ("scope_registry.scope_kind", "scope_registry.scope_id")
    assert constraints["fk_cap_grant_writer_epoch_definition_tenant"].ondelete == "RESTRICT"


def test_stale_generation_denial_is_documented_as_policy_owned() -> None:
    # A CHECK constraint cannot compare a row against the epoch table, so the
    # comparison must live in the trusted-writer policy of revision 2026110307.
    cases = set(canonical_grants.M06_DDL_OWNERSHIP_CASES)
    assert "stale_writer_generation_cannot_write_under_the_partition_epoch" in cases
    assert "rls_no_context_and_cross_tenant_using_with_check_deny" in set(
        canonical_grants.M06_ACCEPTANCE_CASES
    )


# 3. Duplicate active grant -----------------------------------------------------

def test_active_uniqueness_covers_the_whole_authorization_tuple() -> None:
    index = _index("uq_cap_grant_active_identity")

    assert index.unique is True
    assert [str(expression) for expression in index.expressions] == [
        "dynamic_app_capability_grants.tenant_id",
        "dynamic_app_capability_grants.app_version_id",
        "dynamic_app_capability_grants.capability_key",
        "dynamic_app_capability_grants.subject_kind",
        "dynamic_app_capability_grants.subject_id",
        "COALESCE(plugin_instance_id, '')",
        "dynamic_app_capability_grants.scope_kind",
        "dynamic_app_capability_grants.scope_id",
        "dynamic_app_capability_grants.operations_digest",
        "dynamic_app_capability_grants.resources_digest",
        "dynamic_app_capability_grants.domains_digest",
        "dynamic_app_capability_grants.constraints_digest",
    ]


def test_active_uniqueness_excludes_revoked_rows_and_never_uses_the_clock() -> None:
    index = _index("uq_cap_grant_active_identity")
    where = str(index.dialect_options["postgresql"]["where"])

    assert where == "revoked_at IS NULL"
    # Expiry is a read-time deny; a moving clock in the predicate would make the
    # index non-deterministic and silently free up a still-unique identity.
    assert "expires_at" not in where
    assert not any("expires_at" in str(expression) for expression in index.expressions)


def test_idempotency_key_is_unique_per_tenant() -> None:
    assert _unique_keys(GRANTS)["uq_cap_grant_idempotency"] == ("tenant_id", "idempotency_key")


def test_authorize_lookup_index_is_partial_on_active_rows() -> None:
    index = _index("ix_cap_grant_authorize")

    assert index.unique is False
    assert str(index.dialect_options["postgresql"]["where"]) == "revoked_at IS NULL"
    assert tuple(column.name for column in index.columns) == (
        "tenant_id",
        "scope_kind",
        "scope_id",
        "app_definition_id",
        "app_version_id",
        "capability_key",
        "subject_kind",
        "subject_id",
        "plugin_instance_id",
    )


# 4. Cascade versus retention ---------------------------------------------------

def test_no_canonical_table_allows_a_cascade_delete() -> None:
    for table in (GRANTS, AUDIT, MAPPINGS, QUARANTINE, EPOCHS, PROMOTION):
        for constraint in _foreign_keys(table).values():
            assert constraint.ondelete != "CASCADE", (table.name, constraint.name)


def test_grant_provenance_fks_restrict_owner_cleanup() -> None:
    constraints = _foreign_keys(GRANTS)

    assert {
        name: constraint.ondelete
        for name, constraint in constraints.items()
        if name.startswith("fk_cap_grant_")
    } == {
        "fk_cap_grant_version_definition_artifact": "RESTRICT",
        "fk_cap_grant_definition": "RESTRICT",
        "fk_cap_grant_definition_tenant": "RESTRICT",
        "fk_cap_grant_scope_registry": "RESTRICT",
        "fk_cap_grant_instance_exact": "RESTRICT",
    }


def test_dependent_evidence_survives_grant_removal() -> None:
    assert _foreign_keys(AUDIT)["fk_cap_audit_grant"].ondelete == "SET NULL"
    assert _foreign_keys(MAPPINGS)["fk_cap_grant_mapping_target"].ondelete == "SET NULL"
    assert _foreign_keys(PROMOTION)["fk_m06_grant_promotion_definition_tenant"].ondelete == "RESTRICT"


def test_evidence_tables_keep_immutable_source_identity_and_digest() -> None:
    assert _unique_keys(MAPPINGS)["uq_cap_grant_mapping_source"] == (
        "source_kind",
        "source_id",
        "canonical_schema_version",
    )
    assert _unique_keys(QUARANTINE)["uq_cap_grant_quarantine_observation"] == (
        "source_kind",
        "source_id",
        "schema_version",
        "source_payload_digest",
    )
    assert _unique_keys(AUDIT)["uq_cap_audit_event"] == ("event_id",)
    assert _unique_keys(PROMOTION)["uq_m06_grant_promotion_revision"] == (
        "tenant_id",
        "definition_id",
        "partition_id",
        "revision_id",
    )


def test_promotion_evidence_can_never_allow_positive_writes() -> None:
    check = next(
        constraint
        for constraint in PROMOTION.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name == "ck_m06_grant_no_positive_writes"
    )

    assert str(check.sqltext) == "canonical_positive_writes = false"
    assert PROMOTION.c["canonical_positive_writes"].nullable is False


def test_quarantine_carries_no_raw_payload_only_redacted_metadata() -> None:
    names = set(QUARANTINE.c.keys())

    assert "redacted_metadata" in names
    for forbidden in ("raw_payload", "payload", "token", "secret", "credentials", "arguments"):
        assert forbidden not in names
