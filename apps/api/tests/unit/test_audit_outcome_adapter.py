"""Source acceptance tests for the decision-only audit outcome adapter.

Verification is intentionally frozen during migration; this file is not executed here.
"""

from datetime import UTC, datetime
import inspect

import pytest

from prodavan.application.audit_outcome_adapter import AuditOutcomeAdapter, AuditTransactionEvidenceV2
from prodavan.application.audit_outcomes import AuditDecision, AuditOutcome, AuditResult


DIGEST = "sha256:" + "a" * 64


def outcome(**changes):
    values = {
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "event_id": "event-1", "event_type": "mcp.tool.outcome",
        "decision": AuditDecision.DENY, "reason_code": "grant_missing",
        "tenant_id": "tenant-1", "app_definition_id": "definition-1", "app_id": "app-1",
        "app_version_id": "version-1", "request_id": "request-1", "trace_id": "trace-1",
        "span_id": "span-1", "profile_id": "profile-1", "tool_server": "server-1",
        "tool_name": "read", "tool_version": "v1", "arguments_digest": DIGEST,
        "operation": "read", "resource": "file-1", "domain": "project", "payload_digest": DIGEST,
        "result_status": AuditResult.DENY, "error_code": "grant_missing", "duration_ms": 3,
        "correlation_id": "corr-1", "manifest_version": "m1", "app_artifact_digest": DIGEST,
        "plugin_instance_id": "plugin-1", "runtime_generation": 1, "writer_generation": 1,
        "lease_id": "lease-1", "capability_grant_id": "grant-1", "capability_key": "files.read",
        "source_kind": "canonical", "source_id": "source-1", "mapping_version": "mapping-1",
        "capability_mapping_digest": DIGEST, "capability_snapshot_digest": DIGEST,
    }
    values.update(changes)
    return AuditOutcome(**values)


class Evidence:
    def __init__(self):
        self.calls = []

    async def append_evidence(self, **kwargs):
        self.calls.append(kwargs)


class Transaction:
    def __init__(self):
        self.calls = []

    async def append_transaction_evidence(self, evidence):
        self.calls.append(evidence)


@pytest.mark.asyncio
async def test_maps_redacted_evidence_and_preserves_decision_and_result():
    sink = Evidence()
    original = outcome()
    evidence = await AuditOutcomeAdapter(evidence=sink).append(original)
    assert evidence.outcome is original
    assert evidence.decision is AuditDecision.DENY
    assert evidence.result_status is AuditResult.DENY
    assert evidence.redacted.result.value == "deny"
    assert sink.calls == [{"audit": evidence.redacted}]
    assert evidence.redacted.correlation_id == original.correlation_id
    assert evidence.outcome.payload_digest == DIGEST
    assert evidence.outcome.capability_snapshot_digest == DIGEST


@pytest.mark.asyncio
async def test_transaction_seam_appends_validated_full_evidence_without_remapping():
    sink = Transaction()
    original = outcome(result_status=AuditResult.ERROR, error_code="tool_failed")
    evidence = await AuditOutcomeAdapter(transaction=sink).append_transaction_evidence(original)
    assert sink.calls == [evidence]
    assert sink.calls[0].outcome is original
    assert sink.calls[0].redacted.result.value == "error"
    assert sink.calls[0].redacted.error_code == "tool_failed"


def test_legacy_outcome_evidence_contract_remains_available():
    assert AuditOutcomeAdapter.adapt(outcome()).__class__.__name__ == "AuditOutcomeEvidence"


def test_requires_complete_identity_and_digests():
    with pytest.raises(ValueError, match="capability_snapshot_digest"):
        AuditOutcomeAdapter.adapt(outcome(capability_snapshot_digest=None))
    with pytest.raises(ValueError, match="lease_id"):
        AuditOutcomeAdapter.adapt(outcome(lease_id=None))
    with pytest.raises(ValueError, match="payload_digest"):
        AuditOutcomeAdapter.adapt(outcome(payload_digest=None))
    with pytest.raises(ValueError, match="arguments_digest"):
        AuditOutcomeAdapter.adapt(outcome(arguments_digest=None))


def test_rejects_quarantine_before_trusted_append():
    quarantined = outcome(
        decision=AuditDecision.QUARANTINE,
        result_status=AuditResult.ERROR,
        error_code="policy_review",
    )
    with pytest.raises(ValueError, match="quarantine"):
        AuditOutcomeAdapter.adapt(quarantined)


def test_adapter_has_no_database_or_positive_write_implementation():
    source = inspect.getsource(AuditOutcomeAdapter).lower()
    assert "sqlalchemy" not in source
    assert "session.commit" not in source
    assert "create_grant" not in source
    assert "update" not in source
    assert "delete" not in source
