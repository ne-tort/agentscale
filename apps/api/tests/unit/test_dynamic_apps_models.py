"""Source-level persistence acceptance checks for the dynamic app registry."""

from prodavan.infrastructure.persistence.models.dynamic_apps import (
    AppDefinitionRow,
    AppGrantRow,
    AppInstanceRow,
    AppVersionRow,
)


def test_registry_models_have_stable_tables_and_immutable_version_keys() -> None:
    assert AppDefinitionRow.__tablename__ == "dynamic_app_definitions"
    assert AppVersionRow.__tablename__ == "dynamic_app_versions"
    assert AppGrantRow.__tablename__ == "dynamic_app_grants"
    assert AppInstanceRow.__tablename__ == "dynamic_app_instances"

    constraints = {constraint.name for constraint in AppVersionRow.__table__.constraints}
    assert "uq_dynamic_app_version_manifest_version" in constraints
    assert "uq_dynamic_app_version_artifact_digest" in constraints
    assert "ck_dynamic_app_version_digest_sha256" in constraints
    assert "ck_dynamic_app_version_manifest_protocol" in constraints
    assert "fk_dynamic_app_version_rollback_from" in constraints
    assert "fk_dynamic_app_version_rollback_to" in constraints

    grant_constraints = {constraint.name for constraint in AppGrantRow.__table__.constraints}
    assert "fk_dynamic_app_grant_version_definition" in grant_constraints
    assert "ck_dynamic_app_grant_expiry_after_issue" in grant_constraints

    instance_constraints = {constraint.name for constraint in AppInstanceRow.__table__.constraints}
    assert "fk_dynamic_app_instance_version_definition" in instance_constraints


def test_registry_models_capture_scope_lifecycle_and_rollback_columns() -> None:
    assert "tenant_id" in AppDefinitionRow.__table__.c
    for model in (AppDefinitionRow, AppGrantRow, AppInstanceRow):
        assert "scope_kind" in model.__table__.c
        assert "scope_id" in model.__table__.c

    assert "rollback_from_version_id" in AppVersionRow.__table__.c
    assert "rollback_to_version_id" in AppVersionRow.__table__.c
    assert "rollback_from_version_id" in AppInstanceRow.__table__.c
    assert "rollback_to_version_id" in AppInstanceRow.__table__.c
    assert "requested_capabilities" in AppVersionRow.__table__.c
