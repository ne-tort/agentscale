"""Ports consumed by automation orchestration implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from prodavan.domain.automation.types import (
    AutomationDefinition,
    IdempotencyRecord,
    InboundWebhookEndpoint,
    OutboundDelivery,
    TaskRun,
)


class AutomationDefinitionRepository(Protocol):
    async def get_definition(self, automation_id: str) -> AutomationDefinition | None: ...


class WebhookEndpointRepository(Protocol):
    async def get_endpoint(self, endpoint_id: str) -> InboundWebhookEndpoint | None: ...

    async def get_by_slug(self, tenant_id: str, slug: str) -> InboundWebhookEndpoint | None: ...


class TaskRunRepository(Protocol):
    async def create_or_get_by_idempotency(self, run: TaskRun) -> TaskRun: ...

    async def save(self, run: TaskRun) -> TaskRun: ...

    async def claim(self, run_id: str, owner_id: str, lease_until: datetime) -> TaskRun | None: ...


class DeliveryRepository(Protocol):
    async def enqueue(self, delivery: OutboundDelivery) -> OutboundDelivery: ...

    async def claim_due(self, owner_id: str, now: datetime, limit: int) -> Sequence[OutboundDelivery]: ...

    async def save(self, delivery: OutboundDelivery) -> OutboundDelivery: ...


class IdempotencyRepository(Protocol):
    async def reserve(self, record: IdempotencyRecord) -> IdempotencyRecord | None: ...

    async def get(self, tenant_id: str, operation: str, key: str) -> IdempotencyRecord | None: ...


class AutomationClock(Protocol):
    def now(self) -> datetime: ...


class WebhookSignatureVerifier(Protocol):
    def verify(self, *, secret_ref: str, body: bytes, signature: str | None) -> bool: ...


__all__ = [
    "AutomationClock",
    "AutomationDefinitionRepository",
    "DeliveryRepository",
    "IdempotencyRepository",
    "TaskRunRepository",
    "WebhookEndpointRepository",
    "WebhookSignatureVerifier",
]
