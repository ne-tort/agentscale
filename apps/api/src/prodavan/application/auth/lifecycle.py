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
AUTH_USER_ENABLE = "auth.user.enable"
AUTH_USER_DELETE = "auth.user.delete"
AUTH_USER_RENAME = "auth.user.rename"
AUTH_USER_DISABLED = "auth.user.disabled"
AUTH_USER_ENABLED = "auth.user.enabled"
AUTH_USER_DELETED = "auth.user.deleted"
AUTH_USER_RENAMED = "auth.user.renamed"
AUTH_USER_DISABLE_FAILED = "auth.user.disable_failed"
AUTH_USER_ENABLE_FAILED = "auth.user.enable_failed"
AUTH_USER_DELETE_FAILED = "auth.user.delete_failed"
AUTH_USER_RENAME_FAILED = "auth.user.rename_failed"


@dataclass(slots=True, frozen=True)
class LifecycleUserCommand:
    request_id: str
    client_ref: str
    sub: str | None
    username: str | None
    email: str | None
    action: str  # disable | enable | delete | rename
    new_username: str | None = None


@dataclass(slots=True, frozen=True)
class RenameUserCommand:
    request_id: str
    client_ref: str
    sub: str | None
    old_username: str | None
    new_username: str
    email: str | None


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


async def publish_enable_command(
    *,
    client_ref: str,
    sub: str | None = None,
    username: str | None = None,
    email: str | None = None,
) -> bool:
    from prodavan.core.events.bus import publish_envelope

    return await publish_envelope(
        _build_command_envelope(
            event_type=AUTH_USER_ENABLE,
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


async def publish_rename_command(
    *,
    client_ref: str,
    sub: str | None = None,
    old_username: str | None = None,
    new_username: str,
    email: str | None = None,
) -> bool:
    from prodavan.core.events.bus import publish_envelope

    rid = str(uuid.uuid4())
    return await publish_envelope(
        auth_command_envelope(
            event_id=rid,
            event_type=AUTH_USER_RENAME,
            payload={
                "request_id": rid,
                "client_ref": client_ref,
                "sub": sub,
                "old_username": old_username,
                "new_username": new_username,
                "email": email,
            },
        )
    )


def parse_rename_command(payload: dict[str, Any]) -> RenameUserCommand:
    sub = payload.get("sub")
    return RenameUserCommand(
        request_id=str(payload.get("request_id") or "").strip() or str(uuid.uuid4()),
        client_ref=str(payload.get("client_ref") or "").strip(),
        sub=str(sub).strip() if sub else None,
        old_username=(str(payload["old_username"]).strip() if payload.get("old_username") else None),
        new_username=str(payload.get("new_username") or "").strip(),
        email=(str(payload["email"]).strip().lower() if payload.get("email") else None),
    )


async def apply_rename(command: RenameUserCommand) -> EventEnvelope:
    if not command.client_ref or not command.new_username:
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=AUTH_USER_RENAME_FAILED,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "error": "client_ref and new_username required",
            },
        )
    try:
        await get_user_admin().rename_user(
            keycloak_user_id=command.sub,
            old_username=command.old_username,
            new_username=command.new_username,
            email=command.email,
        )
    except AppError as exc:
        logger.warning(
            "auth rename failed client_ref=%s code=%s",
            command.client_ref,
            exc.code,
        )
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=AUTH_USER_RENAME_FAILED,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "sub": command.sub,
                "error": exc.detail or exc.code,
                "error_code": exc.code,
            },
        )
    except Exception as exc:
        logger.exception("auth rename unexpected client_ref=%s", command.client_ref)
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=AUTH_USER_RENAME_FAILED,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "sub": command.sub,
                "error": str(exc),
            },
        )
    return auth_event_envelope(
        event_id=str(uuid.uuid4()),
        event_type=AUTH_USER_RENAMED,
        payload={
            "request_id": command.request_id,
            "client_ref": command.client_ref,
            "sub": command.sub,
            "old_username": command.old_username,
            "new_username": command.new_username,
            "email": command.email,
        },
    )


async def apply_lifecycle(command: LifecycleUserCommand) -> EventEnvelope:
    if command.action == "delete":
        ok_type = AUTH_USER_DELETED
        fail_type = AUTH_USER_DELETE_FAILED
    elif command.action == "enable":
        ok_type = AUTH_USER_ENABLED
        fail_type = AUTH_USER_ENABLE_FAILED
    else:
        ok_type = AUTH_USER_DISABLED
        fail_type = AUTH_USER_DISABLE_FAILED
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
        elif command.action == "enable":
            await admin.enable_user(
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
    if envelope.event_type == AUTH_USER_ENABLE:
        return await apply_lifecycle(
            parse_lifecycle_command(envelope.payload or {}, action="enable")
        )
    if envelope.event_type == AUTH_USER_DELETE:
        return await apply_lifecycle(
            parse_lifecycle_command(envelope.payload or {}, action="delete")
        )
    if envelope.event_type == AUTH_USER_RENAME:
        return await apply_rename(parse_rename_command(envelope.payload or {}))
    return None
