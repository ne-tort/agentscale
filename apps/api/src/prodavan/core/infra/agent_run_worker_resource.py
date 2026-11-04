"""Host wiring for the durable Agent Run background loops (MIG-AR-HOST-01).

Problem this module solves
--------------------------
``application/agent_runs`` holds the dispatcher, the run worker, the lease
manager, the retry scheduler and the reconciler, and
``infrastructure/persistence/agent_control_plane/intent_consumer.py`` holds the
durable consumer for ``agent_control_plane_enqueue_intents``.  All of it is
library code: a library cannot create a task in the process that hosts it, so a
delivered API process never claimed an intent and never executed a run.  The
queue looked healthy (rows piled up) while nothing consumed it.

This module *is* the host.  It is a :class:`LifespanResource` that owns exactly
two asyncio loops:

* **consumer loop** — one bounded round of ``EnqueueIntentDispatcher.dispatch_batch``
  per tick: claim due intents, resolve scope from persisted rows, reserve a
  delivery receipt, run the registered handler, record the redacted outcome;
* **run worker loop** — one bounded round of ``AgentRunWorker.tick`` per tick:
  claim an eligible run, drive the real agent turn, settle exactly one terminal
  transition (or refuse to write at all).

Both follow the ``application/agent/trigger_worker.py`` discipline:

* opt-in through settings, **disabled by default**;
* one bounded round per tick, never an unbounded drain;
* the tick runs under a PostgreSQL advisory lock, so two pods do not run the
  same pipeline step at the same time;
* the interval comes from settings and is waited on through the ``stop`` event,
  so shutdown is immediate instead of "after the next sleep";
* an exception inside a tick is logged and the loop continues — a broken tick
  must never silently kill the only consumer of the queue.

Why a process advisory lock *and* the durable leases
---------------------------------------------------
The authoritative fences stay where they already are: the consumer claims with
``SELECT ... FOR UPDATE SKIP LOCKED`` under a database-clock lease, and the run
worker holds a per-run ``lease_generation``/``lease_token``.  Those are what make
a stale, duplicated or preempted actor harmless.  The advisory lock is coarser on
purpose and does not replace them: it keeps two pods from interleaving the same
pipeline (double repair, double sweep) and it is a *lock*, not a lease, so a pod
that dies releases it together with its session.

Fail-closed dependencies
------------------------
The loops need a composition owned by the execution slice: a fenced
``AgentRunExecutionPort`` adapter, a run projection source, a durable provider
attempt journal and a turn runner.  Enabling a flag before that composition
exists must neither crash the process nor consume queue rows:

* a tick with missing dependencies logs one blocker code and idles;
* no session is opened, no intent is claimed, nothing is written.

Claiming intents without an execution adapter would not be "harmless progress":
the dispatcher would record ``intent_handler_not_wired`` retries and walk a real
handoff to its terminal poison-pill state, which is a silent loss of work.  Idle
is the honest answer, and it is visible in :meth:`AgentRunWorkerResource.report`.

Integration seam
----------------
:func:`register_host_dependency_provider` is the single place the execution
adapter slice plugs its composition in; ``AgentRunWorkerResource(dependencies=…)``
does the same explicitly for tests and admin tools.  Nothing here imports an
adapter, so this module cannot create an import cycle with the layer that will
provide one.

Observability
-------------
:meth:`AgentRunWorkerResource.report` returns the last tick time, tick/error
counters and the redacted per-tick counters for both loops — the state a health
probe or an operator needs, which a bare ``bool`` cannot express.  Nothing on
that path (or in the logs) can carry a prompt, a payload, an identifier tuple, a
credential or an exception message: ticks return closed counter keys, failures log
the exception *class* only, and every audit-ish string is a fixed reason code.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prodavan.core.infra.advisory_lock import advisory_lock
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (codes, bounds, defaults)
# ---------------------------------------------------------------------------

LOOP_CONSUMER = "intent_consumer"
LOOP_WORKER = "run_worker"

#: Queue the control plane writes its enqueue intents to.  The consumer must poll
#: the same queue or the durable handoff is never claimed; the writer's value is
#: ``prodavan.api.migration_agent_runs.dependencies.INTENT_QUEUE_NAME`` and
#: ``DurableAgentControlPlane``'s default.  It is duplicated here (not imported)
#: because ``core`` must not depend on the API layer.
INTENT_QUEUE_NAME = "agent-control-plane"

#: Advisory lock keys.  Static constants, never user input (see ``advisory_lock``).
CONSUMER_LOCK_KEY = "prodavan.agent_run.intent_consumer"
WORKER_LOCK_KEY = "prodavan.agent_run.run_worker"

OWNER_PREFIX = "agent-run-host"
OWNER_MAX = 128
OWNER_INSTANCE_MAX = 16

#: Bounds.  ``MAX_CONSUMER_BATCH`` mirrors ``dispatcher.MAX_BATCH_SIZE`` (a claim
#: round is refused above it), ``MAX_WORKER_BATCH`` bounds how many claim→execute
#: iterations one tick may start.
MAX_CONSUMER_BATCH = 200
MAX_WORKER_BATCH = 25
MIN_INTERVAL_SEC = 1.0
MAX_INTERVAL_SEC = 3600.0

CONSUMER_DEFAULT_INTERVAL_SEC = 5.0
CONSUMER_DEFAULT_BATCH_MAX = 25
WORKER_DEFAULT_INTERVAL_SEC = 15.0
WORKER_DEFAULT_BATCH_MAX = 5

#: Dispatch retry budget while a handler is not wired.  Mirrors the API
#: composition (``migration_agent_runs.dependencies.DISPATCH_MAX_ATTEMPTS``): with
#: the 300 s backoff cap this is roughly a day of retries before a poison-pill
#: refusal, which is the window a rollout of the execution adapter fits into.
DEFAULT_DISPATCH_MAX_ATTEMPTS = 288

#: How long shutdown waits for a loop to finish its current tick before it is
#: cancelled.  Must exceed ``WorkerConfig.shutdown_grace`` (20 s) so a tick that
#: is draining an in-flight turn can settle it instead of being cut off.
SHUTDOWN_TIMEOUT_SEC = 30.0

BLOCKER_DEPENDENCIES_UNWIRED = "agent_run_host_dependencies_unwired"
BLOCKER_EXECUTION_PORT_UNWIRED = "agent_run_execution_port_unwired"
BLOCKER_RUN_SOURCE_UNWIRED = "agent_run_run_input_source_unwired"
BLOCKER_JOURNAL_UNWIRED = "agent_run_provider_attempt_journal_unwired"
BLOCKER_TURN_RUNNER_UNWIRED = "agent_run_turn_runner_unwired"
BLOCKER_WORKER_COMPOSITION_FAILED = "agent_run_worker_composition_failed"

REASON_OK = "ok"
REASON_IDLE = "idle"
REASON_SKIPPED = "skipped"
#: Another pod (or another loop generation) holds the advisory lock right now.
REASON_LOCK_HELD = "advisory_lock_held"
REASON_TICK_FAILED = "tick_failed"

#: Closed set of keys a tick result may expose.  Both the stored last-tick
#: snapshot and the debug log line are filtered through this set, so a future
#: tick that starts returning something richer cannot leak it by accident.
SAFE_RESULT_KEYS: frozenset[str] = frozenset(
    {
        "skipped",
        "reason",
        "claimed",
        "delivered",
        "retried",
        "rejected",
        "quarantined",
        "lease_lost",
        "repaired",
        "ticks",
        "wrote_run_state",
        "statuses",
    }
)


# ---------------------------------------------------------------------------
# Settings projection
# ---------------------------------------------------------------------------


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _bounded_float(value: Any, default: float, low: float, high: float, name: str) -> float:
    """Clamp a timing knob instead of failing the process.

    A wrong interval is a misconfiguration, not a fatal error: an API that
    refuses to boot because a background poll interval was ``0`` would take the
    whole product down for a worker.  The clamp is logged loudly once at config
    build time so the mistake is still visible.
    """
    parsed = _number(value, default)
    clamped = min(high, max(low, parsed))
    if clamped != parsed:
        logger.warning("agent run host: %s=%r clamped to %s", name, value, clamped)
    return clamped


def _bounded_int(value: Any, default: int, low: int, high: int, name: str) -> int:
    parsed = int(_number(value, float(default)))
    clamped = min(high, max(low, parsed))
    if clamped != parsed:
        logger.warning("agent run host: %s=%r clamped to %s", name, value, clamped)
    return clamped


def _flag(source: Any, name: str, default: bool = False) -> bool:
    value = getattr(source, name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True, slots=True)
class AgentRunHostSettings:
    """The six knobs this resource reads, normalised and bounded.

    Deliberately value-only: no URLs, no credentials, no feature secrets.  The
    host loop has no network dependency of its own — every remote call belongs to
    an adapter that already owns its configuration.
    """

    worker_enabled: bool = False
    worker_interval_sec: float = WORKER_DEFAULT_INTERVAL_SEC
    worker_batch_max: int = WORKER_DEFAULT_BATCH_MAX
    consumer_enabled: bool = False
    consumer_interval_sec: float = CONSUMER_DEFAULT_INTERVAL_SEC
    consumer_batch_max: int = CONSUMER_DEFAULT_BATCH_MAX

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "worker_interval_sec",
            _bounded_float(
                self.worker_interval_sec,
                WORKER_DEFAULT_INTERVAL_SEC,
                MIN_INTERVAL_SEC,
                MAX_INTERVAL_SEC,
                "agent_run_worker_interval_sec",
            ),
        )
        object.__setattr__(
            self,
            "worker_batch_max",
            _bounded_int(
                self.worker_batch_max,
                WORKER_DEFAULT_BATCH_MAX,
                1,
                MAX_WORKER_BATCH,
                "agent_run_worker_batch_max",
            ),
        )
        object.__setattr__(
            self,
            "consumer_interval_sec",
            _bounded_float(
                self.consumer_interval_sec,
                CONSUMER_DEFAULT_INTERVAL_SEC,
                MIN_INTERVAL_SEC,
                MAX_INTERVAL_SEC,
                "agent_run_consumer_interval_sec",
            ),
        )
        object.__setattr__(
            self,
            "consumer_batch_max",
            _bounded_int(
                self.consumer_batch_max,
                CONSUMER_DEFAULT_BATCH_MAX,
                1,
                MAX_CONSUMER_BATCH,
                "agent_run_consumer_batch_max",
            ),
        )

    @property
    def any_enabled(self) -> bool:
        return bool(self.worker_enabled or self.consumer_enabled)


def agent_run_host_settings_from_settings(source: Any = None) -> AgentRunHostSettings:
    """Read the six flags from the application settings object.

    ``getattr`` with defaults keeps this usable with a settings double that
    predates the fields: a missing knob means "disabled", never a crash.
    """
    if source is None:
        from prodavan.config.settings import settings as app_settings

        source = app_settings
    return AgentRunHostSettings(
        worker_enabled=_flag(source, "agent_run_worker_enabled"),
        worker_interval_sec=_number(
            getattr(source, "agent_run_worker_interval_sec", WORKER_DEFAULT_INTERVAL_SEC),
            WORKER_DEFAULT_INTERVAL_SEC,
        ),
        worker_batch_max=int(
            _number(
                getattr(source, "agent_run_worker_batch_max", WORKER_DEFAULT_BATCH_MAX),
                WORKER_DEFAULT_BATCH_MAX,
            )
        ),
        consumer_enabled=_flag(source, "agent_run_consumer_enabled"),
        consumer_interval_sec=_number(
            getattr(source, "agent_run_consumer_interval_sec", CONSUMER_DEFAULT_INTERVAL_SEC),
            CONSUMER_DEFAULT_INTERVAL_SEC,
        ),
        consumer_batch_max=int(
            _number(
                getattr(source, "agent_run_consumer_batch_max", CONSUMER_DEFAULT_BATCH_MAX),
                CONSUMER_DEFAULT_BATCH_MAX,
            )
        ),
    )


# ---------------------------------------------------------------------------
# Dependency bundle + registration seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentRunHostDependencies:
    """Everything the two loops need, resolved once at startup.

    Every member is a protocol implementation owned by another slice.  ``None``
    is not "not needed" — it is "not assembled yet", and it is reported as a
    blocker code rather than worked around.
    """

    execution_port: Any | None = None
    runs: Any | None = None
    journal: Any | None = None
    runner: Any | None = None

    @property
    def consumer_blockers(self) -> tuple[str, ...]:
        """The consumer needs an execution handler registry, i.e. the port."""
        if self.execution_port is None:
            return (BLOCKER_EXECUTION_PORT_UNWIRED,)
        return ()

    @property
    def worker_blockers(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.execution_port is None:
            missing.append(BLOCKER_EXECUTION_PORT_UNWIRED)
        if self.runs is None:
            missing.append(BLOCKER_RUN_SOURCE_UNWIRED)
        if self.journal is None:
            missing.append(BLOCKER_JOURNAL_UNWIRED)
        if self.runner is None:
            missing.append(BLOCKER_TURN_RUNNER_UNWIRED)
        return tuple(missing)


HostDependencyProvider = Callable[[], AgentRunHostDependencies]


def unavailable_host_dependencies() -> AgentRunHostDependencies:
    """Default provider: nothing is assembled (fail-closed, no in-memory stand-in)."""
    return AgentRunHostDependencies()


_host_dependency_provider: HostDependencyProvider = unavailable_host_dependencies


def register_host_dependency_provider(provider: HostDependencyProvider | None) -> None:
    """Install (or clear) the composition the loops run against.

    The execution adapter slice calls this once at composition time.  Passing
    ``None`` restores the fail-closed default, which is what tests need to prove
    that enabling a flag alone starts nothing.
    """
    global _host_dependency_provider
    _host_dependency_provider = provider or unavailable_host_dependencies


def current_host_dependency_provider() -> HostDependencyProvider:
    return _host_dependency_provider


# ---------------------------------------------------------------------------
# Loop state (observability, no payloads)
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _redacted(result: dict[str, Any]) -> dict[str, Any]:
    """Keep only the closed counter vocabulary, whatever a tick returned."""
    safe: dict[str, Any] = {}
    for key in SAFE_RESULT_KEYS:
        if key not in result:
            continue
        value = result[key]
        if key == "statuses" and isinstance(value, dict):
            safe[key] = {
                str(name): int(count)
                for name, count in value.items()
                if isinstance(count, int) and not isinstance(count, bool)
            }
        elif isinstance(value, bool):
            safe[key] = value
        elif isinstance(value, (int, float)):
            safe[key] = value
        elif isinstance(value, str) and len(value) <= 64:
            safe[key] = value
    return safe


@dataclass(slots=True)
class _LoopState:
    """One loop's runtime state; the only thing health and logs read from."""

    name: str
    enabled: bool
    interval_sec: float
    batch_max: int
    lock_key: str
    started_at: datetime | None = None
    last_tick_at: datetime | None = None
    last_reason: str | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    ticks: int = 0
    errors: int = 0
    last_error_class: str | None = None
    blockers: tuple[str, ...] = ()
    task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def blocked(self) -> bool:
        return bool(self.blockers)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "blocked": self.blocked,
            "blockers": list(self.blockers),
            "interval_sec": self.interval_sec,
            "batch_max": self.batch_max,
            "started_at": _iso(self.started_at),
            "last_tick_at": _iso(self.last_tick_at),
            "last_reason": self.last_reason,
            "last_result": dict(self.last_result),
            "ticks": self.ticks,
            "errors": self.errors,
            "last_error_class": self.last_error_class,
        }


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


class AgentRunWorkerResource(LifespanResource):
    """Hosts the durable Agent Run consumer and run worker loops.

    Registered once in ``core/wiring.py`` after the database resource.  With both
    flags off (the default) :meth:`startup` creates no task, no session and no
    provider lookup, so the resource is inert in every environment that has not
    opted in.
    """

    def __init__(
        self,
        *,
        config: AgentRunHostSettings | None = None,
        dependencies: AgentRunHostDependencies | None = None,
        dispatcher: Any | None = None,
        run_worker: Any | None = None,
        session_factory: Callable[[], Any] | None = None,
        lock: Callable[[Any, str], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        owner_id: str | None = None,
        queue_name: str = INTENT_QUEUE_NAME,
    ) -> None:
        self._config = config or agent_run_host_settings_from_settings()
        self._deps = dependencies
        self._session_factory = session_factory
        self._lock = lock or advisory_lock
        self._clock = clock or (lambda: datetime.now(UTC))
        self._instance = (owner_id or uuid.uuid4().hex)[:OWNER_INSTANCE_MAX]
        self._queue_name = queue_name
        self._consumer = _LoopState(
            name=LOOP_CONSUMER,
            enabled=self._config.consumer_enabled,
            interval_sec=self._config.consumer_interval_sec,
            batch_max=self._config.consumer_batch_max,
            lock_key=CONSUMER_LOCK_KEY,
        )
        self._worker = _LoopState(
            name=LOOP_WORKER,
            enabled=self._config.worker_enabled,
            interval_sec=self._config.worker_interval_sec,
            batch_max=self._config.worker_batch_max,
            lock_key=WORKER_LOCK_KEY,
        )
        self._stop: asyncio.Event | None = None
        self._started = False
        # Injected compositions are kept separately so a shutdown can release the
        # lazily built ones and restore the explicit ones for a restart.
        self._injected_dispatcher = dispatcher
        self._injected_run_worker = run_worker
        self._dispatcher: Any = dispatcher
        self._run_worker: Any = run_worker
        self._composition_error: str | None = None
        self._blocker_logged: set[str] = set()

    # -- LifespanResource --------------------------------------------------

    @property
    def name(self) -> str:
        return "agent_run_host"

    @property
    def enabled(self) -> bool:
        return self._config.any_enabled

    async def startup(self) -> None:
        if not self.enabled:
            logger.info(
                "agent run host: disabled worker=%s consumer=%s",
                self._config.worker_enabled,
                self._config.consumer_enabled,
            )
            return

        self._stop = asyncio.Event()
        self._resolve_dependencies()
        self._started = True

        for loop in self._loops():
            if not loop.enabled:
                continue
            loop.blockers = self._blockers_for(loop)
            if loop.blocked:
                # Deliberately not fatal: an enabled flag without an assembled
                # adapter must leave the process healthy and the queue untouched.
                self._log_blocker(loop)
            loop.started_at = self._now()
            loop.task = asyncio.create_task(
                self._serve(loop), name=f"prodavan-agent-run-{loop.name}"
            )

    async def shutdown(self) -> None:
        stop = self._stop
        if not self._started or stop is None:
            self._started = False
            return
        stop.set()
        for loop in self._loops():
            await self._await_loop(loop)
        # A cancelled tick may still hold a run lease.  ``AgentRunWorker.shutdown``
        # is idempotent: it sets the same stop signal and drains whatever is left,
        # so the lease is released instead of being left to its TTL.  A blocked
        # loop never claimed anything, so there is no lease to drain.
        worker = self._run_worker
        if worker is not None and self._worker.enabled and not self._worker.blocked:
            try:
                await worker.shutdown(stop=stop)
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                logger.warning(
                    "agent run worker drain failed error=%s", type(exc).__qualname__
                )
        self._dispatcher = self._injected_dispatcher
        self._run_worker = self._injected_run_worker
        self._composition_error = None
        self._stop = None
        self._started = False
        logger.info("agent run host: stopped")

    async def health(self) -> bool | None:
        """``None`` when disabled; ``False`` when enabled but not actually serving.

        A loop that idles on a missing dependency is *alive*: reporting ``False``
        there would fail readiness for every deployment that enables a flag ahead
        of its adapter, which is exactly the failure mode this task forbids.  The
        blocker itself is visible in :meth:`report`.
        """
        if not self.enabled:
            return None
        if not self._started:
            return False
        for loop in self._loops():
            if loop.enabled and not loop.running:
                return False
        return True

    # -- public diagnostics ------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Last tick, counters and blockers for both loops (no payloads)."""
        return {
            "resource": self.name,
            "enabled": self.enabled,
            "started": self._started,
            "queue_name": self._queue_name,
            "loops": {
                LOOP_CONSUMER: self._consumer.snapshot(),
                LOOP_WORKER: self._worker.snapshot(),
            },
        }

    # -- one bounded round (also the seam acceptance checks drive) ---------

    async def drain_consumer_once(self) -> dict[str, Any]:
        """One bounded consumer round under the advisory lock."""
        blockers = self._blockers_for(self._consumer)
        if blockers:
            self._log_blocker(self._consumer)
            return {"skipped": True, "reason": blockers[0], "claimed": 0}

        dispatcher = self._intent_dispatcher()
        if dispatcher is None:
            reason = self._composition_error or BLOCKER_WORKER_COMPOSITION_FAILED
            return {"skipped": True, "reason": reason, "claimed": 0}

        batch = self._consumer.batch_max
        factory = self._factory()
        async with factory() as session:
            async with self._lock(session, CONSUMER_LOCK_KEY) as held:
                if not held:
                    return {"skipped": True, "reason": REASON_LOCK_HELD, "claimed": 0}
                report = await dispatcher.dispatch_batch(
                    owner_id=self._owner_id(LOOP_CONSUMER),
                    limit=batch,
                    # Bounded repair of leases whose owner died.  The consumer is
                    # the only actor that can repair this queue, and both
                    # statements are self-guarding, so a concurrent repair by
                    # another pod (or an operator drain) updates nothing.
                    repair_expired=batch,
                )
        return {
            "skipped": False,
            "reason": REASON_OK,
            "claimed": int(report.claimed),
            "delivered": int(report.delivered),
            "retried": int(report.retried),
            "rejected": int(report.rejected),
            "quarantined": int(report.quarantined),
            "lease_lost": int(report.lease_lost),
            "repaired": int(report.repaired),
        }

    async def run_worker_round_once(self) -> dict[str, Any]:
        """One bounded run-worker round (claim → execute → settle) under the lock."""
        blockers = self._blockers_for(self._worker)
        if blockers:
            self._log_blocker(self._worker)
            return {"skipped": True, "reason": blockers[0], "claimed": 0, "ticks": 0}

        worker = self._worker_instance()
        if worker is None:
            reason = self._composition_error or BLOCKER_WORKER_COMPOSITION_FAILED
            return {"skipped": True, "reason": reason, "claimed": 0, "ticks": 0}

        from prodavan.application.agent_runs.worker import run_worker_once

        factory = self._factory()
        async with factory() as session:
            async with self._lock(session, WORKER_LOCK_KEY) as held:
                if not held:
                    return {"skipped": True, "reason": REASON_LOCK_HELD, "claimed": 0, "ticks": 0}
                ticks = await run_worker_once(worker, max_ticks=self._worker.batch_max)

        statuses: dict[str, int] = {}
        claimed = 0
        wrote_run_state = 0
        for tick in ticks:
            statuses[tick.status.value] = statuses.get(tick.status.value, 0) + 1
            claimed += 1 if tick.claimed else 0
            wrote_run_state += 1 if tick.wrote_run_state else 0
        return {
            "skipped": False,
            "reason": REASON_IDLE if not ticks else REASON_OK,
            "ticks": len(ticks),
            "claimed": claimed,
            "wrote_run_state": wrote_run_state,
            "statuses": statuses,
        }

    # -- loop driver -------------------------------------------------------

    def _loops(self) -> tuple[_LoopState, ...]:
        return (self._consumer, self._worker)

    async def _serve(self, loop: _LoopState) -> None:
        tick = self.drain_consumer_once if loop.name == LOOP_CONSUMER else self.run_worker_round_once
        logger.info(
            "agent run %s loop started interval=%ss batch=%s",
            loop.name,
            loop.interval_sec,
            loop.batch_max,
        )
        try:
            while self._stop is not None and not self._stop.is_set():
                loop.ticks += 1
                loop.last_tick_at = self._now()
                try:
                    result = await tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - one bad tick must not stop the loop
                    # Class name only: an exception message can carry a run
                    # identifier, a prompt fragment or a connection string.
                    loop.errors += 1
                    loop.last_error_class = type(exc).__qualname__
                    loop.last_reason = REASON_TICK_FAILED
                    loop.last_result = {}
                    logger.warning(
                        "agent run %s loop tick failed error=%s (message withheld)",
                        loop.name,
                        loop.last_error_class,
                    )
                else:
                    skipped = bool(result.get("skipped"))
                    loop.last_reason = str(
                        result.get("reason") or (REASON_SKIPPED if skipped else REASON_OK)
                    )
                    loop.last_result = _redacted(result)
                    if not skipped:
                        logger.debug(
                            "agent run %s loop tick result=%s", loop.name, loop.last_result
                        )
                if not await self._wait_next(loop.interval_sec):
                    break
        finally:
            logger.info(
                "agent run %s loop stopped ticks=%s errors=%s",
                loop.name,
                loop.ticks,
                loop.errors,
            )

    async def _wait_next(self, interval_sec: float) -> bool:
        """Wait out the interval; ``False`` means shutdown was requested."""
        stop = self._stop
        if stop is None:
            return False
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_sec)
        except TimeoutError:
            return True
        return False

    async def _await_loop(self, loop: _LoopState) -> None:
        task = loop.task
        if task is None:
            return
        try:
            done, _pending = await asyncio.wait({task}, timeout=SHUTDOWN_TIMEOUT_SEC)
            if not done:
                logger.warning(
                    "agent run %s loop did not stop within %ss; cancelling",
                    loop.name,
                    SHUTDOWN_TIMEOUT_SEC,
                )
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            else:
                try:
                    task.result()
                except asyncio.CancelledError:
                    logger.warning("agent run %s loop was cancelled", loop.name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "agent run %s loop ended with error=%s",
                        loop.name,
                        type(exc).__qualname__,
                    )
        finally:
            loop.task = None

    # -- dependency resolution --------------------------------------------

    def _resolve_dependencies(self) -> None:
        if self._deps is not None:
            return
        provider = current_host_dependency_provider()
        try:
            self._deps = provider()
        except Exception as exc:  # noqa: BLE001 - composition failure must stay closed
            self._deps = None
            logger.warning(
                "agent run host: dependency provider failed error=%s", type(exc).__qualname__
            )

    def _blockers_for(self, loop: _LoopState) -> tuple[str, ...]:
        deps = self._deps
        if deps is None:
            return (BLOCKER_DEPENDENCIES_UNWIRED,)
        if loop.name == LOOP_CONSUMER:
            return deps.consumer_blockers
        return deps.worker_blockers

    def _log_blocker(self, loop: _LoopState) -> None:
        if not loop.blockers:
            return
        key = f"{loop.name}:{loop.blockers[0]}"
        if key in self._blocker_logged:
            return
        self._blocker_logged.add(key)
        logger.warning(
            "agent run %s loop idle — dependencies not assembled blocker=%s",
            loop.name,
            loop.blockers[0],
        )

    # -- composition -------------------------------------------------------

    def _factory(self) -> Callable[[], Any]:
        if self._session_factory is not None:
            return self._session_factory
        from prodavan.infrastructure.persistence.database import get_session_factory

        return get_session_factory()

    def _intent_dispatcher(self) -> Any:
        if self._dispatcher is not None:
            return self._dispatcher
        deps = self._deps
        if deps is None or deps.execution_port is None:
            return None
        try:
            from prodavan.infrastructure.persistence.agent_control_plane.intent_consumer import (
                IntentConsumerSettings,
                build_intent_dispatcher,
            )

            dispatcher, _consumer = build_intent_dispatcher(
                session_factory=self._factory(),
                queue_name=self._queue_name,
                execution=deps.execution_port,
                settings=IntentConsumerSettings(),
                max_attempts=DEFAULT_DISPATCH_MAX_ATTEMPTS,
            )
        except Exception as exc:  # noqa: BLE001 - a broken composition idles, never crashes
            self._composition_error = BLOCKER_WORKER_COMPOSITION_FAILED
            logger.warning(
                "agent run intent dispatcher composition failed error=%s",
                type(exc).__qualname__,
            )
            return None
        self._dispatcher = dispatcher
        return self._dispatcher

    def _worker_instance(self) -> Any:
        if self._run_worker is not None:
            return self._run_worker
        if self._composition_error is not None:
            return None
        deps = self._deps
        if deps is None:
            return None
        try:
            from prodavan.application.agent_runs.worker import AgentRunWorker, WorkerConfig

            self._run_worker = AgentRunWorker(
                port=deps.execution_port,
                runs=deps.runs,
                journal=deps.journal,
                runner=deps.runner,
                config=WorkerConfig(owner_id=self._owner_id(LOOP_WORKER)),
                clock=self._clock,
            )
        except Exception as exc:  # noqa: BLE001 - WorkerUnavailable included
            self._composition_error = BLOCKER_WORKER_COMPOSITION_FAILED
            logger.warning(
                "agent run worker composition failed error=%s", type(exc).__qualname__
            )
            return None
        return self._run_worker

    # -- helpers -----------------------------------------------------------

    def _owner_id(self, loop_name: str) -> str:
        return f"{OWNER_PREFIX}:{self._instance}:{loop_name}"[:OWNER_MAX]

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value


__all__ = [
    "AgentRunHostDependencies",
    "AgentRunHostSettings",
    "AgentRunWorkerResource",
    "BLOCKER_DEPENDENCIES_UNWIRED",
    "BLOCKER_EXECUTION_PORT_UNWIRED",
    "BLOCKER_JOURNAL_UNWIRED",
    "BLOCKER_RUN_SOURCE_UNWIRED",
    "BLOCKER_TURN_RUNNER_UNWIRED",
    "CONSUMER_LOCK_KEY",
    "INTENT_QUEUE_NAME",
    "LOOP_CONSUMER",
    "LOOP_WORKER",
    "REASON_LOCK_HELD",
    "REASON_TICK_FAILED",
    "SHUTDOWN_TIMEOUT_SEC",
    "WORKER_LOCK_KEY",
    "agent_run_host_settings_from_settings",
    "current_host_dependency_provider",
    "register_host_dependency_provider",
    "unavailable_host_dependencies",
]
