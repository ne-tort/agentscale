"""Application service skeleton for automation contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from prodavan.application.automation_persistence.contracts import (
    Conflict,
    DeliveryClaim,
    DeliveryCasResult,
    LeaseToken,
    StaleCasOutcome,
    TaskCasResult,
    TaskClaim,
)
from prodavan.application.automation_service.contracts import (
    AutomationRepositoryPort,
    ClockPort,
    DeliveryEgressPolicyPort,
    DeliverySchedule,
    DeliveryStatus,
    DeliveryTransportPort,
    DurableError,
    IngressResult,
    SignatureVerifierPort,
    durable_error,
    TaskQueuePort,
    TaskRun,
    TaskRunStatus,
    WebhookIngress,
    require_aware,
    task_idempotency_key,
)


class AutomationService:
    """Coordinates automation workflows while leaving side effects to ports."""

    def __init__(
        self,
        *,
        repository: AutomationRepositoryPort,
        clock: ClockPort,
        verifier: SignatureVerifierPort,
        queue: TaskQueuePort,
        transport: DeliveryTransportPort,
        egress_policy: DeliveryEgressPolicyPort | None = None,
        max_attempts: int = 3,
        lease_seconds: int = 60,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self._repository = repository
        self._clock = clock
        self._verifier = verifier
        self._queue = queue
        self._transport = transport
        self._egress_policy = egress_policy
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds

    async def ingest_webhook(
        self,
        *,
        ingress: WebhookIngress,
        task_name: str,
        secret_ref: str,
        payload: dict[str, Any],
    ) -> IngressResult:
        """Verify, deduplicate, persist ingress, and enqueue exactly once."""
        if not secret_ref or not secret_ref.strip():
            raise ValueError("secret_ref must not be empty")
        if not self._verifier.verify(body=ingress.body, headers=ingress.headers, secret_ref=secret_ref):
            return IngressResult(accepted=False)
        # The repository must claim the event and create its durable task/outbox
        # intent in one transaction. A queue failure is recoverable by an outbox
        # drainer; it must never turn into a second task creation.
        recorded, run = await self._repository.record_and_enqueue(
            ingress=ingress,
            task_name=task_name,
            payload=payload,
        )
        if not recorded:
            # Re-drive the durable outbox intent as well: queue.enqueue must be
            # idempotent, so a retry after a queue outage repairs the gap.
            if run is not None:
                await self._queue.enqueue(
                    task_id=run.id,
                    idempotency_key=task_idempotency_key(ingress=ingress),
                )
            return IngressResult(accepted=True, duplicate=True, task_id=run.id if run else None)
        if run is None:
            raise RuntimeError("repository accepted ingress without creating task run")
        await self._queue.enqueue(
            task_id=run.id,
            idempotency_key=task_idempotency_key(ingress=ingress),
        )
        return IngressResult(accepted=True, task_id=run.id)

    async def lease_task_run(self, *, task_id: str, worker_id: str) -> TaskClaim | None:
        now = require_aware(self._clock.now(), name="clock.now()")
        claimed = await self._repository.claim_task(
            task_id=task_id,
            worker_id=worker_id,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        return claimed if isinstance(claimed, TaskClaim) else None

    async def start_task_run(self, *, claim: TaskClaim) -> bool:
        lease = claim.lease
        _validate_lease(lease, self._clock.now())
        result = await self._repository.start_task(claim=claim, started_at=require_aware(self._clock.now(), name="clock.now()"))
        return _cas_applied(result)

    async def complete_task_run(self, *, claim: TaskClaim) -> bool:
        lease = claim.lease
        _validate_lease(lease, self._clock.now())
        result = await self._repository.complete_task(claim=claim, completed_at=require_aware(self._clock.now(), name="clock.now()"))
        return _cas_applied(result)

    async def fail_task_run(
        self, *, claim: TaskClaim, error: BaseException, retry_after_seconds: int = 30
    ) -> bool:
        """Record a failure and report whether a retry was scheduled.

        The boolean reports the *outcome for the caller*: ``True`` only when the
        run was moved to ``RETRY_WAIT`` and that CAS was applied. A dead-lettered
        run is terminal, so it must report ``False`` — otherwise a worker would
        treat an exhausted run as queued for another attempt and re-drive work
        that was already declared failed.
        """
        run = claim.run
        lease = claim.lease
        now = require_aware(self._clock.now(), name="clock.now()")
        _validate_lease(lease, now)
        safe = _safe_error(error, code="task_failed")
        if run.attempts >= self._max_attempts:
            await self._repository.dead_letter_task(claim=claim, error=safe, failed_at=now)
            return False
        result = await self._repository.retry_task(claim=claim, error=safe, now=now)
        return _cas_applied(result)

    async def schedule_delivery(self, *, delivery: DeliverySchedule) -> None:
        await self._repository.schedule_delivery(delivery=delivery)

    async def deliver(self, *, delivery: DeliverySchedule, worker_id: str) -> bool:
        now = require_aware(self._clock.now(), name="clock.now()")
        if delivery.status in {DeliveryStatus.SCHEDULED, DeliveryStatus.RETRY_WAIT}:
            if require_aware(delivery.available_at, name="delivery available_at") > now:
                return False
        elif delivery.status == DeliveryStatus.LEASED:
            if delivery.lease_until is None or require_aware(delivery.lease_until, name="delivery lease_until") > now:
                return False
        else:
            return False
        claimed = await self._repository.claim_delivery(
            delivery_id=delivery.id,
            worker_id=worker_id,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        if claimed is None:
            return False
        if isinstance(claimed, DeliveryClaim):
            delivery, claim_lease = claimed.delivery, claimed.lease
        else:
            return False
        if self._egress_policy is None or not self._egress_policy.check(destination=delivery.destination).is_allowed:
            return False
        try:
            await self._transport.deliver(
                destination=delivery.destination,
                payload=delivery.payload,
                idempotency_key=delivery.id,
            )
        except Exception as exc:
            safe = _safe_error(exc, code="delivery_failed")
            if delivery.attempts >= self._max_attempts:
                result = await self._repository.dead_letter_delivery(claim=DeliveryClaim(delivery=delivery, lease=claim_lease), dead_lettered_at=now, error=safe)
                return _cas_applied(result)
            result = await self._repository.retry_delivery(claim=DeliveryClaim(delivery=delivery, lease=claim_lease), error=safe, now=now)
            return _cas_applied(result)
        result = await self._repository.complete_delivery(claim=DeliveryClaim(delivery=delivery, lease=claim_lease), delivered_at=now)
        return _cas_applied(result)


def _safe_error(error: BaseException | str, *, code: str) -> DurableError:
    if isinstance(error, BaseException):
        return durable_error(error, code=code)
    return DurableError(code=f"automation.error.{code}", digest=sha256(b"application_error").hexdigest())


def _cas_applied(result: TaskCasResult | DeliveryCasResult) -> bool:
    return not isinstance(result, (Conflict, StaleCasOutcome))


def _validate_lease(lease: LeaseToken, now: datetime) -> None:
    if lease.lease_until <= require_aware(now, name="timestamp"):
        raise ValueError("lease has expired")


__all__ = [
    "AutomationService",
    "DeliverySchedule",
    "DeliveryStatus",
    "IngressResult",
    "TaskRun",
    "TaskRunStatus",
    "WebhookIngress",
]
