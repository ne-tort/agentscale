"""Redaction and column-mapping acceptance for the two append ports.

Two different failures are possible here and both are checked: a mapper could invent a
column the registered model does not have, or a payload could carry material that must
never be persisted. The module refuses both instead of coping silently.
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.evidence_ports import (
    REDACTION,
    MigrationEvidenceBlocked,
    _assert_redacted,
    handoff_marker_row_values,
    marker_payload,
    promotion_payload,
    promotion_row_values,
    record_payload,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.plugin_instance_registry import (
    M05PluginInstancePromotionEvidenceRow,
)

from .acceptance_fixtures import backfill_record, marker, promotion_record

M04E_COLUMNS = set(M04EDefinitionVersionPromotionEvidenceRow.__table__.c.keys())
M05_COLUMNS = set(M05PluginInstancePromotionEvidenceRow.__table__.c.keys())

#: The only values the two promotion tables admit; a ``ready`` outcome would be refused
#: by the database check constraint, so readiness lives in the redacted envelope.
ALLOWED_OUTCOMES = {"blocked", "quarantined"}


def test_row_values_match_registered_models():
    marker_values = handoff_marker_row_values(marker())
    assert set(marker_values) <= M04E_COLUMNS, set(marker_values) - M04E_COLUMNS

    promotion_values = promotion_row_values(promotion_record())
    assert set(promotion_values) <= M05_COLUMNS, set(promotion_values) - M05_COLUMNS


def test_outcome_column_stays_inside_its_check_constraint():
    marker_values = handoff_marker_row_values(marker())
    promotion_values = promotion_row_values(promotion_record())
    assert marker_values["outcome"] in ALLOWED_OUTCOMES
    assert promotion_values["outcome"] in ALLOWED_OUTCOMES
    # Readiness is not lost: it survives in the envelope and decodes back exactly.
    assert marker_values["gate_evidence"]["status"] == "ready"
    assert promotion_values["gate_evidence"]["record"]["status"] == "ready"
    assert marker_values["canonical_positive_writes"] is False
    assert promotion_values["canonical_positive_writes"] is False


def test_digest_columns_receive_sha256_and_counts_are_integers():
    values = handoff_marker_row_values(marker())
    for column in (
        "source_digest",
        "target_digest",
        "current_version_resolution_digest",
        "artifact_equality_digest",
    ):
        assert str(values[column]).startswith("sha256:")
        assert len(values[column]) == len("sha256:") + 64
    assert all(
        isinstance(values[column], int)
        for column in ("valid_count", "archived_count", "quarantined_count")
    )


def test_row_mapping_keeps_identifier_columns_within_their_widths():
    marker_values = handoff_marker_row_values(marker())
    assert len(marker_values["id"]) <= 80
    assert len(marker_values["definition_id"]) <= 80
    assert len(marker_values["reviewer"]) <= 200
    promotion_values = promotion_row_values(promotion_record())
    assert len(promotion_values["id"]) <= 40
    assert len(promotion_values["partition_id"]) <= 128
    assert len(promotion_values["correlation_id"]) <= 128
    assert len(promotion_values["actor_id"]) <= 128


def test_payload_refuses_carrying_keys():
    for offending in (
        {"payload": {"any": "thing"}},
        {"raw_payload": "x"},
        {"manifest": {"appId": "demo"}},
        {"artifact_token": "secret"},
        {"access_secret": "secret"},
        {"request_body": "{}"},
    ):
        with pytest.raises(MigrationEvidenceBlocked, match="redaction_violation"):
            _assert_redacted(offending, where="unit")


def test_marker_payload_has_no_carrier_and_names_its_redaction():
    payload = marker_payload(marker())
    assert payload["redaction"] == REDACTION
    assert payload["canonical_positive_writes"] is False
    lowered = {str(key).lower() for key in payload}
    for banned in ("payload", "manifest", "token", "secret", "password", "body"):
        assert not [key for key in lowered if banned in key]


def test_promotion_payload_records_only_counts_digests_and_reason_codes():
    payload = promotion_payload(promotion_record())
    assert payload["redaction"] == REDACTION
    assert payload["legacy_read_write_owner"] == "legacy"
    assert payload["canonical_positive_writes"] is False
    for gate in payload["gates"]:
        assert set(gate) == {"name", "passed", "evidence_digest", "reason_code"}
    for ban in ("raw", "request", "capability_body"):
        assert ban not in payload


def test_backfill_record_payload_is_redacted_and_reversible():
    from prodavan.application.migration.evidence_ports import record_from_payload

    sample = backfill_record()
    payload = record_payload(sample)
    assert "payload" not in payload
    assert record_from_payload(payload) == sample


def test_marker_rejects_a_positive_write_or_a_foreign_task_id():
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        handoff_marker_row_values(marker(canonical_positive_writes=True, task_id="MIG-M05-02"))
    assert "handoff_marker_cannot_enable_positive_writes" in raised.value.blockers
    assert "handoff_marker_task_id_unexpected" in raised.value.blockers


def test_marker_requires_the_two_gates_the_table_stores_as_digest_columns():
    from prodavan.application.migration.m04e_definition_version.backfill import (
        HandoffGate,
        HandoffStatus as M04EHandoffStatus,
    )

    without_gates = tuple(
        HandoffGate(gate.name, gate.passed, None)
        if gate.name in {"same_definition_version", "immutable_artifact"}
        else gate
        for gate in marker().gate_evidence
    )
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        handoff_marker_row_values(marker(gate_evidence=without_gates))
    assert "handoff_marker_gate_evidence_missing:same_definition_version" in raised.value.blockers
    assert "handoff_marker_gate_evidence_missing:immutable_artifact" in raised.value.blockers

    # A blocked marker may legitimately lack those gates: its digest columns fall back to
    # its own redacted envelope digest, and the exact gate list stays in the envelope.
    blocked = handoff_marker_row_values(marker(
        status=M04EHandoffStatus.BLOCKED, gate_evidence=without_gates
    ))
    assert blocked["current_version_resolution_digest"].startswith("sha256:")
    assert blocked["artifact_equality_digest"] == blocked["current_version_resolution_digest"]
    assert [gate["name"] for gate in blocked["gate_evidence"]["gates"]] == [
        gate.name for gate in without_gates
    ]
    assert blocked["gate_evidence"]["status"] == "blocked"


def test_promotion_record_rejects_positive_writes_and_a_partition_drift():
    from dataclasses import replace

    with pytest.raises(MigrationEvidenceBlocked) as raised:
        promotion_row_values(promotion_record(canonical_positive_writes=True))
    assert "m05_promotion_cannot_enable_positive_writes" in raised.value.blockers

    drifted = replace(promotion_record(), partition=("tenant-other", "definition-x"))
    with pytest.raises(MigrationEvidenceBlocked) as mismatch:
        promotion_row_values(drifted)
    assert "m05_promotion_partition_differs_from_checkpoint" in mismatch.value.blockers


def test_blocked_error_requires_at_least_one_named_blocker():
    with pytest.raises(ValueError, match="at least one blocker"):
        MigrationEvidenceBlocked(())
