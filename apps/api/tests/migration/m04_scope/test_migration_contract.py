"""MIG-DDL-M04-01 acceptance contract for Alembic revision ``2026110301``.

Authored as a review artifact under the migration verification freeze: it is not
executed yet. The module never opens a database connection, never boots the
Alembic environment and never renders real SQL — it drives ``upgrade()`` and
``downgrade()`` against a recording stand-in for ``alembic.op`` and compares the
recorded DDL intent with ``models/owner_registry.py``, which is the schema's
source of truth.

What is proven here:

* revision identity and linear chain (``2026110301`` ← ``2026110202``);
* the created table set is exactly the seven owner-registry tables, in the
  documented dependency order, with no extra and no missing table;
* column parity (order, dialect type, nullability, server default);
* constraint parity (name, kind, column tuple, check expression, FK target tuple
  and ``ondelete``);
* index parity (name, table, column order);
* every composite FK target exposes a matching unique/primary key, so the DDL is
  accepted by PostgreSQL instead of failing at ``CREATE TABLE`` time;
* no dangling FK targets: every reference resolves to a table created earlier in
  this revision or to a retained parent table that already exists;
* ``downgrade()`` is the strict reverse (tables, indexes, retained-parent key);
* negative cases: no legacy ``dynamic_app_*`` object is created, altered or
  dropped, ``dynamic_app_capability_grants`` is untouched, no raw SQL is
  emitted (RLS/privileges/append-only belong to revision ``2026110307``), and no
  M05 plugin-instance object is created (revision ``2026110303``).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

import prodavan.infrastructure.persistence.models  # noqa: F401 — full Base.metadata
from prodavan.infrastructure.persistence.models import owner_registry as owner_models
from prodavan.infrastructure.persistence.models.base import Base

POSTGRES = postgresql.dialect()

REVISION_FILE = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "2026110301_m04_owner_registry.py"
)

EXPECTED_REVISION = "2026110301"
EXPECTED_DOWN_REVISION = "2026110202"

# The order is part of the contract: it is the FK dependency order, not an
# alphabetical convenience. Changing it is a schema-review event.
EXPECTED_CREATE_ORDER = (
    "scope_owner_tenant_anchors",
    "scope_owner_cabinet_anchors",
    "scope_owner_project_anchors",
    "scope_registry",
    "scope_owner_mappings",
    "scope_owner_quarantine",
    "scope_owner_audit",
)

MODEL_BY_TABLE = {
    "scope_owner_tenant_anchors": owner_models.CanonicalTenantAnchorRow,
    "scope_owner_cabinet_anchors": owner_models.ScopeOwnerCabinetAnchorRow,
    "scope_owner_project_anchors": owner_models.ScopeOwnerProjectAnchorRow,
    "scope_registry": owner_models.ScopeRegistryRow,
    "scope_owner_mappings": owner_models.ScopeOwnerMappingRow,
    "scope_owner_quarantine": owner_models.ScopeOwnerQuarantineRow,
    "scope_owner_audit": owner_models.ScopeOwnerAuditRow,
}

# Pre-existing tables owned by earlier revisions; this revision may reference
# them but must never create, alter or drop them.
RETAINED_PARENT_TABLES = frozenset(
    {"companies", "cabinet_instances", "cabinet_company_grants", "projects"}
)

LEGACY_DYNAMIC_APP_TABLES = frozenset(
    {
        "dynamic_app_capability_grants",
        "dynamic_app_definitions",
        "dynamic_app_versions",
        "dynamic_app_grants",
        "dynamic_app_instances",
    }
)

# Appended to scope_registry by plugin_instance_registry.py; owner is 2026110303.
DEFERRED_M05_PREFIXES = ("fk_m05_", "uq_scope_registry_m05_instance_")

PARENT_COMPATIBILITY_KEY = "uq_projects_scope_owner_parent_tuple"


# --------------------------------------------------------------------------- #
# Recording stand-in for alembic.op
# --------------------------------------------------------------------------- #


class _RecordingOp:
    """Records DDL intent as real SQLAlchemy objects; rejects every other op.

    Every ``create_table`` builds a genuine ``sa.Table`` inside a metadata that
    already contains the retained parent tables, so a constraint that names a
    missing column raises immediately and an FK that names a missing target
    column cannot be resolved later.
    """

    def __init__(self, metadata: sa.MetaData) -> None:
        self.metadata = metadata
        self.created_tables: list[sa.Table] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...]]] = []
        self.added_unique_constraints: list[tuple[str, str, tuple[str, ...]]] = []
        self.dropped: list[tuple[str, ...]] = []
        self.raw_sql: list[str] = []

    # -- upgrade surface ---------------------------------------------------- #

    def create_table(self, name: str, *args: object, **kwargs: object) -> sa.Table:
        table = sa.Table(name, self.metadata, *args, **kwargs)
        self.created_tables.append(table)
        return table

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        **kwargs: object,
    ) -> None:
        self.created_indexes.append((name, table_name, tuple(columns)))

    def create_unique_constraint(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        **kwargs: object,
    ) -> None:
        table = self.metadata.tables[table_name]
        table.append_constraint(sa.UniqueConstraint(*columns, name=name))
        self.added_unique_constraints.append((name, table_name, tuple(columns)))

    # -- downgrade surface -------------------------------------------------- #

    def drop_table(self, name: str, **kwargs: object) -> None:
        self.dropped.append(("drop_table", name))
        self.metadata.remove(self.metadata.tables[name])

    def drop_index(self, name: str, table_name: str, **kwargs: object) -> None:
        self.dropped.append(("drop_index", name, table_name))

    def drop_constraint(self, name: str, table_name: str, **kwargs: object) -> None:
        self.dropped.append(("drop_constraint", name, table_name))

    # -- escape hatch that must stay unused --------------------------------- #

    def execute(self, statement: object, *args: object, **kwargs: object) -> None:
        self.raw_sql.append(str(statement))

    def __getattr__(self, item: str) -> object:
        raise AssertionError(
            f"revision {EXPECTED_REVISION} must stay purely additive: unexpected op.{item}()"
        )


def _retained_parent_metadata() -> sa.MetaData:
    """Metadata holding only what earlier revisions already created."""
    metadata = sa.MetaData()
    sa.Table("companies", metadata, sa.Column("id", sa.String(40), primary_key=True))
    sa.Table("cabinet_instances", metadata, sa.Column("id", sa.String(40), primary_key=True))
    sa.Table(
        "cabinet_company_grants",
        metadata,
        sa.Column("cabinet_id", sa.String(40), nullable=False),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("id", sa.String(40), primary_key=True),
        # Created by revision 2026082702; mirrored here as pre-existing state.
        sa.UniqueConstraint("cabinet_id", "company_id", name="uq_cabinet_company_grant"),
    )
    sa.Table(
        "projects",
        metadata,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("cabinet_id", sa.String(40), nullable=False),
    )
    return metadata


def _load_revision_module() -> ModuleType:
    assert REVISION_FILE.is_file(), f"missing revision file: {REVISION_FILE}"
    spec = importlib.util.spec_from_file_location("m04_owner_registry_revision", REVISION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def revision_module() -> ModuleType:
    return _load_revision_module()


@pytest.fixture()
def upgraded(revision_module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> _RecordingOp:
    recorder = _RecordingOp(_retained_parent_metadata())
    monkeypatch.setattr(revision_module, "op", recorder)
    revision_module.upgrade()
    return recorder


# --------------------------------------------------------------------------- #
# Comparison helpers
# --------------------------------------------------------------------------- #


def _normalize(sql: object) -> str:
    return " ".join(str(sql).split())


def _type_signature(column: sa.Column) -> str:
    return column.type.compile(dialect=POSTGRES)


def _server_default(column: sa.Column) -> str | None:
    default = column.server_default
    if default is None:
        return None
    return _normalize(default.arg)


def _column_signature(column: sa.Column) -> tuple[str, str, bool, str | None]:
    return (
        column.name,
        _type_signature(column),
        bool(column.nullable),
        _server_default(column),
    )


def _constraint_signature(constraint: object) -> tuple[object, ...]:
    if isinstance(constraint, sa.PrimaryKeyConstraint):
        return ("primary_key", tuple(column.name for column in constraint.columns))
    if isinstance(constraint, UniqueConstraint):
        return ("unique", tuple(column.name for column in constraint.columns))
    if isinstance(constraint, CheckConstraint):
        return ("check", _normalize(constraint.sqltext))
    if isinstance(constraint, ForeignKeyConstraint):
        return (
            "fk",
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
    raise AssertionError(f"unexpected constraint type: {type(constraint).__name__}")


def _is_deferred_m05(name: str | None) -> bool:
    return name is not None and name.startswith(DEFERRED_M05_PREFIXES)


def _constraint_map(
    table: sa.Table,
    *,
    drop_deferred_m05: bool = False,
) -> dict[str, tuple[object, ...]]:
    mapped: dict[str, tuple[object, ...]] = {}
    for constraint in table.constraints:
        name = constraint.name or "<primary_key>"
        if drop_deferred_m05 and _is_deferred_m05(constraint.name):
            continue
        mapped[name] = _constraint_signature(constraint)
    return mapped


def _index_map(table: sa.Table) -> dict[str, tuple[str, ...]]:
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }


def _created_index_map(recorder: _RecordingOp, table_name: str) -> dict[str, tuple[str, ...]]:
    return {
        name: columns
        for name, owner, columns in recorder.created_indexes
        if owner == table_name
    }


# --------------------------------------------------------------------------- #
# Revision identity and chain
# --------------------------------------------------------------------------- #


def test_revision_identity_and_linear_chain(revision_module: ModuleType) -> None:
    assert revision_module.revision == EXPECTED_REVISION
    assert revision_module.down_revision == EXPECTED_DOWN_REVISION
    assert revision_module.branch_labels is None
    assert revision_module.depends_on is None
    assert callable(revision_module.upgrade)
    assert callable(revision_module.downgrade)


def test_revision_file_name_matches_revision_id() -> None:
    assert REVISION_FILE.name == f"{EXPECTED_REVISION}_m04_owner_registry.py"


def test_revision_does_not_redeclare_itself_as_parent() -> None:
    assert EXPECTED_DOWN_REVISION != EXPECTED_REVISION
    assert EXPECTED_REVISION > EXPECTED_DOWN_REVISION


def _revision_declarations() -> list[tuple[str, str | None, object]]:
    """Return ``(file name, revision id, down_revision)`` for every revision file.

    Handles both ``revision = "..."`` and ``revision: str = "..."`` declarations;
    a list/tuple ``down_revision`` yields every branch parent downstream.
    """
    declarations: list[tuple[str, str | None, object]] = []
    for path in sorted(REVISION_FILE.parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        revision_id: str | None = None
        parents: object = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    continue
                target_name = node.targets[0].id
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is None:
                    continue
                target_name = node.target.id
                value = node.value
            else:
                continue
            if target_name == "revision":
                revision_id = ast.literal_eval(value)
            elif target_name == "down_revision":
                parents = ast.literal_eval(value)
        declarations.append((path.name, revision_id, parents))
    return declarations


def _revision_graph() -> dict[str, set[str]]:
    """Map revision id → parent ids without importing any revision module."""
    graph: dict[str, set[str]] = {}
    for _file_name, revision_id, parents in _revision_declarations():
        if revision_id is None:
            continue
        edges = graph.setdefault(revision_id, set())
        if isinstance(parents, str):
            edges.add(parents)
        elif isinstance(parents, (list, tuple)):
            edges.update(parent for parent in parents if isinstance(parent, str))
    return graph


def test_revision_id_is_unique_and_parent_exists() -> None:
    graph = _revision_graph()
    declarations = [
        (file_name, revision_id)
        for file_name, revision_id, _parents in _revision_declarations()
        if revision_id == EXPECTED_REVISION
    ]

    assert declarations == [(REVISION_FILE.name, EXPECTED_REVISION)]
    assert EXPECTED_REVISION in graph
    assert EXPECTED_DOWN_REVISION in graph
    assert graph[EXPECTED_REVISION] == {EXPECTED_DOWN_REVISION}


def test_this_revision_is_the_only_child_of_its_parent() -> None:
    graph = _revision_graph()
    children = [revision for revision, parents in graph.items() if EXPECTED_DOWN_REVISION in parents]

    assert children == [EXPECTED_REVISION], "a second child would fork the alembic head"


def test_no_other_revision_creates_the_same_objects() -> None:
    """No sibling revision may re-create the seven tables owned by this one."""
    for path in sorted(REVISION_FILE.parent.glob("*.py")):
        if path.name == REVISION_FILE.name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "create_table" or not node.args:
                continue
            try:
                table_name = ast.literal_eval(node.args[0])
            except ValueError:
                continue
            assert table_name not in EXPECTED_CREATE_ORDER, (
                f"{path.name} re-creates {table_name} owned by revision {EXPECTED_REVISION}"
            )


# --------------------------------------------------------------------------- #
# Table set and order
# --------------------------------------------------------------------------- #


def test_creates_exactly_the_seven_owner_registry_tables(upgraded: _RecordingOp) -> None:
    created = tuple(table.name for table in upgraded.created_tables)
    expected = tuple(table_name for table_name in EXPECTED_CREATE_ORDER)

    assert created == expected
    assert set(created) == {model.__tablename__ for model in MODEL_BY_TABLE.values()}
    assert len(created) == len(set(created)) == 7


def test_every_model_table_is_created_and_no_extra_table_exists(
    upgraded: _RecordingOp,
) -> None:
    created = {table.name for table in upgraded.created_tables}

    assert created == set(MODEL_BY_TABLE)
    assert created & RETAINED_PARENT_TABLES == frozenset()


def test_table_names_are_registered_in_orm_metadata(upgraded: _RecordingOp) -> None:
    for table in upgraded.created_tables:
        assert table.name in Base.metadata.tables, "revision created an unowned table"
        assert Base.metadata.tables[table.name] is MODEL_BY_TABLE[table.name].__table__


def test_revision_creates_no_m05_plugin_instance_table(upgraded: _RecordingOp) -> None:
    created = {table.name for table in upgraded.created_tables}

    assert not any(name.startswith("m05_") for name in created)


# --------------------------------------------------------------------------- #
# Column parity
# --------------------------------------------------------------------------- #


def test_columns_match_model_definition(upgraded: _RecordingOp) -> None:
    for table in upgraded.created_tables:
        model_table = MODEL_BY_TABLE[table.name].__table__
        created_signatures = [_column_signature(column) for column in table.columns]
        model_signatures = [_column_signature(column) for column in model_table.columns]

        assert created_signatures == model_signatures, table.name


def test_primary_keys_are_single_application_generated_ids(upgraded: _RecordingOp) -> None:
    for table in upgraded.created_tables:
        primary_key = tuple(column.name for column in table.primary_key.columns)

        assert primary_key == ("id",), table.name
        assert table.c.id.type.compile(dialect=POSTGRES) == "VARCHAR(40)"
        assert table.c.id.server_default is None, "identifiers stay application-generated"
        assert table.c.created_at.server_default is not None
        assert _server_default(table.c.created_at) == "now()"


# --------------------------------------------------------------------------- #
# Constraint parity
# --------------------------------------------------------------------------- #


def test_constraint_names_and_shapes_match_model_table_args(upgraded: _RecordingOp) -> None:
    for table in upgraded.created_tables:
        model_table = MODEL_BY_TABLE[table.name].__table__

        created = _constraint_map(table)
        expected = _constraint_map(model_table, drop_deferred_m05=True)

        assert created == expected, table.name
        assert len(created) == len(table.constraints)


def test_constraint_names_are_unique_per_table(upgraded: _RecordingOp) -> None:
    for table in upgraded.created_tables:
        names = [constraint.name for constraint in table.constraints if constraint.name]
        assert len(names) == len(set(names)), table.name


def test_deferred_m05_constraints_are_not_created(upgraded: _RecordingOp) -> None:
    for table in upgraded.created_tables:
        for constraint in table.constraints:
            assert not _is_deferred_m05(constraint.name), (
                f"{constraint.name} belongs to revision 2026110303"
            )


def test_all_foreign_keys_are_restrictive(upgraded: _RecordingOp) -> None:
    foreign_keys = [
        constraint
        for table in upgraded.created_tables
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]

    assert foreign_keys
    for constraint in foreign_keys:
        assert constraint.ondelete == "RESTRICT", constraint.name


def test_typed_registry_fk_targets_include_owner_state(upgraded: _RecordingOp) -> None:
    registry = next(
        table for table in upgraded.created_tables if table.name == "scope_registry"
    )
    targets = {
        constraint.name: tuple(element.target_fullname for element in constraint.elements)
        for constraint in registry.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert targets["fk_scope_registry_tenant_owner_state"] == (
        "scope_owner_tenant_anchors.tenant_id",
        "scope_owner_tenant_anchors.tenant_type",
        "scope_owner_tenant_anchors.owner_state",
    )
    assert targets["fk_scope_registry_cabinet_typed_parent"] == (
        "scope_owner_cabinet_anchors.cabinet_id",
        "scope_owner_cabinet_anchors.tenant_id",
        "scope_owner_cabinet_anchors.tenant_type",
        "scope_owner_cabinet_anchors.owner_state",
    )
    assert targets["fk_scope_registry_project_typed_parent"] == (
        "scope_owner_project_anchors.project_id",
        "scope_owner_project_anchors.tenant_id",
        "scope_owner_project_anchors.tenant_type",
        "scope_owner_project_anchors.cabinet_id",
        "scope_owner_project_anchors.project_status",
        "scope_owner_project_anchors.owner_state",
    )


def test_plugin_instance_column_has_no_foreign_key_yet(upgraded: _RecordingOp) -> None:
    registry = next(
        table for table in upgraded.created_tables if table.name == "scope_registry"
    )

    assert registry.c.plugin_instance_id.nullable is True
    assert not registry.c.plugin_instance_id.foreign_keys


# --------------------------------------------------------------------------- #
# Index parity
# --------------------------------------------------------------------------- #


def test_indexes_match_model_indexes(upgraded: _RecordingOp) -> None:
    for table in upgraded.created_tables:
        model_table = MODEL_BY_TABLE[table.name].__table__

        assert _created_index_map(upgraded, table.name) == _index_map(model_table), table.name


def test_every_index_is_created_for_a_created_table(upgraded: _RecordingOp) -> None:
    created_tables = {table.name for table in upgraded.created_tables}

    for name, table_name, columns in upgraded.created_indexes:
        assert table_name in created_tables
        assert columns
        assert name.startswith("ix_")


# --------------------------------------------------------------------------- #
# Referential integrity of the DDL itself
# --------------------------------------------------------------------------- #


def test_create_order_follows_foreign_key_dependencies(upgraded: _RecordingOp) -> None:
    positions = {
        table.name: index for index, table in enumerate(upgraded.created_tables)
    }

    for table in upgraded.created_tables:
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            target = constraint.elements[0].column.table.name
            if target in positions:
                assert positions[target] < positions[table.name], (
                    f"{table.name} references {target} before it exists"
                )


def test_no_dangling_fk_targets(upgraded: _RecordingOp) -> None:
    created = {table.name for table in upgraded.created_tables}

    for table in upgraded.created_tables:
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            targets = {element.column.table.name for element in constraint.elements}
            assert len(targets) == 1, f"{constraint.name} spans several tables"
            target = targets.pop()
            assert target in Base.metadata.tables, (
                f"{constraint.name} references unknown table {target}"
            )
            assert target in created or target in RETAINED_PARENT_TABLES, (
                f"{constraint.name} references {target} outside the migration chain"
            )


def test_composite_fk_targets_expose_matching_unique_keys(upgraded: _RecordingOp) -> None:
    for table in upgraded.created_tables:
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            if len(constraint.elements) < 2:
                continue
            target_table = upgraded.metadata.tables[constraint.elements[0].column.table.name]
            target_columns = frozenset(
                element.column.name for element in constraint.elements
            )
            unique_keys = {
                frozenset(column.name for column in candidate.columns)
                for candidate in target_table.constraints
                if isinstance(candidate, (UniqueConstraint, sa.PrimaryKeyConstraint))
            }
            assert target_columns in unique_keys, (
                f"{constraint.name} has no unique key on {target_table.name}"
            )


def test_retained_parent_compatibility_key_is_installed(upgraded: _RecordingOp) -> None:
    projects = upgraded.metadata.tables["projects"]

    assert (
        PARENT_COMPATIBILITY_KEY,
        "projects",
        ("id", "company_id", "cabinet_id"),
    ) in upgraded.added_unique_constraints
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == PARENT_COMPATIBILITY_KEY
        and tuple(column.name for column in constraint.columns)
        == ("id", "company_id", "cabinet_id")
        for constraint in projects.constraints
    )


def test_retained_parent_compatibility_key_matches_orm_declaration() -> None:
    projects = Base.metadata.tables["projects"]

    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == PARENT_COMPATIBILITY_KEY
        and tuple(column.name for column in constraint.columns)
        == ("id", "company_id", "cabinet_id")
        for constraint in projects.constraints
    )


def test_cabinet_grant_composite_target_matches_existing_revision() -> None:
    grants = Base.metadata.tables["cabinet_company_grants"]

    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_cabinet_company_grant"
        and tuple(column.name for column in constraint.columns)
        == ("cabinet_id", "company_id")
        for constraint in grants.constraints
    )


# --------------------------------------------------------------------------- #
# Downgrade
# --------------------------------------------------------------------------- #


@pytest.fixture()
def round_tripped(
    revision_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> _RecordingOp:
    recorder = _RecordingOp(_retained_parent_metadata())
    monkeypatch.setattr(revision_module, "op", recorder)
    revision_module.upgrade()
    revision_module.downgrade()
    return recorder


def test_downgrade_drops_tables_in_strict_reverse_order(round_tripped: _RecordingOp) -> None:
    dropped = tuple(
        entry[1] for entry in round_tripped.dropped if entry[0] == "drop_table"
    )

    assert dropped == tuple(reversed(EXPECTED_CREATE_ORDER))
    assert set(dropped) == set(MODEL_BY_TABLE)


def test_downgrade_drops_indexes_before_their_tables(round_tripped: _RecordingOp) -> None:
    dropped_indexes = {
        entry[1] for entry in round_tripped.dropped if entry[0] == "drop_index"
    }
    created_indexes = {entry[0] for entry in round_tripped.created_indexes}

    assert dropped_indexes == created_indexes

    positions = {entry: index for index, entry in enumerate(round_tripped.dropped)}
    for entry in round_tripped.dropped:
        if entry[0] != "drop_index":
            continue
        _, index_name, table_name = entry
        assert positions[("drop_index", index_name, table_name)] < positions[
            ("drop_table", table_name)
        ]


def test_downgrade_drops_composite_fks_before_the_parent_key(
    round_tripped: _RecordingOp,
) -> None:
    entries = list(round_tripped.dropped)

    assert entries[-1] == (
        "drop_constraint",
        PARENT_COMPATIBILITY_KEY,
        "projects",
    )
    assert entries.index(("drop_table", "scope_owner_project_anchors")) < len(entries) - 1
    assert entries.index(("drop_table", "scope_owner_tenant_anchors")) < len(entries) - 1


def test_downgrade_never_drops_a_retained_parent_table(round_tripped: _RecordingOp) -> None:
    dropped_tables = {
        entry[1] for entry in round_tripped.dropped if entry[0] == "drop_table"
    }
    touched_tables = {
        entry[2] for entry in round_tripped.dropped if entry[0] in {"drop_index", "drop_constraint"}
    }

    assert dropped_tables & RETAINED_PARENT_TABLES == frozenset()
    assert touched_tables <= set(MODEL_BY_TABLE) | {"projects"}
    assert set(round_tripped.metadata.tables) == RETAINED_PARENT_TABLES


def test_downgrade_leaves_orm_metadata_untouched(round_tripped: _RecordingOp) -> None:
    for table_name in MODEL_BY_TABLE:
        assert table_name in Base.metadata.tables
        assert Base.metadata.tables[table_name] is MODEL_BY_TABLE[table_name].__table__


# --------------------------------------------------------------------------- #
# Negative cases
# --------------------------------------------------------------------------- #


def test_revision_never_creates_legacy_dynamic_app_tables(round_tripped: _RecordingOp) -> None:
    created = {table.name for table in round_tripped.created_tables}

    assert created & LEGACY_DYNAMIC_APP_TABLES == frozenset()


def test_revision_never_touches_dynamic_app_capability_grants(
    round_tripped: _RecordingOp,
) -> None:
    created = {table.name for table in round_tripped.created_tables}
    dropped_tables = {
        entry[1] for entry in round_tripped.dropped if entry[0] == "drop_table"
    }
    referenced_tables = {
        entry[2] for entry in round_tripped.dropped if entry[0] in {"drop_index", "drop_constraint"}
    }
    referenced_tables |= {table_name for _, table_name, _ in round_tripped.created_indexes}

    assert "dynamic_app_capability_grants" not in created
    assert "dynamic_app_capability_grants" not in dropped_tables
    assert "dynamic_app_capability_grants" not in referenced_tables
    assert created | dropped_tables | referenced_tables <= set(MODEL_BY_TABLE) | {"projects"}


def test_no_foreign_key_points_at_a_legacy_table(round_tripped: _RecordingOp) -> None:
    for table in round_tripped.created_tables:
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            targets = {element.column.table.name for element in constraint.elements}
            assert targets & LEGACY_DYNAMIC_APP_TABLES == frozenset(), constraint.name


def test_revision_emits_no_raw_sql(upgraded: _RecordingOp) -> None:
    # RLS policies, role privileges and the append-only audit trigger are
    # revision 2026110307; any op.execute here would silently pre-empt it.
    assert upgraded.raw_sql == []


def test_upgrade_is_purely_additive(upgraded: _RecordingOp) -> None:
    assert upgraded.created_tables
    assert upgraded.created_indexes
    assert upgraded.dropped == [], "upgrade() must not drop or rename anything"
    assert upgraded.raw_sql == []
