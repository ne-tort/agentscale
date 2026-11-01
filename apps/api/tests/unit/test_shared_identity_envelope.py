"""Source-level acceptance tests for the M04-M06 shared identity zone.

Migration verification freeze: this file is intentionally not executed here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.shared_identity import (
    IdentityContext,
    IdentityEnvelope,
    IdentityEnvelopeError,
    PromotionPartition,
    QuarantineClassification,
    QuarantineEvidence,
)

DIGEST = "sha256:" + "a" * 64


def envelope(**changes):
    values = {
        "tenant_id": "tenant-1",
        "cabinet_id": "cabinet-1",
        "project_id": "project-1",
        "scope_kind": "project",
        "scope_id": "project-1",
        "definition_id": "definition-1",
        "version_id": "version-1",
        "artifact_digest": DIGEST,
        "plugin_instance_id": "instance-1",
        "subject_id": "subject-1",
        "session_id": "session-1",
        "runtime_generation": 3,
        "writer_generation": 7,
        "lease_id": "lease-1",
        "lease_owner": "worker-1",
        "lease_until": datetime.now(UTC) + timedelta(minutes=5),
        "correlation_id": "corr-1",
        "trace_id": "trace-1",
    }
    values.update(changes)
    return IdentityEnvelope(**values)


def test_digest_is_canonical_and_immutable():
    value = envelope()
    assert value.envelope_digest == value.compute_digest()
    assert value.envelope_digest.startswith("sha256:")
    with pytest.raises((AttributeError, TypeError)):
        value.writer_generation = 8


def test_tampered_digest_is_rejected():
    with pytest.raises(IdentityEnvelopeError, match="envelope_digest"):
        envelope(envelope_digest="sha256:" + "b" * 64)


def test_non_hex_source_digest_is_rejected():
    with pytest.raises(IdentityEnvelopeError, match="artifact_digest"):
        envelope(artifact_digest="sha256:" + "g" * 64)


def test_conflicting_promotion_partition_is_rejected():
    partition = PromotionPartition(
        tenant_id="tenant-1",
        definition_id="definition-1",
        version_id="version-1",
        scope_kind="project",
        scope_id="other-project",
        plugin_instance_id="instance-1",
        subject_id="subject-1",
        session_id="session-1",
    )
    with pytest.raises(IdentityEnvelopeError, match="promotion_partition"):
        envelope(promotion_partition=partition)


def test_plugin_scope_requires_exact_instance_identity():
    with pytest.raises(IdentityEnvelopeError):
        envelope(scope_kind="plugin_instance", scope_id="wrong", project_id="project-1", cabinet_id="cabinet-1")


def test_context_propagation_excludes_runtime_lease_details():
    context = IdentityContext(envelope=envelope(), hop="m04")
    headers = context.propagation_headers()
    assert headers["x-prodavan-identity-digest"] == context.envelope.envelope_digest
    assert headers["x-prodavan-correlation-id"] == "corr-1"
    assert "lease_id" not in headers
    assert "lease_owner" not in headers


def test_quarantine_is_typed_redacted_and_fail_closed():
    record = QuarantineEvidence(
        envelope=envelope(),
        classification=QuarantineClassification.CROSS_TENANT,
        reason_code="tenant_anchor_mismatch",
        source_kind="legacy_grant",
        source_id="source-1",
        source_digest=DIGEST,
        checkpoint="m04.2",
        observed_at=datetime.now(UTC),
        metadata={"field": "tenant_id"},
    )
    assert record.classification is QuarantineClassification.CROSS_TENANT
    assert record.metadata["field"] == "tenant_id"
    assert record.envelope.envelope_digest == record.envelope.compute_digest()
