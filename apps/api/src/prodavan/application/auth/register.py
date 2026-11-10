"""Auth Service — authenticated Kafka commands and direct typed registration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from typing import Any

import httpx

from prodavan.application.auth.credentials import (
    RegistrationCredentialError,
    discard_registration_password,
    put_registration_password,
    take_registration_password,
)
from prodavan.application.auth.user_admin import RegisterResult, get_user_admin
from prodavan.core.events.auth_command import (
    AuthCommandRejected,
    verify_auth_command_envelope,
)
from prodavan.core.events.envelope import EventEnvelope, auth_command_envelope, auth_event_envelope
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_COMPANY, ROLE_EMPLOYEE

logger = logging.getLogger(__name__)

AUTH_USER_REGISTER = "auth.user.register"
AUTH_USER_REGISTERED = "auth.user.registered"
AUTH_USER_REGISTER_FAILED = "auth.user.register_failed"
_INTERNAL_ROLES = {"company": ROLE_COMPANY, "employee": ROLE_EMPLOYEE}


@dataclass(slots=True, frozen=True)
class RegisterUserCommand:
    """Typed direct-call contract; password_ref exists only at the bus edge."""

    request_id: str
    client_ref: str
    username: str
    email: str
    password: str | None
    realm_roles: list[str]
    display_name: str | None
    password_ref: str | None = None


def _reject(code: str, message: str) -> AuthCommandRejected:
    return AuthCommandRejected(code, message)


def parse_register_command(payload: dict[str, Any]) -> RegisterUserCommand:
    """Parse a verified command and enforce the existing internal registration shape."""

    # Reject even a null legacy field so plaintext cannot be smuggled through a
    # mixed old/new producer deployment or retained topic entry.
    if "password" in payload:
        raise _reject("AUTH_PLAINTEXT_PASSWORD_FORBIDDEN", "Plaintext registration passwords are forbidden")
    roles_raw = payload.get("realm_roles") or []
    if not isinstance(roles_raw, list) or any(not isinstance(role, str) for role in roles_raw):
        raise _reject("AUTH_REGISTRATION_ROLES_INVALID", "Registration roles must be a string list")
    roles = [role.strip() for role in roles_raw if role.strip()]
    password_ref = payload.get("password_ref")
    if password_ref is not None and not isinstance(password_ref, str):
        raise _reject("AUTH_CREDENTIAL_REF_INVALID", "Registration credential reference is invalid")
    command = RegisterUserCommand(
        request_id=str(payload.get("request_id") or "").strip(),
        client_ref=str(payload.get("client_ref") or "").strip(),
        username=str(payload.get("username") or "").strip(),
        email=str(payload.get("email") or "").strip(),
        password=None,
        realm_roles=roles,
        display_name=(str(payload["display_name"]).strip() if payload.get("display_name") else None),
        password_ref=password_ref.strip() if password_ref else None,
    )
    client_kind, separator, client_id = command.client_ref.partition(":")
    expected_role = _INTERNAL_ROLES.get(client_kind)
    if not separator or not client_id or expected_role is None or command.realm_roles != [expected_role]:
        raise _reject(
            "AUTH_REGISTRATION_PATH_FORBIDDEN",
            "Registration is restricted to internal company/employee commands",
        )
    return command


def build_register_command_envelope(
    *,
    client_ref: str,
    username: str,
    email: str,
    password_ref: str | None = None,
    realm_roles: list[str],
    display_name: str | None = None,
    request_id: str | None = None,
    signing_secret: str | None = None,
) -> EventEnvelope:
    """Build and sign a command; there is intentionally no password parameter."""

    rid = request_id or str(uuid.uuid4())
    return auth_command_envelope(
        event_id=rid,
        event_type=AUTH_USER_REGISTER,
        payload={
            "request_id": rid,
            "client_ref": client_ref,
            "username": username,
            "email": email,
            "password_ref": password_ref,
            "realm_roles": list(realm_roles),
            "display_name": display_name,
        },
        signing_secret=signing_secret,
    )


def _register_failed(
    command: RegisterUserCommand,
    *,
    error: str,
    error_code: str,
    retryable: bool,
) -> EventEnvelope:
    return auth_event_envelope(
        event_id=str(uuid.uuid4()),
        event_type=AUTH_USER_REGISTER_FAILED,
        payload={
            "request_id": command.request_id,
            "client_ref": command.client_ref,
            "username": command.username,
            "email": command.email,
            "realm_roles": list(command.realm_roles),
            "error": error,
            "error_code": error_code,
            # True after the bounded in-consumer retry budget means the internal
            # caller may issue a NEW command/ref; the old ref is never restored.
            "retryable": retryable,
        },
    )


async def register_user(command: RegisterUserCommand) -> EventEnvelope:
    """Direct typed registration; bus authentication/ref resolution is done by the caller."""

    if not command.client_ref or not command.username or not command.email:
        return _register_failed(
            command,
            error="client_ref, username and email required",
            error_code="VALIDATION_ERROR",
            retryable=False,
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
        retryable = exc.status == 429 or exc.status >= 500
        # Details from an IdP/client exception are not trusted to be secret-free.
        logger.warning(
            "auth register failed client_ref=%s code=%s status=%s",
            command.client_ref,
            exc.code,
            exc.status,
        )
        return _register_failed(
            command,
            error=(
                "identity provider temporarily unavailable"
                if retryable
                else "identity provider rejected registration"
            ),
            error_code=exc.code,
            retryable=retryable,
        )
    except Exception as exc:
        retryable = isinstance(exc, (httpx.HTTPError, TimeoutError, ConnectionError))
        # Do not use logger.exception or str(exc): either can contain request data.
        logger.error(
            "auth register unexpected client_ref=%s error_type=%s",
            command.client_ref,
            type(exc).__name__,
        )
        return _register_failed(
            command,
            error=(
                "identity provider temporarily unavailable"
                if retryable
                else "identity provider registration failed"
            ),
            error_code="IDENTITY_PROVIDER" if retryable else "AUTH_REGISTER_FAILED",
            retryable=retryable,
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


async def _register_from_verified_command(command: RegisterUserCommand) -> EventEnvelope:
    password: str | None = None
    if command.password_ref:
        try:
            password = await take_registration_password(command.password_ref)
        except RegistrationCredentialError:
            raise _reject(
                "AUTH_CREDENTIAL_STORE_UNAVAILABLE", "Registration credential store is unavailable"
            ) from None
        if password is None:
            raise _reject(
                "AUTH_CREDENTIAL_EXPIRED_OR_REUSED",
                "Registration credential is missing, expired or already consumed",
            )

    # The ref is consumed exactly once. Bounded retries reuse only the local
    # typed value, never the bus ref and never a restored/long-lived Redis key.
    # If Keycloak commits and the response is lost, register_user's existing
    # create-or-reuse + password/role reconciliation makes this retry idempotent.
    typed = replace(command, password=password, password_ref=None)
    from prodavan.config.settings import settings

    attempts = max(1, int(settings.auth_registration_retry_attempts))
    delay = max(0.0, float(settings.auth_registration_retry_delay_seconds))
    for attempt in range(1, attempts + 1):
        result = await register_user(typed)
        retryable = (
            result.event_type == AUTH_USER_REGISTER_FAILED and bool(result.payload.get("retryable"))
        )
        if not retryable or attempt == attempts:
            return result
        logger.warning(
            "auth register transient failure; retrying attempt=%s request_id=%s",
            attempt + 1,
            command.request_id,
        )
        if delay:
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable registration retry state")


async def handle_auth_command_envelope(
    envelope: EventEnvelope, *, signing_secret: str | None = None
) -> EventEnvelope | None:
    """Verify provenance before parsing or invoking any auth operation."""

    verify_auth_command_envelope(envelope, signing_secret=signing_secret)
    if envelope.bus != "auth_command":
        return None
    if envelope.event_type == AUTH_USER_REGISTER:
        command = parse_register_command(envelope.payload or {})
        if command.request_id != envelope.event_id:
            raise _reject("AUTH_COMMAND_REQUEST_MISMATCH", "Registration request id does not match event id")
        return await _register_from_verified_command(command)
    from prodavan.application.auth.lifecycle import handle_auth_lifecycle_command

    return await handle_auth_lifecycle_command(envelope)


async def publish_register_command(
    *,
    client_ref: str,
    username: str,
    email: str,
    password: str | None = None,
    realm_roles: list[str],
    display_name: str | None = None,
) -> bool:
    """Store a one-shot password in Redis, then publish only its signed ref."""

    from prodavan.core.events.bus import publish_envelope

    password_ref: str | None = None
    if password is not None and password.strip():
        password_ref = await put_registration_password(password)
    try:
        envelope = build_register_command_envelope(
            client_ref=client_ref,
            username=username,
            email=email,
            password_ref=password_ref,
            realm_roles=realm_roles,
            display_name=display_name,
        )
    except Exception:
        if password_ref:
            await discard_registration_password(password_ref)
        raise
    ok = await publish_envelope(envelope)
    if not ok and password_ref:
        await discard_registration_password(password_ref)
    return ok
