"""Auth Service — Kafka-driven user disable/delete (domain-agnostic)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from prodavan.application.auth.user_admin import get_user_admin
from prodavan.core.events.envelope import EventEnvelope, auth_command_envelope, auth_event_envelope
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

AUTH_USER_DISABLE = "auth.user.disable"
AUTH_USER_DELETE = "auth.user.delete"
AUTH_USER_DISABLED = "auth.user.disabled"
AUTH_USER_DELETED = "auth.user.deleted"
AUTH_USER_DISABLE_FAILED = "auth.user.disable_failed"
AUTH_USER_DELETE_FAILED = "auth.user.delete_failed"


@dataclass(slots=True, frozen=True)
class LifecycleUserCommand:
    request_id: str
    client_ref: str
    sub: str | None
    username: str | None
    email: str | None
    action: str  # disable | delete


def parse_lifecycle_command(payload: dict[str, Any], *, action: str) -> LifecycleUserCommand:
    sub = payload.get("sub")
    return LifecycleUserCommand(
        request_id=str(payload.get("request_id") or "").strip() or str(uuid.uuid4()),
        client_ref=str(payload.get("client_ref") or "").strip(),
        sub=str(sub).strip() if sub else None,
        username=(str(payload["username"]).strip() if payload.get("username") else None),
        email=(str(payload["email"]).strip().lower() if payload.get("email") else None),
        action=action,
    )


def _build_command_envelope(
    *,
    event_type: str,
    client_ref: str,
    sub: str | None,
    username: str | None,
    email: str | None,
    request_id: str | None = None,
) -> EventEnvelope:
    rid = request_id or str(uuid.uuid4())
    return auth_command_envelope(
        event_id=rid,
        event_type=event_type,
        payload={
            "request_id": rid,
            "client_ref": client_ref,
            "sub": sub,
            "username": username,
            "email": email,
        },
    )


async def publish_disable_command(
    *,
    client_ref: str,
    sub: str | None = None,
    username: str | None = None,
    email: str | None = None,
) -> bool:
    from prodavan.core.events.bus import publish_envelope

    return await publish_envelope(
        _build_command_envelope(
            event_type=AUTH_USER_DISABLE,
            client_ref=client_ref,
            sub=sub,
            username=username,
            email=email,
        )
    )


async def publish_delete_command(
    *,
    client_ref: str,
    sub: str | None = None,
    username: str | None = None,
    email: str | None = None,
) -> bool:
    from prodavan.core.events.bus import publish_envelope

    return await publish_envelope(
        _build_command_envelope(
            event_type=AUTH_USER_DELETE,
            client_ref=client_ref,
            sub=sub,
            username=username,
            email=email,
        )
    )


async def apply_lifecycle(command: LifecycleUserCommand) -> EventEnvelope:
    ok_type = AUTH_USER_DELETED if command.action == "delete" else AUTH_USER_DISABLED
    fail_type = AUTH_USER_DELETE_FAILED if command.action == "delete" else AUTH_USER_DISABLE_FAILED
    if not command.client_ref:
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=fail_type,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "error": "client_ref required",
            },
        )
    try:
        admin = get_user_admin()
        if command.action == "delete":
            await admin.delete_user(
                keycloak_user_id=command.sub,
                username=command.username,
                email=command.email,
            )
        else:
            await admin.disable_user(
                keycloak_user_id=command.sub,
                username=command.username,
                email=command.email,
            )
    except AppError as exc:
        logger.warning(
            "auth %s failed client_ref=%s code=%s",
            command.action,
            command.client_ref,
            exc.code,
        )
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=fail_type,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "sub": command.sub,
                "error": exc.detail or exc.code,
                "error_code": exc.code,
            },
        )
    except Exception as exc:
        logger.exception("auth %s unexpected client_ref=%s", command.action, command.client_ref)
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=fail_type,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "sub": command.sub,
                "error": str(exc),
            },
        )

    return auth_event_envelope(
        event_id=str(uuid.uuid4()),
        event_type=ok_type,
        payload={
            "request_id": command.request_id,
            "client_ref": command.client_ref,
            "sub": command.sub,
            "username": command.username,
            "email": command.email,
        },
    )


async def handle_auth_lifecycle_command(envelope: EventEnvelope) -> EventEnvelope | None:
    if envelope.bus != "auth_command":
        return None
    if envelope.event_type == AUTH_USER_DISABLE:
        return await apply_lifecycle(
            parse_lifecycle_command(envelope.payload or {}, action="disable")
        )
    if envelope.event_type == AUTH_USER_DELETE:
        return await apply_lifecycle(
            parse_lifecycle_command(envelope.payload or {}, action="delete")
        )
    return None
