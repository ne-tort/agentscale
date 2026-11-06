"""Source acceptance contracts for the full identity partition zone.

Migration verification freeze: tests are intentionally not executed here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.full_identity_partition import (
    FullIdentityPartitionDecision,
    FullIdentityPartitionError,
    FullIdentityPartitionResolver,
    canonical_full_identity_promotion_digest,
)
from prodavan.application.shared_identity import IdentityEnvelope

ARTIFACT = "sha256:" + "a" * 64


def envelope(**changes):
    values = {
        "tenant_id": "tenant-1",
        "cabinet_id": "cabinet-1",
        "project_id": "project-1",
        "scope_kind": "project",
        "scope_id": "project-1",
        "definition_id": "definition-1",
        "version_id": "version-1",
        "artifact_digest": ARTIFACT,
        "plugin_instance_id": "instance-1",
        "subject_id": "subject-1",
        "session_id": "session-1",
        "runtime_generation": 3,
        "writer_generation": 7,
        "lease_id": "lease-1",
        "lease_owner": "worker-1",
        "lease_until": datetime(2026, 11, 1, 12, tzinfo=UTC) + timedelta(minutes=5),
        "correlation_id": "corr-1",
        "trace_id": "trace-1",
    }
    values.update(changes)
    return IdentityEnvelope(**values)


def test_resolves_exact_shared_partition_and_recomputes_canonical_digest():
    identity = envelope()
    resolved = FullIdentityPartitionResolver.resolve(
        envelope=identity,
        partition=identity.promotion_partition,
    )

    assert resolved.accepted
    assert resolved.digest == canonical_full_identity_promotion_digest(
        identity, identity.promotion_partition
    )
    assert resolved.canonical_payload()["promotion_partition"]["plugin_instance_id"] == "instance-1"
    assert resolved.canonical_payload()["lease_snapshot"] == {
        "lease_id": "lease-1",
        "lease_owner": "worker-1",
        "lease_until": identity.lease_until.isoformat(),
    }


def test_complete_identity_and_partition_fields_are_digest_bound():
    identity = envelope()
    base = canonical_full_identity_promotion_digest(identity, identity.promotion_partition)
    changes = (
        {"tenant_id": "tenant-2"},
        {"cabinet_id": "cabinet-2", "project_id": "project-2", "scope_id": "project-2"},
        {"definition_id": "definition-2"},
        {"version_id": "version-2"},
        {"artifact_digest": "sha256:" + "b" * 64},
        {"plugin_instance_id": "instance-2"},
        {"runtime_generation": 4},
        {"writer_generation": 8},
        {"lease_id": "lease-2"},
        {"lease_owner": "worker-2"},
        {"lease_until": identity.lease_until + timedelta(seconds=1)},
        {"correlation_id": "corr-2"},
        {"trace_id": "trace-2"},
        {"subject_id": "subject-2"},
        {"session_id": "session-2"},
    )

    for change in changes:
        changed = envelope(**change)
        assert canonical_full_identity_promotion_digest(
            changed, changed.promotion_partition
        ) != base


def test_direct_decision_construction_rechecks_envelope_partition_and_payload_digest():
    identity = envelope()
    partition = identity.promotion_partition
    digest = canonical_full_identity_promotion_digest(identity, partition)
    decision = FullIdentityPartitionDecision(
        envelope=identity,
        partition=partition,
        digest=digest,
    )
    assert decision.digest == digest

    with pytest.raises(FullIdentityPartitionError, match="promotion partition"):
        FullIdentityPartitionDecision(
            envelope=identity,
            partition=envelope(session_id="other-session").promotion_partition,
            digest=digest,
        )

    with pytest.raises(FullIdentityPartitionError, match="canonical full identity payload"):
        FullIdentityPartitionDecision(
            envelope=identity,
            partition=partition,
            digest="sha256:" + "a" * 64,
        )

    object.__setattr__(identity, "writer_generation", 99)
    with pytest.raises(FullIdentityPartitionError, match="envelope digest"):
        FullIdentityPartitionDecision(
            envelope=identity,
            partition=identity.promotion_partition,
            digest=digest,
        )


def test_rejects_partition_mismatch_and_invalid_envelope_digest():
    identity = envelope()
    mismatched = envelope(session_id="another-session").promotion_partition
    with pytest.raises(FullIdentityPartitionError, match="does not match"):
        FullIdentityPartitionResolver.resolve(envelope=identity, partition=mismatched)

    object.__setattr__(identity, "writer_generation", 99)
    with pytest.raises(FullIdentityPartitionError, match="envelope digest"):
        FullIdentityPartitionResolver.resolve(
            envelope=identity,
            partition=identity.promotion_partition,
        )


def test_rejects_plugin_id_only_supplier_only_and_other_partial_shapes():
    identity = envelope()
    for partial in (
        {"plugin_id": "plugin-1"},
        {"supplier_id": "supplier-1"},
        {"tenant_id": "tenant-1", "plugin_instance_id": "instance-1"},
    ):
        with pytest.raises(FullIdentityPartitionError):
            FullIdentityPartitionResolver.resolve_mapping(
                envelope=identity,
                partition=partial,
            )


def test_mapping_must_be_exact_complete_shared_identity_partition():
    identity = envelope()
    complete = {
        "tenant_id": "tenant-1",
        "definition_id": "definition-1",
        "version_id": "version-1",
        "scope_kind": "project",
        "scope_id": "project-1",
        "plugin_instance_id": "instance-1",
        "subject_id": "subject-1",
        "session_id": "session-1",
    }
    result = FullIdentityPartitionResolver.resolve_mapping(
        envelope=identity,
        partition=complete,
    )
    assert result.partition == identity.promotion_partition

    with pytest.raises(FullIdentityPartitionError, match="unsupported"):
        FullIdentityPartitionResolver.resolve_mapping(
            envelope=identity,
            partition={**complete, "lease_id": "must-not-be-a-partition-field"},
        )


def test_supplied_digest_is_verified_and_must_be_lowercase_sha256():
    identity = envelope()
    canonical = canonical_full_identity_promotion_digest(identity, identity.promotion_partition)
    assert FullIdentityPartitionResolver.resolve(
        envelope=identity,
        partition=identity.promotion_partition,
        supplied_digest=canonical,
    ).supplied_digest == canonical

    with pytest.raises(FullIdentityPartitionError, match="conflicts"):
        FullIdentityPartitionResolver.resolve(
            envelope=identity,
            partition=identity.promotion_partition,
            supplied_digest="sha256:" + "b" * 64,
        )
    with pytest.raises(FullIdentityPartitionError, match="lowercase"):
        FullIdentityPartitionResolver.resolve(
            envelope=identity,
            partition=identity.promotion_partition,
            supplied_digest="sha256:" + "A" * 64,
        )


def test_payload_has_no_secret_or_propagation_headers():
    identity = envelope()
    payload = FullIdentityPartitionResolver.resolve(
        envelope=identity,
        partition=identity.promotion_partition,
    ).canonical_payload()
    serialized = str(payload).lower()
    assert "secret" not in serialized
    assert "token" not in serialized
    assert "x-prodavan-" not in serialized
    assert "lease_snapshot" in payload
