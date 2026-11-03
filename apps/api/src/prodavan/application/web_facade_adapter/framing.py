"""SSE framing and cursor commit semantics for the web stream transport.

Three invariants from the bridge contract live here:

* a replayable frame is written as ``id: <uuid>`` / ``event: <name>`` /
  ``data: <json>``;
* the replay cursor advances **only after** the frame was successfully handed to
  the client. The generator commits after ``yield`` returns, which is exactly
  when the ASGI ``send`` of that chunk completed; if the client dies, the
  generator is closed at the ``yield`` and the commit never runs. A reconnect
  therefore resumes from the last event the browser actually received;
* an idle keepalive is a bare SSE comment: it carries no id, never becomes a
  replayable event and never moves the cursor.

Failures are split by stage. Before the response is opened a malformed upstream
surfaces as :class:`StreamIdentityError` / :class:`StreamCursorError` and becomes
a JSON problem (502/400). After headers the only structured signal left is a
terminal ``error`` frame with a redacted payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .envelopes import EncodedWireEvent, ResolvedCursor, StreamScope

logger = logging.getLogger(__name__)

# Deliberately generic: no upstream text, no frame payload and no foreign
# identifiers ever reach the browser (redaction policy, §5).
REDACTED_DETAIL = "the upstream chat stream produced an event that cannot be replayed"
TERMINAL_TITLE = "Chat stream failed"

TERMINAL_WIRE_EVENTS = frozenset({"done", "error", "status"})


@dataclass(slots=True)
class CursorLedger:
    """The last cursor whose frame was confirmed delivered."""

    position: ResolvedCursor | None = None
    committed_frames: int = 0

    def commit(self, event: EncodedWireEvent) -> None:
        self.position = ResolvedCursor(
            run_id=event.position.run_id,
            after_seq=event.position.sequence,
            event_id=event.event_id,
        )
        self.committed_frames += 1

    @property
    def last_event_id(self) -> str | None:
        if self.position is None:
            return None
        if self.position.event_id is not None:
            return str(self.position.event_id)
        return self.position.application_event_id


@dataclass(slots=True)
class StreamStats:
    """Outcome of one streaming attempt, for logging and metrics only."""

    delivered: int = 0
    terminated: bool = False
    failure_code: str | None = None
    committed: list[str] = field(default_factory=list)


def serialize_frame(event: EncodedWireEvent) -> str:
    """One canonical SSE frame; ``data`` is a single JSON line by construction."""
    return f"id: {event.event_id}\nevent: {event.event_name}\ndata: {event.data}\n\n"


def serialize_comment(text: str = "keepalive") -> str:
    """SSE comment: liveness only, never replayable, never moves the cursor."""
    return f": {text}\n\n"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def terminal_error_frame(
    *,
    code: str,
    status: int,
    project_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Redacted terminal wire error for a failure discovered after headers.

    The envelope deliberately has no ``run_id``/``sequence``: the transport will
    not synthesize a replayable identity, so the browser must treat this as a
    terminal, non-resumable protocol error. There is no ``id:`` line either,
    because such a frame must not advance a cursor.
    """
    envelope: dict[str, Any] = {
        "event_type": "error",
        "schema_version": 1,
        "occurred_at": _now_iso(),
        "payload": {
            "code": code,
            "title": TERMINAL_TITLE,
            "detail": REDACTED_DETAIL,
            "status": status,
        },
    }
    if project_id is not None:
        envelope["project_id"] = project_id
    if session_id is not None:
        envelope["session_id"] = session_id
    return f"event: error\ndata: {json.dumps(envelope, ensure_ascii=False, sort_keys=True)}\n\n"


def _failure_code(exc: BaseException) -> tuple[str, int]:
    """Stable code/status for a post-header failure; never leaks the message."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    if isinstance(code, str) and isinstance(status, int):
        return code, status
    if isinstance(code, str):
        return code, 502
    return "INTERNAL", 500


async def stream_frames(
    *,
    scope: StreamScope,
    source: AsyncIterator[EncodedWireEvent],
    ledger: CursorLedger,
    keepalive_seconds: float | None = None,
    is_terminal: Callable[[EncodedWireEvent], bool] | None = None,
    failure_frame: Callable[[str, int], str] | None = None,
    stats: StreamStats | None = None,
) -> AsyncIterator[str]:
    """Yield SSE chunks, committing the cursor strictly after each send.

    A slow upstream emits a comment keepalive instead of blocking the client.
    A failure raised after at least one frame was delivered becomes a redacted
    terminal error; before the first frame it propagates, because the caller can
    still answer with a JSON problem.
    """
    terminal = is_terminal or (lambda event: event.event_name in TERMINAL_WIRE_EVENTS)
    build_failure = failure_frame or (
        lambda code, status: terminal_error_frame(
            code=code, status=status, project_id=scope.project_id, session_id=scope.session_id
        )
    )
    stats = stats if stats is not None else StreamStats()

    iterator = source.__aiter__()
    pending: asyncio.Future[EncodedWireEvent] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            if keepalive_seconds is None:
                timed_out = False
            else:
                # asyncio.wait (NOT wait_for): cancelling an in-flight
                # ``__anext__`` would tear the upstream generator mid-write.
                done, _ = await asyncio.wait({pending}, timeout=keepalive_seconds)
                timed_out = not done
            if timed_out:
                yield serialize_comment()
                continue

            task, pending = pending, None
            try:
                event = task.result()
            except StopAsyncIteration:
                return
            except (GeneratorExit, asyncio.CancelledError):
                raise
            except Exception as exc:  # noqa: BLE001 - mapped to a stable code
                code, status = _failure_code(exc)
                stats.failure_code = code
                if stats.delivered == 0:
                    raise
                yield build_failure(code, status)
                stats.terminated = True
                return

            yield serialize_frame(event)
            # Reached only after the consumer accepted (and sent) the chunk.
            ledger.commit(event)
            stats.delivered += 1
            stats.committed.append(str(event.event_id))
            if terminal(event):
                stats.terminated = True
                return
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
