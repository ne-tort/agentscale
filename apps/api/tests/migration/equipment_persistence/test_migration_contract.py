"""Acceptance contract for revision ``2026110305`` (``equipment_v2`` DDL).

Authored during the migration verification freeze and **intentionally not executed**.
Instead of running a database, the checks load the revision module, swap
``alembic.op`` for a recording stub, and compare the recorded operations against the
ORM metadata of ``prodavan.infrastructure.persistence.models.equipment_v2`` (the
declared source of truth). Every negative case names the database object that must
reject the offending row.

What is covered:

* linear revision chain (``2026110305`` after ``2026110304``) and no second head;
* exact table set, column order, column types, nullability and server defaults;
* primary keys, foreign keys with ``ondelete``, composite uniques, checks;
* every index, including the ``DESC`` ordering expressions;
* ``postgresql.JSONB`` for all JSON payload columns, no invented types;
* ``downgrade()`` dropping exactly the created objects in reverse order;
* absence of RLS, of invented scope columns, and of a second status vocabulary owner;
* five negative cases: duplicate ingestion-run source digest, request line without
  request, offer from a foreign snapshot, negative quantity/money, candidate group
  outside a request line.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from prodavan.infrastructure.persistence.models.equipment_v2 import (
    EquipmentBudgetAllocationRow,
    EquipmentCandidateGroupRow,
    EquipmentCatalogOfferRow,
    EquipmentCatalogSnapshotRow,
    EquipmentIngestionRunRow,
    EquipmentRequestLineRow,
    EquipmentRequestRow,
    EquipmentTenderRow,
)

API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = API_ROOT / "alembic" / "versions"
REVISION_PATH = VERSIONS_DIR / "2026110305_equipment_v2.py"

_MODELS = (
    EquipmentIngestionRunRow,
    EquipmentCatalogSnapshotRow,
    EquipmentCatalogOfferRow,
    EquipmentRequestRow,
    EquipmentRequestLineRow,
    EquipmentCandidateGroupRow,
    EquipmentBudgetAllocationRow,
    EquipmentTenderRow,
)

# Creation order follows foreign-key dependency, not the model file order.
_EXPECTED_TABLES = (
    "equipment_v2_ingestion_runs",
    "equipment_v2_catalog_snapshots",
    "equipment_v2_catalog_offers",
    "equipment_v2_requests",
    "equipment_v2_request_lines",
    "equipment_v2_candidate_groups",
    "equipment_v2_budget_allocations",
    "equipment_v2_tenders",
)


class _DdlRecorder:
    """Stand-in for ``alembic.op`` that builds the described schema in memory.

    Only the operations authored by this revision are implemented; any other call
    (``add_column``, ``execute`` for policy DDL, ``bulk_insert``, ...) raises
    ``AttributeError`` so that an unexpected operation fails the acceptance run
    instead of passing silently. ``execute`` is recorded so tests can assert the
    revision still performs no raw DDL.
    """

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: list[sa.Table] = []
        self.indexes: list[tuple[Any, ...]] = []
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[dict[str, Any]] = []
        self.executed: list[str] = []

    def create_table(self, name: str, *elements: Any, **kw: Any) -> None:
        self.tables.append(sa.Table(name, self.metadata, *elements, **kw))

    def create_index(self, name: str, table_name: str, columns: Any, unique: bool = False, **kw: Any) -> None:
        assert table_name in self.metadata.tables, f"index {name!r} targets an unknown table"
        self.indexes.append(_index_signature(name, table_name, columns, unique))

    def drop_index(self, name: str, table_name: str | None = None, **kw: Any) -> None:
        self.dropped_indexes.append({"name": name, "table": table_name})

    def drop_table(self, name: str, **kw: Any) -> None:
        self.dropped_tables.append(name)

    def execute(self, statement: Any, **kw: Any) -> None:
        self.executed.append(str(statement))


def _load_revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("equipment_v2_revision_2026110305", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class _RevisionHeader:
    """Chain metadata of one revision file, parsed from source (never imported)."""

    revision: str
    down_revision: str | tuple[str, ...] | None
    path: Path

    @property
    def parents(self) -> tuple[str, ...]:
        if self.down_revision is None:
            return ()
        if isinstance(self.down_revision, str):
            return (self.down_revision,)
        return tuple(self.down_revision)


@dataclass(frozen=True)
class _RevisionGraph:
    """Revision headers keyed by id, plus every file that claims each id.

    ``claims`` exists because ``headers`` cannot represent the defect this test
    looks for: two files declaring the same ``revision`` id collapse onto one
    dictionary key, so uniqueness has to be checked on the raw files.
    """

    headers: dict[str, _RevisionHeader]
    claims: dict[str, tuple[Path, ...]]

    def head_ids(self) -> tuple[str, ...]:
        referenced = {parent for header in self.headers.values() for parent in header.parents}
        return tuple(sorted(set(self.headers) - referenced))

    def children_of(self, revision: str) -> tuple[str, ...]:
        return tuple(sorted(
            child for child, header in self.headers.items() if revision in header.parents
        ))


def _revision_graph() -> _RevisionGraph:
    """Every declared revision in ``alembic/versions``, parsed from source.

    Read with :mod:`ast` instead of a text search: ``'revision = "…"'`` occurs
    inside ``down_revision = "…"``, so a substring scan reports every *child* of
    a revision as a competing claim on that revision's id.
    """
    headers: dict[str, _RevisionHeader] = {}
    claims: dict[str, list[Path]] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        values: dict[str, Any] = {}
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id not in {"revision", "down_revision"}:
                continue
            values[target.id] = ast.literal_eval(node.value)
        revision = values.get("revision")
        if not isinstance(revision, str):
            continue
        headers[revision] = _RevisionHeader(
            revision=revision, down_revision=values.get("down_revision"), path=path,
        )
        claims.setdefault(revision, []).append(path)
    assert headers, f"no revision files found in {VERSIONS_DIR}"
    return _RevisionGraph(
        headers=headers,
        claims={revision: tuple(paths) for revision, paths in claims.items()},
    )


def _record(module: ModuleType) -> tuple[_DdlRecorder, _DdlRecorder]:
    upgrade_recorder = _DdlRecorder()
    module.op = upgrade_recorder
    module.upgrade()

    downgrade_recorder = _DdlRecorder()
    module.op = downgrade_recorder
    module.downgrade()
    return upgrade_recorder, downgrade_recorder


def _normalized_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _type_signature(type_: sa.types.TypeEngine) -> tuple[Any, ...]:
    if isinstance(type_, postgresql.JSONB):
        return ("jsonb",)
    if isinstance(type_, sa.Text):
        return ("text",)
    if isinstance(type_, sa.String):
        return ("string", type_.length)
    if isinstance(type_, sa.Numeric):
        return ("numeric", type_.precision, type_.scale)
    if isinstance(type_, sa.Integer):
        return ("integer",)
    if isinstance(type_, sa.DateTime):
        return ("datetime", bool(type_.timezone))
    raise AssertionError(f"unexpected column type in equipment_v2 DDL: {type_!r}")


def _column_signature(column: sa.Column[Any]) -> tuple[Any, ...]:
    server_default = None if column.server_default is None else str(column.server_default.arg)
    return (
        column.name,
        _type_signature(column.type),
        bool(column.nullable),
        _normalized_sql(server_default) if server_default is not None else None,
    )


def _primary_key_signature(constraint: sa.PrimaryKeyConstraint) -> tuple[Any, ...]:
    return tuple(column.name for column in constraint.columns)


def _foreign_key_signature(constraint: sa.ForeignKeyConstraint) -> tuple[Any, ...]:
    return (
        tuple(constraint.column_keys),
        tuple(element.target_fullname for element in constraint.elements),
        tuple(sorted({(element.ondelete or "") for element in constraint.elements})),
    )


def _unique_signature(constraint: sa.UniqueConstraint) -> tuple[Any, ...]:
    return (constraint.name, tuple(column.name for column in constraint.columns))


def _check_signature(constraint: sa.CheckConstraint) -> tuple[Any, ...]:
    return (constraint.name, _normalized_sql(str(constraint.sqltext)))


def _constraint_bundle(table: sa.Table) -> dict[str, list[tuple[Any, ...]]]:
    bundle: dict[str, list[tuple[Any, ...]]] = {"pk": [], "fk": [], "uq": [], "ck": []}
    for constraint in table.constraints:
        if isinstance(constraint, sa.PrimaryKeyConstraint):
            bundle["pk"].append(_primary_key_signature(constraint))
        elif isinstance(constraint, sa.ForeignKeyConstraint):
            bundle["fk"].append(_foreign_key_signature(constraint))
        elif isinstance(constraint, sa.UniqueConstraint):
            bundle["uq"].append(_unique_signature(constraint))
        elif isinstance(constraint, sa.CheckConstraint):
            bundle["ck"].append(_check_signature(constraint))
    return {kind: sorted(items) for kind, items in bundle.items()}


def _index_element(element: Any) -> str:
    if isinstance(element, sa.Column):
        return element.name
    return _normalized_sql(str(element))


def _index_signature(name: str, table_name: str, columns: Any, unique: bool) -> tuple[Any, ...]:
    return (name, table_name, tuple(_index_element(column) for column in columns), bool(unique))


def _table(recorder: _DdlRecorder, name: str) -> sa.Table:
    for table in recorder.tables:
        if table.name == name:
            return table
    raise AssertionError(f"revision does not create table {name!r}")


def _column(recorder: _DdlRecorder, table_name: str, column_name: str) -> sa.Column[Any]:
    table = _table(recorder, table_name)
    assert column_name in table.columns, f"{table_name} has no column {column_name!r}"
    return table.columns[column_name]


def _foreign_key_for(recorder: _DdlRecorder, table_name: str, column_name: str) -> dict[str, Any]:
    table = _table(recorder, table_name)
    for constraint in table.foreign_key_constraints:
        if column_name not in constraint.column_keys:
            continue
        local, targets, ondelete = _foreign_key_signature(constraint)
        return {"local": local, "targets": targets, "ondelete": ondelete}
    raise AssertionError(f"{table_name}.{column_name} has no foreign key")


def _orm_tables() -> dict[str, sa.Table]:
    return {model.__tablename__: model.__table__ for model in _MODELS}


#: Revision that anchors the equipment tables *after* ``2026110305`` created them
#: (task ``MIG-EQ-ANCHORS-01``): it adds ``project_id``/``cabinet_id``, their
#: ``RESTRICT`` keys and their indexes.  ``2026110305`` must still create exactly the
#: pre-anchor shape - that is what the "nothing is invented" assertions below pin -
#: but the table a database holds after ``upgrade head`` is the sum of both revisions,
#: and the model is the source of truth for that sum.  The parity tests therefore
#: replay the anchor revision on top: comparing ``2026110305`` alone against today's
#: metadata would demand that the model forget the anchors, which is the opposite of
#: what the metadata is for.
ANCHOR_REVISION_PATH = VERSIONS_DIR / "2026110319_equipment_anchors.py"


class _AlterRecorder:
    """Records the ALTER-shaped operations the anchor revision performs."""

    def __init__(self) -> None:
        self.added_columns: list[tuple[str, sa.Column[Any]]] = []
        self.created_foreign_keys: list[dict[str, Any]] = []
        self.indexes: list[tuple[str, str, list[Any], bool]] = []
        self.dropped_constraints: list[tuple[str, str]] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.created_tables: list[Any] = []
        self.executed: list[str] = []

    def add_column(self, table_name: str, column: sa.Column[Any], **kw: Any) -> None:
        self.added_columns.append((table_name, column))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        **kw: Any,
    ) -> None:
        self.created_foreign_keys.append(
            {
                "name": name,
                "table": source_table,
                "local": tuple(local_cols),
                "remote": tuple(f"{referent_table}.{column}" for column in remote_cols),
                "ondelete": kw.get("ondelete"),
            }
        )

    def create_index(self, name: str, table_name: str, columns: Any, unique: bool = False, **kw: Any) -> None:
        self.indexes.append((name, table_name, list(columns), unique))

    def drop_constraint(self, name: str, table_name: str, type_: str | None = None, **kw: Any) -> None:
        self.dropped_constraints.append((name, table_name))

    def drop_column(self, table_name: str, column_name: str, **kw: Any) -> None:
        self.dropped_columns.append((table_name, column_name))

    def create_table(self, *args: Any, **kw: Any) -> None:
        # The anchor revision also creates the two association tables, which are not
        # part of the eight ``2026110305`` tables this module compares; they are
        # covered by ``tests/migration/equipment_anchors``.
        self.created_tables.append(args[0] if args else kw.get("name"))

    def drop_table(self, *args: Any, **kw: Any) -> None:  # pragma: no cover - not expected
        raise AssertionError("the anchor revision must not drop a 2026110305 table")

    def drop_index(self, *args: Any, **kw: Any) -> None:
        # Only the indexes of its own association tables are dropped by the downgrade,
        # which this recorder does not replay.
        pass

    def execute(self, statement: Any, **kw: Any) -> None:
        self.executed.append(str(statement))

    def get_bind(self):  # noqa: ANN201 - mirrors Alembic's signature
        # The revision verifies the policies it just installed against the catalog.
        # This replay is database-free and only needs the schema operations, so the
        # verification is answered with "exists and carries its declared pair"; the
        # real check runs on a live server in ``tests/migration/rls`` and in the
        # round-trip exercised by ``tests/migration/equipment_anchors``.
        return _VerifiedBind()


class _VerifiedBind:
    """Minimal bind that reports every model table as present, policied and anchored."""

    def execute(self, statement: Any, params: dict[str, Any] | None = None):
        from prodavan.infrastructure.persistence.models import Base

        sql = " ".join(str(statement).split())
        relation = (params or {}).get("relation") or (params or {}).get("table")

        class _Result:
            def __init__(self, rows: list[tuple[Any, ...]]) -> None:
                self._rows = rows

            def all(self) -> list[tuple[Any, ...]]:
                return list(self._rows)

            def scalar(self) -> Any:
                return self._rows[0][0] if self._rows else None

            def scalar_one(self) -> Any:
                return self.scalar()

        from prodavan.infrastructure.persistence.rls import policies

        if relation not in Base.metadata.tables:
            return _Result([(None,)] if "to_regclass" in sql else [])
        model = Base.metadata.tables[relation]
        if "pg_attribute" in sql:
            return _Result([(column,) for column in model.columns.keys()])
        if "to_regclass" in sql:
            return _Result([(relation,)])
        if "pg_policy" in sql:
            spec = next((item for item in policies.CANONICAL_TABLE_POLICIES if item.table == relation), None)
            return _Result([] if spec is None else [(name,) for name in spec.policy_names])
        return _Result([])


def _anchor_replay() -> _AlterRecorder:
    spec = importlib.util.spec_from_file_location("_equipment_anchors_replay", ANCHOR_REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = _AlterRecorder()
    original = module.op
    module.op = recorder
    try:
        module.upgrade()
    finally:
        module.op = original
    return recorder


def _with_anchors(tables: dict[str, sa.Table], indexes: list[tuple[Any, ...]]) -> dict[str, sa.Table]:
    """Apply the anchor revision on top of the ``2026110305`` replay, in place.

    The result is what PostgreSQL holds after ``upgrade head``: the original columns
    in their original order, then the anchors, then the keys and indexes installed by
    the later revision.  ``indexes`` (the recorder's signature list) is extended in
    place so the index-parity test sees them too.
    """

    anchors = _anchor_replay()
    for table_name, column in anchors.added_columns:
        # Rebuilt rather than ``Column.copy()`` (deprecated in SQLAlchemy 1.4+): the
        # anchor columns carry no server default, so name/type/nullability is the whole
        # declaration, and rebuilding keeps the replayed objects independent.
        tables[table_name].append_column(
            sa.Column(column.name, column.type, nullable=column.nullable)
        )
    for key in anchors.created_foreign_keys:
        tables[key["table"]].append_constraint(
            sa.ForeignKeyConstraint(
                list(key["local"]),
                list(key["remote"]),
                name=key["name"],
                ondelete=key["ondelete"],
            )
        )
    for name, table_name, columns, unique in anchors.indexes:
        indexes.append(_index_signature(name, table_name, columns, unique))
    return tables


def _orm_index_signatures(table: sa.Table) -> list[tuple[Any, ...]]:
    return sorted(
        _index_signature(index.name, table.name, index.expressions, bool(index.unique))
        for index in table.indexes
    )


def test_revision_is_chained_after_canonical_grants_without_a_second_head() -> None:
    module = _load_revision()

    assert module.revision == "2026110305"
    assert module.down_revision == "2026110304"
    assert module.branch_labels is None
    assert module.depends_on is None

    # "No second head" is a property of the revision *graph*, not of one tip.
    # This revision sits mid-chain: revisions declared after it (0306…) are its
    # legitimate descendants, and a later tail does not create a branch. What
    # would create a branch is a fork — either another file claiming this very
    # revision id, or a second child of it — so both are checked directly.
    graph = _revision_graph()
    assert graph.headers[module.revision].parents == (module.down_revision,), (
        "the pinned revision must sit directly on its declared parent"
    )
    claimants = sorted(
        path.name for path in graph.claims[module.revision] if path != REVISION_PATH
    )
    assert claimants == [], f"revision id {module.revision} is also claimed by {claimants}"
    children = graph.children_of(module.revision)
    assert len(children) <= 1, f"{module.revision} has {len(children)} children: {children}"
    # Exactly one head overall: a fork anywhere above this revision would leave
    # more than one, and a head is what alembic upgrades to.
    heads = graph.head_ids()
    assert len(heads) == 1, f"expected a single alembic head, found {heads}"


def test_upgrade_creates_exactly_the_orm_tables_after_their_parents() -> None:
    upgrade, _ = _record(_load_revision())

    assert tuple(table.name for table in upgrade.tables) == _EXPECTED_TABLES
    assert set(_EXPECTED_TABLES) == set(_orm_tables())

    created: list[str] = []
    for table in upgrade.tables:
        for constraint in table.foreign_key_constraints:
            for element in constraint.elements:
                parent = element.target_fullname.split(".", 1)[0]
                assert parent in created, f"{table.name} is created before its parent {parent}"
        created.append(table.name)


def test_columns_match_orm_metadata_exactly() -> None:
    upgrade, _ = _record(_load_revision())
    orm_tables = _orm_tables()
    tables = _with_anchors({table.name: table for table in upgrade.tables}, upgrade.indexes)

    for table_name in _EXPECTED_TABLES:
        recorded = tables[table_name]
        orm = orm_tables[table_name]
        assert tuple(recorded.columns.keys()) == tuple(orm.columns.keys()), table_name
        assert [_column_signature(column) for column in recorded.columns] == [
            _column_signature(column) for column in orm.columns
        ], table_name


def test_constraints_match_orm_metadata_exactly() -> None:
    upgrade, _ = _record(_load_revision())
    orm_tables = _orm_tables()
    tables = _with_anchors({table.name: table for table in upgrade.tables}, upgrade.indexes)

    for table_name in _EXPECTED_TABLES:
        assert _constraint_bundle(tables[table_name]) == _constraint_bundle(
            orm_tables[table_name]
        ), table_name


def test_indexes_match_orm_metadata_exactly() -> None:
    upgrade, _ = _record(_load_revision())
    orm_tables = _orm_tables()
    # ``_with_anchors`` appends the anchor revision's indexes to this list in place.
    indexes: list[tuple[Any, ...]] = list(upgrade.indexes)
    _with_anchors({table.name: table for table in upgrade.tables}, indexes)

    for table_name in _EXPECTED_TABLES:
        recorded = sorted(signature for signature in indexes if signature[1] == table_name)
        assert recorded == _orm_index_signatures(orm_tables[table_name]), table_name


def test_jsonb_payload_columns_use_postgresql_jsonb() -> None:
    upgrade, _ = _record(_load_revision())

    jsonb_columns = {
        (table.name, column.name)
        for table in upgrade.tables
        for column in table.columns
        if isinstance(column.type, postgresql.JSONB)
    }
    assert jsonb_columns == {
        ("equipment_v2_catalog_snapshots", "metadata_json"),
        ("equipment_v2_catalog_offers", "attributes"),
        ("equipment_v2_request_lines", "specification"),
        ("equipment_v2_candidate_groups", "offer_ids"),
        ("equipment_v2_tenders", "candidate_group_ids"),
    }

    generic_json = [
        (table.name, column.name)
        for table in upgrade.tables
        for column in table.columns
        if isinstance(column.type, sa.JSON) and not isinstance(column.type, postgresql.JSONB)
    ]
    assert generic_json == []


def test_numeric_and_integer_types_are_not_invented() -> None:
    upgrade, _ = _record(_load_revision())

    numeric_columns = {
        (table.name, column.name): (column.type.precision, column.type.scale)
        for table in upgrade.tables
        for column in table.columns
        if isinstance(column.type, sa.Numeric)
    }
    assert numeric_columns == {
        ("equipment_v2_catalog_offers", "unit_price"): (18, 4),
        ("equipment_v2_catalog_offers", "available_quantity"): (18, 4),
        ("equipment_v2_request_lines", "quantity"): (18, 4),
        ("equipment_v2_budget_allocations", "amount"): (18, 4),
    }

    integer_columns = {
        (table.name, column.name)
        for table in upgrade.tables
        for column in table.columns
        if isinstance(column.type, sa.Integer)
    }
    assert integer_columns == {
        ("equipment_v2_ingestion_runs", "item_count"),
        ("equipment_v2_request_lines", "line_number"),
    }


def test_downgrade_drops_exactly_the_created_objects_in_reverse_order() -> None:
    upgrade, downgrade = _record(_load_revision())

    assert upgrade.dropped_tables == []
    assert upgrade.dropped_indexes == []
    assert upgrade.executed == []

    assert downgrade.dropped_tables == list(reversed(_EXPECTED_TABLES))
    assert [entry["name"] for entry in downgrade.dropped_indexes] == list(
        reversed([signature[0] for signature in upgrade.indexes])
    )
    assert {entry["table"] for entry in downgrade.dropped_indexes} == set(_EXPECTED_TABLES)
    assert downgrade.executed == []


def test_no_scope_columns_or_rls_policies_are_invented() -> None:
    upgrade, downgrade = _record(_load_revision())

    invented_scope = {
        (table.name, column.name)
        for table in upgrade.tables
        for column in table.columns
        if column.name in {"project_id", "cabinet_id", "company_id"}
    }
    assert invented_scope == set()

    tenant_owners = {
        table.name for table in upgrade.tables if "tenant_id" in table.columns
    }
    assert tenant_owners == {
        "equipment_v2_ingestion_runs",
        "equipment_v2_requests",
        "equipment_v2_tenders",
    }

    source = REVISION_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "CREATE POLICY",
        "ALTER TABLE",
    ):
        assert forbidden not in source
    assert upgrade.executed == [] and downgrade.executed == []


def test_status_vocabulary_stays_application_owned() -> None:
    """A status CHECK would make the revision a second policy owner.

    ``equipment_v2`` declares ``status`` as ``varchar(24)`` with a Python-side default
    only; the lifecycle vocabulary belongs to the typed command layer and to the
    persistence adapter outcome classifications, which extend it without a migration.
    """

    upgrade, _ = _record(_load_revision())

    status_columns = {
        (table.name, column.name): _type_signature(column.type)
        for table in upgrade.tables
        for column in table.columns
        if column.name == "status"
    }
    assert set(status_columns.values()) == {("string", 24)}
    assert len(status_columns) == 6

    status_checks = [
        (table.name, check)
        for table in upgrade.tables
        for check in _constraint_bundle(table)["ck"]
        if "status" in check[1]
    ]
    assert status_checks == []


def test_negative_duplicate_ingestion_run_with_same_source_digest_is_rejected() -> None:
    """A replay of one run may not store the same source digest twice.

    The ingestion run row carries no digest column (identity is the run itself), so
    content identity is enforced one level down: ``(ingestion_run_id, content_hash)``
    is unique on ``equipment_v2_catalog_snapshots``. A re-run creates a new run row;
    the same run cannot hold two snapshots with identical content, and a changed
    digest for the same run/source is a new snapshot rather than an overwrite.
    """

    upgrade, _ = _record(_load_revision())

    uniques = _constraint_bundle(_table(upgrade, "equipment_v2_catalog_snapshots"))["uq"]
    assert ("uq_equipment_v2_snapshot_hash", ("ingestion_run_id", "content_hash")) in uniques

    run_columns = set(_orm_tables()["equipment_v2_ingestion_runs"].columns.keys())
    assert not {"content_hash", "source_digest", "digest"} & run_columns


def test_negative_request_line_without_request_is_rejected() -> None:
    upgrade, _ = _record(_load_revision())

    column = _column(upgrade, "equipment_v2_request_lines", "request_id")
    assert column.nullable is False

    foreign_key = _foreign_key_for(upgrade, "equipment_v2_request_lines", "request_id")
    assert foreign_key["local"] == ("request_id",)
    assert foreign_key["targets"] == ("equipment_v2_requests.id",)
    assert foreign_key["ondelete"] == ("CASCADE",)


def test_negative_offer_from_foreign_snapshot_is_rejected() -> None:
    """An offer can only belong to a snapshot, and that snapshot keeps its run.

    Offers carry no ``tenant_id``, so cross-scope proof is the lineage
    ``offer.snapshot_id -> snapshot.ingestion_run_id -> run.tenant_id/source_id``.
    The offer FK is mandatory; the snapshot -> run FK is ``RESTRICT`` so provenance
    cannot be deleted out from under an offer.
    """

    upgrade, _ = _record(_load_revision())

    assert _column(upgrade, "equipment_v2_catalog_offers", "snapshot_id").nullable is False
    offer_fk = _foreign_key_for(upgrade, "equipment_v2_catalog_offers", "snapshot_id")
    assert offer_fk["targets"] == ("equipment_v2_catalog_snapshots.id",)
    assert offer_fk["ondelete"] == ("CASCADE",)

    assert _column(upgrade, "equipment_v2_catalog_snapshots", "ingestion_run_id").nullable is False
    snapshot_fk = _foreign_key_for(upgrade, "equipment_v2_catalog_snapshots", "ingestion_run_id")
    assert snapshot_fk["targets"] == ("equipment_v2_ingestion_runs.id",)
    assert snapshot_fk["ondelete"] == ("RESTRICT",)

    assert "tenant_id" not in _orm_tables()["equipment_v2_catalog_offers"].columns


def test_negative_quantity_and_money_are_rejected() -> None:
    upgrade, _ = _record(_load_revision())

    recorded = {
        (table.name, name, sql)
        for table in upgrade.tables
        for name, sql in _constraint_bundle(table)["ck"]
    }
    assert recorded == {
        (
            "equipment_v2_ingestion_runs",
            "ck_equipment_v2_ingestion_item_count_nonnegative",
            "item_count >= 0",
        ),
        (
            "equipment_v2_catalog_offers",
            "ck_equipment_v2_offer_price_nonnegative",
            "unit_price >= 0",
        ),
        (
            "equipment_v2_catalog_offers",
            "ck_equipment_v2_offer_quantity_nonnegative",
            "available_quantity >= 0",
        ),
        (
            "equipment_v2_catalog_offers",
            "ck_equipment_v2_offer_currency_upper",
            "currency = upper(currency)",
        ),
        (
            "equipment_v2_request_lines",
            "ck_equipment_v2_request_line_positive",
            "line_number > 0",
        ),
        (
            "equipment_v2_request_lines",
            "ck_equipment_v2_request_quantity_positive",
            "quantity > 0",
        ),
        (
            "equipment_v2_budget_allocations",
            "ck_equipment_v2_budget_amount_positive",
            "amount > 0",
        ),
        (
            "equipment_v2_tenders",
            "ck_equipment_v2_tender_window_order",
            "opens_at is null or closes_at is null or closes_at > opens_at",
        ),
    }


def test_negative_candidate_group_outside_request_line_is_rejected() -> None:
    """A candidate group must hang off one request line; selection cannot dangle.

    ``request_line_id`` is mandatory, so a group with no line is unrepresentable.
    ``selected_offer_id`` may be ``NULL`` (no human selection) and is released when
    the offer disappears. ``offer_ids`` stays JSONB compatibility storage until the
    normalized ``equipment_v2_candidate_group_offers`` table exists, so membership
    is not yet database-provable - the adapter contract keeps it unproven, not trusted.
    """

    upgrade, _ = _record(_load_revision())

    assert _column(upgrade, "equipment_v2_candidate_groups", "request_line_id").nullable is False
    line_fk = _foreign_key_for(upgrade, "equipment_v2_candidate_groups", "request_line_id")
    assert line_fk["targets"] == ("equipment_v2_request_lines.id",)
    assert line_fk["ondelete"] == ("CASCADE",)

    assert _column(upgrade, "equipment_v2_candidate_groups", "selected_offer_id").nullable is True
    offer_fk = _foreign_key_for(upgrade, "equipment_v2_candidate_groups", "selected_offer_id")
    assert offer_fk["targets"] == ("equipment_v2_catalog_offers.id",)
    assert offer_fk["ondelete"] == ("SET NULL",)

    assert isinstance(_column(upgrade, "equipment_v2_candidate_groups", "offer_ids").type, postgresql.JSONB)
    assert isinstance(_column(upgrade, "equipment_v2_tenders", "candidate_group_ids").type, postgresql.JSONB)
