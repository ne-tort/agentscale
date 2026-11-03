"""Focused policy tests; authored for the migration verification gate."""
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from prodavan.application.migration.automation_outbox.durable import (
    DeliveryClaim,
    DeliveryState,
    OutboxBlockedError,
    OutboxClaim,
    OutboxState,
    OutboxStateRecord,
    OutboundDeliveryIntent,
    TransactionalOutboxEvent,
)
from prodavan.application.migration.automation_outbox.outbox import PartitionKey, WriterLease
from prodavan.application.migration.automation_outbox.retry_policy import (
    AutomationFailure,
    AutomationFailureCategory,
    AutomationRetryPolicy,
    classify_failure,
)

NOW = datetime(2026, 11, 1, 14, 40, tzinfo=UTC)
PARTITION = PartitionKey("tenant-fixture", "project-fixture", "automation-fixture")


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _delivery_claim(
    *, attempt: int = 1, identity: str = "source-event-1", lease_seconds: int = 60,
) -> DeliveryClaim:
    key = OutboundDeliveryIntent.stable_key(
        partition=PARTITION, subscription_id="subscription-1", source_event_id=identity,
    )
    intent = OutboundDeliveryIntent(
        f"delivery-{identity}", PARTITION, "task-run-1", "subscription-1", identity,
        "destination-ref-1", "secret-ref-1", {"value": "must-not-appear-in-diagnostics"}, key,
    )
    lease = WriterLease(PARTITION, "worker-a", 7, "fence-token-7", NOW + timedelta(minutes=5))
    return DeliveryClaim(
        intent, attempt, lease, attempt, f"claim-token-{attempt}", NOW,
        NOW + timedelta(seconds=lease_seconds),
    )


def _outbox_claim(*, attempt: int = 1, lease_seconds: int = 60) -> OutboxClaim:
    event_key = TransactionalOutboxEvent.aggregate_event_key(
        partition=PARTITION, aggregate_type="automation", aggregate_id="automation-1", aggregate_version=1,
    )
    event = TransactionalOutboxEvent(
        "event-1", PARTITION, "automation", "automation-1", 1,
        "automation.updated", "source-event-1", "run-1", NOW, {"safe": True}, event_key,
    )
    lease = WriterLease(PARTITION, "worker-a", 7, "fence-token-7", NOW + timedelta(minutes=5))
    state = OutboxStateRecord(
        event.event_id, PARTITION, OutboxState.CLAIMED, NOW, attempts=attempt,
        lease_generation=attempt, lease_owner=lease.holder, lease_token=f"claim-token-{attempt}",
        lease_until=NOW + timedelta(seconds=lease_seconds),
    )
    return OutboxClaim(event, state, lease, NOW)


def test_capped_exponential_delay_and_stable_jitter_are_replayable() -> None:
    clock = FixedClock(NOW + timedelta(seconds=1))
    policy = AutomationRetryPolicy(
        clock=clock, max_attempts=6, initial_delay_seconds=2,
        max_delay_seconds=5, jitter_basis_points=0,
    )
    expected = (2, 4, 5, 5)
    actual = []
    for attempt, delay in enumerate(expected, start=1):
        claim = _delivery_claim(attempt=attempt, lease_seconds=2)
        decision = policy.evaluate_delivery(
            claim=claim, failure=AutomationFailure(AutomationFailureCategory.TIMEOUT),
        )
        assert decision.next_attempt_at == clock.value + timedelta(seconds=delay)
        actual.append(decision.next_attempt_at)
    assert actual[-1] - clock.value == timedelta(seconds=5)

    jittered = AutomationRetryPolicy(
        clock=clock, initial_delay_seconds=10, max_delay_seconds=30, jitter_basis_points=2500,
    )
    claim = _delivery_claim(lease_seconds=2)
    first = jittered.evaluate_delivery(
        claim=claim, failure=AutomationFailure(AutomationFailureCategory.CONNECTION),
    )
    replay = jittered.evaluate_delivery(
        claim=claim, failure=AutomationFailure(AutomationFailureCategory.CONNECTION),
    )
    assert first == replay
    assert first.next_attempt_at is not None
    jitter_seconds = (first.next_attempt_at - clock.value).total_seconds()
    assert 7.5 <= jitter_seconds <= 12.5

    delays = {
        jittered.evaluate_delivery(
            claim=_delivery_claim(identity=f"source-{number}", lease_seconds=2),
            failure=AutomationFailure(AutomationFailureCategory.CONNECTION),
        ).next_attempt_at
        for number in range(12)
    }
    assert len(delays) > 1


def test_retry_after_delta_date_and_malformed_values_are_bounded_and_redacted() -> None:
    clock = FixedClock(NOW + timedelta(seconds=1))
    policy = AutomationRetryPolicy(
        clock=clock, initial_delay_seconds=2, max_delay_seconds=30, jitter_basis_points=0,
    )
    claim = _delivery_claim(lease_seconds=2)

    delta = policy.evaluate_delivery(
        claim=claim, failure=AutomationFailure(AutomationFailureCategory.RATE_LIMITED),
        retry_after="12",
    )
    assert delta.next_attempt_at == clock.value + timedelta(seconds=12)

    date_value = format_datetime(NOW + timedelta(seconds=90), usegmt=True)
    date_decision = policy.evaluate_delivery(
        claim=claim, failure=AutomationFailure(AutomationFailureCategory.RATE_LIMITED),
        retry_after=date_value,
    )
    assert date_decision.next_attempt_at == clock.value + timedelta(seconds=30)

    malformed = policy.evaluate_delivery(
        claim=claim, failure=AutomationFailure(AutomationFailureCategory.RATE_LIMITED),
        retry_after="secret-value-not-a-date",
    )
    assert malformed.next_attempt_at == clock.value + timedelta(seconds=2)
    assert "secret-value-not-a-date" not in repr(malformed)
    assert "must-not-appear-in-diagnostics" not in repr(malformed)
    assert "secret-ref-1" not in repr(malformed)
    assert malformed.classification.redacted_diagnostic == "Automation failure details were redacted."


@pytest.mark.parametrize(
    ("status", "expected_category", "retryable"),
    [
        (408, AutomationFailureCategory.TIMEOUT, True),
        (429, AutomationFailureCategory.RATE_LIMITED, True),
        (503, AutomationFailureCategory.SERVER_ERROR, True),
        (401, AutomationFailureCategory.AUTHENTICATION, False),
        (403, AutomationFailureCategory.AUTHORIZATION, False),
        (404, AutomationFailureCategory.NOT_FOUND, False),
        (422, AutomationFailureCategory.INVALID_REQUEST, False),
    ],
)
def test_failure_classification_uses_fixed_retryable_and_permanent_codes(
    status: int, expected_category: AutomationFailureCategory, retryable: bool,
) -> None:
    result = classify_failure(AutomationFailure(AutomationFailureCategory.UNKNOWN, status))
    assert result.category is expected_category
    assert result.retryable is retryable
    assert result.error_code == f"automation.error.{expected_category.value}"
    assert result.redacted_diagnostic == "Automation failure details were redacted."


def test_permanent_failure_and_exhausted_attempt_dead_letter_using_existing_delivery_types() -> None:
    clock = FixedClock(NOW + timedelta(seconds=1))
    policy = AutomationRetryPolicy(clock=clock, max_attempts=2, jitter_basis_points=0)
    retry_claim = _delivery_claim()
    retry = policy.evaluate_delivery(
        claim=retry_claim, failure=AutomationFailure(AutomationFailureCategory.TIMEOUT),
    )
    retry_outcome = retry.to_delivery_outcome(claim=retry_claim)
    assert retry_outcome.state is DeliveryState.RETRY_WAIT
    assert retry_outcome.retry_at is not None
    assert retry_outcome.retry_at >= retry_claim.lease_until

    permanent = policy.evaluate_delivery(
        claim=_delivery_claim(), failure=AutomationFailure(AutomationFailureCategory.INVALID_REQUEST),
    )
    assert permanent.dead_letter
    assert permanent.to_delivery_outcome(claim=_delivery_claim()).state is DeliveryState.DEAD_LETTER

    exhausted_claim = _delivery_claim(attempt=2)
    exhausted = policy.evaluate_delivery(
        claim=exhausted_claim, failure=AutomationFailure(AutomationFailureCategory.TIMEOUT),
    )
    outcome = exhausted.to_delivery_outcome(claim=exhausted_claim)
    assert exhausted.dead_letter
    assert outcome.state is DeliveryState.DEAD_LETTER
    assert outcome.error_code == "automation.error.timeout"


def test_outbox_claim_adapter_emits_retry_wait_state_without_changing_outbox_contract() -> None:
    clock = FixedClock(NOW + timedelta(seconds=5))
    policy = AutomationRetryPolicy(
        clock=clock, initial_delay_seconds=2, max_delay_seconds=30, jitter_basis_points=0,
    )
    claim = _outbox_claim(lease_seconds=15)
    decision = policy.evaluate_outbox(
        claim=claim, failure=AutomationFailure(AutomationFailureCategory.TRANSIENT),
    )
    assert decision.next_attempt_at == claim.state.lease_until
    next_state = decision.to_outbox_state(claim=claim)
    assert next_state.state is OutboxState.RETRY_WAIT
    assert next_state.available_at == claim.state.lease_until
    assert next_state.last_error_code == "automation.error.transient"
    assert next_state.revision == claim.state.revision + 1

    exhausted = _outbox_claim(attempt=8)
    exhausted_policy = AutomationRetryPolicy(clock=clock, max_attempts=8)
    dead = exhausted_policy.evaluate_outbox(
        claim=exhausted, failure=AutomationFailure(AutomationFailureCategory.SERVER_ERROR),
    ).to_outbox_state(claim=exhausted)
    assert dead.state is OutboxState.DEAD_LETTER
    assert dead.dead_lettered_at == clock.value


def test_lease_expiry_and_naive_clock_fail_closed() -> None:
    failure = AutomationFailure(AutomationFailureCategory.TIMEOUT)
    stale_policy = AutomationRetryPolicy(clock=FixedClock(NOW + timedelta(seconds=60)))
    with pytest.raises(OutboxBlockedError):
        stale_policy.evaluate_delivery(claim=_delivery_claim(lease_seconds=60), failure=failure)

    naive_policy = AutomationRetryPolicy(clock=FixedClock(datetime(2026, 11, 1, 14, 40)))
    with pytest.raises(ValueError, match="timezone-aware"):
        naive_policy.evaluate_delivery(claim=_delivery_claim(), failure=failure)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"initial_delay_seconds": 0},
        {"initial_delay_seconds": 10, "max_delay_seconds": 9},
        {"jitter_basis_points": 10_001},
    ],
)
def test_invalid_policy_bounds_are_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        AutomationRetryPolicy(clock=FixedClock(NOW), **kwargs)


def test_retry_after_datetime_requires_timezone() -> None:
    policy = AutomationRetryPolicy(clock=FixedClock(NOW))
    with pytest.raises(ValueError, match="timezone-aware"):
        policy.evaluate_delivery(
            claim=_delivery_claim(),
            failure=AutomationFailure(AutomationFailureCategory.RATE_LIMITED),
            retry_after=datetime(2026, 11, 1, 14, 40, 5),
        )