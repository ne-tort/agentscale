"""Exclusive target persistence boundary for automation intent and delivery ownership."""

from .contracts import (
    AutomationRecord,
    CanonicalAutomationEnvelope,
    Conflict,
    DeliveryCasResult,
    DeliveryClaim,
    DurableError,
    IdempotencyClaim,
    LeaseToken,
    OutboxCasResult,
    RetryCommand,
    StaleCasOutcome,
    TaskCasResult,
    TaskClaim,
)
from .ports import (
    AutomationIntentRepository,
    AutomationPersistencePort,
    DeliveryLeaseRepository,
    DeliveryRepository,
    IdempotencyRepository,
    OperationNowPort,
    OutboxPersistencePort,
    OutboxRepository,
    ProjectTriggersPort,
    TaskRunRepository,
)
from .service import AutomationPersistenceBoundary, AutomationPersistenceService, BlockedAutomationPersistenceAdapter

__all__ = [
    "AutomationIntentRepository", "AutomationPersistenceBoundary", "AutomationPersistencePort", "AutomationPersistenceService",
    "AutomationRecord", "BlockedAutomationPersistenceAdapter", "CanonicalAutomationEnvelope", "Conflict", "DeliveryCasResult", "DeliveryClaim", "DurableError", "DeliveryLeaseRepository", "DeliveryRepository",
    "IdempotencyClaim", "IdempotencyRepository", "LeaseToken", "OperationNowPort", "OutboxCasResult", "OutboxPersistencePort", "OutboxRepository",
    "ProjectTriggersPort", "RetryCommand", "StaleCasOutcome", "TaskCasResult", "TaskClaim", "TaskRunRepository",
]
