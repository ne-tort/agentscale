"""Separate HMAC provenance, freshness and schema checks for auth events."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, Mapping

from prodavan.core.events.envelope import EventEnvelope

AUTH_EVENT_SCHEMA_VERSION = 1
_SIGNATURE_PREFIX = "v1."
_FUTURE_SKEW_SECONDS = 30.0


class AuthEventError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AuthEventConfigurationError(AuthEventError):
    pass


class AuthEventProvenanceError(AuthEventError):
    pass


class AuthEventExpired(AuthEventError):
    pass


class AuthEventReplay(AuthEventError):
    pass


def _secret_bytes(signing_secret: str | None) -> bytes:
    if signing_secret is None:
        from prodavan.config.settings import settings

        signing_secret = settings.auth_event_signing_secret
    if not isinstance(signing_secret, str) or not signing_secret.strip():
        raise AuthEventConfigurationError(
            "AUTH_EVENT_SIGNING_REQUIRED", "AUTH_EVENT_SIGNING_SECRET is required for auth events"
        )
    raw = signing_secret.encode("utf-8")
    if len(raw) < 32:
        raise AuthEventConfigurationError(
            "AUTH_EVENT_SIGNING_WEAK", "AUTH_EVENT_SIGNING_SECRET must contain at least 32 bytes"
        )
    return raw


def validate_auth_event_signing_secret(signing_secret: str | None = None) -> None:
    _secret_bytes(signing_secret)


def _canonical_event(data: Mapping[str, Any]) -> bytes:
    version = data.get("schema_version")
    event_id = data.get("event_id")
    event_type = data.get("event_type")
    occurred_at = data.get("occurred_at")
    payload = data.get("payload")
    if type(version) is not int or version != AUTH_EVENT_SCHEMA_VERSION:
        raise AuthEventProvenanceError("AUTH_EVENT_VERSION_INVALID", "Unsupported auth event schema version")
    if not isinstance(event_id, str) or not event_id.strip():
        raise AuthEventProvenanceError("AUTH_EVENT_ID_INVALID", "Auth event id is required")
    if not isinstance(event_type, str) or not event_type.strip():
        raise AuthEventProvenanceError("AUTH_EVENT_TYPE_INVALID", "Auth event type is required")
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        raise AuthEventProvenanceError("AUTH_EVENT_TIMESTAMP_INVALID", "Auth event occurred_at is required")
    if not isinstance(payload, dict):
        raise AuthEventProvenanceError("AUTH_EVENT_PAYLOAD_INVALID", "Auth event payload must be an object")
    try:
        return json.dumps(
            {
                "version": version,
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuthEventProvenanceError(
            "AUTH_EVENT_CANONICALIZATION_FAILED", "Auth event is not canonical JSON"
        ) from exc


def _expected_signature(data: Mapping[str, Any], signing_secret: str | None) -> str:
    digest = hmac.new(_secret_bytes(signing_secret), _canonical_event(data), hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def sign_auth_event_envelope(
    envelope: EventEnvelope, *, signing_secret: str | None = None
) -> EventEnvelope:
    """Sign at the bus producer edge; direct register_user remains unsigned."""

    if envelope.bus != "auth_event":
        raise AuthEventProvenanceError("AUTH_EVENT_BUS_INVALID", "Only auth_event can be signed")
    envelope.schema_version = AUTH_EVENT_SCHEMA_VERSION
    envelope.signature = _expected_signature(envelope.to_dict(), signing_secret)
    return envelope


def _verify_freshness(occurred_at: str, *, max_age_seconds: int | None) -> None:
    if max_age_seconds is None:
        from prodavan.config.settings import settings

        max_age_seconds = int(settings.auth_event_max_age_seconds)
    try:
        occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            raise ValueError("timezone required")
        occurred = occurred.astimezone(UTC)
    except (TypeError, ValueError):
        raise AuthEventProvenanceError(
            "AUTH_EVENT_TIMESTAMP_INVALID", "Auth event occurred_at must be timezone-aware ISO-8601"
        ) from None
    age = (datetime.now(UTC) - occurred).total_seconds()
    if age > max(1, int(max_age_seconds)):
        raise AuthEventExpired("AUTH_EVENT_EXPIRED", "Auth event is outside the accepted freshness window")
    if age < -_FUTURE_SKEW_SECONDS:
        raise AuthEventProvenanceError("AUTH_EVENT_FROM_FUTURE", "Auth event timestamp is too far in the future")


def verify_auth_event_data(
    data: Mapping[str, Any],
    *,
    signing_secret: str | None = None,
    max_age_seconds: int | None = None,
) -> None:
    """Verify signature and freshness before payload parsing or identity bind."""

    if not isinstance(data, Mapping) or data.get("bus") != "auth_event":
        raise AuthEventProvenanceError("AUTH_EVENT_BUS_INVALID", "Unexpected auth event bus")
    signature = data.get("signature")
    if not isinstance(signature, str) or not signature.startswith(_SIGNATURE_PREFIX):
        raise AuthEventProvenanceError("AUTH_EVENT_SIGNATURE_MISSING", "Unsigned auth events are forbidden")
    expected = _expected_signature(data, signing_secret)
    if not hmac.compare_digest(signature, expected):
        raise AuthEventProvenanceError(
            "AUTH_EVENT_SIGNATURE_INVALID", "Auth event signature verification failed"
        )
    _verify_freshness(data["occurred_at"], max_age_seconds=max_age_seconds)


def verified_auth_event_envelope(
    data: Mapping[str, Any],
    *,
    signing_secret: str | None = None,
    max_age_seconds: int | None = None,
) -> EventEnvelope:
    verify_auth_event_data(data, signing_secret=signing_secret, max_age_seconds=max_age_seconds)
    return EventEnvelope(
        bus="auth_event",
        event_id=data["event_id"],
        event_type=data["event_type"],
        occurred_at=data["occurred_at"],
        company_id=data.get("company_id"),
        project_id=data.get("project_id"),
        cabinet_id=data.get("cabinet_id"),
        payload=dict(data["payload"]),
        schema_version=AUTH_EVENT_SCHEMA_VERSION,
        signature=data["signature"],
    )


def verify_auth_event_envelope(
    envelope: EventEnvelope,
    *,
    signing_secret: str | None = None,
    max_age_seconds: int | None = None,
) -> None:
    verify_auth_event_data(
        envelope.to_dict(), signing_secret=signing_secret, max_age_seconds=max_age_seconds
    )
