"""Source acceptance contracts for the M06 persistence adapter.

Migration verification is intentionally frozen; this file is not executed here.
"""

from datetime import UTC, datetime

import pytest

from prodavan.application.canonical_grants.contracts import (
    CanonicalAuditEvent,
    CanonicalGrantDecision,
    CanonicalGrantReason,
)
from prodavan.application.canonical_grants_promotion import (
    AuditResult,
    CanonicalGrantsPromotionService,
    PromotionOutcome,
    PromotionPartitionKey,
    PromotionRecord,
    QuarantineKind,
    QuarantineRecord,
    REQUIRED_GATE_KEYS,
)
from prodavan.application.canonical_grants_persistence_adapter import (
    CanonicalGrantsPersistenceAdapter,
    McpAuditCorrelation,
)


NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def partition():
    return PromotionPartitionKey(tenant_id="tenant-1", app_definition_id="definition-1")


def promotion(**changes):
    values = {
        "partition": partition(),
        "source_snapshot_id": "snapshot-1",
        "source_schema_version": "m06.v1",
        "source_digest": DIGEST,
        "canonical_digest": DIGEST,
        "binding_digest": DIGEST,
        "final_delta_checkpoint": "delta-1",
        "gate_evidence": {key: {"passed": True} for key in REQUIRED_GATE_KEYS},
        "fencing_epoch": 1,
        "writer_generation": 1,
        "decision_owner": "owner",
        "actor": "worker",
        "decided_at": NOW,
        "outcome": PromotionOutcome.PROMOTABLE,
    }
    values.update(changes)
    return PromotionRecord(**values)


def quarantine(**changes):
    values = {
        "partition": partition(),
        "kind": QuarantineKind.AUDIT_FAILURE,
        "source_kind": "legacy_grant",
        "source_id": "grant-1",
        "source_schema_version": "v1",
        "source_digest": DIGEST,
        "reason_code": "audit_failure",
        "observed_at": NOW,
        "checkpoint": "cp-1",
    }
    values.update(changes)
    return QuarantineRecord(**values)


class _Repository:
    def __init__(self):
        self.calls = []

    async def read_promotions(self, *, partition):
        return ("promotion", partition)

    async def read_quarantine(self, *, partition):
        return ("quarantine", partition)

    async def append_evidence(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_adapter_exposes_partition_reads_and_append_only_evidence():
    repository = _Repository()
    adapter = CanonicalGrantsPersistenceAdapter(repository)
    key = partition()

    assert await adapter.read_promotions(partition=key) == ("promotion", key)
    assert await adapter.read_quarantine(partition=key) == ("quarantine", key)
    await adapter.append_promotion(promotion())
    await adapter.append_quarantine(quarantine())

    assert repository.calls == [{"promotion": repository.calls[0]["promotion"]}, {"quarantine": repository.calls[1]["quarantine"]}]
    assert repository.calls[0]["promotion"].canonical_positive_writes is False
    assert repository.calls[1]["quarantine"].canonical_positive_writes is False


@pytest.mark.asyncio
async def test_audit_append_requires_mcp_correlation_id():
    repository = _Repository()
    adapter = CanonicalGrantsPersistenceAdapter(repository)
    from prodavan.application.canonical_grants_promotion import RedactedAuditOutcome

    outcome = RedactedAuditOutcome(
        mcp_trace_id="trace-1", span_id="span-1", profile_id="profile-1",
        tool_server="server-1", tool_name="lookup", tool_version="v1",
        argument_digest=DIGEST, result=AuditResult.ALLOW, duration_ms=1, occurred_at=NOW,
    )
    with pytest.raises(ValueError, match="correlation_id"):
        await adapter.append_audit_outcome(outcome)
    assert repository.calls == []


@pytest.mark.asyncio
async def test_adapter_is_fail_closed_for_positive_write_evidence():
    repository = _Repository()
    adapter = CanonicalGrantsPersistenceAdapter(repository)

    # Запись с canonical_positive_writes=True можно получить только через
    # ревизованный финал: промежуточная запись такого состояния не допускает.
    # Прежний вызов `promotion(canonical_positive_writes=True)` падал в контракте
    # продвижения и до адаптера не доходил, поэтому защита адаптера не
    # проверялась вовсе.
    reviewed_final = CanonicalGrantsPromotionService.reviewed_final(
        promotion(), reviewer="security-reviewer", actor="owner"
    )
    assert reviewed_final.canonical_positive_writes is True
    with pytest.raises(ValueError, match="positive canonical writes"):
        await adapter.append_promotion(reviewed_final)
    assert repository.calls == []

    # Карантин не может включить положительные записи даже на уровне контракта.
    with pytest.raises(ValueError, match="quarantine records cannot enable"):
        quarantine(canonical_positive_writes=True)
    # Тем не менее адаптер обязан отказать и подделанной записи (fail-closed).
    tampered = quarantine()
    object.__setattr__(tampered, "canonical_positive_writes", True)
    with pytest.raises(ValueError, match="positive canonical writes"):
        await adapter.append_quarantine(tampered)
    assert repository.calls == []


def audit_event(decision, reason):
    return CanonicalAuditEvent(
        correlation_id="canonical-correlation",
        occurred_at=NOW,
        company_id="company-1",
        tenant_id="tenant-1",
        app_id="app-1",
        manifest_version="v1",
        app_version_id="version-1",
        plugin_instance_id="instance-1",
        capability_key="files.read",
        operation="read",
        resource="file-1",
        domain="workspace",
        decision=decision,
        reason=reason,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "reason", "result", "error_code"),
    [
        (CanonicalGrantDecision.ALLOW, CanonicalGrantReason.ALLOWED, AuditResult.ALLOW, None),
        (CanonicalGrantDecision.DENY, CanonicalGrantReason.GRANT_MISSING, AuditResult.DENY, "grant_missing"),
        (CanonicalGrantDecision.QUARANTINE, CanonicalGrantReason.UNSUPPORTED_GRANT, AuditResult.ERROR, "unsupported_grant"),
    ],
)
async def test_canonical_audit_mapping_keeps_mcp_correlation_and_redaction(
    decision, reason, result, error_code
):
    repository = _Repository()
    adapter = CanonicalGrantsPersistenceAdapter(repository)
    await adapter.append_canonical_audit(
        audit_event(decision, reason),
        mcp=McpAuditCorrelation("trace-1", "span-1", "mcp-correlation"),
        profile_id="profile-1",
        tool_server="server-1",
        tool_name="lookup",
        tool_version="v1",
        argument_digest=DIGEST,
        duration_ms=12,
    )

    outcome = repository.calls[0]["audit"]
    assert outcome.result is result
    assert outcome.error_code == error_code
    assert outcome.mcp_trace_id == "trace-1"
    assert outcome.span_id == "span-1"
    assert outcome.correlation_id == "mcp-correlation"
    assert "arguments" not in outcome.redacted()


def test_mcp_correlation_requires_trace_span_and_correlation():
    with pytest.raises(ValueError, match="mcp_trace_id"):
        McpAuditCorrelation("", "span-1", "correlation-1")
    with pytest.raises(ValueError, match="span_id"):
        McpAuditCorrelation("trace-1", "", "correlation-1")
    with pytest.raises(ValueError, match="correlation_id"):
        McpAuditCorrelation("trace-1", "span-1", "")
