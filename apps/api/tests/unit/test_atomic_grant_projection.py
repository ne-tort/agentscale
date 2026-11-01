"""Acceptance contracts for the exclusive M04/M05 grant projection transaction.

Migration verification is frozen; this suite is intentionally not executed here.
"""
from datetime import UTC, datetime

import pytest

from prodavan.application.atomic_grant_projection import (
    AtomicGrantProjectionCommand,
    AtomicGrantProjectionService,
    AtomicProjectionDecision,
    AtomicProjectionReason,
    M04GrantProjectionContext,
    M05GrantProjectionContext,
)
from prodavan.application.canonical_grants import (
    CanonicalGrantDecision,
    CanonicalGrantDecisionResult,
)
from prodavan.application.canonical_grants_promotion import (
    PromotionOutcome,
    PromotionPartitionKey,
    PromotionRecord,
    REQUIRED_GATE_KEYS,
    QuarantineKind,
    QuarantineRecord,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def partition():
    return PromotionPartitionKey(
        tenant_id="tenant-1", app_definition_id="definition-1",
        scope_kind="plugin_instance", scope_id="instance-1",
        plugin_instance_id="instance-1", definition_version="1.0.0",
        artifact_digest=DIGEST, subject_id="subject-1", session_id="session-1",
        runtime_generation=2, lease_id="lease-1",
    )


def promotion():
    return PromotionRecord(
        partition=partition(), source_snapshot_id="snapshot-1", source_schema_version="m06.v1",
        source_digest=DIGEST, canonical_digest=DIGEST, binding_digest=DIGEST,
        final_delta_checkpoint="delta-1", gate_evidence={key: {"passed": True} for key in REQUIRED_GATE_KEYS},
        fencing_epoch=2, writer_generation=3, decision_owner="canonical", actor="migration-worker",
        decided_at=NOW, outcome=PromotionOutcome.CANONICAL_OWNER, canonical_positive_writes=True,
        reviewed_final=True, reviewer="reviewer", parent_record_id="parent-1", lineage_reference="lineage-1",
    )


def contexts():
    m04 = M04GrantProjectionContext(
        tenant_id="tenant-1", scope_kind="plugin_instance", scope_id="instance-1",
        app_definition_id="definition-1", app_id="billing", app_version_id="version-1",
        manifest_version="1.0.0", artifact_digest=DIGEST, owner_evidence_digest=DIGEST,
    )
    m05 = M05GrantProjectionContext(
        plugin_instance_id="instance-1", subject_id="subject-1", session_id="session-1",
        runtime_generation=2, writer_generation=3, lease_id="lease-1", lease_owner="worker-1",
        artifact_digest=DIGEST,
    )
    return m04, m05


def allow_command(**changes):
    m04, m05 = contexts()
    values = dict(
        m04=m04, m05=m05,
        canonical_decision=CanonicalGrantDecisionResult(CanonicalGrantDecision.ALLOW, "allowed", "corr-1"),
        partition=partition(), promotion_evidence=promotion(), quarantine_evidence=None,
        decision=AtomicProjectionDecision.ALLOW, reason=AtomicProjectionReason.ALLOWED,
        issued_at=NOW, capability_key="storage.kv", operation="read", resource="invoice/1",
        domain="storage", correlation_id="corr-1",
    )
    values.update(changes)
    return AtomicGrantProjectionCommand(**values)


class Repository:
    def __init__(self):
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        return __import__("prodavan.application.atomic_grant_projection", fromlist=["AtomicGrantProjectionResult"]).AtomicGrantProjectionResult(
            command.decision, command.reason, command, True
        )


@pytest.mark.asyncio
async def test_single_repository_execute_owns_allow_transition():
    repository = Repository()
    result = await AtomicGrantProjectionService(repository).execute(allow_command())
    assert result.committed
    assert repository.commands == [repository.commands[0]]
    assert repository.commands[0].m04.app_version_id == "version-1"
    assert repository.commands[0].m05.writer_generation == 3
    assert repository.commands[0].promotion_evidence.reviewed_final


def test_port_exposes_no_split_transition_methods():
    from prodavan.application.atomic_grant_projection import ports
    assert hasattr(ports.AtomicGrantProjectionRepository, "execute")
    for name in ("add", "update", "delete", "project", "quarantine", "promote"):
        assert not hasattr(ports.AtomicGrantProjectionRepository, name)


def test_allow_requires_canonical_allow_and_reviewed_partition_evidence():
    with pytest.raises(ValueError, match="reviewed promotion evidence"):
        allow_command(promotion_evidence=None)
    with pytest.raises(ValueError, match="canonical allow"):
        allow_command(canonical_decision=CanonicalGrantDecisionResult(CanonicalGrantDecision.DENY, "denied", "corr-1"))


def test_m04_m05_context_mismatch_fails_closed():
    with pytest.raises(ValueError, match="artifact digests"):
        allow_command(m05=M05GrantProjectionContext(
            plugin_instance_id="instance-1", subject_id="subject-1", session_id="session-1",
            runtime_generation=2, writer_generation=3, lease_id="lease-1", lease_owner="worker-1",
            artifact_digest="sha256:" + "b" * 64,
        ))
    with pytest.raises(ValueError, match="M05 instance"):
        allow_command(m05=M05GrantProjectionContext(
            plugin_instance_id="other-instance", subject_id="subject-1", session_id="session-1",
            runtime_generation=2, writer_generation=3, lease_id="lease-1", lease_owner="worker-1",
            artifact_digest=DIGEST,
        ))


def test_quarantine_requires_quarantine_evidence_and_never_allow():
    quarantine = QuarantineRecord(
        partition=partition(), kind=QuarantineKind.STALE_GENERATION, source_kind="grant",
        source_id="grant-1", source_schema_version="m06.v1", source_digest=DIGEST,
        reason_code="stale_generation", observed_at=NOW, checkpoint="cp-1",
    )
    command = allow_command(
        decision=AtomicProjectionDecision.QUARANTINE,
        reason=AtomicProjectionReason.QUARANTINED,
        canonical_decision=CanonicalGrantDecisionResult(CanonicalGrantDecision.QUARANTINE, "unsupported_grant", "corr-1"),
        promotion_evidence=None, quarantine_evidence=quarantine,
    )
    assert command.quarantine_evidence is quarantine
    with pytest.raises(ValueError, match="quarantine cannot carry"):
        allow_command(decision=AtomicProjectionDecision.QUARANTINE, reason=AtomicProjectionReason.QUARANTINED,
                      promotion_evidence=None, quarantine_evidence=quarantine)


@pytest.mark.asyncio
async def test_missing_command_is_rejected_before_repository_call():
    repository = Repository()
    with pytest.raises(ValueError, match="complete atomic"):
        await AtomicGrantProjectionService(repository).execute(None)
    assert repository.commands == []
