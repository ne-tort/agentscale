"""Static/unit checks for additive revision 2026110901 (no Alembic/DB run)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import sqlalchemy as sa

from prodavan.infrastructure.persistence.models.plugin_platform import ModuleSourceRow
from prodavan.infrastructure.persistence.models.plugin_platform import PluginArtifactRow


def _load():
    root = Path(__file__).resolve().parents[6]
    path = root / "apps/api/alembic/versions/2026110901_plugin_ci_publication.py"
    spec = importlib.util.spec_from_file_location("revision_2026110901_ci", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_points_to_real_0801_head_not_pending_0802() -> None:
    module = _load()
    assert module.revision == "2026110901"
    assert module.down_revision == "2026110801"
    assert module.branch_labels is None
    assert module.depends_on is None


def test_upgrade_is_additive_and_matches_orm_columns() -> None:
    module = _load()
    op = Mock()
    module.op = op
    module.upgrade()

    assert op.create_table.call_count == 1
    source_call = op.create_table.call_args
    assert source_call.args[0] == "plugin_module_sources"
    source_columns = {
        item.name for item in source_call.args[1:] if isinstance(item, sa.Column)
    }
    # 0901 predates the 0909 installation-scope columns; compare the
    # revision-owned baseline, not the later 0909 source projection.
    baseline_columns = set(ModuleSourceRow.__table__.c.keys()) - {
        "cabinet_id", "project_id"
    }
    assert source_columns == baseline_columns

    added = [call.args[1].name for call in op.add_column.call_args_list]
    assert added == [
        "source_id",
        "commit_sha",
        "backend_digest",
        "backend_image_ref",
        "signer_identity",
        "issuer",
        "run_url",
        "verification_evidence",
    ]
    assert set(added) <= set(PluginArtifactRow.__table__.c.keys())
    checks = [call.args[0] for call in op.create_check_constraint.call_args_list]
    assert checks == [
        "ck_plugin_artifact_commit_sha",
        "ck_plugin_artifact_backend_digest",
        "ck_plugin_artifact_backend_image_ref",
        "ck_plugin_artifact_backend_pair",
        "ck_plugin_artifact_evidence_object",
    ]
    fk = op.create_foreign_key.call_args
    assert fk.args[0] == "fk_plugin_artifact_source"
    assert fk.args[1:3] == ("plugin_artifacts", "plugin_module_sources")
    assert fk.kwargs["ondelete"] == "RESTRICT"
    assert not op.execute.called
    assert not op.alter_column.called
    assert not op.drop_column.called
    assert not op.drop_table.called


def test_secret_ref_check_is_pg_compatible_and_matches_orm() -> None:
    import re

    from prodavan.infrastructure.persistence.models.plugin_platform import (
        MODULE_SOURCE_SECRET_REF_LENGTH_SQL,
        MODULE_SOURCE_SECRET_REF_SQL,
    )

    module = _load()
    assert module._SECRET_REF_RE == MODULE_SOURCE_SECRET_REF_SQL
    assert module._SECRET_REF_LENGTH_SQL == MODULE_SOURCE_SECRET_REF_LENGTH_SQL
    # PostgreSQL ARE rejects repetition counts above 255; the former {2,511}
    # made every plugin_module_sources INSERT fail with InvalidRegularExpressionError.
    for text in (module._SECRET_REF_RE, module._SECRET_REF_LENGTH_SQL):
        for match in re.finditer(r"\{(\d+)(?:,(\d*))?\}", text):
            assert int(match.group(1)) <= 255
            if match.group(2):
                assert int(match.group(2)) <= 255

    op = Mock()
    module.op = op
    module.upgrade()
    source_call = op.create_table.call_args
    revision_checks = {
        item.name: str(item.sqltext)
        for item in source_call.args[1:]
        if isinstance(item, sa.CheckConstraint)
    }
    orm_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in ModuleSourceRow.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name
    }
    assert (
        revision_checks["ck_plugin_module_source_credential_ref"]
        == orm_checks["ck_plugin_module_source_credential_ref"]
    )
    assert "BETWEEN 3 AND 512" in revision_checks["ck_plugin_module_source_credential_ref"]


def test_revision_does_not_reactivate_or_rewrite_existing_artifacts() -> None:
    module = _load()
    op = Mock()
    module.op = op
    module.upgrade()
    for call in op.add_column.call_args_list:
        column = call.args[1]
        if column.name == "verification_evidence":
            assert column.nullable is False
            assert column.server_default.arg == "{}"
        else:
            assert column.nullable is True
    # There is intentionally no UPDATE/status/is_current/published_at backfill.
    assert not op.execute.called
    assert not op.bulk_insert.called
    assert not op.alter_column.called


def test_downgrade_drops_only_revision_owned_objects() -> None:
    module = _load()
    op = Mock()
    module.op = op
    module.downgrade()
    dropped_columns = [call.args[1] for call in op.drop_column.call_args_list]
    assert dropped_columns == [
        "verification_evidence",
        "run_url",
        "issuer",
        "signer_identity",
        "backend_image_ref",
        "backend_digest",
        "commit_sha",
        "source_id",
    ]
    assert op.drop_table.call_args.args[0] == "plugin_module_sources"
    assert not op.execute.called
