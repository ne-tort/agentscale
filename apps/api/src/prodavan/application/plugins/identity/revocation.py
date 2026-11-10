"""Async grant-revocation ports for the plugin identity trust domain."""
from __future__ import annotations

import inspect
import threading
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .types import PluginClaims, PluginTokenError, PluginTokenErrorCode


@runtime_checkable
class RevocationPort(Protocol):
    async def is_active(self, grant_id: str, *, now: datetime) -> bool: ...
    async def revoke(self, grant_id: str) -> bool: ...


class InMemoryRevocation:
    """Async test/dev implementation; never an authoritative cluster store."""

    def __init__(self, *, initially_revoked: tuple[str, ...] | list[str] | None = None) -> None:
        self._revoked: set[str] = set(initially_revoked or ())
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(grant_id: str) -> str:
        return (grant_id or "").strip()

    async def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        key = self._normalize(grant_id)
        if not key:
            return False
        with self._lock:
            return key not in self._revoked

    async def is_revoked(self, grant_id: str) -> bool:
        return not await self.is_active(grant_id)

    async def revoke(self, grant_id: str) -> bool:
        key = self._normalize(grant_id)
        if not key:
            raise ValueError("grant_id must not be empty")
        with self._lock:
            self._revoked.add(key)
        return True

    async def activate(self, grant_id: str) -> None:
        key = self._normalize(grant_id)
        with self._lock:
            self._revoked.discard(key)

    @property
    def revoked_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._revoked)


class GrantRevocation:
    """Fail-closed async check used by verify -> check -> allowlist."""

    def __init__(self, port: RevocationPort | None = None) -> None:
        self._port = port

    @property
    def port(self) -> RevocationPort | None:
        return self._port

    def bind(self, port: RevocationPort) -> None:
        self._port = port

    async def is_active(
        self, grant_id: str, *, now: int | float | datetime | None = None
    ) -> bool:
        key = (grant_id or "").strip()
        if not key or self._port is None:
            return False
        try:
            moment = _utc_datetime(now)
            result = self._port.is_active(key, now=moment)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            return False

    async def is_revoked(self, grant_id: str) -> bool:
        return not await self.is_active(grant_id)

    async def revoke(self, grant_id: str) -> bool:
        if self._port is None:
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="revocation port is not configured; cannot deactivate grant",
            )
        key = (grant_id or "").strip()
        if not key:
            raise PluginTokenError(
                code=PluginTokenErrorCode.MISSING_CLAIM,
                detail="cannot revoke an empty grant_id",
            )
        result = self._port.revoke(key)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def check(self, claims: PluginClaims) -> PluginClaims:
        if not await self.is_active(claims.grant_id):
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="capability grant is revoked or revocation source unavailable",
            )
        return claims


def _utc_datetime(now: int | float | datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if isinstance(now, datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise PluginTokenError(
                code=PluginTokenErrorCode.MALFORMED,
                detail="revocation now datetime must be timezone-aware",
            )
        return now.astimezone(UTC)
    return datetime.fromtimestamp(float(now), tz=UTC)
