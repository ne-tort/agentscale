"""Detached agent turns — the run is owned by the API process, not the client.

Why this exists
---------------
Chat turns used to be driven by the client's SSE request: the API generator
streamed tokens straight to the browser, and the bridge runtime aborted as
soon as that connection dropped. A page reload (or any network blip) therefore
**killed the agent** mid-answer and lost the unflushed tail.

Here a turn runs as an autonomous `asyncio.Task` that owns its own DB session:
it reads the runtime stream, persists every event to `agent_events`, and does
so whether or not any client is watching. Clients (the send SSE, or a reloaded
page polling `after_seq`) only *tail* the persisted events. Disconnecting a
client never touches the run; the agent keeps working until it finishes or the
user explicitly cancels.

The registry is process-local. There is exactly one API replica (see
`infra/k3s/base/agentscale-api/deployment.yaml`), so a single in-process map is
the source of truth for "is a turn running"; a DB-derived heuristic remains the
fallback after an API restart (see `turn_in_progress`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# How many finished turns to keep around so late tailers can still resolve
# their record (then `assistant_text` / errors) after the run ended.
_KEEP_FINISHED = 64


@dataclass
class DetachedTurn:
    """One in-flight (or just-finished) agent turn."""

    turn_id: str
    session_id: str
    project_id: str
    # Highest event seq that existed BEFORE this turn — the tailer starts here.
    start_seq: int
    # Auth context carried from the request (the run re-authorizes nothing;
    # the send request already authorized). ``principal`` is a plain object;
    # the employee is re-loaded in the run session by id.
    employee_id: str | None = None
    principal: object | None = None
    task: asyncio.Task[None] | None = None
    finished: bool = False
    cancel_requested: bool = False
    assistant_text: str = ""
    error_code: str | None = None
    error_detail: str | None = None
    # Events drained by the tailer, for the closing `_turn_complete` frame.
    drained_events: list[dict] = field(default_factory=list)


class DetachedTurnRegistry:
    """Tracks autonomous turns keyed by turn id (idempotent start) + session."""

    def __init__(self) -> None:
        self._by_turn: dict[str, DetachedTurn] = {}
        self._active_by_session: dict[str, str] = {}

    def get(self, turn_id: str) -> DetachedTurn | None:
        return self._by_turn.get(turn_id)

    def active_for_session(self, session_id: str) -> DetachedTurn | None:
        turn_id = self._active_by_session.get(session_id)
        if turn_id is None:
            return None
        turn = self._by_turn.get(turn_id)
        if turn is None or turn.finished:
            self._active_by_session.pop(session_id, None)
            return None
        return turn

    def is_working(self, session_id: str) -> bool:
        return self.active_for_session(session_id) is not None

    async def start(
        self,
        *,
        turn_id: str,
        session_id: str,
        project_id: str,
        start_seq: int,
        runner: Callable[[DetachedTurn], Awaitable[None]],
        employee_id: str | None = None,
        principal: object | None = None,
    ) -> DetachedTurn:
        """Start a turn, or return the existing one for the same turn_id.

        Idempotency by ``turn_id`` makes a retried send (flaky network, HTTP
        replay) reuse the same run instead of spawning a duplicate — and a
        superseding send on the same session starts a fresh turn (the runtime
        aborts the previous run on its own).
        """
        existing = self._by_turn.get(turn_id)
        if existing is not None and not existing.finished:
            return existing

        turn = DetachedTurn(
            turn_id=turn_id,
            session_id=session_id,
            project_id=project_id,
            start_seq=start_seq,
            employee_id=employee_id,
            principal=principal,
        )
        self._by_turn[turn_id] = turn
        self._active_by_session[session_id] = turn_id
        turn.task = asyncio.create_task(self._run(turn, runner))
        return turn

    async def _run(
        self, turn: DetachedTurn, runner: Callable[[DetachedTurn], Awaitable[None]]
    ) -> None:
        try:
            await runner(turn)
        except asyncio.CancelledError:
            turn.cancel_requested = True
            raise
        except Exception:  # noqa: BLE001 — a detached turn must never crash the loop
            logger.exception("detached turn failed session=%s", turn.session_id)
            turn.error_code = turn.error_code or "INTERNAL"
        finally:
            turn.finished = True
            if self._active_by_session.get(turn.session_id) == turn.turn_id:
                self._active_by_session.pop(turn.session_id, None)
            self._prune()

    def request_cancel(self, session_id: str) -> DetachedTurn | None:
        """Cancel the active turn for a session (closes runtime, marks stop)."""
        turn = self.active_for_session(session_id)
        if turn is None:
            return None
        turn.cancel_requested = True
        if turn.task is not None and not turn.task.done():
            turn.task.cancel()
        return turn

    def _prune(self) -> None:
        finished = [t for t in self._by_turn.values() if t.finished]
        if len(finished) <= _KEEP_FINISHED:
            return
        # Drop the oldest finished records; a tailer keeps its own reference.
        for turn in finished[: len(finished) - _KEEP_FINISHED]:
            self._by_turn.pop(turn.turn_id, None)


# Process-wide registry (one API replica → authoritative for "working").
detached_turns = DetachedTurnRegistry()
