"""MIG-AUTO-02 acceptance declarations; deliberately not executed."""

from .acceptance_fixtures import CASES, DELIVERED, GATE_EVIDENCE, INITIAL_OWNERSHIP, INTENT, LEASE, PARTITION, PROJECTION
from prodavan.application.migration.automation_outbox.outbox import (
    AutomationOutboxService,
    DeliveryClaim,
    DeliveryState,
    OutboxIntent,
    OutboxRepositoryPort,
    OwnershipGateEvidence,
    OwnershipGateVerifierPort,
    OwnershipState,
    PartitionOwnershipService,
)


def test_acceptance_fixture_shapes_and_required_scenarios() -> None:
    assert len(CASES) >= 20
    assert INTENT.partition == PARTITION
    assert INTENT.payload_digest.startswith("sha256:")
    assert LEASE.partition == PARTITION
    assert DELIVERED.state is DeliveryState.DELIVERED
    assert PROJECTION.partition == PARTITION
    assert INITIAL_OWNERSHIP.state is OwnershipState.LEGACY_ACTIVE
    assert GATE_EVIDENCE.partition == PARTITION
    assert GATE_EVIDENCE.evidence_digest.startswith("sha256:")
    assert GATE_EVIDENCE.parity_digest.startswith("sha256:")
    assert GATE_EVIDENCE.final_checkpoint
    assert GATE_EVIDENCE.decision_owner and GATE_EVIDENCE.signature_ref
    assert OwnershipGateEvidence.__name__ == "OwnershipGateEvidence"
    assert OwnershipGateVerifierPort.__name__ == "OwnershipGateVerifierPort"
    assert DeliveryClaim.__name__ == "DeliveryClaim"
    assert OutboxIntent.__name__ == "OutboxIntent"
    assert OutboxRepositoryPort.__name__ == "OutboxRepositoryPort"
    assert AutomationOutboxService.__name__ == "AutomationOutboxService"
    assert PartitionOwnershipService.__name__ == "PartitionOwnershipService"
