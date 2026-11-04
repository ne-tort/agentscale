"""Dependency-bundle assembly for the durable Agent Run host loops (MIG-AR-BUNDLE-01).

Problem this module solves
--------------------------
``core/infra/agent_run_worker_resource.py`` hosts both durable loops and exposes a
single seam, :func:`register_host_dependency_provider`, for the composition the
loops run against.  Its default provider is ``unavailable_host_dependencies()`` —
an entirely empty :class:`AgentRunHostDependencies` — so a delivered process that
enables either flag logs ``agent_run_host_dependencies_unwired`` and idles: no
intent is claimed and no run is ever executed.  Every member of the bundle now
exists in the tree; nothing assembled them, and the seam was called by nobody.

This module is that assembly.  It is a *composition root*, not a fourth copy of
the execution slice: it constructs the four members from one session factory,
using exactly the classes those slices already own, and it answers with a report
that names every member it could not compose instead of raising.

The bundle contract
-------------------
:class:`AgentRunHostDependencies` (``core/infra/agent_run_worker_resource.py``)
has four members, and the map below is the whole of this module's policy:

| Member | Class constructed here | What stays blocked when it is absent |
|---|---|---|
| ``execution_port`` | ``application/agent_runs/execution_adapter.ControlPlaneAgentRunExecutionAdapter`` | **both** loops. The consumer refuses to claim an intent (``agent_run_execution_port_unwired``): claiming without an executor would record ``intent_handler_not_wired`` retries and walk a real handoff to its poison-pill state. The worker publishes the same code. |
| ``runs`` | ``infrastructure/persistence/agent_control_plane/repository.SqlAlchemyAgentControlPlaneRepository`` | the worker (``agent_run_run_input_source_unwired``): a run's ``project_id``/``session_id``/``input_payload`` cannot be read, so no turn can be built. The adapter also needs this same read port to resolve the acting employee, so the execution port is not composed either. |
| ``journal`` | ``application/agent_runs/attempts.ProviderAttemptJournal`` over ``.../attempt_journal.SqlAlchemyProviderAttemptJournal`` | the worker (``agent_run_provider_attempt_journal_unwired``): ``AgentRunWorker.__init__`` refuses to exist without a durable attempt ledger, which is what makes a duplicated provider call harmless. |
| ``runner`` | ``application/agent_runs/worker.SessionAgentTurnRunner`` | the worker (``agent_run_turn_runner_unwired``): nothing can drive ``agent.session_service.start_detached_turn``, i.e. no provider turn happens. |
| *(not a member)* ``lease_store`` | ``.../agent_control_plane/lease_store.SqlAlchemyRunLeaseStore`` | the fenced execution port and the addressed intent pipeline cannot be built at all (``agent_run_lease_store_unwired``), so the consumer loop loses its port as well. |

The lease store is deliberately **not** a bundle member: the contract has four
fields and this task may not change it.  It is reported separately because a
bundle without it is a bundle whose port cannot exist.

Fail-closed assembly
--------------------
:func:`build_agent_run_bundle` **does not raise**.  Each member is composed in its
own guarded step; a constructor that refuses, a missing table-level dependency, an
unimportable adapter or an unreachable settings/database factory leaves that
member ``None`` and adds a named blocker.  There is no in-memory stand-in and no
``if False`` stub: ``None`` *is* the answer, and the host already knows how to
report it (:meth:`AgentRunHostDependencies.worker_blockers` /
:meth:`AgentRunHostDependencies.consumer_blockers`).

The report is *derived* from the dependencies object that is handed to the host,
never tracked beside it, so a caller can never see "wired" in a report and a
``None`` in the bundle.

Install / restore
-----------------
:func:`install_agent_run_host_dependencies` builds the bundle once, keeps it, and
registers one provider that answers with **that same instance** — the resource
resolves the provider once per start, and handing out a freshly built bundle on
every call would silently multiply lease stores and repositories.
:func:`restore_agent_run_host_dependencies` puts the seam back to the fail-closed
default (``register_host_dependency_provider(None)``); both directions are
idempotent, which is what tests and shutdown need.

Deliberately not in this bundle
-------------------------------
* ``application/agent_runs/retry.py::SqlAlchemyAgentRunRetryJournal`` and
  ``application/agent_runs/reconcile.py::SqlAlchemyUnknownOutcomeJournal`` — both
  are one-argument constructions (``session_factory=...``) and both exist, but
  neither is a member of ``AgentRunHostDependencies`` or of
  ``IntentExecutionPipeline``.  They belong to the retry scheduler and the
  reconciler, which have no host loop yet; adding them here would be dead
  configuration.
* Dispatcher *policy* (batch size, retry budget, poison-pill bounds) stays with
  ``agent_run_worker_resource`` / ``agent_runs.dispatcher``.  This module only
  supplies the addressable handler registry (``agent_runs.handler``) that those
  policies dispatch to, because the resource's own fallback registry
  (``build_execution_handlers``) can only register a non-addressable claim and
  ``claim_run`` does not exist on the fenced adapter.

Secrets and payloads
--------------------
Nothing composed here carries a credential, a prompt or a payload: the session
factory is a callable, the actor fallback is an identity, and every failure is
logged as an exception **class** plus a fixed reason code — never a message, which
can embed a DSN.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from prodavan.core.infra.agent_run_worker_resource import (
    BLOCKER_COMPOSITION_FAILED,
    BLOCKER_DEPENDENCIES_UNWIRED,
    BLOCKER_EXECUTION_PORT_UNWIRED,
    BLOCKER_JOURNAL_UNWIRED,
    BLOCKER_RUN_SOURCE_UNWIRED,
    BLOCKER_TURN_RUNNER_UNWIRED,
    CONSUMER_DEFAULT_BATCH_MAX,
    DEFAULT_DISPATCH_MAX_ATTEMPTS,
    INTENT_QUEUE_NAME,
    AgentRunHostDependencies,
    AgentRunWorkerResource,
    agent_run_host_settings_from_settings,
    current_host_dependency_provider,
    register_host_dependency_provider,
    unavailable_host_dependencies,
)

if TYPE_CHECKING:  # pragma: no cover - typing only; runtime imports stay lazy
    from prodavan.application.agent_control_plane.service import DurableAgentControlPlane
    from prodavan.application.agent_runs.dispatcher import DispatchHandler, EnqueueIntentDispatcher, IntentKind
    from prodavan.application.agent_runs.execution import AgentRunExecutionPort
    from prodavan.application.agent_runs.handler import IntentExecutionPipeline
    from prodavan.application.agent_runs.lease import LeasePolicy
    from prodavan.infrastructure.persistence.agent_control_plane.intent_consumer import (
        IntentConsumerSettings,
        SqlAlchemyIntentConsumer,
    )

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: The fenced lease store could not be composed, so neither the execution port nor
#: the addressed intent pipeline exists.
BLOCKER_LEASE_STORE_UNWIRED = "agent_run_lease_store_unwired"
#: The process session factory itself is unusable; nothing can be composed.
BLOCKER_SESSION_FACTORY_UNAVAILABLE = "agent_run_bundle_session_factory_unavailable"

#: Owner stamped on the turn runner's system principal.  Bounded by
#: ``SessionAgentTurnRunner`` at 128 characters; it is an identity, never a token.
DEFAULT_TURN_RUNNER_OWNER = "agent-run-turns"

#: Member order for every report/snapshot in this module: the four contract
#: members in the resource's own order, then the lease store.
MEMBER_ORDER: Final[tuple[str, ...]] = (
    "execution_port",
    "runs",
    "journal",
    "runner",
    "lease_store",
)

#: "The caller did not pass this factory" — distinct from an explicit ``None``,
#: which means "this member is deliberately unavailable".
_UNSET: Final[Any] = object()

__all__ = [
    "BLOCKER_COMPOSITION_FAILED",
    "BLOCKER_DEPENDENCIES_UNWIRED",
    "BLOCKER_LEASE_STORE_UNWIRED",
    "BLOCKER_SESSION_FACTORY_UNAVAILABLE",
    "DEFAULT_TURN_RUNNER_OWNER",
    "MEMBER_ORDER",
    "AgentRunBundle",
    "AgentRunBundleReport",
    "agent_run_host_dependencies_installed",
    "build_agent_run_bundle",
    "build_agent_run_host_dependencies",
    "build_agent_run_host_resource",
    "build_agent_run_intent_dispatcher",
    "build_agent_run_intent_handlers",
    "build_agent_run_intent_pipeline",
    "consumer_lease_seconds",
    "install_agent_run_bundle",
    "install_agent_run_host_dependencies",
    "installed_agent_run_bundle",
    "restore_agent_run_host_dependencies",
]


# ---------------------------------------------------------------------------
# Redacted reasons
# ---------------------------------------------------------------------------


def _safe_reason(exc: BaseException) -> str:
    """Exception *class* as a bounded code; a message can carry a DSN.

    Composition errors are the one place where a connection string, a table name or
    a path can end up inside an exception text, so the only thing that may leave
    this module is the class name, normalised to a code.
    """
    name = type(exc).__name__
    cleaned = "".join(character.lower() if character.isalnum() else "_" for character in name)
    return cleaned.strip("_") or "composition_error"


# ---------------------------------------------------------------------------
# Report (derived from the bundle, never tracked beside it)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentRunBundleReport:
    """Member → named blocker, in a closed vocabulary safe for logs and probes.

    Produced by :attr:`AgentRunBundle.report`; the member blockers are computed
    from the very :class:`AgentRunHostDependencies` the host receives, so this view
    can never disagree with the bundle.  ``worker_blockers``/``consumer_blockers``
    delegate to that object as well: the host's own diagnostics stay the single
    source of truth for "which loop is blocked".
    """

    dependencies: AgentRunHostDependencies
    execution_port_blocker: str | None = None
    runs_blocker: str | None = None
    journal_blocker: str | None = None
    runner_blocker: str | None = None
    lease_store_blocker: str | None = None
    composition_blockers: tuple[str, ...] = ()
    reasons: tuple[tuple[str, str], ...] = ()

    @property
    def blockers(self) -> tuple[str, ...]:
        """Every reason this bundle is not fully assembled (fixed order, deduped)."""
        ordered: list[str] = []
        for blocker in (
            self.execution_port_blocker,
            self.runs_blocker,
            self.journal_blocker,
            self.runner_blocker,
            self.lease_store_blocker,
        ):
            if blocker and blocker not in ordered:
                ordered.append(blocker)
        for blocker in self.composition_blockers:
            if blocker not in ordered:
                ordered.append(blocker)
        return tuple(ordered)

    @property
    def worker_blockers(self) -> tuple[str, ...]:
        """Exactly what ``AgentRunHostDependencies.worker_blockers`` answers."""
        return self.dependencies.worker_blockers

    @property
    def consumer_blockers(self) -> tuple[str, ...]:
        """Exactly what ``AgentRunHostDependencies.consumer_blockers`` answers."""
        return self.dependencies.consumer_blockers

    @property
    def any_wired(self) -> bool:
        """The host's rule: at least one of the four members is assembled."""
        return self.dependencies.any_wired

    @property
    def wired(self) -> bool:
        """Strictly assembled: all four members, the lease store, no failure."""
        return not self.blockers

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe copy of the report (no callables, no payloads)."""
        members = {
            "execution_port": self.execution_port_blocker,
            "runs": self.runs_blocker,
            "journal": self.journal_blocker,
            "runner": self.runner_blocker,
            "lease_store": self.lease_store_blocker,
        }
        return {
            "wired": self.wired,
            "any_wired": self.any_wired,
            "blockers": list(self.blockers),
            "members": {name: members[name] for name in MEMBER_ORDER},
            "worker_blockers": list(self.worker_blockers),
            "consumer_blockers": list(self.consumer_blockers),
            "reasons": dict(self.reasons),
        }


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentRunBundle:
    """One composed host composition plus everything needed to audit it.

    ``dependencies`` is the only thing the host consumes.  The other fields exist so
    the intent pipeline can be built from the *same* parts (the addressed handler
    must hand the adapter a narrowed lease store, so it needs the adapter factory
    rather than a pre-built port) and so a test can assert what was composed without
    reaching into private state.
    """

    dependencies: AgentRunHostDependencies
    lease_store: Any | None = None
    session_factory: Callable[[], Any] | None = None
    execution_factory: Callable[[Any], Any] | None = None
    queue_name: str = INTENT_QUEUE_NAME
    composition_blockers: tuple[str, ...] = ()
    reasons: tuple[tuple[str, str], ...] = ()

    @property
    def report(self) -> AgentRunBundleReport:
        dependencies = self.dependencies
        return AgentRunBundleReport(
            dependencies=dependencies,
            execution_port_blocker=(
                None if dependencies.execution_port is not None else BLOCKER_EXECUTION_PORT_UNWIRED
            ),
            runs_blocker=None if dependencies.runs is not None else BLOCKER_RUN_SOURCE_UNWIRED,
            journal_blocker=None if dependencies.journal is not None else BLOCKER_JOURNAL_UNWIRED,
            runner_blocker=None if dependencies.runner is not None else BLOCKER_TURN_RUNNER_UNWIRED,
            lease_store_blocker=None if self.lease_store is not None else BLOCKER_LEASE_STORE_UNWIRED,
            composition_blockers=self.composition_blockers,
            reasons=self.reasons,
        )

    @property
    def any_wired(self) -> bool:
        return self.dependencies.any_wired

    @property
    def wired(self) -> bool:
        return self.report.wired

    def snapshot(self) -> dict[str, Any]:
        return {
            "queue_name": self.queue_name,
            "any_wired": self.any_wired,
            **self.report.snapshot(),
        }


# ---------------------------------------------------------------------------
# Assembly (one guarded step per member, no exception escapes)
# ---------------------------------------------------------------------------


def _as_attempt_journal(value: Any) -> Any:
    """Accept a journal port and return the application-tier journal around it.

    ``AgentRunWorker`` requires the ``ProviderAttemptJournal`` *wrapper* (it is the
    object that enforces the state machine), while the infrastructure store is a
    port.  An injected factory may hand over either; a value that is neither is a
    composition failure, not something to wrap blindly.
    """
    from prodavan.application.agent_runs.attempts import ProviderAttemptJournal

    if isinstance(value, ProviderAttemptJournal):
        return value
    if callable(getattr(value, "cas_finish", None)):
        return ProviderAttemptJournal(port=value)
    raise TypeError("journal must be a ProviderAttemptJournal or a ProviderAttemptJournalPort")


class _Assembly:
    """A single composition attempt.

    Every member is built in its own guarded step.  The class keeps three things:
    the resolved session factory, the lazily built control-plane repository (shared
    by the run read port and the control-plane factory so one process does not
    multiply repositories), and the list of composition-level codes.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None,
        control_plane: Any,
        queue_name: str,
        service_actor_id: str | None,
        lease_policy: LeasePolicy | None,
        runner_owner_id: str,
        lease_store_factory: Any,
        runs_factory: Any,
        journal_factory: Any,
        runner_factory: Any,
        execution_factory: Any,
    ) -> None:
        self._session_factory_arg = session_factory
        self._control_plane_arg = control_plane
        self._queue_name = queue_name
        self._service_actor_id = service_actor_id
        self._lease_policy = lease_policy
        self._runner_owner_id = runner_owner_id
        self._lease_store_factory = lease_store_factory
        self._runs_factory = runs_factory
        self._journal_factory = journal_factory
        self._runner_factory = runner_factory
        self._execution_factory_arg = execution_factory
        self._session_factory: Callable[[], Any] | None = None
        self._cached_repository: Any | None = None
        self._repository_built = False
        self._composition: list[str] = []
        self._failures: dict[str, str] = {}

    # -- diagnostics -------------------------------------------------------

    @property
    def failures(self) -> dict[str, str]:
        """Member/reason pairs, redacted (exception class or ``unavailable``)."""
        return dict(self._failures)

    def _compose(self, code: str) -> None:
        if code not in self._composition:
            self._composition.append(code)

    def _attempt(self, member: str, thunk: Callable[[], Any]) -> Any | None:
        """Run one constructor; a refusal becomes a code, never an exception."""
        try:
            value = thunk()
        except Exception as exc:  # noqa: BLE001 - composition failure is data here
            reason = _safe_reason(exc)
            self._failures[member] = reason
            self._compose(BLOCKER_COMPOSITION_FAILED)
            logger.warning(
                "agent run bundle: %s composition failed error=%s code=%s",
                member,
                reason,
                BLOCKER_COMPOSITION_FAILED,
            )
            return None
        if value is None:
            self._failures[member] = "unavailable"
            return None
        return value

    # -- session factory ---------------------------------------------------

    def _resolve_session_factory(self) -> None:
        """Resolve the process factory once; an unusable one is a blocker.

        ``get_session_factory`` builds the engine lazily, so an invalid or absent
        ``database_url`` surfaces here — as ``agent_run_bundle_session_factory_unavailable``
        rather than as a startup crash that would take the whole API down for a
        background loop.
        """
        if self._session_factory_arg is not None:
            if not callable(self._session_factory_arg):
                # An object that merely *looks* like a factory would compose four
                # members that fail on their first call; refuse it here instead.
                self._failures["session_factory"] = "not_callable"
                self._compose(BLOCKER_SESSION_FACTORY_UNAVAILABLE)
                logger.warning(
                    "agent run bundle: session factory is not callable code=%s",
                    BLOCKER_SESSION_FACTORY_UNAVAILABLE,
                )
                return
            self._session_factory = self._session_factory_arg
            return
        try:
            from prodavan.infrastructure.persistence.database import get_session_factory

            self._session_factory = get_session_factory()
        except Exception as exc:  # noqa: BLE001 - an unusable factory is a blocker
            self._session_factory = None
            self._failures["session_factory"] = _safe_reason(exc)
            logger.warning(
                "agent run bundle: session factory unavailable error=%s code=%s",
                _safe_reason(exc),
                BLOCKER_SESSION_FACTORY_UNAVAILABLE,
            )
        if self._session_factory is None:
            self._compose(BLOCKER_SESSION_FACTORY_UNAVAILABLE)

    # -- members -----------------------------------------------------------

    def _lease_store(self) -> Any | None:
        if self._session_factory is None or self._lease_store_factory is None:
            return None
        if self._lease_store_factory is _UNSET:
            return self._attempt("lease_store", self._default_lease_store)
        factory = self._lease_store_factory
        return self._attempt("lease_store", lambda: factory(self._session_factory))

    def _default_lease_store(self) -> Any:
        from prodavan.infrastructure.persistence.agent_control_plane.lease_store import (
            SqlAlchemyRunLeaseStore,
        )

        return SqlAlchemyRunLeaseStore(session_factory=self._session_factory)

    def _repository(self) -> Any | None:
        """The tenant-scoped control-plane repository, shared by reads and writes."""
        if self._repository_built:
            return self._cached_repository
        self._repository_built = True
        if self._session_factory is None:
            return None

        def build() -> Any:
            from prodavan.infrastructure.persistence.agent_control_plane.repository import (
                SqlAlchemyAgentControlPlaneRepository,
            )

            return SqlAlchemyAgentControlPlaneRepository(session_factory=self._session_factory)

        self._cached_repository = self._attempt("repository", build)
        return self._cached_repository

    def _runs(self) -> Any | None:
        """Run projection source; the repository satisfies both read contracts."""
        if self._session_factory is None or self._runs_factory is None:
            return None
        if self._runs_factory is _UNSET:
            return self._repository()
        factory = self._runs_factory
        return self._attempt("runs", lambda: factory(self._session_factory))
    def _journal(self) -> Any | None:
        if self._session_factory is None or self._journal_factory is None:
            return None
        if self._journal_factory is _UNSET:
            return self._attempt("journal", self._default_journal)
        factory = self._journal_factory
        return self._attempt(
            "journal", lambda: _as_attempt_journal(factory(self._session_factory))
        )

    def _default_journal(self) -> Any:
        from prodavan.infrastructure.persistence.agent_control_plane.attempt_journal import (
            SqlAlchemyProviderAttemptJournal,
        )

        return _as_attempt_journal(
            SqlAlchemyProviderAttemptJournal(session_factory=self._session_factory)
        )

    def _runner(self) -> Any | None:
        if self._session_factory is None or self._runner_factory is None:
            return None
        if self._runner_factory is _UNSET:
            return self._attempt("runner", self._default_runner)
        factory = self._runner_factory
        return self._attempt("runner", lambda: factory(self._session_factory))

    def _default_runner(self) -> Any:
        from prodavan.application.agent_runs.worker import SessionAgentTurnRunner

        return SessionAgentTurnRunner(
            owner_id=self._runner_owner_id,
            session_factory=self._session_factory,
        )

    # -- execution port ----------------------------------------------------

    def _control_plane(self) -> Any | None:
        """A tenant-bound control plane, or the caller's instance/factory verbatim.

        ``DurableAgentControlPlane`` is bound to one tenant at construction, so a
        multi-tenant worker needs a factory; the default therefore returns one, over
        the single repository this assembly already owns.
        """
        if self._control_plane_arg is not _UNSET:
            return self._control_plane_arg
        repository = self._repository()
        if repository is None:
            return None
        queue_name = self._queue_name

        def factory(tenant_id: str) -> DurableAgentControlPlane:
            from prodavan.application.agent_control_plane.service import DurableAgentControlPlane

            return DurableAgentControlPlane(
                transaction=repository,
                tenant_id=tenant_id,
                queue_name=queue_name,
            )

        return factory

    def _execution_factory(self, *, runs: Any | None) -> Callable[[Any], Any] | None:
        """Adapter factory over an arbitrary (possibly narrowed) lease store.

        The intent handler must hand the adapter a lease store whose only candidate
        is the addressed run, so the port cannot be built here once and reused: the
        factory is the reusable part, and :attr:`AgentRunBundle.execution_factory`
        is exactly it.
        """
        injected = self._execution_factory_arg
        if injected is not _UNSET:
            if injected is None:
                return None
            if not callable(injected):
                self._failures["execution_factory"] = "not_callable"
                self._compose(BLOCKER_COMPOSITION_FAILED)
                logger.warning(
                    "agent run bundle: execution_factory is not callable code=%s",
                    BLOCKER_COMPOSITION_FAILED,
                )
                return None
            return injected
        if runs is None:
            return None
        control_plane = self._control_plane()
        if control_plane is None:
            return None
        lease_policy = self._lease_policy
        service_actor_id = self._service_actor_id

        def factory(lease_store: Any) -> AgentRunExecutionPort:
            from prodavan.application.agent_runs.execution_adapter import (
                ControlPlaneAgentRunExecutionAdapter,
            )

            return ControlPlaneAgentRunExecutionAdapter(
                leases=lease_store,
                control_plane=control_plane,
                runs=runs,
                lease_policy=lease_policy,
                service_actor_id=service_actor_id,
            )

        return factory

    # -- entry point -------------------------------------------------------

    def build(self) -> AgentRunBundle:
        self._resolve_session_factory()
        lease_store = self._lease_store()
        runs = self._runs()
        journal = self._journal()
        runner = self._runner()
        execution_factory = self._execution_factory(runs=runs)

        execution_port: Any | None = None
        if execution_factory is not None and lease_store is not None:
            execution_port = self._attempt(
                "execution_port", lambda: execution_factory(lease_store)
            )
            if execution_port is not None and not callable(
                getattr(execution_port, "claim_next", None)
            ):
                # A factory that returns something else would satisfy `is not None`
                # and then fail every tick: refuse it here, where it is a code.
                self._failures["execution_port"] = "not_an_execution_port"
                self._compose(BLOCKER_COMPOSITION_FAILED)
                logger.warning(
                    "agent run bundle: execution_port is not a claimable port code=%s",
                    BLOCKER_COMPOSITION_FAILED,
                )
                execution_port = None

        dependencies = AgentRunHostDependencies(
            execution_port=execution_port,
            runs=runs,
            journal=journal,
            runner=runner,
        )
        return AgentRunBundle(
            dependencies=dependencies,
            lease_store=lease_store,
            session_factory=self._session_factory,
            execution_factory=execution_factory,
            queue_name=self._queue_name,
            composition_blockers=tuple(self._composition),
            reasons=tuple(sorted(self.failures.items())),
        )


def build_agent_run_bundle(
    *,
    session_factory: Callable[[], Any] | None = None,
    control_plane: Any = _UNSET,
    queue_name: str = INTENT_QUEUE_NAME,
    service_actor_id: str | None = None,
    lease_policy: LeasePolicy | None = None,
    runner_owner_id: str = DEFAULT_TURN_RUNNER_OWNER,
    lease_store_factory: Any = _UNSET,
    runs_factory: Any = _UNSET,
    journal_factory: Any = _UNSET,
    runner_factory: Any = _UNSET,
    execution_factory: Any = _UNSET,
) -> AgentRunBundle:
    """Compose the whole host composition from one session factory.

    Never raises.  Every keyword is an explicit seam:

    ``session_factory``
        The process ``async_sessionmaker``.  ``None`` resolves
        ``database.get_session_factory()``; if that is unavailable the bundle comes
        back empty with ``agent_run_bundle_session_factory_unavailable``.
    ``control_plane``
        A ``DurableAgentControlPlane`` instance, a ``tenant_id -> control plane``
        factory, or ``None`` to declare it unavailable.  Omitted (the default) builds
        the tenant factory over the shared repository.
    ``service_actor_id``
        Fallback actor for runs whose requester has no live employee identity.  It is
        an identity, never a credential; ``None`` means "the requester or nothing",
        which the adapter reports per run as
        ``agent_run_control_plane_actor_unresolved``.
    ``lease_policy``, ``queue_name``, ``runner_owner_id``
        Passed through to the adapter, the control plane and the turn runner.
    ``lease_store_factory`` / ``runs_factory`` / ``journal_factory`` / ``runner_factory``
        ``callable(session_factory) -> member``.  Omitted composes the real class;
        ``None`` declares that member deliberately unavailable (tests and hosts with
        a partial composition), and the report names the matching blocker.
    ``execution_factory``
        ``callable(lease_store) -> AgentRunExecutionPort``.  Omitted builds the fenced
        adapter over ``control_plane``, the run read port and the lease policy;
        ``None`` declares the port unavailable.  A factory that returns something
        without ``claim_next`` is refused here rather than at the first tick.

    The result always carries a :class:`AgentRunBundle`, so a caller can inspect
    ``bundle.report`` even when nothing was assembled.
    """
    assembly = _Assembly(
        session_factory=session_factory,
        control_plane=control_plane,
        queue_name=queue_name,
        service_actor_id=service_actor_id,
        lease_policy=lease_policy,
        runner_owner_id=runner_owner_id,
        lease_store_factory=lease_store_factory,
        runs_factory=runs_factory,
        journal_factory=journal_factory,
        runner_factory=runner_factory,
        execution_factory=execution_factory,
    )
    return assembly.build()


def build_agent_run_host_dependencies(**kwargs: Any) -> AgentRunHostDependencies:
    """The four-member contract the host loops consume, and nothing else.

    Thin wrapper over :func:`build_agent_run_bundle` — the same assembly, without
    the audit surface.  A caller that also wants the report (or the intent pipeline)
    should keep the bundle instead.
    """
    return build_agent_run_bundle(**kwargs).dependencies


# ---------------------------------------------------------------------------
# Addressable intent execution (same parts, different fence)
# ---------------------------------------------------------------------------


def _resolve_bundle(bundle: AgentRunBundle | None, kwargs: dict[str, Any]) -> AgentRunBundle:
    if bundle is not None:
        if not isinstance(bundle, AgentRunBundle):
            raise TypeError("bundle must be an AgentRunBundle")
        if kwargs:
            raise TypeError("pass either a bundle or composition kwargs, not both")
        return bundle
    return build_agent_run_bundle(**kwargs)


def build_agent_run_intent_pipeline(
    bundle: AgentRunBundle | None = None,
    **kwargs: Any,
) -> IntentExecutionPipeline:
    """Assemble ``IntentExecutionPipeline`` from a bundle's own parts.

    The pipeline is what ``agent_runs.handler.build_run_intent_handlers`` needs to
    execute an *addressed* intent: the same lease store, run source, attempt journal
    and turn runner, plus the adapter factory so every dispatch builds its port over
    the narrowed lease store (``IntentAddressedLeaseStore``).

    Never raises: a pipeline that cannot be validated comes back empty, which the
    handler reports as the four ``agent_run_intent_*_unwired`` blockers and retries —
    it never claims the intent was delivered.
    """
    resolved = _resolve_bundle(bundle, kwargs)
    from prodavan.application.agent_runs.handler import IntentExecutionPipeline

    try:
        return IntentExecutionPipeline(
            lease_store=resolved.lease_store,
            execution_factory=resolved.execution_factory,
            runs=resolved.dependencies.runs,
            journal=resolved.dependencies.journal,
            runner=resolved.dependencies.runner,
        )
    except Exception as exc:  # noqa: BLE001 - an empty pipeline is the fail-closed answer
        logger.warning(
            "agent run bundle: intent pipeline refused error=%s; every intent will retry",
            _safe_reason(exc),
        )
        return IntentExecutionPipeline()


def build_agent_run_intent_handlers(
    bundle: AgentRunBundle | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    terminal_kinds: Iterable[IntentKind] = (),
    **kwargs: Any,
) -> dict[IntentKind, DispatchHandler]:
    """The registry ``EnqueueIntentDispatcher`` takes: ``agent_run.created`` → executor.

    ``agent_runs.handler.build_run_intent_handlers`` registers one handler for
    ``EXECUTABLE_INTENT_KINDS`` (``agent_run.created``); every other kind is an
    already-applied transition whose delivery is an external projection, so it is
    left unregistered (it retries) unless the caller names it in ``terminal_kinds``,
    which parks it with a terminal refusal instead of a day of useless retries.

    Misconfiguration raises (an executable kind in ``terminal_kinds``, a negative
    retry budget): this is a composition root call, not a per-tick path, and guessing
    what the caller meant would be the opposite of fail-closed.
    """
    resolved = _resolve_bundle(bundle, kwargs)
    from prodavan.application.agent_runs.handler import (
        build_run_intent_handlers,
        build_terminal_refusal_handlers,
    )

    registry: dict[IntentKind, DispatchHandler] = dict(
        build_run_intent_handlers(
            pipeline=build_agent_run_intent_pipeline(resolved),
            clock=clock,
        )
    )
    terminal = tuple(terminal_kinds)
    if terminal:
        registry.update(build_terminal_refusal_handlers(terminal))
    return registry


def consumer_lease_seconds(batch_max: int) -> float:
    """Lease that one whole claim batch can plausibly finish inside.

    Delegated to the host resource's own sizing (30 s + 3 s per intent, capped at
    900 s) instead of re-derived: ``SqlAlchemyIntentConsumer.claim`` stamps one
    ``lease_until`` on every row of a batch and an expired ``dispatching`` row is
    quarantined rather than retried, so two copies of that formula would drift into
    silently destroying deliverable intents.
    """
    from prodavan.core.infra.agent_run_worker_resource import _consumer_lease_seconds

    return _consumer_lease_seconds(int(batch_max))


def build_agent_run_intent_dispatcher(
    bundle: AgentRunBundle | None = None,
    *,
    settings: IntentConsumerSettings | None = None,
    max_attempts: int = DEFAULT_DISPATCH_MAX_ATTEMPTS,
    clock: Callable[[], datetime] | None = None,
    terminal_kinds: Iterable[IntentKind] = (),
    **kwargs: Any,
) -> tuple[EnqueueIntentDispatcher, SqlAlchemyIntentConsumer]:
    """A dispatcher whose registry executes intents, unlike the default one.

    ``intent_consumer.build_intent_dispatcher`` registers
    ``build_execution_handlers(execution)``, i.e. ``AgentRunExecutionHandler``, which
    requires ``port.claim_run`` — a method the fenced adapter deliberately does not
    have (``AgentRunExecutionHandler`` documents the refusal).  Composed that way the
    consumer loop claims every intent and retries it until the poison-pill bound,
    which is exactly the "silent loss of work" the host resource refuses to accept.

    This builder composes the same consumer and scope resolver with the *addressed*
    registry from :func:`build_agent_run_intent_handlers`, so a delivered intent runs
    a real turn through ``AgentRunWorker``.

    Raises ``ValueError`` when the bundle has no session factory: there is no
    headless dispatcher to fall back to, and a dispatcher over a missing database is
    worse than a named refusal at composition time.
    """
    resolved = _resolve_bundle(bundle, kwargs)
    session_factory = resolved.session_factory
    if session_factory is None:
        raise ValueError(BLOCKER_SESSION_FACTORY_UNAVAILABLE)

    handlers = build_agent_run_intent_handlers(
        resolved,
        clock=clock,
        terminal_kinds=terminal_kinds,
    )
    from prodavan.application.agent_runs.dispatcher import EnqueueIntentDispatcher
    from prodavan.infrastructure.persistence.agent_control_plane.intent_consumer import (
        IntentConsumerSettings,
        SqlAlchemyIntentConsumer,
        SqlAlchemyIntentScopeResolver,
    )

    consumer_settings = settings or IntentConsumerSettings(max_attempts=max_attempts)
    consumer = SqlAlchemyIntentConsumer(
        session_factory=session_factory,
        queue_name=resolved.queue_name,
        settings=consumer_settings,
    )
    dispatcher = EnqueueIntentDispatcher(
        consumer=consumer,
        resolver=SqlAlchemyIntentScopeResolver(session_factory=session_factory),
        execution=resolved.dependencies.execution_port,
        handlers=handlers,
        max_attempts=max_attempts,
    )
    logger.info(
        "agent run bundle: intent dispatcher composed queue=%s kinds=%s",
        resolved.queue_name,
        sorted(kind.value for kind in dispatcher.registered_kinds),
    )
    return dispatcher, consumer


def build_agent_run_host_resource(
    *,
    config: Any = None,
    consumer_settings: IntentConsumerSettings | None = None,
    terminal_kinds: Iterable[IntentKind] = (),
    install: bool = True,
    **kwargs: Any,
) -> AgentRunWorkerResource:
    """A host resource wired with the bundle *and* the addressable dispatcher.

    This is the one-line composition entry for ``core/wiring.py``: it builds the
    bundle, publishes it on the seam (unless ``install=False``), composes the
    intent dispatcher over the addressed handler registry, and returns the resource
    with both injected.  The loops are still opt-in through their own settings flags
    — this function starts nothing by itself.

    ``consumer_settings`` defaults to the resource's own lease sizing for
    ``config.consumer_batch_max`` intents with ``DEFAULT_DISPATCH_MAX_ATTEMPTS`` retry
    budget, so an injected dispatcher cannot be handed a smaller lease than the
    resource would have chosen for itself.
    """
    resolved_config = config
    if resolved_config is None:
        resolved_config = agent_run_host_settings_from_settings()
    bundle = build_agent_run_bundle(**kwargs)
    if install:
        install_agent_run_bundle(bundle)

    settings = consumer_settings
    if settings is None:
        from prodavan.infrastructure.persistence.agent_control_plane.intent_consumer import (
            IntentConsumerSettings,
        )

        batch_max = int(
            getattr(resolved_config, "consumer_batch_max", CONSUMER_DEFAULT_BATCH_MAX)
            or CONSUMER_DEFAULT_BATCH_MAX
        )
        settings = IntentConsumerSettings(
            lease_seconds=consumer_lease_seconds(batch_max),
            max_attempts=DEFAULT_DISPATCH_MAX_ATTEMPTS,
        )

    dispatcher: Any | None = None
    try:
        dispatcher, _consumer = build_agent_run_intent_dispatcher(
            bundle,
            settings=settings,
            terminal_kinds=terminal_kinds,
        )
    except Exception as exc:  # noqa: BLE001 - a host without a dispatcher still runs the worker
        logger.warning(
            "agent run bundle: intent dispatcher composition failed error=%s; "
            "the consumer loop will fall back to the resource's own registry",
            _safe_reason(exc),
        )
        dispatcher = None

    return AgentRunWorkerResource(
        config=resolved_config,
        dependencies=bundle.dependencies,
        dispatcher=dispatcher,
        session_factory=bundle.session_factory,
        queue_name=bundle.queue_name,
    )


# ---------------------------------------------------------------------------
# Install / restore (the only module state in this file)
# ---------------------------------------------------------------------------


_installed_bundle: AgentRunBundle | None = None


def _bundle_provider(bundle: AgentRunBundle) -> Callable[[], AgentRunHostDependencies]:
    """One provider per install, always answering with the same dependencies.

    The resource resolves the provider once per start, but an admin tool or a second
    resource may call it again: rebuilding the bundle on every call would create a
    second lease store and a second repository for one process, and would make
    "installed" mean something different on each call.
    """
    dependencies = bundle.dependencies

    def provider() -> AgentRunHostDependencies:
        return dependencies

    return provider


def install_agent_run_bundle(bundle: AgentRunBundle) -> AgentRunBundle:
    """Publish an already built bundle on the host seam (idempotent)."""
    if not isinstance(bundle, AgentRunBundle):
        raise TypeError("bundle must be an AgentRunBundle")
    global _installed_bundle
    register_host_dependency_provider(_bundle_provider(bundle))
    _installed_bundle = bundle
    report = bundle.report
    logger.info(
        "agent run bundle: installed wired=%s any_wired=%s blockers=%s",
        report.wired,
        report.any_wired,
        list(report.blockers),
    )
    return bundle


def install_agent_run_host_dependencies(**kwargs: Any) -> AgentRunBundle:
    """Build the bundle and install it as the host's dependency provider.

    The single call a composition root needs; safe to repeat (the newest bundle
    replaces the previous one) and safe with an empty result (the host then reports
    ``agent_run_host_dependencies_unwired`` exactly as it does today).
    """
    return install_agent_run_bundle(build_agent_run_bundle(**kwargs))


def restore_agent_run_host_dependencies() -> None:
    """Put the seam back to the fail-closed default (idempotent).

    ``register_host_dependency_provider(None)`` re-installs
    ``unavailable_host_dependencies``, which is byte-for-byte the pristine module
    default: both loops report ``agent_run_host_dependencies_unwired`` and claim
    nothing.  Tests need it to prove that enabling a flag alone starts nothing;
    shutdown needs it so a torn-down process cannot keep answering with a bundle
    whose stores belong to a disposed engine.
    """
    global _installed_bundle
    register_host_dependency_provider(None)
    _installed_bundle = None
    logger.info("agent run bundle: withdrawn; the host is back to the fail-closed default")


def installed_agent_run_bundle() -> AgentRunBundle | None:
    """The bundle this process installed, or ``None`` (for probes and tests)."""
    return _installed_bundle


def agent_run_host_dependencies_installed() -> bool:
    """``True`` only while the seam answers with a real composition.

    Compares the live provider with the fail-closed default by identity: that is the
    same check the resource cannot make for itself, because it only sees the result.
    """
    return current_host_dependency_provider() is not unavailable_host_dependencies
