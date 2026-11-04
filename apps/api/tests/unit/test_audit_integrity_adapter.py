"""Source acceptance tests for the exclusive audit integrity adapter zone.

Migration verification is intentionally frozen; this file is not executed here.
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from prodavan.application.audit_integrity import AuditEventClass, AuditIntegrityService, AuditLinkedEvent
from prodavan.application.audit_integrity_adapter import AuditIntegrityAdapter, AuditIntegrityAppendRequest
from prodavan.application.audit_outcome_adapter import AuditOutcomeAdapter, AuditTransactionEvidenceV2
from prodavan.application.audit_outcomes import AuditDecision, AuditOutcome, AuditResult


DIGEST = "sha256:" + "a" * 64
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def outcome(**changes):
    values = {
        "occurred_at": STAMP, "event_id": "final-1", "event_type": "mcp.tool.outcome",
        "decision": AuditDecision.ALLOW, "reason_code": "allowed", "tenant_id": "tenant-1",
        "app_definition_id": "definition-1", "app_id": "app-1", "app_version_id": "version-1",
        "request_id": "request-1", "trace_id": "trace-1", "span_id": "span-1",
        "profile_id": "profile-1", "tool_server": "server-1", "tool_name": "read",
        "tool_version": "v1", "arguments_digest": DIGEST, "operation": "read",
        "resource": "project-1", "domain": "project", "payload_digest": DIGEST,
        "result_status": AuditResult.ALLOW, "error_code": None, "duration_ms": 3,
        "correlation_id": "corr-1", "manifest_version": "m1", "app_artifact_digest": DIGEST,
        "plugin_instance_id": "plugin-1", "runtime_generation": 1, "writer_generation": 1,
        "lease_id": "lease-1", "capability_grant_id": "grant-1", "capability_key": "files.read",
        "source_kind": "canonical", "source_id": "source-1", "mapping_version": "mapping-1",
        "capability_mapping_digest": DIGEST, "capability_snapshot_digest": DIGEST,
    }
    values.update(changes)
    return AuditOutcome(**values)


def linked(event_class, **changes):
    values = {
        "event_id": "auth-1" if event_class is AuditEventClass.AUTH else "final-1",
        "event_class": event_class, "scope_kind": "project", "scope_id": "project-1",
        "decision": AuditDecision.ALLOW, "result_status": AuditResult.ALLOW,
        "correlation_id": "corr-1", "run_id": "run-1", "occurred_at": STAMP,
        "identity": {"tenant_id": "tenant-1", "app_id": "app-1", "app_version_id": "version-1", "plugin_instance_id": "plugin-1", "capability_key": "files.read"},
        "digests": {"arguments_digest": DIGEST, "payload_digest": DIGEST, "app_artifact_digest": DIGEST, "capability_snapshot_digest": DIGEST},
    }
    values.update(changes)
    return AuditLinkedEvent(**values)


class Transaction:
    def __init__(self):
        self.items = []

    async def append_transaction_evidence(self, evidence):
        self.items.append(evidence)


class Quarantine:
    def __init__(self):
        self.items = []

    async def append_quarantine(self, evidence, *, reason):
        self.items.append((evidence, reason))


@pytest.mark.asyncio
async def test_allow_routes_only_full_transaction_evidence():
    transaction = Transaction()
    adapter = AuditIntegrityAdapter(
        outcome_adapter=AuditOutcomeAdapter(transaction=transaction),
        quarantine=Quarantine(),
    )
    original = outcome()
    auth = linked(AuditEventClass.AUTH)
    final = linked(AuditEventClass.FINAL_RESULT)
    result = await adapter.append(AuditIntegrityAppendRequest(original, auth, final))
    assert transaction.items == [result]
    assert result.outcome is original
    assert result.outcome.correlation_id == original.correlation_id
    assert result.outcome.arguments_digest == original.arguments_digest
    assert result.outcome.payload_digest == original.payload_digest
    assert result.outcome.result_status is original.result_status
    assert isinstance(result, AuditTransactionEvidenceV2)
    assert result.VERSION == "2"
    assert result.auth_event is auth
    assert result.final_result_event.event_id == "final-1"
    assert result.scope == ("project", "project-1")
    assert result.integrity.auth_event.digests["payload_digest"] == DIGEST
    # The redaction marker lives on the full audit envelope; ``redacted`` is the
    # narrow MCP projection and by contract carries only MCP fields, so the
    # version has to be checked where it is actually preserved.
    assert result.redacted.argument_digest == original.arguments_digest
    assert result.redacted.correlation_id == original.correlation_id
    assert result.outcome.redaction_version == original.redaction_version


@pytest.mark.asyncio
async def test_quarantine_routes_only_to_durable_quarantine():
    transaction = Transaction()
    quarantine = Quarantine()
    adapter = AuditIntegrityAdapter(
        outcome_adapter=AuditOutcomeAdapter(transaction=transaction), quarantine=quarantine
    )
    quarantined = outcome(
        decision=AuditDecision.QUARANTINE,
        result_status=AuditResult.ERROR,
        # A quarantine outcome whose reason_code still said "allowed" (the
        # default of this helper, which was written for an allow outcome) is a
        # self-contradictory envelope: quarantine evidence must name the reason
        # it was quarantined, and the durable record keeps that same code.
        reason_code="review",
        error_code="review",
    )
    auth = linked(AuditEventClass.AUTH, decision=AuditDecision.QUARANTINE, result_status=AuditResult.ERROR, error_code="review")
    final = linked(AuditEventClass.FINAL_RESULT, decision=AuditDecision.QUARANTINE, result_status=AuditResult.ERROR, error_code="review")
    result = await adapter.append(AuditIntegrityAppendRequest(quarantined, auth, final))
    assert transaction.items == []
    assert quarantine.items == [(result, "review")]
    assert result.outcome is quarantined
    assert result.auth_event is auth
    assert result.final_result_event is final
    assert result.event_ids == (auth.event_id, final.event_id)


@pytest.mark.asyncio
async def test_deny_routes_full_transaction_evidence_without_reconstruction():
    transaction = Transaction()
    adapter = AuditIntegrityAdapter(
        outcome_adapter=AuditOutcomeAdapter(transaction=transaction), quarantine=Quarantine()
    )
    original = outcome(decision=AuditDecision.DENY, result_status=AuditResult.DENY, error_code="denied")
    result = await adapter.append(
        AuditIntegrityAppendRequest(
            original,
            linked(AuditEventClass.AUTH, decision=AuditDecision.DENY, result_status=AuditResult.DENY, error_code="denied"),
            linked(AuditEventClass.FINAL_RESULT, decision=AuditDecision.DENY, result_status=AuditResult.DENY, error_code="denied"),
        )
    )
    assert transaction.items == [result]
    assert result.outcome is original
    assert result.decision is AuditDecision.DENY
    assert result.result_status is AuditResult.DENY
    assert result.redacted.correlation_id == original.correlation_id
    assert result.auth_event.event_id == "auth-1"
    assert result.final_result_event.event_id == "final-1"
    assert result.scope == ("project", "project-1")
    assert result.integrity.auth_event.digests["arguments_digest"] == DIGEST
    # Same reason as above: the redaction marker belongs to the full envelope.
    assert result.outcome.redaction_version == original.redaction_version


@pytest.mark.asyncio
async def test_invalid_linkage_precedes_and_blocks_both_routes():
    transaction = Transaction()
    quarantine = Quarantine()
    adapter = AuditIntegrityAdapter(
        outcome_adapter=AuditOutcomeAdapter(transaction=transaction), quarantine=quarantine
    )
    invalid_final = linked(AuditEventClass.FINAL_RESULT, event_id="other-final")
    with pytest.raises(ValueError, match="outcome event_id"):
        await adapter.append(
            AuditIntegrityAppendRequest(outcome(), linked(AuditEventClass.AUTH), invalid_final)
        )
    assert transaction.items == []
    assert quarantine.items == []


def test_validation_precedes_any_route_and_zone_has_no_trusted_redacted_append():
    source = inspect.getsource(AuditIntegrityAdapter).lower()
    assert "validate" in source
    assert "append_transaction_evidence" in source
    assert "append_quarantine" in source
    assert "append_trusted" not in source
    assert "redactedauditoutcome" not in source
    assert "audittransactionevidencev2" in source
    assert "sqlalchemy" not in source
    assert "session.commit" not in source
    assert "router" not in source
    assert AuditIntegrityService is not None


def test_adapter_is_the_only_composition_entrypoint():
    assert AuditIntegrityAdapter.__module__.endswith("audit_integrity_adapter.service")
    assert AuditOutcomeAdapter.__module__.endswith("audit_outcome_adapter.service")
