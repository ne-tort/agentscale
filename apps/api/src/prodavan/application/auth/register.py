"""Auth Service — Kafka-driven user registration (domain-agnostic)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from prodavan.application.auth.user_admin import RegisterResult, get_user_admin
from prodavan.core.events.envelope import EventEnvelope, auth_command_envelope, auth_event_envelope
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

AUTH_USER_REGISTER = "auth.user.register"
AUTH_USER_REGISTERED = "auth.user.registered"
AUTH_USER_REGISTER_FAILED = "auth.user.register_failed"


@dataclass(slots=True, frozen=True)
class RegisterUserCommand:
    request_id: str
    client_ref: str
    username: str
    email: str
    password: str | None
    realm_roles: list[str]
    display_name: str | None


def parse_register_command(payload: dict[str, Any]) -> RegisterUserCommand:
    roles_raw = payload.get("realm_roles") or []
    roles = [str(r).strip() for r in roles_raw if str(r).strip()]
    password = payload.get("password")
    if password is not None:
        password = str(password)
        if not password.strip():
            password = None
    return RegisterUserCommand(
        request_id=str(payload.get("request_id") or "").strip() or str(uuid.uuid4()),
        client_ref=str(payload.get("client_ref") or "").strip(),
        username=str(payload.get("username") or "").strip(),
        email=str(payload.get("email") or "").strip(),
        password=password,
        realm_roles=roles,
        display_name=(str(payload["display_name"]).strip() if payload.get("display_name") else None),
    )


def build_register_command_envelope(
    *,
    client_ref: str,
    username: str,
    email: str,
    password: str | None = None,
    realm_roles: list[str],
    display_name: str | None = None,
    request_id: str | None = None,
) -> EventEnvelope:
    rid = request_id or str(uuid.uuid4())
    return auth_command_envelope(
        event_id=rid,
        event_type=AUTH_USER_REGISTER,
        payload={
            "request_id": rid,
            "client_ref": client_ref,
            "username": username,
            "email": email,
            "password": password,
            "realm_roles": list(realm_roles),
            "display_name": display_name,
        },
    )


async def register_user(command: RegisterUserCommand) -> EventEnvelope:
    """Create Keycloak user; return registered / register_failed event envelope."""
    if not command.client_ref or not command.username or not command.email:
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=AUTH_USER_REGISTER_FAILED,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "username": command.username,
                "email": command.email,
                "realm_roles": list(command.realm_roles),
                "error": "client_ref, username and email required",
            },
        )
    try:
        result: RegisterResult = await get_user_admin().register_user(
            username=command.username,
            email=command.email,
            password=command.password,
            realm_roles=command.realm_roles,
            display_name=command.display_name,
        )
    except AppError as exc:
        logger.warning(
            "auth register failed client_ref=%s code=%s detail=%s",
            command.client_ref,
            exc.code,
            exc.detail,
        )
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=AUTH_USER_REGISTER_FAILED,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "username": command.username,
                "email": command.email,
                "realm_roles": list(command.realm_roles),
                "error": exc.detail or exc.code,
                "error_code": exc.code,
            },
        )
    except Exception as exc:
        logger.exception("auth register unexpected client_ref=%s", command.client_ref)
        return auth_event_envelope(
            event_id=str(uuid.uuid4()),
            event_type=AUTH_USER_REGISTER_FAILED,
            payload={
                "request_id": command.request_id,
                "client_ref": command.client_ref,
                "username": command.username,
                "email": command.email,
                "realm_roles": list(command.realm_roles),
                "error": str(exc),
            },
        )

    return auth_event_envelope(
        event_id=str(uuid.uuid4()),
        event_type=AUTH_USER_REGISTERED,
        payload={
            "request_id": command.request_id,
            "client_ref": command.client_ref,
            "sub": result.keycloak_user_id,
            "username": result.username,
            "email": result.email,
            "realm_roles": list(result.realm_roles),
        },
    )


async def handle_auth_command_envelope(envelope: EventEnvelope) -> EventEnvelope | None:
    if envelope.bus != "auth_command":
        return None
    if envelope.event_type != AUTH_USER_REGISTER:
        logger.debug("auth command ignored type=%s", envelope.event_type)
        return None
    command = parse_register_command(envelope.payload or {})
    return await register_user(command)


async def publish_register_command(
    *,
    client_ref: str,
    username: str,
    email: str,
    password: str | None = None,
    realm_roles: list[str],
    display_name: str | None = None,
) -> bool:
    from prodavan.core.events.bus import publish_envelope

    envelope = build_register_command_envelope(
        client_ref=client_ref,
        username=username,
        email=email,
        password=password,
        realm_roles=realm_roles,
        display_name=display_name,
    )
    return await publish_envelope(envelope)
