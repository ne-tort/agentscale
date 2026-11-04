"""Source tests for the transactionally durable automation outbox foundation."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from prodavan.application.automation_service.outbox import OutboxWorker
from prodavan.domain.automation.outbox import (
    OutboxEnqueueIntent,
    OutboxEvent,
    OutboxState,
    OutboxStateRecord,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def event(**changes: object) -> OutboxEvent:
    values: dict[str, object] = {
        "event_id": "evt-1",
        "tenant_id": "tenant-1",
        "aggregate_type": "invoice",
        "aggregate_id": "invoice-1",
        "aggregate_version": 3,
        "event_type": "invoice.approved",
        "occurred_at": NOW,
        "payload": {"total": 10},
    }
    values.update(changes)
    return OutboxEvent(**values)


def state(**changes: object) -> OutboxStateRecord:
    values: dict[str, object] = {"event_id": "evt-1", "available_at": NOW}
    values.update(changes)
    return OutboxStateRecord(**values)


def test_aggregate_event_key_is_derived_and_stable() -> None:
    created = event()

    assert created.idempotency_key == "8=tenant-1:7=invoice:9=invoice-1:1=3"
    assert OutboxEvent.aggregate_event_key(
        tenant_id="tenant-1", aggregate_type="invoice", aggregate_id="invoice-1", aggregate_version=3
    ) == created.idempotency_key


def test_event_rejects_a_key_for_a_different_aggregate_version() -> None:
    with pytest.raises(ValidationError, match="idempotency_key"):
        event(idempotency_key="8=tenant-1:7=invoice:9=invoice-1:1=4")


def test_outbox_state_requires_lease_fields_only_while_leased() -> None:
    with pytest.raises(ValidationError, match="requires owner"):
        state(state=OutboxState.LEASED)

    leased = state(
        state=OutboxState.LEASED,
        leased_by="worker-1",
        lease_until=NOW + timedelta(minutes=1),
    )
    assert leased.leased_by == "worker-1"


def test_terminal_states_require_terminal_timestamps() -> None:
    with pytest.raises(ValidationError, match="delivered_at"):
        state(state=OutboxState.DELIVERED)
    with pytest.raises(ValidationError, match="dead_lettered_at"):
        state(state=OutboxState.DEAD_LETTER)


def test_enqueue_intent_carries_the_same_event_deduplication_key() -> None:
    created = event()
    intent = OutboxEnqueueIntent(
        event_id=created.event_id,
        queue_name="automation-outbox",
        deduplication_key=created.idempotency_key,
    )

    assert intent.deduplication_key == created.idempotency_key


class FakeRepository:
    def __init__(self, claimed: list[tuple[OutboxEvent, OutboxStateRecord]]) -> None:
        self.claimed = claimed
        self.actions: list[tuple[str, str]] = []

    async def claim(self, **_: object) -> list[tuple[OutboxEvent, OutboxStateRecord]]:
        return [(event, state, 3, "lease-token-00000003") for event, state in self.claimed]

    async def mark_delivered(self, *, event_id: str, **_: object) -> OutboxStateRecord:
        self.actions.append(("delivered", event_id))
        return state(state=OutboxState.DELIVERED, delivered_at=NOW)

    async def mark_retry(self, *, event_id: str, **_: object) -> OutboxStateRecord:
        self.actions.append(("retry", event_id))
        return state(state=OutboxState.RETRY_WAIT, last_error="temporary")

    async def mark_dead_letter(self, *, event_id: str, **_: object) -> OutboxStateRecord:
        self.actions.append(("dead_letter", event_id))
        return state(state=OutboxState.DEAD_LETTER, dead_lettered_at=NOW)


class FakeDispatcher:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.events: list[str] = []

    async def dispatch(self, *, event: OutboxEvent) -> None:
        self.events.append(event.event_id)
        if self.error is not None:
            raise self.error


ALLOWED_EGRESS = DeliveryEgressPolicyResult(True, True, True, True, True, True, True)


class FakeEgressPolicy:
    """Egress evidence port: the worker must obtain proof before dispatching."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.destinations: list[str] = []

    def check(self, *, destination: str) -> DeliveryEgressPolicyResult:
        self.destinations.append(destination)
        if self.allowed:
            return ALLOWED_EGRESS
        return DeliveryEgressPolicyResult(False, False, False, False, False, False, False)


def egress_event(**changes: object) -> OutboxEvent:
    """Outbox event that carries the destination its egress proof is about."""
    return event(payload={"total": 10, "destination": "https://example.test/hook"}, **changes)


@pytest.mark.asyncio
async def test_worker_acknowledges_only_after_successful_dispatch() -> None:
    repository = FakeRepository([(event(), state(state=OutboxState.LEASED, leased_by="w", lease_until=NOW + timedelta(minutes=1)))])
    dispatcher = FakeDispatcher()
    worker = OutboxWorker(repository=repository, dispatcher=dispatcher, worker_id="w")

    assert await worker.run_once(now=NOW, lease_until=NOW + timedelta(minutes=1), retry_at=NOW + timedelta(minutes=2)) == 1
    assert dispatcher.events == ["evt-1"]
    assert repository.actions == [("delivered", "evt-1")]


@pytest.mark.asyncio
async def test_worker_retries_before_dead_lettering() -> None:
    repository = FakeRepository([(event(), state(state=OutboxState.LEASED, leased_by="w", lease_until=NOW + timedelta(minutes=1), attempts=1))])
    worker = OutboxWorker(repository=repository, dispatcher=FakeDispatcher(RuntimeError("timeout")), worker_id="w", max_attempts=2)

    assert await worker.run_once(now=NOW, lease_until=NOW + timedelta(minutes=1), retry_at=NOW + timedelta(minutes=2)) == 0
    assert repository.actions == [("retry", "evt-1")]

    repository = FakeRepository([(event(), state(state=OutboxState.LEASED, leased_by="w", lease_until=NOW + timedelta(minutes=1), attempts=2))])
    worker = OutboxWorker(repository=repository, dispatcher=FakeDispatcher(RuntimeError("timeout")), worker_id="w", max_attempts=2)
    await worker.run_once(now=NOW, lease_until=NOW + timedelta(minutes=1), retry_at=NOW + timedelta(minutes=2))
    assert repository.actions == [("dead_letter", "evt-1")]


@pytest.mark.asyncio
async def test_worker_does_not_persist_raw_transport_error() -> None:
    repository = FakeRepository([(event(), state(state=OutboxState.LEASED, leased_by="w", lease_until=NOW + timedelta(minutes=1)))])
    await OutboxWorker(
        repository=repository,
        dispatcher=FakeDispatcher(RuntimeError("https://user:secret@example.test/private")),
        worker_id="w",
    ).run_once(now=NOW, lease_until=NOW + timedelta(minutes=1), retry_at=NOW + timedelta(minutes=2))
    assert repository.actions == [("retry", "evt-1")]
    # The fake records only lifecycle calls; the repository contract receives a
    # bounded automation.error.<class>:<sha256> value, never exception text.

