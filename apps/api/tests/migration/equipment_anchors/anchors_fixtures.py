"""Database-free replay of revision ``2026110319`` (``MIG-EQ-ANCHORS-01``).

The revision is loaded by path (``alembic/versions`` is not a package), ``alembic.op``
is replaced by a recorder, and both ``upgrade()`` and ``downgrade()`` are replayed.
The recorder materialises ``create_table`` as real :class:`sqlalchemy.Table` objects
in an isolated :class:`~sqlalchemy.MetaData`, so the declared schema can be compared
with ``Base.metadata`` object by object - same SQLAlchemy rendering on both sides.

``add_column`` / ``create_foreign_key`` / ``drop_column`` are recorded as *operations*
rather than folded into a table, because that is exactly what the revision does: it
alters tables another revision owns.  The tests then assert the operations against the
model, and assert that ``downgrade`` is their exact mirror in reverse order.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

import sqlalchemy as sa

API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = API_ROOT / "alembic" / "versions"
REVISION_ID = "2026110319"
REVISION_PATH = VERSIONS_DIR / f"{REVISION_ID}_equipment_anchors.py"


def load_revision() -> ModuleType:
    """Import the revision module by path, once per module object."""

    name = f"_equipment_anchors_revision_{REVISION_ID}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REVISION_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {REVISION_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class AddedColumn:
    table: str
    column: sa.Column


@dataclass(frozen=True)
class CreatedForeignKey:
    name: str
    table: str
    referent: str
    local: tuple[str, ...]
    remote: tuple[str, ...]
    ondelete: str | None
    deferrable: bool | None = None
    initially: str | None = None


@dataclass
class Replay:
    """Everything one ``upgrade()`` / ``downgrade()`` call declared, in call order."""

    phase: str
    metadata: sa.MetaData = field(default_factory=sa.MetaData)
    tables: dict[str, sa.Table] = field(default_factory=dict)
    table_order: list[str] = field(default_factory=list)
    added_columns: list[AddedColumn] = field(default_factory=list)
    dropped_columns: list[tuple[str, str]] = field(default_factory=list)
    foreign_keys: list[CreatedForeignKey] = field(default_factory=list)
    dropped_constraints: list[tuple[str, str, str | None]] = field(default_factory=list)
    indexes: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    dropped_indexes: list[tuple[str, str | None]] = field(default_factory=list)
    dropped_tables: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)

    @property
    def executed(self) -> list[str]:
        return self.statements


class _Recorder:
    """Stand-in for ``alembic.op``; anything unexpected raises instead of passing."""

    def __init__(self, replay: Replay) -> None:
        self._replay = replay

    # -- tables ------------------------------------------------------------
    def create_table(self, name: str, *elements: Any, **kwargs: Any) -> sa.Table:
        assert name not in self._replay.tables, f"{name} created twice"
        table = sa.Table(name, self._replay.metadata, *elements, **kwargs)
        self._replay.tables[name] = table
        self._replay.table_order.append(name)
        return table

    def drop_table(self, name: str, **kwargs: Any) -> None:
        self._replay.dropped_tables.append(name)

    # -- columns -----------------------------------------------------------
    def add_column(self, table_name: str, column: sa.Column, **kwargs: Any) -> None:
        # ``op.add_column`` needs a bound ``Column``; a bare ``String`` is a bug.
        assert isinstance(column, sa.Column), f"{table_name}: add_column got {column!r}"
        self._replay.added_columns.append(AddedColumn(table=table_name, column=column))

    def drop_column(self, table_name: str, column_name: str, **kwargs: Any) -> None:
        self._replay.dropped_columns.append((table_name, column_name))

    # -- constraints -------------------------------------------------------
    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: Sequence[str],
        remote_cols: Sequence[str],
        **kwargs: Any,
    ) -> None:
        self._replay.foreign_keys.append(
            CreatedForeignKey(
                name=name,
                table=source_table,
                referent=referent_table,
                local=tuple(local_cols),
                remote=tuple(remote_cols),
                ondelete=kwargs.get("ondelete"),
                deferrable=kwargs.get("deferrable"),
                initially=kwargs.get("initially"),
            )
        )

    def drop_constraint(self, name: str, table_name: str, type_: str | None = None, **kwargs: Any) -> None:
        self._replay.dropped_constraints.append((name, table_name, type_))

    # -- indexes -----------------------------------------------------------
    def create_index(self, name: str, table_name: str, columns: Sequence[Any], **kwargs: Any) -> None:
        self._replay.indexes.append((name, table_name, tuple(_render(element) for element in columns)))

    def drop_index(self, name: str, table_name: str | None = None, **kwargs: Any) -> None:
        self._replay.dropped_indexes.append((name, table_name))

    # -- raw SQL -----------------------------------------------------------
    def execute(self, statement: Any, **kwargs: Any) -> None:
        self._replay.statements.append(str(statement))

    def get_bind(self):  # noqa: ANN201 - mirrors Alembic's signature
        # The verification half of the revision needs a live connection.  The replay
        # is database-free, so the caller substitutes a fake bind (see
        # ``_FakeBind``) instead of letting the revision touch a server.
        return _FakeBind()

    def __getattr__(self, item: str):  # pragma: no cover - defensive
        raise AssertionError(f"unexpected alembic.op call in {self._replay.phase}: {item}")


class _FakeBind:
    """Minimal bind that answers the three catalog probes the registry helpers make."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None):
        sql = _squash(str(statement))
        self.calls.append(sql)
        params = params or {}
        # ``Policies.missing_scope_columns``/``relation_exists`` bind ``relation``;
        # ``_verify_policies`` binds ``table``.
        relation = params.get("relation") or params.get("table")

        class _Result:
            def __init__(self, rows: list[tuple[Any, ...]]) -> None:
                self._rows = rows

            def all(self) -> list[tuple[Any, ...]]:
                return list(self._rows)

            def scalar_one(self) -> Any:
                return self._rows[0][0] if self._rows else None

            def scalar(self) -> Any:
                return self.scalar_one()

        from prodavan.infrastructure.persistence.models.base import Base
        from prodavan.infrastructure.persistence.rls import policies

        if relation not in Base.metadata.tables:
            # An unknown relation: ``to_regclass`` answers NULL and every probe is empty.
            return _Result([(None,)] if "to_regclass" in sql else [])

        model = Base.metadata.tables[relation]
        # ``pg_attribute`` first: the column probe resolves its relation through
        # ``to_regclass(:relation)`` as well, so a ``to_regclass`` test would shadow it.
        if "pg_attribute" in sql:
            return _Result([(column,) for column in model.columns.keys()])
        if "to_regclass" in sql:
            return _Result([(relation,)])
        if "pg_policy" in sql:
            spec = next((item for item in policies.CANONICAL_TABLE_POLICIES if item.table == relation), None)
            return _Result([] if spec is None else [(name,) for name in spec.policy_names])
        return _Result([])


def replay(phase: str) -> Replay:
    module = load_revision()
    record = Replay(phase=phase)
    original = module.op
    module.op = _Recorder(record)
    try:
        getattr(module, phase)()
    finally:
        module.op = original
    return record


# --------------------------------------------------------------------------- #
# signature helpers
# --------------------------------------------------------------------------- #


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _render(element: Any) -> str:
    if isinstance(element, sa.Column):
        return element.name
    return _squash(str(element))


def compile_type(type_: sa.types.TypeEngine) -> str:
    return _squash(str(type_.compile(dialect=sa.dialects.postgresql.dialect())))


def column_signature(column: sa.Column) -> tuple[Any, ...]:
    default = column.server_default
    return (
        column.name,
        compile_type(column.type),
        bool(column.nullable),
        None if default is None else _squash(str(default.arg)),
    )


def table_signature(table: sa.Table) -> dict[str, Any]:
    uniques = []
    for constraint in table.constraints:
        if isinstance(constraint, sa.UniqueConstraint):
            uniques.append(
                (
                    constraint.name,
                    tuple(column.name for column in constraint.columns),
                    bool(constraint.deferrable),
                    constraint.initially,
                )
            )
    foreign_keys = []
    for constraint in table.constraints:
        if isinstance(constraint, sa.ForeignKeyConstraint):
            foreign_keys.append(
                (
                    constraint.name,
                    tuple(constraint.column_keys),
                    tuple(element.target_fullname for element in constraint.elements),
                    (constraint.ondelete or "").upper() or None,
                )
            )
    checks = [
        (constraint.name, _squash(str(constraint.sqltext)))
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    ]
    return {
        "columns": tuple(column_signature(column) for column in table.columns),
        "primary_key": tuple(column.name for column in table.primary_key.columns),
        "unique": tuple(sorted(uniques, key=lambda item: (item[0] or "", item[1]))),
        "foreign_keys": tuple(sorted(foreign_keys, key=lambda item: (item[0] or "", item[1]))),
        "checks": tuple(sorted(checks, key=lambda item: (item[0] or "", item[1]))),
    }


def index_signature(index: sa.Index) -> tuple[Any, ...]:
    where = index.dialect_options.get("postgresql", {}).get("where")
    return (
        index.name,
        tuple(_render(expression) for expression in index.expressions),
        bool(index.unique),
        None if where is None else _squash(str(where)),
    )


def revision_header() -> dict[str, Any]:
    """Chain metadata parsed with :mod:`ast` - never imported, never executed."""

    tree = ast.parse(REVISION_PATH.read_text(encoding="utf-8"), filename=str(REVISION_PATH))
    values: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if name in {"revision", "down_revision", "branch_labels", "depends_on"} and value is not None:
            values[name] = ast.literal_eval(value)
    return values
