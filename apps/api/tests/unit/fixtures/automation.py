"""Factories for automation control-plane contract tests."""

from datetime import UTC, datetime, timedelta
from typing import Any

from prodavan.domain.automation.types import (
    AutomationDefinition,
    DeliveryLease,
    IdempotencyRecord,
    InboundWebhookEndpoint,
    OutboundDelivery,
    TaskRun,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def automation_definition(**overrides: Any) -> AutomationDefinition:
    values: dict[str, Any] = {
        "id": "auto-1",
        "tenant_id": "tenant-1",
        "name": "Forward inbound event",
        "created_at": NOW,
        "updated_at": NOW,
        "trigger_type": "webhook.received",
        "action_type": "http.dispatch",
    }
    values.update(overrides)
    return AutomationDefinition(**values)


def webhook_endpoint(**overrides: Any) -> InboundWebhookEndpoint:
    values: dict[str, Any] = {
        "id": "endpoint-1",
        "tenant_id": "tenant-1",
        "automation_id": "auto-1",
        "slug": "orders-created",
        "secret_ref": "secrets/automation/endpoint-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return InboundWebhookEndpoint(**values)


def task_run(**overrides: Any) -> TaskRun:
    values: dict[str, Any] = {
        "id": "run-1",
        "tenant_id": "tenant-1",
        "automation_id": "auto-1",
        "source_event_id": "evt-1",
        "idempotency_key": "evt-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return TaskRun(**values)


def idempotency_record(**overrides: Any) -> IdempotencyRecord:
    values: dict[str, Any] = {
        "key": "request-1",
        "tenant_id": "tenant-1",
        "operation": "automation.trigger",
        "request_hash": "sha256:request",
        "task_run_id": "run-1",
        "expires_at": NOW + timedelta(hours=1),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return IdempotencyRecord(**values)


def outbound_delivery(**overrides: Any) -> OutboundDelivery:
    values: dict[str, Any] = {
        "id": "delivery-1",
        "tenant_id": "tenant-1",
        "task_run_id": "run-1",
        "destination_url": "https://example.test/hooks/automation",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return OutboundDelivery(**values)


def active_delivery_lease(**overrides: Any) -> DeliveryLease:
    values: dict[str, Any] = {
        "resource_id": "delivery-1",
        "owner_id": "worker-1",
        "acquired_at": NOW,
        "expires_at": NOW + timedelta(minutes=2),
    }
    values.update(overrides)
    return DeliveryLease(**values)


__all__ = [
    "NOW",
    "active_delivery_lease",
    "automation_definition",
    "idempotency_record",
    "outbound_delivery",
    "task_run",
    "webhook_endpoint",
]
