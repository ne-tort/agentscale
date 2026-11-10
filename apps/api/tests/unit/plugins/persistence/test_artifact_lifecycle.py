"""Static ORM/revision shape checks for artifact lifecycle (not run in freeze)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
)

from prodavan.infrastructure.persistence import models
from prodavan.infrastructure.persistence.models import artifact_lifecycle
from prodavan.infrastructure.persistence.models.artifact_lifecycle import (
    PluginArtifactLifecycleRow,
)
from prodavan.infrastructure.persistence.models.base import Base

EXPECTED_COLUMNS = {
    "id",
    "artifact_id",
    "operation",
    "actor",
    "detail",
    "occurred_at",
    "operation_token",
}
MODEL_COLUMNS = {*EXPECTED_COLUMNS, "command_fingerprint"}
HISTORY_INDEX = "ix_plugin_artifact_lifecycle_artifact_occurred_at"
TOKEN_UNIQUE = "uq_plugin_artifact_lifecycle_artifact_operation_token"


def _revision():
    path = (
        Path(__file__).resolve().parents[4]
        / "alembic/versions/2026110906_artifact_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("revision_2026110906_lifecycle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rollback_revision():
    path = (
        Path(__file__).resolve().parents[4]
        / "alembic/versions/2026110907_artifact_rollback_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("revision_2026110907_lifecycle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _created_items(module) -> tuple[object, ...]:
    op = Mock()
    module.op = op
    module.upgrade()
    op.create_table.assert_called_once()
    return op.create_table.call_args.args[1:]


def test_model_export_and_append_only_secret_free_shape() -> None:
    table = PluginArtifactLifecycleRow.__table__
    assert models.PluginArtifactLifecycleRow is PluginArtifactLifecycleRow
    assert "PluginArtifactLifecycleRow" in models.__all__
    assert table.name == "plugin_artifact_lifecycle"
    assert set(table.c.keys()) == MODEL_COLUMNS

    # There is no mutable lifecycle/status timestamp or generic raw payload.
    assert not {"created_at", "updated_at", "deleted_at", "status", "payload"} & set(
        table.c.keys()
    )
    assert all(column.onupdate is None for column in table.c)
    assert all(column.server_onupdate is None for column in table.c)
    assert all(table.c[name].nullable is False for name in EXPECTED_COLUMNS)
    assert table.c.command_fingerprint.nullable is True
    assert table.c.command_fingerprint.type.length == 64
    assert "reason" not in table.c
    assert table.c.id.primary_key is True
    assert isinstance(table.c.detail.type, Text)
    assert isinstance(table.c.occurred_at.type, DateTime)
    assert table.c.occurred_at.type.timezone is True

    forbidden_names = {
        "password",
        "secret",
        "secrets",
        "secret_value",
        "credentials",
        "authorization",
        "cookie",
        "access_token",
        "refresh_token",
        "private_key",
        "raw_payload",
        "request_body",
        "response_body",
    }
    assert not forbidden_names & set(table.c.keys())
    # operation_token is an idempotency key, not an authentication credential.
    assert "token" not in table.c


def test_model_has_no_update_delete_listeners_or_column_hooks() -> None:
    source = inspect.getsource(artifact_lifecycle)
    forbidden_markers = (
        "listens_for",
        "before_update",
        "before_delete",
        "after_update",
        "after_delete",
        "onupdate",
    )
    assert not any(marker in source for marker in forbidden_markers)


def test_model_has_exact_token_unique_restrict_fk_checks_and_history_index() -> None:
    table = PluginArtifactLifecycleRow.__table__
    uniques = [item for item in table.constraints if isinstance(item, UniqueConstraint)]
    assert any(
        item.name == TOKEN_UNIQUE
        and tuple(column.name for column in item.columns)
        == ("artifact_id", "operation_token")
        for item in uniques
    )

    foreign_keys = [
        item for item in table.constraints if isinstance(item, ForeignKeyConstraint)
    ]
    assert len(foreign_keys) == 1
    assert foreign_keys[0].ondelete == "RESTRICT"
    assert foreign_keys[0].elements[0].target_fullname == "plugin_artifacts.id"

    checks = {
        item.name: str(item.sqltext)
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name
    }
    operation_check = checks["ck_plugin_artifact_lifecycle_operation"]
    assert all(
        f"'{operation}'" in operation_check
        for operation in ("publish", "promote", "rollback")
    )
    assert "actor" in checks["ck_plugin_artifact_lifecycle_actor_nonempty"]
    assert "operation_token" in checks[
        "ck_plugin_artifact_lifecycle_operation_token_nonempty"
    ]

    history = next(item for item in table.indexes if item.name == HISTORY_INDEX)
    assert history.unique is False
    assert tuple(column.name for column in history.columns) == (
        "artifact_id",
        "occurred_at",
    )


def test_revision_0906_is_additive_and_has_no_backfill_or_trigger_logic() -> None:
    module = _revision()
    assert module.revision == "2026110906"
    assert module.down_revision == "2026110905"
    assert module.branch_labels is None
    assert module.depends_on is None

    op = Mock()
    module.op = op
    module.upgrade()

    assert [item[0] for item in op.method_calls] == ["create_table", "create_index"]
    assert op.create_table.call_args.args[0] == "plugin_artifact_lifecycle"
    assert op.create_index.call_args.args == (
        HISTORY_INDEX,
        "plugin_artifact_lifecycle",
        ["artifact_id", "occurred_at"],
    )
    assert not op.execute.called
    assert not op.bulk_insert.called
    assert not op.add_column.called
    assert not op.alter_column.called
    assert not op.drop_column.called
    assert not op.create_foreign_key.called
    assert not op.drop_table.called


def test_revision_columns_constraints_and_fk_match_model() -> None:
    module = _revision()
    items = _created_items(module)
    # Attach captured items only to in-memory metadata; no DB/Alembic execution.
    table = sa.Table("plugin_artifact_lifecycle", sa.MetaData(), *items)
    assert set(table.c.keys()) == EXPECTED_COLUMNS
    assert table.c.occurred_at.type.timezone is True
    assert isinstance(table.c.detail.type, Text)
    assert all(table.c[name].nullable is False for name in EXPECTED_COLUMNS)

    assert any(
        isinstance(item, UniqueConstraint)
        and item.name == TOKEN_UNIQUE
        and tuple(column.name for column in item.columns)
        == ("artifact_id", "operation_token")
        for item in table.constraints
    )
    assert any(
        isinstance(item, ForeignKeyConstraint)
        and item.ondelete == "RESTRICT"
        and item.elements[0].target_fullname == "plugin_artifacts.id"
        for item in table.constraints
    )
    check_names = {
        item.name
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name
    }
    assert check_names == {
        "ck_plugin_artifact_lifecycle_operation",
        "ck_plugin_artifact_lifecycle_actor_nonempty",
        "ck_plugin_artifact_lifecycle_operation_token_nonempty",
    }


def test_downgrade_drops_only_lifecycle_objects() -> None:
    module = _revision()
    op = Mock()
    module.op = op
    module.downgrade()

    assert [item[0] for item in op.method_calls] == ["drop_index", "drop_table"]
    assert op.drop_index.call_args.args[0] == HISTORY_INDEX
    assert op.drop_index.call_args.kwargs == {"table_name": "plugin_artifact_lifecycle"}
    assert op.drop_table.call_args.args[0] == "plugin_artifact_lifecycle"
    assert not op.execute.called


def _fingerprint_revision():
    path = (
        Path(__file__).resolve().parents[4]
        / "alembic/versions/2026110908_rollback_command_fingerprint.py"
    )
    spec = importlib.util.spec_from_file_location(
        "revision_2026110908_command_fingerprint", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0908_adds_only_nullable_fingerprint_and_drops_it_on_downgrade() -> None:
    module = _fingerprint_revision()
    assert module.revision == "2026110908"
    assert module.down_revision == "2026110907"
    assert module.branch_labels is None
    assert module.depends_on is None

    op = Mock()
    module.op = op
    module.upgrade()
    op.add_column.assert_called_once()
    assert op.add_column.call_args.args[0] == "plugin_artifact_lifecycle"
    column = op.add_column.call_args.args[1]
    assert column.name == "command_fingerprint"
    assert column.type.length == 64
    assert column.nullable is True
    assert not op.execute.called
    assert not op.bulk_insert.called
    assert not op.alter_column.called

    op = Mock()
    module.op = op
    module.downgrade()
    op.drop_column.assert_called_once_with(
        "plugin_artifact_lifecycle", "command_fingerprint"
    )


def test_revision_0907_is_registered_in_metadata_and_extends_0906() -> None:
    parent = _revision()
    module = _rollback_revision()

    assert parent.revision == "2026110906"
    assert parent.down_revision == "2026110905"
    assert module.revision == "2026110907"
    assert module.down_revision == parent.revision
    assert module.branch_labels is None
    assert module.depends_on is None
    assert (
        Base.metadata.tables["plugin_artifact_lifecycle"]
        is PluginArtifactLifecycleRow.__table__
    )


def test_revision_0907_upgrade_matches_model_operation_vocabulary() -> None:
    module = _rollback_revision()
    op = Mock()
    module.op = op
    module.upgrade()

    expected_check = "operation IN ('publish', 'promote', 'rollback')"
    model_check = next(
        item
        for item in PluginArtifactLifecycleRow.__table__.constraints
        if isinstance(item, CheckConstraint)
        and item.name == "ck_plugin_artifact_lifecycle_operation"
    )
    assert str(model_check.sqltext) == expected_check
    assert [item[0] for item in op.method_calls] == [
        "drop_constraint",
        "create_check_constraint",
    ]
    op.drop_constraint.assert_called_once_with(
        "ck_plugin_artifact_lifecycle_operation",
        "plugin_artifact_lifecycle",
        type_="check",
    )
    assert op.create_check_constraint.call_args.args == (
        "ck_plugin_artifact_lifecycle_operation",
        "plugin_artifact_lifecycle",
        expected_check,
    )
    assert not op.get_bind.called
    assert not op.execute.called


def test_revision_0907_downgrade_denies_rollback_rows_before_ddl() -> None:
    module = _rollback_revision()
    op = Mock()
    connection = op.get_bind.return_value
    connection.execute.return_value.scalar_one.return_value = True
    module.op = op

    with pytest.raises(RuntimeError) as caught:
        module.downgrade()

    assert str(caught.value) == (
        "2026110907 downgrade refused: rollback lifecycle rows exist"
    )
    connection.execute.assert_called_once()
    query = str(connection.execute.call_args.args[0])
    assert query == (
        "SELECT EXISTS ("
        "SELECT 1 FROM plugin_artifact_lifecycle WHERE operation = 'rollback'"
        ")"
    )
    assert [item[0] for item in op.method_calls] == ["get_bind"]
    op.drop_constraint.assert_not_called()
    op.create_check_constraint.assert_not_called()
    assert not op.execute.called
    assert not op.bulk_insert.called


def test_revision_0907_downgrade_without_rollback_rows_only_narrows_check() -> None:
    module = _rollback_revision()
    op = Mock()
    connection = op.get_bind.return_value
    # SELECT EXISTS is false both for an empty table and with publish/promote only.
    connection.execute.return_value.scalar_one.return_value = False
    module.op = op
    module.downgrade()

    assert [item[0] for item in op.method_calls] == [
        "get_bind",
        "drop_constraint",
        "create_check_constraint",
    ]
    connection.execute.assert_called_once()
    assert str(connection.execute.call_args.args[0]) == (
        "SELECT EXISTS ("
        "SELECT 1 FROM plugin_artifact_lifecycle WHERE operation = 'rollback'"
        ")"
    )
    op.drop_constraint.assert_called_once_with(
        "ck_plugin_artifact_lifecycle_operation",
        "plugin_artifact_lifecycle",
        type_="check",
    )
    assert op.create_check_constraint.call_args.args == (
        "ck_plugin_artifact_lifecycle_operation",
        "plugin_artifact_lifecycle",
        "operation IN ('publish', 'promote')",
    )
    assert not op.execute.called
    assert not op.bulk_insert.called
