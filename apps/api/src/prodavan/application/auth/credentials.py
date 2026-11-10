"""Short-lived, one-shot registration passwords backed by shared Redis.

Kafka carries only a 256-bit opaque reference. No process-local/file fallback
is intentional: every API replica and the trusted consumer must see the same
single-use credential lifecycle.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from prodavan.core.infra.redis_manager import get_redis_manager

_KEY_PREFIX = "prodavan:auth:registration:password:"
_REF_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class RegistrationCredentialError(RuntimeError):
    """Credential storage failure; messages never contain a password or ref."""


def _ttl_seconds() -> int:
    from prodavan.config.settings import settings

    return max(1, int(settings.auth_registration_password_ttl_seconds))


def _client() -> Any:
    manager = get_redis_manager()
    if manager is None or not manager.enabled:
        raise RegistrationCredentialError("shared Redis is required for registration credentials")
    try:
        return manager.client
    except Exception:
        raise RegistrationCredentialError("shared Redis is not started") from None


def _key(password_ref: str) -> str:
    if not isinstance(password_ref, str) or _REF_RE.fullmatch(password_ref) is None:
        raise RegistrationCredentialError("invalid registration credential reference")
    return f"{_KEY_PREFIX}{password_ref}"


async def put_registration_password(password: str, *, ttl_seconds: int | None = None) -> str:
    """Store a password with SET NX EX and return only its opaque reference."""

    if not isinstance(password, str) or not password.strip():
        raise RegistrationCredentialError("registration password must be non-empty")
    password_ref = secrets.token_urlsafe(32)
    ttl = max(1, int(ttl_seconds if ttl_seconds is not None else _ttl_seconds()))
    try:
        stored = await _client().set(_key(password_ref), password, nx=True, ex=ttl)
    except RegistrationCredentialError:
        raise
    except Exception:
        raise RegistrationCredentialError("could not store registration credential") from None
    if not stored:
        # A 256-bit collision is not realistically retryable data; fail closed.
        raise RegistrationCredentialError("could not allocate registration credential")
    return password_ref


async def take_registration_password(password_ref: str) -> str | None:
    """Atomically GETDEL a credential; missing, expired and reused refs all return None."""

    try:
        value = await _client().getdel(_key(password_ref))
    except RegistrationCredentialError:
        raise
    except Exception:
        raise RegistrationCredentialError("could not consume registration credential") from None
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


async def discard_registration_password(password_ref: str) -> None:
    """Delete an unpublished credential without reading it (best-effort cleanup)."""

    try:
        await _client().delete(_key(password_ref))
    except Exception:
        # The short Redis TTL remains the backstop; never log the ref or value.
        return
