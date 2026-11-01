"""Source tests for the automation application service contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.automation_persistence.contracts import (
    Conflict,
    DeliveryClaim,
    DeliveryCasResult,
    LeaseToken,
    StaleCasOutcome,
    TaskClaim,
    TaskCasResult,
)
from prodavan.application.automation_service.contracts import (
    DeliveryEgressPolicyResult,
    DeliverySchedule,
    DeliveryStatus,
    TaskRun,
    TaskRunStatus,
    WebhookIngress,
)
from prodavan.application.automation_service.service import AutomationService


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class FakeVerifier:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid

    def verify(self, *, body: bytes, headers: dict[str, str], secret_ref: str) -> bool:
        return self.valid


class FakeRepository:
    def __init__(self) -> None:
        self.ingress: set[tuple[str, str]] = set()
        self.ingress_tasks: dict[tuple[str, str], str] = {}
        self.tasks: dict[str, TaskRun] = {}
        self.deliveries: dict[str, DeliverySchedule] = {}
        self.recorded: list[WebhookIngress] = []
        self.next_id = 1
        self.generation: dict[str, int] = {}

    async def record_and_enqueue(
        self, *, ingress: WebhookIngress, task_name: str, payload: dict
    ) -> tuple[bool, TaskRun | None]:
        key = (ingress.source, ingress.event_id)
        if key in self.ingress:
            task_id = self.ingress_tasks[key]
            return False, self.tasks[task_id]
        self.ingress.add(key)
        self.recorded.append(ingress)
        run = TaskRun(id=f"task-{self.next_id}", task_name=task_name, payload=payload)
        self.next_id += 1
        self.tasks[run.id] = run
        self.ingress_tasks[key] = run.id
        return True, run

    async def claim_task(
        self, *, task_id: str, worker_id: str, now: datetime, lease_until: datetime
    ) -> TaskClaim | None:
        run = self.tasks.get(task_id)
        if run is None:
            return None
        claimable = run.status in {TaskRunStatus.QUEUED, TaskRunStatus.RETRY_WAIT}
        reclaimable = run.status in {TaskRunStatus.LEASED, TaskRunStatus.RUNNING} and (
            run.lease_until is not None and run.lease_until <= now
        )
        if not (claimable or reclaimable):
            return None
        generation = self.generation.get(task_id, 0) + 1
        self.generation[task_id] = generation
        run.status = TaskRunStatus.LEASED
        run.attempts += 1
        run.leased_by = worker_id
        run.lease_until = lease_until
        token = LeaseToken.issue(owner_id=worker_id, generation=generation, lease_until=lease_until)
        run.lease_generation = generation
        run.lease_token = token.token
        return TaskClaim(run=run, lease=token)

    async def _task_cas(self, *, claim: TaskClaim, mutate) -> TaskCasResult:
        current = self.tasks.get(claim.run.id)
        if current is not claim.run or current.lease_generation != claim.lease.generation or current.lease_token != claim.lease.token:
            return StaleCasOutcome(applied=False)
        mutate(current)
        return current

    async def start_task(self, *, claim: TaskClaim, started_at: datetime) -> TaskCasResult:
        return await self._task_cas(claim=claim, mutate=lambda run: setattr(run, "status", TaskRunStatus.RUNNING))

    async def complete_task(self, *, claim: TaskClaim, completed_at: datetime) -> TaskCasResult:
        def mutate(run: TaskRun) -> None:
            run.status = TaskRunStatus.SUCCEEDED
            run.leased_by = run.lease_until = run.lease_generation = run.lease_token = None
        return await self._task_cas(claim=claim, mutate=mutate)

    async def retry_task(self, *, claim: TaskClaim, error, now: datetime) -> TaskCasResult:
        def mutate(run: TaskRun) -> None:
            run.status = TaskRunStatus.RETRY_WAIT
            run.available_at = now + timedelta(seconds=30)
            run.last_error = error
            run.leased_by = run.lease_until = run.lease_generation = run.lease_token = None
        return await self._task_cas(claim=claim, mutate=mutate)

    async def dead_letter_task(self, *, claim: TaskClaim, error, failed_at: datetime) -> TaskCasResult:
        def mutate(run: TaskRun) -> None:
            run.status = TaskRunStatus.FAILED
            run.last_error = error
            run.leased_by = run.lease_until = run.lease_generation = run.lease_token = None
        return await self._task_cas(claim=claim, mutate=mutate)

    async def get_task_run(self, *, task_id: str) -> TaskRun | None:
        return self.tasks.get(task_id)

    async def schedule_delivery(self, *, delivery: DeliverySchedule) -> None:
        self.deliveries[delivery.id] = delivery

    async def claim_delivery(
        self, *, delivery_id: str, worker_id: str, now: datetime, lease_until: datetime
    ) -> DeliverySchedule | None:
        delivery = self.deliveries.get(delivery_id)
        if delivery is None:
            return None
        claimable = delivery.status in {DeliveryStatus.SCHEDULED, DeliveryStatus.RETRY_WAIT}
        reclaimable = delivery.status == DeliveryStatus.LEASED and (
            delivery.lease_until is not None and delivery.lease_until <= now
        )
        if not (claimable or reclaimable):
            return None
        generation = self.generation.get(delivery_id, 0) + 1
        self.generation[delivery_id] = generation
        delivery.status = DeliveryStatus.LEASED
        delivery.attempts += 1
        delivery.leased_by = worker_id
        delivery.lease_until = lease_until
        token = LeaseToken.issue(owner_id=worker_id, generation=generation, lease_until=lease_until)
        delivery.lease_generation = generation
        delivery.lease_token = token.token
        return DeliveryClaim(delivery=delivery, lease=token)

    async def _delivery_cas(self, *, claim: DeliveryClaim, mutate) -> DeliveryCasResult:
        current = self.deliveries.get(claim.delivery.id)
        if current is not claim.delivery or current.lease_generation != claim.lease.generation or current.lease_token != claim.lease.token:
            return StaleCasOutcome(applied=False)
        mutate(current)
        return current

    async def complete_delivery(self, *, claim: DeliveryClaim, delivered_at: datetime) -> DeliveryCasResult:
        def mutate(delivery: DeliverySchedule) -> None:
            delivery.status = DeliveryStatus.SENT
            delivery.leased_by = delivery.lease_until = delivery.lease_generation = delivery.lease_token = None
        return await self._delivery_cas(claim=claim, mutate=mutate)

    async def retry_delivery(self, *, claim: DeliveryClaim, error, now: datetime) -> DeliveryCasResult:
        def mutate(delivery: DeliverySchedule) -> None:
            delivery.status = DeliveryStatus.RETRY_WAIT
            delivery.available_at = now + timedelta(seconds=30)
            delivery.last_error = error
            delivery.leased_by = delivery.lease_until = delivery.lease_generation = delivery.lease_token = None
        return await self._delivery_cas(claim=claim, mutate=mutate)

    async def dead_letter_delivery(self, *, claim: DeliveryClaim, error, dead_lettered_at: datetime) -> DeliveryCasResult:
        def mutate(delivery: DeliverySchedule) -> None:
            delivery.status = DeliveryStatus.FAILED
            delivery.last_error = error
            delivery.leased_by = delivery.lease_until = delivery.lease_generation = delivery.lease_token = None
        return await self._delivery_cas(claim=claim, mutate=mutate)


class FakeQueue:
    def __init__(self) -> None:
        self.ids: list[str] = []

    async def enqueue(self, *, task_id: str, idempotency_key: str) -> None:
        assert idempotency_key
        self.ids.append(task_id)


class FakeEgressPolicy:
    def __init__(self, result: DeliveryEgressPolicyResult) -> None:
        self.result = result
        self.destinations: list[str] = []

    def check(self, *, destination: str) -> DeliveryEgressPolicyResult:
        self.destinations.append(destination)
        return self.result


ALLOW = DeliveryEgressPolicyResult(True, True, True, True, True, True, True)


class FakeTransport:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[str] = []

    async def deliver(self, *, destination: str, payload: dict, idempotency_key: str) -> None:
        assert idempotency_key
        if self.error:
            raise self.error
        self.sent.append(destination)


def service(repo: FakeRepository, clock: FakeClock, verifier: FakeVerifier, queue: FakeQueue, transport: FakeTransport, policy: FakeEgressPolicy | None = None) -> AutomationService:
    policy = policy or FakeEgressPolicy(ALLOW)
    return AutomationService(
        repository=repo,
        clock=clock,
        verifier=verifier,
        queue=queue,
        transport=transport,
        egress_policy=policy,
        max_attempts=2,
        lease_seconds=15,
    )


@pytest.mark.asyncio
async def test_ingress_verifies_records_and_enqueues_once() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(), queue, FakeTransport())
    ingress = WebhookIngress(source="github", event_id="evt-1", body=b"{}")

    first = await app.ingest_webhook(ingress=ingress, task_name="sync", secret_ref="github", payload={"x": 1})
    second = await app.ingest_webhook(ingress=ingress, task_name="sync", secret_ref="github", payload={"x": 1})

    assert first.accepted and first.task_id == "task-1"
    assert second.accepted and second.duplicate and second.task_id == "task-1"
    assert queue.ids == ["task-1", "task-1"]
    assert len(repo.recorded) == 1


@pytest.mark.asyncio
async def test_duplicate_ingress_reuses_run_when_payload_differs() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(), queue, FakeTransport())
    ingress = WebhookIngress(source="github", event_id="evt-1", body=b"{}")

    first = await app.ingest_webhook(ingress=ingress, task_name="sync", secret_ref="github", payload={"x": 1})
    second = await app.ingest_webhook(ingress=ingress, task_name="sync", secret_ref="github", payload={"x": 2})

    assert first.task_id == second.task_id == "task-1"
    assert second.duplicate is True
    assert queue.ids == ["task-1", "task-1"]
    assert len(repo.tasks) == 1


@pytest.mark.asyncio
async def test_invalid_signature_does_not_persist_or_enqueue() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(valid=False), queue, FakeTransport())
    result = await app.ingest_webhook(
        ingress=WebhookIngress(source="stripe", event_id="evt-2", body=b"bad"),
        task_name="charge",
        secret_ref="stripe",
        payload={},
    )
    assert result.accepted is False
    assert not repo.recorded
    assert not queue.ids


@pytest.mark.asyncio
async def test_empty_secret_is_rejected_before_verification() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(), queue, FakeTransport())
    with pytest.raises(ValueError, match="secret_ref"):
        await app.ingest_webhook(
            ingress=WebhookIngress(source="stripe", event_id="evt-3", body=b"{}"),
            task_name="charge",
            secret_ref=" ",
            payload={},
        )


@pytest.mark.asyncio
async def test_task_lease_is_claimed_by_repository() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(), queue, FakeTransport())
    run = TaskRun(id="task-1", task_name="sync", payload={})
    repo.tasks[run.id] = run
    leased = await app.lease_task_run(task_id=run.id, worker_id="worker-a")
    assert leased is not None and leased.run is run
    assert run.status == TaskRunStatus.LEASED


@pytest.mark.asyncio
async def test_expired_task_lease_is_reclaimed_by_repository() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(), queue, FakeTransport())
    run = TaskRun(
        id="task-1",
        task_name="sync",
        payload={},
        status=TaskRunStatus.RUNNING,
        attempts=1,
        leased_by="worker-old",
        lease_until=clock.value - timedelta(seconds=1),
    )
    repo.tasks[run.id] = run

    reclaimed = await app.lease_task_run(task_id=run.id, worker_id="worker-new")

    assert reclaimed is not None and reclaimed.run is run
    assert run.status == TaskRunStatus.LEASED
    assert run.leased_by == "worker-new"
    assert run.attempts == 2


@pytest.mark.asyncio
async def test_task_lease_and_retry_transition() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(), queue, FakeTransport())
    run = TaskRun(id="task-1", task_name="sync", payload={})
    repo.tasks[run.id] = run

    leased = await app.lease_task_run(task_id=run.id, worker_id="worker-a")
    assert leased is not None and leased.run is run
    assert run.status == TaskRunStatus.LEASED
    assert run.attempts == 1
    assert run.lease_until == clock.value + timedelta(seconds=15)

    assert leased is not None
    await app.start_task_run(claim=leased)
    assert run.status == TaskRunStatus.RUNNING

    retry = await app.fail_task_run(claim=leased, error=RuntimeError("temporary"))
    assert retry is True
    assert run.status == TaskRunStatus.RETRY_WAIT
    assert run.leased_by is None
    assert run.available_at == clock.value + timedelta(seconds=30)


@pytest.mark.asyncio
async def test_task_failure_after_max_attempts_is_terminal() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(), queue, FakeTransport())
    run = TaskRun(id="task-1", task_name="sync", payload={}, attempts=2, status=TaskRunStatus.LEASED)
    repo.tasks[run.id] = run

    run.lease_until = clock.value + timedelta(seconds=15)
    run.leased_by = "worker-a"
    run.lease_generation = 1
    run.lease_token = "0123456789abcdef"
    claim = TaskClaim(run=run, lease=LeaseToken(owner_id="worker-a", generation=1, token=run.lease_token, lease_until=run.lease_until))
    retry = await app.fail_task_run(claim=claim, error=RuntimeError("permanent"))
    assert retry is False
    assert run.status == TaskRunStatus.FAILED
    assert run.available_at is None


@pytest.mark.asyncio
async def test_stale_task_claim_cannot_replay_after_new_generation() -> None:
    repo, clock, queue = FakeRepository(), FakeClock(), FakeQueue()
    app = service(repo, clock, FakeVerifier(), queue, FakeTransport())
    run = TaskRun(id="task-stale", task_name="sync", payload={})
    repo.tasks[run.id] = run

    old_claim = await app.lease_task_run(task_id=run.id, worker_id="worker-old")
    assert old_claim is not None
    run.lease_until = clock.value - timedelta(seconds=1)
    new_claim = await app.lease_task_run(task_id=run.id, worker_id="worker-new")
    assert new_claim is not None

    assert await app.complete_task_run(claim=old_claim) is False
    assert run.status == TaskRunStatus.LEASED
    assert run.leased_by == "worker-new"


@pytest.mark.asyncio
async def test_expired_delivery_lease_is_reclaimed_by_repository() -> None:
    repo, clock, queue, transport = FakeRepository(), FakeClock(), FakeQueue(), FakeTransport()
    app = service(repo, clock, FakeVerifier(), queue, transport)
    delivery = DeliverySchedule(
        id="delivery-1",
        destination="https://example.test",
        payload={},
        available_at=clock.value,
        status=DeliveryStatus.LEASED,
        attempts=1,
        lease_until=clock.value - timedelta(seconds=1),
        leased_by="worker-old",
    )
    repo.deliveries[delivery.id] = delivery

    delivered = await app.deliver(delivery=delivery, worker_id="worker-new")

    assert delivered is True
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.leased_by is None
    assert delivery.attempts == 2


@pytest.mark.asyncio
async def test_delivery_succeeds_and_clears_lease() -> None:
    repo, clock, queue, transport = FakeRepository(), FakeClock(), FakeQueue(), FakeTransport()
    app = service(repo, clock, FakeVerifier(), queue, transport)
    delivery = DeliverySchedule(id="delivery-1", destination="https://example.test", payload={}, available_at=clock.value)

    delivered = await app.deliver(delivery=delivery, worker_id="worker-a")

    assert delivered is True
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.lease_until is None
    assert transport.sent == ["https://example.test"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "allowed",
        "validated_http_scheme",
        "credentials_absent",
        "allowed_port",
        "resolved_ip_global_nonreserved",
        "redirects_checked_per_hop",
        "tls_verified",
    ],
)
async def test_delivery_egress_requires_every_validation_proof(field: str) -> None:
    repo, clock, queue, transport = FakeRepository(), FakeClock(), FakeQueue(), FakeTransport()
    evidence = {
        "allowed": True,
        "validated_http_scheme": True,
        "credentials_absent": True,
        "allowed_port": True,
        "resolved_ip_global_nonreserved": True,
        "redirects_checked_per_hop": True,
        "tls_verified": True,
    }
    evidence[field] = False
    policy = FakeEgressPolicy(DeliveryEgressPolicyResult(**evidence))
    app = service(repo, clock, FakeVerifier(), queue, transport, policy)
    delivery = DeliverySchedule(id="delivery-denied", destination="https://example.test", payload={}, available_at=clock.value)
    repo.deliveries[delivery.id] = delivery

    assert await app.deliver(delivery=delivery, worker_id="worker-a") is False
    assert transport.sent == []
    assert policy.destinations == ["https://example.test"]


@pytest.mark.asyncio
async def test_delivery_defaults_to_deny_when_policy_port_is_missing() -> None:
    repo, clock, queue, transport = FakeRepository(), FakeClock(), FakeQueue(), FakeTransport()
    app = AutomationService(
        repository=repo,
        clock=clock,
        verifier=FakeVerifier(),
        queue=queue,
        transport=transport,
    )
    delivery = DeliverySchedule(id="delivery-no-policy", destination="https://example.test", payload={}, available_at=clock.value)
    repo.deliveries[delivery.id] = delivery

    assert await app.deliver(delivery=delivery, worker_id="worker-a") is False
    assert transport.sent == []
