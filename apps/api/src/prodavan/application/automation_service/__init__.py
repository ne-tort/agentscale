"""Application contracts for automation ingress, task runs, and deliveries."""

from prodavan.application.automation_service.contracts import (
    DeliveryEgressPolicyPort,
    DeliveryEgressPolicyResult,
    DurableError,
    LeaseToken,
    durable_error,
)
from prodavan.application.automation_service.outbox import (
    OutboxDispatcher,
    OutboxRepository,
    OutboxWorker,
)
from prodavan.application.automation_service.service import (
    AutomationService,
    DeliverySchedule,
    DeliveryStatus,
    IngressResult,
    TaskRun,
    TaskRunStatus,
    WebhookIngress,
)

__all__ = [
    "OutboxDispatcher",
    "OutboxRepository",
    "OutboxWorker",
    "AutomationService",
    "DeliveryEgressPolicyPort",
    "DeliveryEgressPolicyResult",
    "DurableError",
    "LeaseToken",
    "durable_error",
    "DeliverySchedule",
    "DeliveryStatus",
    "IngressResult",
    "TaskRun",
    "TaskRunStatus",
    "WebhookIngress",
]
