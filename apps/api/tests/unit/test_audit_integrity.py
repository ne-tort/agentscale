"""Source acceptance tests for the exclusive audit integrity zone.

The migration verification freeze is lifted: this file is executed and must stay green.
"""
from datetime import UTC, datetime
import inspect

import pytest

from prodavan.application.audit_integrity import (
    AuditEventClass,
    AuditIntegrityService,
    AuditLinkedEvent,
)
from prodavan.application.audit_outcomes import AuditDecision, AuditOutcome, AuditResult


DIGEST = "sha256:" + "a" * 64
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def make_outcome(**changes):
    values = {
        "occurred_at": STAMP, "event_id": "final-1", "event_type": "mcp.tool.outcome",
        "decision": AuditDecision.ALLOW, "reason_code": "allowed", "tenant_id": "tenant-1",
        "app_definition_id": "definition-1", "app_id": "app-1", "app_version_id": "version-1",
        "request_id": "request-1", "trace_id": "trace-1", "span_id": "span-1",
        "profile_id": "profile-1", "tool_server": "server-1", "tool_name": "read",
        "tool_version": "v1", "arguments_digest": DIGEST, "operation": "read",
        "resource": "project-1", "domain": "project", "payload_digest": DIGEST,
        "result_status": AuditResult.ALLOW, "error_code": None, "duration_ms": 3,
        "correlation_id": "corr-1", "plugin_instance_id": "plugin-1",
        "capability_key": "files.read", "app_artifact_digest": DIGEST,
        "capability_snapshot_digest": DIGEST,
    }
    values.update(changes)
    return AuditOutcome(**values)


def event(event_class, **changes):
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
    def __init__(self): self.items = []
    async def append_trusted(self, evidence): self.items.append(evidence)


class Quarantine:
    def __init__(self): self.items = []
    async def append_quarantine(self, evidence, *, reason): self.items.append((evidence, reason))


@pytest.mark.asyncio
async def test_valid_linked_allow_routes_to_trusted_append():
    sink = Trusted()
    result = await AuditIntegrityService(trusted=sink).append(
        make_outcome(), auth_event=event(AuditEventClass.AUTH), final_result_event=event(AuditEventClass.FINAL_RESULT)
    )
    assert sink.items == [result]
    assert result.auth_event.scope_id == result.final_result_event.scope_id == "project-1"


def test_rejects_scope_identity_digest_correlation_and_timestamp_mismatch():
    base = dict(auth_event=event(AuditEventClass.AUTH), final_result_event=event(AuditEventClass.FINAL_RESULT))
    with pytest.raises(ValueError, match="scope"):
        AuditIntegrityService.validate(make_outcome(), **{**base, "final_result_event": event(AuditEventClass.FINAL_RESULT, scope_id="other")})
    with pytest.raises(ValueError, match="identity"):
        AuditIntegrityService.validate(make_outcome(), **{**base, "auth_event": event(AuditEventClass.AUTH, identity={**event(AuditEventClass.AUTH).identity, "app_id": "other"})})
    with pytest.raises(ValueError, match="digest"):
        AuditIntegrityService.validate(make_outcome(), **{**base, "final_result_event": event(AuditEventClass.FINAL_RESULT, digests={**event(AuditEventClass.FINAL_RESULT).digests, "payload_digest": "sha256:" + "b" * 64})})
    with pytest.raises(ValueError, match="correlation"):
        AuditIntegrityService.validate(make_outcome(), **{**base, "final_result_event": event(AuditEventClass.FINAL_RESULT, correlation_id="other")})
    with pytest.raises(ValueError, match="timestamp"):
        AuditIntegrityService.validate(make_outcome(), **{**base, "final_result_event": event(AuditEventClass.FINAL_RESULT, occurred_at=datetime(2026, 1, 2, tzinfo=UTC))})


def test_requires_final_result_identity_and_matching_decision():
    base = dict(auth_event=event(AuditEventClass.AUTH), final_result_event=event(AuditEventClass.FINAL_RESULT))
    with pytest.raises(ValueError, match="final result event"):
        AuditIntegrityService.validate(make_outcome(event_id="auth-1"), **base)
    with pytest.raises(ValueError, match="decision"):
        AuditIntegrityService.validate(
            make_outcome(),
            **{**base, "final_result_event": event(AuditEventClass.FINAL_RESULT, decision=AuditDecision.DENY, result_status=AuditResult.ALLOW)},
        )


@pytest.mark.asyncio
async def test_quarantine_never_uses_trusted_success_and_requires_durable_port():
    # A quarantine envelope whose reason_code still said "allowed" (the default of
    # this helper, written for an allow outcome) is self-contradictory: the durable
    # quarantine record must name the reason it was quarantined, and the service
    # keeps that same code (see test_audit_integrity_adapter for the same rule).
    outcome = make_outcome(
        decision=AuditDecision.QUARANTINE,
        result_status=AuditResult.ERROR,
        reason_code="review",
        error_code="review",
    )
    auth = event(AuditEventClass.AUTH, decision=AuditDecision.QUARANTINE, result_status=AuditResult.ERROR, error_code="review")
    final = event(AuditEventClass.FINAL_RESULT, decision=AuditDecision.QUARANTINE, result_status=AuditResult.ERROR, error_code="review")
    trusted, quarantine = Trusted(), Quarantine()
    evidence = await AuditIntegrityService(trusted=trusted, quarantine=quarantine).append(outcome, auth_event=auth, final_result_event=final)
    assert trusted.items == []
    assert quarantine.items == [(evidence, "review")]
    with pytest.raises(RuntimeError, match="quarantine"):
        await AuditIntegrityService(trusted=trusted).append(outcome, auth_event=auth, final_result_event=final)


def test_integrity_zone_has_no_database_implementation():
    source = inspect.getsource(AuditIntegrityService).lower()
    assert "sqlalchemy" not in source
    assert "session.commit" not in source
    assert "create_grant" not in source
