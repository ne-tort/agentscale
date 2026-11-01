"""Application boundary adapter for target automation persistence."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from prodavan.application.automation_service.contracts import DeliverySchedule, TaskRun, WebhookIngress, require_aware
from prodavan.domain.automation.outbox import OutboxEnqueueIntent, OutboxEvent, OutboxStateRecord

from .contracts import AutomationRecord, CanonicalAutomationEnvelope, DeliveryCasResult, DeliveryClaim, IdempotencyClaim, LeaseToken, OutboxCasResult, RetryCommand, TaskCasResult, TaskClaim
from .ports import AutomationPersistencePort, OperationNowPort


class BlockedAutomationPersistenceAdapter:
    """Default adapter while no reviewed durable DB implementation exists."""

    def __getattr__(self, _name: str) -> Any:
        async def blocked(**_kwargs: Any) -> Any:
            raise RuntimeError("automation persistence is blocked: concrete DB adapter required")

        return blocked


class AutomationPersistenceBoundary:
    """Translate commands into atomic, tenant-scoped and fenced persistence calls."""

    def __init__(self, *, persistence: AutomationPersistencePort, operation_now: OperationNowPort, lease_seconds: int = 60) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self._persistence = persistence
        self._operation_now = operation_now
        self._lease_seconds = lease_seconds

    async def record_and_enqueue(self, *, ingress: WebhookIngress, tenant_id: str, subscription_id: str, task_name: str, payload: dict[str, Any], operation: str = "webhook.ingress", queue_name: str = "automation") -> tuple[bool, TaskRun | None]:
        envelope = CanonicalAutomationEnvelope.from_ingress(ingress=ingress, tenant_id=tenant_id, subscription_id=subscription_id, task_name=task_name, payload=payload)
        now = require_aware(self._operation_now.now(), name="operation_now")
        event = OutboxEvent(event_id=envelope.idempotency_key, tenant_id=envelope.tenant_id, aggregate_type="automation.subscription", aggregate_id=envelope.subscription_id, aggregate_version=1, event_type=operation, payload={"source": envelope.source, "source_event_id": envelope.source_event_id, "body": envelope.body.decode("utf-8", errors="surrogateescape"), "payload": payload}, occurred_at=now)
        initial_state = OutboxStateRecord(event_id=event.event_id, available_at=now)
        enqueue_intent = OutboxEnqueueIntent(event_id=event.event_id, queue_name=queue_name, deduplication_key=event.idempotency_key)
        record = AutomationRecord(envelope=envelope, task_name=task_name, payload=payload, event=event, initial_state=initial_state, enqueue_intent=enqueue_intent)
        claim = IdempotencyClaim(key=record.idempotency_key, request_hash=record.request_hash, operation=operation, tenant_id=tenant_id, subscription_id=subscription_id, source_event_id=envelope.source_event_id)
        return await self._persistence.record_and_enqueue(record=record, idempotency=claim)

    async def claim_task(self, *, task_id: str, owner_id: str, now: datetime) -> TaskClaim | None:
        now = require_aware(now, name="now")
        return await self._persistence.claim_task(task_id=task_id, owner_id=_owner(owner_id), now=now, lease_until=now + timedelta(seconds=self._lease_seconds))

    async def claim_delivery(self, *, delivery_id: str, owner_id: str, now: datetime) -> DeliveryClaim | None:
        now = require_aware(now, name="now")
        return await self._persistence.claim_delivery(delivery_id=delivery_id, owner_id=_owner(owner_id), now=now, lease_until=now + timedelta(seconds=self._lease_seconds))

    async def start_task(self, *, claim: TaskClaim, started_at: datetime) -> TaskCasResult:
        started_at = require_aware(started_at, name="started_at")
        _validate_lease(claim.lease, started_at)
        return await self._persistence.start_task(claim=claim, started_at=started_at)

    async def complete_task(self, *, claim: TaskClaim, completed_at: datetime) -> TaskCasResult:
        _validate_lease(claim.lease, completed_at)
        return await self._persistence.complete_task(claim=claim, completed_at=completed_at)

    async def retry_task(self, *, claim: TaskClaim, error: str) -> TaskCasResult:
        now = _operation_now(self._operation_now)
        _validate_lease(claim.lease, now)
        return await self._persistence.retry_task(claim=claim, error=error, now=now)

    async def dead_letter_task(self, *, claim: TaskClaim, failed_at: datetime, error: str) -> TaskCasResult:
        failed_at = require_aware(failed_at, name="failed_at")
        _validate_lease(claim.lease, failed_at)
        return await self._persistence.dead_letter_task(claim=claim, failed_at=failed_at, error=_error(error))

    async def complete_delivery(self, *, claim: DeliveryClaim, delivered_at: datetime) -> DeliveryCasResult:
        _validate_lease(claim.lease, delivered_at)
        return await self._persistence.complete_delivery(claim=claim, delivered_at=delivered_at)

    async def retry_delivery(self, *, claim: DeliveryClaim, error: str) -> DeliveryCasResult:
        now = _operation_now(self._operation_now)
        _validate_lease(claim.lease, now)
        return await self._persistence.retry_delivery(claim=claim, error=error, now=now)

    async def dead_letter_delivery(self, *, claim: DeliveryClaim, dead_lettered_at: datetime, error: str) -> DeliveryCasResult:
        dead_lettered_at = require_aware(dead_lettered_at, name="dead_lettered_at")
        _validate_lease(claim.lease, dead_lettered_at)
        return await self._persistence.dead_letter_delivery(claim=claim, dead_lettered_at=dead_lettered_at, error=_error(error))

    async def complete_outbox(self, *, event_id: str, lease: LeaseToken, delivered_at: datetime) -> OutboxCasResult:
        delivered_at = require_aware(delivered_at, name="delivered_at")
        _validate_lease(lease, delivered_at)
        return await self._persistence.complete_outbox(event_id=event_id, lease=lease, delivered_at=delivered_at)

    async def retry_outbox(self, *, event_id: str, lease: LeaseToken, error: str) -> OutboxCasResult:
        now = _operation_now(self._operation_now)
        _validate_lease(lease, now)
        return await self._persistence.retry_outbox(event_id=event_id, lease=lease, error=_error(error), now=now)

    async def dead_letter_outbox(self, *, event_id: str, lease: LeaseToken, error: str, dead_lettered_at: datetime) -> OutboxCasResult:
        dead_lettered_at = require_aware(dead_lettered_at, name="dead_lettered_at")
        _validate_lease(lease, dead_lettered_at)
        return await self._persistence.dead_letter_outbox(event_id=event_id, lease=lease, error=_error(error), dead_lettered_at=dead_lettered_at)

    async def release_outbox(self, *, event_id: str, lease: LeaseToken) -> OutboxCasResult:
        now = _operation_now(self._operation_now)
        _validate_lease(lease, now)
        return await self._persistence.release_outbox(event_id=event_id, lease=lease, now=now)


def _operation_now(clock: OperationNowPort) -> datetime:
    return require_aware(clock.now(), name="operation_now")


def _owner(owner_id: str) -> str:
    if not owner_id.strip():
        raise ValueError("owner_id is required")
    return owner_id


def _error(error: str | BaseException) -> str:
    """Return stable class/code plus class digest; never persist raw text."""
    if isinstance(error, BaseException):
        error_class = f"{error.__class__.__module__}.{error.__class__.__qualname__}"
    else:
        if not error.strip():
            raise ValueError("error is required")
        error_class = "application_error"
    digest = sha256(error_class.encode("utf-8")).hexdigest()
    return f"automation.error.failure:{error_class}:{digest}"


def _validate_lease(lease: LeaseToken, now: datetime) -> None:
    if lease.lease_until <= require_aware(now, name="timestamp"):
        raise ValueError("lease has expired")


AutomationPersistenceService = AutomationPersistenceBoundary
__all__ = ["AutomationPersistenceBoundary", "AutomationPersistenceService", "BlockedAutomationPersistenceAdapter"]
