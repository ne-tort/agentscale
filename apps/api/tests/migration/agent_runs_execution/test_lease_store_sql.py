"""Acceptance for the SQL lease store: statement predicates and table contract.

The store is verified by rendering its statements for the PostgreSQL dialect
(no database, no DDL) and asserting on the rendered ``WHERE`` clause, plus a
structural check that the table it targets is the one the persistence slice
authored.  See ``acceptance.md`` for the case matrix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from prodavan.application.agent_runs.execution import AgentRunLease
from prodavan.application.agent_runs.lease import (
    CLAIMABLE_STATUSES,
    TERMINAL_STATUSES,
    WRITABLE_WHILE_RUNNING,
    LeaseSlot,
    RunLeaseStore,
)
from prodavan.infrastructure.persistence.agent_control_plane import lease_store
from prodavan.infrastructure.persistence.agent_control_plane.lease_registry import (
    AgentRunLeaseRow,
)

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(seconds=60)

DIALECT = postgresql.dialect()


def rendered(statement) -> str:
    """Render one statement as literal PostgreSQL text on a single line."""
    compiled = statement.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
    return " ".join(str(compiled).split())


def make_slot(**overrides: object) -> LeaseSlot:
    fields: dict[str, object] = {
        "run_id": "agr_lease",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "status": "queued",
        "sequence": 3,
        "attempt": 1,
        "generation": 0,
        "session_id": "session-a",
    }
    fields.update(overrides)
    return LeaseSlot(**fields)  # type: ignore[arg-type]


def make_lease(**overrides: object) -> AgentRunLease:
    fields: dict[str, object] = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "run_id": "agr_lease",
        "session_id": "session-a",
        "owner_id": "worker-a",
        "lease_token": "token-generation-1",
        "generation": 1,
        "attempt": 2,
        "sequence": 4,
        "lease_until": DEADLINE,
    }
    fields.update(overrides)
    return AgentRunLease(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Claim candidate
# --------------------------------------------------------------------------- #


def test_claim_candidate_locks_exactly_one_eligible_row() -> None:
    sql = rendered(lease_store.claim_candidate_statement(now=NOW))

    # exactly one row, and the row lock is *skipping* what another worker locked
    assert "LIMIT 1" in sql
    assert sql.endswith("FOR UPDATE OF agent_run_leases SKIP LOCKED")
    # the lock is only on the lease row, never on agent_runs (no run-row contention)
    assert "FOR UPDATE OF agent_runs" not in sql
    # deterministic order: retry window first, then lease deadline, then identity
    assert "ORDER BY agent_run_leases.retry_at ASC NULLS FIRST" in sql
    assert "agent_run_leases.lease_until ASC NULLS FIRST" in sql


def test_claim_candidate_applies_status_due_and_deadline_predicates() -> None:
    sql = rendered(lease_store.claim_candidate_statement(now=NOW))

    assert "agent_runs.status IN ('queued', 'retry_due')" in sql
    assert "agent_control_plane_aggregates.status = agent_runs.status" in sql
    assert "agent_control_plane_aggregates.last_sequence >= 1" in sql
    assert "agent_run_leases.attempt >= 1" in sql
    assert (
        "agent_run_leases.retry_at IS NULL OR agent_run_leases.retry_at <= '2026-11-04 09:00:00+00:00'"
        in sql
    )
    assert (
        "agent_run_leases.lease_until IS NULL OR agent_run_leases.lease_until <= '2026-11-04 09:00:00+00:00'"
        in sql
    )


def test_claim_candidate_requires_a_committed_aggregate_cursor() -> None:
    """An inner join on the cursor: a run without a sequence is never claimable."""
    sql = rendered(lease_store.claim_candidate_statement(now=NOW))

    assert "JOIN agent_runs ON agent_runs.id = agent_run_leases.run_id" in sql
    assert (
        "JOIN agent_control_plane_aggregates ON agent_control_plane_aggregates.aggregate_type = 'agent_run'"
        in sql
    )
    assert "LEFT OUTER JOIN" not in sql


def test_claim_candidate_can_be_narrowed_to_one_status() -> None:
    """A caller that cannot start ``retry_due`` narrows the predicate in SQL."""
    sql = rendered(
        lease_store.claim_candidate_statement(now=NOW, statuses=frozenset({"queued"}))
    )

    assert "agent_runs.status IN ('queued')" in sql
    assert "retry_due" not in sql
    # still exactly one locked row, still never locking the run row
    assert "LIMIT 1" in sql
    assert sql.endswith("FOR UPDATE OF agent_run_leases SKIP LOCKED")


def test_claim_candidate_refuses_an_empty_or_widened_status_set() -> None:
    with pytest.raises(ValueError):
        lease_store.claim_candidate_statement(now=NOW, statuses=frozenset())
    with pytest.raises(ValueError):
        lease_store.claim_candidate_statement(now=NOW, statuses=frozenset({"running"}))


def test_claim_candidate_reads_its_snapshot_from_three_tables() -> None:
    sql = rendered(lease_store.claim_candidate_statement(now=NOW))

    for column in (
        "agent_runs.status",
        "agent_control_plane_aggregates.last_sequence",
        "agent_runs.session_id",
        "agent_run_leases.lease_generation",
        "agent_run_leases.attempt",
        "agent_run_leases.retry_at",
    ):
        assert column in sql


# --------------------------------------------------------------------------- #
# Acquire
# --------------------------------------------------------------------------- #


def test_acquire_is_one_generation_and_attempt_fenced_update() -> None:
    statement = lease_store.acquire_statement(slot=make_slot(), lease=make_lease(), now=NOW)
    sql = rendered(statement)

    assert sql.startswith("UPDATE agent_run_leases SET ")
    assert "SELECT" not in sql.split("EXISTS")[0]  # no read-then-write before the fence
    assert "agent_run_leases.lease_generation = 0" in sql
    assert "agent_run_leases.attempt = 1" in sql
    assert "agent_run_leases.lease_generation=1" in sql
    assert "attempt=2" in sql
    assert "lease_token='token-generation-1'" in sql
    assert "lease_owner='worker-a'" in sql


def test_acquire_requires_the_run_to_still_be_claimable() -> None:
    sql = rendered(lease_store.acquire_statement(slot=make_slot(), lease=make_lease(), now=NOW))

    assert (
        "EXISTS (SELECT agent_runs.id FROM agent_runs WHERE agent_runs.id = agent_run_leases.run_id "
        "AND agent_runs.status IN ('queued', 'retry_due'))"
    ) in sql


def test_acquire_requires_an_open_retry_window_and_dead_lease() -> None:
    sql = rendered(lease_store.acquire_statement(slot=make_slot(), lease=make_lease(), now=NOW))

    assert "agent_run_leases.retry_at IS NULL OR agent_run_leases.retry_at <=" in sql
    assert "agent_run_leases.lease_until IS NULL OR agent_run_leases.lease_until <=" in sql


def test_acquire_refuses_a_lease_that_does_not_follow_the_planned_slot() -> None:
    slot = make_slot()

    with pytest.raises(ValueError):
        # generation must be exactly slot.generation + 1
        lease_store.acquire_statement(slot=slot, lease=make_lease(generation=2), now=NOW)
    with pytest.raises(ValueError):
        lease_store.acquire_statement(slot=slot, lease=make_lease(attempt=1), now=NOW)
    with pytest.raises(ValueError):
        lease_store.acquire_statement(
            slot=slot, lease=make_lease(run_id="agr_other"), now=NOW
        )


# --------------------------------------------------------------------------- #
# Renew / release / park / fence
# --------------------------------------------------------------------------- #


def test_renew_requires_token_owner_and_a_live_deadline() -> None:
    sql = rendered(
        lease_store.renew_statement(
            lease=make_lease(), now=NOW, lease_until=DEADLINE + timedelta(seconds=30)
        )
    )

    assert sql.startswith("UPDATE agent_run_leases SET lease_until=")
    assert "agent_run_leases.lease_generation = 1" in sql
    assert "agent_run_leases.lease_token = 'token-generation-1'" in sql
    assert "agent_run_leases.lease_owner = 'worker-a'" in sql
    # an expired lease is never resurrected, and a renewal never shortens the deadline
    assert "agent_run_leases.lease_until > '2026-11-04 09:00:00+00:00'" in sql
    assert "agent_run_leases.lease_until <= '2026-11-04 09:01:30+00:00'" in sql
    assert "agent_runs.status IN ('queued', 'retry_due', 'running')" in sql


def test_renew_refuses_a_deadline_that_is_not_in_the_future() -> None:
    with pytest.raises(ValueError):
        lease_store.renew_statement(lease=make_lease(), now=NOW, lease_until=NOW)


def test_release_requires_generation_token_owner_and_the_asserted_status() -> None:
    sql = rendered(lease_store.release_statement(lease=make_lease(), now=NOW, status="running"))

    assert sql.startswith("UPDATE agent_run_leases SET lease_owner=NULL, lease_token=NULL, lease_until=NULL")
    assert "agent_run_leases.lease_generation = 1" in sql
    assert "agent_run_leases.lease_token = 'token-generation-1'" in sql
    assert "agent_run_leases.lease_owner = 'worker-a'" in sql
    assert "agent_run_leases.lease_until IS NOT NULL" in sql
    assert "agent_runs.status IN ('running')" in sql
    # releasing keeps the generation: the next claim must increment it
    assert "lease_generation=" not in sql.split("WHERE")[0]


def test_release_back_to_queued_clears_the_retry_gate() -> None:
    sql = rendered(lease_store.release_statement(lease=make_lease(), now=NOW, status="queued"))

    assert "retry_at=NULL" in sql
    assert "agent_runs.status IN ('queued')" in sql


def test_release_refuses_a_status_outside_the_releasable_set() -> None:
    with pytest.raises(ValueError):
        lease_store.release_statement(lease=make_lease(), now=NOW, status="waiting_approval")
    with pytest.raises(ValueError):
        lease_store.release_statement(lease=make_lease(), now=NOW, status="")


def test_park_keeps_the_generation_and_sets_the_retry_gate() -> None:
    sql = rendered(
        lease_store.park_statement(lease=make_lease(), now=NOW, retry_at=DEADLINE)
    )

    assert sql.startswith("UPDATE agent_run_leases SET lease_owner=NULL")
    assert "retry_at='2026-11-04 09:01:00+00:00'" in sql
    assert "lease_generation=" not in sql.split("WHERE")[0]
    assert "agent_run_leases.lease_generation = 1" in sql
    assert "agent_run_leases.lease_token = 'token-generation-1'" in sql
    assert "agent_runs.status IN ('running')" in sql


def test_park_without_a_retry_time_clears_the_gate() -> None:
    sql = rendered(lease_store.park_statement(lease=make_lease(), now=NOW, retry_at=None))

    assert "retry_at=NULL" in sql


def test_fence_bumps_the_generation_for_the_tenant() -> None:
    sql = rendered(
        lease_store.fence_statement(tenant_id="tenant-a", run_id="agr_lease", now=NOW)
    )

    assert sql.startswith("UPDATE agent_run_leases SET ")
    assert "lease_generation=(agent_run_leases.lease_generation + 1)" in sql
    assert "lease_owner=NULL" in sql
    assert "agent_run_leases.tenant_id = 'tenant-a'" in sql
    # the fence is the one statement that is not token-scoped: it is executed by
    # an actor that does not hold the lease
    assert "lease_token = '" not in sql


def test_every_cas_statement_is_a_single_update() -> None:
    statements = (
        lease_store.acquire_statement(slot=make_slot(), lease=make_lease(), now=NOW),
        lease_store.renew_statement(
            lease=make_lease(), now=NOW, lease_until=DEADLINE + timedelta(seconds=30)
        ),
        lease_store.release_statement(lease=make_lease(), now=NOW, status="queued"),
        lease_store.park_statement(lease=make_lease(), now=NOW, retry_at=DEADLINE),
        lease_store.fence_statement(tenant_id="tenant-a", run_id="agr_lease", now=NOW),
    )

    for statement in statements:
        sql = rendered(statement)
        assert sql.count("UPDATE ") == 1
        assert sql.count("DELETE ") == 0
        assert sql.count("INSERT ") == 0


# --------------------------------------------------------------------------- #
# Store and table contract
# --------------------------------------------------------------------------- #


def test_store_exposes_the_full_protocol_and_the_fencing_extensions() -> None:
    for name in (
        "database_now",
        "select_claim_candidate",
        "cas_acquire",
        "cas_renew",
        "cas_release",
        "read_slot",
        "cas_park",
        "cas_fence",
        "claim_scope",
    ):
        assert callable(getattr(lease_store.SqlAlchemyRunLeaseStore, name, None)), name

    # the protocol the application tier declared is a subset of that surface
    for name in (
        "database_now",
        "select_claim_candidate",
        "cas_acquire",
        "cas_renew",
        "cas_release",
        "read_slot",
    ):
        assert name in dir(RunLeaseStore), name
        assert callable(getattr(lease_store.SqlAlchemyRunLeaseStore, name, None)), name


def test_store_holds_no_in_memory_state() -> None:
    """No fallback store: the only mutable attribute is the pinned transaction."""
    def fake_factory() -> None:  # pragma: no cover - never called here
        return None

    store = lease_store.SqlAlchemyRunLeaseStore(session_factory=fake_factory)  # type: ignore[arg-type]

    assert store._pinned is None
    public = {name for name in vars(store) if not name.startswith("_")}
    assert public == set()
    assert set(vars(store)) == {"_session_factory", "_pinned"}


def test_release_and_park_status_sets_are_closed() -> None:
    assert lease_store.RELEASABLE_STATUSES == (
        CLAIMABLE_STATUSES | WRITABLE_WHILE_RUNNING | TERMINAL_STATUSES
    )
    assert lease_store.PARKED_STATUSES == WRITABLE_WHILE_RUNNING
    assert lease_store.RENEWABLE_STATUSES == CLAIMABLE_STATUSES | WRITABLE_WHILE_RUNNING


def test_lease_table_is_the_row_the_persistence_slice_authored() -> None:
    table = AgentRunLeaseRow.__table__

    assert table.name == "agent_run_leases"
    assert [column.name for column in table.primary_key.columns] == ["run_id"]
    columns = set(table.columns.keys())
    assert {
        "run_id",
        "tenant_id",
        "project_id",
        "lease_owner",
        "lease_token",
        "lease_until",
        "lease_generation",
        "attempt",
        "retry_at",
        "updated_at",
    } <= columns

    foreign_keys = list(table.c.run_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].references("agent_runs.id")
    assert foreign_keys[0].ondelete == "CASCADE"

    checks = {constraint.name for constraint in table.constraints if constraint.name}
    assert {
        "ck_agent_run_lease_token_pair",
        "ck_agent_run_lease_until_pair",
        "ck_agent_run_lease_generation_nonnegative",
        "ck_agent_run_lease_attempt_positive",
    } <= checks

    indexes = {index.name for index in table.indexes}
    assert {"ix_agent_run_leases_claimable", "ix_agent_run_leases_tenant_project"} <= indexes
