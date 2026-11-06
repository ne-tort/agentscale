"""Acceptance: the journal stores digests and allow-listed scalars, nothing else.

Three separate risks are covered:

1. **A secret never reaches a column** — the metadata path is
   ``redact_metadata`` (allow-list) plus ``_assert_secret_free`` (forbidden-key
   scan) at the *write site*, so a direct caller cannot bypass the application
   tier's redaction.
2. **The drop counter is honest** — ``metadata_dropped`` counts what the journal
   refused to store: the start records its own count, a finish adds its delta to
   the stored column, and re-redacting an already-clean mapping counts nothing
   (no double counting).
3. **The record stays readable** — everything persisted here must still satisfy
   ``ProviderAttempt.__post_init__`` when it is read back.
"""

from __future__ import annotations

import pytest

from prodavan.application.agent_runs.attempts import (
    FORBIDDEN_METADATA_KEYS,
    REDACTED_METADATA_KEYS,
    AttemptOutcome,
    AttemptRefusal,
    AttemptRejected,
    AttemptState,
    lease_token_digest,
)
from prodavan.infrastructure.persistence.agent_control_plane.attempt_journal import (
    finish_statement,
    finish_values,
    start_values,
)

from .acceptance_fixtures import (
    FAILURE_CODE,
    NOW,
    PROMPT,
    FakeResult,
    Script,
    Step,
    make_attempt,
    make_lease,
    record,
    rendered_sql,
    store_for,
)

SECRET = "sk-live-0123456789"
LEASE_TOKEN = "super-secret-lease-token"

RAW_METADATA = {
    "provider": "agent-runtime",
    "http_status": 200,
    "latency_ms": 1450.5,
    "phase": "dispatch",
    # below: every one of these must be dropped and counted
    "prompt": PROMPT,
    "text": "the model answer",
    "authorization": f"Bearer {SECRET}",
    "api_key": SECRET,
    "lease_token": LEASE_TOKEN,
    "unknown_future_key": "value",
    "nested": {"secret": SECRET},
    "null_value": None,
}


# --------------------------------------------------------------------------- #
# Start
# --------------------------------------------------------------------------- #


def test_start_writes_only_allow_listed_metadata() -> None:
    attempt = make_attempt(metadata=RAW_METADATA)

    values = start_values(attempt)

    assert set(values["metadata_json"]) == {"provider", "http_status", "latency_ms", "phase"}
    assert values["metadata_dropped"] >= 6


def test_start_does_not_double_count_the_drops_it_inherits() -> None:
    """``ProviderAttempt`` already counted; re-redacting a clean mapping is free."""
    attempt = make_attempt(metadata=RAW_METADATA)
    counted_by_the_record = attempt.metadata_dropped
    assert counted_by_the_record >= 6

    values = start_values(attempt)

    assert values["metadata_dropped"] == counted_by_the_record


def test_start_never_persists_the_lease_token_only_its_digest() -> None:
    lease = make_lease(lease_token=LEASE_TOKEN)
    attempt = make_attempt(lease=lease)

    values = start_values(attempt)

    assert values["lease_token_digest"] == lease_token_digest(LEASE_TOKEN)
    assert LEASE_TOKEN not in repr(values)


def test_start_values_carry_no_prompt_and_no_payload() -> None:
    attempt = make_attempt(metadata=RAW_METADATA)

    rendered = repr(start_values(attempt))

    assert PROMPT not in rendered
    assert "the model answer" not in rendered
    assert SECRET not in rendered


# --------------------------------------------------------------------------- #
# Finish
# --------------------------------------------------------------------------- #


def test_finish_writes_only_allow_listed_metadata() -> None:
    values = finish_values(
        expected_state=AttemptState.STARTED,
        next_state=AttemptState.KNOWN_SUCCEEDED,
        finished_at=NOW,
        metadata={"provider": "agent-runtime", "api_key": SECRET, "prompt": PROMPT},
    )

    assert values["metadata_json"] == {"provider": "agent-runtime"}
    assert SECRET not in repr(values)
    assert PROMPT not in repr(values)


def test_finish_reports_its_dropped_keys_as_a_delta() -> None:
    values = finish_values(
        expected_state=AttemptState.STARTED,
        next_state=AttemptState.KNOWN_FAILED,
        finished_at=NOW,
        failure_code=FAILURE_CODE,
        metadata={"provider": "agent-runtime", "api_key": SECRET, "text": "answer"},
        metadata_dropped=2,
    )

    # two keys dropped here plus the two the caller already accounted for
    assert values["metadata_dropped"] == 4


def test_the_finish_statement_adds_the_delta_to_the_stored_count() -> None:
    statement = finish_statement(
        attempt_id="pat_any",
        expected_state=AttemptState.STARTED,
        next_state=AttemptState.KNOWN_SUCCEEDED,
        finished_at=NOW,
        metadata={"provider": "agent-runtime"},
        metadata_dropped=3,
    )

    sql = rendered_sql(statement, literals=False)

    assert "metadata_dropped=(agent_run_provider_attempts.metadata_dropped +" in sql
    # the column is never reset by a transition
    assert "metadata_dropped=%(metadata_dropped)s" not in sql


async def test_a_finish_through_the_store_writes_no_secret() -> None:
    attempt = record(metadata=RAW_METADATA)
    script = Script(Step("execute", FakeResult(rowcount=1)))

    applied = await store_for(script).finish_attempt(
        attempt, outcome=AttemptOutcome.SUCCEEDED, at=NOW, metadata=RAW_METADATA
    )

    assert applied.applied
    # the only JSONB value bound by the statement is the redacted mapping; the
    # raw secret exists nowhere in the statement the adapter issued
    statement_repr = repr(script.calls[0][1].compile().params)
    assert SECRET not in statement_repr
    assert LEASE_TOKEN not in statement_repr
    assert PROMPT not in statement_repr


# --------------------------------------------------------------------------- #
# Defence in depth
# --------------------------------------------------------------------------- #


def test_every_persisted_metadata_key_is_allowed_and_never_forbidden() -> None:
    attempt = make_attempt(metadata=RAW_METADATA)

    start = start_values(attempt)["metadata_json"]
    finished = finish_values(
        expected_state=AttemptState.STARTED,
        next_state=AttemptState.UNKNOWN,
        finished_at=NOW,
        metadata=RAW_METADATA,
    )["metadata_json"]

    for written in (start, finished):
        assert set(written) <= REDACTED_METADATA_KEYS
        assert not (set(written) & FORBIDDEN_METADATA_KEYS)


def test_non_scalar_and_null_values_are_dropped_not_serialised() -> None:
    attempt = make_attempt(
        metadata={"provider": "agent-runtime", "nested": {"deep": 1}, "latency_ms": None}
    )

    values = start_values(attempt)

    assert values["metadata_json"] == {"provider": "agent-runtime"}
    assert values["metadata_dropped"] == attempt.metadata_dropped == 2


def test_metadata_must_be_a_mapping() -> None:
    with pytest.raises(AttemptRejected) as rejected:
        finish_values(
            expected_state=AttemptState.STARTED,
            next_state=AttemptState.KNOWN_SUCCEEDED,
            finished_at=NOW,
            metadata=["not", "a", "mapping"],  # type: ignore[arg-type]
        )

    assert rejected.value.reason is AttemptRefusal.INVALID_RECORD


async def test_a_forbidden_key_alone_never_reaches_the_row() -> None:
    """Even a lone ``lease_token``/``api_key`` is dropped, not persisted."""
    attempt = record()
    script = Script(Step("execute", FakeResult(rowcount=1)))

    applied = await store_for(script).finish_attempt(
        attempt,
        outcome=AttemptOutcome.SUCCEEDED,
        at=NOW,
        metadata={"lease_token": LEASE_TOKEN, "api_key": SECRET},
    )

    assert applied.applied
    params = script.calls[0][1].compile().params
    assert LEASE_TOKEN not in repr(params)
    assert SECRET not in repr(params)
    # both keys were refused, so the transition reports them as dropped
    assert any(value == 2 for value in params.values() if isinstance(value, int))
