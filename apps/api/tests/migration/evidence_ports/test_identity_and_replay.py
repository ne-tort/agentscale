"""Append-only identity and replay semantics of the two insert ports.

The ports make the decision in two steps: read the immutable primary key and the logical
evidence key under the bound tenant, then decide insert / replay / conflict from those two
rows (:func:`plan_marker_append`, :func:`plan_promotion_append`). Both steps are pure
functions here, so the identity rules are pinned without a live transaction.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.migration.evidence_ports import (
    AppendPlan,
    backfill_observation_key,
    backfill_source_key,
    marker_ledger_key,
    marker_payload,
    page_ledger_key,
    plan_marker_append,
    plan_promotion_append,
    promotion_payload,
)

from .acceptance_fixtures import (
    CANONICAL_DIGEST,
    DEFINITION,
    TENANT,
    backfill_context,
    checkpoint,
    marker,
    marker_row_view,
    promotion_record,
    promotion_row_view,
)


def _marker_row(sample):
    from prodavan.application.migration.evidence_ports import handoff_marker_row_values

    return marker_row_view(handoff_marker_row_values(sample))


def _promotion_row(sample):
    from prodavan.application.migration.evidence_ports import promotion_row_values

    return promotion_row_view(promotion_row_values(sample))


def test_marker_ledger_key_is_stable_and_covers_the_evidence_identity():
    sample = marker()
    key = marker_ledger_key(sample)
    assert key == marker_ledger_key(marker())
    assert key.startswith("sha256:") and len(key) == len("sha256:") + 64
    assert key != marker_ledger_key(marker(canonical_digest="sha256:" + "7" * 64))
    assert key != marker_ledger_key(
        marker(checkpoint=replace(sample.checkpoint, sequence=11))
    )
    # The key never exposes either partition component in clear text.
    assert TENANT not in key and DEFINITION not in key


def test_exact_marker_repeat_is_a_replay():
    sample = marker()
    first = plan_marker_append(sample, existing_by_id=None, existing_by_key=None)
    assert first.action == "insert"
    assert first.is_insert

    repeat = plan_marker_append(
        sample, existing_by_id=_marker_row(sample), existing_by_key=None
    )
    assert repeat.action == "replay"
    assert repeat.stored == sample

    # The same marker found through the logical key is still an exact replay.
    by_key = plan_marker_append(sample, existing_by_id=None, existing_by_key=_marker_row(sample))
    assert by_key.action == "replay"
    assert by_key.stored == sample


def test_marker_with_another_digest_is_a_conflict():
    stored = marker()
    other = marker(canonical_digest="sha256:" + "8" * 64, accepted_count=4)
    plan = plan_marker_append(other, existing_by_id=_marker_row(stored), existing_by_key=None)
    assert plan.action == "conflict"
    assert plan.blockers == ("handoff_marker_payload_conflict",)
    assert plan.stored == stored

    # A different marker that only collides on the immutable evidence key conflicts too.
    key_collision = marker(record_id="sha256:" + "2" * 64)
    other_by_key = plan_marker_append(
        key_collision, existing_by_id=None, existing_by_key=_marker_row(stored)
    )
    assert other_by_key.action == "conflict"
    assert other_by_key.blockers == ("handoff_marker_identity_conflict",)


def test_promotion_repeat_is_a_replay_and_a_changed_record_conflicts():
    sample = promotion_record()
    assert plan_promotion_append(
        sample, existing_by_id=None, existing_by_key=None
    ).action == "insert"
    replay = plan_promotion_append(
        sample, existing_by_id=_promotion_row(sample), existing_by_key=None
    )
    assert replay.action == "replay" and replay.stored == sample

    changed = promotion_record(imported_count=9)
    conflict = plan_promotion_append(
        changed, existing_by_id=_promotion_row(sample), existing_by_key=None
    )
    assert conflict.action == "conflict"
    assert conflict.blockers == ("m05_promotion_record_payload_conflict",)


def test_plan_type_rejects_an_unknown_action():
    with pytest.raises(ValueError, match="insert, replay or conflict"):
        AppendPlan("overwrite")


def test_source_and_observation_keys_are_distinct_per_observed_digest():
    base = backfill_source_key("legacy_app_version", "version-1", "v1")
    observed = backfill_observation_key(
        "legacy_app_version", "version-1", "v1",
        observed_source_digest=CANONICAL_DIGEST, observed_artifact_digest=None,
    )
    artifact = backfill_observation_key(
        "legacy_app_version", "version-1", "v1",
        observed_source_digest=None, observed_artifact_digest=CANONICAL_DIGEST,
    )
    assert len({base, observed, artifact}) == 3
    assert observed == backfill_observation_key(
        "legacy_app_version", "version-1", "v1",
        observed_source_digest=CANONICAL_DIGEST, observed_artifact_digest=None,
    )


def test_page_ledger_key_pins_the_cursor_and_the_candidate_set():
    from prodavan.application.migration.m04e_definition_version.backfill import (
        CandidateDecision,
        EvidenceStatus,
    )

    candidate = _candidate()
    decision = CandidateDecision(candidate, EvidenceStatus.ACCEPTED, None, None)
    first = page_ledger_key(checkpoint(sequence=2, after="v2"), (decision,))
    assert first == page_ledger_key(checkpoint(sequence=2, after="v2"), (decision,))
    assert first != page_ledger_key(checkpoint(sequence=3, after="v3"), (decision,))
    rejected = CandidateDecision(candidate, EvidenceStatus.QUARANTINED, "archived_source_row", None)
    assert first != page_ledger_key(checkpoint(sequence=2, after="v2"), (rejected,))
    assert backfill_context().usable is True


def _candidate():
    from prodavan.application.migration.m04e_definition_version.backfill import EvidenceCandidate

    return EvidenceCandidate(
        source_kind="legacy_app_version",
        source_id="version-1",
        schema_version="v1",
        payload={"status": "published", "manifest_version": "1.0.0"},
        phase="version",
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        canonical_schema_version="m04e.v1",
    )


def test_marker_payload_is_reversible_through_the_decoder():
    from prodavan.application.migration.evidence_ports import decode_marker_row

    sample = marker()
    envelope = marker_payload(sample)
    assert decode_marker_row(None) is None
    assert decode_marker_row(marker_row_view({
        "id": sample.record_id,
        "tenant_id": TENANT,
        "definition_id": DEFINITION,
        "revision_id": sample.record_id,
        "outcome": "blocked",
        "source_snapshot_id": sample.checkpoint.source_snapshot_id,
        "source_digest": sample.source_snapshot_digest,
        "target_digest": sample.canonical_digest,
        "gate_evidence": envelope,
    })) == sample


def test_promotion_payload_is_reversible_through_the_decoder():
    from prodavan.application.migration.evidence_ports import (
        decode_promotion_row,
        promotion_row_values,
    )

    sample = promotion_record()
    values = promotion_row_values(sample)
    assert decode_promotion_row(None) is None
    assert decode_promotion_row(promotion_row_view(values)) == sample
    assert promotion_payload(sample)["status"] == "ready"
