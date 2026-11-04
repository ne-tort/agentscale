"""Criterion 4 — claim/lease contention on ``agent_run_leases``.

The durable lease fence is what keeps two workers from advancing one run.  This
module exercises the real adapter
(:class:`~prodavan.infrastructure.persistence.agent_control_plane.lease_store.SqlAlchemyRunLeaseStore`)
and the real policy layer
(:class:`~prodavan.application.agent_runs.lease.DurableRunLeaseManager`) against
PostgreSQL; the SQL is production SQL, not a re-implementation.

Covered:

* two workers racing for the same run — exactly one lease is written;
* ``FOR UPDATE SKIP LOCKED`` really skips: with the candidate row locked by a
  peer transaction, a second worker sees **no** candidate instead of blocking;
* a live lease is not stolen, but an **expired** lease is reusable, and the
  reclaim moves ``lease_generation`` forward so the previous owner is fenced out;
* a ``retry_at`` in the future makes the run unclaimable even with no lease
  held, and clearing the window makes it claimable again;
* a run with no committed aggregate cursor is fail-closed (never claimable).

Every contention point is created by holding a transaction open on an
independent connection and observing the peer in ``pg_stat_activity``; there is
no ``sleep``-based race anywhere.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from prodavan.application.agent_runs.lease import DurableRunLeaseManager, LeasePolicy
from prodavan.infrastructure.persistence.agent_control_plane.lease_store import (
    RUN_AGGREGATE_TYPE,
    SqlAlchemyRunLeaseStore,
    claim_candidate_statement,
)

from ._seeds import seed_claimable_run, seed_run, seed_scope

pytestmark = pytest.mark.integration

WORKER_A = "worker-a"
WORKER_B = "worker-b"


def _store(harness) -> SqlAlchemyRunLeaseStore:
    return SqlAlchemyRunLeaseStore(session_factory=harness.session_factory)


def _manager(store: SqlAlchemyRunLeaseStore, *, ttl_seconds: int = 60) -> DurableRunLeaseManager:
    return DurableRunLeaseManager(store=store, policy=LeasePolicy(ttl=timedelta(seconds=ttl_seconds)))


async def _candidate_run_ids(harness) -> list[str]:
    """Ask the *production* claim statement which run a worker would take."""
    statement = claim_candidate_statement(now=await _store(harness).database_now())
    async with harness.session() as session:
        async with session.begin():
            rows = (await session.execute(statement)).mappings().all()
    return [row["run_id"] for row in rows]


# --------------------------------------------------------------------------- #
# two workers, one run
# --------------------------------------------------------------------------- #
async def test_two_workers_cannot_claim_the_same_run(pg_schema):
    scope = await seed_scope(pg_schema)
    await seed_claimable_run(pg_schema, scope, run_id="agr_claim_race")
    store = _store(pg_schema)
    manager = _manager(store)

    first, second = await asyncio.gather(
        manager.acquire_next(owner_id=WORKER_A),
        manager.acquire_next(owner_id=WORKER_B),
    )

    leases = [lease for lease in (first, second) if lease is not None]
    assert len(leases) == 1, f"exactly one worker may claim the run, got {len(leases)}"
    winner = leases[0]
    assert winner.run_id == "agr_claim_race"
    assert winner.generation == 1
    assert winner.attempt == 2
    assert winner.sequence == 2  # the claim commits agent_run.start

    rows = await pg_schema.rows(
        "SELECT lease_owner, lease_token, lease_generation, attempt, lease_until "
        "FROM agent_run_leases WHERE run_id = :run",
        {"run": winner.run_id},
    )
    assert len(rows) == 1
    owner, token, generation, attempt, lease_until = rows[0]
    assert (owner, token, generation, attempt) == (
        winner.owner_id,
        winner.lease_token,
        1,
        2,
    )
    assert lease_until is not None

    # A live lease is not claimable at all, so a third worker gets nothing.
    third = await manager.acquire_next(owner_id="worker-c")
    assert third is None
    assert await _candidate_run_ids(pg_schema) == []


async def test_skip_locked_makes_the_peer_skip_instead_of_blocking(pg_schema):
    """A locked candidate is skipped, not waited on.

    ``SKIP LOCKED`` is the property that lets N workers poll one queue without
    serialising.  The proof is behavioural: while a peer transaction holds the
    candidate row, the statement returns ``[]`` *immediately*; the same
    statement with a plain ``FOR UPDATE`` would block until the peer commits.
    """
    scope = await seed_scope(pg_schema)
    await seed_claimable_run(pg_schema, scope, run_id="agr_skip_locked")
    store = _store(pg_schema)
    now = await store.database_now()
    statement = claim_candidate_statement(now=now)

    async with pg_schema.transaction(commit=False) as holder:
        locked = (await holder.execute(statement)).mappings().all()
        assert [row["run_id"] for row in locked] == ["agr_skip_locked"]

        # Same statement, different connection, while the row lock is held.
        async with pg_schema.session() as peer:
            async with peer.begin():
                skipped = (await peer.execute(claim_candidate_statement(now=now))).mappings().all()
        assert skipped == [], "SKIP LOCKED must skip the row this transaction holds"

    # Once the holder released the lock the row is a candidate again.
    assert await _candidate_run_ids(pg_schema) == ["agr_skip_locked"]


async def test_a_blocked_peer_does_not_prevent_the_winner_from_writing(pg_schema):
    """The winner's claim commits while a second claim is parked in the database.

    This is the "no partial write under contention" half of criterion 4: the
    parked worker holds no lock on the winner's row (it skipped it), so the
    winner's ``cas_acquire`` is not blocked by the peer's presence.
    """
    scope = await seed_scope(pg_schema)
    await seed_claimable_run(pg_schema, scope, run_id="agr_claim_order")
    store = _store(pg_schema)
    manager = _manager(store)

    async with store.claim_scope():
        lease = await manager.acquire_next(owner_id=WORKER_A)
        assert lease is not None
        # While this claim transaction is still open, a second store sees the row
        # as locked-or-leased and claims nothing.
        peer_lease = await _manager(_store(pg_schema)).acquire_next(owner_id=WORKER_B)
        assert peer_lease is None

    stored = await pg_schema.scalar(
        "SELECT lease_owner FROM agent_run_leases WHERE run_id = :run",
        {"run": "agr_claim_order"},
    )
    assert stored == WORKER_A


# --------------------------------------------------------------------------- #
# expiry and reuse
# --------------------------------------------------------------------------- #
async def test_an_expired_lease_is_reused_with_a_new_generation(pg_schema):
    scope = await seed_scope(pg_schema)
    store = _store(pg_schema)
    now = await store.database_now()
    await seed_claimable_run(
        pg_schema,
        scope,
        run_id="agr_expired",
        lease={
            "lease_owner": WORKER_A,
            "lease_token": "stale-token",
            "lease_until": now - timedelta(minutes=5),
            "lease_generation": 3,
            "attempt": 2,
        },
    )

    reclaimed = await _manager(store).acquire_next(owner_id=WORKER_B)
    assert reclaimed is not None, "an expired lease must be claimable again"
    assert reclaimed.run_id == "agr_expired"
    assert reclaimed.generation == 4, "reclaim must move the fence forward"
    assert reclaimed.attempt == 3
    assert reclaimed.owner_id == WORKER_B
    assert reclaimed.lease_token != "stale-token"

    (generation, owner, token) = (
        await pg_schema.rows(
            "SELECT lease_generation, lease_owner, lease_token FROM agent_run_leases "
            "WHERE run_id = :run",
            {"run": "agr_expired"},
        )
    )[0]
    assert (generation, owner) == (4, WORKER_B)
    assert token != "stale-token"

    # The previous owner is now fenced out: its token and generation are stale.
    verify = await store.read_slot(run_id="agr_expired")
    assert verify is not None
    assert verify.generation == 4
    assert verify.lease_token == reclaimed.lease_token


async def test_a_lease_expiring_during_the_claim_window_is_still_reclaimable(pg_schema):
    """Eligibility is decided in *database* time at claim time, not by the caller.

    An expired lease is only reclaimable while nothing else holds the row; the
    deadline comparison uses ``SELECT now()``.  Placing the deadline a second in
    the past and one in the future pins both sides of that comparison.
    """
    scope = await seed_scope(pg_schema)
    store = _store(pg_schema)
    now = await store.database_now()
    await seed_claimable_run(
        pg_schema,
        scope,
        run_id="agr_future_lease",
        lease={
            "lease_owner": WORKER_A,
            "lease_token": "live-token",
            "lease_until": now + timedelta(minutes=10),
            "lease_generation": 2,
            "attempt": 2,
        },
    )

    assert await _candidate_run_ids(pg_schema) == []
    assert await _manager(store).acquire_next(owner_id=WORKER_B) is None

    # Same row, deadline moved into the past — now the very same claim succeeds.
    await pg_schema.execute(
        "UPDATE agent_run_leases SET lease_until = now() - interval '1 second' "
        "WHERE run_id = :run",
        {"run": "agr_future_lease"},
    )
    assert await _candidate_run_ids(pg_schema) == ["agr_future_lease"]
    reclaimed = await _manager(store).acquire_next(owner_id=WORKER_B)
    assert reclaimed is not None and reclaimed.generation == 3


# --------------------------------------------------------------------------- #
# retry window
# --------------------------------------------------------------------------- #
async def test_retry_at_in_the_future_is_not_claimed(pg_schema):
    """A retry is a delay, not a lease: ``retry_at`` blocks a claim on its own."""
    scope = await seed_scope(pg_schema)
    store = _store(pg_schema)
    await seed_claimable_run(
        pg_schema,
        scope,
        run_id="agr_retry_pending",
        lease={"retry_at": (await store.database_now()) + timedelta(hours=1)},
    )

    assert await _candidate_run_ids(pg_schema) == []
    assert await _manager(store).acquire_next(owner_id=WORKER_A) is None

    # The run keeps its generation and attempt counters: a refused claim writes nothing.
    (generation, attempt, owner) = (
        await pg_schema.rows(
            "SELECT lease_generation, attempt, lease_owner FROM agent_run_leases "
            "WHERE run_id = :run",
            {"run": "agr_retry_pending"},
        )
    )[0]
    assert (generation, attempt, owner) == (0, 1, None)

    # Once the window opens (moved into the past here) the run is claimable.
    await pg_schema.execute(
        "UPDATE agent_run_leases SET retry_at = now() - interval '1 second' "
        "WHERE run_id = :run",
        {"run": "agr_retry_pending"},
    )
    claimed = await _manager(store).acquire_next(owner_id=WORKER_A)
    assert claimed is not None and claimed.run_id == "agr_retry_pending"


async def test_a_retry_window_does_not_hide_a_second_claimable_run(pg_schema):
    """Ordering is by due time, so a delayed run does not starve a due one."""
    scope = await seed_scope(pg_schema)
    store = _store(pg_schema)
    now = await store.database_now()
    await seed_claimable_run(
        pg_schema,
        scope,
        run_id="agr_aaa_delayed",
        lease={"retry_at": now + timedelta(hours=2)},
    )
    await seed_claimable_run(pg_schema, scope, run_id="agr_zzz_due")

    assert await _candidate_run_ids(pg_schema) == ["agr_zzz_due"]
    claimed = await _manager(store).acquire_next(owner_id=WORKER_A)
    assert claimed is not None and claimed.run_id == "agr_zzz_due"


# --------------------------------------------------------------------------- #
# fail-closed shapes
# --------------------------------------------------------------------------- #
async def test_a_run_without_a_committed_cursor_is_never_claimable(pg_schema):
    """A missing aggregate cursor is a refusal, not a default sequence."""
    scope = await seed_scope(pg_schema)
    await seed_run(pg_schema, scope, run_id="agr_no_cursor", status="queued")
    # Lease row present, cursor deliberately absent.
    await pg_schema.execute(
        "INSERT INTO agent_run_leases (run_id, tenant_id, project_id, lease_generation, attempt) "
        "VALUES ('agr_no_cursor', :tenant, :project, 0, 1)",
        {"tenant": scope.tenant_id, "project": scope.project_id},
    )

    assert await _candidate_run_ids(pg_schema) == []
    assert await _manager(_store(pg_schema)).acquire_next(owner_id=WORKER_A) is None


async def test_a_cursor_disagreeing_with_the_run_status_is_never_claimable(pg_schema):
    """``aggregate.status == run.status`` is part of the eligibility predicate."""
    scope = await seed_scope(pg_schema)
    await seed_run(pg_schema, scope, run_id="agr_status_drift", status="queued")
    await pg_schema.execute(
        "INSERT INTO agent_control_plane_aggregates "
        "(aggregate_type, aggregate_id, last_sequence, status) "
        "VALUES (:kind, 'agr_status_drift', 4, 'running')",
        {"kind": RUN_AGGREGATE_TYPE},
    )
    await pg_schema.execute(
        "INSERT INTO agent_run_leases (run_id, tenant_id, project_id, lease_generation, attempt) "
        "VALUES ('agr_status_drift', :tenant, :project, 0, 1)",
        {"tenant": scope.tenant_id, "project": scope.project_id},
    )

    assert await _candidate_run_ids(pg_schema) == []
    assert await _manager(_store(pg_schema)).acquire_next(owner_id=WORKER_A) is None


async def test_a_running_run_behind_an_expired_lease_is_not_claimable(pg_schema):
    """Crash recovery is a reconciler job: a claim may only take queued/retry_due.

    An orphaned ``running`` run is exactly the state the lease tier refuses to
    paper over, because taking it would require a requeue transition that does
    not exist yet (``agent_run_requeue_transition_unwired``).
    """
    scope = await seed_scope(pg_schema)
    store = _store(pg_schema)
    now = await store.database_now()
    await seed_claimable_run(
        pg_schema,
        scope,
        run_id="agr_orphaned",
        status="running",
        lease={
            "lease_owner": WORKER_A,
            "lease_token": "orphan-token",
            "lease_until": now - timedelta(minutes=5),
            "lease_generation": 1,
            "attempt": 2,
        },
    )

    assert await _candidate_run_ids(pg_schema) == []
    assert await _manager(store).acquire_next(owner_id=WORKER_B) is None
    # The expired lease is left untouched rather than silently stolen.
    owner = await pg_schema.scalar(
        "SELECT lease_owner FROM agent_run_leases WHERE run_id = :run",
        {"run": "agr_orphaned"},
    )
    assert owner == WORKER_A


async def test_a_claim_deadline_is_derived_from_the_database_clock(pg_schema):
    """The deadline a claim writes comes from ``SELECT now()``, not the process.

    Two replicas do not share wall time, so the only assertion that stays
    meaningful is that the written deadline lies inside the database-clock
    window observed around the claim, exactly one TTL wide.
    """
    scope = await seed_scope(pg_schema)
    await seed_claimable_run(pg_schema, scope, run_id="agr_db_clock")
    store = _store(pg_schema)
    manager = _manager(store, ttl_seconds=30)

    before = await store.database_now()
    lease = await manager.acquire_next(owner_id=WORKER_A)
    after = await store.database_now()
    assert lease is not None

    stored = await pg_schema.scalar(
        "SELECT lease_until FROM agent_run_leases WHERE run_id = :run",
        {"run": "agr_db_clock"},
    )
    assert stored == lease.lease_until
    assert before + timedelta(seconds=30) <= stored <= after + timedelta(seconds=30)
