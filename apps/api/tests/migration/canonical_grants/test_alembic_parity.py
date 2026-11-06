"""Revision ``2026110304`` must be a faithful projection of the owning metadata.

The migration verification freeze forbids ``alembic upgrade``, so the revision
is replayed against a recording stand-in for ``alembic.op``: every
``create_table`` / ``create_index`` call is materialised as a real
:class:`sqlalchemy.Table` in an isolated :class:`~sqlalchemy.MetaData`. No
database, dialect connection or DDL statement is involved; the comparison is
pure schema-object parity.

This is the "metadata parity" acceptance artifact required by MIG-DDL-M06-01.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa

from prodavan.infrastructure.persistence.models import canonical_grants
from prodavan.infrastructure.persistence.models.base import Base


API_ROOT = Path(__file__).resolve().parents[3]
REVISION_PATH = API_ROOT / "alembic" / "versions" / "2026110304_canonical_grants.py"

ALLOWED_OP_CALLS = {"create_table", "create_index", "drop_table", "drop_index"}


class _RecordingOp:
    """Minimal ``alembic.op`` stand-in that only records schema objects."""

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.other_calls: list[str] = []

    def create_table(self, name: str, *args, **kwargs) -> sa.Table:
        table = sa.Table(name, self.metadata, *args, **kwargs)
        self.tables[name] = table
        return table

    def create_index(self, name: str, table_name: str, columns, **kwargs) -> None:
        # Recreate the index on the isolated table so both sides are compared as
        # real Index objects (expressions, uniqueness and partial predicate).
        sa.Index(name, *[self.tables[table_name].c[c] if isinstance(c, str) else c for c in columns], **kwargs)

    def __getattr__(self, item):  # any extra op call is a contract break
        def _unexpected(*args, **kwargs):
            self.other_calls.append(item)

        return _unexpected


def _load_revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_m06_canonical_grants_revision", REVISION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def recorded() -> _RecordingOp:
    revision = _load_revision()
    recorder = _RecordingOp()
    revision.op = recorder
    revision.upgrade()
    return recorder


def _normalise_default(column: sa.Column) -> str | None:
    default = column.server_default
    if default is None:
        return None
    if isinstance(default, sa.Identity):
        # Identity columns carry no literal default; the identity flag is
        # compared separately in ``_column_signature``.
        return "<identity>"
    text = str(default.arg).strip().removesuffix("::jsonb")
    return text.strip("'\"").lower()


def _column_signature(column: sa.Column) -> tuple:
    return (
        column.name,
        str(column.type),
        bool(column.nullable),
        _normalise_default(column),
        bool(column.primary_key),
        column.identity is not None,
    )


def _fk_signature(constraint: sa.ForeignKeyConstraint) -> tuple:
    return (
        constraint.name,
        tuple(element.parent.name for element in constraint.elements),
        tuple(element.target_fullname for element in constraint.elements),
        constraint.ondelete,
    )


def _table_signature(table: sa.Table) -> dict:
    return {
        "columns": sorted(_column_signature(column) for column in table.columns),
        "primary_key": tuple(column.name for column in table.primary_key.columns),
        # Both sides leave the PK unnamed (metadata has no naming convention
        # beyond index labels), so PostgreSQL assigns the same default name.
        "primary_key_name": tuple(
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, sa.PrimaryKeyConstraint)
        ),
        "unique": sorted(
            (constraint.name, tuple(column.name for column in constraint.columns))
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        ),
        "fk": sorted(
            _fk_signature(constraint)
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
        ),
        "check": sorted(
            (constraint.name, str(constraint.sqltext))
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)
        ),
    }


def _index_signature(index: sa.Index) -> tuple:
    where = index.dialect_options["postgresql"].get("where")
    return (
        index.name,
        tuple(str(expression) for expression in index.expressions),
        bool(index.unique),
        str(where) if where is not None else None,
    )


def test_revision_identity_matches_the_wave_plan() -> None:
    revision = _load_revision()

    assert revision.revision == "2026110304"
    assert revision.down_revision == "2026110303"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_revision_calls_only_table_and_index_operations() -> None:
    tree = ast.parse(REVISION_PATH.read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    }

    assert called <= ALLOWED_OP_CALLS
    # No data step, no privilege/RLS statement, no trigger: revision 2026110307
    # owns policies and every promotion stays closed in this revision.
    assert "execute" not in called


def test_revision_creates_exactly_the_owned_tables(recorded: _RecordingOp) -> None:
    assert set(recorded.tables) == set(canonical_grants.M06_TABLE_OWNERS)
    assert recorded.other_calls == []


@pytest.mark.parametrize("table_name", sorted(canonical_grants.M06_TABLE_OWNERS))
def test_revision_table_matches_metadata(recorded: _RecordingOp, table_name: str) -> None:
    metadata_table = Base.metadata.tables[table_name]
    revision_table = recorded.tables[table_name]

    assert _table_signature(revision_table) == _table_signature(metadata_table)


@pytest.mark.parametrize("table_name", sorted(canonical_grants.M06_TABLE_OWNERS))
def test_revision_indexes_match_metadata(recorded: _RecordingOp, table_name: str) -> None:
    metadata_sig = sorted(
        _index_signature(index) for index in Base.metadata.tables[table_name].indexes
    )
    revision_sig = sorted(
        _index_signature(index) for index in recorded.tables[table_name].indexes
    )

    assert revision_sig == metadata_sig


def test_downgrade_drops_only_this_revision_objects(recorded: _RecordingOp) -> None:
    revision = _load_revision()
    dropped: list[str] = []
    indexes: list[str] = []

    class _DropOp:
        def drop_table(self, name, *args, **kwargs):
            dropped.append(name)

        def drop_index(self, name, *args, **kwargs):
            indexes.append(name)

    revision.op = _DropOp()
    revision.downgrade()

    # Children before parents: mapping/quarantine/audit reference grants.
    assert dropped == [
        "m06_canonical_grant_promotion_evidence",
        "dynamic_app_capability_writer_epochs",
        "dynamic_app_capability_audit",
        "dynamic_app_capability_grant_quarantine",
        "dynamic_app_capability_grant_mappings",
        "dynamic_app_capability_grants",
    ]
    assert sorted(indexes) == sorted(
        index.name for table in recorded.tables.values() for index in table.indexes
    )
