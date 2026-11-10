"""Cryptographic provenance for privileged auth command envelopes.

Only the platform control plane receives AUTH_COMMAND_SIGNING_SECRET.
The signature covers exactly the stable command fields; transport metadata and
DLQ metadata are deliberately outside the trust boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping

from prodavan.core.events.envelope import EventEnvelope

AUTH_COMMAND_SCHEMA_VERSION = 1
_SIGNATURE_PREFIX = "v1."


class AuthCommandError(RuntimeError):
    """Base fail-closed auth command error with a non-sensitive stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AuthCommandConfigurationError(AuthCommandError):
    pass


class AuthCommandProvenanceError(AuthCommandError):
    pass


class AuthCommandRejected(AuthCommandError):
    pass


def _secret_bytes(signing_secret: str | None) -> bytes:
    if signing_secret is None:
        from prodavan.config.settings import settings

        signing_secret = settings.auth_command_signing_secret
    if not isinstance(signing_secret, str) or not signing_secret.strip():
        raise AuthCommandConfigurationError(
            "AUTH_COMMAND_SIGNING_REQUIRED",
            "AUTH_COMMAND_SIGNING_SECRET is required for auth commands",
        )
    raw = signing_secret.encode("utf-8")
    if len(raw) < 32:
        raise AuthCommandConfigurationError(
            "AUTH_COMMAND_SIGNING_WEAK",
            "AUTH_COMMAND_SIGNING_SECRET must contain at least 32 bytes",
        )
    return raw


def validate_auth_command_signing_secret(signing_secret: str | None = None) -> None:
    """Validate configuration without exposing or returning the secret."""

    _secret_bytes(signing_secret)


def _canonical_command(data: Mapping[str, Any]) -> bytes:
    version = data.get("schema_version")
    event_id = data.get("event_id")
    event_type = data.get("event_type")
    occurred_at = data.get("occurred_at")
    payload = data.get("payload")
    if type(version) is not int or version != AUTH_COMMAND_SCHEMA_VERSION:
        raise AuthCommandProvenanceError(
            "AUTH_COMMAND_VERSION_INVALID", "Unsupported auth command schema version"
        )
    if not isinstance(event_id, str) or not event_id.strip():
        raise AuthCommandProvenanceError("AUTH_COMMAND_ID_INVALID", "Auth command event id is required")
    if not isinstance(event_type, str) or not event_type.strip():
        raise AuthCommandProvenanceError("AUTH_COMMAND_TYPE_INVALID", "Auth command event type is required")
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        raise AuthCommandProvenanceError(
            "AUTH_COMMAND_TIMESTAMP_INVALID", "Auth command occurred_at is required"
        )
    if not isinstance(payload, dict):
        raise AuthCommandProvenanceError("AUTH_COMMAND_PAYLOAD_INVALID", "Auth command payload must be an object")
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
        raise AuthCommandProvenanceError(
            "AUTH_COMMAND_CANONICALIZATION_FAILED", "Auth command is not canonical JSON"
        ) from exc


def _expected_signature(data: Mapping[str, Any], signing_secret: str | None) -> str:
    digest = hmac.new(_secret_bytes(signing_secret), _canonical_command(data), hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def sign_auth_command_envelope(
    envelope: EventEnvelope, *, signing_secret: str | None = None
) -> EventEnvelope:
    """Sign one auth command in place and return it (single producer primitive)."""

    if envelope.bus != "auth_command":
        raise AuthCommandProvenanceError("AUTH_COMMAND_BUS_INVALID", "Only auth_command can be signed")
    envelope.schema_version = AUTH_COMMAND_SCHEMA_VERSION
    envelope.signature = _expected_signature(envelope.to_dict(), signing_secret)
    return envelope


def verify_auth_command_data(
    data: Mapping[str, Any], *, signing_secret: str | None = None
) -> None:
    """Verify raw bus data before payload parsing or any Keycloak operation."""

    if not isinstance(data, Mapping) or data.get("bus") != "auth_command":
        raise AuthCommandProvenanceError("AUTH_COMMAND_BUS_INVALID", "Unexpected auth command bus")
    signature = data.get("signature")
    if not isinstance(signature, str) or not signature.startswith(_SIGNATURE_PREFIX):
        raise AuthCommandProvenanceError(
            "AUTH_COMMAND_SIGNATURE_MISSING", "Unsigned auth commands are forbidden"
        )
    expected = _expected_signature(data, signing_secret)
    if not hmac.compare_digest(signature, expected):
        raise AuthCommandProvenanceError(
            "AUTH_COMMAND_SIGNATURE_INVALID", "Auth command signature verification failed"
        )
    if data["event_type"] == "auth.user.register" and "password" in data["payload"]:
        # Also runs in KafkaManager.publish, so even a correctly signed legacy
        # envelope cannot put plaintext onto the topic before being rejected.
        raise AuthCommandRejected(
            "AUTH_PLAINTEXT_PASSWORD_FORBIDDEN", "Plaintext registration passwords are forbidden"
        )


def verified_auth_command_envelope(
    data: Mapping[str, Any], *, signing_secret: str | None = None
) -> EventEnvelope:
    """Construct an envelope only after cryptographic verification succeeds."""

    verify_auth_command_data(data, signing_secret=signing_secret)
    return EventEnvelope(
        bus="auth_command",
        event_id=data["event_id"],
        event_type=data["event_type"],
        occurred_at=data["occurred_at"],
        company_id=data.get("company_id"),
        project_id=data.get("project_id"),
        cabinet_id=data.get("cabinet_id"),
        payload=dict(data["payload"]),
        schema_version=AUTH_COMMAND_SCHEMA_VERSION,
        signature=data["signature"],
    )


def verify_auth_command_envelope(
    envelope: EventEnvelope, *, signing_secret: str | None = None
) -> None:
    verify_auth_command_data(envelope.to_dict(), signing_secret=signing_secret)
