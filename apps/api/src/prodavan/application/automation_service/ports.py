"""Stable port imports for automation adapters."""

from prodavan.application.automation_service.contracts import (
    AutomationRepositoryPort,
    ClockPort,
    DeliveryEgressPolicyPort,
    DeliveryEgressPolicyResult,
    DeliveryTransportPort,
    SignatureVerifierPort,
    TaskQueuePort,
)

__all__ = [
    "AutomationRepositoryPort",
    "ClockPort",
    "DeliveryEgressPolicyPort",
    "DeliveryEgressPolicyResult",
    "DeliveryTransportPort",
    "SignatureVerifierPort",
    "TaskQueuePort",
]
