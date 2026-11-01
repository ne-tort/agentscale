"""MIG-AUTO-02 transactional outbox contracts; adapters are not supplied."""

from .outbox import (
    AutomationOutboxService,
    DeliveryClaim,
    DeliveryOutcome,
    DeliveryState,
    OutboxIntent,
    OutboxRepositoryPort,
    OwnershipGateEvidence,
    OwnershipGateVerifierPort,
    PartitionKey,
    PartitionOwnership,
    PartitionOwnershipService,
    ProjectionUpdate,
    WriterLease,
)

__all__ = [
    "AutomationOutboxService", "DeliveryClaim", "DeliveryOutcome", "DeliveryState",
    "OutboxIntent", "OutboxRepositoryPort", "OwnershipGateEvidence", "OwnershipGateVerifierPort",
    "PartitionKey", "PartitionOwnership", "PartitionOwnershipService", "ProjectionUpdate", "WriterLease",
]
