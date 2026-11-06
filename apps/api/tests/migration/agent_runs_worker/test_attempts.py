"""Provider attempt journal acceptance: idempotency, redaction, unknown outcomes.

Authored during the migration verification freeze — not executed here.
"""

from __future__ import annotations

import pytest

from prodavan.application.agent_runs.attempts import (
    ATTEMPT_JOURNAL_BLOCKER,
    ATTEMPT_TABLE_BLOCKER,
    AttemptConflict,
    AttemptOutcome,
    AttemptRefusal,
    AttemptRejected,
    AttemptState,
    ProviderAttempt,
    ProviderAttemptJournal,
    attempt_id_for,
    attempt_idempotency_key,
    build_attempt,
    next_state,
    outcome_for_result,
    prompt_digest,
    redact_metadata,
    request_digest,
)
from prodavan.application.agent_runs.execution import ProviderExecutionResult, ProviderOutcome

from .fakes import NOW, RUN_ID, FakeAttemptPort, failed, make_lease, success, unknown


def journal() -> tuple[ProviderAttemptJournal, FakeAttemptPort]:
    port = FakeAttemptPort()
    return ProviderAttemptJournal(port=port), port


def _digest(prompt: str = "summarise the pipeline") -> str:
    return request_digest(
        provider="agent-runtime",
        model=None,
        session_id="session-worker",
        attachments=0,
        prompt=prompt,
    )


def attempt_for(*, lease=None, prompt: str = "summarise the pipeline", metadata=None) -> ProviderAttempt:
    lease = lease or make_lease()
    return build_attempt(
        lease=lease,
        provider="agent-runtime",
        request_digest_value=_digest(prompt),
        started_at=NOW,
        deadline_at=lease.lease_until,
        metadata=metadata if metadata is not None else {"provider": "agent-runtime", "phase": "dispatch"},
    )


# --- idempotency ----------------------------------------------------------


def test_attempt_key_is_stable_for_run_generation_attempt() -> None:
    first = attempt_idempotency_key(tenant_id="tenant-worker", run_id=RUN_ID, generation=1, attempt=1)
    same = attempt_idempotency_key(tenant_id="tenant-worker", run_id=RUN_ID, generation=1, attempt=1)
    newer = attempt_idempotency_key(tenant_id="tenant-worker", run_id=RUN_ID, generation=2, attempt=2)
    assert first == same
    assert first != newer
    assert attempt_id_for(first) == attempt_id_for(same)
    with pytest.raises(ValueError):
        attempt_idempotency_key(tenant_id="tenant-worker", run_id=RUN_ID, generation=0, attempt=1)


def test_generation_increments_the_attempt_key_so_a_reclaim_is_a_new_attempt() -> None:
    generation_one = attempt_for(lease=make_lease(generation=1, attempt=1))
    generation_two = attempt_for(lease=make_lease(generation=2, attempt=2, token="token-generation-2"))
    assert generation_one.idempotency_key != generation_two.idempotency_key
    assert generation_one.attempt_id != generation_two.attempt_id


async def test_begin_is_idempotent_and_never_overwrites() -> None:
    log, port = journal()
    first = await log.begin(attempt_for())
    second = await log.begin(attempt_for())
    assert first.created is True
    assert second.created is False
    assert len(port.records) == 1
    assert port.start_calls == 2


async def test_same_key_with_a_different_digest_is_a_conflict() -> None:
    log, port = journal()
    await log.begin(attempt_for())
    conflicting = attempt_for(prompt="a different prompt")
    assert conflicting.idempotency_key == attempt_for().idempotency_key
    assert conflicting.request_digest != attempt_for().request_digest
    with pytest.raises(AttemptConflict) as rejected:
        await log.begin(conflicting)
    assert rejected.value.reason is AttemptRefusal.DIGEST_CONFLICT
    assert len(port.records) == 1


async def test_begin_rejects_an_already_finished_attempt() -> None:
    log, port = journal()
    started = await log.begin(attempt_for())
    await log.finish(started.attempt, outcome=AttemptOutcome.SUCCEEDED, at=NOW)
    finished = port.records[started.attempt.attempt_id]
    with pytest.raises(AttemptRejected):
        await log.begin(finished)


# --- redaction ------------------------------------------------------------


def test_journal_metadata_is_allow_listed() -> None:
    redacted, dropped = redact_metadata(
        {
            "provider": "agent-runtime",
            "http_status": 200,
            "latency_ms": 1450,
            "prompt": "the user's private prompt",
            "text": "assistant output",
            "authorization": "Bearer sk-live-123",
            "api_key": "sk-live-123",
            "unknown_future_key": "value",
            "nested": {"secret": "x"},
        }
    )
    assert set(redacted) == {"provider", "http_status", "latency_ms"}
    assert dropped >= 6


def test_attempt_records_carry_no_prompt_token_or_payload() -> None:
    attempt = attempt_for(
        lease=make_lease(token="super-secret-lease-token"),
        metadata={
            "prompt": "user text",
            "text": "model text",
            "lease_token": "super-secret-lease-token",
            "provider": "agent-runtime",
        },
    )
    assert attempt.metadata == {"provider": "agent-runtime"}
    assert attempt.metadata_dropped >= 3
    rendered = repr(attempt)
    assert "user text" not in rendered
    assert "model text" not in rendered
    assert "super-secret-lease-token" not in rendered


def test_prompt_is_reduced_to_a_digest() -> None:
    digest = prompt_digest("a private prompt")
    assert digest is not None and len(digest) == 64
    assert "private" not in digest
    assert prompt_digest(None) is None


# --- state machine --------------------------------------------------------


def test_unknown_can_only_leave_through_reconciliation() -> None:
    for outcome in (AttemptOutcome.SUCCEEDED, AttemptOutcome.FAILED, AttemptOutcome.RETRYABLE):
        with pytest.raises(AttemptRejected) as rejected:
            next_state(AttemptState.UNKNOWN, outcome)
        assert rejected.value.reason is AttemptRefusal.UNKNOWN_REQUIRES_RECONCILIATION

    assert (
        next_state(AttemptState.UNKNOWN, AttemptOutcome.RECONCILED_SUCCEEDED)
        is AttemptState.RECONCILED_SUCCEEDED
    )
    assert (
        next_state(AttemptState.UNKNOWN, AttemptOutcome.RECONCILED_FAILED)
        is AttemptState.RECONCILED_FAILED
    )


def test_terminal_attempts_are_immutable() -> None:
    with pytest.raises(AttemptRejected) as rejected:
        next_state(AttemptState.KNOWN_SUCCEEDED, AttemptOutcome.UNKNOWN)
    assert rejected.value.reason is AttemptRefusal.ALREADY_TERMINAL


def test_result_mapping_keeps_unknown_unknown() -> None:
    assert outcome_for_result(success()) is AttemptOutcome.SUCCEEDED
    assert outcome_for_result(failed()) is AttemptOutcome.FAILED
    assert outcome_for_result(unknown()) is AttemptOutcome.UNKNOWN


async def test_an_unknown_attempt_cannot_be_marked_successful() -> None:
    log, port = journal()
    started = await log.begin(attempt_for())
    await log.finish(started.attempt, outcome=AttemptOutcome.UNKNOWN, at=NOW)
    stored = port.records[started.attempt.attempt_id]
    assert stored.state is AttemptState.UNKNOWN

    with pytest.raises(AttemptRejected) as rejected:
        await log.finish_with_result(stored, result=success(), at=NOW)
    assert rejected.value.reason is AttemptRefusal.UNKNOWN_REQUIRES_RECONCILIATION
    assert port.records[started.attempt.attempt_id].state is AttemptState.UNKNOWN


async def test_reconciliation_is_the_only_success_path_from_unknown() -> None:
    log, port = journal()
    started = await log.begin(attempt_for())
    await log.finish(started.attempt, outcome=AttemptOutcome.UNKNOWN, at=NOW)
    applied = await log.reconcile(
        port.records[started.attempt.attempt_id],
        result=ProviderExecutionResult(
            outcome=ProviderOutcome.SUCCEEDED,
            output_payload={"assistant_text": "recovered"},
            provider_request_id="req-1",
        ),
        at=NOW,
    )
    assert applied.applied
    assert port.records[started.attempt.attempt_id].state is AttemptState.RECONCILED_SUCCEEDED


async def test_reconciliation_cannot_conclude_unknown() -> None:
    log, port = journal()
    started = await log.begin(attempt_for())
    await log.finish(started.attempt, outcome=AttemptOutcome.UNKNOWN, at=NOW)
    with pytest.raises(AttemptRejected):
        await log.reconcile(port.records[started.attempt.attempt_id], result=unknown(), at=NOW)


async def test_reconciling_a_started_attempt_is_not_reconciliation() -> None:
    log, port = journal()
    started = await log.begin(attempt_for())
    with pytest.raises(AttemptRejected) as rejected:
        await log.reconcile(port.records[started.attempt.attempt_id], result=success(), at=NOW)
    assert rejected.value.reason is AttemptRefusal.ILLEGAL_TRANSITION


async def test_failed_attempts_require_a_bounded_failure_code() -> None:
    log, port = journal()
    started = await log.begin(attempt_for())
    with pytest.raises(AttemptRejected):
        await log.finish(
            started.attempt,
            outcome=AttemptOutcome.FAILED,
            at=NOW,
            failure_code="this is not a machine code",
        )
    assert port.records[started.attempt.attempt_id].state is AttemptState.STARTED


def test_attempt_record_rejects_inconsistent_state_pairs() -> None:
    template = attempt_for()
    with pytest.raises(AttemptRejected):
        ProviderAttempt(
            attempt_id=template.attempt_id,
            idempotency_key=template.idempotency_key,
            tenant_id=template.tenant_id,
            project_id=template.project_id,
            run_id=template.run_id,
            generation=1,
            attempt=1,
            sequence=2,
            provider="agent-runtime",
            request_digest=template.request_digest,
            lease_token_digest=template.lease_token_digest,
            state=AttemptState.KNOWN_SUCCEEDED,
            started_at=NOW,
            finished_at=None,  # terminal without an observation time
        )
    with pytest.raises(AttemptRejected):
        ProviderAttempt(
            attempt_id=template.attempt_id,
            idempotency_key=template.idempotency_key,
            tenant_id=template.tenant_id,
            project_id=template.project_id,
            run_id=template.run_id,
            generation=1,
            attempt=1,
            sequence=2,
            provider="agent-runtime",
            request_digest=template.request_digest,
            lease_token_digest=template.lease_token_digest,
            state=AttemptState.STARTED,
            started_at=NOW,
            finished_at=NOW,  # open attempt with an observation time
        )


def test_blockers_are_declared() -> None:
    assert ATTEMPT_JOURNAL_BLOCKER == "agent_run_provider_attempt_journal_unwired"
    assert ATTEMPT_TABLE_BLOCKER == "agent_run_provider_attempt_table_missing"
