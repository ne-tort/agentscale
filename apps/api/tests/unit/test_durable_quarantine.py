"""Source acceptance tests for the exclusive durable quarantine boundary.

Migration verification is intentionally frozen; this file is not executed here.
"""
from datetime import UTC, datetime
import inspect

import pytest

from prodavan.application.durable_quarantine import (
    DurableQuarantineService,
    ImmutableEvidence,
    PromotionLineage,
    QuarantineDuplicateConflict,
    QuarantineIdentity,
    QuarantinePartition,
    QuarantineReason,
    QuarantineRecord,
)

DIGEST = "sha256:" + "a" * 64
STAMP = datetime(2026, 11, 1, tzinfo=UTC)


def make_record(**changes):
    values = {
        "record_id": "qr-1",
        "idempotency_key": "source:legacy-1:v1",
        "partition": QuarantinePartition("tenant-1", "tenant-1:definition-1"),
        "identity": QuarantineIdentity(
            tenant_id="tenant-1", app_definition_id="definition-1", app_id="app-1",
            app_version_id="version-1", manifest_version="1", scope_kind="project",
            scope_id="project-1", subject_kind="employee", subject_id="employee-1",
            plugin_instance_id="plugin-1", session_id="session-1", runtime_generation=2,
            writer_generation=4, capability_key="files.read",
        ),
        "reason": QuarantineReason.DIGEST_CONFLICT,
        "source_digest": DIGEST, "canonical_digest": DIGEST, "binding_digest": DIGEST,
        "promotion_lineage": PromotionLineage("legacy_grant", "legacy-1", "checkpoint-7"),
        "correlation_id": "corr-1", "trace_id": "trace-1", "span_id": "span-1",
        "evidence": ImmutableEvidence("evidence-1", STAMP, {"status": "redacted"}),
        "created_at": STAMP,
    }
    values.update(changes)
    return QuarantineRecord(**values)


class MemoryRepository:
    def __init__(self):
        self.items = {}

    async def find_by_idempotency_key(self, *, tenant_id, idempotency_key):
        return self.items.get((tenant_id, idempotency_key))

    async def put_if_absent(self, record):
        return self.items.setdefault((record.partition.tenant_id, record.idempotency_key), record)


@pytest.mark.asyncio
async def test_duplicate_retry_is_idempotent_and_preserves_original_evidence():
    repository = MemoryRepository()
    service = DurableQuarantineService(repository)
    record = make_record()
    assert await service.record(record) is record
    retry = make_record(record_id="qr-retry", created_at=datetime(2026, 11, 2, tzinfo=UTC))
    assert await service.record(retry) is record
    assert len(repository.items) == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_with_changed_digest_is_conflict():
    repository = MemoryRepository()
    service = DurableQuarantineService(repository)
    await service.record(make_record())
    with pytest.raises(QuarantineDuplicateConflict, match="different"):
        await service.record(make_record(source_digest="sha256:" + "b" * 64))


def test_full_identity_and_tenant_partition_are_required():
    with pytest.raises(ValueError, match="tenant_id"):
        record = make_record()
        QuarantineRecord(
            record_id=record.record_id,
            idempotency_key=record.idempotency_key,
            partition=QuarantinePartition("tenant-2", "p"),
            identity=record.identity,
            reason=record.reason,
            source_digest=record.source_digest,
            canonical_digest=record.canonical_digest,
            binding_digest=record.binding_digest,
            promotion_lineage=record.promotion_lineage,
            correlation_id=record.correlation_id,
            trace_id=record.trace_id,
            span_id=record.span_id,
            evidence=record.evidence,
            created_at=record.created_at,
        )


def test_evidence_mapping_is_immutable_and_digests_are_canonical():
    evidence = ImmutableEvidence("e-1", STAMP, {"reason": "redacted"})
    with pytest.raises(TypeError):
        evidence.fields["new"] = "value"
    with pytest.raises(ValueError, match="sha256"):
        make_record(binding_digest="not-a-digest")


def test_zone_has_no_database_or_trusted_success_implementation():
    source = inspect.getsource(DurableQuarantineService).lower()
    assert "sqlalchemy" not in source
    assert "session.commit" not in source
    assert "authorize" not in source
    assert DurableQuarantineService.TRUSTED_SUCCESS is False
