"""MIG-DDL-M04E-01 contract for Alembic revision ``2026110302``.

Static acceptance artifact, authored during the verification freeze and
intentionally **not executed** as part of this task.

The test never connects to a database and never runs ``alembic upgrade``. It
replaces the ``alembic.op`` proxy with a recorder, executes the revision
module's ``upgrade()``/``downgrade()`` in-process, and compares the emitted
objects with the SQLAlchemy metadata that is the schema source of truth
(``apps/api/src/prodavan/infrastructure/persistence/models/
dynamic_app_definition_version.py``).

What it proves while reasoning over code:

* revision identifiers keep the wave chain ``2026110302 -> 2026110301``;
* exactly three M04.e tables are created, with column/type/nullability/default
  parity, named constraint parity, primary-key parity and index parity;
* every composite foreign key targets a primary/unique key, so the DDL is
  valid PostgreSQL rather than an op that fails at apply time;
* the definition -> current-version constraint is emitted as a *late*, named,
  ``DEFERRABLE INITIALLY DEFERRED`` FK after both keys exist;
* a database-side guard protects a non-draft version snapshot on every field
  the ORM already treats as immutable, refuses deletion of frozen evidence and
  refuses moving a frozen row back to ``draft``;
* no RLS policy, role, grant, backfill or data statement is emitted here
  (RLS belongs to revision ``2026110307``);
* ``downgrade()`` removes only these objects, trigger/function first, then the
  late FK, then tables in reverse creation order, using ``DROP ... IF EXISTS``
  for the trigger and function.

Negative cases encoded below are enforced by the named objects; the row-level
outcomes themselves (duplicate ``manifest_version`` insert, artifact update
after publish, cyclic current-version insert, cross-tenant version insert) need
a live PostgreSQL database and stay review inputs until the freeze is lifted.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import alembic
import alembic.op  # noqa: F401 — load the proxy module so it can be swapped below
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from prodavan.infrastructure.persistence.models import Base
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    _VERSION_IMMUTABLE_FIELDS,
    DynamicAppDefinitionFoundationRow,
    DynamicAppDefinitionVersionFoundationRow,
    M04EDefinitionVersionPromotionEvidenceRow,
)


REVISION_ID = "2026110302"
DOWN_REVISION_ID = "2026110301"

DEFINITION_TABLE = "m04e_dynamic_app_definitions"
VERSION_TABLE = "m04e_dynamic_app_versions"
PROMOTION_TABLE = "m04e_definition_version_promotion_evidence"

DEFINITION_INDEX = "ix_m04e_definition_tenant_lifecycle"
VERSION_INDEX = "ix_m04e_version_definition_lifecycle"
PROMOTION_INDEX = "ix_m04e_promotion_partition_time"

CURRENT_VERSION_FK = "fk_m04e_definition_current_version_same_definition"
ARTIFACT_GUARD_FUNCTION = "m04e_version_artifact_immutable"
ARTIFACT_GUARD_TRIGGER = "trg_m04e_version_artifact_immutable"

REVISION_FILE = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "2026110302_m04e_definition_version.py"
)

MODEL_TABLES = (DynamicAppDefinitionFoundationRow, DynamicAppDefinitionVersionFoundationRow,
                M04EDefinitionVersionPromotionEvidenceRow)
MANAGED_TABLES = (DEFINITION_TABLE, VERSION_TABLE, PROMOTION_TABLE)

_DIALECT = postgresql.dialect()

# DDL shapes that must not appear in this revision. RLS is a separate revision
# (2026110307); grants/writers/backfill are never part of a foundation DDL step.
FORBIDDEN_DDL = ("ROW LEVEL SECURITY", "CREATE POLICY", "GRANT ", "REVOKE ", "INSERT INTO", "ALTER TABLE")

# Row-level scenarios that still need a live PostgreSQL database and therefore
# stay review inputs until the verification freeze is lifted. Each one names the
# object that must produce the rejection and the expected SQLSTATE.
#
# 1. duplicate manifest version (23505):
#      INSERT a second version with the same (definition_id, manifest_version)
#      and a different artifact_digest -> uq_m04e_version_definition_manifest.
# 2. artifact mutation after publish (23514 from the trigger):
#      UPDATE a published version's artifact_digest / artifact_uri / manifest /
#      requested_capabilities, or any identity column -> guard trigger; the same
#      UPDATE on a draft row must succeed, and flipping lifecycle to 'published'
#      must remain possible while published_at stays consistent. Published ->
#      'disabled' must succeed only while nulling published_at, and the same
#      row must still reject an artifact change and reject UPDATE ... lifecycle
#      = 'draft' (rewind branch).
# 3. cyclic / cross-definition current version (23503):
#      INSERT definition D1 with current_version_id pointing at V2 of another
#      definition -> fk_m04e_definition_current_version_same_definition; a
#      mutually referencing D1 <-> V1 pair inside one transaction must succeed
#      because the constraint is INITIALLY DEFERRED, and must fail at COMMIT when
#      the version row is never inserted.
# 4. cross-tenant version (23503):
#      INSERT a version whose tenant_id or app_id differs from its definition ->
#      fk_m04e_version_definition_tenant / fk_m04e_version_definition_app.


class RecordedOps:
    """Minimal stand-in for the ``alembic.op`` proxy.

    Tables are rebuilt with real SQLAlchemy objects so the recorded revision can
    be compared structurally with the ORM metadata. A local ``companies`` stub
    keeps the tenant foreign key resolvable inside the recording metadata; it
    is a test scaffold and is never created by the revision.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.metadata = sa.MetaData()
        sa.Table("companies", self.metadata, sa.Column("id", sa.String(40), primary_key=True))
        self.tables: dict[str, sa.Table] = {}

    def create_table(self, table_name: str, *columns: Any, **kwargs: Any) -> None:
        table = sa.Table(table_name, self.metadata, *columns, **kwargs)
        self.tables[table_name] = table
        self.calls.append(("create_table", table_name))

    def create_index(
        self,
        index_name: str,
        table_name: str,
        columns: Any,
        **kwargs: Any,
    ) -> None:
        self.calls.append(("create_index", table_name, index_name, tuple(columns), kwargs))

    def create_foreign_key(
        self,
        constraint_name: str,
        source_table: str,
        referent_table: str,
        local_cols: Any,
        remote_cols: Any,
        **kwargs: Any,
    ) -> None:
        self.calls.append(
            (
                "create_foreign_key",
                constraint_name,
                source_table,
                referent_table,
                tuple(local_cols),
                tuple(remote_cols),
                kwargs,
            )
        )

    def drop_constraint(
        self,
        constraint_name: str,
        table_name: str,
        type_: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.calls.append(("drop_constraint", table_name, constraint_name, type_))

    def drop_index(self, index_name: str, table_name: str | None = None, **kwargs: Any) -> None:
        self.calls.append(("drop_index", table_name, index_name))

    def drop_table(self, table_name: str, **kwargs: Any) -> None:
        self.calls.append(("drop_table", table_name))

    def execute(self, statement: Any, **kwargs: Any) -> None:
        self.calls.append(("execute", str(statement)))


def _load_revision_module_from(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_revision_module() -> Any:
    return _load_revision_module_from(REVISION_FILE)


@pytest.fixture()
def revision(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, RecordedOps]:
    """Import the revision with a recording ``op`` proxy and run ``upgrade()``."""
    recorder = RecordedOps()
    monkeypatch.setattr(alembic, "op", recorder)
    module = _load_revision_module()
    module.upgrade()
    return module, recorder


def _type_label(type_: Any) -> str:
    return _DIALECT.type_compiler.process(type_)


def _default_text(column: sa.Column) -> str | None:
    if column.server_default is None:
        return None
    return " ".join(str(column.server_default.arg).split()).lower()


def _column_signature(table: sa.Table) -> list[tuple[str, str, bool, str | None]]:
    return [
        (column.name, _type_label(column.type), column.nullable, _default_text(column))
        for column in table.columns
    ]


def _check_catalog(table: sa.Table) -> dict[str | None, str]:
    return {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def _unique_catalog(table: sa.Table) -> dict[str | None, tuple[str, ...]]:
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def _fk_signature(constraint: sa.ForeignKeyConstraint) -> tuple[Any, ...]:
    return (
        tuple(element.parent.name for element in constraint.elements),
        tuple(element.target_fullname for element in constraint.elements),
        constraint.ondelete,
        constraint.deferrable,
        constraint.initially,
    )


def _metadata_fk_catalog(table: sa.Table) -> dict[str | None, tuple[Any, ...]]:
    return {
        constraint.name: _fk_signature(constraint)
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    }


def _recorded_fk_catalog(recorder: RecordedOps, table_name: str) -> dict[str | None, tuple[Any, ...]]:
    """Inline FKs from ``create_table`` plus late FKs from ``create_foreign_key``."""
    catalog = _metadata_fk_catalog(recorder.tables[table_name])
    for call in recorder.calls:
        if call[0] != "create_foreign_key":
            continue
        _, constraint_name, source_table, referent_table, local_cols, remote_cols, options = call
        if source_table != table_name:
            continue
        catalog[constraint_name] = (
            tuple(local_cols),
            tuple(f"{referent_table}.{column}" for column in remote_cols),
            options.get("ondelete"),
            options.get("deferrable"),
            options.get("initially"),
        )
    return catalog


def _unique_key_column_sets(table: sa.Table) -> set[tuple[str, ...]]:
    keys = {tuple(column.name for column in table.primary_key.columns)}
    keys.update(tuple(column.name for column in constraint.columns)
                for constraint in table.constraints
                if isinstance(constraint, sa.UniqueConstraint))
    return keys


def test_revision_identifiers_follow_the_wave_chain() -> None:
    # Identifiers are plain module attributes: the module is loaded with the real
    # ``op`` proxy, which is never called at import time.
    module = _load_revision_module()

    assert REVISION_FILE.is_file()
    assert module.revision == REVISION_ID
    assert module.down_revision == DOWN_REVISION_ID
    assert module.branch_labels is None
    assert module.depends_on is None


def test_next_revision_chains_from_this_one() -> None:
    """A parallel agent owns 2026110303; it must not fork the head."""
    successor = REVISION_FILE.with_name("2026110303_m05_plugin_instance_registry.py")
    if not successor.is_file():
        pytest.skip("successor revision is not authored yet")

    module = _load_revision_module_from(successor)

    assert module.down_revision == REVISION_ID


def test_creates_only_the_three_m04e_tables(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    assert set(recorder.tables) == set(MANAGED_TABLES)
    for table_name in MANAGED_TABLES:
        assert table_name in Base.metadata.tables
    for model in MODEL_TABLES:
        assert model.__tablename__ in MANAGED_TABLES


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_columns_match_metadata(revision: tuple[Any, RecordedOps], table_name: str) -> None:
    _, recorder = revision

    recorded = recorder.tables[table_name]
    expected = Base.metadata.tables[table_name]

    assert recorded.schema is None
    assert _column_signature(recorded) == _column_signature(expected)


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_check_constraints_match_metadata(revision: tuple[Any, RecordedOps], table_name: str) -> None:
    _, recorder = revision

    recorded = _check_catalog(recorder.tables[table_name])
    expected = _check_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(name for name in recorded), "every check constraint is explicitly named"


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_unique_constraints_match_metadata(revision: tuple[Any, RecordedOps], table_name: str) -> None:
    _, recorder = revision

    recorded = _unique_catalog(recorder.tables[table_name])
    expected = _unique_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(name for name in recorded), "every unique constraint is explicitly named"


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_foreign_keys_match_metadata(revision: tuple[Any, RecordedOps], table_name: str) -> None:
    _, recorder = revision

    recorded = _recorded_fk_catalog(recorder, table_name)
    expected = _metadata_fk_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(name for name in recorded), "every foreign key is explicitly named"


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_primary_keys_match_metadata(revision: tuple[Any, RecordedOps], table_name: str) -> None:
    _, recorder = revision

    recorded = tuple(column.name for column in recorder.tables[table_name].primary_key.columns)
    expected = tuple(column.name for column in Base.metadata.tables[table_name].primary_key.columns)

    assert recorded == expected


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_indexes_match_metadata(revision: tuple[Any, RecordedOps], table_name: str) -> None:
    _, recorder = revision

    recorded = {
        (call[2], tuple(call[3]))
        for call in recorder.calls
        if call[0] == "create_index" and call[1] == table_name
    }
    expected = {
        (index.name, tuple(column.name for column in index.columns))
        for index in Base.metadata.tables[table_name].indexes
    }

    assert recorded == expected


def test_every_composite_foreign_key_targets_a_primary_or_unique_key(
    revision: tuple[Any, RecordedOps],
) -> None:
    """A composite FK against a non-unique column set is invalid PostgreSQL DDL."""
    _, recorder = revision

    checked: list[str] = []
    for table_name in MANAGED_TABLES:
        for constraint_name, signature in _recorded_fk_catalog(recorder, table_name).items():
            local_cols, target_fullnames = signature[0], signature[1]
            if len(local_cols) < 2:
                continue
            referent_table = target_fullnames[0].split(".", 1)[0]
            referent_columns = tuple(item.split(".", 1)[1] for item in target_fullnames)
            assert referent_table in Base.metadata.tables, (
                f"{constraint_name}: referent table {referent_table} is not registered in metadata"
            )
            assert referent_columns in _unique_key_column_sets(Base.metadata.tables[referent_table]), (
                f"{constraint_name}: referenced columns {referent_columns} of {referent_table} "
                "are not covered by a primary or unique key"
            )
            checked.append(constraint_name)

    assert "fk_m04e_version_definition_tenant" in checked
    assert "fk_m04e_version_definition_app" in checked
    assert "fk_m04e_promotion_definition_tenant" in checked
    assert CURRENT_VERSION_FK in checked


def test_current_version_fk_is_late_named_and_deferred(revision: tuple[Any, RecordedOps]) -> None:
    """The definition <-> version cycle is broken by one deferred, late constraint."""
    _, recorder = revision

    create_fk_calls = [call for call in recorder.calls if call[0] == "create_foreign_key"]
    assert len(create_fk_calls) == 1

    _, name, source_table, referent_table, local_cols, remote_cols, options = create_fk_calls[0]
    assert name == CURRENT_VERSION_FK
    assert source_table == DEFINITION_TABLE
    assert referent_table == VERSION_TABLE
    assert local_cols == ("current_version_id", "id")
    assert remote_cols == ("id", "definition_id")
    assert options["deferrable"] is True
    assert options["initially"] == "DEFERRED"
    assert options["ondelete"] == "RESTRICT"
    assert options["use_alter"] is True

    positions = {
        call[1]: index for index, call in enumerate(recorder.calls) if call[0] == "create_table"
    }
    fk_position = next(
        index for index, call in enumerate(recorder.calls) if call[0] == "create_foreign_key"
    )
    assert fk_position > positions[DEFINITION_TABLE]
    assert fk_position > positions[VERSION_TABLE]

    # The table itself must not carry a non-deferrable variant of the same pair.
    inline = _metadata_fk_catalog(recorder.tables[DEFINITION_TABLE])
    assert CURRENT_VERSION_FK not in inline


def test_version_snapshot_guard_is_emitted_for_every_immutable_field(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision

    statements = [call[1] for call in recorder.calls if call[0] == "execute"]
    assert len(statements) == 2

    function_ddl = next(statement for statement in statements if "CREATE OR REPLACE FUNCTION" in statement)
    trigger_ddl = next(statement for statement in statements if statement.startswith("CREATE TRIGGER"))

    assert ARTIFACT_GUARD_FUNCTION in function_ddl
    assert "RETURNS trigger" in function_ddl
    assert "LANGUAGE plpgsql" in function_ddl
    assert ARTIFACT_GUARD_TRIGGER in trigger_ddl
    assert f"BEFORE UPDATE OR DELETE ON {VERSION_TABLE}" in trigger_ddl

    # The snapshot freezes on every state other than draft, so disabling a
    # published row cannot be used to reopen its artifact for mutation.
    assert "OLD.lifecycle <> 'draft'" in function_ddl
    assert "NEW.lifecycle = 'draft'" in function_ddl
    assert "m04e_version_snapshot_lifecycle_rewind" in function_ddl
    # Deletion of frozen evidence is refused as well.
    assert "TG_OP = 'DELETE'" in function_ddl
    # Redacted reason code, never row values or digests.
    assert "m04e_version_snapshot_immutable" in function_ddl
    assert "ERRCODE = '23514'" in function_ddl

    for field in _VERSION_IMMUTABLE_FIELDS:
        assert f"NEW.{field} IS DISTINCT FROM OLD.{field}" in function_ddl, (
            f"artifact guard must reject direct SQL changes to {field}"
        )


def test_revision_creates_no_rls_grants_or_data(revision: tuple[Any, RecordedOps]) -> None:
    """RLS policies belong to revision 2026110307; no writer or data path here."""
    _, recorder = revision

    assert {call[0] for call in recorder.calls} <= {
        "create_table",
        "create_index",
        "create_foreign_key",
        "execute",
    }

    for call in recorder.calls:
        if call[0] != "execute":
            continue
        statement = call[1].upper()
        for forbidden in FORBIDDEN_DDL:
            assert forbidden not in statement, f"unexpected DDL fragment {forbidden!r}"


NEGATIVE_CASES = (
    (
        "duplicate_manifest_version_within_definition",
        VERSION_TABLE,
        "uq_m04e_version_definition_manifest",
        ("definition_id", "manifest_version"),
    ),
    (
        "same_artifact_digest_within_definition",
        VERSION_TABLE,
        "uq_m04e_version_definition_artifact",
        ("definition_id", "artifact_digest"),
    ),
    (
        "cross_tenant_or_cross_definition_version",
        VERSION_TABLE,
        "fk_m04e_version_definition_tenant",
        ("definition_id", "tenant_id"),
    ),
    (
        "cross_definition_current_version_cycle",
        DEFINITION_TABLE,
        CURRENT_VERSION_FK,
        ("current_version_id", "id"),
    ),
)


@pytest.mark.parametrize(
    "case, table_name, constraint_name, columns",
    NEGATIVE_CASES,
    ids=[case[0] for case in NEGATIVE_CASES],
)
def test_negative_cases_are_enforced_by_named_objects(
    revision: tuple[Any, RecordedOps],
    case: str,
    table_name: str,
    constraint_name: str,
    columns: tuple[str, ...],
) -> None:
    _, recorder = revision

    uniques = _unique_catalog(recorder.tables[table_name])
    if constraint_name in uniques:
        assert uniques[constraint_name] == columns
        assert uniques[constraint_name] == _unique_catalog(Base.metadata.tables[table_name])[constraint_name]
        return

    recorded_fks = _recorded_fk_catalog(recorder, table_name)
    assert constraint_name in recorded_fks, f"{case} has no enforcing constraint"
    assert recorded_fks[constraint_name][0] == columns


def test_downgrade_removes_only_this_revision_objects_in_reverse_order(
    revision: tuple[Any, RecordedOps],
) -> None:
    module, recorder = revision

    upgrade_call_count = len(recorder.calls)
    module.downgrade()
    downgrade_calls = recorder.calls[upgrade_call_count:]
    kinds = [call[0] for call in downgrade_calls]

    # The late FK must go first, otherwise the versions table cannot be dropped.
    assert downgrade_calls[0] == ("drop_constraint", DEFINITION_TABLE, CURRENT_VERSION_FK, "foreignkey")
    assert kinds.count("drop_constraint") == 1

    dropped_tables = [call[1] for call in downgrade_calls if call[0] == "drop_table"]
    assert dropped_tables == [PROMOTION_TABLE, VERSION_TABLE, DEFINITION_TABLE]
    assert set(dropped_tables) == set(recorder.tables)

    dropped_indexes = {call[2] for call in downgrade_calls if call[0] == "drop_index"}
    assert dropped_indexes == {DEFINITION_INDEX, VERSION_INDEX, PROMOTION_INDEX}

    execute_calls = [call[1] for call in downgrade_calls if call[0] == "execute"]
    assert len(execute_calls) == 2
    assert all("IF EXISTS" in statement for statement in execute_calls)

    trigger_drop = next(
        index for index, call in enumerate(downgrade_calls)
        if call[0] == "execute" and "DROP TRIGGER IF EXISTS" in call[1]
    )
    function_drop = next(
        index for index, call in enumerate(downgrade_calls)
        if call[0] == "execute" and "DROP FUNCTION IF EXISTS" in call[1]
    )
    assert "DROP TRIGGER IF EXISTS " + ARTIFACT_GUARD_TRIGGER in downgrade_calls[trigger_drop][1]
    assert f" ON {VERSION_TABLE}" in downgrade_calls[trigger_drop][1]
    assert "DROP FUNCTION IF EXISTS " + ARTIFACT_GUARD_FUNCTION in downgrade_calls[function_drop][1]

    version_table_drop = downgrade_calls.index(("drop_table", VERSION_TABLE))
    assert trigger_drop < function_drop < version_table_drop
    assert kinds[-1] == "drop_table"
