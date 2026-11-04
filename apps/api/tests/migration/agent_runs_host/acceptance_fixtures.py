"""Acceptance fakes for MIG-AR-HOST-01 (host wiring of the Agent Run loops).

These tests are **artifacts, not an executed suite**: the migration stage is under
a verification freeze, so nothing here is run.  Everything below is written so its
behaviour can be checked by reading it against the resource contract:

* no fake touches a database, a network or the real agent bootstrap path;
* every fake records what it was asked to do, which is how "the loop did nothing"
  becomes an assertion instead of a claim;
* the secret marker is a fixed literal, so "it never reached the log or the
  report" is a substring check rather than an opinion.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from prodavan.application.agent_runs.dispatcher import DispatchBatchReport
from prodavan.application.agent_runs.worker import WorkerTick, WorkerTickStatus
from prodavan.core.infra.agent_run_worker_resource import (
    AgentRunHostDependencies,
    AgentRunHostSettings,
)

#: Present in an exception message, a prompt and a payload below.  If any of it
#: shows up in a log record or in ``report()``, a secret leaked.
SECRET_MARKER = "sk-live-HOST-MUST-NOT-BE-LOGGED"
PROMPT_MARKER = "customer-private-message-body"

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Session / lock fakes
# ---------------------------------------------------------------------------


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc_info: Any) -> bool:
        return False


class FakeSessionFactory:
    """Counts opened sessions: a fail-closed tick must open none."""

    def __init__(self) -> None:
        self.opened = 0

    def __call__(self) -> FakeSession:
        self.opened += 1
        return FakeSession()


class FakeLockContext:
    def __init__(self, held: bool) -> None:
        self._held = held

    async def __aenter__(self) -> bool:
        return self._held

    async def __aexit__(self, *_exc_info: Any) -> bool:
        return False


class FakeLock:
    """Stand-in for ``core.infra.advisory_lock.advisory_lock``.

    ``held=False`` models the second pod: the key belongs to another process, so
    the tick must refuse to do any work.
    """

    def __init__(self, *, held: bool = True) -> None:
        self.held = held
        self.keys: list[str] = []

    def __call__(self, _session: Any, key: str) -> FakeLockContext:
        self.keys.append(key)
        return FakeLockContext(self.held)


# ---------------------------------------------------------------------------
# Composition fakes
# ---------------------------------------------------------------------------


class FakeExecutionPort:
    """Marker implementation of the fenced execution port.

    The ticks under test never reach a provider: the consumer uses the injected
    dispatcher and the worker uses the injected worker, so this object exists only
    to satisfy the "an execution adapter is assembled" gate.
    """

    async def claim_next(self, *, owner_id: str, now: datetime, lease_until: datetime) -> None:
        return None


class FakeDispatcher:
    def __init__(
        self,
        *,
        report: DispatchBatchReport | None = None,
        error: BaseException | None = None,
        error_times: int = 0,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._report = report
        self._error = error
        self._error_times = error_times

    async def dispatch_batch(
        self,
        *,
        owner_id: str,
        limit: int,
        repair_expired: int = 0,
    ) -> DispatchBatchReport:
        self.calls.append(
            {"owner_id": owner_id, "limit": limit, "repair_expired": repair_expired}
        )
        if self._error is not None and self._error_times != 0:
            self._error_times = max(0, self._error_times - 1)
            raise self._error
        return self._report or DispatchBatchReport(
            claimed=2,
            delivered=1,
            retried=1,
            repaired=3,
        )


class FakeWorker:
    """Stand-in for ``AgentRunWorker`` (``tick`` + ``shutdown`` only)."""

    def __init__(
        self,
        *,
        ticks: list[WorkerTick] | None = None,
        error: BaseException | None = None,
        error_times: int = 0,
    ) -> None:
        self.calls = 0
        self.stopped_with: asyncio.Event | None = None
        self._ticks = list(ticks or [])
        self._error = error
        self._error_times = error_times

    async def tick(self) -> WorkerTick:
        self.calls += 1
        if self._error is not None and self._error_times != 0:
            self._error_times = max(0, self._error_times - 1)
            raise self._error
        if self._ticks:
            return self._ticks.pop(0)
        return WorkerTick(WorkerTickStatus.IDLE)

    async def shutdown(self, *, stop: asyncio.Event) -> None:
        self.stopped_with = stop
        stop.set()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def enabled_config(
    *,
    worker: bool = True,
    consumer: bool = True,
    worker_interval_sec: float = 60.0,
    consumer_interval_sec: float = 60.0,
    worker_batch_max: int = 1,
    consumer_batch_max: int = 4,
) -> AgentRunHostSettings:
    """Opt-in config with long intervals so a test never races the tick sleep."""
    return AgentRunHostSettings(
        worker_enabled=worker,
        worker_interval_sec=worker_interval_sec,
        worker_batch_max=worker_batch_max,
        consumer_enabled=consumer,
        consumer_interval_sec=consumer_interval_sec,
        consumer_batch_max=consumer_batch_max,
    )


def wired_dependencies() -> AgentRunHostDependencies:
    """A complete bundle; the worker is injected separately, so the rest are markers."""
    return AgentRunHostDependencies(
        execution_port=FakeExecutionPort(),
        runs=object(),
        journal=object(),
        runner=object(),
    )


def succeeded_tick(*, run_id: str = "run-1") -> WorkerTick:
    return WorkerTick(
        WorkerTickStatus.SUCCEEDED,
        run_id=run_id,
        generation=1,
        reason="provider reported success with a durable output payload",
        wrote_run_state=True,
    )


# ---------------------------------------------------------------------------
# Async polling helper (no sleeps in assertions, only in the wait)
# ---------------------------------------------------------------------------


async def wait_until(
    predicate: Any,
    *,
    timeout: float = 5.0,
    interval: float = 0.02,
) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())
