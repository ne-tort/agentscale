"""Acceptance tests for the exclusive M06 V2 audit persistence zone.

These tests are executed: the migration verification freeze is lifted.
"""
from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from prodavan.application.audit_integrity import AuditEventClass, AuditLinkedEvent, AuditOutcomeEvidence
from prodavan.application.audit_outcome_adapter import AuditOutcomeAdapter, AuditTransactionEvidenceV2
from prodavan.application.audit_outcome_adapter import AuditOutcomeEvidence as AdapterOutcomeEvidence
from prodavan.application.audit_outcomes import AuditDecision, AuditOutcome, AuditResult
from prodavan.application.canonical_grants_persistence_v2_adapter import (
    CanonicalGrantsPersistenceV2Adapter,
    LegacyAuditClassification,
)
from prodavan.application.canonical_grants_promotion import AuditResult as ProjectionResult
from prodavan.application.canonical_grants_promotion import RedactedAuditOutcome

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


class Trusted:
    def __init__(self):
        self.items = []

    async def append_trusted(self, evidence):
        self.items.append(evidence)


class Quarantine:
    def __init__(self):
        self.items = []

    async def append_quarantine(self, evidence, *, reason):
        self.items.append((evidence, reason))


@pytest.mark.asyncio
async def test_trusted_append_accepts_only_v2_and_preserves_full_linked_evidence():
    trusted = Trusted()
    adapter = CanonicalGrantsPersistenceV2Adapter(trusted=trusted)
    original = outcome()
    integrity = AuditOutcomeEvidence(original, "project", "project-1", linked(AuditEventClass.AUTH), linked(AuditEventClass.FINAL_RESULT))
    evidence = AuditTransactionEvidenceV2(legacy=AuditOutcomeAdapter.adapt(original), integrity=integrity)

    await adapter.append_trusted(evidence)

    assert trusted.items == [evidence]
    assert trusted.items[0].VERSION == "2"
    assert trusted.items[0].outcome is original
    assert trusted.items[0].event_ids == ("auth-1", "final-1")
    assert trusted.items[0].scope == ("project", "project-1")
    assert trusted.items[0].outcome.capability_snapshot_digest == DIGEST
    assert trusted.items[0].redacted.argument_digest == DIGEST


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [AuditDecision.ALLOW, AuditDecision.DENY])
async def test_dispatch_routes_allow_and_deny_v2_to_trusted_only(decision):
    trusted, quarantine = Trusted(), Quarantine()
    adapter = CanonicalGrantsPersistenceV2Adapter(trusted=trusted, quarantine=quarantine)
    original = outcome(
        decision=decision,
        result_status=AuditResult.ALLOW if decision is AuditDecision.ALLOW else AuditResult.DENY,
        error_code=None if decision is AuditDecision.ALLOW else "denied",
    )
    integrity = AuditOutcomeEvidence(original, "project", "project-1", linked(AuditEventClass.AUTH, decision=decision, result_status=original.result_status, error_code=original.error_code), linked(AuditEventClass.FINAL_RESULT, decision=decision, result_status=original.result_status, error_code=original.error_code))
    evidence = AuditTransactionEvidenceV2(legacy=AuditOutcomeAdapter.adapt(original), integrity=integrity)

    assert await adapter.append(evidence) is evidence
    assert trusted.items == [evidence]
    assert quarantine.items == []


@pytest.mark.asyncio
async def test_trusted_append_rejects_redacted_projection_mismatch_before_port_call():
    trusted = Trusted()
    adapter = CanonicalGrantsPersistenceV2Adapter(trusted=trusted)
    original = outcome()
    integrity = AuditOutcomeEvidence(original, "project", "project-1", linked(AuditEventClass.AUTH), linked(AuditEventClass.FINAL_RESULT))
    redacted = replace(AuditOutcomeAdapter.adapt(original).redacted, tool_name="write")
    evidence = AuditTransactionEvidenceV2(
        legacy=AdapterOutcomeEvidence(outcome=original, redacted=redacted),
        integrity=integrity,
    )

    with pytest.raises(ValueError, match="redacted tool identity"):
        await adapter.append_trusted(evidence)

    assert trusted.items == []


@pytest.mark.asyncio
async def test_trusted_append_rejects_non_v2_version_before_port_call(monkeypatch):
    trusted = Trusted()
    adapter = CanonicalGrantsPersistenceV2Adapter(trusted=trusted)
    original = outcome()
    integrity = AuditOutcomeEvidence(original, "project", "project-1", linked(AuditEventClass.AUTH), linked(AuditEventClass.FINAL_RESULT))
    evidence = AuditTransactionEvidenceV2(legacy=AuditOutcomeAdapter.adapt(original), integrity=integrity)
    monkeypatch.setattr(AuditTransactionEvidenceV2, "VERSION", "3")

    with pytest.raises(ValueError, match="unsupported audit transaction evidence version"):
        await adapter.append_trusted(evidence)

    assert trusted.items == []


@pytest.mark.asyncio
async def test_trusted_append_rejects_broken_linked_identity_before_port_call():
    trusted = Trusted()
    adapter = CanonicalGrantsPersistenceV2Adapter(trusted=trusted)
    original = outcome()
    integrity = AuditOutcomeEvidence(
        original,
        "project",
        "project-1",
        linked(AuditEventClass.AUTH, identity={"tenant_id": "other-tenant"}),
        linked(AuditEventClass.FINAL_RESULT),
    )
    evidence = AuditTransactionEvidenceV2(legacy=AuditOutcomeAdapter.adapt(original), integrity=integrity)

    with pytest.raises(ValueError, match="identity mismatch"):
        await adapter.append_trusted(evidence)

    assert trusted.items == []


@pytest.mark.asyncio
async def test_trusted_append_rejects_broken_linked_digest_before_port_call():
    trusted = Trusted()
    adapter = CanonicalGrantsPersistenceV2Adapter(trusted=trusted)
    original = outcome()
    integrity = AuditOutcomeEvidence(
        original,
        "project",
        "project-1",
        linked(AuditEventClass.AUTH, digests={"arguments_digest": "sha256:" + "b" * 64}),
        linked(AuditEventClass.FINAL_RESULT),
    )
    evidence = AuditTransactionEvidenceV2(legacy=AuditOutcomeAdapter.adapt(original), integrity=integrity)

    with pytest.raises(ValueError, match="digest mismatch"):
        await adapter.append_trusted(evidence)

    assert trusted.items == []


@pytest.mark.asyncio
async def test_quarantine_never_calls_trusted_port():
    trusted, quarantine = Trusted(), Quarantine()
    adapter = CanonicalGrantsPersistenceV2Adapter(trusted=trusted, quarantine=quarantine)
    # The durable quarantine record is keyed by the canonical ``reason_code`` --
    # the same field ``AuditIntegrityService.append`` uses -- while ``error_code``
    # describes the execution error.  The fixture used to keep the helper's
    # default ``reason_code="allowed"`` while asserting the quarantine reason was
    # "review" (i.e. the *error* code), so the only way to satisfy it was to key
    # quarantine on the execution error and drop the canonical reason.  The two
    # fields are now deliberately distinct so the assertion pins the right one.
    quarantined = outcome(
        decision=AuditDecision.QUARANTINE,
        result_status=AuditResult.ERROR,
        reason_code="quarantine_review",
        error_code="tool_error",
    )
    integrity = AuditOutcomeEvidence(
        quarantined,
        "project",
        "project-1",
        linked(AuditEventClass.AUTH, decision=AuditDecision.QUARANTINE, result_status=AuditResult.ERROR, error_code="tool_error"),
        linked(AuditEventClass.FINAL_RESULT, decision=AuditDecision.QUARANTINE, result_status=AuditResult.ERROR, error_code="tool_error"),
    )

    result = await adapter.append(integrity)

    assert result is integrity
    assert trusted.items == []
    assert quarantine.items == [(integrity, "quarantine_review")]


@pytest.mark.asyncio
async def test_legacy_redacted_append_is_classified_and_quarantined():
    quarantine = Quarantine()
    adapter = CanonicalGrantsPersistenceV2Adapter(quarantine=quarantine)
    legacy = RedactedAuditOutcome(
        mcp_trace_id="trace-1", span_id="span-1", profile_id="profile-1",
        tool_server="server-1", tool_name="read", tool_version="v1",
        argument_digest=DIGEST, result=ProjectionResult.ALLOW, duration_ms=1, occurred_at=STAMP,
        correlation_id="corr-1",
    )

    classification = await adapter.append(legacy)

    assert isinstance(classification, LegacyAuditClassification)
    assert classification.outcome is legacy
    assert quarantine.items[0][0] is classification
    assert quarantine.items[0][1] == "legacy_redacted_audit_outcome"


def test_zone_has_no_database_routes_or_legacy_trusted_append():
    source = inspect.getsource(CanonicalGrantsPersistenceV2Adapter).lower()
    assert "sqlalchemy" not in source
    assert "session.commit" not in source
    assert "router" not in source
    assert "append_audit_outcome" not in source
    assert "append_trusted" in source
    assert "audittransactionevidencev2" in source
