"""Fail-closed service for canonical durable quarantine records."""
from __future__ import annotations

from typing import Final

from .contracts import QuarantineRecord
from .ports import DurableQuarantineRepository


class QuarantineDuplicateConflict(ValueError):
    """Same scoped idempotency key was presented with different immutable evidence."""


class DurableQuarantineService:
    """Create and persist quarantine evidence without ever producing trusted success."""

    TRUSTED_SUCCESS: Final[bool] = False

    def __init__(self, repository: DurableQuarantineRepository) -> None:
        self._repository = repository

    async def record(self, record: QuarantineRecord) -> QuarantineRecord:
        """Persist once; an identical retry returns the original record."""
        existing = await self._repository.find_by_idempotency_key(
            tenant_id=record.partition.tenant_id,
            idempotency_key=record.idempotency_key,
        )
        if existing is not None:
            if existing.fingerprint != record.fingerprint:
                raise QuarantineDuplicateConflict(
                    "idempotency key already identifies different quarantine evidence"
                )
            return existing
        stored = await self._repository.put_if_absent(record)
        if stored.fingerprint != record.fingerprint:
            raise QuarantineDuplicateConflict(
                "repository returned conflicting quarantine evidence"
            )
        return stored

    @staticmethod
    def assert_not_trusted(record: QuarantineRecord) -> None:
        """Defensive guard for callers that might otherwise confuse quarantine with allow."""
        if record.reason is None:  # pragma: no cover - impossible for a valid contract
            raise ValueError("quarantine record must have a reason")


__all__ = ["DurableQuarantineService", "QuarantineDuplicateConflict"]
