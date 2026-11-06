"""MIG-DDL-M04E-LEDGER-01 contract for Alembic revision ``2026110315``.

Static acceptance artifact, authored during the verification freeze and intentionally
**not executed** as part of this task.

The test never connects to a database and never runs ``alembic upgrade``. It replaces
the ``alembic.op`` proxy with a recorder, executes the revision module's
``upgrade()``/``downgrade()`` in-process, and compares the emitted objects with the
SQLAlchemy metadata that is the schema source of truth
(``apps/api/src/prodavan/infrastructure/persistence/models/m04e_ledger.py``).

What it proves while reasoning over code:

* the revision identifiers keep the chain ``2026110315 -> 2026110314``, the graph has
  exactly one head, that head is this revision, and nothing forks it;
* exactly four tables are created — ``m04e_definition_version_mappings``,
  ``..._quarantine``, ``..._checkpoints`` and ``..._audit`` — with column, type,
  nullability and server-default parity, named check-constraint parity, unique and
  primary-key parity and index parity against the models;
* every column that ``EVIDENCE_STORE_REQUIRED_DDL`` enumerates exists on the table the
  entry names, and the two prose entries carry the columns their gap text requires —
  i.e. the four objects the ``EvidenceStore`` port asked for are the four objects
  authored here, with no invented or missing field;
* the per-source key ``(tenant_id, definition_id, source_kind, source_id,
  source_schema_version)`` is unique, so a second mapping row for an already-mapped
  source identity cannot exist;
* ``status`` admits ``accepted`` (gap ``m04e_promotion_accepted_outcome_unrepresentable``
  is closed for new rows) while the older promotion-evidence table keeps its two-state
  limit untouched, and no outcome column carries a server default;
* a changed-digest observation carries exactly one of the two observed digests, and the
  observation key includes both — declared ``NULLS NOT DISTINCT`` so a repeated artifact
  observation still collides when the source digest is NULL;
* the cursor is one head row per ``(tenant_id, definition_id, phase)`` with a globally
  unique ``checkpoint_token``, a non-negative sequence, and completion reachable only
  in the ``version`` phase;
* the audit stream is closed to two event kinds and its counters must balance;
* append-only is enforced twice for the three immutable ledgers — the ORM
  ``before_update``/``before_delete`` guards (exercised below on transient state) and a
  database trigger that raises ``23514`` for UPDATE and DELETE regardless of role — and
  the checkpoint table gets a monotonicity trigger that refuses a sequence regression, a
  phase rewind, a lineage rebind, an un-completion and token reuse for direct SQL too;
* the revision is its **own** RLS installer: it creates the four tables and then walks
  the canonical registry, so the newest policy installer is ``2026110315`` and the
  install-order gate cannot report these tables as late;
* ``downgrade()`` withdraws only the four policies whose ``owner_revision`` is this
  revision, then drops the four triggers, the two functions, the ten indexes and the four
  tables in reverse creation order, and never touches data.

Row-level outcomes (a duplicate source key insert, a NULLS-NOT-DISTINCT collision, a
cross-tenant read, an UPDATE denied by the trigger) need a live PostgreSQL database and
stay review inputs until the freeze is lifted; the named objects that must produce each
rejection are asserted here, and the residual gaps are recorded in the model's
``M04E_LEDGER_DEFERRED``.
"""

from __future__ import annotations

from typing import Any

import alembic
import alembic.op  # noqa: F401 — load the proxy module so it can be swapped below
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import make_transient_to_detached
from sqlalchemy.schema import CreateTable

import prodavan.infrastructure.persistence.models as models
from prodavan.application.migration.m04e_definition_version.backfill import BackfillOutcome
from prodavan.infrastructure.persistence.models import Base
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04E_LEDGER_ACCEPTANCE_CASES,
    M04E_LEDGER_DEFERRED,
    M04E_LEDGER_TABLES,
    M04EDefinitionVersionAuditRow,
    M04EDefinitionVersionCheckpointRow,
    M04EDefinitionVersionMappingRow,
    M04EDefinitionVersionQuarantineRow,
    _reject_audit_delete,
    _reject_audit_update,
    _reject_checkpoint_regression,
    _reject_mapping_delete,
    _reject_mapping_update,
    _reject_quarantine_delete,
    _reject_quarantine_update,
)
from prodavan.infrastructure.persistence.rls import policies
from tests.migration.m04e_ledger.acceptance_fixtures import (
    AUDIT_TABLE,
    BOUNDARY,
    CHECKPOINT_TABLE,
    DOWN_REVISION_ID,
    FORBIDDEN_COLUMN_NAMES,
    FORBIDDEN_DDL,
    LEDGER_TABLES,
    MANAGED_MODELS,
    MAPPING_TABLE,
    NEGATIVE_CASES,
    PROSE_SPEC_COLUMNS,
    QUARANTINE_TABLE,
    REVISION_FILE,
    REVISION_ID,
    ROW_FACTORIES,
    RecordedOps,
    SPEC_TABLES,
    SUCCESSOR_REVISION_ID,
    audit_row,
    chain_from_head,
    checkpoint_row,
    head_revisions,
    load_revision_module,
    load_revision_module_from,
    mapping_row,
    policy_installers,
    quarantine_row,
    revision_graph,
    spec_columns,
)


MODEL_BY_TABLE = {model.__tablename__: model for model in MANAGED_MODELS}

MAPPING_INDEXES = (
    "ix_m04e_mapping_partition",
    "ix_m04e_mapping_cursor",
    "ix_m04e_mapping_source_lookup",
)
QUARANTINE_INDEXES = (
    "ix_m04e_quarantine_partition",
    "ix_m04e_quarantine_source_lookup",
)
CHECKPOINT_INDEXES = (
    "ix_m04e_checkpoint_snapshot",
    "ix_m04e_checkpoint_completion",
)
AUDIT_INDEXES = (
    "ix_m04e_audit_partition_time",
    "ix_m04e_audit_checkpoint",
    "ix_m04e_audit_correlation",
)

INDEXES_BY_TABLE = {
    MAPPING_TABLE: MAPPING_INDEXES,
    QUARANTINE_TABLE: QUARANTINE_INDEXES,
    CHECKPOINT_TABLE: CHECKPOINT_INDEXES,
    AUDIT_TABLE: AUDIT_INDEXES,
}

APPEND_ONLY_FUNCTION = "m04e_ledger_append_only"
CHECKPOINT_GUARD_FUNCTION = "m04e_checkpoint_advance_guard"
CHECKPOINT_GUARD_TRIGGER = "trg_m04e_checkpoint_monotonic"

SOURCE_KEY_CONSTRAINT = "uq_m04e_mapping_source"
OBSERVATION_KEY_CONSTRAINT = "uq_m04e_quarantine_observation"
MAPPING_STATUS_CHECK = "ck_m04e_mapping_status"
LEGACY_OUTCOME_CHECK = "ck_m04e_promotion_outcome"

_DIALECT = postgresql.dialect()


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


def _unique_constraint(table: sa.Table, name: str) -> sa.UniqueConstraint:
    return next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint) and constraint.name == name
    )


def _execute_statements(recorder: RecordedOps) -> list[str]:
    return [call[1] for call in recorder.calls if call[0] == "execute"]


def _policy_blocks(statements: list[str]) -> list[str]:
    return [statement for statement in statements if "DO $rls$" in statement]


def _policy_block_table(statement: str) -> str:
    marker = "to_regclass('"
    start = statement.index(marker) + len(marker)
    return statement[start : statement.index("'", start)]


# ---------------------------------------------------------------------------
# 1. Chain and head
# ---------------------------------------------------------------------------


def test_revision_identifiers_follow_the_wave_chain() -> None:
    # Identifiers are plain module attributes: the module is loaded with the real
    # ``op`` proxy, which is never called at import time.
    module = load_revision_module()

    assert REVISION_FILE.is_file()
    assert module.revision == REVISION_ID
    assert module.down_revision == DOWN_REVISION_ID
    assert module.branch_labels is None
    assert module.depends_on is None
    assert module.LEDGER_TABLES == LEDGER_TABLES
    assert module._POLICIES_ADDED_HERE == frozenset({REVISION_ID})


def test_the_chain_has_exactly_one_head_and_it_is_a_successor_of_this_revision() -> None:
    """Was "the head is this revision": that stopped being true when ``2026110316``
    landed, and again when ``2026110317`` (the ``auth_sessions`` store) succeeded *it*.

    The invariant that still matters is that the tree has one head, this revision is on its
    chain, and its only successor declares it as ``down_revision`` (a second head or a fork
    would make ``upgrade head`` ambiguous). The successor is pinned; the head is not, because
    every later revision legitimately extends the chain — that is what a chain is for.
    """

    heads = head_revisions()
    assert len(heads) == 1, f"expected exactly one head, got {heads}"
    assert revision_graph()[SUCCESSOR_REVISION_ID] == REVISION_ID
    assert REVISION_ID in chain_from_head()


def test_exactly_one_revision_declares_this_one_as_its_parent() -> None:
    """Was "no revision declares this one as its parent yet": ``2026110316`` now does.

    A *second* successor would fork the chain, so exactly one is required.
    """
    graph = revision_graph()
    children = sorted(revision for revision, down in graph.items() if down == REVISION_ID)

    assert children == [SUCCESSOR_REVISION_ID], f"unexpected successors of {REVISION_ID}: {children}"


def test_the_head_walks_back_through_the_scope_evidence_and_rls_passes() -> None:
    """Walking the chain from the head must reach this revision, in the reviewed order.

    Expressed as "everything below this revision is unchanged" rather than "this revision is
    at position 1": a later revision (``2026110317`` is one) extends the chain above it and
    must not make this assertion fail for the wrong reason.
    """

    chain = chain_from_head()
    position = chain.index(REVISION_ID)

    assert chain[position : position + 4] == (
        REVISION_ID,
        DOWN_REVISION_ID,
        "2026110313",
        "2026110312",
    )


# ---------------------------------------------------------------------------
# 2. Tables and metadata parity
# ---------------------------------------------------------------------------


def test_creates_only_the_four_ledger_tables(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    assert set(recorder.tables) == set(LEDGER_TABLES)
    for table_name in LEDGER_TABLES:
        assert table_name in Base.metadata.tables
    assert {model.__tablename__ for model in MANAGED_MODELS} == set(LEDGER_TABLES)


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
def test_columns_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = recorder.tables[table_name]
    expected = Base.metadata.tables[table_name]

    assert recorded.schema is None
    assert _column_signature(recorded) == _column_signature(expected)


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
def test_check_constraints_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = _check_catalog(recorder.tables[table_name])
    expected = _check_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(recorded), "every check constraint is explicitly named"


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
def test_unique_constraints_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = _unique_catalog(recorder.tables[table_name])
    expected = _unique_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(recorded), "every unique constraint is explicitly named"


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
def test_primary_keys_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = tuple(column.name for column in recorder.tables[table_name].primary_key.columns)
    expected = tuple(column.name for column in Base.metadata.tables[table_name].primary_key.columns)

    assert recorded == expected == ("id",)


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
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


@pytest.mark.parametrize(
    "table_name, expected_indexes",
    sorted(INDEXES_BY_TABLE.items()),
    ids=sorted(INDEXES_BY_TABLE),
)
def test_partition_and_cursor_indexes_are_the_documented_ones(
    revision: tuple[Any, RecordedOps], table_name: str, expected_indexes: tuple[str, ...]
) -> None:
    _, recorder = revision

    created = {
        call[2] for call in recorder.calls if call[0] == "create_index" and call[1] == table_name
    }

    assert created == set(expected_indexes)


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
def test_no_ledger_declares_a_foreign_key(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    """Deliberate: a blocked page must be recordable for an unresolvable definition."""
    _, recorder = revision

    recorded = recorder.tables[table_name]
    assert [c for c in recorded.constraints if isinstance(c, sa.ForeignKeyConstraint)] == []
    assert not any(c[0] == "create_foreign_key" for c in recorder.calls)


# ---------------------------------------------------------------------------
# 3. The specification the port refused over
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", SPEC_TABLES)
def test_every_column_the_port_spec_enumerates_exists(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    """``EVIDENCE_STORE_REQUIRED_DDL`` is the spec, not a paraphrase of it."""
    _, recorder = revision

    required = spec_columns(table_name)
    assert required, f"{table_name} has no enumerated spec entry"

    for container in (recorder.tables[table_name], Base.metadata.tables[table_name]):
        missing = [column for column in required if column not in container.c]
        assert missing == [], f"{table_name} is missing required columns {missing}"


@pytest.mark.parametrize(
    "table_name", sorted(PROSE_SPEC_COLUMNS), ids=sorted(PROSE_SPEC_COLUMNS)
)
def test_the_prose_spec_entries_carry_their_required_columns(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    for container in (recorder.tables[table_name], Base.metadata.tables[table_name]):
        missing = [
            column for column in PROSE_SPEC_COLUMNS[table_name] if column not in container.c
        ]
        assert missing == [], f"{table_name} is missing required columns {missing}"


def test_the_four_spec_objects_are_the_four_authored_tables() -> None:
    """The ledger closes gaps 1-4 by name; gaps 5-7 stay recorded as deferred.

    ``EVIDENCE_STORE_REQUIRED_DDL`` is now empty: task ``MIG-M04E-STORE-01``
    implemented ``SqlM04EEvidenceStore`` over these four tables, so the port no longer
    *requires* DDL it does not have. The reviewed spec of those four objects is kept
    under ``EVIDENCE_STORE_LEDGER_DDL`` and is what the parity checks above compare
    against — deleting it along with the blocker would have dropped the only written
    record of what these tables were required to contain.
    """
    assert set(LEDGER_TABLES) == {
        MAPPING_TABLE,
        QUARANTINE_TABLE,
        CHECKPOINT_TABLE,
        AUDIT_TABLE,
    }
    assert set(M04E_LEDGER_TABLES) == set(LEDGER_TABLES)

    from prodavan.application.migration.m04e_ports import (
        EVIDENCE_STORE_LEDGER_DDL,
        EVIDENCE_STORE_REQUIRED_DDL,
    )

    assert EVIDENCE_STORE_REQUIRED_DDL == (), (
        "the four ledger objects exist; a non-empty requirement list would be a stale "
        "blocker for a table that is already in the schema"
    )
    assert len(EVIDENCE_STORE_LEDGER_DDL) == 4
    for entry in EVIDENCE_STORE_LEDGER_DDL:
        assert entry.split(" ", 1)[0] in LEDGER_TABLES, entry


# ---------------------------------------------------------------------------
# 4. Per-source mapping ledger
# ---------------------------------------------------------------------------


def test_the_source_key_is_the_exact_five_column_identity(
    revision: tuple[Any, RecordedOps],
) -> None:
    """A second row for an already-mapped source identity collides at the storage layer."""
    _, recorder = revision

    recorded = _unique_catalog(recorder.tables[MAPPING_TABLE])
    expected = _unique_catalog(Base.metadata.tables[MAPPING_TABLE])

    assert recorded[SOURCE_KEY_CONSTRAINT] == (
        "tenant_id",
        "definition_id",
        "source_kind",
        "source_id",
        "source_schema_version",
    )
    assert recorded == expected


def test_accepted_is_representable_and_the_old_table_stays_two_state(
    revision: tuple[Any, RecordedOps],
) -> None:
    """Gap ``m04e_promotion_accepted_outcome_unrepresentable`` is closed for new rows."""
    _, recorder = revision

    recorded = _check_catalog(recorder.tables[MAPPING_TABLE])[MAPPING_STATUS_CHECK]
    expected = _check_catalog(Base.metadata.tables[MAPPING_TABLE])[MAPPING_STATUS_CHECK]

    assert recorded == expected
    assert "'accepted'" in recorded
    for outcome in BackfillOutcome:
        assert f"'{outcome.value}'" in recorded, outcome

    # The historical promotion table keeps its two-state limit: rewriting an applied
    # CHECK is a data migration, not a schema fix.
    legacy = _check_catalog(M04EDefinitionVersionPromotionEvidenceRow.__table__)[
        LEGACY_OUTCOME_CHECK
    ]
    assert "'accepted'" not in legacy
    assert "'blocked'" in legacy and "'quarantined'" in legacy


@pytest.mark.parametrize(
    "table_name", (MAPPING_TABLE, QUARANTINE_TABLE), ids=(MAPPING_TABLE, QUARANTINE_TABLE)
)
def test_no_outcome_column_carries_a_server_default(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    """A default on ``status`` would let an insert claim acceptance or fabricate a refusal.

    ``status`` deliberately has no server default on the mapping ledger, so the writer
    must state the outcome. The quarantine ledger's ``status`` tracks the *observation*
    lifecycle (open/resolved/archived) and defaults to ``open``, which asserts nothing
    about the backfill classification.
    """
    _, recorder = revision

    assert _default_text(recorder.tables[table_name].c.status) == (
        "open" if table_name == QUARANTINE_TABLE else None
    )
    assert _default_text(Base.metadata.tables[table_name].c.status) == (
        _default_text(recorder.tables[table_name].c.status)
    )
    # ``phase`` has no default either: a row never guesses which stream it belongs to.
    assert _default_text(Base.metadata.tables[table_name].c.phase) is None


def test_accepted_mapping_needs_a_canonical_digest_and_a_typed_identity() -> None:
    checks = _check_catalog(Base.metadata.tables[MAPPING_TABLE])

    assert checks["ck_m04e_mapping_accepted_has_canonical_digest"] == (
        "status <> 'accepted' OR canonical_digest IS NOT NULL"
    )
    # ``CASE`` guards ``jsonb_array_length`` against a non-array value, because a CHECK
    # does not promise left-to-right evaluation.
    shape = checks["ck_m04e_mapping_canonical_identity_shape"]
    assert shape.startswith("CASE WHEN jsonb_typeof(canonical_identity) <> 'array' THEN false")
    assert "CASE phase WHEN 'definition' THEN 2 ELSE 3 END" in shape


# ---------------------------------------------------------------------------
# 5. Observation ledger
# ---------------------------------------------------------------------------


def test_the_observation_key_includes_both_observed_digests(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision

    recorded = _unique_catalog(recorder.tables[QUARANTINE_TABLE])
    expected = _unique_catalog(Base.metadata.tables[QUARANTINE_TABLE])

    assert recorded[OBSERVATION_KEY_CONSTRAINT] == (
        "tenant_id",
        "definition_id",
        "source_kind",
        "source_id",
        "source_schema_version",
        "observed_source_digest",
        "observed_artifact_digest",
    )
    assert recorded == expected


def test_the_observation_key_is_null_safe(revision: tuple[Any, RecordedOps]) -> None:
    """A plain unique key would treat two NULL source digests as distinct observations."""
    _, recorder = revision

    for table in (
        recorder.tables[QUARANTINE_TABLE],
        Base.metadata.tables[QUARANTINE_TABLE],
    ):
        constraint = _unique_constraint(table, OBSERVATION_KEY_CONSTRAINT)
        assert constraint.dialect_options["postgresql"]["nulls_not_distinct"] is True
        assert (
            str(CreateTable(table).compile(dialect=_DIALECT)).count(
                "UNIQUE NULLS NOT DISTINCT"
            )
            == 1
        )


def test_an_observation_is_exactly_one_changed_digest() -> None:
    checks = _check_catalog(Base.metadata.tables[QUARANTINE_TABLE])

    assert checks["ck_m04e_quarantine_single_observation"] == (
        "(observed_source_digest IS NOT NULL) <> (observed_artifact_digest IS NOT NULL)"
    )
    assert checks["ck_m04e_quarantine_observation_differs"] == (
        "observed_source_digest IS NULL OR observed_source_digest <> source_digest"
    )
    assert checks["ck_m04e_quarantine_blocker_evidence_array"] == (
        "jsonb_typeof(blocker_evidence) = 'array'"
    )


# ---------------------------------------------------------------------------
# 6. Durable cursor
# ---------------------------------------------------------------------------


def test_the_cursor_is_one_head_row_per_partition_and_phase(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision

    recorded = _unique_catalog(recorder.tables[CHECKPOINT_TABLE])
    expected = _unique_catalog(Base.metadata.tables[CHECKPOINT_TABLE])

    assert recorded["uq_m04e_checkpoint_partition"] == ("tenant_id", "definition_id", "phase")
    assert recorded["uq_m04e_checkpoint_token"] == ("checkpoint_token",)
    assert recorded == expected


def test_the_cas_token_and_monotonicity_are_bounded() -> None:
    checks = _check_catalog(Base.metadata.tables[CHECKPOINT_TABLE])

    assert checks["ck_m04e_checkpoint_sequence_nonnegative"] == "sequence >= 0"
    assert checks["ck_m04e_checkpoint_complete_only_version"] == (
        "phase_complete = false OR phase = 'version'"
    )
    assert checks["ck_m04e_checkpoint_token_nonempty"] == (
        "length(btrim(checkpoint_token)) > 0"
    )
    assert "source_snapshot_digest" in checks["ck_m04e_checkpoint_source_sha256"]


# ---------------------------------------------------------------------------
# 7. Audit stream
# ---------------------------------------------------------------------------


def test_the_audit_stream_is_closed_and_its_counters_balance() -> None:
    checks = _check_catalog(Base.metadata.tables[AUDIT_TABLE])

    assert checks["ck_m04e_audit_event_kind"] == (
        "event_kind IN ('page_committed', 'page_blocked')"
    )
    assert checks["ck_m04e_audit_blocked_requires_reason"] == (
        "event_kind <> 'page_blocked' OR (reason_code IS NOT NULL "
        "AND length(btrim(reason_code)) > 0)"
    )
    balance = checks["ck_m04e_audit_counters_balance"]
    assert "record_count = accepted_count + replay_count + quarantined_count + conflict_count" in balance
    assert "event_kind = 'page_blocked' AND record_count = 0" in balance


@pytest.mark.parametrize(
    "case, table_name, constraint_name, fragment", NEGATIVE_CASES, ids=[c[0] for c in NEGATIVE_CASES]
)
def test_each_negative_case_is_enforced_by_a_named_constraint(
    revision: tuple[Any, RecordedOps],
    case: str,
    table_name: str,
    constraint_name: str,
    fragment: str,
) -> None:
    _, recorder = revision

    recorded = _check_catalog(recorder.tables[table_name])
    expected = _check_catalog(Base.metadata.tables[table_name])

    assert constraint_name in recorded, f"{case} has no enforcing check constraint"
    assert recorded[constraint_name] == expected[constraint_name]
    assert " ".join(fragment.split()) in recorded[constraint_name], case


# ---------------------------------------------------------------------------
# 8. Append-only and monotonicity enforcement
# ---------------------------------------------------------------------------


def test_append_only_triggers_cover_the_three_immutable_ledgers(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision
    statements = _execute_statements(recorder)

    function_ddl = next(
        statement for statement in statements if APPEND_ONLY_FUNCTION in statement
    )
    assert "RETURNS trigger" in function_ddl
    assert "LANGUAGE plpgsql" in function_ddl
    # Redacted by construction: a reason code, never a row value or digest.
    assert "m04e_ledger_row_immutable" in function_ddl
    assert "ERRCODE = '23514'" in function_ddl

    triggers = [statement for statement in statements if statement.startswith("CREATE TRIGGER")]
    append_only = [
        statement for statement in triggers if "trg_m04e_ledger_append_only_" in statement
    ]
    assert len(append_only) == 3
    for table_name in (MAPPING_TABLE, QUARANTINE_TABLE, AUDIT_TABLE):
        matching = [s for s in append_only if s.endswith(f"ON {table_name} FOR EACH ROW EXECUTE FUNCTION {APPEND_ONLY_FUNCTION}()")]
        assert len(matching) == 1, table_name
        assert "BEFORE UPDATE OR DELETE" in matching[0]
    # The checkpoint table is mutable by design: it must not carry the blanket guard.
    assert not any(CHECKPOINT_TABLE in statement for statement in append_only)


def test_the_checkpoint_table_carries_a_monotonicity_trigger(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision
    statements = _execute_statements(recorder)

    function_ddl = next(
        statement for statement in statements if CHECKPOINT_GUARD_FUNCTION in statement
    )
    for reason in (
        "m04e_checkpoint_lineage_rebind",
        "m04e_checkpoint_sequence_regression",
        "m04e_checkpoint_phase_rewind",
        "m04e_checkpoint_reopened",
        "m04e_checkpoint_token_reused",
    ):
        assert reason in function_ddl, reason
    assert "NEW.sequence < OLD.sequence" in function_ddl
    assert "NEW.checkpoint_token = OLD.checkpoint_token" in function_ddl
    # Six refusal branches: rebind, sequence regression, phase rewind, two un-completion
    # paths and token reuse. Each raises the same redacted SQLSTATE.
    assert function_ddl.count("ERRCODE = '23514'") == 6

    trigger = next(
        statement for statement in statements if statement.startswith(f"CREATE TRIGGER {CHECKPOINT_GUARD_TRIGGER}")
    )
    assert f"BEFORE UPDATE ON {CHECKPOINT_TABLE}" in trigger
    assert f"EXECUTE FUNCTION {CHECKPOINT_GUARD_FUNCTION}()" in trigger


def test_orm_append_only_guards_refuse_every_update_and_delete() -> None:
    """The ORM layer, exercised on transient state that carries no history."""
    guards = (
        (mapping_row, _reject_mapping_update, _reject_mapping_delete, "status", "blocked"),
        (
            quarantine_row,
            _reject_quarantine_update,
            _reject_quarantine_delete,
            "reason_code",
            "artifact_digest_changed",
        ),
        (audit_row, _reject_audit_update, _reject_audit_delete, "actor", "other-actor"),
    )

    for factory, update_guard, delete_guard, field, value in guards:
        unchanged = factory()
        make_transient_to_detached(unchanged)
        update_guard(None, None, unchanged)

        changed = factory()
        make_transient_to_detached(changed)
        setattr(changed, field, value)
        with pytest.raises(ValueError, match="append-only") as raised:
            update_guard(None, None, changed)
        assert field in str(raised.value)

        with pytest.raises(ValueError, match="append-only"):
            delete_guard(None, None, factory())

    # Every column of the three immutable ledgers is covered: any changed attribute is
    # a violation, so an unlisted column cannot be silently mutable.
    for table_name in (MAPPING_TABLE, QUARANTINE_TABLE, AUDIT_TABLE):
        guard = {
            MAPPING_TABLE: _reject_mapping_update,
            QUARANTINE_TABLE: _reject_quarantine_update,
            AUDIT_TABLE: _reject_audit_update,
        }[table_name]
        for column in Base.metadata.tables[table_name].columns:
            if column.name == "id":
                continue
            probe = ROW_FACTORIES[table_name]()
            make_transient_to_detached(probe)
            setattr(probe, column.name, "probe-value")
            with pytest.raises(ValueError, match="append-only"):
                guard(None, None, probe)


def test_checkpoint_guard_refuses_every_illegal_advance() -> None:
    # A legal advance: the cursor moves forward and the token is renewed.
    legal = checkpoint_row()
    make_transient_to_detached(legal)
    legal.sequence = 12
    legal.after_source_id = "src-12"
    legal.checkpoint_token = "checkpoint-token-12"
    _reject_checkpoint_regression(None, None, legal)

    with pytest.raises(ValueError, match="must not regress"):
        regressed = checkpoint_row()
        make_transient_to_detached(regressed)
        regressed.sequence = 5
        _reject_checkpoint_regression(None, None, regressed)

    with pytest.raises(ValueError, match="must not regress"):
        rewound = checkpoint_row(phase="version", phase_complete=True)
        make_transient_to_detached(rewound)
        rewound.phase = "definition"
        rewound.phase_complete = False
        rewound.checkpoint_token = "checkpoint-token-new"
        _reject_checkpoint_regression(None, None, rewound)

    with pytest.raises(ValueError, match="cannot be reopened"):
        reopened = checkpoint_row(phase="version", phase_complete=True)
        make_transient_to_detached(reopened)
        reopened.phase_complete = False
        reopened.checkpoint_token = "checkpoint-token-new"
        _reject_checkpoint_regression(None, None, reopened)

    for field, value in (
        ("tenant_id", "tenant-b"),
        ("definition_id", "definition-2"),
        ("source_snapshot_id", "snapshot-2"),
        ("source_snapshot_digest", "sha256:" + "c" * 64),
        ("canonical_schema_version", "m04e.v2"),
        ("writer_generation", 2),
    ):
        rebound = checkpoint_row()
        make_transient_to_detached(rebound)
        setattr(rebound, field, value)
        with pytest.raises(ValueError, match="cannot be rebound") as raised:
            _reject_checkpoint_regression(None, None, rebound)
        assert field in str(raised.value)


# ---------------------------------------------------------------------------
# 9. RLS: this revision installs its own policies
# ---------------------------------------------------------------------------


def test_this_revision_is_its_own_registry_reapply(
    revision: tuple[Any, RecordedOps],
) -> None:
    """A table-adding revision must not leave its policies to "the next pass"."""
    _, recorder = revision
    blocks = _policy_blocks(_execute_statements(recorder))

    assert blocks, "the revision must re-apply the canonical registry"
    installed = {_policy_block_table(block) for block in blocks}
    assert installed == set(policies.registry_tables()), (
        "the registry walk must cover every registered table; an uninstalled sibling "
        "would be reported by uncovered_relations instead of being policied"
    )
    assert set(LEDGER_TABLES) <= installed

    owned = {
        spec.table for spec in policies.CANONICAL_TABLE_POLICIES if spec.owner_revision == REVISION_ID
    }
    assert owned == set(LEDGER_TABLES)


def test_no_registered_table_is_created_after_the_newest_policy_installer() -> None:
    """Was "the newest policy installer is this revision": true only while this file's
    revision was the head. ``2026110316`` (the M06 cutover ledger) succeeded it and is
    itself a registry re-apply, so the rule this test must keep protecting is not "I am
    newest" but "no table is left behind a re-apply" — asserted against the head chain.
    """
    chain = chain_from_head()
    position = {revision: index for index, revision in enumerate(reversed(chain))}

    installers = policy_installers() & set(position)
    assert installers, "no revision installs policies"
    newest = max(installers, key=lambda revision: position[revision])

    assert position[newest] >= position[REVISION_ID], (
        "this revision's own tables would stay unpolicied: the newest policy installer "
        f"({newest}) predates {REVISION_ID}"
    )


def test_every_registered_table_is_policied_at_or_after_its_creating_revision() -> None:
    """The same rule ``test_policy_install_order`` enforces, restated from the head."""
    chain = chain_from_head()
    position = {revision: index for index, revision in enumerate(reversed(chain))}
    newest = max(
        (revision for revision in policy_installers() if revision in position),
        key=lambda revision: position[revision],
    )

    late = sorted(
        spec.table
        for spec in policies.CANONICAL_TABLE_POLICIES
        if position.get(spec.owner_revision, -1) > position[newest]
    )

    assert late == []


def test_the_ledgers_are_registered_next_to_the_other_m04e_tables() -> None:
    order = [spec.table for spec in policies.CANONICAL_TABLE_POLICIES]

    anchor = order.index("m04e_definition_version_promotion_evidence")
    assert order[anchor + 1 : anchor + 5] == list(LEDGER_TABLES)
    assert order.count(MAPPING_TABLE) == 1


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
def test_each_ledger_has_a_tenant_anchor_and_a_trusted_all_policy(table_name: str) -> None:
    spec = next(
        spec for spec in policies.CANONICAL_TABLE_POLICIES if spec.table == table_name
    )

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
    assert "true" not in predicate.replace("', true)", "")
    assert "::uuid" not in predicate

    tenant_statement, trusted_statement = policies.create_policy_statements(spec)
    assert f"WITH CHECK ({predicate})" in tenant_statement
    assert "FOR ALL" in trusted_statement
    assert "prodavan_service" in trusted_statement

    # Idempotent, existence-guarded DDL: a re-run converges instead of raising.
    upgrade_sql = policies.upgrade_guard_sql(spec)
    assert "DROP POLICY IF EXISTS" in upgrade_sql and "CREATE POLICY" in upgrade_sql
    assert f"to_regclass('{table_name}') IS NULL" in upgrade_sql
    assert "RAISE NOTICE" in upgrade_sql


def test_every_ledger_scope_column_exists_on_its_model() -> None:
    for table_name in LEDGER_TABLES:
        table = Base.metadata.tables[table_name]
        spec = next(
            spec for spec in policies.CANONICAL_TABLE_POLICIES if spec.table == table_name
        )
        assert spec.tenant is not None and spec.tenant.column in table.c


def test_the_coverage_report_finds_nothing_uncovered() -> None:
    assert policies.uncovered_relations(Base.metadata.tables.keys()) == ()

    assert all(table.startswith(policies.CANONICAL_TABLE_PREFIXES) for table in LEDGER_TABLES)


# ---------------------------------------------------------------------------
# 10. Redaction and registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
def test_no_raw_material_column_exists(table_name: str) -> None:
    """Only identifiers, digests, reason codes, cursor tokens and counters."""
    columns = {column.name for column in Base.metadata.tables[table_name].columns}

    for forbidden in FORBIDDEN_COLUMN_NAMES:
        assert forbidden not in columns, f"{table_name} exposes a raw {forbidden!r} column"


def test_json_columns_carry_only_derived_evidence() -> None:
    json_columns = {
        table_name: {
            column.name
            for column in Base.metadata.tables[table_name].columns
            if _type_label(column.type) == "JSONB"
        }
        for table_name in LEDGER_TABLES
    }

    assert json_columns == {
        MAPPING_TABLE: {"canonical_identity", "proof_evidence"},
        QUARANTINE_TABLE: {"blocker_evidence"},
        CHECKPOINT_TABLE: set(),
        AUDIT_TABLE: {"blocker_evidence"},
    }


@pytest.mark.parametrize("table_name", LEDGER_TABLES)
def test_every_ledger_is_registered_once_in_the_models_package(table_name: str) -> None:
    model = MODEL_BY_TABLE[table_name]

    assert getattr(models, model.__name__) is model
    assert model.__name__ in models.__all__
    assert Base.metadata.tables[table_name] is model.__table__

    owners = [
        mapper.class_ for mapper in Base.registry.mappers if mapper.local_table is model.__table__
    ]
    assert owners == [model]


def test_the_models_record_the_deferred_trade_offs() -> None:
    assert set(M04E_LEDGER_DEFERRED) == {
        "writer_and_composition_root",
        "direct_sql_checkpoint_regression",
        "append_only_privileges",
        "promotion_evidence_two_state_check",
        "definition_reference_integrity",
        "checkpoint_registry_version",
    }
    for reason in M04E_LEDGER_DEFERRED.values():
        assert len(reason.split()) >= 8, "each deferred gap needs a reviewed justification"
    assert "REGISTRY_VERSION" in M04E_LEDGER_DEFERRED["checkpoint_registry_version"]
    assert "SECURITY_REVIEW_ID" in M04E_LEDGER_DEFERRED["checkpoint_registry_version"]
    assert "MIG-M04E-PORTS-01" in M04E_LEDGER_DEFERRED["writer_and_composition_root"]

    assert len(set(M04E_LEDGER_ACCEPTANCE_CASES)) == len(M04E_LEDGER_ACCEPTANCE_CASES)
    for case in (
        "a_second_mapping_for_one_exact_source_identity_is_rejected",
        "a_mapping_row_can_express_the_accepted_outcome",
        "a_repeated_observation_collides_even_when_one_digest_is_null",
        "checkpoint_token_is_unique_so_a_stale_cas_token_cannot_be_reused",
        "audit_counter_balance_accounts_for_every_classification",
        "all_four_ledgers_are_tenant_scoped_by_rls",
    ):
        assert case in M04E_LEDGER_ACCEPTANCE_CASES


# ---------------------------------------------------------------------------
# 11. Revision hygiene and downgrade
# ---------------------------------------------------------------------------


def test_revision_emits_no_grants_or_data_statements(
    revision: tuple[Any, RecordedOps],
) -> None:
    """Registry re-apply is legitimate here; grants, writers and data are not."""
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
    assert len(own_statements) == 6, own_statements

    for statement in statements:
        upper = statement.upper()
        for forbidden in FORBIDDEN_DDL:
            assert forbidden not in upper, f"unexpected DDL fragment {forbidden!r}"

    # Every ``ALTER TABLE`` in this revision is the reviewed RLS enable/force pair the
    # registry emits — for any registered table, never for an unrelated one, and never
    # a column/constraint change on an existing table.
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
    assert set(LEDGER_TABLES) <= altered
    assert altered <= set(policies.registry_tables())

    # The six own statements are the two guard functions and their four triggers.
    assert sum(1 for s in own_statements if "CREATE OR REPLACE FUNCTION" in s) == 2
    assert sum(1 for s in own_statements if s.startswith("CREATE TRIGGER")) == 4


def test_downgrade_removes_only_this_revision_objects_in_reverse_order(
    revision: tuple[Any, RecordedOps],
) -> None:
    module, recorder = revision

    upgrade_call_count = len(recorder.calls)
    module.downgrade()
    downgrade_calls = recorder.calls[upgrade_call_count:]
    kinds = [call[0] for call in downgrade_calls]

    # 1. Only this revision's four policies are withdrawn.
    blocks = _policy_blocks([call[1] for call in downgrade_calls if call[0] == "execute"])
    assert {_policy_block_table(block) for block in blocks} == set(LEDGER_TABLES)
    for block in blocks:
        assert "DROP POLICY IF EXISTS" in block
        assert "DISABLE ROW LEVEL SECURITY" in block
        assert "DROP TABLE" not in block
        assert "EXCEPTION" not in block, "a real DDL failure must abort, not be swallowed"

    # 2. Then the triggers and their functions.
    executed = [call[1] for call in downgrade_calls if call[0] == "execute"]
    trigger_drops = [statement for statement in executed if "DROP TRIGGER IF EXISTS" in statement]
    assert len(trigger_drops) == 4
    assert any(CHECKPOINT_GUARD_TRIGGER in s for s in trigger_drops)
    for table_name in (MAPPING_TABLE, QUARANTINE_TABLE, AUDIT_TABLE):
        assert any(s.endswith(f" ON {table_name}") for s in trigger_drops), table_name
    function_drops = [statement for statement in executed if "DROP FUNCTION IF EXISTS" in statement]
    assert len(function_drops) == 2
    assert any(APPEND_ONLY_FUNCTION in s for s in function_drops)
    assert any(CHECKPOINT_GUARD_FUNCTION in s for s in function_drops)

    # 3. Then the indexes, newest table first, and finally the tables in reverse order.
    dropped_indexes = [call[2] for call in downgrade_calls if call[0] == "drop_index"]
    assert set(dropped_indexes) == {
        *MAPPING_INDEXES,
        *QUARANTINE_INDEXES,
        *CHECKPOINT_INDEXES,
        *AUDIT_INDEXES,
    }
    assert len(dropped_indexes) == 10

    dropped_tables = [call[1] for call in downgrade_calls if call[0] == "drop_table"]
    assert dropped_tables == list(reversed(LEDGER_TABLES))

    # 4. Order: policies, triggers, functions, indexes, tables.
    first_table_drop = kinds.index("drop_table")
    last_index_drop = max(
        index for index, kind in enumerate(kinds) if kind == "drop_index"
    )
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

    # 5. No data statement of any kind.
    assert not any(
        keyword in statement.upper()
        for statement in executed
        for keyword in ("DELETE FROM", "TRUNCATE", "INSERT INTO", "UPDATE M04E_")
    )


def test_downgrade_leaves_earlier_installers_policies_alone() -> None:
    """Withdrawing them would silently unprotect tables this revision never policied."""
    module = load_revision_module()

    assert module._POLICIES_ADDED_HERE == frozenset({REVISION_ID})
    earlier = {
        spec.owner_revision
        for spec in policies.CANONICAL_TABLE_POLICIES
        if spec.owner_revision != REVISION_ID
    }
    assert earlier, "the chain must have earlier policy owners"
    assert not earlier & module._POLICIES_ADDED_HERE


def test_a_successor_revision_would_not_fork_the_head() -> None:
    """Exactly one successor, it attaches here, and walking down from the head reaches us.

    Was "the head is the successor": ``2026110317`` now sits above ``2026110316``, so the
    head is one more step away. The property this test exists for — a linear chain with no
    fork — is stated directly instead of assuming the successor is always the head.
    """

    graph = revision_graph()
    successors = sorted(revision for revision, down in graph.items() if down == REVISION_ID)
    assert successors == [SUCCESSOR_REVISION_ID]
    for successor in successors:
        assert graph[successor] == REVISION_ID

    heads = head_revisions()
    assert len(heads) == 1, f"a fork would produce several heads: {heads}"
    assert heads[0] == chain_from_head()[0]
    assert REVISION_ID in chain_from_head()


def test_the_revision_can_be_loaded_by_path_like_alembic_does() -> None:
    module = load_revision_module_from(REVISION_FILE)

    assert callable(module.upgrade) and callable(module.downgrade)


def test_boundary_states_no_execution_and_no_positive_enablement() -> None:
    assert BOUNDARY == {
        "execution": "not run",
        "alembic_upgrade": False,
        "database": False,
        "positive_canonical_writes": False,
        "verification_freeze": True,
    }
