"""Application contracts for transactionally durable automation outbox workers."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Protocol

from prodavan.application.automation_persistence.contracts import StaleCasOutcome
from prodavan.application.automation_service.contracts import DeliveryEgressPolicyPort

from prodavan.domain.automation.outbox import (
    OutboxEnqueueIntent,
    OutboxEvent,
    OutboxState,
    OutboxStateRecord,
)


class OutboxRepository(Protocol):
    """Persistence port with atomic record-and-enqueue-intent semantics.

    ``record_and_enqueue`` must commit the event, its initial state, and the
    queue intent in one transaction.  A duplicate idempotency key returns the
    existing event and ``False``; it must not create another intent.
    """

    async def record_and_enqueue(
        self,
        *,
        event: OutboxEvent,
        intent: OutboxEnqueueIntent,
    ) -> tuple[OutboxEvent, bool]: ...

    async def claim(
        self,
        *,
        now: datetime,
        owner_id: str,
        lease_until: datetime,
        limit: int = 1,
    ) -> list[tuple[OutboxEvent, OutboxStateRecord, int, str]]: ...

    async def mark_delivered(
        self,
        *,
        event_id: str,
        owner_id: str,
        lease_generation: int,
        lease_token: str,
        delivered_at: datetime,
    ) -> OutboxStateRecord: ...

    async def mark_retry(
        self,
        *,
        event_id: str,
        owner_id: str,
        lease_generation: int,
        lease_token: str,
        available_at: datetime,
        error: str,
    ) -> OutboxStateRecord: ...

    async def mark_dead_letter(
        self,
        *,
        event_id: str,
        owner_id: str,
        lease_generation: int,
        lease_token: str,
        dead_lettered_at: datetime,
        error: str,
    ) -> OutboxStateRecord: ...


class OutboxDispatcher(Protocol):
    """Side-effect port invoked once for each successfully claimed event."""

    async def dispatch(self, *, event: OutboxEvent) -> None: ...


def _safe_error(error: BaseException) -> str:
    """Persist only stable class and digest; never transport exception text."""
    error_class = f"{error.__class__.__module__}.{error.__class__.__qualname__}"
    digest = sha256(error_class.encode("utf-8")).hexdigest()
    return f"automation.error.delivery_failed:{error_class}:{digest}"


class OutboxWorker:
    """Small orchestration contract for a lease-owning outbox worker."""

    def __init__(
        self,
        *,
        repository: OutboxRepository,
        dispatcher: OutboxDispatcher,
        worker_id: str,
        egress_policy: DeliveryEgressPolicyPort | None = None,
        max_attempts: int = 5,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.repository = repository
        self.dispatcher = dispatcher
        self.worker_id = worker_id
        self.egress_policy = egress_policy
        self.max_attempts = max_attempts

    async def run_once(
        self,
        *,
        now: datetime,
        lease_until: datetime,
        retry_at: datetime,
        limit: int = 1,
    ) -> int:
        """Claim and dispatch due events, recording retry or dead-letter state.

        The repository owns compare-and-set lease checks.  A dispatch exception
        is converted into a retry until ``max_attempts`` is reached; after that
        the event is dead-lettered.  Successful dispatch is acknowledged only
        after the dispatcher returns.
        """
        if not self.worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if limit < 1:
            raise ValueError("limit must be positive")
        for value, name in ((now, "now"), (lease_until, "lease_until"), (retry_at, "retry_at")):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if lease_until <= now:
            raise ValueError("lease_until must be after now")

        claimed = await self.repository.claim(
            now=now,
            owner_id=self.worker_id,
            lease_until=lease_until,
            limit=limit,
        )
        completed = 0
        for event, state, lease_generation, lease_token in claimed:
            destination = event.payload.get("destination")
            policy_result = (
                self.egress_policy.check(destination=destination)
                if self.egress_policy is not None and isinstance(destination, str)
                else None
            )
            if policy_result is None or not policy_result.is_allowed:
                message = "automation.error.delivery_egress_denied"
                if state.attempts >= self.max_attempts:
                    await self.repository.mark_dead_letter(
                        event_id=event.event_id,
                        owner_id=self.worker_id,
                        lease_generation=lease_generation,
                        lease_token=lease_token,
                        dead_lettered_at=now,
                        error=message,
                    )
                else:
                    await self.repository.mark_retry(
                        event_id=event.event_id,
                        owner_id=self.worker_id,
                        lease_generation=lease_generation,
                        lease_token=lease_token,
                        available_at=retry_at,
                        error=message,
                    )
                continue
            try:
                await self.dispatcher.dispatch(event=event)
            except Exception as exc:
                message = _safe_error(exc)
                if state.attempts >= self.max_attempts:
                    await self.repository.mark_dead_letter(
                        event_id=event.event_id,
                        owner_id=self.worker_id,
                        lease_generation=lease_generation,
                        lease_token=lease_token,
                        dead_lettered_at=now,
                        error=message,
                    )
                else:
                    await self.repository.mark_retry(
                        event_id=event.event_id,
                        owner_id=self.worker_id,
                        lease_generation=lease_generation,
                        lease_token=lease_token,
                        available_at=retry_at,
                        error=message,
                    )
                continue

            await self.repository.mark_delivered(
                event_id=event.event_id,
                owner_id=self.worker_id,
                lease_generation=lease_generation,
                lease_token=lease_token,
                delivered_at=now,
            )
            completed += 1
        return completed


__all__ = ["OutboxDispatcher", "OutboxRepository", "OutboxWorker"]
