"""Source acceptance tests for the versioned redacted audit outcome zone.

Migration verification is intentionally frozen; this file is not executed here.
"""

from datetime import UTC, datetime
import inspect

import pytest

from prodavan.application.audit_outcomes import (
    REDACTION_VERSION,
    AuditDecision,
    AuditOutcome,
    AuditResult,
    AuditOutcomeService,
)


DIGEST = "sha256:" + "a" * 64


def make_outcome(**changes):
    values = {
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "event_id": "evt-1",
        "event_type": "mcp.tool.outcome",
        "decision": AuditDecision.ALLOW,
        "reason_code": "allowed",
        "tenant_id": "tenant-1",
        "cabinet_id": "cabinet-1",
        "project_id": "project-1",
        "user_id": "user-1",
        "subject_id": "subject-1",
        "session_id": "session-1",
        "app_definition_id": "definition-1",
        "app_id": "app-1",
        "app_version_id": "version-1",
        "manifest_version": "3.2.1",
        "app_artifact_digest": DIGEST,
        "plugin_instance_id": "plugin-1",
        "runtime_generation": 1,
        "writer_generation": 1,
        "lease_id": "lease-1",
        "capability_grant_id": "grant-1",
        "capability_key": "files.read",
        "source_kind": "canonical",
        "source_id": "source-1",
        "mapping_version": "mapping-v1",
        "request_id": "req-1",
        "trace_id": "trace-1",
        "span_id": "span-1",
        "profile_id": "profile-1",
        "tool_server": "server-1",
        "tool_name": "read",
        "tool_version": "2.1.0",
        "arguments_digest": DIGEST,
        "operation": "read_file",
        "resource": "workspace/a.txt",
        "domain": "project",
        "payload_digest": DIGEST,
        "result_status": AuditResult.ALLOW,
        "error_code": None,
        "duration_ms": 12,
        "correlation_id": "corr-1",
        "capability_mapping_digest": DIGEST,
        "capability_snapshot_digest": DIGEST,
        "metadata": {"checkpoint": "cp-1"},
    }
    values.update(changes)
    return AuditOutcome(**values)


def test_envelope_has_full_m06_fields_and_digest_only_redacted_shape():
    wire = make_outcome().redacted()
    assert set(wire) == {
        "occurred_at", "event_id", "event_type", "decision", "reason_code",
        "tenant_id", "cabinet_id", "project_id", "user_id", "subject_id", "session_id",
        "app_definition_id", "app_id", "app_version_id", "manifest_version",
        "app_artifact_digest", "plugin_instance_id", "runtime_generation", "writer_generation",
        "lease_id", "capability_grant_id", "capability_key", "source_kind", "source_id",
        "mapping_version", "request_id", "trace_id", "span_id", "profile_id", "tool_server",
        "tool_name", "tool_version", "arguments_digest", "operation", "resource", "domain",
        "payload_digest", "result_status", "error_code", "duration_ms", "correlation_id",
        "capability_mapping_digest", "capability_snapshot_digest", "metadata", "redaction_version",
    }
    assert wire["redaction_version"] == REDACTION_VERSION
    assert all(key not in wire for key in ("args", "arguments", "payload", "secret", "raw_input"))


def test_contract_is_immutable_and_rejects_invalid_digest_or_naive_time():
    outcome = make_outcome()
    with pytest.raises((AttributeError, TypeError)):
        outcome.tool_name = "write"
    with pytest.raises(ValueError, match="sha256"):
        make_outcome(arguments_digest="raw-secret")
    with pytest.raises(ValueError, match="timezone-aware"):
        make_outcome(occurred_at=datetime(2026, 1, 1))


def test_result_mapping_is_exact_and_allow_deny_quarantine_semantics_are_explicit():
    service = AuditOutcomeService()
    assert service.map_result("allow") is AuditDecision.ALLOW
    assert service.map_result("deny") is AuditDecision.DENY
    assert service.map_result("quarantine") is AuditDecision.QUARANTINE
    quarantined = service.outcome(make_outcome(), result="quarantine", error_code="policy_review")
    assert quarantined.decision is AuditDecision.QUARANTINE
    assert quarantined.result_status is AuditResult.ERROR
    denied = service.outcome(make_outcome(), result="deny", error_code="grant_missing")
    assert denied.result_status is AuditResult.DENY
    # The envelope contract names the violated rule by its typed fields
    # (decision/result_status/error_code); the assertions below pin those exact
    # messages rather than an older free-form wording ("allow outcome", "match").
    with pytest.raises(ValueError, match="allow result cannot contain an error_code"):
        make_outcome(error_code="unexpected")
    with pytest.raises(ValueError, match="deny decision cannot have allow result_status"):
        make_outcome(decision=AuditDecision.DENY)
    with pytest.raises(ValueError, match="map to"):
        service.map_result("error")


def test_correlation_id_must_match_envelope_before_append():
    service = AuditOutcomeService()
    with pytest.raises(ValueError, match="does not match"):
        service.enforce_correlation(make_outcome(), correlation_id="different")
    assert service.enforce_correlation(make_outcome(), correlation_id="corr-1") == make_outcome()


@pytest.mark.asyncio
async def test_append_and_transaction_evidence_ports_are_append_only_boundaries():
    class AppendSink:
        def __init__(self):
            self.items = []

        async def append(self, outcome):
            self.items.append(outcome)

    class TransactionSink:
        def __init__(self):
            self.items = []

        async def append_transaction_evidence(self, outcome):
            self.items.append(outcome)

    append_sink = AppendSink()
    transaction_sink = TransactionSink()
    outcome = make_outcome()
    await AuditOutcomeService(append=append_sink).record(outcome, correlation_id="corr-1")
    await AuditOutcomeService(transaction=transaction_sink).record(
        outcome, correlation_id="corr-1", transaction_evidence=True
    )
    assert append_sink.items == [outcome]
    assert transaction_sink.items == [outcome]


def test_metadata_is_redacted_and_deeply_immutable():
    with pytest.raises(ValueError, match="raw input"):
        make_outcome(metadata={"arguments": "raw"})
    outcome = make_outcome(metadata={"evidence": {"tags": ["safe"]}})
    with pytest.raises(TypeError):
        outcome.metadata["evidence"]["tags"] = ("changed",)


def test_service_does_not_accept_or_persist_raw_arguments_or_secrets():
    source = inspect.getsource(AuditOutcomeService)
    assert "arguments" not in source.lower()
    assert "secret" not in source.lower()
    assert "sqlalchemy" not in source.lower()
    assert "session.commit" not in source.lower()
