"""Source acceptance contracts for the exclusive identity handoff adapter.

Migration verification is intentionally frozen; this file is not executed here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.identity_handoff_adapter import (
    AuditEvidenceLink,
    IdentityHandoffAdapter,
)
from prodavan.application.shared_identity import IdentityEnvelope

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64
PARENT = "sha256:" + "b" * 64


def envelope(**changes: object) -> IdentityEnvelope:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "scope_kind": "project",
        "scope_id": "project-1",
        "definition_id": "definition-1",
        "version_id": "version-1",
        "artifact_digest": DIGEST,
        "plugin_instance_id": "instance-1",
        "subject_id": "subject-1",
        "session_id": "session-1",
        "runtime_generation": 4,
        "writer_generation": 9,
        "lease_id": "lease-secret-id",
        "lease_owner": "worker-secret",
        "lease_until": NOW + timedelta(hours=1),
        "cabinet_id": "cabinet-1",
        "project_id": "project-1",
        "correlation_id": "corr-1",
        "trace_id": "trace-1",
    }
    values.update(changes)
    return IdentityEnvelope(**values)


def link(identity: IdentityEnvelope, **changes: object) -> AuditEvidenceLink:
    values: dict[str, object] = {
        "envelope_digest": identity.envelope_digest,
        "evidence_digest": DIGEST,
        "parent_evidence_digest": PARENT,
        "correlation_id": identity.correlation_id,
        "trace_id": identity.trace_id,
        "promotion_partition_digest": DIGEST,
    }
    values.update(changes)
    return AuditEvidenceLink(**values)


class M05:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.seen = []

    async def admit(self, context) -> bool:
        self.seen.append(context)
        return self.allowed


class M06:
    def __init__(self) -> None:
        self.seen = []

    async def handoff(self, context, evidence) -> None:
        self.seen.append((context, evidence))


@pytest.mark.asyncio
async def test_bridge_passes_exact_derived_context_from_envelope_to_m05_and_m06() -> None:
    identity = envelope()
    m05, m06 = M05(), M06()
    adapter = IdentityHandoffAdapter(m05=m05, m06=m06, clock=lambda: NOW)

    result = await adapter.bridge(envelope=identity, evidence=link(identity))

    assert result.envelope is identity
    assert m05.seen == [result.m05]
    assert m06.seen == [(result.m06, result.evidence)]
    for field in (
        "tenant_id", "cabinet_id", "project_id", "scope_kind", "scope_id",
        "plugin_instance_id", "definition_id", "version_id", "artifact_digest",
        "subject_id", "session_id", "runtime_generation", "writer_generation",
        "lease_owner", "lease_id",
    ):
        assert getattr(result.installation, field) == getattr(identity, field)
    assert result.installation.observed_at == NOW


@pytest.mark.asyncio
async def test_propagation_headers_exclude_lease_credentials() -> None:
    identity = envelope()
    adapter = IdentityHandoffAdapter(m05=M05(), clock=lambda: NOW)

    handoff = await adapter.bridge(envelope=identity, evidence=link(identity))
    headers = adapter.propagation_headers(handoff)

    assert set(headers) == {
        "x-prodavan-identity-digest", "x-prodavan-correlation-id",
        "x-prodavan-trace-id", "x-prodavan-identity-hop",
    }
    assert "lease-secret-id" not in headers.values()
    assert "worker-secret" not in headers.values()


@pytest.mark.asyncio
async def test_partial_or_mismatched_evidence_is_rejected_before_m05() -> None:
    identity = envelope()
    m05 = M05()
    adapter = IdentityHandoffAdapter(m05=m05, clock=lambda: NOW)

    with pytest.raises(ValueError, match="envelope digest"):
        await adapter.bridge(
            envelope=identity, evidence=link(identity, envelope_digest=DIGEST)
        )
    assert m05.seen == []


@pytest.mark.asyncio
async def test_expired_lease_is_rejected_before_m05() -> None:
    identity = envelope(lease_until=NOW)
    m05 = M05()
    adapter = IdentityHandoffAdapter(m05=m05, clock=lambda: NOW)

    with pytest.raises(ValueError, match="future"):
        await adapter.bridge(envelope=identity, evidence=link(identity))
    assert m05.seen == []


@pytest.mark.asyncio
async def test_naive_adapter_clock_is_rejected_before_m05() -> None:
    identity = envelope()
    m05 = M05()
    adapter = IdentityHandoffAdapter(m05=m05, clock=lambda: NOW.replace(tzinfo=None))

    with pytest.raises(ValueError, match="timezone-aware"):
        await adapter.bridge(envelope=identity, evidence=link(identity))
    assert m05.seen == []


@pytest.mark.asyncio
async def test_m05_denial_prevents_m06_handoff() -> None:
    identity = envelope()
    m06 = M06()
    adapter = IdentityHandoffAdapter(m05=M05(False), m06=m06, clock=lambda: NOW)

    with pytest.raises(ValueError, match="M05 CAS"):
        await adapter.bridge(envelope=identity, evidence=link(identity))
    assert m06.seen == []


@pytest.mark.asyncio
async def test_m05_admission_precedes_m06_and_observed_at_uses_one_clock_snapshot() -> None:
    identity = envelope()
    events: list[str] = []
    clock_values = iter((NOW, NOW + timedelta(seconds=1)))

    class OrderedM05(M05):
        async def admit(self, context):
            events.append("m05")
            return await super().admit(context)

    class OrderedM06(M06):
        async def handoff(self, context, evidence):
            events.append("m06")
            await super().handoff(context, evidence)

    adapter = IdentityHandoffAdapter(
        m05=OrderedM05(), m06=OrderedM06(), clock=lambda: next(clock_values)
    )
    result = await adapter.bridge(envelope=identity, evidence=link(identity))

    assert events == ["m05", "m06"]
    assert result.installation.observed_at == NOW


def test_evidence_link_rejects_parent_equal_to_evidence_digest() -> None:
    identity = envelope()
    with pytest.raises(ValueError, match="parent evidence digest"):
        link(identity, parent_evidence_digest=DIGEST)


def test_evidence_link_requires_parent_and_partition_digests() -> None:
    identity = envelope()
    with pytest.raises(ValueError, match="parent_evidence_digest"):
        AuditEvidenceLink(
            envelope_digest=identity.envelope_digest,
            evidence_digest=DIGEST,
            parent_evidence_digest="missing",
            correlation_id=identity.correlation_id,
            trace_id=identity.trace_id,
            promotion_partition_digest=DIGEST,
        )
