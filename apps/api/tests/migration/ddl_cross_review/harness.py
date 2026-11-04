"""Database-free replay harness for the wave-1 DDL chain (``2026110301``-``2026110308``).

Why a replay and not an AST diff
--------------------------------
Every revision in the wave is a plain module whose ``upgrade()`` / ``downgrade()``
functions only call Alembic operations on literal, hand-written objects.  Replacing
``alembic.op`` with a recorder therefore replays the *declared* schema without a
database, a connection, or a migration run: tables are materialised as real
:class:`sqlalchemy.Table` objects in an isolated :class:`~sqlalchemy.MetaData`,
so column types, nullability, server defaults, keys, checks and foreign keys are
comparable with the declarative metadata byte-for-byte (same SQLAlchemy rendering
both sides).

The harness answers exactly the questions of the cross-review:

* chain shape - ``revision`` / ``down_revision`` / heads / forks;
* import order - which revision creates which table, in which statement order;
* parity - replay output versus ``Base.metadata``;
* inter-revision references - every foreign key target is created earlier (or is
  a retained table) and every composite target has a matching PK/UNIQUE key;
* duplication - an object name created twice in the wave;
* downgrade - the reverse order and the exact set of objects this revision owns;
* RLS coverage - the registry in ``persistence/rls/policies.py`` versus the
  tables the wave actually creates.

Nothing here touches the network, the database, Alembic's migration context, or
mutable global state beyond an ``alembic.op`` attribute patch that is restored in
a ``finally`` block.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Iterable, Iterator, Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

#: ``apps/api/tests/migration/ddl_cross_review/harness.py`` -> ``apps/api``.
API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = API_ROOT / "alembic" / "versions"

#: The wave under review, in chain order.  ``2026110308`` may still be authored
#: by a sibling agent; the harness tolerates its absence (see :func:`revision_files`).
WAVE_REVISIONS: tuple[str, ...] = (
    "2026110301",
    "2026110302",
    "2026110303",
    "2026110304",
    "2026110305",
    "2026110306",
    "2026110307",
    "2026110308",
)

#: Front of the wave: the pre-existing head this chain must attach to.
CHAIN_ROOT_REVISION = "2026110202"

_POSTGRESQL = postgresql.dialect()

_REVISION_FILE = re.compile(r"^(?P<revision>\d{10})_(?P<slug>[a-z0-9_]+)\.py$")
_ALTER_ADD_UNIQUE = re.compile(
    r"ALTER\s+TABLE\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+CONSTRAINT\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+UNIQUE",
    re.IGNORECASE,
)
_CREATE_TRIGGER = re.compile(
    r"CREATE\s+TRIGGER\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\s\S]*?\bON\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_CREATE_FUNCTION = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.IGNORECASE,
)
_CREATE_POLICY = re.compile(
    r"CREATE\s+POLICY\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+ON\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_DROP_TRIGGER = re.compile(
    r"DROP\s+TRIGGER\s+(?:IF\s+EXISTS\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)
_DROP_FUNCTION = re.compile(
    r"DROP\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(", re.IGNORECASE
)
_DROP_POLICY = re.compile(
    r"DROP\s+POLICY\s+(?:IF\s+EXISTS\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)
_DROP_CONSTRAINT_SQL = re.compile(
    r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Chain metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevisionHeader:
    """Module-level chain metadata, parsed with :mod:`ast` (no import needed)."""

    revision: str
    down_revision: str | tuple[str, ...] | None
    branch_labels: object
    depends_on: object
    path: Path

    @property
    def parents(self) -> tuple[str, ...]:
        if self.down_revision is None:
            return ()
        if isinstance(self.down_revision, str):
            return (self.down_revision,)
        return tuple(self.down_revision)


def revision_files() -> dict[str, Path]:
    """Every revision file in ``alembic/versions`` keyed by revision id."""

    files: dict[str, Path] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        match = _REVISION_FILE.match(path.name)
        if match is None:
            continue
        files[match.group("revision")] = path
    return files


@lru_cache(maxsize=1)
def revision_chain() -> dict[str, RevisionHeader]:
    """All revision headers, parsed from source without importing the modules."""

    chain: dict[str, RevisionHeader] = {}
    for revision, path in revision_files().items():
        values: dict[str, object] = {}
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id not in {"revision", "down_revision", "branch_labels", "depends_on"}:
                continue
            try:
                values[target.id] = ast.literal_eval(node.value)
            except ValueError:  # pragma: no cover - defensive, all four are literals
                values[target.id] = None
        chain[revision] = RevisionHeader(
            revision=revision,
            down_revision=values.get("down_revision"),  # type: ignore[arg-type]
            branch_labels=values.get("branch_labels"),
            depends_on=values.get("depends_on"),
            path=path,
        )
    return chain


def wave_present_revisions() -> tuple[str, ...]:
    """Wave revisions that exist on disk today, in declared order."""

    available = revision_chain()
    return tuple(revision for revision in WAVE_REVISIONS if revision in available)


def head_revisions() -> tuple[str, ...]:
    """Revisions that no other revision points at (expected: exactly one)."""

    chain = revision_chain()
    referenced = {parent for header in chain.values() for parent in header.parents}
    return tuple(sorted(set(chain) - referenced))


def chain_problems() -> tuple[str, ...]:
    """Every deviation from a single linear chain, as human-readable findings."""

    problems: list[str] = []
    chain = revision_chain()

    for revision, header in sorted(chain.items()):
        if len(header.parents) > 1:
            problems.append(f"{revision} has {len(header.parents)} parents {header.parents}: fork")
        if header.branch_labels not in (None, ()):  # branch_labels=() is equivalent to None
            problems.append(f"{revision} declares branch_labels={header.branch_labels!r}")
        if header.depends_on not in (None, ()):
            problems.append(f"{revision} declares depends_on={header.depends_on!r}")

    heads = head_revisions()
    if len(heads) != 1:
        problems.append(f"expected exactly one head, found {heads}")

    present = wave_present_revisions()
    if not present:
        return tuple(problems)

    if present[0] != WAVE_REVISIONS[0]:
        problems.append(f"first wave revision present is {present[0]}, expected {WAVE_REVISIONS[0]}")

    first = chain[present[0]]
    if first.parents != (CHAIN_ROOT_REVISION,):
        problems.append(
            f"{present[0]} down_revision is {first.down_revision!r}, expected {CHAIN_ROOT_REVISION!r}"
        )
    if CHAIN_ROOT_REVISION not in chain:
        problems.append(f"chain root {CHAIN_ROOT_REVISION} is missing from alembic/versions")

    for previous, current in zip(present, present[1:]):
        expected = (previous,)
        actual = chain[current].parents
        if actual != expected:
            problems.append(f"{current} down_revision is {actual}, expected {expected}")

    return tuple(problems)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def squash(text: str) -> str:
    """Collapse whitespace so a wrapped SQL/check literal compares equal."""

    return re.sub(r"\s+", " ", text).strip()


def compile_type(type_: sa.types.TypeEngine) -> str:
    """Render a column type exactly as PostgreSQL DDL would."""

    return squash(str(type_.compile(dialect=_POSTGRESQL)))


def render_expression(value: object) -> str:
    """Render one index expression to a comparable string."""

    if isinstance(value, str):
        return squash(value)
    if isinstance(value, sa.Column):
        return value.name
    try:
        compiled = value.compile(dialect=_POSTGRESQL, compile_kwargs={"literal_binds": True})  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive
        return squash(str(value))
    return squash(str(compiled))


def render_default(column: sa.Column) -> str | None:
    """Render ``server_default`` to a value-only token.

    ``server_default="false"`` and ``server_default=sa.text("false")`` are the same
    PostgreSQL default but render differently, and ``'{}'::jsonb`` is the same
    default as ``'{}'`` on a JSONB column.  The comparison cares about the value,
    not the spelling, so quoting and trailing casts are normalised away.
    """

    default = column.server_default
    if default is None:
        return None
    if isinstance(default, sa.Identity):
        return (
            "identity("
            f"always={bool(default.always)}, start={default.start}, increment={default.increment}, "
            f"minvalue={default.minvalue}, maxvalue={default.maxvalue}, cycle={default.cycle})"
        )
    argument = default.arg
    if isinstance(argument, str):
        text = argument
    else:
        try:
            text = str(
                argument.compile(dialect=_POSTGRESQL, compile_kwargs={"literal_binds": True})  # type: ignore[attr-defined]
            )
        except Exception:  # pragma: no cover - defensive
            text = str(argument)
    text = text.strip()
    text = re.sub(r"::[A-Za-z_ ]+$", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return squash(text)


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    nullable: bool
    default: str | None


@dataclass(frozen=True)
class UniqueSpec:
    name: str | None
    columns: tuple[str, ...]
    table: str | None = None


@dataclass(frozen=True)
class ForeignKeySpec:
    name: str | None
    local: tuple[str, ...]
    remote: tuple[str, ...]
    ondelete: str | None
    deferrable: bool | None
    initially: str | None
    source_table: str | None = None

    @property
    def target_table(self) -> str:
        return self.remote[0].split(".", 1)[0]

    @property
    def target_columns(self) -> tuple[str, ...]:
        return tuple(remote.split(".", 1)[1] for remote in self.remote)


@dataclass(frozen=True)
class IndexSpec:
    name: str
    table: str
    expressions: tuple[str, ...]
    unique: bool
    where: str | None


def column_specs(table: sa.Table) -> tuple[ColumnSpec, ...]:
    return tuple(
        ColumnSpec(
            name=column.name,
            type=compile_type(column.type),
            nullable=False if column.primary_key else bool(column.nullable),
            default=render_default(column),
        )
        for column in table.columns
    )


def primary_key(table: sa.Table) -> tuple[str, ...]:
    return tuple(column.name for column in table.primary_key.columns)


def unique_specs(table: sa.Table) -> tuple[UniqueSpec, ...]:
    specs = [
        UniqueSpec(
            name=constraint.name,
            columns=tuple(column.name for column in constraint.columns),
            table=table.name,
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    ]
    return tuple(sorted(specs, key=lambda spec: (spec.columns, spec.name or "")))


def check_specs(table: sa.Table) -> tuple[tuple[str | None, str], ...]:
    specs = [
        (constraint.name, squash(str(constraint.sqltext)))
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    ]
    return tuple(sorted(specs, key=lambda spec: (spec[0] or "", spec[1])))


def foreign_key_specs(table: sa.Table) -> tuple[ForeignKeySpec, ...]:
    specs: list[ForeignKeySpec] = []
    for constraint in table.constraints:
        if not isinstance(constraint, sa.ForeignKeyConstraint):
            continue
        specs.append(
            ForeignKeySpec(
                name=constraint.name,
                local=tuple(element.parent.name for element in constraint.elements),
                remote=tuple(element.target_fullname for element in constraint.elements),
                ondelete=constraint.ondelete,
                deferrable=constraint.deferrable,
                initially=constraint.initially,
                source_table=table.name,
            )
        )
    return tuple(sorted(specs, key=lambda spec: (spec.name or "", spec.local)))


def index_specs(table: sa.Table) -> tuple[IndexSpec, ...]:
    specs: list[IndexSpec] = []
    for index in table.indexes:
        where = index.dialect_options.get("postgresql", {}).get("where")
        specs.append(
            IndexSpec(
                name=index.name or "",
                table=table.name,
                expressions=tuple(render_expression(expression) for expression in index.expressions),
                unique=bool(index.unique),
                where=None if where is None else render_expression(where),
            )
        )
    return tuple(sorted(specs, key=lambda spec: spec.name))


def unique_key_sets(table: sa.Table) -> set[frozenset[str]]:
    """Every column set PostgreSQL accepts as a foreign-key target on ``table``."""

    keys: set[frozenset[str]] = set()
    primary = primary_key(table)
    if primary:
        keys.add(frozenset(primary))
    for spec in unique_specs(table):
        keys.add(frozenset(spec.columns))
    return keys


# ---------------------------------------------------------------------------
# Recording Alembic operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SqlObject:
    """An object created or dropped by a raw ``op.execute`` statement."""

    name: str
    kind: str
    table: str | None


@dataclass
class Replay:
    """Everything one ``upgrade()`` or ``downgrade()`` call declared."""

    revision: str
    phase: str
    metadata: sa.MetaData
    tables: dict[str, sa.Table] = field(default_factory=dict)
    table_order: list[str] = field(default_factory=list)
    indexes: dict[str, IndexSpec] = field(default_factory=dict)
    detached_foreign_keys: dict[str, ForeignKeySpec] = field(default_factory=dict)
    detached_uniques: dict[str, UniqueSpec] = field(default_factory=dict)
    sql_objects: dict[str, SqlObject] = field(default_factory=dict)
    dropped_tables: list[str] = field(default_factory=list)
    dropped_indexes: list[str] = field(default_factory=list)
    dropped_constraints: dict[str, str] = field(default_factory=dict)
    dropped_sql_objects: dict[str, str] = field(default_factory=dict)
    statements: list[str] = field(default_factory=list)
    #: ``ALTER TABLE ... ADD COLUMN`` pairs, in call order.  A revision may extend a
    #: table another revision created (``2026110319`` adds the equipment anchors to
    #: tables ``2026110305`` owns), and parity is about the *cumulative* schema, so the
    #: columns are kept here and folded in by :func:`cumulative_table`.
    added_columns: list[tuple[str, sa.Column]] = field(default_factory=list)
    dropped_columns: list[tuple[str, str]] = field(default_factory=list)

    # -- aliases that read better at the call site -------------------------
    @property
    def created_names(self) -> set[str]:
        return (
            set(self.tables)
            | set(self.indexes)
            | set(self.detached_foreign_keys)
            | set(self.detached_uniques)
            | set(self.sql_objects)
        )

    @property
    def dropped_names(self) -> set[str]:
        return (
            set(self.dropped_tables)
            | set(self.dropped_indexes)
            | set(self.dropped_constraints)
            | set(self.dropped_sql_objects)
        )


class _OfflineContext:
    """Minimal stand-in for ``op.get_context()``: offline mode, no connection."""

    as_sql = True


class _CatalogStub:
    """Answers the catalog *reads* a revision performs, from the declarative metadata.

    Three shapes are supported, because that is what the registry helpers ask:

    * ``to_regclass(:relation)`` - the relation is present when the model declares it;
    * ``pg_attribute`` - the column list of that model table;
    * ``pg_policy`` - the two policy names the registry declares for that table.

    Anything else returns no rows, so an unexpected query cannot silently agree with
    an assertion it was never written to answer.
    """

    def execute(self, statement: object, params: Mapping[str, object] | None = None):
        from prodavan.infrastructure.persistence.models import Base
        from prodavan.infrastructure.persistence.rls import policies

        sql = " ".join(str(statement).split()).lower()
        params = params or {}
        relation = params.get("relation") or params.get("table") or params.get("name")
        table = Base.metadata.tables.get(relation) if isinstance(relation, str) else None

        rows: list[tuple[object, ...]] = []
        if "pg_attribute" in sql:
            rows = [] if table is None else [(column,) for column in table.columns.keys()]
        elif "pg_policy" in sql:
            spec = next((item for item in policies.CANONICAL_TABLE_POLICIES if item.table == relation), None)
            rows = [] if spec is None else [(name,) for name in spec.policy_names]
        elif "to_regclass" in sql:
            rows = [(relation if table is not None else None,)]
        return _StubResult(rows)


class _StubResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)

    def scalar(self) -> object:
        return self._rows[0][0] if self._rows else None

    def scalar_one(self) -> object:
        return self.scalar()


class _FakeOp:
    """Recorder standing in for ``alembic.op`` during a replay."""

    def __init__(self, replay: Replay) -> None:
        self._replay = replay

    # -- DDL ---------------------------------------------------------------
    def create_table(self, name: str, *args: object, **kwargs: object) -> sa.Table:
        replay = self._replay
        table = sa.Table(name, replay.metadata, *args, **kwargs)  # type: ignore[arg-type]
        if name in replay.tables:
            raise AssertionError(f"{replay.revision}.{replay.phase}: table {name} created twice")
        replay.tables[name] = table
        replay.table_order.append(name)
        return table

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: Sequence[object],
        unique: bool = False,
        **kwargs: object,
    ) -> None:
        where = kwargs.get("postgresql_where")
        spec = IndexSpec(
            name=name,
            table=table_name,
            expressions=tuple(render_expression(column) for column in columns),
            unique=bool(unique),
            where=None if where is None else render_expression(where),
        )
        if name in self._replay.indexes:
            raise AssertionError(f"{self._replay.revision}: index {name} created twice")
        self._replay.indexes[name] = spec

    def create_unique_constraint(
        self, name: str, table_name: str, columns: Sequence[str], **kwargs: object
    ) -> None:
        if name in self._replay.detached_uniques:
            raise AssertionError(f"{self._replay.revision}: unique {name} created twice")
        self._replay.detached_uniques[name] = UniqueSpec(
            name=name, columns=tuple(columns), table=table_name
        )

    def add_column(self, table_name: str, column: object, **kwargs: object) -> None:
        if not isinstance(column, sa.Column):
            raise AssertionError(f"{self._replay.revision}: add_column got a non-Column {column!r}")
        self._replay.added_columns.append((table_name, column))

    def drop_column(self, table_name: str, column_name: str, **kwargs: object) -> None:
        self._replay.dropped_columns.append((table_name, column_name))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: Sequence[str],
        remote_cols: Sequence[str],
        **kwargs: object,
    ) -> None:
        if name in self._replay.detached_foreign_keys:
            raise AssertionError(f"{self._replay.revision}: foreign key {name} created twice")
        self._replay.detached_foreign_keys[name] = ForeignKeySpec(
            name=name,
            local=tuple(local_cols),
            remote=tuple(f"{referent_table}.{column}" for column in remote_cols),
            ondelete=kwargs.get("ondelete"),  # type: ignore[arg-type]
            deferrable=kwargs.get("deferrable"),  # type: ignore[arg-type]
            initially=kwargs.get("initially"),  # type: ignore[arg-type]
            source_table=source_table,
        )

    # -- drops -------------------------------------------------------------
    def drop_table(self, name: str, **kwargs: object) -> None:
        self._replay.dropped_tables.append(name)

    def drop_index(self, name: str, table_name: str | None = None, **kwargs: object) -> None:
        self._replay.dropped_indexes.append(name)

    def drop_constraint(self, name: str, table_name: str, type_: str | None = None, **kwargs: object) -> None:
        self._replay.dropped_constraints[name] = table_name

    # -- raw SQL -----------------------------------------------------------
    def execute(self, sql: object, **kwargs: object) -> None:
        text = str(sql)
        self._replay.statements.append(text)
        self._scan(text)

    def _scan(self, sql: str) -> None:
        replay = self._replay
        if replay.phase == "upgrade":
            for match in _ALTER_ADD_UNIQUE.finditer(sql):
                replay.sql_objects[match.group("name")] = SqlObject(
                    name=match.group("name"), kind="unique_constraint", table=match.group("table")
                )
            for match in _CREATE_TRIGGER.finditer(sql):
                replay.sql_objects[match.group("name")] = SqlObject(
                    name=match.group("name"), kind="trigger", table=match.group("table")
                )
            for match in _CREATE_FUNCTION.finditer(sql):
                replay.sql_objects[match.group("name")] = SqlObject(
                    name=match.group("name"), kind="function", table=None
                )
            for match in _CREATE_POLICY.finditer(sql):
                replay.sql_objects[match.group("name")] = SqlObject(
                    name=match.group("name"), kind="policy", table=match.group("table")
                )
        else:
            for pattern in (_DROP_TRIGGER, _DROP_FUNCTION, _DROP_POLICY, _DROP_CONSTRAINT_SQL):
                for match in pattern.finditer(sql):
                    replay.dropped_sql_objects[match.group("name")] = "sql"

    # -- context -----------------------------------------------------------
    def get_bind(self):  # noqa: ANN201 - mirrors Alembic's signature
        # Some revisions verify what they just installed by reading the catalog
        # (``2026110318`` checks the policy names, ``2026110319`` checks that every
        # existing canonical relation carries its declared pair).  That is a *read*,
        # not a DDL statement, so answering it from metadata keeps the replay
        # database-free: no connection, no server, no migration run.  The stub reports
        # "exists and is policied" for every model table, which is exactly what those
        # checks assert about a correctly migrated schema.
        return _CatalogStub()

    def get_context(self) -> _OfflineContext:
        return _OfflineContext()


_OP_ATTRIBUTES = (
    "create_table",
    "create_index",
    "create_unique_constraint",
    "create_foreign_key",
    "add_column",
    "drop_column",
    "drop_table",
    "drop_index",
    "drop_constraint",
    "execute",
    "get_bind",
    "get_context",
)

_MISSING = object()


@contextmanager
def _patched_alembic_op(fake: _FakeOp) -> Iterator[None]:
    """Patch ``alembic.op`` for the duration of one replay and always restore it."""

    import alembic.op as op_module

    saved = {name: getattr(op_module, name, _MISSING) for name in _OP_ATTRIBUTES}
    try:
        for name in _OP_ATTRIBUTES:
            setattr(op_module, name, getattr(fake, name))
        yield
    finally:
        for name, value in saved.items():
            if value is _MISSING:  # pragma: no cover - defensive
                delattr(op_module, name)
            else:
                setattr(op_module, name, value)


@lru_cache(maxsize=None)
def load_revision(revision: str) -> ModuleType:
    """Import one revision module by path (``alembic/versions`` is not a package)."""

    files = revision_files()
    if revision not in files:
        raise LookupError(f"revision {revision} has no file in {VERSIONS_DIR}")
    path = files[revision]
    module_name = f"_ddl_cross_review_{revision}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load revision module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(module_name, module)
    spec.loader.exec_module(module)
    return module


def replay(revision: str, phase: str = "upgrade") -> Replay:
    """Replay ``upgrade()`` or ``downgrade()`` of one revision with a fake ``op``."""

    module = load_revision(revision)
    function = getattr(module, phase)
    record = Replay(revision=revision, phase=phase, metadata=sa.MetaData())
    with _patched_alembic_op(_FakeOp(record)):
        function()
    return record


@lru_cache(maxsize=None)
def wave_upgrades() -> dict[str, Replay]:
    return {revision: replay(revision, "upgrade") for revision in wave_present_revisions()}


@lru_cache(maxsize=None)
def wave_downgrades() -> dict[str, Replay]:
    return {revision: replay(revision, "downgrade") for revision in wave_present_revisions()}


def wave_table_owners() -> dict[str, str]:
    """Table name -> revision that creates it (first creator wins)."""

    owners: dict[str, str] = {}
    for revision, record in wave_upgrades().items():
        for name in record.table_order:
            owners.setdefault(name, revision)
    return owners


def wave_created_names() -> dict[str, list[tuple[str, str]]]:
    """Object name -> [(kind, revision)] for every object created by the wave."""

    owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for revision, record in wave_upgrades().items():
        for name in record.table_order:
            owners[name].append(("table", revision))
        for name in record.indexes:
            owners[name].append(("index", revision))
        for name in record.detached_uniques:
            owners[name].append(("unique_constraint", revision))
        for name in record.detached_foreign_keys:
            owners[name].append(("foreign_key", revision))
        for name, sql_object in record.sql_objects.items():
            owners[name].append((sql_object.kind, revision))
    return dict(owners)


def wave_index_specs() -> dict[str, IndexSpec]:
    specs: dict[str, IndexSpec] = {}
    for record in wave_upgrades().values():
        specs.update(record.indexes)
    return specs


def wave_foreign_keys() -> Iterator[tuple[str, str, ForeignKeySpec]]:
    """Yield ``(revision, source_table, spec)`` for every FK declared by the wave."""

    for revision, record in wave_upgrades().items():
        for table_name in record.table_order:
            for spec in foreign_key_specs(record.tables[table_name]):
                yield revision, table_name, spec
        for spec in record.detached_foreign_keys.values():
            yield revision, spec.source_table or "?", spec


def declared_unique_keys(upto_revision: str | None = None) -> dict[str, set[frozenset[str]]]:
    """FK-target column sets declared by the wave up to and including ``upto_revision``."""

    keys: dict[str, set[frozenset[str]]] = defaultdict(set)
    for revision, record in wave_upgrades().items():
        if upto_revision is not None and revision > upto_revision:
            continue
        for table_name in record.table_order:
            for key in unique_key_sets(record.tables[table_name]):
                keys[table_name].add(key)
        for spec in record.detached_uniques.values():
            if spec.table is not None:
                keys[spec.table].add(frozenset(spec.columns))
    return keys


# ---------------------------------------------------------------------------
# Model metadata (retained baseline)
# ---------------------------------------------------------------------------


def model_metadata() -> sa.MetaData:
    """Declarative metadata, imported read-only, without touching a database."""

    from prodavan.infrastructure.persistence.models import Base

    return Base.metadata


# ---------------------------------------------------------------------------
# Review findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    check: str
    detail: str
    severity: str = "major"

    def __str__(self) -> str:  # pragma: no cover - reporting helper
        return f"[{self.severity}] {self.check}: {self.detail}"


def chain_findings() -> tuple[Finding, ...]:
    return tuple(Finding("chain", problem, "blocker") for problem in chain_problems())


def duplicate_findings() -> tuple[Finding, ...]:
    """Any table/index/constraint name created twice inside the wave."""

    findings: list[Finding] = []
    for name, creators in sorted(wave_created_names().items()):
        if len(creators) < 2:
            continue
        kinds = {kind for kind, _ in creators}
        revisions = sorted({revision for _, revision in creators})
        severity = "blocker" if kinds & {"table", "index"} else "major"
        findings.append(
            Finding("duplicate_object", f"{name} created {len(creators)}x as {sorted(kinds)} by {revisions}", severity)
        )
    return tuple(findings)


def foreign_target_findings() -> tuple[Finding, ...]:
    """Every FK target must exist earlier (or be retained) and expose a matching key."""

    findings: list[Finding] = []
    metadata = model_metadata()
    owners = wave_table_owners()
    wave_revisions = wave_present_revisions()

    for revision, source_table, spec in wave_foreign_keys():
        target = spec.target_table
        if target in owners:
            owner_revision = owners[target]
            if owner_revision > revision:
                findings.append(
                    Finding(
                        "fk_order",
                        f"{revision}: {source_table}.{spec.name} -> {target} created later by {owner_revision}",
                        "blocker",
                    )
                )
                continue
            keys = declared_unique_keys(revision).get(target, set())
        elif target in metadata.tables:
            keys = unique_key_sets(metadata.tables[target])
        else:
            findings.append(
                Finding(
                    "fk_target",
                    f"{revision}: {source_table}.{spec.name} -> {target} neither in the wave nor in Base.metadata",
                    "blocker",
                )
            )
            continue

        if len(spec.remote) > 1 and frozenset(spec.target_columns) not in keys:
            findings.append(
                Finding(
                    "fk_target_key",
                    f"{revision}: {source_table}.{spec.name} -> {target}{spec.target_columns} "
                    f"has no PK/UNIQUE on exactly those columns",
                    "blocker",
                )
            )
        if len(spec.remote) == 1 and frozenset(spec.target_columns) not in keys:
            # Single-column targets normally hit the primary key; a miss is a real defect.
            findings.append(
                Finding(
                    "fk_target_key",
                    f"{revision}: {source_table}.{spec.name} -> {target}.{spec.target_columns[0]} "
                    "is not a PK/UNIQUE column",
                    "blocker",
                )
            )
    return tuple(findings)


def downgrade_findings() -> tuple[Finding, ...]:
    """Downgrade must mirror the upgrade: same objects, reverse order, nothing foreign."""

    findings: list[Finding] = []
    for revision in wave_present_revisions():
        up = wave_upgrades()[revision]
        down = wave_downgrades()[revision]

        if set(up.tables) != set(down.dropped_tables):
            findings.append(
                Finding(
                    "downgrade_tables",
                    f"{revision}: created {sorted(set(up.tables) - set(down.dropped_tables))} but did not drop, "
                    f"dropped {sorted(set(down.dropped_tables) - set(up.tables))} but never created",
                    "blocker",
                )
            )
        if list(down.dropped_tables) != list(reversed(up.table_order)):
            findings.append(
                Finding(
                    "downgrade_order",
                    f"{revision}: drop order {down.dropped_tables} is not the reverse of create order {up.table_order}",
                )
            )
        if set(up.indexes) != set(down.dropped_indexes):
            findings.append(
                Finding(
                    "downgrade_indexes",
                    f"{revision}: created {sorted(set(up.indexes) - set(down.dropped_indexes))} / "
                    f"dropped {sorted(set(down.dropped_indexes) - set(up.indexes))}",
                )
            )
        explicit_created = set(up.detached_foreign_keys) | set(up.detached_uniques) | set(up.sql_objects)
        explicit_dropped = set(down.dropped_constraints) | set(down.dropped_sql_objects)
        if explicit_created != explicit_dropped:
            findings.append(
                Finding(
                    "downgrade_constraints",
                    f"{revision}: not dropped {sorted(explicit_created - explicit_dropped)}, "
                    f"dropped without creating {sorted(explicit_dropped - explicit_created)}",
                )
            )
    return tuple(findings)


def rls_findings() -> tuple[Finding, ...]:
    """Registry versus the tables the wave actually creates, and versus metadata."""

    from prodavan.infrastructure.persistence.rls import policies

    findings: list[Finding] = []
    metadata = model_metadata()
    owners = wave_table_owners()
    registry = {spec.table: spec.owner_revision for spec in policies.CANONICAL_TABLE_POLICIES}
    excluded = policies.excluded_table_names()

    for table, owner in sorted(registry.items()):
        if table not in metadata.tables:
            findings.append(Finding("rls_metadata", f"{table} is policied but absent from Base.metadata", "blocker"))
        actual = owners.get(table)
        if actual is None:
            findings.append(Finding("rls_owner", f"{table} is policied but no wave revision creates it", "blocker"))
        elif actual != owner:
            findings.append(
                Finding("rls_owner", f"{table}: registry says {owner}, wave creates it in {actual}", "blocker")
            )

    for table in sorted(set(owners) - set(registry) - excluded):
        findings.append(
            Finding(
                "rls_uncovered",
                f"{table} is created by {owners[table]} but has neither a policy nor a reviewed exclusion",
                "major",
            )
        )
    return tuple(findings)


def parity_findings() -> tuple[Finding, ...]:
    """Replayed DDL versus declarative metadata, table by table."""

    findings: list[Finding] = []
    metadata = model_metadata()
    wave_tables = wave_table_owners()

    for table_name, revision in sorted(wave_tables.items()):
        model_table = metadata.tables.get(table_name)
        if model_table is None:
            findings.append(
                Finding(
                    "parity_missing_model",
                    f"{revision}: {table_name} is created but not registered in Base.metadata",
                    "blocker",
                )
            )
            continue
        declared = wave_upgrades()[revision].tables[table_name]

        if column_specs(declared) != column_specs(model_table):
            findings.append(Finding("parity_columns", _column_diff(table_name, declared, model_table)))
        if primary_key(declared) != primary_key(model_table):
            findings.append(
                Finding(
                    "parity_pk",
                    f"{table_name}: migration PK {primary_key(declared)} vs model {primary_key(model_table)}",
                )
            )
        if unique_specs(declared) != unique_specs(model_table):
            findings.append(
                Finding(
                    "parity_unique",
                    f"{table_name}: migration {unique_specs(declared)} vs model {unique_specs(model_table)}",
                )
            )
        if foreign_key_specs(declared) != foreign_key_specs(model_table):
            findings.append(
                Finding(
                    "parity_fk",
                    f"{table_name}: migration {foreign_key_specs(declared)} vs model {foreign_key_specs(model_table)}",
                )
            )
        if check_specs(declared) != check_specs(model_table):
            findings.append(Finding("parity_check", _check_diff(table_name, declared, model_table)))
        if index_specs(declared) != index_specs(model_table):
            findings.append(
                Finding(
                    "parity_index",
                    f"{table_name}: migration {index_specs(declared)} vs model {index_specs(model_table)}",
                )
            )
        declared_comment = declared.comment
        model_comment = model_table.comment
        if squash(declared_comment or "") != squash(model_comment or ""):
            findings.append(
                Finding("parity_comment", f"{table_name}: table comment differs from the model")
            )

    # A retained table modified by the wave must declare exactly the key the model has.
    for revision, record in wave_upgrades().items():
        for spec in record.detached_uniques.values():
            if spec.table is None or spec.table in wave_tables:
                continue
            model_table = metadata.tables.get(spec.table)
            if model_table is None:
                findings.append(
                    Finding("parity_retained_key", f"{revision}: {spec.name} on unknown table {spec.table}", "blocker")
                )
                continue
            if spec not in unique_specs(model_table):
                findings.append(
                    Finding(
                        "parity_retained_key",
                        f"{revision}: {spec.name}{spec.columns} on retained {spec.table} is not declared by the model",
                    )
                )
    return tuple(findings)


def _column_diff(table_name: str, declared: sa.Table, model_table: sa.Table) -> str:
    declared_by_name = {spec.name: spec for spec in column_specs(declared)}
    model_by_name = {spec.name: spec for spec in column_specs(model_table)}
    parts: list[str] = []
    for name in sorted(set(declared_by_name) - set(model_by_name)):
        parts.append(f"only in migration: {name}")
    for name in sorted(set(model_by_name) - set(declared_by_name)):
        parts.append(f"only in model: {name}")
    for name in sorted(set(declared_by_name) & set(model_by_name)):
        if declared_by_name[name] != model_by_name[name]:
            parts.append(f"{name}: migration {declared_by_name[name]} vs model {model_by_name[name]}")
    if tuple(declared_by_name) != tuple(model_by_name):
        parts.append("column order differs")
    return f"{table_name}: " + "; ".join(parts)


def _check_diff(table_name: str, declared: sa.Table, model_table: sa.Table) -> str:
    declared_by_name = {spec[0]: spec[1] for spec in check_specs(declared)}
    model_by_name = {spec[0]: spec[1] for spec in check_specs(model_table)}
    parts: list[str] = []
    for name in sorted(set(declared_by_name) - set(model_by_name), key=str):
        parts.append(f"only in migration: {name}")
    for name in sorted(set(model_by_name) - set(declared_by_name), key=str):
        parts.append(f"only in model: {name}")
    for name in sorted(set(declared_by_name) & set(model_by_name), key=str):
        if declared_by_name[name] != model_by_name[name]:
            parts.append(f"{name}: migration {declared_by_name[name]!r} vs model {model_by_name[name]!r}")
    return f"{table_name}: " + "; ".join(parts)


def all_findings() -> tuple[Finding, ...]:
    """Every automated finding, for the CLI report only (tests assert individually)."""

    return (
        *chain_findings(),
        *duplicate_findings(),
        *foreign_target_findings(),
        *downgrade_findings(),
        *rls_findings(),
        *parity_findings(),
    )


def _main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - review tool
    """Print the review findings; used by hand, never by pytest."""

    findings = all_findings()
    present = wave_present_revisions()
    print(f"wave revisions present: {present}")
    print(f"chain root: {CHAIN_ROOT_REVISION}; heads: {head_revisions()}")
    print(f"tables created by the wave: {len(wave_table_owners())}")
    print(f"findings: {len(findings)}")
    for finding in findings:
        print(" ", finding)
    return 0 if not findings else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
