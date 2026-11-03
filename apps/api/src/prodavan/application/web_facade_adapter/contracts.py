"""Typed contracts owned by the legacy-to-canonical web facade adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AdapterScope(StrEnum):
    CURRENT_USER_READ = "web:current_user:read"
    PROJECTS_READ = "web:projects:read"
    CONTAINER_STATUS_READ = "web:container_status:read"
    CHAT_SIDEBAR_READ = "web:chat_sidebar:read"
    CHAT_TRANSCRIPT_READ = "web:chat_transcript:read"
    CHAT_RUN = "web:chat_run"


class LastEventIdError(ValueError):
    """Stable transport-level rejection of a malformed ``Last-Event-ID``.

    Subclasses ``ValueError`` so existing callers keep their contract; the
    transport maps ``code`` to an RFC 7807 ``400`` instead of letting a raw
    ``ValueError`` mutate into a generic 500/502.
    """

    code = "INVALID_LAST_EVENT_ID"

    def __init__(self, code: str = "INVALID_LAST_EVENT_ID") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ReplayCursor:
    """Parsed SSE cursor; the sequence is scoped to one run/session."""

    run_id: str | None
    after_seq: int

    def event_id(self) -> str:
        return f"{self.run_id}:{self.after_seq}" if self.run_id else str(self.after_seq)


def parse_last_event_id(value: str | None) -> ReplayCursor | None:
    """Parse ``Last-Event-ID`` without accepting ambiguous negative cursors.

    Accepts the legacy/application ``run_id:sequence`` form and a bare
    non-negative sequence. UUID wire ids are deliberately not understood here:
    they can only be resolved through a trusted run index by the transport.
    """
    if value is None or not value.strip():
        return None
    raw = value.strip()
    if ":" in raw:
        run_id, sequence = raw.rsplit(":", 1)
        if not run_id:
            raise LastEventIdError()
    else:
        run_id, sequence = None, raw
    if not sequence.isascii() or not sequence.isdigit():
        raise LastEventIdError()
    seq = int(sequence)
    if seq < 0:
        raise LastEventIdError()
    return ReplayCursor(run_id=run_id, after_seq=seq)


def legacy_event_to_run_event(raw: object, *, fallback_run_id: str | None = None):
    """Translate one legacy event dict into the canonical ``RunStreamEvent``."""
    from prodavan.application.web_facade.contracts import (
        ChatBlock,
        RunEventType,
        RunStreamEvent,
    )

    if not isinstance(raw, dict):
        raise TypeError("legacy stream event must be a mapping")
    data = dict(raw.get("data") or {})
    event_type = str(raw.get("type") or raw.get("event") or data.pop("type", ""))
    aliases = {
        "start": "run_started", "started": "run_started", "done": "run_completed",
        "complete": "run_completed", "error": "run_failed", "cancel": "run_cancelled",
        "delta": "text_delta", "keepalive": "heartbeat",
    }
    event_name = aliases.get(event_type, event_type)
    run_id = str(raw.get("run_id") or data.pop("run_id", None) or fallback_run_id or "")
    if not run_id:
        raise ValueError("legacy stream event has no run_id")
    try:
        event_kind = RunEventType(event_name)
    except ValueError as exc:
        raise ValueError(f"unsupported legacy stream event: {event_name}") from exc
    seq_raw = raw.get("seq", raw.get("sequence", data.pop("seq", None)))
    if seq_raw is None or isinstance(seq_raw, bool):
        raise ValueError("legacy stream event has no sequence")
    try:
        seq = int(seq_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("legacy stream event has a non-numeric sequence") from exc
    if seq < 0:
        raise ValueError("legacy stream event has a negative sequence")
    block = raw.get("block") or data.pop("block", None)
    if isinstance(block, dict):
        block = ChatBlock.model_validate(block)
    text = raw.get("text", data.pop("text", None))
    return RunStreamEvent(type=event_kind, run_id=run_id, seq=seq, text=text, block=block, data=data)
