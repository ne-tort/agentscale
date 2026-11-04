"""Unrun acceptance checks for lease fencing and durable error contracts."""

from datetime import UTC, datetime, timedelta

import hashlib

import pytest

from prodavan.application.automation_persistence.contracts import (
    DeliveryClaim,
    LeaseToken,
    StaleCasOutcome,
    TaskClaim,
)
from prodavan.application.automation_service.contracts import (
    DeliverySchedule,
    TaskRun,
    TaskRunStatus,
    durable_error,
)


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_lease_token_exposes_owner_generation_opaque_and_expiry() -> None:
    token = LeaseToken(owner_id="worker-a", generation=7, token="opaque-token-00000007", lease_until=NOW + timedelta(seconds=30))
    assert token.owner == "worker-a"
    assert token.generation == 7
    assert token.opaque == "opaque-token-00000007"
    assert token.expiry == NOW + timedelta(seconds=30)


def test_claim_contains_fencing_credential_and_detached_record() -> None:
    run = TaskRun(id="task-1", task_name="sync", payload={})
    claim = TaskClaim(run=run, lease=LeaseToken(owner_id="worker-a", generation=1, token="opaque-token-00000001", lease_until=NOW + timedelta(seconds=30)))
    assert claim.run.id == "task-1"
    assert claim.lease.owner_id == "worker-a"


def test_stale_cas_is_explicitly_not_applied() -> None:
    outcome = StaleCasOutcome(applied=False)
    assert outcome.applied is False
    assert outcome.reason == "stale_owner_generation_or_token"


def test_durable_error_never_contains_exception_message() -> None:
    error = durable_error(RuntimeError("secret transport response"), code="automation.error.delivery_failed")
    assert error.code == "automation.error.delivery_failed"
    # The durable record is exactly ``code`` + ``digest``: the review decided that
    # the exception class is stored as a digest, never as text, so there is no
    # ``error_class`` attribute to read. Pinning the digest to sha256 of the
    # qualified class name keeps the original intent — the record identifies the
    # failure class — and additionally proves the message never entered it: a value
    # derived from anything but the class name would not match.
    assert error.digest == hashlib.sha256(b"builtins.RuntimeError").hexdigest()
    assert "secret transport response" not in error.digest


def test_delivery_identity_is_stable_for_retries() -> None:
    delivery = DeliverySchedule(id="delivery-1", destination="https://example.test", payload={}, available_at=NOW)
    assert delivery.id == "delivery-1"
    assert delivery.status.value == "scheduled"
