"""Shared fakes for the MIG-AR-BUNDLE-01 acceptance artifacts.

The rules these fakes encode:

* **No constructor in the composition may touch a database.** Every store in the
  bundle stores its session factory and opens a session per method, so a factory
  that hands out a disposable double is enough to assemble the real bundle.  The
  fixtures assert that by counting *opened* sessions: assembly must open zero.
* **A missing member is expressed as ``None``/a raising factory, never as a
  half-built stand-in.** That is the property the bundle's report is judged on.
* **The marker is a fixed literal**, so "the exception message never reached a
  log line or the report" is a substring check rather than an opinion.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prodavan.core.infra.agent_run_bundle import AgentRunBundle
from prodavan.core.infra.agent_run_worker_resource import (
    BLOCKER_EXECUTION_PORT_UNWIRED,
    BLOCKER_JOURNAL_UNWIRED,
    BLOCKER_RUN_SOURCE_UNWIRED,
    BLOCKER_TURN_RUNNER_UNWIRED,
)

#: Present in every fake composition error below.  If it appears in a log record or
#: in a report, a message (which can carry a DSN) leaked instead of a class name.
SECRET_MARKER = "postgresql://user:sk-live-BUNDLE-MUST-NOT-BE-LOGGED@db/prod"

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Session factory double
# ---------------------------------------------------------------------------


class FakeSession:
    """Disposable session double; nothing in the bundle opens one at assembly."""

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc_info: Any) -> bool:
        return False

    async def close(self) -> None:  # pragma: no cover - never reached here
        return None


class FakeSessionFactory:
    """Callable factory that counts opens, so "assembly touched no database" is provable."""

    def __init__(self) -> None:
        self.opened = 0

    def __call__(self) -> FakeSession:
        self.opened += 1
        return FakeSession()


class ExplodingSessionFactory:
    """A factory whose *resolution* fails, e.g. an unparsable ``database_url``."""

    def __init__(self, message: str = SECRET_MARKER) -> None:
        self.message = message
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        raise RuntimeError(self.message)


# ---------------------------------------------------------------------------
# Dependency doubles for injected seams
# ---------------------------------------------------------------------------


class FakeLeaseStore:
    """Marker lease store with the full fenced capability surface.

    ``ControlPlaneAgentRunExecutionAdapter.__init__`` duck-types seven methods on the
    store before it accepts it, so a marker that implements none of them would make
    the *substitution* look like a composition failure.  The methods raise
    ``AssertionError`` because assembly must never call them — the adapter only
    validates their presence here.
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory

    def claim_scope(self) -> Any:  # pragma: no cover - presence check only
        raise AssertionError("assembly must not open a claim scope")

    async def database_now(self) -> Any:  # pragma: no cover
        raise AssertionError("assembly must not read the database clock")

    async def select_claim_candidate(self, **_kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("assembly must not select a candidate")

    async def read_slot(self, **_kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("assembly must not read a slot")

    async def cas_acquire(self, **_kwargs: Any) -> int:  # pragma: no cover
        raise AssertionError("assembly must not acquire")

    async def cas_renew(self, **_kwargs: Any) -> int:  # pragma: no cover
        raise AssertionError("assembly must not renew")

    async def cas_release(self, **_kwargs: Any) -> int:  # pragma: no cover
        raise AssertionError("assembly must not release")

    async def cas_park(self, **_kwargs: Any) -> int:  # pragma: no cover
        raise AssertionError("assembly must not park")

    async def cas_fence(self, **_kwargs: Any) -> int:  # pragma: no cover
        raise AssertionError("assembly must not fence")


class FakeRunSource:
    async def get_run(self, *, tenant_id: str, run_id: str) -> None:
        return None


class FakeAttemptPort:
    """A ``ProviderAttemptJournalPort`` shaped object (``cas_finish`` is the probe)."""

    async def insert_start(self, attempt: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("assembly must not call the journal")

    async def read(self, *, attempt_id: str) -> None:  # pragma: no cover - unused
        return None

    async def find_unresolved(self, *, run_id: str) -> None:  # pragma: no cover - unused
        return None

    async def cas_finish(self, **_kwargs: Any) -> int:  # pragma: no cover - unused
        return 0


class FakeTurnRunner:
    async def execute(self, *, request: Any, cancel: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("assembly must not execute a turn")


class RaisingFactory:
    """Seam double for "this member could not be composed"."""

    def __init__(self, exception: BaseException | None = None) -> None:
        self._exception = exception or RuntimeError(SECRET_MARKER)
        self.calls = 0

    def __call__(self, _session_factory: Any) -> Any:
        self.calls += 1
        raise self._exception


# ---------------------------------------------------------------------------
# Expectations
# ---------------------------------------------------------------------------

#: Member → the blocker the *host* reports when the member is absent.
MEMBER_BLOCKERS: dict[str, str] = {
    "execution_port": BLOCKER_EXECUTION_PORT_UNWIRED,
    "runs": BLOCKER_RUN_SOURCE_UNWIRED,
    "journal": BLOCKER_JOURNAL_UNWIRED,
    "runner": BLOCKER_TURN_RUNNER_UNWIRED,
}


def member_names(bundle: AgentRunBundle) -> dict[str, bool]:
    """Which contract members are assembled, as a plain mapping for assertions."""
    dependencies = bundle.dependencies
    return {
        "execution_port": dependencies.execution_port is not None,
        "runs": dependencies.runs is not None,
        "journal": dependencies.journal is not None,
        "runner": dependencies.runner is not None,
    }
