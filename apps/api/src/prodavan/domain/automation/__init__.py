"""Automation control-plane domain contracts."""

from prodavan.domain.automation.outbox import (
    OutboxEnqueueIntent,
    OutboxEvent,
    OutboxState,
    OutboxStateRecord,
)
from prodavan.domain.automation.types import (
    AutomationDefinition,
    AutomationStatus,
    DeliveryAttempt,
    DeliveryLease,
    DeliveryState,
    IdempotencyRecord,
    IdempotencyState,
    InboundWebhookEndpoint,
    LeaseState,
    OutboundDelivery,
    RetryPolicy,
    TaskRun,
    TaskRunState,
)

__all__ = [
    "OutboxEnqueueIntent",
    "OutboxEvent",
    "OutboxState",
    "OutboxStateRecord",
    "AutomationDefinition",
    "AutomationStatus",
    "DeliveryAttempt",
    "DeliveryLease",
    "DeliveryState",
    "IdempotencyRecord",
    "IdempotencyState",
    "InboundWebhookEndpoint",
    "LeaseState",
    "OutboundDelivery",
    "RetryPolicy",
    "TaskRun",
    "TaskRunState",
]
