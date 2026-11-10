"""Async durable revocation for composition-root grant-store implementations."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol, runtime_checkable

from prodavan.application.plugins.wiring.config import PluginPlatformConfigError

GRANT_STATUS_ACTIVE: Final[str] = "active"
GRANT_STATUS_REVOKED: Final[str] = "revoked"
DEFAULT_CACHE_TTL_SECONDS: Final[float] = 1.0


class GrantRevocationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GrantSnapshot:
    status: str
    raw: Any = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status == GRANT_STATUS_ACTIVE


@runtime_checkable
class GrantStorePort(Protocol):
    async def get_grant(self, grant_id: str) -> GrantSnapshot | Any | None: ...
    async def deactivate(self, grant_id: str) -> bool: ...


def _attr(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _aware_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError("grant timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("grant timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _coerce_snapshot(value: Any) -> GrantSnapshot:
    if value is None:
        raise LookupError("grant not found")
    if isinstance(value, GrantSnapshot):
        snapshot = value
    else:
        status = _attr(value, "status")
        if status is None:
            raise ValueError("grant store returned an object without status")
        snapshot = GrantSnapshot(
            status=str(getattr(status, "value", status)).strip().lower(),
            raw=value,
            issued_at=_aware_utc(_attr(value, "issued_at", _attr(value, "created_at"))),
            expires_at=_aware_utc(_attr(value, "expires_at")),
            revoked_at=_aware_utc(_attr(value, "revoked_at")),
        )
    normalized = GrantSnapshot(
        status=str(snapshot.status).strip().lower(),
        raw=snapshot.raw,
        issued_at=_aware_utc(snapshot.issued_at),
        expires_at=_aware_utc(snapshot.expires_at),
        revoked_at=_aware_utc(snapshot.revoked_at),
    )
    if normalized.status not in (GRANT_STATUS_ACTIVE, GRANT_STATUS_REVOKED):
        raise ValueError("unsupported grant status")
    if normalized.status == GRANT_STATUS_ACTIVE and normalized.revoked_at is not None:
        return GrantSnapshot(
            status=GRANT_STATUS_REVOKED,
            raw=normalized.raw,
            issued_at=normalized.issued_at,
            expires_at=normalized.expires_at,
            revoked_at=normalized.revoked_at,
        )
    return normalized


class DurableGrantRevocation:
    """Read the durable store on every check; no TTL can authorize a request."""

    def __init__(
        self,
        store: GrantStorePort | None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Any = None,
    ) -> None:
        if store is None:
            raise PluginPlatformConfigError(
                "DurableGrantRevocation requires a GrantStorePort; no in-memory fallback"
            )
        if cache_ttl_seconds < 0 or cache_ttl_seconds != cache_ttl_seconds:
            raise PluginPlatformConfigError("cache_ttl_seconds must be non-negative")
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self, now: datetime | None) -> datetime:
        value = self._clock() if now is None else now
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _normalize(grant_id: str) -> str:
        return (grant_id or "").strip() if isinstance(grant_id, str) else ""

    async def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        key = self._normalize(grant_id)
        if not key:
            return False
        try:
            moment = self._now(now)
            raw = self._store.get_grant(key)
            if inspect.isawaitable(raw):
                raw = await raw
            snapshot = _coerce_snapshot(raw)
            if not snapshot.active or snapshot.revoked_at is not None:
                return False
            if snapshot.issued_at is not None and moment < snapshot.issued_at:
                return False
            if snapshot.expires_at is not None and moment >= snapshot.expires_at:
                return False
            return True
        except Exception:
            return False

    async def is_revoked(self, grant_id: str) -> bool:
        return not await self.is_active(grant_id)

    async def revoke(self, grant_id: str) -> bool:
        key = self._normalize(grant_id)
        if not key:
            raise ValueError("grant_id must not be empty")
        try:
            result = self._store.deactivate(key)
            confirmed = bool(await result) if inspect.isawaitable(result) else bool(result)
        except Exception as exc:
            raise GrantRevocationUnavailable(
                "grant store is unavailable; revoke is NOT confirmed"
            ) from exc
        if not confirmed:
            raise GrantRevocationUnavailable("grant store did not confirm deactivation")
        return True

    async def check(self, claims: Any) -> Any:
        from prodavan.application.plugins.identity.types import (
            PluginTokenError,
            PluginTokenErrorCode,
        )

        grant_id = self._normalize(_attr(claims, "grant_id", ""))
        if not await self.is_active(grant_id):
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="capability grant is revoked or grant store unavailable",
            )
        return claims

    @property
    def cached_ids(self) -> frozenset[str]:
        """Compatibility diagnostic: authoritative mode intentionally has no cache."""
        return frozenset()


__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "GRANT_STATUS_ACTIVE",
    "GRANT_STATUS_REVOKED",
    "DurableGrantRevocation",
    "GrantRevocationUnavailable",
    "GrantSnapshot",
    "GrantStorePort",
]
