"""Source acceptance contracts for M06 promotion evidence.

Migration verification is intentionally frozen; this file is not executed here.
"""

from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from prodavan.application.canonical_grants_promotion import (
    AuditResult,
    CanonicalGrantsPromotionService,
    PromotionOutcome,
    PromotionPartitionKey,
    PromotionRecord,
    QuarantineKind,
    QuarantineRecord,
    RedactedAuditOutcome,
    REQUIRED_GATE_KEYS,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def partition(**changes):
    values = {"tenant_id": "tenant-1", "app_definition_id": "definition-1"}
    values.update(changes)
    return PromotionPartitionKey(**values)


def promotion(**changes):
    values = {
        "partition": partition(),
        "source_snapshot_id": "snapshot-1",
        "source_schema_version": "m06.v1",
        "source_digest": DIGEST,
        "canonical_digest": DIGEST,
        "binding_digest": DIGEST,
        "final_delta_checkpoint": "delta-42",
        "gate_evidence": {key: {"passed": True} for key in REQUIRED_GATE_KEYS},
        "fencing_epoch": 4,
        "writer_generation": 9,
        "decision_owner": "legacy",
        "actor": "migration-worker",
        "decided_at": NOW,
        "outcome": PromotionOutcome.SHADOW,
    }
    values.update(changes)
    return PromotionRecord(**values)


def test_promotion_is_immutable_append_only_evidence_and_defaults_closed():
    record = promotion(gate_evidence={key: {"passed": True, "checks": ["ok"]} for key in REQUIRED_GATE_KEYS})
    assert not record.canonical_positive_writes
    assert not record.reviewed_final
    assert isinstance(record.gate_evidence, MappingProxyType)
    first_gate = REQUIRED_GATE_KEYS[0]
    assert isinstance(record.gate_evidence[first_gate], MappingProxyType)
    with pytest.raises((AttributeError, TypeError)):
        record.outcome = PromotionOutcome.CANONICAL_OWNER
    with pytest.raises(TypeError):
        record.gate_evidence[first_gate] = "changed"
    with pytest.raises(TypeError):
        record.gate_evidence[first_gate]["checks"] = ("changed",)
    with pytest.raises(ValueError, match="positive"):
        promotion(fencing_epoch=0)
    with pytest.raises(ValueError, match="positive"):
        promotion(writer_generation=0)
    with pytest.raises(ValueError, match="canonical_owner"):
        promotion(outcome=PromotionOutcome.CANONICAL_OWNER)
    with pytest.raises(ValueError, match="sha256"):
        promotion(source_digest="sha256:" + "z" * 64)


def test_missing_gate_blocks_promotion_evidence():
    assert len(REQUIRED_GATE_KEYS) == 12
    assert len(set(REQUIRED_GATE_KEYS)) == 12
    values = {key: {"passed": True} for key in REQUIRED_GATE_KEYS[:-1]}
    with pytest.raises(ValueError, match="missing required gate evidence"):
        promotion(gate_evidence=values)


def test_positive_writes_require_reviewed_final_canonical_owner():
    # Контракт отказывает формулировкой «requires a reviewed final record»:
    # положительные записи разрешены только финальной ревизованной записи
    # (которая сама обязана быть canonical_owner). Прежнее ожидание
    # «reviewed canonical_owner» не совпадало ни с одним сообщением контракта.
    with pytest.raises(ValueError, match="reviewed final record"):
        promotion(canonical_positive_writes=True)
    final = CanonicalGrantsPromotionService.reviewed_final(
        promotion(outcome=PromotionOutcome.PROMOTABLE), reviewer="security-reviewer", actor="owner"
    )
    assert final.outcome is PromotionOutcome.CANONICAL_OWNER
    assert final.reviewed_final
    assert final.canonical_positive_writes
    assert final.reviewer == "security-reviewer"
    assert final.actor == "owner"
    assert final.decided_at.tzinfo is not None
    assert final.source_snapshot_id == "snapshot-1"
    assert final.source_digest == DIGEST
    assert final.canonical_digest == DIGEST
    assert final.binding_digest == DIGEST
    assert final.final_delta_checkpoint == "delta-42"
    assert final.fencing_epoch == 4
    assert final.writer_generation == 9
    source = promotion(outcome=PromotionOutcome.PROMOTABLE)
    final = CanonicalGrantsPromotionService.reviewed_final(source, reviewer="security-reviewer", actor="owner")
    assert final.record_id != source.record_id
    assert final.correlation_id != source.correlation_id
    assert final.parent_record_id == source.record_id
    assert final.lineage_reference == source.correlation_id
    assert source.outcome is PromotionOutcome.PROMOTABLE
    assert not source.reviewed_final
    assert not source.canonical_positive_writes
    assert source.reviewer is None

    with pytest.raises(ValueError, match="reviewer and actor"):
        CanonicalGrantsPromotionService.reviewed_final(source, reviewer="", actor="owner")
    with pytest.raises(ValueError, match="reviewer and actor"):
        CanonicalGrantsPromotionService.reviewed_final(source, reviewer="security-reviewer", actor="")
    with pytest.raises(ValueError, match="reviewer and actor"):
        CanonicalGrantsPromotionService.reviewed_final(source, reviewer=None, actor="owner")


def test_narrowed_partition_requires_complete_identity_and_preserves_digests():
    with pytest.raises(ValueError, match="complete narrowed partition identity"):
        partition(scope_kind="project", scope_id="project-1")
    with pytest.raises(ValueError, match="complete narrowed partition identity"):
        partition(definition_version="v1")
    with pytest.raises(ValueError, match="complete narrowed partition identity"):
        partition(artifact_digest=DIGEST)
    narrowed = partition(
        scope_kind="project", scope_id="project-1", plugin_instance_id="instance-1",
        definition_version="v1", artifact_digest=DIGEST, subject_id="subject-1",
        session_id="session-1", runtime_generation=3, lease_id="lease-1",
    )
    record = promotion(partition=narrowed)
    assert record.source_digest == DIGEST
    assert record.canonical_digest == DIGEST
    assert record.binding_digest == DIGEST


def test_quarantine_rejects_positive_writes_even_when_other_evidence_is_valid():
    with pytest.raises(ValueError, match="quarantine"):
        QuarantineRecord(
            partition=partition(),
            kind=QuarantineKind.UNSUPPORTED_GRANT,
            source_kind="legacy_grant",
            source_id="grant-1",
            source_schema_version="v1",
            source_digest=DIGEST,
            reason_code="unsupported",
            observed_at=NOW,
            checkpoint="cp-1",
            canonical_positive_writes=True,
        )


class _AppendSink:
    async def append_promotion(self, record):
        self.record = record


class _EvidenceReader:
    async def read_evidence(self, *, partition):
        return {key: {"passed": True, "source": "server"} for key in REQUIRED_GATE_KEYS}


class _GateDecision:
    async def evaluate_gates(self, *, partition, evidence):
        return {key: {"passed": True, "reviewed": True} for key in REQUIRED_GATE_KEYS}


class _IdentityChangingDecision:
    async def evaluate_gates(self, *, partition, evidence):
        return {
            **{key: {"passed": True, "reviewed": True} for key in REQUIRED_GATE_KEYS},
            "source_digest": "sha256:" + "b" * 64,
            "canonical_digest": "sha256:" + "c" * 64,
            "binding_digest": "sha256:" + "d" * 64,
            "fencing_epoch": 99,
        }


@pytest.mark.asyncio
async def test_failed_supplied_gate_cannot_be_overridden_by_decision_port():
    failed_key = REQUIRED_GATE_KEYS[0]
    service = CanonicalGrantsPromotionService(
        promotions=_AppendSink(), decisions=_GateDecision()
    )
    record = await service.collect_pre_cutover(
        partition=partition(), source_snapshot_id="snapshot-1", source_schema_version="m06.v1",
        source_digest=DIGEST, canonical_digest=DIGEST, binding_digest=DIGEST,
        final_delta_checkpoint="delta-42",
        gate_evidence={key: {"passed": key != failed_key} for key in REQUIRED_GATE_KEYS},
        fencing_epoch=4, writer_generation=9, decision_owner="legacy", actor="worker", decided_at=NOW,
    )
    assert record.outcome is PromotionOutcome.BLOCKED
    assert record.gate_evidence[failed_key]["passed"] is False
    assert record.gate_evidence[failed_key] == {"passed": False}
    assert not record.canonical_positive_writes


@pytest.mark.asyncio
async def test_decision_port_cannot_change_source_canonical_binding_or_fencing_identity():
    service = CanonicalGrantsPromotionService(
        promotions=_AppendSink(), decisions=_IdentityChangingDecision()
    )
    record = await service.collect_pre_cutover(
        partition=partition(), source_snapshot_id="snapshot-1", source_schema_version="m06.v1",
        source_digest=DIGEST, canonical_digest=DIGEST, binding_digest=DIGEST,
        final_delta_checkpoint="delta-42",
        gate_evidence={key: {"passed": True} for key in REQUIRED_GATE_KEYS},
        fencing_epoch=4, writer_generation=9, decision_owner="legacy", actor="worker", decided_at=NOW,
    )
    assert record.source_snapshot_id == "snapshot-1"
    assert record.source_digest == DIGEST
    assert record.canonical_digest == DIGEST
    assert record.binding_digest == DIGEST
    assert record.fencing_epoch == 4
    assert record.writer_generation == 9
    assert "derived" in record.gate_evidence
    assert "source_digest" not in record.gate_evidence
    assert "canonical_digest" not in record.gate_evidence
    assert "binding_digest" not in record.gate_evidence
    assert "fencing_epoch" not in record.gate_evidence
    assert record.gate_evidence["derived"]["source_digest"] == "sha256:" + "b" * 64
    assert record.gate_evidence["derived"]["canonical_digest"] == "sha256:" + "c" * 64
    assert record.gate_evidence["derived"]["binding_digest"] == "sha256:" + "d" * 64
    assert record.gate_evidence["derived"]["fencing_epoch"] == 99


@pytest.mark.asyncio
async def test_missing_source_gate_cannot_be_overridden_by_decision_port():
    service = CanonicalGrantsPromotionService(
        promotions=_AppendSink(), decisions=_GateDecision()
    )
    record = await service.collect_pre_cutover(
        partition=partition(), source_snapshot_id="snapshot-1", source_schema_version="m06.v1",
        source_digest=DIGEST, canonical_digest=DIGEST, binding_digest=DIGEST,
        final_delta_checkpoint="delta-42", gate_evidence={key: {"passed": True} for key in REQUIRED_GATE_KEYS[:-1]},
        fencing_epoch=4, writer_generation=9, decision_owner="legacy", actor="worker", decided_at=NOW,
    )
    assert record.outcome is PromotionOutcome.BLOCKED
    assert record.gate_evidence[REQUIRED_GATE_KEYS[-1]]["passed"] is False
    assert not record.canonical_positive_writes


@pytest.mark.asyncio
async def test_collect_pre_cutover_uses_read_decision_ports_and_stays_closed():
    service = CanonicalGrantsPromotionService(
        promotions=_AppendSink(), evidence=_EvidenceReader(), decisions=_GateDecision()
    )
    record = await service.collect_pre_cutover(
        partition=partition(), source_snapshot_id="snapshot-1", source_schema_version="m06.v1",
        source_digest=DIGEST, canonical_digest=DIGEST, binding_digest=DIGEST,
        final_delta_checkpoint="delta-42", gate_evidence={}, fencing_epoch=4,
        writer_generation=9, decision_owner="legacy", actor="worker", decided_at=NOW,
    )
    assert record.outcome is PromotionOutcome.PROMOTABLE
    assert not record.canonical_positive_writes
    assert set(REQUIRED_GATE_KEYS).issubset(record.gate_evidence)
    assert set(REQUIRED_GATE_KEYS).issubset(record.gate_evidence["derived"])
    assert record.gate_evidence[REQUIRED_GATE_KEYS[0]]["source"] == "server"


@pytest.mark.asyncio
async def test_collect_pre_cutover_missing_evidence_is_blocked():
    service = CanonicalGrantsPromotionService(promotions=_AppendSink())
    record = await service.collect_pre_cutover(
        partition=partition(), source_snapshot_id="snapshot-1", source_schema_version="m06.v1",
        source_digest=DIGEST, canonical_digest=DIGEST, binding_digest=DIGEST,
        final_delta_checkpoint="delta-42", gate_evidence={}, fencing_epoch=4,
        writer_generation=9, decision_owner="legacy", actor="worker", decided_at=NOW,
    )
    assert record.outcome is PromotionOutcome.BLOCKED
    assert not record.canonical_positive_writes


def test_partition_is_smallest_bounded_key_and_scope_pair_is_atomic():
    narrowed = partition(
        scope_kind="project", scope_id="project-1", plugin_instance_id="instance-1",
        definition_version="v1", artifact_digest=DIGEST, subject_id="subject-1",
        session_id="session-1", runtime_generation=3, lease_id="lease-1",
    )
    assert narrowed.scope_id == "project-1"
    with pytest.raises(ValueError, match="supplied together"):
        partition(scope_kind="project")


def test_quarantine_covers_unsupported_grant_mapping_and_audit_failure():
    for kind in QuarantineKind:
        record = QuarantineRecord(
            partition=partition(),
            kind=kind,
            source_kind="legacy_grant",
            source_id="grant-1",
            source_schema_version="v1",
            source_digest=DIGEST,
            reason_code=kind.value,
            observed_at=NOW,
            checkpoint="cp-1",
            redacted_metadata={"evidence": {"source": "legacy"}},
        )
        assert record.kind is kind
        assert not record.canonical_positive_writes
        assert isinstance(record.resolved_identities, MappingProxyType)
        assert isinstance(record.redacted_metadata["evidence"], MappingProxyType)
        with pytest.raises(TypeError):
            record.redacted_metadata["evidence"]["source"] = "raw"


def test_audit_outcome_contains_trace_span_profile_tool_argument_digest_and_result():
    outcome = RedactedAuditOutcome(
        mcp_trace_id="trace-1",
        span_id="span-1",
        profile_id="profile-1",
        tool_server="server-1",
        tool_name="lookup",
        tool_version="v1",
        argument_digest=DIGEST,
        result=AuditResult.ERROR,
        error_code="adapter_timeout",
        duration_ms=125,
        occurred_at=NOW,
    )
    payload = outcome.redacted()
    assert payload["mcp_trace_id"] == "trace-1"
    assert payload["span_id"] == "span-1"
    assert payload["profile_id"] == "profile-1"
    assert payload["tool_server"] == "server-1"
    assert payload["tool_name"] == "lookup"
    assert payload["tool_version"] == "v1"
    assert payload["argument_digest"] == DIGEST
    assert payload["error_code"] == "adapter_timeout"
    assert payload["duration_ms"] == 125
    assert "arguments" not in payload


def test_audit_error_requires_error_code_and_raw_arguments_have_no_contract_field():
    with pytest.raises(ValueError, match="error_code"):
        RedactedAuditOutcome(
            mcp_trace_id="trace-1", span_id="span-1", profile_id="profile-1",
            tool_server="server-1", tool_name="lookup", tool_version="v1", argument_digest=DIGEST,
            result=AuditResult.ERROR, duration_ms=1, occurred_at=NOW,
        )
