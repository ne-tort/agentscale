"""ORM shape tests for ModuleSource and additive CI artifact provenance."""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from prodavan.infrastructure.persistence.models.plugin_platform import ModuleSourceRow
from prodavan.infrastructure.persistence.models.plugin_platform import PluginArtifactRow


def _uniques(row: type) -> set[tuple[str, ...]]:
    return {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in row.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _checks(row: type) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in row.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }


def test_module_source_is_only_source_control_metadata_with_immutable_uniques() -> None:
    assert ModuleSourceRow.__tablename__ == "plugin_module_sources"
    assert _uniques(ModuleSourceRow) == {
        ("company_id", "plugin_id"),
        ("provider", "repo_id"),
    }
    assert isinstance(ModuleSourceRow.__table__.c.repo_id.type, BigInteger)
    assert ModuleSourceRow.__table__.c.provider.server_default.arg == "github_app"
    assert ModuleSourceRow.__table__.c.status.server_default.arg == "provisioning"
    checks = _checks(ModuleSourceRow)
    assert "40" in checks["ck_plugin_module_source_workflow_ref"]
    credential_check = checks["ck_plugin_module_source_credential_ref"]
    assert "vault://" in credential_check
    assert "secret_ref:" in credential_check
    # PostgreSQL ARE rejects repeat counts >255; the 3..512-after-scheme length
    # contract is enforced via length()/regexp_replace instead of {2,511}.
    assert "{2,511}" not in credential_check
    assert "regexp_replace" in credential_check
    assert "BETWEEN 3 AND 512" in credential_check
    assert "token" not in ModuleSourceRow.__table__.c.keys()
    assert "secret" not in {
        column.name for column in ModuleSourceRow.__table__.columns
    } - {"installation_credential_ref"}


def test_artifact_ci_columns_are_additive_and_bundle_pin_is_unchanged() -> None:
    columns = PluginArtifactRow.__table__.c
    assert columns.oci_digest.nullable is False
    assert columns.image_ref.nullable is True
    for name in (
        "source_id",
        "commit_sha",
        "backend_digest",
        "backend_image_ref",
        "signer_identity",
        "issuer",
        "run_url",
    ):
        assert columns[name].nullable is True
    assert isinstance(columns.verification_evidence.type, JSONB)
    assert columns.verification_evidence.nullable is False
    assert columns.verification_evidence.server_default.arg == "{}"
    assert columns.status.server_default.arg == "quarantined"
    assert columns.is_current.server_default.arg == "false"

    checks = _checks(PluginArtifactRow)
    assert "[0-9a-f]{40}" in checks["ck_plugin_artifact_commit_sha"]
    assert ":latest" in checks["ck_plugin_artifact_backend_image_ref"]
    assert "right(backend_image_ref, 72)" in checks["ck_plugin_artifact_backend_pair"]
    assert checks["ck_plugin_artifact_evidence_object"].endswith("'object'")
    foreign_keys = {
        constraint.name: constraint
        for constraint in PluginArtifactRow.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys["fk_plugin_artifact_source"].ondelete == "RESTRICT"
