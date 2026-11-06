"""Persistence ports for target automation intent, outbox, and delivery state."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from prodavan.application.automation_service.contracts import DeliverySchedule, TaskRun
from prodavan.domain.automation.outbox import OutboxEvent, OutboxStateRecord

from .contracts import AutomationRecord, DeliveryCasResult, DeliveryClaim, IdempotencyClaim, LeaseToken, OutboxCasResult, RetryCommand, TaskCasResult, TaskClaim


class OperationNowPort(Protocol):
    def now(self) -> datetime: ...


class AutomationPersistencePort(Protocol):
    """Required durable adapter; implementations must use DB CAS predicates.

    There is intentionally no default/in-memory implementation. Until a
    concrete adapter persists lease token+generation and idempotency rows in one
    transaction, target automation remains blocked.
    """

    async def record_and_enqueue(self, *, record: AutomationRecord, idempotency: IdempotencyClaim) -> tuple[bool, TaskRun | None]: ...
    async def claim_task(self, *, task_id: str, owner_id: str, now: datetime, lease_until: datetime) -> TaskClaim | None: ...
    async def claim_delivery(self, *, delivery_id: str, owner_id: str, now: datetime, lease_until: datetime) -> DeliveryClaim | None: ...
    async def start_task(self, *, claim: TaskClaim, started_at: datetime) -> TaskCasResult: ...
    async def complete_task(self, *, claim: TaskClaim, completed_at: datetime) -> TaskCasResult: ...
    async def retry_task(self, *, claim: TaskClaim, error: str, now: datetime) -> TaskCasResult: ...
    async def dead_letter_task(self, *, claim: TaskClaim, failed_at: datetime, error: str) -> TaskCasResult: ...
    async def complete_delivery(self, *, claim: DeliveryClaim, delivered_at: datetime) -> DeliveryCasResult: ...
    async def retry_delivery(self, *, claim: DeliveryClaim, error: str, now: datetime) -> DeliveryCasResult: ...
    async def dead_letter_delivery(self, *, claim: DeliveryClaim, dead_lettered_at: datetime, error: str) -> DeliveryCasResult: ...
    async def claim_outbox(self, *, owner_id: str, now: datetime, lease_until: datetime, limit: int = 1) -> list[tuple[OutboxEvent, OutboxStateRecord, LeaseToken]]: ...
    async def complete_outbox(self, *, event_id: str, lease: LeaseToken, delivered_at: datetime) -> OutboxCasResult: ...
    async def retry_outbox(self, *, event_id: str, lease: LeaseToken, error: str, now: datetime) -> OutboxCasResult: ...
    async def dead_letter_outbox(self, *, event_id: str, lease: LeaseToken, error: str, dead_lettered_at: datetime) -> OutboxCasResult: ...
    async def release_outbox(self, *, event_id: str, lease: LeaseToken, now: datetime) -> OutboxCasResult: ...


AutomationIntentRepository = AutomationPersistencePort
OutboxPersistencePort = AutomationPersistencePort
OutboxRepository = AutomationPersistencePort
TaskRunRepository = AutomationPersistencePort
DeliveryRepository = AutomationPersistencePort
IdempotencyRepository = AutomationPersistencePort
DeliveryLeaseRepository = AutomationPersistencePort


class ProjectTriggersPort(Protocol):
    """Separate legacy queue; no target automation operations."""
    pass


__all__ = ["AutomationIntentRepository", "AutomationPersistencePort", "DeliveryLeaseRepository", "DeliveryRepository", "IdempotencyRepository", "OperationNowPort", "OutboxPersistencePort", "OutboxRepository", "ProjectTriggersPort", "TaskRunRepository"]
