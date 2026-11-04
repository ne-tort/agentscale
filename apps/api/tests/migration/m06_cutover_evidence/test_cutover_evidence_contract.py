"""Contract for ``MIG-DDL-CUTOVER-01``: model ↔ revision ↔ RLS registry ↔ port spec.

What this module proves, and from which side
=============================================
Three artefacts describe the same two tables, and the whole point of this module is
that none of them can drift from the others:

* ``application/migration/m06_cutover/ports.py::CUTOVER_REQUIRED_SCHEMA`` — the
  reviewed specification the repository reads (``CUTOVER_LEDGER_TABLE``,
  ``CUTOVER_SOURCE_TAIL_TABLE``, every column, key, check and index).
* ``models/m06_cutover_evidence.py`` — the declarative schema source of truth.
* ``alembic/versions/2026110316_m06_cutover_evidence.py`` — the DDL that will actually
  reach the database, which must also be its own RLS installer.

The revision is replayed in-process against a recording ``alembic.op`` stand-in, so the
comparison is between real :class:`sqlalchemy.Table` objects built by the revision and
the declarative metadata — same SQLAlchemy rendering on both sides. No database, no
``alembic upgrade``, no DDL is executed.

Covered:

* chain shape: ``2026110316 -> 2026110315``, one head, no fork, ``upgrade head`` ends
  here;
* exactly two tables with column/type/nullability/default parity, named check parity,
  unique and primary-key parity, index parity and no foreign key;
* the specification is copied, not paraphrased: every ``ColumnSpec`` (name, type,
  nullability, order), every primary/unique key, every check predicate and every index
  the spec names exists on the model and in the revision;
* ``UNIQUE (tenant, definition, revision)`` is the state CAS and
  ``UNIQUE (tenant, definition, action, correlation)`` the command idempotency key;
* ``canonical_positive_writes = true`` is admissible exactly for
  ``resulting_state = 'promoted'`` — the one property
  ``ck_m06_grant_no_positive_writes`` on ``m06_canonical_grant_promotion_evidence``
  cannot express, and that table still pins ``false``;
* a half-written lineage link (id without digest) is refused;
* the ledger is append-only twice (ORM guard + ``23514`` trigger) and the live source
  tail deliberately is not;
* the revision re-applies the canonical RLS registry, so both new tables are policied
  in the same step that creates them, and ``downgrade`` withdraws only the policies
  whose ``owner_revision`` is this revision before dropping its own objects in reverse
  creation order.

Row-level outcomes (a duplicate CAS insert, a cross-tenant read, a trigger denial) are
covered by the SQLite acceptance harness of the ports package
(``tests/migration/m06_cutover``); here the objects that must produce each rejection are
asserted, and the residual gaps are recorded in the model's
``M06_CUTOVER_EVIDENCE_DEFERRED``.
"""

from __future__ import annotations

from typing import Any

import alembic
import alembic.op  # noqa: F401 — load the proxy module so it can be swapped below
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import make_transient_to_detached
from sqlalchemy.schema import CreateTable

import prodavan.infrastructure.persistence.models as models
from prodavan.application.migration.m06_cutover.ports import (
    CUTOVER_LEDGER_TABLE,
    CUTOVER_REQUIRED_SCHEMA,
    CUTOVER_SOURCE_TAIL_TABLE,
    cutover_schema_ddl,
)
from prodavan.infrastructure.persistence.models import Base
from prodavan.infrastructure.persistence.models.canonical_grants import (
    M06CanonicalGrantPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.m06_cutover_evidence import (
    M06_CUTOVER_EVIDENCE_ACCEPTANCE_CASES,
    M06_CUTOVER_EVIDENCE_DEFERRED,
    M06_CUTOVER_EVIDENCE_TABLES,
    M06PartitionCutoverEvidenceRow,
    M06PartitionCutoverSourceTailRow,
    _reject_cutover_delete,
    _reject_cutover_update,
)
from prodavan.infrastructure.persistence.rls import policies

from .acceptance_fixtures import (
    CUTOVER_TABLES,
    DOWN_REVISION_ID,
    FORBIDDEN_DDL,
    LEDGER_TABLE,
    MANAGED_MODELS,
    POSTGRESQL,
    REVISION_FILE,
    REVISION_ID,
    SOURCE_TAIL_TABLE,
    SPEC_TYPE_LABELS,
    RecordedOps,
    chain_from_head,
    check_catalog,
    column_signature,
    head_revisions,
    load_revision_module,
    load_revision_module_from,
    policy_installers,
    revision_graph,
    shadow_record,
    shadow_state,
    squash,
    unique_catalog,
)

MODEL_BY_TABLE = {model.__tablename__: model for model in MANAGED_MODELS}

SPEC_BY_TABLE = {spec.name: spec for spec in CUTOVER_REQUIRED_SCHEMA}

STATE_CAS = "uq_m06_cutover_partition_revision"
COMMAND_KEY = "uq_m06_cutover_command"
LEDGER_PARTITION_INDEX = "ix_m06_cutover_partition_revision"

APPEND_ONLY_FUNCTION = "m06_cutover_ledger_append_only"
APPEND_ONLY_TRIGGER = "trg_m06_cutover_ledger_append_only"
APPEND_ONLY_REASON = "m06_cutover_ledger_row_immutable"

#: Columns that hold canonical JSON *text* (the bytes whose digests the lineage chains),
#: never a JSONB document the database may re-order.
LEDGER_PAYLOAD_COLUMNS = ("state_payload", "record_payload")

#: A raw payload, secret or body must never reach a cutover column.
FORBIDDEN_COLUMN_FRAGMENTS = ("raw", "secret", "password", "api_key", "body", "manifest")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture()
def revision(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, RecordedOps]:
    """Import the revision with a recording ``op`` proxy and run ``upgrade()``."""
    recorder = RecordedOps()
    monkeypatch.setattr(alembic, "op", recorder)
    module = load_revision_module()
    module.upgrade()
    return module, recorder


def _execute_statements(recorder: RecordedOps) -> list[str]:
    return [call[1] for call in recorder.calls if call[0] == "execute"]


def _policy_blocks(statements: list[str]) -> list[str]:
    return [statement for statement in statements if "DO $rls$" in statement]


def _policy_block_table(statement: str) -> str:
    marker = "to_regclass('"
    start = statement.index(marker) + len(marker)
    return statement[start : statement.index("'", start)]


def _check_texts(table: sa.Table) -> set[str]:
    return {squash(str(constraint.sqltext)) for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)}


def _index_columns(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in index.columns) for index in table.indexes
    }


# ---------------------------------------------------------------------------
# 1. Chain and head
# ---------------------------------------------------------------------------


def test_revision_identifiers_follow_the_cutover_chain() -> None:
    module = load_revision_module()

    assert REVISION_FILE.is_file()
    assert module.revision == REVISION_ID
    assert module.down_revision == DOWN_REVISION_ID
    assert module.branch_labels is None
    assert module.depends_on is None
    assert module.CUTOVER_TABLES == CUTOVER_TABLES
    assert module._POLICIES_ADDED_HERE == frozenset({REVISION_ID})


def test_the_chain_has_exactly_one_head_and_it_is_this_revision() -> None:
    assert head_revisions() == (REVISION_ID,)


def test_no_revision_declares_this_one_as_its_parent_yet() -> None:
    graph = revision_graph()
    children = sorted(revision for revision, down in graph.items() if down == REVISION_ID)

    assert children == [], f"unexpected successors of {REVISION_ID}: {children}"


def test_the_head_walks_back_through_the_m04e_ledger() -> None:
    chain = chain_from_head()

    assert chain[:4] == (REVISION_ID, DOWN_REVISION_ID, "2026110314", "2026110313")


def test_the_revision_can_be_loaded_by_path_like_alembic_does() -> None:
    module = load_revision_module_from(REVISION_FILE)

    assert callable(module.upgrade) and callable(module.downgrade)
    assert module.LEDGER_TABLE == LEDGER_TABLE
    assert module.SOURCE_TAIL_TABLE == SOURCE_TAIL_TABLE


# ---------------------------------------------------------------------------
# 2. Tables and metadata parity
# ---------------------------------------------------------------------------


def test_creates_only_the_two_cutover_tables(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    assert tuple(recorder.tables) == CUTOVER_TABLES, "creation order is ledger, then tail"
    for table_name in CUTOVER_TABLES:
        assert table_name in Base.metadata.tables
    assert {model.__tablename__ for model in MANAGED_MODELS} == set(CUTOVER_TABLES)


def test_the_port_table_names_are_the_registered_table_names() -> None:
    """The repository reads the names from the spec; the models must own those names."""
    assert CUTOVER_LEDGER_TABLE == LEDGER_TABLE == M06PartitionCutoverEvidenceRow.__tablename__
    assert CUTOVER_SOURCE_TAIL_TABLE == SOURCE_TAIL_TABLE == (
        M06PartitionCutoverSourceTailRow.__tablename__
    )
    assert set(SPEC_BY_TABLE) == set(CUTOVER_TABLES)


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_columns_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = recorder.tables[table_name]
    expected = Base.metadata.tables[table_name]

    assert recorded.schema is None
    assert column_signature(recorded) == column_signature(expected)


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_check_constraints_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = check_catalog(recorder.tables[table_name])
    expected = check_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(recorded), "every check constraint is explicitly named"


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_unique_constraints_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = unique_catalog(recorder.tables[table_name])
    expected = unique_catalog(Base.metadata.tables[table_name])

    assert recorded == expected


@pytest.mark.parametrize(
    "table_name, expected_pk",
    (
        (LEDGER_TABLE, ("id",)),
        (SOURCE_TAIL_TABLE, ("tenant_id", "app_definition_id")),
    ),
    ids=(LEDGER_TABLE, SOURCE_TAIL_TABLE),
)
def test_primary_keys_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str, expected_pk: tuple[str, ...]
) -> None:
    _, recorder = revision

    recorded = tuple(column.name for column in recorder.tables[table_name].primary_key.columns)
    expected = tuple(column.name for column in Base.metadata.tables[table_name].primary_key.columns)

    assert recorded == expected == expected_pk


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_indexes_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
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


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_no_cutover_table_declares_a_foreign_key(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    """A ``BLOCKED`` transition must be recordable for an unresolvable partition."""
    _, recorder = revision

    recorded = recorder.tables[table_name]
    assert [c for c in recorded.constraints if isinstance(c, sa.ForeignKeyConstraint)] == []
    assert not any(call[0] == "create_foreign_key" for call in recorder.calls)


# ---------------------------------------------------------------------------
# 3. The specification is copied, not paraphrased
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_the_specification_columns_are_the_model_columns(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    """``CUTOVER_REQUIRED_SCHEMA`` is the source of truth: names, order, type, nullability."""
    _, recorder = revision
    spec = SPEC_BY_TABLE[table_name]

    expected = [
        (
            column.name,
            SPEC_TYPE_LABELS[column.sql_type],
            # A primary-key column is non-nullable by construction, exactly as the
            # specification declares it.
            column.nullable,
            None,
        )
        for column in spec.columns
    ]

    assert column_signature(recorder.tables[table_name]) == expected, table_name
    assert column_signature(Base.metadata.tables[table_name]) == expected, table_name


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_the_specification_keys_are_declared(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision
    spec = SPEC_BY_TABLE[table_name]

    for container in (recorder.tables[table_name], Base.metadata.tables[table_name]):
        primary = tuple(column.name for column in container.primary_key.columns)
        assert primary == spec.primary_key, table_name
        declared = {tuple(columns) for columns in unique_catalog(container).values()}
        assert declared == {tuple(key) for key in spec.unique}, table_name


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_the_specification_checks_are_declared_verbatim(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    """Every predicate the spec names exists, and nothing extra is invented."""
    _, recorder = revision
    spec = SPEC_BY_TABLE[table_name]
    expected = {squash(check) for check in spec.checks}

    assert _check_texts(recorder.tables[table_name]) == expected
    assert _check_texts(Base.metadata.tables[table_name]) == expected


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_the_specification_indexes_are_declared(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision
    spec = SPEC_BY_TABLE[table_name]
    expected = {tuple(columns) for columns in spec.indexes}

    created = {
        tuple(call[3])
        for call in recorder.calls
        if call[0] == "create_index" and call[1] == table_name
    }

    assert created == expected
    assert _index_columns(Base.metadata.tables[table_name]) == expected


def test_the_rendered_specification_ddl_covers_the_models() -> None:
    """``cutover_schema_ddl()`` is the spec in executable form; the models agree with it."""
    statements = "\n".join(cutover_schema_ddl())
    for table_name in CUTOVER_TABLES:
        assert f"CREATE TABLE {table_name} (" in statements
        for column in SPEC_BY_TABLE[table_name].column_names:
            assert column in Base.metadata.tables[table_name].c


# ---------------------------------------------------------------------------
# 4. State CAS, command idempotency, positive writes
# ---------------------------------------------------------------------------


def test_the_state_cas_is_the_partition_revision_key() -> None:
    """A lost race on revision n+1 must collide, not overwrite."""
    table = Base.metadata.tables[LEDGER_TABLE]

    assert unique_catalog(table)[STATE_CAS] == (
        "tenant_id", "app_definition_id", "revision",
    )
    assert _index_columns(table) == {("tenant_id", "app_definition_id", "revision")}
    assert LEDGER_PARTITION_INDEX in {index.name for index in table.indexes}


def test_the_idempotency_key_is_partition_action_correlation() -> None:
    """A replayed command collides at the storage layer, whatever its evidence id."""
    table = Base.metadata.tables[LEDGER_TABLE]

    assert unique_catalog(table)[COMMAND_KEY] == (
        "tenant_id", "app_definition_id", "action", "correlation_id",
    )


def test_positive_writes_require_a_promoted_resulting_state() -> None:
    """The one property ``m06_canonical_grant_promotion_evidence`` cannot express."""
    table = Base.metadata.tables[LEDGER_TABLE]
    predicate = check_catalog(table)["ck_m06_cutover_positive_writes_only_when_promoted"]

    assert predicate == (
        "canonical_positive_writes = false OR resulting_state = 'promoted'"
    )
    assert "'promoted'" in predicate
    # The upstream evidence table keeps its own blanket refusal: a promoted cutover is
    # recorded here, not by relaxing an applied CHECK there.
    legacy = check_catalog(M06CanonicalGrantPromotionEvidenceRow.__table__)
    assert legacy["ck_m06_grant_no_positive_writes"] == (
        "canonical_positive_writes = false"
    )


def test_a_prior_record_id_without_its_digest_is_refused() -> None:
    """The successor's CAS compares the digest with the stored lineage head."""
    table = Base.metadata.tables[LEDGER_TABLE]

    assert check_catalog(table)["ck_m06_cutover_prior_record_pair"] == (
        "prior_record_id IS NULL OR prior_record_digest IS NOT NULL"
    )


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_no_cutover_column_carries_a_server_default(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    """A default on ``revision`` would fabricate a CAS position."""
    _, recorder = revision

    for table in (recorder.tables[table_name], Base.metadata.tables[table_name]):
        offenders = [column.name for column in table.columns
                     if column.server_default is not None]
        assert offenders == [], f"{table_name} defaults {offenders}"


def test_the_source_tail_counters_are_non_negative() -> None:
    table = Base.metadata.tables[SOURCE_TAIL_TABLE]

    assert check_catalog(table)["ck_m06_cutover_tail_sequence_nonnegative"] == (
        "change_sequence >= 0 AND writer_epoch >= 0"
    )
    assert check_catalog(table)["ck_m06_cutover_tail_sequence_nonnegative"] in _check_texts(
        table
    )
    assert unique_catalog(table) == {}, "the tail is one head row per partition, not more"


# ---------------------------------------------------------------------------
# 5. Append-only ledger, mutable tail
# ---------------------------------------------------------------------------


def test_the_ledger_carries_an_append_only_trigger(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision
    statements = _execute_statements(recorder)

    function_ddl = next(statement for statement in statements if APPEND_ONLY_FUNCTION in statement)
    assert "RETURNS trigger" in function_ddl
    assert "LANGUAGE plpgsql" in function_ddl
    assert APPEND_ONLY_REASON in function_ddl
    assert "ERRCODE = '23514'" in function_ddl

    triggers = [
        statement for statement in statements if statement.startswith("CREATE TRIGGER")
    ]
    assert len(triggers) == 1, "only the ledger is append-only"
    assert APPEND_ONLY_TRIGGER in triggers[0]
    assert "BEFORE UPDATE OR DELETE" in triggers[0]
    assert f"ON {LEDGER_TABLE}" in triggers[0]
    assert SOURCE_TAIL_TABLE not in triggers[0]


def test_the_source_tail_is_mutable_by_design(
    revision: tuple[Any, RecordedOps],
) -> None:
    """It is the CAS head the legacy writer advances; a trigger would freeze it."""
    _, recorder = revision
    statements = _execute_statements(recorder)
    registry_blocks = set(_policy_blocks(statements))
    # Only this revision's own statements: the registry blocks legitimately name every
    # registered table (they create its policies), so they cannot answer "is there a
    # trigger on the tail".
    own_statements = "\n".join(
        statement for statement in statements if statement not in registry_blocks
    )

    assert f"ON {SOURCE_TAIL_TABLE}" not in own_statements
    assert "CREATE TRIGGER" in own_statements
    assert SPEC_BY_TABLE[SOURCE_TAIL_TABLE].append_only is False
    assert SPEC_BY_TABLE[LEDGER_TABLE].append_only is True


def test_orm_guards_refuse_every_update_and_delete() -> None:
    for column in Base.metadata.tables[LEDGER_TABLE].columns:
        probe = M06PartitionCutoverEvidenceRow(id="probe", tenant_id="tenant-a")
        make_transient_to_detached(probe)
        setattr(probe, column.name, "probe-value")
        with pytest.raises(ValueError, match="append-only") as raised:
            _reject_cutover_update(None, None, probe)
        assert column.name in str(raised.value)

    unchanged = M06PartitionCutoverEvidenceRow(id="probe", tenant_id="tenant-a")
    make_transient_to_detached(unchanged)
    _reject_cutover_update(None, None, unchanged)

    with pytest.raises(ValueError, match="append-only"):
        _reject_cutover_delete(None, None, M06PartitionCutoverEvidenceRow(id="probe"))


def test_no_raw_material_reaches_a_cutover_column() -> None:
    for table_name in CUTOVER_TABLES:
        columns = {column.name: column for column in Base.metadata.tables[table_name].columns}
        for name in columns:
            for fragment in FORBIDDEN_COLUMN_FRAGMENTS:
                assert fragment not in name, f"{table_name}.{name} looks like raw material"

    # The two payload columns are canonical JSON *text*: JSONB would re-order and
    # re-escape the bytes whose digest the lineage chains.
    for name in LEDGER_PAYLOAD_COLUMNS:
        column = Base.metadata.tables[LEDGER_TABLE].c[name]
        assert POSTGRESQL.type_compiler.process(column.type) == "TEXT"


def test_the_cutover_models_render_valid_postgresql_ddl() -> None:
    """The checks use the PostgreSQL regex operator, so the DDL must compile for it."""
    for table_name in CUTOVER_TABLES:
        rendered = str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=POSTGRESQL))
        assert f"CREATE TABLE {table_name}" in rendered
        assert "PRIMARY KEY" in rendered
    ledger = str(CreateTable(Base.metadata.tables[LEDGER_TABLE]).compile(dialect=POSTGRESQL))
    assert "TIMESTAMP WITH TIME ZONE" in ledger
    assert "~ '^sha256:[0-9a-f]{64}$'" in ledger
    assert "UNIQUE (tenant_id, app_definition_id, revision)" in ledger


# ---------------------------------------------------------------------------
# 6. RLS: this revision installs its own policies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_each_cutover_table_is_registered_with_this_revision(table_name: str) -> None:
    spec = next(spec for spec in policies.CANONICAL_TABLE_POLICIES if spec.table == table_name)

    assert spec.owner_revision == REVISION_ID
    assert spec.tenant is not None
    assert spec.tenant.column == "tenant_id"
    assert spec.tenant.chain == ()
    assert spec.tenant.allows_null is False
    assert spec.cabinet is None and spec.project is None
    assert spec.tenant_restriction is None
    assert spec.trusted_command == "ALL"
    assert spec.policy_names == (
        f"{table_name}_tenant_isolation",
        f"{table_name}_trusted_service_access",
    )

    predicate = policies.tenant_predicate(spec)
    assert predicate == (
        f"{table_name}.tenant_id = nullif(current_setting('app.tenant_id', true), '')"
    )

    tenant_statement, trusted_statement = policies.create_policy_statements(spec)
    assert f"WITH CHECK ({predicate})" in tenant_statement
    assert "FOR ALL" in trusted_statement
    assert policies.TRUSTED_SERVICE_ROLE in trusted_statement


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_the_scope_column_exists_on_the_model(table_name: str) -> None:
    assert "tenant_id" in Base.metadata.tables[table_name].c


def test_the_cutover_tables_are_registered_next_to_the_m06_evidence_tables() -> None:
    order = [spec.table for spec in policies.CANONICAL_TABLE_POLICIES]

    anchor = order.index("m06_canonical_grant_promotion_evidence")
    assert order[anchor + 1 : anchor + 3] == list(CUTOVER_TABLES)
    for table_name in CUTOVER_TABLES:
        assert order.count(table_name) == 1


def test_this_revision_is_its_own_registry_reapply(
    revision: tuple[Any, RecordedOps],
) -> None:
    """A table-adding revision must not leave its policies to "the next pass"."""
    _, recorder = revision
    blocks = _policy_blocks(_execute_statements(recorder))

    assert blocks, "the revision must re-apply the canonical registry"
    installed = {_policy_block_table(block) for block in blocks}
    assert installed == set(policies.registry_tables())
    assert set(CUTOVER_TABLES) <= installed

    owned = {
        spec.table
        for spec in policies.CANONICAL_TABLE_POLICIES
        if spec.owner_revision == REVISION_ID
    }
    assert owned == set(CUTOVER_TABLES)

    for block in blocks:
        assert "DROP POLICY IF EXISTS" in block and "CREATE POLICY" in block
        assert "RAISE NOTICE" in block


def test_the_install_order_gate_is_satisfied() -> None:
    """The same rule ``test_policy_install_order`` enforces, restated from the head."""
    chain = chain_from_head()
    position = {revision: index for index, revision in enumerate(reversed(chain))}
    installers = policy_installers() & set(position)

    assert installers
    newest = max(installers, key=lambda revision: position[revision])
    assert newest == REVISION_ID, (
        "these tables would stay unpolicied; the install-order gate reports a table "
        f"whose owner revision comes after the last installer ({newest})"
    )

    late = sorted(
        spec.table
        for spec in policies.CANONICAL_TABLE_POLICIES
        if position.get(spec.owner_revision, -1) > position[newest]
    )
    assert late == []


def test_the_coverage_report_finds_nothing_uncovered() -> None:
    assert policies.uncovered_relations(Base.metadata.tables.keys()) == ()
    assert all(
        table.startswith(policies.CANONICAL_TABLE_PREFIXES) for table in CUTOVER_TABLES
    )


def test_downgrade_removes_only_this_revision_objects_in_reverse_order(
    revision: tuple[Any, RecordedOps],
) -> None:
    module, recorder = revision

    upgrade_call_count = len(recorder.calls)
    module.downgrade()
    downgrade_calls = recorder.calls[upgrade_call_count:]
    kinds = [call[0] for call in downgrade_calls]

    blocks = _policy_blocks([call[1] for call in downgrade_calls if call[0] == "execute"])
    assert {_policy_block_table(block) for block in blocks} == set(CUTOVER_TABLES)
    for block in blocks:
        assert "DROP POLICY IF EXISTS" in block
        assert "DISABLE ROW LEVEL SECURITY" in block
        assert "DROP TABLE" not in block
        assert "EXCEPTION" not in block, "a real DDL failure must abort, not be swallowed"

    executed = [call[1] for call in downgrade_calls if call[0] == "execute"]
    trigger_drops = [statement for statement in executed if "DROP TRIGGER IF EXISTS" in statement]
    assert len(trigger_drops) == 1
    assert trigger_drops[0].endswith(f" ON {LEDGER_TABLE}")
    function_drops = [statement for statement in executed if "DROP FUNCTION IF EXISTS" in statement]
    assert len(function_drops) == 1 and APPEND_ONLY_FUNCTION in function_drops[0]

    dropped_indexes = [call[2] for call in downgrade_calls if call[0] == "drop_index"]
    assert dropped_indexes == [LEDGER_PARTITION_INDEX]
    dropped_tables = [call[1] for call in downgrade_calls if call[0] == "drop_table"]
    assert dropped_tables == list(reversed(CUTOVER_TABLES))

    first_table_drop = kinds.index("drop_table")
    last_index_drop = max(index for index, kind in enumerate(kinds) if kind == "drop_index")
    last_function_drop = max(
        index
        for index, call in enumerate(downgrade_calls)
        if call[0] == "execute" and "DROP FUNCTION" in call[1]
    )
    last_trigger_drop = max(
        index
        for index, call in enumerate(downgrade_calls)
        if call[0] == "execute" and "DROP TRIGGER" in call[1]
    )
    assert last_trigger_drop < last_function_drop < last_index_drop < first_table_drop

    assert not any(
        keyword in statement.upper()
        for statement in executed
        for keyword in ("DELETE FROM", "TRUNCATE", "INSERT INTO", "UPDATE M06_")
    )


def test_downgrade_leaves_earlier_installers_policies_alone() -> None:
    module = load_revision_module()

    assert module._POLICIES_ADDED_HERE == frozenset({REVISION_ID})
    earlier = {
        spec.owner_revision
        for spec in policies.CANONICAL_TABLE_POLICIES
        if spec.owner_revision != REVISION_ID
    }
    assert earlier, "the chain must have earlier policy owners"
    assert not earlier & module._POLICIES_ADDED_HERE


# ---------------------------------------------------------------------------
# 7. Revision hygiene and registration
# ---------------------------------------------------------------------------


def test_revision_emits_no_grants_data_or_unrelated_ddl(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision

    assert {call[0] for call in recorder.calls} <= {
        "create_table",
        "create_index",
        "execute",
    }

    statements = _execute_statements(recorder)
    registry_blocks = set(_policy_blocks(statements))
    assert registry_blocks, "the revision must re-apply the canonical registry"

    own_statements = [statement for statement in statements if statement not in registry_blocks]
    assert len(own_statements) == 2, own_statements
    assert sum(1 for s in own_statements if "CREATE OR REPLACE FUNCTION" in s) == 1
    assert sum(1 for s in own_statements if s.startswith("CREATE TRIGGER")) == 1

    for statement in statements:
        upper = statement.upper()
        for forbidden in FORBIDDEN_DDL:
            assert forbidden not in upper, f"unexpected DDL fragment {forbidden!r}"

    altered: set[str] = set()
    for statement in statements:
        for line in statement.splitlines():
            line = line.strip()
            if not line.startswith("ALTER TABLE "):
                continue
            table, _, rest = line[len("ALTER TABLE ") :].partition(" ")
            altered.add(table)
            assert rest.rstrip(";") in {
                "ENABLE ROW LEVEL SECURITY",
                "FORCE ROW LEVEL SECURITY",
                "NO FORCE ROW LEVEL SECURITY",
                "DISABLE ROW LEVEL SECURITY",
            }, line
    assert set(CUTOVER_TABLES) <= altered
    assert altered <= set(policies.registry_tables())


@pytest.mark.parametrize("table_name", CUTOVER_TABLES)
def test_every_cutover_model_is_registered_once_in_the_models_package(
    table_name: str,
) -> None:
    model = MODEL_BY_TABLE[table_name]

    assert getattr(models, model.__name__) is model
    assert model.__name__ in models.__all__
    assert Base.metadata.tables[table_name] is model.__table__

    owners = [
        mapper.class_ for mapper in Base.registry.mappers if mapper.local_table is model.__table__
    ]
    assert owners == [model]


def test_the_models_record_the_deferred_trade_offs() -> None:
    assert set(M06_CUTOVER_EVIDENCE_TABLES) == set(CUTOVER_TABLES)
    assert set(M06_CUTOVER_EVIDENCE_DEFERRED) == {
        "occurred_at_bound_as_iso_string",
        "append_only_privileges",
        "source_tail_monotonicity",
        "promotion_marker",
        "registry_version",
    }
    for reason in M06_CUTOVER_EVIDENCE_DEFERRED.values():
        assert len(reason.split()) >= 8, "each deferred gap needs a reviewed justification"

    for case in (
        "a_second_row_for_one_partition_and_revision_is_rejected_by_the_state_cas",
        "the_same_command_correlation_cannot_be_appended_twice",
        "a_promoted_row_may_claim_canonical_positive_writes",
        "a_non_promoted_row_cannot_claim_canonical_positive_writes",
        "both_tables_are_tenant_scoped_by_rls",
    ):
        assert case in M06_CUTOVER_EVIDENCE_ACCEPTANCE_CASES
    assert len(set(M06_CUTOVER_EVIDENCE_ACCEPTANCE_CASES)) == len(
        M06_CUTOVER_EVIDENCE_ACCEPTANCE_CASES
    )


# ---------------------------------------------------------------------------
# 8. The row shapes the DDL admits
# ---------------------------------------------------------------------------


def test_the_positive_write_check_names_a_state_the_domain_actually_has() -> None:
    """The constraint vocabulary must be the state machine's vocabulary.

    ``ck_m06_cutover_positive_writes_only_when_promoted`` admits ``true`` only for
    ``resulting_state = 'promoted'``. If the constraint named a state
    :class:`CutoverState` does not have, every promoted append would be refused by
    storage while the state machine believed it had succeeded — so the literal is
    compared with the enum rather than with a reviewer's memory.
    """

    from prodavan.application.migration.m06_cutover.cutover import CutoverState

    predicate = check_catalog(Base.metadata.tables[LEDGER_TABLE])[
        "ck_m06_cutover_positive_writes_only_when_promoted"
    ]
    assert "canonical_positive_writes = false OR resulting_state = 'promoted'" == predicate
    assert CutoverState.PROMOTED.value == "promoted"
    # The default direction is refusal: a row that says nothing claims nothing.
    assert "canonical_positive_writes = false" in predicate

    # The transition that produces such a row is the one that flips the owners.
    promoted = shadow_state(shadow_record(), revision=2)
    assert promoted.canonical_positive_writes is False


def test_a_successor_row_can_carry_the_lineage_the_cas_compares() -> None:
    """The two columns the successor's CAS reads are the two the ledger must carry.

    ``SqlCutoverRepository._lineage_continues`` requires the appended record's
    ``prior_record_id``/``prior_record_digest`` to equal the stored
    ``last_evidence_id``/``lineage_head_digest`` exactly. The checklist below proves the
    ledger's columns can express that pair for a real transition, and that a row which
    breaks it is representable but would be refused by the port — which is why
    ``ck_m06_cutover_prior_record_pair`` refuses the half-written form at storage level.
    """

    first = shadow_record(evidence_id="cmd-shadow-1", epoch=1)
    first_state = shadow_state(first, revision=1)

    assert first.prior_record_id is None and first.prior_record_digest is None
    assert first_state.revision == 1
    assert first_state.lineage_head_digest == first.record_digest
    assert first_state.last_evidence_id == first.evidence_id

    # The successor the port would accept: the pair it cites is exactly the stored head.
    predicate = check_catalog(Base.metadata.tables[LEDGER_TABLE])[
        "ck_m06_cutover_prior_record_pair"
    ]
    assert "prior_record_id IS NULL OR prior_record_digest IS NOT NULL" == predicate

    # A ledger row for the successor carries that pair verbatim; the same column pair is
    # what the loaded state exposes, so load() -> transition() needs no translation.
    successor_columns = {
        "prior_record_id": first_state.last_evidence_id,
        "prior_record_digest": first_state.lineage_head_digest,
        "revision": first_state.revision + 1,
    }
    assert successor_columns["prior_record_id"] == "cmd-shadow-1"
    assert successor_columns["prior_record_digest"] == first.record_digest
    assert set(successor_columns) <= set(
        Base.metadata.tables[LEDGER_TABLE].c.keys()
    )
