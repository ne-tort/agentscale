"""Acceptance contracts for R-08/R-09 automation fencing and safe errors."""

from datetime import UTC, datetime, timedelta

from prodavan.application.automation_persistence.contracts import LeaseToken, StaleCasOutcome
from prodavan.application.automation_service.contracts import durable_error
from prodavan.application.automation_service.outbox import _safe_error


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_lease_token_is_opaque_and_generation_fenced() -> None:
    lease = LeaseToken(owner_id="worker-a", generation=4, token="opaque-token-123456", lease_until=NOW + timedelta(minutes=1))
    assert lease.owner_id == "worker-a"
    assert lease.generation == 4
    assert lease.token == "opaque-token-123456"
    assert StaleCasOutcome(applied=False).applied is False


def test_durable_error_excludes_exception_message_and_is_stable() -> None:
    first = durable_error(RuntimeError("https://internal.example/?token=secret"), code="delivery_failed")
    second = durable_error(RuntimeError("different secret"), code="delivery_failed")
    assert first.code == "automation.error.delivery_failed"
    assert first.error_class.endswith("RuntimeError")
    assert first.digest == second.digest
    assert "secret" not in first.digest


def test_outbox_safe_error_does_not_store_transport_text() -> None:
    value = _safe_error(RuntimeError("password=secret host=internal"))
    assert "password" not in value
    assert "secret" not in value
    assert value.startswith("automation.error.delivery_failed:")
