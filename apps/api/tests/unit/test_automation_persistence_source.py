"""Source acceptance tests for the T05 automation persistence boundary."""

from datetime import UTC, datetime, timedelta
from typing import get_type_hints

import pytest

from prodavan.application.automation_persistence import (
    AutomationPersistenceBoundary,
    BlockedAutomationPersistenceAdapter,
    CanonicalAutomationEnvelope,
    DeliveryClaim,
    IdempotencyClaim,
    LeaseToken,
    OperationNowPort,
    ProjectTriggersPort,
    StaleCasOutcome,
    TaskClaim,
)
from prodavan.application.automation_service.contracts import DeliverySchedule, TaskRun, WebhookIngress
from prodavan.application.automation_persistence.contracts import Conflict, DeliveryCasResult, OutboxCasResult, TaskCasResult

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakePersistence:
    def __init__(self) -> None:
        self.record_calls: list[tuple[object, object]] = []
        self.retry_calls: list[dict[str, object]] = []

    async def record_and_enqueue(self, *, record: object, idempotency: object) -> tuple[bool, TaskRun]:
        self.record_calls.append((record, idempotency))
        return True, TaskRun(id="task-1", task_name="sync", payload={})

    async def claim_task(self, **kwargs: object) -> TaskClaim:
        run = TaskRun(id=str(kwargs["task_id"]), task_name="sync", payload={})
        return TaskClaim(run=run, lease=LeaseToken(owner_id=str(kwargs["owner_id"]), generation=4, token="lease-token-00000004", lease_until=kwargs["lease_until"]))

    async def claim_delivery(self, **kwargs: object) -> DeliveryClaim:
        delivery = DeliverySchedule(id=str(kwargs["delivery_id"]), destination="https://example.test", payload={}, available_at=NOW)
        return DeliveryClaim(delivery=delivery, lease=LeaseToken(owner_id=str(kwargs["owner_id"]), generation=2, token="lease-token-00000002", lease_until=kwargs["lease_until"]))

    async def retry_task(self, **kwargs: object) -> TaskRun:
        self.retry_calls.append(kwargs)
        return TaskRun(id=str(kwargs["task_id"]), task_name="sync", payload={})


@pytest.mark.asyncio
async def test_record_and_enqueue_requires_atomic_immutable_outbox_contract() -> None:
    repository = FakePersistence()
    adapter = AutomationPersistenceBoundary(persistence=repository, operation_now=FakeClock())
    ingress = WebhookIngress(source="github", event_id="evt-1", body=b"{}")

    accepted, run = await adapter.record_and_enqueue(
        ingress=ingress, tenant_id="tenant-1", subscription_id="sub-1", task_name="sync", payload={"x": 1}
    )

    assert accepted is True
    assert run is not None
    record, claim = repository.record_calls[0]
    assert record.event.event_id == record.initial_state.event_id == record.enqueue_intent.event_id
    assert record.enqueue_intent.deduplication_key == record.event.idempotency_key
    assert record.event.tenant_id == "tenant-1"
    assert isinstance(claim, IdempotencyClaim)
    assert claim.key == record.idempotency_key
    assert claim.request_hash == record.request_hash
    assert claim.tenant_id == "tenant-1"
    assert claim.subscription_id == "sub-1"
    assert claim.source_event_id == "evt-1"
    assert record.envelope.source == "github"
    assert record.envelope.task_name == "sync"
    assert record.envelope.body == b"{}"
    assert record.event.event_id == record.idempotency_key


def test_canonical_identity_is_tenant_scoped_and_hash_is_stable() -> None:
    ingress = WebhookIngress(source="github", event_id="evt-1", body=b"{}")
    left = CanonicalAutomationEnvelope.from_ingress(ingress=ingress, tenant_id="tenant-a", subscription_id="sub-1", task_name="sync", payload={"b": 2, "a": 1})
    right = CanonicalAutomationEnvelope.from_ingress(ingress=ingress, tenant_id="tenant-b", subscription_id="sub-1", task_name="sync", payload={"a": 1, "b": 2})
    assert left.request_hash != right.request_hash
    assert left.idempotency_key != right.idempotency_key
    assert left.idempotency_key == CanonicalAutomationEnvelope.from_ingress(
        ingress=WebhookIngress(source=" github ", event_id="evt-1", body=b"{}"),
        tenant_id=" tenant-a ", subscription_id="sub-1", task_name="sync", payload={"a": 1, "b": 2}
    ).idempotency_key


@pytest.mark.asyncio
async def test_retry_uses_operation_clock_not_caller_available_at() -> None:
    repository = FakePersistence()
    adapter = AutomationPersistenceBoundary(persistence=repository, operation_now=FakeClock())
    claim = TaskClaim(run=TaskRun(id="task-1", task_name="sync", payload={}), lease=LeaseToken(owner_id="worker-a", generation=3, token="lease-token-00000003", lease_until=NOW + timedelta(seconds=10)))

    await adapter.retry_task(claim=claim, error="temporary")

    assert repository.retry_calls[0]["now"] == NOW
    assert "available_at" not in repository.retry_calls[0]


def test_lease_has_owner_and_monotonic_generation_and_stale_outcome() -> None:
    lease = LeaseToken(owner_id="worker-a", generation=3, token="lease-token-00000003", lease_until=NOW + timedelta(seconds=1))
    assert lease.owner_id == "worker-a"
    assert lease.generation == 3
    assert StaleCasOutcome(applied=False).reason == "stale_owner_generation_or_token"


def test_outbox_port_exposes_lifecycle_cas_operations() -> None:
    from prodavan.application.automation_persistence import AutomationPersistencePort
    names = set(AutomationPersistencePort.__annotations__)
    assert not names
    for method in ("complete_outbox", "retry_outbox", "dead_letter_outbox", "release_outbox"):
        assert hasattr(AutomationPersistencePort, method)


def test_project_triggers_has_no_target_automation_operations() -> None:
    assert ProjectTriggersPort.__annotations__ == {}


def test_cas_forwarders_keep_success_conflict_and_stale_result_types() -> None:
    expected = {
        "complete_task": TaskCasResult,
        "retry_task": TaskCasResult,
        "dead_letter_task": TaskCasResult,
        "complete_delivery": DeliveryCasResult,
        "retry_delivery": DeliveryCasResult,
        "dead_letter_delivery": DeliveryCasResult,
        "complete_outbox": OutboxCasResult,
        "retry_outbox": OutboxCasResult,
        "dead_letter_outbox": OutboxCasResult,
        "release_outbox": OutboxCasResult,
    }
    for name, result_type in expected.items():
        assert get_type_hints(getattr(AutomationPersistenceBoundary, name))["return"] == result_type
    assert Conflict(reason="same_identity_different_hash").reason
    assert StaleCasOutcome(applied=False).applied is False


@pytest.mark.asyncio
async def test_default_persistence_adapter_is_explicitly_blocked() -> None:
    with pytest.raises(RuntimeError, match="concrete DB adapter required"):
        await BlockedAutomationPersistenceAdapter().claim_task(task_id="task-1", owner_id="worker", now=NOW, lease_until=NOW + timedelta(seconds=1))


def test_operation_clock_is_required_to_be_timezone_aware() -> None:
    class NaiveClock:
        def now(self) -> datetime:
            return datetime(2026, 1, 1)

    adapter = AutomationPersistenceBoundary(persistence=FakePersistence(), operation_now=NaiveClock())
    with pytest.raises(ValueError, match="operation_now must be timezone-aware"):
        import asyncio

        asyncio.run(adapter.retry_task(claim=TaskClaim(run=TaskRun(id="task-1", task_name="sync", payload={}), lease=LeaseToken(owner_id="worker-a", generation=1, token="lease-token-00000001", lease_until=NOW + timedelta(seconds=10))), error="temporary"))
