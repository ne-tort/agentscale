"""M04E backfill page-commit acceptance: checkpoint CAS and cursor advancement.

The M04E schema has no dedicated checkpoint table (review finding G4 assumed one), so the
page cursor and the source ledger live inside the append-only promotion row and
``revision_id`` — the digest of the *expected* checkpoint — is the compare-and-set token.
These cases pin that arithmetic, which is the part the M04E backfill contract validates
row by row after the store returns.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.migration.evidence_ports import (
    MigrationEvidenceConflict,
    SqlM04EBackfillEvidenceStore,
    backfill_checkpoint_digest,
    next_checkpoint,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillOutcome,
    CandidateDecision,
    EvidenceCandidate,
    EvidenceStatus,
)

from .acceptance_fixtures import (
    DEFINITION,
    TENANT,
    VERSION,
    backfill_record,
    checkpoint,
)

REQUIRE_CURSOR_CAS = SqlM04EBackfillEvidenceStore._require_cursor_cas


def _decision(status: EvidenceStatus, source_id: str = VERSION, reason: str | None = None):
    candidate = EvidenceCandidate(
        source_kind="legacy_app_version",
        source_id=source_id,
        schema_version="v1",
        payload={"status": "published", "manifest_version": "1.0.0"},
        phase="version",
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        canonical_schema_version="m04e.v1",
    )
    return CandidateDecision(candidate, status, reason, None)


def _record(
    source_id: str,
    outcome: BackfillOutcome,
    status: EvidenceStatus = EvidenceStatus.ACCEPTED,
    reason: str | None = None,
):
    return backfill_record(
        source_id=source_id, outcome=outcome, status=status, reason_code=reason
    )


def test_cursor_cas_refuses_every_divergent_cursor():
    expected = checkpoint(sequence=5, after="v5")
    with pytest.raises(MigrationEvidenceConflict, match="durable_checkpoint_cursor_conflict"):
        REQUIRE_CURSOR_CAS(checkpoint(sequence=5, after="v4"), expected)
    with pytest.raises(MigrationEvidenceConflict, match="durable_checkpoint_sequence_ahead"):
        REQUIRE_CURSOR_CAS(checkpoint(sequence=6, after="v6"), expected)


def test_cursor_cas_refuses_a_foreign_lineage_or_a_later_phase():
    expected = checkpoint(sequence=1, after="v1")
    for divergent in (
        replace(expected, partition=("tenant-other", DEFINITION)),
        replace(expected, source_snapshot_digest="sha256:" + "0" * 64),
        replace(expected, canonical_schema_version="m04e.v0"),
        replace(expected, writer_generation=9),
    ):
        with pytest.raises(MigrationEvidenceConflict, match="durable_checkpoint_lineage_conflict"):
            REQUIRE_CURSOR_CAS(divergent, expected)

    definition_expected = checkpoint(
        sequence=1, after="d1", phase="definition", phase_complete=False
    )
    with pytest.raises(MigrationEvidenceConflict, match="durable_checkpoint_phase_ahead"):
        REQUIRE_CURSOR_CAS(checkpoint(sequence=1, after="v1"), definition_expected)


def test_cursor_cas_accepts_an_exact_or_older_durable_cursor():
    expected = checkpoint(sequence=4, after="v4")
    REQUIRE_CURSOR_CAS(expected, expected)
    REQUIRE_CURSOR_CAS(checkpoint(sequence=2, after="v2"), expected)
    REQUIRE_CURSOR_CAS(checkpoint(sequence=0, after=None), expected)
    # A durable cursor still in the definition phase may be re-advanced into version.
    REQUIRE_CURSOR_CAS(
        checkpoint(sequence=1, after="d1", phase="definition", phase_complete=False), expected
    )


def test_checkpoint_digest_is_stable_and_phase_sensitive():
    first = backfill_checkpoint_digest(checkpoint(sequence=3, after="v3"))
    assert first == backfill_checkpoint_digest(checkpoint(sequence=3, after="v3"))
    assert first != backfill_checkpoint_digest(checkpoint(sequence=4, after="v4"))
    assert first != backfill_checkpoint_digest(
        checkpoint(sequence=3, after="v3", phase_complete=True)
    )
    assert first.startswith("sha256:") and len(first) == len("sha256:") + 64


def test_next_checkpoint_advances_by_consumed_records_only():
    expected = checkpoint(sequence=0, after=None, phase="definition")
    decisions = (
        _decision(EvidenceStatus.ACCEPTED, "d1"),
        _decision(EvidenceStatus.ACCEPTED, "d2"),
    )
    records = (
        _record("d1", BackfillOutcome.ACCEPTED),
        _record("d2", BackfillOutcome.ACCEPTED),
    )
    successor = next_checkpoint(expected, decisions, records, page_exhausted=False)
    assert successor.sequence == 2
    assert successor.after_source_id == "d2"
    assert successor.phase == "definition"
    assert successor.phase_complete is False


def test_next_checkpoint_switches_phase_only_when_the_definition_stream_drains():
    expected = checkpoint(sequence=0, after=None, phase="definition")
    decisions = (_decision(EvidenceStatus.ACCEPTED, "d1"),)
    records = (_record("d1", BackfillOutcome.ACCEPTED),)
    successor = next_checkpoint(expected, decisions, records, page_exhausted=True)
    assert successor.phase == "version"
    assert successor.after_source_id is None
    assert successor.sequence == 2  # one consumed record plus the phase transition
    assert successor.phase_complete is False


def test_next_checkpoint_completes_only_a_drained_version_stream():
    expected = checkpoint(sequence=1, after="v1")
    decisions = (_decision(EvidenceStatus.ACCEPTED, "v2"),)
    records = (_record("v2", BackfillOutcome.ACCEPTED),)
    successor = next_checkpoint(expected, decisions, records, page_exhausted=True)
    assert successor.phase == "version"
    assert successor.after_source_id == "v2"
    assert successor.sequence == 2
    assert successor.phase_complete is True


def test_next_checkpoint_does_not_advance_an_all_replay_page():
    expected = checkpoint(sequence=7, after="v7")
    decisions = (_decision(EvidenceStatus.REPLAY, "v7"),)
    records = (_record("v7", BackfillOutcome.REPLAY),)
    assert next_checkpoint(expected, decisions, records, page_exhausted=True) == expected


def test_next_checkpoint_advances_on_a_first_time_conflict():
    """A changed digest is a consumed classification: the cursor moves past it."""
    expected = checkpoint(sequence=1, after="v1")
    decisions = (_decision(EvidenceStatus.CONFLICT, "v2", "source_digest_changed"),)
    records = (
        _record(
            "v2", BackfillOutcome.CONFLICT,
            status=EvidenceStatus.CONFLICT, reason="source_digest_changed",
        ),
    )
    successor = next_checkpoint(expected, decisions, records, page_exhausted=False)
    assert successor.sequence == 2
    assert successor.after_source_id == "v2"


def test_empty_page_on_a_version_stream_completes_without_moving_the_cursor():
    expected = checkpoint(sequence=3, after="v3")
    successor = next_checkpoint(expected, (), (), page_exhausted=True)
    assert successor.after_source_id == "v3"
    assert successor.sequence == 3
    assert successor.phase_complete is True


def test_store_cannot_be_half_built_without_a_trusted_scope():
    with pytest.raises(ValueError, match="resolved trusted evidence scope"):
        SqlM04EBackfillEvidenceStore(session_factory=object(), scope=object())
