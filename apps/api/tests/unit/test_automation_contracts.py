"""Contract tests for the automation/webhook control-plane foundation."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from prodavan.domain.automation.types import (
    DeliveryState,
    LeaseState,
    TaskRunState,
)
from tests.unit.fixtures.automation import (
    NOW,
    active_delivery_lease,
    automation_definition,
    idempotency_record,
    outbound_delivery,
    task_run,
    webhook_endpoint,
)


def test_definition_and_endpoint_keep_secret_out_of_contract() -> None:
    definition = automation_definition()
    endpoint = webhook_endpoint()

    assert definition.trigger_type == "webhook.received"
    assert endpoint.secret_ref == "secrets/automation/endpoint-1"
    assert "secret" not in definition.model_dump()


def test_webhook_methods_are_normalized() -> None:
    endpoint = webhook_endpoint(accepted_methods=("post", "PUT"))

    assert endpoint.accepted_methods == ("POST", "PUT")


def test_task_run_lease_state_requires_lease_fields() -> None:
    with pytest.raises(ValidationError, match="active lease"):
        task_run(state=TaskRunState.LEASED)

    leased = task_run(
        state=TaskRunState.LEASED,
        leased_by="worker-1",
        lease_until=NOW + timedelta(minutes=1),
    )
    assert leased.state is TaskRunState.LEASED

    with pytest.raises(ValidationError, match="only leased task runs"):
        task_run(lease_until=NOW + timedelta(minutes=1), leased_by="worker-1")


def test_task_run_waiting_retry_requires_schedule() -> None:
    with pytest.raises(ValidationError, match="next_retry_at"):
        task_run(state=TaskRunState.WAITING_RETRY)


def test_failed_task_run_requires_error_message_and_finished_at() -> None:
    with pytest.raises(ValidationError, match="finished_at"):
        task_run(state=TaskRunState.FAILED, error_message="timeout")

    with pytest.raises(ValidationError, match="error_message"):
        task_run(state=TaskRunState.FAILED, finished_at=NOW + timedelta(minutes=1))


def test_idempotency_record_can_replay_completed_response() -> None:
    record = idempotency_record(state="completed", response_payload={"run_id": "run-1"})

    assert record.response_payload == {"run_id": "run-1"}


def test_delivery_rejects_lease_fields_outside_leased_state() -> None:
    with pytest.raises(ValidationError, match="only leased deliveries"):
        outbound_delivery(leased_by="worker-1", lease_until=NOW + timedelta(minutes=1))


def test_retry_wait_delivery_requires_next_attempt() -> None:
    with pytest.raises(ValidationError, match="next_attempt_at"):
        outbound_delivery(state=DeliveryState.RETRY_WAIT)


def test_exhausted_delivery_uses_all_attempts() -> None:
    with pytest.raises(ValidationError, match="all attempts"):
        outbound_delivery(state=DeliveryState.EXHAUSTED, attempt=1, max_attempts=2)


def test_delivery_attempt_requires_ordered_timestamps() -> None:
    from prodavan.domain.automation.types import DeliveryAttempt

    with pytest.raises(ValidationError, match="finished_at"):
        DeliveryAttempt(attempt=1, started_at=NOW, finished_at=NOW - timedelta(seconds=1))


def test_delivery_lease_has_ordered_expiry_and_release() -> None:
    lease = active_delivery_lease()
    assert lease.state is LeaseState.ACTIVE

    with pytest.raises(ValidationError, match="after acquired_at"):
        active_delivery_lease(expires_at=NOW)

    with pytest.raises(ValidationError, match="released_at"):
        active_delivery_lease(state=LeaseState.RELEASED)
