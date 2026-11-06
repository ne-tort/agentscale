"""MIG-AUTO-02 outbox intent, delivery fencing and partition ownership contract.

Adapters must bind source intent insertion, outbox insertion and any compatibility
projection update to the originating durable transaction. This module supplies no
in-memory or broker-only fallback and cannot prove external exactly-once effects.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


def _required(value: str, name: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _aware(value: datetime, name: str = "timestamp") -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _digest(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


@dataclass(frozen=True, slots=True)
class PartitionKey:
    tenant_id: str
    project_id: str
    automation_id: str

    def __post_init__(self) -> None:
        for field in ("tenant_id", "project_id", "automation_id"):
            _required(getattr(self, field), field)


@dataclass(frozen=True, slots=True)
class OutboxIntent:
    intent_id: str
    partition: PartitionKey
    source_run_id: str
    event_id: str
    destination: str
    idempotency_key: str
    payload: dict[str, Any]
    created_at: datetime
    payload_digest: str = ""

    def __post_init__(self) -> None:
        for field in ("intent_id", "source_run_id", "event_id", "destination", "idempotency_key"):
            _required(getattr(self, field), field)
        _aware(self.created_at, "created_at")
        expected = _digest(self.payload)
        if self.payload_digest and self.payload_digest != expected:
            raise ValueError("payload_digest does not match immutable payload")
        object.__setattr__(self, "payload_digest", expected)


class DeliveryState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class WriterLease:
    partition: PartitionKey
    holder: str
    epoch: int
    fencing_token: str
    expires_at: datetime

    def __post_init__(self) -> None:
        _required(self.holder, "holder")
        _required(self.fencing_token, "fencing_token")
        _aware(self.expires_at, "expires_at")
        if self.epoch < 1:
            raise ValueError("lease epoch must be positive")

    def valid_at(self, now: datetime) -> bool:
        _aware(now, "now")
        return self.expires_at > now


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    intent: OutboxIntent
    attempt: int
    lease: WriterLease
    claimed_at: datetime
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        _aware(self.claimed_at, "claimed_at")
        if self.attempt < 1 or self.lease.partition != self.intent.partition:
            raise ValueError("positive attempt and matching partition lease required")
        if self.retry_at is not None:
            _aware(self.retry_at, "retry_at")


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    intent_id: str
    attempt: int
    fencing_token: str
    state: DeliveryState
    completed_at: datetime
    next_retry_at: datetime | None = None
    receipt_id: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _required(self.intent_id, "intent_id")
        _required(self.fencing_token, "fencing_token")
        _aware(self.completed_at, "completed_at")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if self.next_retry_at is not None:
            _aware(self.next_retry_at, "next_retry_at")
        if self.state is DeliveryState.PENDING and self.next_retry_at is None:
            raise ValueError("retry outcome requires next_retry_at")
        if self.state is DeliveryState.DELIVERED and not self.receipt_id:
            raise ValueError("delivered outcome requires a receiver receipt")


@dataclass(frozen=True, slots=True)
class ProjectionUpdate:
    partition: PartitionKey
    intent_id: str
    source_run_id: str
    compatibility_key: str
    value: dict[str, Any]
    expected_revision: int
    fencing_token: str

    def __post_init__(self) -> None:
        for field in ("intent_id", "source_run_id", "compatibility_key", "fencing_token"):
            _required(getattr(self, field), field)
        if self.expected_revision < 0:
            raise ValueError("expected_revision cannot be negative")


class OwnershipGateVerifierPort(Protocol):
    async def verify(self, *, evidence: OwnershipGateEvidence) -> bool:
        """Verify immutable signed evidence against server-side acceptance state."""
        ...


class OutboxRepositoryPort(Protocol):
    async def append_intent(self, *, intent: OutboxIntent) -> bool:
        """Insert idempotently in the caller's transaction; false means identical replay.

        A reused identity with a different payload digest must raise a conflict.
        This operation must commit atomically with the originating run/state change.
        """
        ...

    async def claim_due(self, *, partition: PartitionKey, lease: WriterLease,
                        now: datetime, limit: int) -> tuple[DeliveryClaim, ...]:
        """Atomically claim due rows and increment attempts under a live fenced lease."""
        ...

    async def record_outcome(self, *, outcome: DeliveryOutcome, lease: WriterLease) -> bool:
        """CAS by intent, attempt and current lease token; stale completion is rejected."""
        ...

    async def apply_projection(self, *, update: ProjectionUpdate, lease: WriterLease) -> bool:
        """CAS compatibility row revision and fence; never make it a second source of truth."""
        ...

    async def load_ownership(self, partition: PartitionKey) -> PartitionOwnership | None: ...

    async def compare_and_set_ownership(self, *, expected_revision: int,
                                        ownership: PartitionOwnership,
                                        gate: OwnershipGateEvidence | None = None) -> bool:
        """CAS owner and append immutable evidence atomically; bind gate digest/signature and reject revoked or stale gate evidence."""
        ...


class AutomationOutboxService:
    """Small worker contract; dispatcher is expected to send with intent.idempotency_key."""

    def __init__(self, repository: OutboxRepositoryPort, *, max_batch_size: int = 100) -> None:
        if not 1 <= max_batch_size <= 1000:
            raise ValueError("max_batch_size must be between 1 and 1000")
        self._repository = repository
        self._max_batch_size = max_batch_size

    async def claim_due(self, *, partition: PartitionKey, lease: WriterLease,
                        now: datetime | None = None, limit: int = 100) -> tuple[DeliveryClaim, ...]:
        checked_at = now or datetime.now(UTC)
        _aware(checked_at, "now")
        if not lease.valid_at(checked_at) or lease.partition != partition:
            return ()
        if not 1 <= limit <= self._max_batch_size:
            raise ValueError("limit exceeds configured bounded batch size")
        claims = await self._repository.claim_due(partition=partition, lease=lease, now=checked_at, limit=limit)
        if any(claim.intent.partition != partition or claim.lease != lease or claim.claimed_at > checked_at
               for claim in claims):
            raise RuntimeError("repository returned a claim outside the requested fenced partition")
        return claims

    async def record_outcome(self, *, outcome: DeliveryOutcome, lease: WriterLease,
                             now: datetime | None = None) -> bool:
        checked_at = now or datetime.now(UTC)
        _aware(checked_at, "now")
        if (not lease.valid_at(checked_at) or outcome.fencing_token != lease.fencing_token
                or outcome.completed_at > checked_at):
            return False
        return await self._repository.record_outcome(outcome=outcome, lease=lease)


class OwnershipState(StrEnum):
    LEGACY_ACTIVE = "legacy_active"
    SHADOW = "shadow"
    CANONICAL_ACTIVE = "canonical_active"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class OwnershipGateEvidence:
    partition: PartitionKey
    evidence_id: str
    evidence_digest: str
    parity_digest: str
    final_checkpoint: str
    decision_owner: str
    signature_ref: str
    accepted: bool

    def __post_init__(self) -> None:
        for field in ("evidence_id", "evidence_digest", "parity_digest", "final_checkpoint",
                      "decision_owner", "signature_ref"):
            _required(getattr(self, field), field)
        if not self.evidence_digest.startswith("sha256:") or not self.parity_digest.startswith("sha256:"):
            raise ValueError("gate evidence digests must be sha256 digests")


@dataclass(frozen=True, slots=True)
class PartitionOwnership:
    partition: PartitionKey
    state: OwnershipState = OwnershipState.LEGACY_ACTIVE
    read_owner: str = "legacy"
    write_owner: str = "legacy"
    projection_read_only: bool = False
    epoch: int = 0
    fencing_token: str | None = None
    revision: int = 0
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        if self.revision < 0 or self.epoch < 0:
            raise ValueError("revision and epoch cannot be negative")


class PartitionOwnershipService:
    """Partition-local owner state machine; defaults stay on legacy without evidence."""

    def __init__(self, repository: OutboxRepositoryPort,
                 gate_verifier: OwnershipGateVerifierPort | None = None) -> None:
        self._repository = repository
        self._gate_verifier = gate_verifier

    async def transition(self, *, partition: PartitionKey, target: OwnershipState,
                         lease: WriterLease | None, evidence_id: str | None,
                         gate: OwnershipGateEvidence | None = None,
                         now: datetime | None = None) -> PartitionOwnership:
        checked_at = now or datetime.now(UTC)
        _aware(checked_at, "now")
        current = await self._repository.load_ownership(partition) or PartitionOwnership(partition)
        if current.partition != partition:
            raise RuntimeError("ownership repository partition mismatch")
        if target is OwnershipState.BLOCKED:
            resulting = PartitionOwnership(partition, OwnershipState.BLOCKED, current.read_owner,
                                           current.write_owner, current.projection_read_only,
                                           current.epoch, current.fencing_token, current.revision + 1,
                                           evidence_id)
        else:
            if lease is None or lease.partition != partition or not lease.valid_at(checked_at):
                raise ValueError("a live same-partition lease is required")
            if lease.epoch <= current.epoch or not evidence_id or not evidence_id.strip():
                raise ValueError("new epoch and immutable evidence id are required")
            allowed = {
                OwnershipState.LEGACY_ACTIVE: {OwnershipState.SHADOW},
                OwnershipState.SHADOW: {OwnershipState.CANONICAL_ACTIVE, OwnershipState.ROLLED_BACK},
                OwnershipState.CANONICAL_ACTIVE: {OwnershipState.ROLLED_BACK},
                OwnershipState.ROLLED_BACK: {OwnershipState.SHADOW},
                OwnershipState.BLOCKED: set(),
            }
            if target not in allowed[current.state]:
                raise ValueError(f"invalid ownership transition: {current.state.value}->{target.value}")
            if target is OwnershipState.CANONICAL_ACTIVE:
                if (gate is None or gate.partition != partition or not gate.accepted
                        or not evidence_id or gate.evidence_id != evidence_id
                        or self._gate_verifier is None
                        or not await self._gate_verifier.verify(evidence=gate)):
                    raise ValueError("canonical promotion requires server-verified partition-bound gate evidence")
            if current.state is OwnershipState.ROLLED_BACK and target is OwnershipState.SHADOW and lease.epoch <= current.epoch:
                raise ValueError("writer epoch must advance after rollback")
            read_owner = "canonical" if target in (OwnershipState.SHADOW, OwnershipState.CANONICAL_ACTIVE) else "legacy"
            write_owner = "canonical" if target is OwnershipState.CANONICAL_ACTIVE else "legacy"
            resulting = PartitionOwnership(partition, target, read_owner, write_owner,
                                           target is OwnershipState.SHADOW, lease.epoch,
                                           lease.fencing_token, current.revision + 1, evidence_id)
        if not await self._repository.compare_and_set_ownership(
            expected_revision=current.revision, ownership=resulting,
            gate=gate if target is OwnershipState.CANONICAL_ACTIVE else None,
        ):
            raise RuntimeError("ownership revision conflict; reconcile from durable evidence")
        return resulting


__all__ = [
    "AutomationOutboxService", "DeliveryClaim", "DeliveryOutcome", "DeliveryState",
    "OutboxIntent", "OutboxRepositoryPort", "OwnershipGateEvidence", "OwnershipGateVerifierPort",
    "OwnershipState", "PartitionKey", "PartitionOwnership", "PartitionOwnershipService",
    "ProjectionUpdate", "WriterLease",
]
