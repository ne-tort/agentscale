"""Ports and value objects for automation application workflows.

The contracts deliberately avoid persistence, transport, and queue dependencies. Concrete
adapters can implement these protocols for SQL, a broker, or an in-memory test harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from prodavan.application.automation_persistence.contracts import (
        DeliveryCasResult,
        DeliveryClaim,
        DurableError,
        LeaseToken,
        TaskCasResult,
        TaskClaim,
    )


def __getattr__(name: str) -> Any:
    """Lazily re-export persistence-owned value objects without an import cycle."""
    if name in {"DurableError", "LeaseToken"}:
        from prodavan.application.automation_persistence import contracts as persistence_contracts

        return getattr(persistence_contracts, name)
    raise AttributeError(name)


def durable_error(exc: BaseException, *, code: str = "delivery_failed") -> "DurableError":
    """Build the persistence-owned safe error identity without raw exception text."""
    error_class = f"{exc.__class__.__module__}.{exc.__class__.__qualname__}"
    safe_code = code if code.startswith("automation.error.") else f"automation.error.{code}"
    return DurableError(code=safe_code, digest=sha256(error_class.encode("utf-8")).hexdigest())


class TaskRunStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    SCHEDULED = "scheduled"
    LEASED = "leased"
    SENT = "sent"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WebhookIngress:
    source: str
    event_id: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    received_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.event_id.strip():
            raise ValueError("source and event_id must not be empty")
        if self.received_at is not None:
            require_aware(self.received_at, name="received_at")


@dataclass(frozen=True, slots=True)
class IngressResult:
    accepted: bool
    duplicate: bool = False
    task_id: str | None = None


@dataclass(slots=True)
class TaskRun:
    id: str
    task_name: str
    payload: dict[str, Any]
    status: TaskRunStatus = TaskRunStatus.QUEUED
    attempts: int = 0
    available_at: datetime | None = None
    lease_until: datetime | None = None
    leased_by: str | None = None
    lease_generation: int | None = None
    lease_token: str | None = None
    last_error: DurableError | None = None


@dataclass(slots=True)
class DeliverySchedule:
    id: str
    destination: str
    payload: dict[str, Any]
    available_at: datetime
    status: DeliveryStatus = DeliveryStatus.SCHEDULED
    attempts: int = 0
    lease_until: datetime | None = None
    leased_by: str | None = None
    lease_generation: int | None = None
    lease_token: str | None = None
    last_error: DurableError | None = None


class SignatureVerifierPort(Protocol):
    def verify(self, *, body: bytes, headers: dict[str, str], secret_ref: str) -> bool: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class AutomationRepositoryPort(Protocol):
    """Service-facing fenced repository contract.

    Claims carry the opaque lease token and generation required by every CAS
    transition. Implementations must fail closed when no durable adapter exists.
    """

    async def record_and_enqueue(
        self, *, ingress: WebhookIngress, task_name: str, payload: dict[str, Any]
    ) -> tuple[bool, TaskRun | None]: ...

    async def claim_task(
        self, *, task_id: str, worker_id: str, now: datetime, lease_until: datetime
    ) -> "TaskClaim | None": ...

    async def start_task(self, *, claim: "TaskClaim", started_at: datetime) -> "TaskCasResult": ...
    async def complete_task(self, *, claim: "TaskClaim", completed_at: datetime) -> "TaskCasResult": ...
    async def retry_task(self, *, claim: "TaskClaim", error: "DurableError", now: datetime) -> "TaskCasResult": ...
    async def dead_letter_task(self, *, claim: "TaskClaim", error: "DurableError", failed_at: datetime) -> "TaskCasResult": ...

    async def schedule_delivery(self, *, delivery: DeliverySchedule) -> None: ...
    async def claim_delivery(
        self, *, delivery_id: str, worker_id: str, now: datetime, lease_until: datetime
    ) -> "DeliveryClaim | None": ...
    async def complete_delivery(self, *, claim: "DeliveryClaim", delivered_at: datetime) -> "DeliveryCasResult": ...
    async def retry_delivery(self, *, claim: "DeliveryClaim", error: "DurableError", now: datetime) -> "DeliveryCasResult": ...
    async def dead_letter_delivery(self, *, claim: "DeliveryClaim", error: "DurableError", dead_lettered_at: datetime) -> "DeliveryCasResult": ...


class TaskQueuePort(Protocol):
    async def enqueue(self, *, task_id: str, idempotency_key: str) -> None:
        """Enqueue durably; repeated calls with the key must be harmless."""
        ...


@dataclass(frozen=True, slots=True)
class DeliveryEgressPolicyResult:
    """Evidence produced by an adapter immediately before delivery."""

    allowed: bool
    validated_http_scheme: bool
    credentials_absent: bool
    allowed_port: bool
    resolved_ip_global_nonreserved: bool
    redirects_checked_per_hop: bool
    tls_verified: bool

    @property
    def is_allowed(self) -> bool:
        """Require the adapter's decision and every mandatory proof."""
        return self.allowed and all(
            (
                self.validated_http_scheme,
                self.credentials_absent,
                self.allowed_port,
                self.resolved_ip_global_nonreserved,
                self.redirects_checked_per_hop,
                self.tls_verified,
            )
        )


class DeliveryEgressPolicyPort(Protocol):
    def check(self, *, destination: str) -> DeliveryEgressPolicyResult:
        """Validate the complete destination egress policy before dispatch."""
        ...


class DeliveryTransportPort(Protocol):
    async def deliver(
        self,
        *,
        destination: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        """Deliver with a stable key; retries must preserve that key."""
        ...


def task_idempotency_key(*, ingress: WebhookIngress) -> str:
    """Derive an injective, tenant-independent key for queue redrives."""
    source = ingress.source.strip()
    event_id = ingress.event_id.strip()
    canonical = f"{len(source)}:{source}:{len(event_id)}:{event_id}"
    return sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "AutomationRepositoryPort",
    "DurableError",
    "LeaseToken",
    "DurableError",
    "LeaseToken",
    "durable_error",
    "ClockPort",
    "DeliverySchedule",
    "DeliveryStatus",
    "DeliveryEgressPolicyPort",
    "DeliveryEgressPolicyResult",
    "DeliveryTransportPort",
    "IngressResult",
    "SignatureVerifierPort",
    "TaskQueuePort",
    "TaskRun",
    "TaskRunStatus",
    "WebhookIngress",
    "require_aware",
    "task_idempotency_key",
]


def require_aware(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value
