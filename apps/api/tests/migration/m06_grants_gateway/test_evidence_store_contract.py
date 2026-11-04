"""Evidence-store boundary acceptance that needs no live transaction.

The SQL store itself is only reachable through a tenant-bound RLS transaction with a
writer-epoch fence, so a unit test cannot exercise its inserts. What can be pinned is
the part that decides *whether* a write is admissible at all, and what is allowed to
reach the append-only audit stream: context usability, cursor parsing, redaction of
caller payloads, the stable mapping correlation key, reason-code mapping and the
run-key report registry.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.migration.m06_grants_gateway.adapter import (
    AdapterOutcome,
    digest,
)
from prodavan.application.migration.m06_grants_gateway.evidence_store import (
    MAPPING_EVENT,
    SHADOW_EVENTS,
    EvidenceStoreBlocked,
    InMemoryParityReportStore,
    ObservationPage,
    _context_usable,
    _cursor,
    _outcome_from_status,
    _redacted_metadata,
    mapping_identity_key,
)
from prodavan.application.migration.m06_grants_gateway.parity_runner import (
    ObservationBatch,
    ReportOutcome,
)

from .acceptance_fixtures import CONTEXT, RLS


def test_observation_page_is_the_parity_runner_batch_contract():
    assert ObservationPage is ObservationBatch


def test_mapping_correlation_key_is_stable_and_hides_the_source_identity():
    key = mapping_identity_key("legacy_app_grant", "grant-a", "grantset-v1")
    assert key.startswith("m06map:")
    assert len(key) == len("m06map:") + 64
    assert key == mapping_identity_key("legacy_app_grant", "grant-a", "grantset-v1")
    assert "grant-a" not in key
    assert key != mapping_identity_key("legacy_app_grant", "grant-b", "grantset-v1")
    assert key != mapping_identity_key("legacy_app_grant", "grant-a", "grantset-v2")
    assert key != mapping_identity_key("grant-a", "legacy_app_grant", "grantset-v1")


def test_context_without_authentication_rls_or_generations_is_unusable():
    assert _context_usable(CONTEXT) is True
    assert _context_usable(replace(CONTEXT, authenticated=False)) is False
    assert _context_usable(replace(CONTEXT, rls_context=None)) is False
    assert _context_usable(
        replace(CONTEXT, rls_context=RLS.model_copy(update={"tenant_id": "tenant-b"}))
    ) is False
    assert _context_usable(
        replace(CONTEXT, rls_context=RLS.model_copy(update={"user_id": "subject-b"}))
    ) is False
    assert _context_usable(
        replace(CONTEXT, plugin_instance_id=None, runtime_generation=None,
                writer_generation=None, app_definition_id=None)
    ) is False


def test_store_blocked_error_carries_every_blocker():
    error = EvidenceStoreBlocked(("write_context_invalid", "writer_epoch_missing"))
    assert error.blockers == ("write_context_invalid", "writer_epoch_missing")
    assert str(error) == "write_context_invalid, writer_epoch_missing"


@pytest.mark.parametrize("value,parsed", [("0", 0), ("7", 7), ("123456", 123456)])
def test_cursor_accepts_only_non_negative_integers(value, parsed):
    assert _cursor(value) == parsed


@pytest.mark.parametrize("value", ["", "-1", "07x", "1.0", "sha256:" + "a" * 64])
def test_invalid_cursor_is_blocked_before_the_read(value):
    with pytest.raises(EvidenceStoreBlocked, match="parity_cursor_invalid"):
        _cursor(value)


def test_audit_metadata_is_redacted_before_it_reaches_the_stream():
    redacted = _redacted_metadata({
        "event": MAPPING_EVENT,
        "tenant_id": "tenant-a",
        "source_digest": digest("source"),
        "reason_code": "capability_unknown",
        "raw_payload": {"capability": "storage.files", "token": "secret"},
        "params": {"path": "/etc/passwd"},
        "authorization": "Bearer secret",
        "secret": "value",
    })
    assert set(redacted) == {"event", "tenant_id", "source_digest", "reason_code"}
    assert redacted["event"] == "mapping_recorded"
    assert "secret" not in str(redacted)
    assert "/etc/passwd" not in str(redacted)


def test_stored_status_maps_back_to_the_adapter_outcome():
    assert _outcome_from_status("mapped", "protocol_denied") is AdapterOutcome.KNOWN_DENY
    assert _outcome_from_status("mapped", "source_grant_expired") is AdapterOutcome.KNOWN_DENY
    assert _outcome_from_status("mapped", None) is AdapterOutcome.MAPPED_EVIDENCE
    assert _outcome_from_status("quarantined", "capability_unknown") is AdapterOutcome.QUARANTINED
    assert _outcome_from_status("unexpected", None) is AdapterOutcome.BLOCKED


def test_audit_event_names_are_the_parity_read_surface():
    assert MAPPING_EVENT == "mapping_recorded"
    assert SHADOW_EVENTS == ("shadow_compared", "shadow_mismatch", "shadow_replayed")


@pytest.mark.asyncio
async def test_report_registry_replays_identical_content_and_conflicts_on_change():
    reports = InMemoryParityReportStore()
    report = {"run_key": digest("run"), "status": "blocked", "blocked_reasons": ["x"]}
    # The registry is the durable port, so ``load`` is a coroutine like ``save``: a
    # missing key must be awaited to observe ``None``.
    assert await reports.load(run_key=report["run_key"]) is None
    assert await reports.save(run_key=report["run_key"], report=report) is ReportOutcome.RECORDED
    assert await reports.save(run_key=report["run_key"], report=dict(report)) is ReportOutcome.REPLAY
    changed = {**report, "blocked_reasons": ["y"]}
    assert await reports.save(run_key=report["run_key"], report=changed) is ReportOutcome.CONFLICT
    assert await reports.load(run_key=report["run_key"]) == report
    assert await reports.load(run_key=digest("other")) is None
