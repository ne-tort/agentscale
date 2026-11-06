"""Persistence-boundary contracts for target automation ownership."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from copy import deepcopy
from typing import Any, Mapping, TypeAlias
from secrets import token_urlsafe

from prodavan.application.automation_service.contracts import (
    DeliverySchedule,
    TaskRun,
    WebhookIngress,
    require_aware,
)
from prodavan.domain.automation.outbox import OutboxEnqueueIntent, OutboxEvent, OutboxStateRecord


@dataclass(frozen=True, slots=True)
class CanonicalAutomationEnvelope:
    """Immutable ingress identity used for tenant-scoped idempotency."""

    tenant_id: str
    subscription_id: str
    source_event_id: str
    source: str
    task_name: str
    body: bytes
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("tenant_id", "subscription_id", "source_event_id", "source", "task_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")
        # Detach the caller-owned mapping before hashing or persistence.
        object.__setattr__(self, "payload", deepcopy(_canonical(self.payload)))

    @property
    def request_hash(self) -> str:
        canonical = {
            "schema": "automation.request.v1",
            "tenant_id": self.tenant_id.strip(),
            "subscription_id": self.subscription_id.strip(),
            "source_event_id": self.source_event_id.strip(),
            "source": self.source.strip(),
            "task_name": self.task_name.strip(),
            "body_sha256": sha256(self.body).hexdigest(),
            "payload": _canonical(self.payload),
        }
        encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        return sha256(encoded).hexdigest()

    @property
    def idempotency_key(self) -> str:
        parts = (self.tenant_id, self.subscription_id, self.source, self.source_event_id)
        raw = ":".join(f"{len(part)}={part}" for part in parts)
        return sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def from_ingress(
        cls, *, ingress: WebhookIngress, tenant_id: str, subscription_id: str, task_name: str, payload: Mapping[str, Any]
    ) -> "CanonicalAutomationEnvelope":
        return cls(tenant_id=tenant_id, subscription_id=subscription_id, source_event_id=ingress.event_id, source=ingress.source, task_name=task_name, body=ingress.body, payload=payload)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    key: str
    request_hash: str
    operation: str
    tenant_id: str
    subscription_id: str
    source_event_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.key, self.request_hash, self.operation, self.tenant_id, self.subscription_id, self.source_event_id)):
            raise ValueError("idempotency identity, request hash, and operation are required")


@dataclass(frozen=True, slots=True)
class LeaseToken:
    """Opaque fencing credential returned by a durable claim.

    The database must compare owner, generation, token, status, and expiry in
    the same UPDATE transaction for every lifecycle transition. A token is
    intentionally never derived from mutable task data.
    """

    owner_id: str
    generation: int
    token: str
    lease_until: datetime

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("owner_id is required")
        if self.generation < 1:
            raise ValueError("generation must be positive")
        if not self.token.strip() or len(self.token) < 16:
            raise ValueError("opaque lease token is required")
        require_aware(self.lease_until, name="lease_until")

    @property
    def owner(self) -> str:
        return self.owner_id

    @property
    def opaque(self) -> str:
        return self.token

    @property
    def expiry(self) -> datetime:
        return self.lease_until

    @classmethod
    def issue(cls, *, owner_id: str, generation: int, lease_until: datetime) -> "LeaseToken":
        return cls(owner_id=owner_id, generation=generation, token=token_urlsafe(32), lease_until=lease_until)


@dataclass(frozen=True, slots=True)
class Conflict:
    """A typed CAS/idempotency conflict that must remain visible to callers."""

    reason: str = "conflicting_existing_state"


@dataclass(frozen=True, slots=True)
class StaleCasOutcome:
    applied: bool
    reason: str = "stale_owner_generation_or_token"

    def __post_init__(self) -> None:
        if self.applied:
            raise ValueError("stale CAS outcome cannot be applied")


TaskCasResult: TypeAlias = TaskRun | Conflict | StaleCasOutcome
DeliveryCasResult: TypeAlias = DeliverySchedule | Conflict | StaleCasOutcome
OutboxCasResult: TypeAlias = OutboxStateRecord | Conflict | StaleCasOutcome


@dataclass(frozen=True, slots=True)
class TaskClaim:
    run: TaskRun
    lease: LeaseToken


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    delivery: DeliverySchedule
    lease: LeaseToken


@dataclass(frozen=True, slots=True)
class AutomationRecord:
    """Atomic record containing immutable event, initial state, and enqueue intent."""

    envelope: CanonicalAutomationEnvelope
    task_name: str
    payload: Mapping[str, Any]
    event: OutboxEvent
    initial_state: OutboxStateRecord
    enqueue_intent: OutboxEnqueueIntent

    def __post_init__(self) -> None:
        if not self.task_name.strip():
            raise ValueError("task_name is required")
        if self.task_name.strip() != self.envelope.task_name:
            raise ValueError("record task_name must match canonical envelope")
        if self.payload != self.envelope.payload:
            raise ValueError("record payload must match canonical envelope")
        if self.event.event_id != self.initial_state.event_id or self.event.event_id != self.enqueue_intent.event_id:
            raise ValueError("event, initial state, and enqueue intent must reference the same event")
        if self.enqueue_intent.deduplication_key != self.event.idempotency_key:
            raise ValueError("enqueue intent must use the event idempotency key")
        if self.event.tenant_id != self.envelope.tenant_id:
            raise ValueError("event tenant must match canonical envelope")

    @property
    def idempotency_key(self) -> str:
        return self.envelope.idempotency_key

    @property
    def request_hash(self) -> str:
        return self.envelope.request_hash


@dataclass(frozen=True, slots=True)
class RetryCommand:
    error: str

    def __post_init__(self) -> None:
        if not self.error.strip():
            raise ValueError("error is required")
        if len(self.error) > 256 or "\n" in self.error or "\r" in self.error:
            raise ValueError("error must be a bounded single-line safe code")


@dataclass(frozen=True, slots=True)
class DurableError:
    """Safe durable error: stable class/code and digest, never raw transport text."""

    code: str
    digest: str

    def __post_init__(self) -> None:
        if not self.code.startswith("automation.error."):
            raise ValueError("code must use the automation.error namespace")
        if len(self.code) > 128 or len(self.digest) != 64:
            raise ValueError("durable error fields have invalid size")


__all__ = ["AutomationRecord", "DurableError", "CanonicalAutomationEnvelope", "Conflict", "DeliveryCasResult", "DeliveryClaim", "IdempotencyClaim", "LeaseToken", "OutboxCasResult", "RetryCommand", "StaleCasOutcome", "TaskCasResult", "TaskClaim"]
