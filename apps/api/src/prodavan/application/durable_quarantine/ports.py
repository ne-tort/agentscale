"""Ports for durable quarantine; implementations own storage transactions."""
from __future__ import annotations

from typing import Protocol

from .contracts import QuarantineRecord


class DurableQuarantineRepository(Protocol):
    """Exclusive append/read port; no authorization or trusted-success methods."""

    async def find_by_idempotency_key(self, *, tenant_id: str, idempotency_key: str) -> QuarantineRecord | None: ...

    async def put_if_absent(self, record: QuarantineRecord) -> QuarantineRecord: ...


__all__ = ["DurableQuarantineRepository"]
