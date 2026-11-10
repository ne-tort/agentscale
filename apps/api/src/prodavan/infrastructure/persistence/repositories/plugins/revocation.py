"""Canonical async grant revocation over the authoritative SQL repository.

There is no synchronous cache facade and no cold-deny refresh protocol.  Every
activity check reads the database through SqlCapabilityGrantRepository.  Revoke
performs one durable mutation; no follow-up synchronous revoke is issued.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ._schema import FLAG_SENTINEL_ID, PluginRepositoryError, text
from .grants import SqlCapabilityGrantRepository

DEFAULT_TTL_SECONDS = 30.0
"""Retained only as a constructor-compatibility default; no TTL authorizes."""


class DbGrantRevocation:
    def __init__(
        self,
        repository: SqlCapabilityGrantRepository,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Any = None,
    ) -> None:
        if not isinstance(repository, SqlCapabilityGrantRepository):
            raise PluginRepositoryError("DbGrantRevocation requires SqlCapabilityGrantRepository")
        if not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise PluginRepositoryError("ttl_seconds must be a positive number")
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._reads = 0

    @property
    def repository(self) -> SqlCapabilityGrantRepository:
        return self._repository

    @property
    def stats(self) -> dict[str, int]:
        """Diagnostic count of authoritative reads, never a decision source."""
        return {"reads": self._reads}

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PluginRepositoryError("DbGrantRevocation clock must return aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _normalize(grant_id: str) -> str:
        return (grant_id or "").strip() if isinstance(grant_id, str) else ""

    async def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        """Read authoritative status/expiry on every request, failing closed."""
        key = self._normalize(grant_id)
        if not key or key == FLAG_SENTINEL_ID:
            return False
        try:
            moment = now or self._now()
            if moment.tzinfo is None or moment.utcoffset() is None:
                return False
            self._reads += 1
            return bool(await self._repository.is_active_async(
                key, now=moment.astimezone(UTC)
            ))
        except Exception:
            return False

    async def is_revoked(self, grant_id: str) -> bool:
        return not await self.is_active(grant_id)

    async def revoke_async(self, *, grant_id: str, reason: str = "") -> bool:
        """Perform exactly one durable revoke through the grant repository."""
        key = text(grant_id, field="grant_id")
        return bool(await self._repository.revoke_async(key, reason=reason))

    async def revoke(self, grant_id: str, *, reason: str = "") -> bool:
        """Identity/admin async port shape over the same single durable action."""
        return await self.revoke_async(grant_id=grant_id, reason=reason)

    async def check(self, claims: Any) -> Any:
        from prodavan.application.plugins.identity.types import (
            PluginTokenError,
            PluginTokenErrorCode,
        )

        grant_id = self._normalize(getattr(claims, "grant_id", ""))
        if not await self.is_active(grant_id):
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="capability grant is revoked or revocation source unavailable",
            )
        return claims


__all__ = ["DEFAULT_TTL_SECONDS", "DbGrantRevocation"]
