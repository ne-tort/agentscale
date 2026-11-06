"""Migration-stage project trigger context handoff contracts."""

from .contracts import (
    DeliveryIdentity,
    HandoffContext,
    IdempotencyClaim,
    OutboxIdentity,
    PartitionName,
    ProjectTriggerIntent,
    PromotionEvidence,
    TaskIdentity,
    WorkerFence,
)
from .ports import AutomationContextHandoffPort
from .service import AutomationContextHandoffService

__all__ = [
    "AutomationContextHandoffPort",
    "AutomationContextHandoffService",
    "DeliveryIdentity",
    "HandoffContext",
    "IdempotencyClaim",
    "OutboxIdentity",
    "PartitionName",
    "ProjectTriggerIntent",
    "PromotionEvidence",
    "TaskIdentity",
    "WorkerFence",
]
