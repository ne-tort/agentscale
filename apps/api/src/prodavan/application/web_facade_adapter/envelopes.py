"""Application and wire stream envelopes — kept deliberately apart.

The facade produces an **application** envelope
(:class:`prodavan.application.web_facade.contracts.SseEventEnvelope`) whose id is
the composite ``run_id:sequence``. The browser consumes the canonical **wire**
envelope (:class:`prodavan.application.web_api.contracts.SseEventEnvelope`) whose
``event_id`` is a UUIDv5 derived from ``prodavan:web-run:{run_id}:{sequence}``.

These two envelopes must never be mixed:

* a ``run_id:sequence`` value is not a wire event id;
* a UUID is not a sequence and must never be ordered or compared numerically;
* a UUID may only be produced **and accepted** through a trusted
  :class:`RunSequenceIndex` that proves ``(project, session, run, sequence)``
  membership in the authorized stream.

Without such an index the encoder refuses with an explicit
``STREAM_REPLAY_INDEX_UNAVAILABLE`` / ``STREAM_EVENT_ID_UNRESOLVED`` code. A
fabricated replay identity is worse than a refusal: it makes a non-replayable
stream look resumable. Every error carries a stable reason code only — no frame
payload, no exception text, no foreign identifiers (redaction policy,
``web-sse-bridge-contract.md`` §5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from prodavan.application.web_api.contracts import (
    SseEventEnvelope as WireSseEventEnvelope,
    SseEventType,
    uuid5_from_run,
)
from prodavan.application.web_facade.contracts import SseEventEnvelope as ApplicationSseEventEnvelope

WIRE_SCHEMA_VERSION = 1

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SEQUENCE_RE = re.compile(r"^[0-9]+$")

EVENT_TYPE_TO_WIRE: dict[str, SseEventType] = {
    "run_started": SseEventType.RUN_STARTED,
    "text_delta": SseEventType.TEXT_DELTA,
    "block": SseEventType.TRANSCRIPT_BLOCK,
    "tool_call": SseEventType.TOOL_CALL,
    "tool_result": SseEventType.TOOL_RESULT,
    "run_completed": SseEventType.DONE,
    "run_failed": SseEventType.ERROR,
    "run_cancelled": SseEventType.STATUS,
    "heartbeat": SseEventType.KEEPALIVE,
}


class StreamIdentityError(Exception):
    """Explicit refusal to cross the application/wire boundary.

    ``status`` is the HTTP status to use when the failure happens before the
    response is opened; after headers it becomes a terminal error frame whose
    payload is limited to ``code``/``title``/``detail``/``status``.
    """

    def __init__(self, code: str, *, status: int = 502, detail: str = "stream identity could not be resolved") -> None:
        self.code = code
        self.status = status
        self.detail = detail
        super().__init__(code)


class StreamCursorError(Exception):
    """Stable rejection of a transport cursor (always HTTP 400/409)."""

    def __init__(self, code: str, *, status: int = 400, detail: str = "stream cursor is not valid") -> None:
        self.code = code
        self.status = status
        self.detail = detail
        super().__init__(code)


class MalformedStreamEvent(StreamIdentityError):
    """An upstream frame that cannot become a canonical event.

    Distinct from a bad cursor: this is an upstream/translation failure, so the
    transport answers ``502 UPSTREAM_STREAM_MALFORMED`` before headers, or emits
    a redacted terminal error afterwards.
    """

    def __init__(self, detail: str = "the upstream chat stream produced an unusable event") -> None:
        super().__init__("UPSTREAM_STREAM_MALFORMED", status=502, detail=detail)


@dataclass(frozen=True, slots=True)
class StreamScope:
    """Authorized stream scope resolved by the server, never by the client."""

    project_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class StreamPosition:
    """Application identity of one event: ``run_id`` + run-local ``sequence``."""

    run_id: str
    sequence: int

    @property
    def application_event_id(self) -> str:
        return f"{self.run_id}:{self.sequence}"


class RunSequenceIndex(Protocol):
    """Trusted, request-scoped mapping for one authorized stream.

    Production implementations must be built from durable run/sequence
    evidence that binds ``(project, session, run, sequence)``. The transport
    never derives a wire id without consulting this index in both directions.
    """

    @property
    def run_id(self) -> str | None:
        """Run the authorized request is bound to, when unambiguously known."""

    @property
    def project_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    def has(self, run_id: str, sequence: int) -> bool:
        """True only when ``(run_id, sequence)`` is a known event of this stream."""

    def find_by_event_id(self, event_id: UUID) -> tuple[str, int] | None:
        """Deterministic reverse lookup; never derived by arithmetic."""


@dataclass(frozen=True, slots=True)
class EncodedWireEvent:
    """One validated wire frame, ready for SSE serialization."""

    position: StreamPosition
    event_id: UUID
    event_name: str
    data: str


class WireEventEncoder:
    """Application envelope -> wire envelope, only through a trusted index."""

    def __init__(self, index: RunSequenceIndex | None, *, occurred_at: datetime | None = None) -> None:
        self._index = index
        self._occurred_at = occurred_at

    def encode(self, envelope: ApplicationSseEventEnvelope, *, scope: StreamScope) -> EncodedWireEvent:
        index = self._index
        if index is None:
            raise StreamIdentityError(
                "STREAM_REPLAY_INDEX_UNAVAILABLE",
                status=503,
                detail="no trusted run/sequence index is available for this stream",
            )
        if index.project_id != scope.project_id or index.session_id != scope.session_id:
            raise StreamIdentityError(
                "STREAM_SCOPE_MISMATCH",
                status=403,
                detail="stream index does not belong to the authorized project/session",
            )

        event = envelope.data
        position = StreamPosition(run_id=event.run_id, sequence=event.seq)
        # The application envelope id is the composite cursor, not a wire id.
        expected_application_id = position.application_event_id
        if envelope.id != expected_application_id or envelope.event != event.type.value:
            raise StreamIdentityError(
                "APPLICATION_ENVELOPE_INCONSISTENT",
                detail="application envelope id/event disagree with its run/sequence payload",
            )
        if not position.run_id.strip() or position.sequence < 0:
            raise StreamIdentityError("UPSTREAM_STREAM_MALFORMED", detail="stream event has no valid run identity")

        if index.run_id is not None and index.run_id != position.run_id:
            # Cross-run leakage is an authorization/integrity failure, never an
            # empty replay: a reconnect must not silently switch runs.
            raise StreamIdentityError(
                "STREAM_SCOPE_MISMATCH",
                status=403,
                detail="stream event belongs to a different run than the authorized cursor",
            )
        if not index.has(position.run_id, position.sequence):
            raise StreamIdentityError(
                "STREAM_EVENT_ID_UNRESOLVED",
                detail="run/sequence pair is not present in the trusted stream index",
            )

        candidate = uuid5_from_run(position.run_id, position.sequence)
        resolved = index.find_by_event_id(candidate)
        if resolved is None or resolved[0] != position.run_id or resolved[1] != position.sequence:
            # A derivable UUID is not proof: only reverse resolution through
            # the index is accepted.
            raise StreamIdentityError(
                "STREAM_EVENT_ID_UNRESOLVED",
                detail="wire event id cannot be proven by the trusted run index",
            )

        wire_type = EVENT_TYPE_TO_WIRE.get(event.type.value)
        if wire_type is None:
            raise StreamIdentityError(
                "UPSTREAM_STREAM_MALFORMED",
                detail="stream event type has no canonical wire mapping",
            )

        payload = dict(event.data)
        if event.text is not None:
            payload["text"] = event.text
        if event.block is not None:
            payload["block"] = event.block.model_dump(mode="json")

        wire = WireSseEventEnvelope(
            event_id=candidate,
            event_type=wire_type,
            schema_version=WIRE_SCHEMA_VERSION,
            occurred_at=self._occurred_at or _utcnow(),
            project_id=scope.project_id,
            session_id=scope.session_id,
            run_id=position.run_id,
            sequence=position.sequence,
            payload=payload,
            resume_after=position.sequence,
        )
        return EncodedWireEvent(
            position=position,
            event_id=wire.event_id,
            event_name=wire.event_type.value,
            data=wire.model_dump_json(),
        )


@dataclass(frozen=True, slots=True)
class ParsedCursor:
    """Syntactically validated ``Last-Event-ID``; unresolved until indexed."""

    kind: str  # "wire" | "application" | "bare"
    event_id: UUID | None = None
    run_id: str | None = None
    after_seq: int | None = None


@dataclass(frozen=True, slots=True)
class ResolvedCursor:
    """Cursor after index verification; always carries an application identity."""

    run_id: str
    after_seq: int
    event_id: UUID | None

    @property
    def application_event_id(self) -> str:
        return f"{self.run_id}:{self.after_seq}"


def parse_transport_cursor(value: str | None) -> ParsedCursor | None:
    """Parse ``Last-Event-ID`` without guessing run identity from a UUID."""
    if value is None or not value.strip():
        return None
    raw = value.strip()
    if _UUID_RE.fullmatch(raw):
        try:
            event_id = UUID(raw)
        except ValueError as exc:  # pragma: no cover - regex already validates shape
            raise StreamCursorError("INVALID_LAST_EVENT_ID", detail="Last-Event-ID is not a valid UUID") from exc
        if event_id.version != 5:
            raise StreamCursorError("INVALID_LAST_EVENT_ID", detail="Last-Event-ID must be a UUIDv5 stream id")
        return ParsedCursor(kind="wire", event_id=event_id)
    if ":" in raw:
        run_id, sequence = raw.rsplit(":", 1)
        if not run_id.strip():
            raise StreamCursorError("INVALID_LAST_EVENT_ID", detail="Last-Event-ID has an empty run id")
        if not _SEQUENCE_RE.fullmatch(sequence):
            raise StreamCursorError("INVALID_LAST_EVENT_ID", detail="Last-Event-ID must contain an integer sequence")
        return ParsedCursor(kind="application", run_id=run_id, after_seq=int(sequence))
    if not _SEQUENCE_RE.fullmatch(raw):
        raise StreamCursorError("INVALID_LAST_EVENT_ID", detail="Last-Event-ID is neither a UUID nor a run cursor")
    return ParsedCursor(kind="bare", after_seq=int(raw))


def resolve_transport_cursor(
    parsed: ParsedCursor | None,
    *,
    index: RunSequenceIndex,
    scope: StreamScope,
) -> ResolvedCursor | None:
    """Resolve a parsed cursor against the trusted, authorized stream index."""
    if parsed is None:
        return None
    if index.project_id != scope.project_id or index.session_id != scope.session_id:
        raise StreamIdentityError(
            "STREAM_SCOPE_MISMATCH",
            status=403,
            detail="stream index does not belong to the authorized project/session",
        )
    if parsed.kind == "wire":
        resolved = index.find_by_event_id(parsed.event_id) if parsed.event_id is not None else None
        if resolved is None:
            raise StreamCursorError(
                "INVALID_LAST_EVENT_ID",
                detail="Last-Event-ID does not resolve to a known event of this stream",
            )
        run_id, sequence = resolved
        if not index.has(run_id, sequence):
            raise StreamCursorError(
                "INVALID_LAST_EVENT_ID",
                detail="Last-Event-ID does not resolve to a known event of this stream",
            )
        if index.run_id is not None and index.run_id != run_id:
            raise StreamCursorError(
                "RUN_NOT_RESUMABLE",
                status=409,
                detail="the cursor run can no longer be reattached to this stream",
            )
        return ResolvedCursor(run_id=run_id, after_seq=sequence, event_id=parsed.event_id)
    if parsed.kind == "application":
        if index.run_id is None or parsed.run_id != index.run_id:
            raise StreamCursorError(
                "RUN_NOT_RESUMABLE",
                status=409,
                detail="the cursor run is not the run bound to this stream",
            )
        return ResolvedCursor(run_id=index.run_id, after_seq=int(parsed.after_seq or 0), event_id=None)
    # A bare sequence is only unambiguous when the index proves a single run.
    if index.run_id is None:
        raise StreamCursorError(
            "AMBIGUOUS_LAST_EVENT_ID",
            detail="a bare sequence cursor is ambiguous for a multi-run stream",
        )
    return ResolvedCursor(run_id=index.run_id, after_seq=int(parsed.after_seq or 0), event_id=None)


def _utcnow() -> datetime:
    from datetime import UTC, datetime as _datetime

    return _datetime.now(UTC)
