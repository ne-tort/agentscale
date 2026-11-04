"""Acceptance: the store *is* the port, targets the authored table, and cannot
silently degrade.

Three structural claims are checked here, and they are the ones a reviewer can
verify without a database:

1. **Surface** — ``SqlAlchemyProviderAttemptJournal`` implements every member of
   ``ProviderAttemptJournalPort`` and can be handed to ``ProviderAttemptJournal``
   (which refuses a port without ``cas_finish``).
2. **Table** — the store writes the row the persistence slice authored
   (``agent_run_provider_attempts``, revision ``2026110311``): primary key,
   unique idempotency key, cascade FK, the named CHECKs and the three indexes.
   A drifted model would make every predicate in this module wrong.
3. **Fail closed** — a missing table is ``AttemptJournalUnavailable`` for *every*
   operation; there is no in-memory fallback and no second table.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import ProgrammingError

from prodavan.application.agent_runs.attempts import (
    ATTEMPT_JOURNAL_BLOCKER,
    ATTEMPT_TABLE_BLOCKER,
    AttemptJournalUnavailable,
    AttemptOutcome,
    AttemptRefusal,
    AttemptRejected,
    AttemptState,
    ProviderAttemptJournal,
    ProviderAttemptJournalPort,
)
from prodavan.infrastructure.persistence.agent_control_plane import attempt_journal
from prodavan.infrastructure.persistence.agent_control_plane.attempt_journal import (
    REVISION,
    TABLE_NAME,
    SqlAlchemyProviderAttemptJournal,
    attempt_from_row,
    expired_unresolved_statement,
    finish_statement,
    read_statement,
    start_insert_statement,
    unresolved_statement,
)
from prodavan.infrastructure.persistence.agent_control_plane.attempt_registry import (
    ATTEMPT_STATES,
    AgentRunProviderAttemptRow,
)

from .acceptance_fixtures import (
    DEADLINE,
    NOW,
    RUN_ID,
    FakeResult,
    Script,
    Step,
    make_attempt,
    record,
    row_of,
    store_for,
)

PORT_MEMBERS = ("insert_start", "read", "find_unresolved", "cas_finish")


# --------------------------------------------------------------------------- #
# 1. Surface
# --------------------------------------------------------------------------- #


def test_the_store_implements_the_whole_port() -> None:
    for name in PORT_MEMBERS:
        assert name in dir(ProviderAttemptJournalPort), name
        assert callable(getattr(SqlAlchemyProviderAttemptJournal, name, None)), name


@pytest.mark.parametrize(
    "name", PORT_MEMBERS + ("start_attempt", "read_attempt", "find_expired_unresolved", "ensure_ready")
)
def test_the_store_exposes_the_migration_operation_vocabulary(name: str) -> None:
    assert callable(getattr(SqlAlchemyProviderAttemptJournal, name, None)), name


def test_the_application_journal_accepts_the_store_as_its_port() -> None:
    store = SqlAlchemyProviderAttemptJournal(session_factory=lambda: None)  # type: ignore[arg-type]

    # ``ProviderAttemptJournal`` refuses a port without ``cas_finish``; this must
    # not raise, otherwise no host could ever wire the two together.
    journal = ProviderAttemptJournal(port=store)  # type: ignore[arg-type]

    assert journal is not None


def test_the_store_holds_no_in_memory_state() -> None:
    """No fallback store: the only attribute is the session factory."""
    def fake_factory() -> None:  # pragma: no cover - never called here
        return None

    store = SqlAlchemyProviderAttemptJournal(session_factory=fake_factory)  # type: ignore[arg-type]

    assert set(vars(store)) == {"_session_factory"}
    assert {name for name in vars(store) if not name.startswith("_")} == set()


def test_no_module_level_attribute_remembers_an_attempt() -> None:
    """No process-memory journal: nothing at import time may hold attempt records."""
    from prodavan.application.agent_runs.attempts import ProviderAttempt

    for name in dir(attempt_journal):
        if name.startswith("__"):
            continue
        value = getattr(attempt_journal, name)
        assert not isinstance(value, ProviderAttempt), name
        if isinstance(value, (list, tuple, set, dict, frozenset)):
            assert not any(isinstance(item, ProviderAttempt) for item in value), name
            if isinstance(value, dict):
                assert not any(
                    isinstance(item, ProviderAttempt) for item in value.values()
                ), name


# --------------------------------------------------------------------------- #
# 2. Table contract
# --------------------------------------------------------------------------- #


def test_the_store_targets_the_authored_table_and_revision() -> None:
    assert TABLE_NAME == "agent_run_provider_attempts"
    assert REVISION == "2026110311"
    assert attempt_journal.__doc__ is not None


def test_the_table_is_the_row_the_persistence_slice_authored() -> None:
    table = AgentRunProviderAttemptRow.__table__

    assert table.name == "agent_run_provider_attempts"
    assert [column.name for column in table.primary_key.columns] == ["attempt_id"]

    columns = set(table.columns.keys())
    assert {
        "attempt_id",
        "idempotency_key",
        "tenant_id",
        "project_id",
        "run_id",
        "generation",
        "attempt",
        "sequence",
        "provider",
        "request_digest",
        "lease_token_digest",
        "state",
        "started_at",
        "session_id",
        "model_digest",
        "prompt_digest",
        "deadline_at",
        "finished_at",
        "failure_code",
        "provider_request_id",
        "metadata",
        "metadata_dropped",
        "created_at",
    } <= columns


def test_the_attempt_fk_cascades_with_the_run() -> None:
    table = AgentRunProviderAttemptRow.__table__

    foreign_keys = list(table.c.run_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].references("agent_runs.id")
    assert foreign_keys[0].ondelete == "CASCADE"


def test_the_unique_key_is_what_the_insert_conflicts_on() -> None:
    table = AgentRunProviderAttemptRow.__table__

    unique = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("idempotency_key",) in unique

    statement = start_insert_statement(attempt=make_attempt())
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in str(statement)


def test_the_widths_the_store_enforces_are_the_column_widths() -> None:
    """The store refuses an over-long identity instead of letting PostgreSQL truncate.

    ``ProviderAttempt`` validates looser bounds (``tenant_id``/``run_id``/
    ``provider`` up to 96 characters) than the columns allow, so the widths the
    store enforces must track the *model*, not the contract — hence this guard.
    """
    table = AgentRunProviderAttemptRow.__table__

    for name, width in attempt_journal._TEXT_WIDTHS.items():
        column = table.c[name]
        assert column.type.length == width, f"{name}: store={width} column={column.type.length}"


def test_an_identity_wider_than_its_column_is_refused_before_the_insert() -> None:
    """The contract allows a 96-character provider; the column allows 40."""
    from dataclasses import replace

    from prodavan.application.agent_runs.attempts import AttemptRefusal, AttemptRejected

    wide = replace(make_attempt(), provider="p" * 60)  # valid for the contract
    assert len(wide.provider) == 60

    with pytest.raises(AttemptRejected) as rejected:
        attempt_journal.start_values(wide)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD
    assert "provider" in rejected.value.detail


def test_the_state_vocabulary_matches_the_contract() -> None:
    """A state the CHECK allows but ``AttemptState`` rejects (or vice versa) is a bug."""
    assert set(ATTEMPT_STATES) == {state.value for state in AttemptState}


def test_the_named_checks_and_indexes_exist() -> None:
    table = AgentRunProviderAttemptRow.__table__

    checks = {constraint.name for constraint in table.constraints if constraint.name}
    assert {
        "ck_agent_run_attempt_generation_positive",
        "ck_agent_run_attempt_number_positive",
        "ck_agent_run_attempt_sequence_positive",
        "ck_agent_run_attempt_metadata_dropped_nonnegative",
        "ck_agent_run_attempt_finish_after_start",
        "ck_agent_run_attempt_state_vocabulary",
    } <= checks

    indexes = {index.name for index in table.indexes}
    assert {
        "ix_agent_run_attempts_run_generation",
        "ix_agent_run_attempts_state_deadline",
        "ix_agent_run_attempts_tenant_started",
    } <= indexes


def test_the_sweep_predicate_can_use_the_state_deadline_index() -> None:
    """The sweep filters on ``(state, deadline_at)``: the index must exist for it."""
    table = AgentRunProviderAttemptRow.__table__
    indexes = {
        index.name: [column.name for column in index.columns] for index in table.indexes
    }

    assert indexes["ix_agent_run_attempts_state_deadline"] == ["state", "deadline_at"]
    assert indexes["ix_agent_run_attempts_run_generation"] == ["run_id", "generation"]


# --------------------------------------------------------------------------- #
# 3. Statement shapes
# --------------------------------------------------------------------------- #


def test_the_insert_is_one_statement_with_a_returning_clause() -> None:
    statement = start_insert_statement(attempt=make_attempt())
    sql = str(statement)

    assert sql.startswith("INSERT INTO agent_run_provider_attempts ")
    assert sql.count("INSERT ") == 1
    assert "UPDATE " not in sql and "DELETE " not in sql
    assert "RETURNING agent_run_provider_attempts.attempt_id" in sql


def test_the_finish_is_exactly_one_update() -> None:
    statement = finish_statement(
        attempt_id="pat_any",
        expected_state=AttemptState.STARTED,
        next_state=AttemptState.KNOWN_SUCCEEDED,
        finished_at=NOW,
        metadata={"provider": "agent-runtime"},
    )
    sql = str(statement)

    assert sql.startswith("UPDATE agent_run_provider_attempts SET ")
    assert sql.count("UPDATE ") == 1
    assert "INSERT " not in sql and "DELETE " not in sql
    # the CAS fence: identity and the observed state, in that order
    assert (
        "WHERE agent_run_provider_attempts.attempt_id = " in sql
        and "agent_run_provider_attempts.state = " in sql
    )


def test_every_read_is_a_single_select() -> None:
    statements = (
        read_statement(attempt_id="pat_any"),
        unresolved_statement(run_id=RUN_ID),
        expired_unresolved_statement(now=NOW),
    )

    for statement in statements:
        sql = str(statement)
        assert sql.startswith("SELECT ")
        assert sql.count("SELECT ") == 1
        assert "UPDATE " not in sql and "DELETE " not in sql and "INSERT " not in sql


def test_no_statement_touches_a_second_table() -> None:
    """The journal is one table; anything else would be a second source of truth."""
    statements = (
        str(start_insert_statement(attempt=make_attempt())),
        str(
            finish_statement(
                attempt_id="pat_any",
                expected_state=AttemptState.STARTED,
                next_state=AttemptState.KNOWN_SUCCEEDED,
                finished_at=NOW,
            )
        ),
        str(read_statement(attempt_id="pat_any")),
        str(unresolved_statement(run_id=RUN_ID)),
        str(expired_unresolved_statement(now=NOW)),
    )

    for sql in statements:
        assert "agent_run_provider_attempts" in sql
        for other in ("agent_runs ", "agent_run_leases", "agent_control_plane_"):
            assert other not in sql, other


# --------------------------------------------------------------------------- #
# 4. Fail closed
# --------------------------------------------------------------------------- #


def missing_table_error() -> ProgrammingError:
    return ProgrammingError(
        "SELECT agent_run_provider_attempts.attempt_id ...",
        {},
        Exception('relation "agent_run_provider_attempts" does not exist'),
    )


async def test_every_operation_reports_the_missing_table_as_a_named_blocker() -> None:
    attempt = make_attempt()

    def operations() -> tuple[tuple[str, str, Any], ...]:
        return (
            ("insert_start", "scalar", lambda store: store.insert_start(attempt)),
            ("start_attempt", "scalar", lambda store: store.start_attempt(attempt)),
            ("read", "execute", lambda store: store.read(attempt_id=attempt.attempt_id)),
            (
                "read_attempt",
                "execute",
                lambda store: store.read_attempt(attempt_id=attempt.attempt_id),
            ),
            ("find_unresolved", "execute", lambda store: store.find_unresolved(run_id=RUN_ID)),
            (
                "find_expired_unresolved",
                "execute",
                lambda store: store.find_expired_unresolved(now=DEADLINE),
            ),
            ("ensure_ready", "execute", lambda store: store.ensure_ready()),
            (
                "cas_finish",
                "execute",
                lambda store: store.cas_finish(
                    attempt_id=attempt.attempt_id,
                    expected_state=AttemptState.STARTED,
                    next_state=AttemptState.KNOWN_SUCCEEDED,
                    finished_at=NOW,
                    failure_code=None,
                    provider_request_id=None,
                    metadata={},
                    metadata_dropped=0,
                ),
            ),
        )

    for name, kind, operation in operations():
        script = Script(Step(kind, error=missing_table_error()))  # type: ignore[arg-type]
        with pytest.raises(AttemptJournalUnavailable) as unavailable:
            await operation(store_for(script))
        assert unavailable.value.blocker == ATTEMPT_JOURNAL_BLOCKER, name
        assert TABLE_NAME in str(unavailable.value), name
        assert REVISION in str(unavailable.value), name


async def test_the_blocker_carries_the_contract_identifier() -> None:
    script = Script(Step("execute", error=missing_table_error()))

    with pytest.raises(AttemptJournalUnavailable) as unavailable:
        await store_for(script).read(attempt_id="pat_any")

    assert isinstance(unavailable.value, AttemptJournalUnavailable)
    assert unavailable.value.blocker == ATTEMPT_JOURNAL_BLOCKER
    # the persistence-side blocker stays a distinct identifier
    assert unavailable.value.blocker != ATTEMPT_TABLE_BLOCKER


async def test_ensure_ready_passes_when_the_table_is_present() -> None:
    script = Script(Step("execute", FakeResult(rows=())))

    await store_for(script).ensure_ready()

    assert script.kinds == ["execute"]
    # one cheap probe of the table, never a migration or a DDL statement
    assert "LIMIT" in str(script.calls[0][1])
    assert "CREATE" not in str(script.calls[0][1]).upper()


# --------------------------------------------------------------------------- #
# 5. Row mapping round-trip
# --------------------------------------------------------------------------- #


def test_a_row_round_trips_through_the_contract_record() -> None:
    attempt = record(provider_request_id="req-1")

    restored = attempt_from_row(row_of(attempt))

    assert restored == attempt
    assert restored.provider_request_id == "req-1"
    assert restored.state is attempt.state


def test_a_row_with_a_null_metadata_column_reads_as_empty() -> None:
    attempt = make_attempt()
    row = row_of(attempt)
    row.metadata_json = None  # a legacy/hand-written row

    restored = attempt_from_row(row)

    assert dict(restored.metadata) == {}


def test_a_row_with_an_unknown_state_is_refused() -> None:
    attempt = make_attempt()
    row = row_of(attempt)
    row.state = "half_open"  # not in the vocabulary

    with pytest.raises(AttemptRejected) as rejected:
        attempt_from_row(row)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


def test_a_row_that_breaks_a_positive_counter_is_refused() -> None:
    attempt = make_attempt()
    row = row_of(attempt)
    row.generation = 0  # the CHECK would never have allowed it

    with pytest.raises(AttemptRejected) as rejected:
        attempt_from_row(row)

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


def test_an_unresolved_record_is_not_success_and_a_decided_one_is_not_unresolved() -> None:
    started = record()
    succeeded = record(AttemptState.KNOWN_SUCCEEDED)
    ambiguous = record(AttemptState.UNKNOWN)

    assert started.is_unresolved and not started.is_terminal and not started.is_success
    assert succeeded.is_success and succeeded.is_terminal and not succeeded.is_unresolved
    assert ambiguous.is_unresolved and not ambiguous.is_terminal and not ambiguous.is_success


def test_the_outcome_vocabulary_is_what_finish_attempt_accepts() -> None:
    """Every outcome the contract names has a defined home in the matrix."""
    assert {outcome.value for outcome in AttemptOutcome} == {
        "succeeded",
        "failed",
        "retryable",
        "unknown",
        "abandoned",
        "discarded_stale",
        "reconciled_succeeded",
        "reconciled_failed",
    }
