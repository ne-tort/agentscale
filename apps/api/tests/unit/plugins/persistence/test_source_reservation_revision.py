"""Static checks for reservation ORM/revision; no Alembic or DB execution."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, UniqueConstraint

from prodavan.infrastructure.persistence.models.plugin_platform import (
    PluginSourceReservationRow,
)


def _load():
    root = Path(__file__).resolve().parents[6]
    path = (
        root
        / "apps/api/alembic/versions/2026110903_plugin_source_reservations.py"
    )
    spec = importlib.util.spec_from_file_location("revision_2026110903_sources", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _uniques() -> set[tuple[str, ...]]:
    return {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in PluginSourceReservationRow.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_reservation_is_separate_from_actual_numeric_repo_identity() -> None:
    columns = PluginSourceReservationRow.__table__.c
    assert PluginSourceReservationRow.__tablename__ == "plugin_source_reservations"
    assert "repo_id" not in columns
    assert "full_name" not in columns
    assert columns.expected_full_name.nullable is False
    assert columns.expected_default_branch.nullable is False
    assert columns.expected_private.nullable is False
    assert columns.create_in_progress.server_default.arg == "true"
    assert _uniques() == {
        ("company_id", "plugin_id"),
        ("expected_full_name", "provider"),
    }
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in PluginSourceReservationRow.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    assert "provisioning" in checks["ck_plugin_source_reservation_completed_attempt"]
    assert "ready" in checks["ck_plugin_source_reservation_status"]
    assert "40" in checks["ck_plugin_source_reservation_workflow_ref"]


def test_revision_follows_publication_approval_and_matches_orm() -> None:
    module = _load()
    assert module.revision == "2026110903"
    assert module.down_revision == "2026110902"
    assert module.branch_labels is None
    assert module.depends_on is None

    op = Mock()
    module.op = op
    module.upgrade()
    op.create_table.assert_called_once()
    call = op.create_table.call_args
    assert call.args[0] == "plugin_source_reservations"
    columns = {
        item.name for item in call.args[1:] if isinstance(item, sa.Column)
    }
    # 0903 predates the 0909 installation-scope columns.
    baseline_columns = set(PluginSourceReservationRow.__table__.c.keys()) - {
        "cabinet_id", "project_id"
    }
    assert columns == baseline_columns
    assert "repo_id" not in columns
    assert not op.execute.called
    assert not op.alter_column.called
    assert not op.add_column.called
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
    for text in (module._SECRET_REF_RE, module._SECRET_REF_LENGTH_SQL):
        for match in re.finditer(r"\{(\d+)(?:,(\d*))?\}", text):
            assert int(match.group(1)) <= 255
            if match.group(2):
                assert int(match.group(2)) <= 255

    op = Mock()
    module.op = op
    module.upgrade()
    call = op.create_table.call_args
    revision_checks = {
        item.name: str(item.sqltext)
        for item in call.args[1:]
        if isinstance(item, sa.CheckConstraint)
    }
    orm_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in PluginSourceReservationRow.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name
    }
    assert (
        revision_checks["ck_plugin_source_reservation_credential_ref"]
        == orm_checks["ck_plugin_source_reservation_credential_ref"]
    )
    assert "BETWEEN 3 AND 512" in revision_checks["ck_plugin_source_reservation_credential_ref"]


def test_downgrade_drops_only_reservation_table_and_index() -> None:
    module = _load()
    op = Mock()
    module.op = op
    module.downgrade()

    assert op.drop_index.call_args.args[0] == "ix_plugin_source_reservations_company"
    assert op.drop_table.call_args.args[0] == "plugin_source_reservations"
    assert not op.execute.called
    assert not op.drop_column.called
