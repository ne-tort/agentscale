"""Static 0909 installation-scope shape/chain tests; not run during freeze."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import MetaData, Table

from prodavan.infrastructure.persistence.models.installation_scope import (
    PluginInstallationScopeRow,
)
from prodavan.infrastructure.persistence.models.plugin_platform import (
    ModuleSourceRow,
    PluginInstanceRow,
    PluginSourceReservationRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

ROOT = Path(__file__).resolve().parents[4]
REVISION_PATH = (
    ROOT / "alembic/versions/2026110909_installation_scope.py"
)


def _load_revision():
    spec = importlib.util.spec_from_file_location(
        "revision_2026110909_installation_scope", REVISION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


def _foreign_key_pairs(constraint: Any) -> list[tuple[str, str]]:
    """Return ordered local and referenced columns via public SQLAlchemy API."""
    local_columns = [column.name for column in constraint.columns]
    referenced_columns = [
        foreign_key.target_fullname for foreign_key in constraint.elements
    ]
    return list(zip(local_columns, referenced_columns, strict=True))


def test_revision_chain_additive_shape_and_no_backfill() -> None:
    module = _load_revision()
    assert module.revision == "2026110909"
    assert module.down_revision == "2026110908"
    recorder = _Recorder()
    module.op = recorder
    module.upgrade()
    names = [name for name, _, _ in recorder.calls]
    assert "execute" not in names
    assert "bulk_insert" not in names
    assert "alter_column" not in names
    assert "drop_table" not in names
    assert names.count("add_column") == 4
    created = next(
        (args, kwargs)
        for name, args, kwargs in recorder.calls
        if name == "create_table" and args[0] == "plugin_installation_scopes"
    )
    recorded_table = Table(created[0][0], MetaData(), *created[0][1:])
    scope_fk = next(
        item
        for item in recorded_table.constraints
        if item.name == "fk_plugin_installation_scope_project"
    )
    assert _foreign_key_pairs(scope_fk) == [
        ("project_id", "projects.id"),
        ("company_id", "projects.company_id"),
        ("cabinet_id", "projects.cabinet_id"),
    ]
    instance_fk = next(
        item
        for item in recorded_table.constraints
        if item.name == "fk_plugin_installation_scope_instance"
    )
    assert _foreign_key_pairs(instance_fk) == [
        ("company_id", "plugin_instances.company_id"),
        ("plugin_instance_id", "plugin_instances.id"),
    ]


def test_orm_matches_migration_composite_constraints() -> None:
    scope_fk = next(
        constraint
        for constraint in PluginInstallationScopeRow.__table__.constraints
        if constraint.name == "fk_plugin_installation_scope_project"
    )
    assert _foreign_key_pairs(scope_fk) == [
        ("project_id", "projects.id"),
        ("company_id", "projects.company_id"),
        ("cabinet_id", "projects.cabinet_id"),
    ]
    assert "id" not in PluginInstallationScopeRow.__table__.columns
    for row_type, first, second in (
        (ModuleSourceRow, "cabinet_id", "project_id"),
        (PluginSourceReservationRow, "cabinet_id", "project_id"),
    ):
        assert row_type.__table__.columns[first].nullable is True
        assert row_type.__table__.columns[second].nullable is True
    assert any(
        constraint.name == "uq_project_tenant_cabinet_anchor"
        for constraint in ProjectRow.__table__.constraints
    )
    assert any(
        constraint.name == "uq_plugin_instance_tenant_anchor"
        for constraint in PluginInstanceRow.__table__.constraints
    )
