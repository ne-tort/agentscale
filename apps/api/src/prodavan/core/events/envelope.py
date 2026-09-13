"""C-EVENT-BUS envelopes — platform events + project triggers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

BusName = Literal[
    "platform",
    "project_trigger",
    "auth_command",
    "auth_event",
    "relation_event",
    "metrics",
    "document",
    "tenant",
    "search",
]


@dataclass(slots=True)
class EventEnvelope:
    """Stable JSON shape for Kafka (and in-memory dual-write buffer)."""

    bus: BusName
    event_id: str
    event_type: str
    occurred_at: str
    company_id: str | None = None
    project_id: str | None = None
    cabinet_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).isoformat()


def platform_envelope(
    *,
    event_id: str,
    event_type: str,
    company_id: str | None = None,
    project_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        bus="platform",
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at or EventEnvelope.now_iso(),
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload=payload or {},
    )


def project_trigger_envelope(
    *,
    event_id: str,
    kind: str,
    project_id: str,
    company_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        bus="project_trigger",
        event_id=event_id,
        event_type=kind,
        occurred_at=occurred_at or EventEnvelope.now_iso(),
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload=payload or {},
    )


def auth_command_envelope(
    *,
    event_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        bus="auth_command",
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at or EventEnvelope.now_iso(),
        payload=payload or {},
    )


def auth_event_envelope(
    *,
    event_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        bus="auth_event",
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at or EventEnvelope.now_iso(),
        payload=payload or {},
    )


def relation_event_envelope(
    *,
    event_id: str,
    event_type: str,
    company_id: str | None = None,
    project_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        bus="relation_event",
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at or EventEnvelope.now_iso(),
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload=payload or {},
    )


def metrics_envelope(
    *,
    event_id: str,
    event_type: str,
    company_id: str | None = None,
    project_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        bus="metrics",
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at or EventEnvelope.now_iso(),
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload=payload or {},
    )


def document_envelope(
    *,
    event_id: str,
    event_type: str,
    company_id: str | None = None,
    project_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        bus="document",
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at or EventEnvelope.now_iso(),
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload=payload or {},
    )


def search_envelope(
    *,
    event_id: str,
    event_type: str,
    company_id: str | None = None,
    project_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        bus="search",
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at or EventEnvelope.now_iso(),
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload=payload or {},
    )
