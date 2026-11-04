"""Durable, generation-fenced ``AgentRunExecutionPort`` composed from real stores.

What this module is
-------------------
``application/agent_runs/lease.py`` owns the *lease policy* over
``RunLeaseStore``; ``application/agent_control_plane`` owns the *transaction*
that writes a run status together with its ordered event, outbox row and queue
intent.  Neither alone is an execution port: a lease without a start event
leaves a run ``queued`` forever, and a start without a lease lets two replicas
drive one run.  This module is the composition, and it is deliberately the only
place that knows how the two tiers interlock.

Claim ordering (why the failure mode is safe)
---------------------------------------------
A port claim performs two durable steps in a fixed order:

1. **lease step** — inside ``claim_scope()`` (one database transaction, one row
   lock): database clock, ``SELECT ... FOR UPDATE SKIP LOCKED`` candidate,
   ``plan_acquisition``, ``cas_acquire``.  ``0`` rows means another worker won;
   the caller gets ``None``.
2. **start step** — ``agent_run.start`` (``queued``/``retry_due`` → ``running``
   state + event + outbox + queue intent, one control-plane transaction) with
   ``expected_sequence = lease.sequence - 1``: the claim *is* that transition, so
   the lease already carries the sequence it commits.

The two steps cannot share one SQL transaction with the current control-plane
port (``commit(write=ControlPlaneAtomicWrite)`` opens its own session), so the
order is chosen so that the only interleaving is harmless:

* the lease is taken first, so a second worker cannot plan against the run;
* if the start step does not apply, the adapter **releases the lease back to
  ``queued``** (generation preserved, token-scoped CAS).  The run therefore
  never ends up ``running`` without a lease, which is the state nothing can
  recover from today (there is no ``agent_run.requeue`` transition);
* if the process dies between the steps, the lease simply expires and the next
  claim increments the generation — the documented expired-lease path.

Every later write is fenced three ways: the lease must still be ours
(``verify_lease``: generation, token, owner, database-clock deadline, status),
the store CAS re-checks generation/token/owner in SQL, and the control-plane
transition compares the aggregate sequence it commits against.

Fail-closed rules encoded here
------------------------------
* A write on a stale generation, an expired lease or a superseded token returns
  ``False`` — it never raises, and it never falls back to a best-effort write.
* ``finish`` accepts only a proven ``succeeded``/``failed`` result; ``unknown``
  must be parked with ``finish_unknown`` and a retry must be scheduled with
  ``schedule_retry`` (which requires a durable ``retry_at``).
* ``finish_unknown`` parks the run without inventing a terminal status: the run
  stays ``running`` with no lease, which blocks any new provider attempt until
  ``reconcile_unknown`` proves the outcome.  Making it ``failed`` here would
  make a later "succeeded" unrepresentable.
* ``cancel_run`` commits the control-plane cancellation and then moves
  ``lease_generation`` forward, so a late provider completion from the old
  worker cannot publish output or reopen the run.
* the control plane re-authorises every write against a live employee, so the
  adapter refuses to claim a run whose actor cannot be resolved instead of
  writing an unattributed transition.

Blockers this adapter does not hide
-----------------------------------
* ``agent_run_requeue_transition_unwired`` (:data:`REQUEUE_BLOCKER`) — there is
  no ``agent_run.requeue`` transition, so this adapter deliberately does **not**
  expose ``release_lease``.  A worker that shuts down after claiming keeps its
  lease until the TTL expires (``plan_release`` documents the same hole).  For
  the same reason ``schedule_retry`` persists the durable ``retry_at`` gate but
  leaves the run ``running``: the retry scheduler must requeue it, and that
  requeue is not wired yet.
* ``agent_run_retry_due_start_transition_unwired`` (:data:`RETRY_DUE_BLOCKER`) —
  ``retry_due`` is in ``CLAIMABLE_STATUSES`` but the adapter cannot start it:
  ``DurableAgentControlPlane.start_run`` CASes from ``queued`` only, even though
  the transition table also permits ``retry_due -> running``.  The adapter
  therefore narrows its claim predicate to ``queued`` in SQL.  A ``retry_due``
  run is therefore not claimed (and does not block the queue) until the retry
  scheduler's requeue — or a ``retry_due`` start on the service API — exists.
* ``agent_run_unknown_case_journal_unwired`` — the ambiguous attempt is parked
  without a row in the reconciliation journal (``agent_runs/reconcile.py`` owns
  that table and no revision exists).  Parking still blocks re-execution, and
  ``reconcile_unknown`` can be driven by an operator.
* ``agent_run_control_plane_actor_unresolved`` — a run whose requester has no
  live employee identity (and no configured service actor) cannot be started,
  finished or cancelled, because the control plane authorises an actor on every
  write.  Such a claim is released and reported as "no claim".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Protocol

from prodavan.application.agent_control_plane.contracts import (
    ControlPlaneLinkage,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
)
from prodavan.application.agent_control_plane.ports import AgentControlPlaneReadPort
from prodavan.application.agent_control_plane.service import DurableAgentControlPlane
from prodavan.application.agent_runs.execution import (
    AgentRunExecutionPort,
    AgentRunLease,
    ProviderExecutionResult,
    ProviderOutcome,
)
from prodavan.application.agent_runs.lease import (
    CLAIMABLE_STATUSES,
    LEASE_REQUEUE_BLOCKER,
    WRITABLE_WHILE_RUNNING,
    DurableRunLeaseManager,
    LeasePolicy,
    LeaseRefusal,
    LeaseRefused,
    LeaseSlot,
    RunLeaseStore,
    verify_lease,
)
from prodavan.domain.agent.control_plane import AgentRunStatus, AgentRunView

logger = logging.getLogger(__name__)

REQUEUE_BLOCKER = LEASE_REQUEUE_BLOCKER
"""No ``agent_run.requeue`` transition exists; release and retry-requeue wait for it."""

UNKNOWN_CASE_JOURNAL_BLOCKER = "agent_run_unknown_case_journal_unwired"
"""The ambiguous attempt is parked durably but not registered in the reconcile journal."""

ACTOR_BLOCKER = "agent_run_control_plane_actor_unresolved"
"""A run with no resolvable employee actor cannot be written by the control plane."""

RETRY_DUE_BLOCKER = "agent_run_retry_due_start_transition_unwired"
"""**Resolved.** Historical code kept here because promotion records cite it.

``retry_due`` was claimable in the lease policy while this adapter could not start
it: the transition table accepted ``retry_due -> running``, but every start went
through ``DurableAgentControlPlane.start_run``, which hard-coded
``expected_status=QUEUED``. A claimed ``retry_due`` row therefore always lost the
status CAS, burning a fence generation per poll.

``start_run`` now takes the observed status as its CAS precondition and the adapter
passes ``slot.status``, so both claimable statuses are startable and
``STARTABLE_STATUSES`` equals ``CLAIMABLE_STATUSES`` again.

What is still missing is the producer: nothing yet moves a run into ``retry_due``
(the retry scheduler / ``agent_run.requeue``), so in practice a run is restarted
from ``queued``. The adapter can start either state.
"""

STARTABLE_STATUSES: frozenset[str] = CLAIMABLE_STATUSES
"""Run statuses this adapter can commit a claim (start transition) for.

Equal to the lease policy's ``CLAIMABLE_STATUSES`` because the control plane can now
express the start for every claimable status: ``start_run`` takes the *observed*
status as its CAS precondition, and the transition table accepts both
``queued -> running`` and ``retry_due -> running``.

An earlier revision narrowed this to ``{queued}`` for a good reason at the time --
``start_run`` hard-coded ``QUEUED``, so a claimed ``retry_due`` row always lost the
status CAS and burned a fence generation per poll. The narrowing was the right
workaround for that defect, but it left retryable runs unclaimable. With the
precondition fixed the workaround is gone; keeping a second, narrower set here would
let the two definitions drift apart again.
"""

DEFAULT_FAILURE_CODE = "agent_run.provider_failed"
RECONCILED_FAILURE_CODE = "agent_run.reconciled_failed"

_RESULT_STATUSES = frozenset(
    {ControlPlaneWriteStatus.APPLIED, ControlPlaneWriteStatus.DUPLICATE}
)
_TERMINAL_BY_OUTCOME = {
    ProviderOutcome.SUCCEEDED: AgentRunStatus.SUCCEEDED,
    ProviderOutcome.FAILED: AgentRunStatus.FAILED,
}


class FencedLeaseStore(RunLeaseStore, Protocol):
    """``RunLeaseStore`` plus the park/fence/claim-scope capabilities.

    The canonical implementation is
    :class:`prodavan.infrastructure.persistence.agent_control_plane.lease_store.SqlAlchemyRunLeaseStore`;
    it is declared here as a protocol so the application tier names the
    capability it needs without importing infrastructure.
    """

    def claim_scope(self) -> AbstractAsyncContextManager[None]:
        """Async context manager pinning one transaction across select + acquire."""
        ...

    async def select_claim_candidate(
        self, *, now: datetime, statuses: frozenset[str] | None = None
    ) -> LeaseSlot | None:
        """``RunLeaseStore`` snapshot, optionally narrowed by ``statuses``."""
        ...

    async def cas_park(
        self,
        *,
        lease: AgentRunLease,
        now: datetime,
        retry_at: datetime | None,
    ) -> int: ...

    async def cas_fence(self, *, tenant_id: str, run_id: str, now: datetime) -> int: ...


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _required(value: str, name: str, *, max_length: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _bounded_code(value: str | None, name: str, *, max_length: int = 128) -> str | None:
    """Validate a redacted machine code; never accept free text or a payload."""
    if value is None:
        return None
    code = _required(value, name, max_length=max_length)
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    if any(character not in allowed for character in code):
        raise ValueError(f"{name} must be a bounded machine code")
    return code


class _StartableOnlyStore:
    """Narrow a lease store to the candidates this adapter can actually start.

    ``DurableRunLeaseManager`` drives the lease policy; this wrapper only
    replaces the eligibility predicate so the manager never plans against a
    ``retry_due`` row whose start transition the control plane cannot express
    (:data:`RETRY_DUE_BLOCKER`).  Everything else is delegated verbatim, so the
    manager still sees exactly one store and the adapter still has exactly one
    place with policy.
    """

    def __init__(self, store: FencedLeaseStore, statuses: frozenset[str]) -> None:
        self._store = store
        self._statuses = statuses

    async def database_now(self) -> datetime:
        return await self._store.database_now()

    async def select_claim_candidate(self, *, now: datetime) -> LeaseSlot | None:
        return await self._store.select_claim_candidate(now=now, statuses=self._statuses)

    async def cas_acquire(
        self, *, slot: LeaseSlot, lease: AgentRunLease, now: datetime
    ) -> int:
        return await self._store.cas_acquire(slot=slot, lease=lease, now=now)

    async def cas_renew(
        self, *, lease: AgentRunLease, now: datetime, lease_until: datetime
    ) -> int:
        return await self._store.cas_renew(lease=lease, now=now, lease_until=lease_until)

    async def cas_release(self, *, lease: AgentRunLease, now: datetime, status: str) -> int:
        return await self._store.cas_release(lease=lease, now=now, status=status)

    async def read_slot(self, *, run_id: str) -> LeaseSlot | None:
        return await self._store.read_slot(run_id=run_id)


class ControlPlaneAgentRunExecutionAdapter:
    """``AgentRunExecutionPort`` over a durable lease store and control plane.

    ``leases`` is the SQL lease store, ``control_plane`` writes run transitions
    with their events, and ``runs`` is the tenant-scoped run read port used to
    resolve the acting employee (the control plane authorises an actor on every
    write) and the run's session linkage.

    ``DurableAgentControlPlane`` is bound to one tenant at construction, so a
    single instance only serves that tenant: pass a ``tenant_id -> control
    plane`` factory for a multi-tenant worker.  A run of another tenant is a
    composition error and fails loudly instead of writing cross-tenant.

    ``runs`` is used for two different reads and satisfies both:
    ``AgentControlPlaneReadPort.get_run`` (the control-plane run view) and
    ``AgentTurnRunner``'s ``RunInputSource.get_run`` (``project_id``,
    ``session_id``, ``requested_by_employee_id``, ``input_payload``), so the same
    tenant-scoped read port can be handed to ``AgentRunWorker`` unchanged.
    """

    def __init__(
        self,
        *,
        leases: FencedLeaseStore,
        control_plane: DurableAgentControlPlane | Callable[[str], DurableAgentControlPlane],
        runs: AgentControlPlaneReadPort,
        lease_policy: LeasePolicy | None = None,
        service_actor_id: str | None = None,
    ) -> None:
        for name in ("database_now", "select_claim_candidate", "cas_acquire", "read_slot"):
            if not callable(getattr(leases, name, None)):
                raise TypeError(f"leases must implement RunLeaseStore.{name}")
        for name in ("claim_scope", "cas_park", "cas_fence"):
            if not callable(getattr(leases, name, None)):
                raise TypeError(f"leases must implement the fenced lease store capability {name}")
        if not isinstance(control_plane, DurableAgentControlPlane) and not callable(control_plane):
            raise TypeError("control_plane must be a DurableAgentControlPlane or a per-tenant factory")
        if not callable(getattr(runs, "get_run", None)):
            raise TypeError("runs must implement AgentControlPlaneReadPort.get_run")
        if lease_policy is not None and not isinstance(lease_policy, LeasePolicy):
            raise TypeError("lease_policy must be a LeasePolicy")
        self._leases = leases
        self._control_plane = control_plane
        self._runs = runs
        self._policy = lease_policy or LeasePolicy()
        self._service_actor_id = (
            _required(service_actor_id, "service_actor_id") if service_actor_id is not None else None
        )

    # -- advertised capabilities -------------------------------------------

    @property
    def is_wired(self) -> bool:
        """A constructed adapter is wired; nothing here degrades to memory."""
        return True

    @property
    def wiring_blocker(self) -> None:
        return None

    @property
    def retry_blocker(self) -> str:
        """Retries are parked durably, but requeueing them is still unwired."""
        return REQUEUE_BLOCKER

    # -- AgentRunExecutionPort: claim --------------------------------------

    async def claim_next(
        self,
        *,
        owner_id: str,
        now: datetime,
        lease_until: datetime,
    ) -> AgentRunLease | None:
        """Claim one eligible run and commit its ``agent_run.start``.

        The lease duration is the caller's window applied to the **database**
        clock: only the duration crosses from the caller's clock domain, never an
        absolute instant.  A window outside ``LeasePolicy`` bounds falls back to
        the configured policy instead of silently clamping.

        Only ``queued`` runs are claimed (:data:`STARTABLE_STATUSES`): ``retry_due``
        is claimable in policy but has no expressible start transition until the
        retry requeue lands (:data:`RETRY_DUE_BLOCKER`).
        """
        owner = _required(owner_id, "owner_id")
        manager = DurableRunLeaseManager(
            store=_StartableOnlyStore(self._leases, STARTABLE_STATUSES),
            policy=self._policy_for(now=now, lease_until=lease_until),
        )
        async with self._leases.claim_scope():
            lease = await manager.acquire_next(owner_id=owner)
        if lease is None:
            return None
        if await self._start(lease):
            return lease
        await self._compensate_failed_start(lease)
        return None

    async def heartbeat(
        self,
        *,
        lease: AgentRunLease,
        now: datetime,
        lease_until: datetime,
    ) -> bool:
        """Renew the lease; ``False`` means the fence is gone (never raises)."""
        _require_lease(lease)
        manager = DurableRunLeaseManager(
            store=self._leases,
            policy=self._policy_for(now=now, lease_until=lease_until),
        )
        check = await manager.renew(lease=lease)
        return check.holds

    # -- AgentRunExecutionPort: settlement ---------------------------------

    async def finish(
        self,
        *,
        lease: AgentRunLease,
        result: ProviderExecutionResult,
        now: datetime,
    ) -> bool:
        """Commit the terminal transition for a proven provider result.

        ``unknown`` and retryable results are refused here: the first must be
        parked for reconciliation, the second scheduled with a durable retry
        time.  Returning ``False`` is the lost-race answer — the caller records
        its result as evidence instead of run state.
        """
        _require_lease(lease)
        if not isinstance(result, ProviderExecutionResult):
            raise TypeError("result must be a ProviderExecutionResult")
        if result.outcome is ProviderOutcome.UNKNOWN:
            raise ValueError("an unknown provider outcome must be parked with finish_unknown")
        if result.outcome is ProviderOutcome.RETRYABLE_FAILURE:
            raise ValueError("a retryable provider failure must be scheduled with schedule_retry")
        _aware(now, "now")
        target = _TERMINAL_BY_OUTCOME[result.outcome]
        db_now, slot = await self._verified_slot(lease)
        if slot is None:
            return False
        linkage = await self._linkage(slot, trace=f"agent-run:{slot.run_id}:g{slot.generation}")
        if linkage is None:
            return False
        if target is AgentRunStatus.SUCCEEDED:
            write = await self._control_plane_for(slot.tenant_id).succeed_run(
                run_id=slot.run_id,
                linkage=linkage,
                expected_sequence=lease.sequence,
                command_idempotency_key=self._key("succeed", lease),
                output_payload=result.output_payload,
                occurred_at=db_now,
            )
        else:
            write = await self._control_plane_for(slot.tenant_id).fail_run(
                run_id=slot.run_id,
                linkage=linkage,
                expected_sequence=lease.sequence,
                expected_status=AgentRunStatus.RUNNING,
                command_idempotency_key=self._key("fail", lease),
                failure_code=_bounded_code(result.failure_code, "failure_code") or DEFAULT_FAILURE_CODE,
                occurred_at=db_now,
            )
        if not _settled(write):
            return False
        # The run is terminal: drop the lease triple so the row cannot look
        # held.  0 rows here is harmless (a canceller already fenced us out).
        await self._leases.cas_release(lease=lease, now=db_now, status=target.value)
        return True

    async def schedule_retry(
        self,
        *,
        lease: AgentRunLease,
        retry_at: datetime,
        failure_code: str,
        now: datetime,
    ) -> bool:
        """Park the run behind a durable ``retry_at`` gate.

        The gate lives on the lease row, so it survives the requeue that the
        retry scheduler performs: a run is not claimable again before
        ``retry_at`` even if its status returns to ``queued`` first.  The failure
        code is validated as a bounded machine code and is deliberately not
        persisted here — the retry-schedule row (with its own failure evidence)
        belongs to the retry slice.
        """
        _require_lease(lease)
        _aware(now, "now")
        due = _aware(retry_at, "retry_at")
        _bounded_code(_required(failure_code, "failure_code"), "failure_code")
        db_now, slot = await self._verified_slot(lease)
        if slot is None:
            return False
        if due <= db_now:
            # Scheduling an already-due retry would make the run claimable in the
            # same instant it was parked; refuse instead of inventing a backoff.
            return False
        rows = await self._leases.cas_park(lease=lease, now=db_now, retry_at=due)
        if rows != 1:
            return False
        logger.warning(
            "agent run parked for retry run=%s generation=%s retry_at=%s blocker=%s",
            slot.run_id,
            slot.generation,
            due.isoformat(),
            REQUEUE_BLOCKER,
        )
        return True

    async def finish_unknown(
        self,
        *,
        lease: AgentRunLease,
        provider_request_id: str | None,
        observed_at: datetime,
    ) -> bool:
        """Park a run whose provider outcome is ambiguous.

        The run stays ``running`` with no lease: nothing may claim it (so no new
        provider attempt can happen) and nothing may call it terminal (so a
        later reconciliation can still publish the real outcome).
        """
        _require_lease(lease)
        _aware(observed_at, "observed_at")
        if provider_request_id is not None:
            # Only shape-checked: it is never persisted here (no journal row yet),
            # so a provider-specific identifier format must not break the park.
            _required(provider_request_id, "provider_request_id", max_length=160)
        db_now, slot = await self._verified_slot(lease)
        if slot is None:
            return False
        rows = await self._leases.cas_park(lease=lease, now=db_now, retry_at=None)
        if rows != 1:
            return False
        logger.warning(
            "agent run parked for reconciliation run=%s generation=%s blocker=%s",
            slot.run_id,
            slot.generation,
            UNKNOWN_CASE_JOURNAL_BLOCKER,
        )
        return True

    async def reconcile_unknown(
        self,
        *,
        tenant_id: str,
        run_id: str,
        generation: int,
        outcome: ProviderExecutionResult,
        now: datetime,
    ) -> bool:
        """Resolve a parked ambiguous attempt from proven evidence.

        The fence is the *generation of the ambiguous attempt*: a cancellation or
        a newer claim moves the generation, and a reconciliation presented with
        the old one is refused.  A run that still holds a live lease is not
        reconcilable — its worker has not finished parking it.
        """
        tenant = _required(tenant_id, "tenant_id")
        run = _required(run_id, "run_id")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("generation must be a positive integer")
        if not isinstance(outcome, ProviderExecutionResult):
            raise TypeError("outcome must be a ProviderExecutionResult")
        if outcome.outcome is ProviderOutcome.UNKNOWN:
            raise ValueError("reconciliation cannot conclude with an unknown outcome")
        if outcome.outcome is ProviderOutcome.RETRYABLE_FAILURE:
            raise ValueError("reconciliation must conclude success or failure; retry is a fresh claim")
        _aware(now, "now")
        db_now = await self._leases.database_now()
        slot = await self._leases.read_slot(run_id=run)
        if slot is None or slot.tenant_id != tenant:
            return False
        if slot.generation != generation:
            return False
        if slot.status != AgentRunStatus.RUNNING.value:
            return False
        if slot.holds_lease:
            return False
        linkage = await self._linkage(slot, trace=f"agent-run-reconcile:{slot.run_id}:g{generation}")
        if linkage is None:
            return False
        if outcome.outcome is ProviderOutcome.SUCCEEDED:
            write = await self._control_plane_for(tenant).succeed_run(
                run_id=slot.run_id,
                linkage=linkage,
                expected_sequence=slot.sequence,
                command_idempotency_key=f"agent_run.reconcile:{slot.run_id}:g{generation}:succeeded",
                output_payload=outcome.output_payload,
                occurred_at=db_now,
            )
        else:
            write = await self._control_plane_for(tenant).fail_run(
                run_id=slot.run_id,
                linkage=linkage,
                expected_sequence=slot.sequence,
                expected_status=AgentRunStatus.RUNNING,
                command_idempotency_key=f"agent_run.reconcile:{slot.run_id}:g{generation}:failed",
                failure_code=_bounded_code(outcome.failure_code, "failure_code") or RECONCILED_FAILURE_CODE,
                occurred_at=db_now,
            )
        if not _settled(write):
            return False
        # Seal the resolved generation so no parked writer can reuse it.
        await self._leases.cas_fence(tenant_id=tenant, run_id=slot.run_id, now=db_now)
        return True

    # -- AgentRunExecutionPort: cancellation -------------------------------

    async def cancel_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        expected_sequence: int,
        expected_status: AgentRunStatus,
        command_idempotency_key: str,
        reason: str | None,
    ) -> bool:
        """Cancel a queued/running run and fence its generation forward.

        Cancellation is a control-plane write (state + event + receipt) followed
        by ``cas_fence``: the generation moves, the lease triple is dropped, and
        any in-flight worker's ``finish`` is refused by both the generation check
        and the aggregate status precondition.
        """
        tenant = _required(tenant_id, "tenant_id")
        project = _required(project_id, "project_id")
        run = _required(run_id, "run_id")
        key = _required(command_idempotency_key, "command_idempotency_key", max_length=512)
        if isinstance(expected_sequence, bool) or not isinstance(expected_sequence, int):
            raise ValueError("expected_sequence must be an int")
        if expected_sequence < 1:
            raise ValueError("expected_sequence must be positive")
        if expected_status not in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING}:
            raise ValueError("only queued or running runs can be cancelled")
        if reason is not None and len(reason) > 2_000:
            raise ValueError("reason exceeds 2000 characters")
        db_now = await self._leases.database_now()
        view = await self._runs.get_run(tenant_id=tenant, run_id=run)
        if view is None or view.project_id != project:
            return False
        actor = self._actor_for(view)
        if actor is None:
            logger.warning("agent run cancel has no resolvable actor run=%s blocker=%s", run, ACTOR_BLOCKER)
            return False
        write = await self._control_plane_for(tenant).cancel_run(
            run_id=run,
            linkage=ControlPlaneLinkage(
                tenant_id=tenant,
                actor_id=actor,
                project_id=project,
                session_id=view.session_id,
                trace_id=f"agent-run-cancel:{run}",
            ),
            expected_sequence=expected_sequence,
            expected_status=expected_status,
            command_idempotency_key=key,
            reason=reason,
            occurred_at=db_now,
        )
        if not _settled(write):
            return False
        await self._leases.cas_fence(tenant_id=tenant, run_id=run, now=db_now)
        return True

    # -- internals ---------------------------------------------------------

    def _control_plane_for(self, tenant_id: str) -> DurableAgentControlPlane:
        """Return the tenant-bound control plane (a factory for multi-tenant hosts)."""
        control_plane = self._control_plane
        if isinstance(control_plane, DurableAgentControlPlane):
            return control_plane
        resolved = control_plane(tenant_id)
        if not isinstance(resolved, DurableAgentControlPlane):
            raise TypeError("control_plane factory must return a DurableAgentControlPlane")
        return resolved

    def _policy_for(self, *, now: datetime, lease_until: datetime) -> LeasePolicy:
        """Derive the claim/renew policy from the caller's requested window.

        Only the *duration* is taken from the caller (skew-free), and it is
        applied to the database clock by the lease manager.  A window outside
        the supported range is not silently clamped: the configured policy wins.
        """
        _aware(now, "now")
        window = _aware(lease_until, "lease_until") - now
        if window <= timedelta(0):
            raise ValueError("lease_until must be after now")
        try:
            return LeasePolicy(ttl=window)
        except ValueError:
            return self._policy

    async def _start(self, lease: AgentRunLease) -> bool:
        """Commit ``agent_run.start`` for a freshly acquired lease."""
        db_now = await self._leases.database_now()
        slot = await self._leases.read_slot(run_id=lease.run_id)
        if slot is None or slot.generation != lease.generation:
            return False
        if slot.status not in STARTABLE_STATUSES:
            logger.warning(
                "agent run claim cannot be started run=%s status=%s blocker=%s",
                slot.run_id,
                slot.status,
                RETRY_DUE_BLOCKER,
            )
            return False
        linkage = await self._linkage(slot, trace=f"agent-run:{slot.run_id}:g{slot.generation}")
        if linkage is None:
            return False
        write = await self._control_plane_for(slot.tenant_id).start_run(
            run_id=slot.run_id,
            linkage=linkage,
            expected_sequence=lease.sequence - 1,
            command_idempotency_key=self._key("claim", lease),
            occurred_at=db_now,
            # The observed status is the CAS precondition. Passing it explicitly is
            # what makes a RETRY_DUE run startable: hard-coding QUEUED here meant the
            # claim acquired a lease and then always lost the status check, burning a
            # fence generation per poll.
            observed_status=AgentRunStatus(slot.status),
        )
        return _settled(write)

    async def _compensate_failed_start(self, lease: AgentRunLease) -> None:
        """Give the lease back when the start transition did not apply.

        ``plan_release``/``cas_release`` keep the generation, so the next claim is
        still ordered after anything this worker could write.  A release that
        cannot be applied (the run moved on anyway) is not an error: the lease
        expires and the new claim increments the generation.
        """
        try:
            manager = DurableRunLeaseManager(store=self._leases, policy=self._policy)
            await manager.release(lease=lease)
        except LeaseRefused as refused:
            logger.warning(
                "agent run claim compensation refused run=%s reason=%s",
                lease.run_id,
                refused.reason.value,
            )
        except Exception:  # noqa: BLE001 — compensation is best effort, never fatal
            logger.exception("agent run claim compensation failed run=%s", lease.run_id)

    async def _verified_slot(self, lease: AgentRunLease) -> tuple[datetime, LeaseSlot | None]:
        """Return ``(database_now, slot)`` when the lease still owns the run."""
        db_now = await self._leases.database_now()
        slot = await self._leases.read_slot(run_id=lease.run_id)
        try:
            verify_lease(slot, lease=lease, now=db_now, allowed_statuses=WRITABLE_WHILE_RUNNING)
        except LeaseRefused:
            return db_now, None
        assert slot is not None  # verify_lease raised otherwise
        if slot.status not in WRITABLE_WHILE_RUNNING or slot.project_id != lease.project_id:
            return db_now, None
        return db_now, slot

    async def _linkage(self, slot: LeaseSlot, *, trace: str) -> ControlPlaneLinkage | None:
        """Build the control-plane linkage for a run, or ``None`` without an actor."""
        view = await self._runs.get_run(tenant_id=slot.tenant_id, run_id=slot.run_id)
        if view is None or view.project_id != slot.project_id:
            return None
        actor = self._actor_for(view)
        if actor is None:
            logger.warning(
                "agent run has no resolvable actor run=%s blocker=%s", slot.run_id, ACTOR_BLOCKER
            )
            return None
        return ControlPlaneLinkage(
            tenant_id=slot.tenant_id,
            actor_id=actor,
            project_id=slot.project_id,
            session_id=slot.session_id,
            trace_id=trace,
        )

    def _actor_for(self, view: AgentRunView) -> str | None:
        """The employee the control plane authorises for this run's writes.

        A run has no separate "system" identity in the control plane: every write
        is attributed to and re-authorised against a live employee, so the
        requester is the only candidate and a configured service actor is the
        explicit fallback.  Without either, nothing is written.
        """
        requester = getattr(view, "requested_by_employee_id", None)
        if requester:
            return _required(requester, "actor_id", max_length=128)
        return self._service_actor_id

    @staticmethod
    def _key(transition: str, lease: AgentRunLease) -> str:
        """Deterministic command key: a replay of one generation is a no-op."""
        return _required(
            f"agent_run.{transition}:{lease.run_id}:g{lease.generation}",
            "command_idempotency_key",
            max_length=512,
        )


def _settled(write: ControlPlaneWriteResult) -> bool:
    """True only when the control plane applied or replayed the transition."""
    if not isinstance(write, ControlPlaneWriteResult):
        raise TypeError("control plane returned an invalid result")
    return write.status in _RESULT_STATUSES


def _require_lease(lease: AgentRunLease) -> AgentRunLease:
    if not isinstance(lease, AgentRunLease):
        raise TypeError("lease must be an AgentRunLease")
    return lease


__all__ = [
    "ACTOR_BLOCKER",
    "ControlPlaneAgentRunExecutionAdapter",
    "DEFAULT_FAILURE_CODE",
    "FencedLeaseStore",
    "RECONCILED_FAILURE_CODE",
    "REQUEUE_BLOCKER",
    "RETRY_DUE_BLOCKER",
    "STARTABLE_STATUSES",
    "UNKNOWN_CASE_JOURNAL_BLOCKER",
]
