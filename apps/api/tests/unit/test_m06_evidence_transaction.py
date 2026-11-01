"""Acceptance contracts for the exclusive M06 evidence transaction.

Migration verification is intentionally frozen; this file is not executed here.
"""

from datetime import UTC, datetime

import pytest

from prodavan.application.shared_identity import IdentityEnvelope
from prodavan.application.m06_evidence_transaction import (
    AppendOnlyAudit,
    AuditOutcomeEvidenceV2,
    BundleSnapshot,
    M06EvidenceTransactionService,
    PromotionLineage,
    QuarantineAllowResult,
    RedactedProjection,
    TransactionContext,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def identity():
    return IdentityEnvelope(
        tenant_id="tenant-1", scope_kind="project", scope_id="project-1",
        definition_id="definition-1", version_id="version-1", artifact_digest=DIGEST,
        plugin_instance_id="plugin-1", subject_id="subject-1", session_id="session-1",
        runtime_generation=1, writer_generation=3, lease_id="lease-1", lease_owner="worker-1",
        lease_until=NOW, cabinet_id="cabinet-1", project_id="project-1",
        correlation_id="corr-1", trace_id="trace-1",
    )


def evidence(**changes):
    values = {
        "event_id": "event-1",
        "tenant_id": "tenant-1",
        "decision": QuarantineAllowResult.ALLOW,
        "occurred_at": NOW,
        "identity_digest": identity().envelope_digest,
        "capability_key": "documents.read",
        "promotion": PromotionLineage(
            parent_record_id="promotion-1", source_snapshot_id="bundle-1",
            source_digest=DIGEST, canonical_digest=DIGEST, binding_digest=DIGEST,
            lineage_reference="corr-1",
        ),
        "quarantine_or_allow": QuarantineAllowResult.ALLOW,
        "redacted": RedactedProjection(
            argument_digest=DIGEST, result=QuarantineAllowResult.ALLOW,
            error_code=None, occurred_at=NOW, tool_name="documents.read",
            correlation_id="corr-1", trace_id="trace-1", span_id="span-1", payload_digest=DIGEST,
        ),
        "bundle": BundleSnapshot(
            snapshot_id="bundle-1", schema_version="m06.v2",
            bundle_digest=DIGEST, captured_at=NOW,
        ),
        "audit": AppendOnlyAudit(
            event_id="event-1", event_type="promotion", occurred_at=NOW,
            audit_digest=DIGEST, correlation_id="corr-1",
        ),
        "metadata": {"tenant_id": "tenant-1", "redacted": True},
         "identity": identity(),
    }
    values.update(changes)
    return AuditOutcomeEvidenceV2(**values)


class _AtomicSink:
    def __init__(self):
        self.calls = []

    async def commit_evidence_transaction(self, *, evidence, context):
        self.calls.append((evidence, context))
        return "receipt-1"


@pytest.mark.asyncio
async def test_record_uses_one_atomic_transaction_for_complete_bundle():
    sink = _AtomicSink()
    service = M06EvidenceTransactionService(transaction=sink)
    context = TransactionContext("tenant-1", "actor-1", 3, "request-1")

    receipt = await service.record(evidence=evidence(), context=context)

    assert receipt == "receipt-1"
    assert len(sink.calls) == 1
    committed, committed_context = sink.calls[0]
    assert committed.event_id == "event-1"
    assert committed.promotion.lineage_reference == "corr-1"
    assert committed.bundle.snapshot_id == "bundle-1"
    assert committed.audit.event_id == committed.event_id
    assert committed_context is context
    assert not hasattr(service, "append_audit")
    assert not hasattr(service, "append_promotion")
    assert not hasattr(service, "append_quarantine")


@pytest.mark.asyncio
async def test_quarantine_is_preserved_and_never_promoted_to_allow():
    sink = _AtomicSink()
    service = M06EvidenceTransactionService(transaction=sink)
    quarantined = evidence(
        decision=QuarantineAllowResult.QUARANTINE,
        quarantine_or_allow=QuarantineAllowResult.QUARANTINE,
        redacted=RedactedProjection(
            argument_digest=DIGEST, result=QuarantineAllowResult.QUARANTINE,
            error_code="ambiguous_mapping", occurred_at=NOW,
            tool_name="documents.read", correlation_id="corr-1", trace_id="trace-1", span_id="span-1", payload_digest=DIGEST,
        ),
    )

    await service.record(evidence=quarantined, context=TransactionContext("tenant-1", "actor-1", 3, "request-1"))
    assert sink.calls[0][0].decision is QuarantineAllowResult.QUARANTINE


@pytest.mark.parametrize(
    "change, message",
    [
        ({"audit": AppendOnlyAudit("other", "promotion", NOW, DIGEST, "corr-1")}, "event_id"),
        ({"redacted": RedactedProjection(DIGEST, QuarantineAllowResult.ALLOW, None, NOW, "documents.read", "other", "trace-1", "span-1", DIGEST)}, "correlation"),
        ({"quarantine_or_allow": QuarantineAllowResult.QUARANTINE}, "redacted result"),
    ],
)
def test_full_envelope_rejects_partial_or_inconsistent_evidence(change, message):
    with pytest.raises(ValueError, match=message):
        evidence(**change)


@pytest.mark.asyncio
async def test_allow_requires_authenticated_tenant_match():
    sink = _AtomicSink()
    service = M06EvidenceTransactionService(transaction=sink)
    with pytest.raises(ValueError, match="tenant"):
        await service.record(evidence=evidence(), context=TransactionContext("tenant-2", "actor-1", 3, "request-1"))
    assert sink.calls == []


def test_digest_contract_rejects_raw_payload_shape():
    with pytest.raises(ValueError, match="sha256"):
        evidence(identity_digest="raw-payload")


def test_promotion_snapshot_requires_matching_bundle_digest():
    with pytest.raises(ValueError, match="source digest"):
        evidence(
            promotion=PromotionLineage(
                parent_record_id="promotion-1", source_snapshot_id="bundle-1",
                source_digest="sha256:" + "b" * 64, canonical_digest=DIGEST,
                binding_digest=DIGEST, lineage_reference="corr-1",
            )
        )


def test_context_writer_generation_must_match_identity():
    sink = _AtomicSink()
    service = M06EvidenceTransactionService(transaction=sink)

    with pytest.raises(ValueError, match="writer generation"):
        import asyncio
        asyncio.run(service.record(
            evidence=evidence(),
            context=TransactionContext("tenant-1", "actor-1", 4, "request-1"),
        ))
    assert sink.calls == []


def test_deny_projection_requires_error_code():
    with pytest.raises(ValueError, match="deny"):
        RedactedProjection(
            argument_digest=DIGEST, result=QuarantineAllowResult.DENY,
            error_code=None, occurred_at=NOW, tool_name="documents.read",
            correlation_id="corr-1", trace_id="trace-1", span_id="span-1", payload_digest=DIGEST,
        )


def test_nested_metadata_secrets_are_rejected():
    sink = _AtomicSink()
    service = M06EvidenceTransactionService(transaction=sink)

    with pytest.raises(ValueError, match="secrets"):
        import asyncio
        asyncio.run(service.record(
            evidence=evidence(metadata={"nested": {"token": "redacted"}}),
            context=TransactionContext("tenant-1", "actor-1", 3, "request-1"),
        ))
    assert sink.calls == []
