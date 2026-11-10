"""Static ORM and Alembic shape checks for plugin_routes (not run in freeze)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import CheckConstraint, Column, ForeignKeyConstraint, MetaData, Table, UniqueConstraint

from prodavan.infrastructure.persistence.models.plugin_routes import PluginRouteRow

EXPECTED_COLUMNS = {
    "company_id",
    "plugin_id",
    "grant_id",
    "instance_id",
    "webhook_path",
    "pluginpage_path",
    "capability_path",
    "egress_rule_count",
    "deployment",
    "created_at",
    "updated_at",
}


def _revision():
    path = (
        Path(__file__).parents[4]
        / "alembic/versions/2026110905_plugin_routes.py"
    )
    spec = importlib.util.spec_from_file_location("revision_2026110905_routes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_is_secret_free_exact_and_restricts_grant_delete() -> None:
    table = PluginRouteRow.__table__
    assert set(table.c.keys()) == EXPECTED_COLUMNS
    assert not any(
        marker in name
        for name in table.c.keys()
        for marker in ("secret", "token", "dsn", "password", "policy")
    )
    uniques = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert any(
        tuple(column.name for column in constraint.columns)
        == ("company_id", "plugin_id", "grant_id")
        for constraint in uniques
    )
    foreign_keys = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    assert len(foreign_keys) == 1
    assert foreign_keys[0].ondelete == "RESTRICT"
    assert foreign_keys[0].elements[0].target_fullname == "plugin_capability_grants.id"
    assert any(
        isinstance(constraint, CheckConstraint)
        and "egress_rule_count >= 0" in str(constraint.sqltext)
        for constraint in table.constraints
    )


def test_revision_0905_is_additive_and_matches_model(monkeypatch) -> None:
    module = _revision()
    created = {}
    dropped = []
    def capture_create_table(name, *items):
        # Attach constraints exactly as SQLAlchemy DDL does: standalone
        # UniqueConstraint columns are pending until a Table owns them.
        table = Table(name, MetaData(), *items)
        created.update(name=name, items=items, table=table)

    monkeypatch.setattr(module.op, "create_table", capture_create_table)
    monkeypatch.setattr(module.op, "drop_table", lambda name: dropped.append(name))

    module.upgrade()
    module.downgrade()

    assert module.revision == "2026110905"
    assert module.down_revision == "2026110904"
    assert created["name"] == "plugin_routes"
    columns = {
        item.name: item for item in created["items"] if isinstance(item, Column)
    }
    assert set(columns) == EXPECTED_COLUMNS
    assert columns["grant_id"].primary_key is True
    constraints = [
        item for item in created["items"] if not isinstance(item, Column)
    ]
    assert any(
        isinstance(item, UniqueConstraint)
        and tuple(column.name for column in item.columns)
        == ("company_id", "plugin_id", "grant_id")
        for item in created["table"].constraints
    )
    assert any(
        isinstance(item, ForeignKeyConstraint)
        and item.ondelete == "RESTRICT"
        and item.elements[0].target_fullname == "plugin_capability_grants.id"
        for item in constraints
    )
    assert dropped == ["plugin_routes"]
