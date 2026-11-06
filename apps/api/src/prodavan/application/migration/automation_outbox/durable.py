"""Fail-closed application contracts and worker-port bindings for MIG-AUTO-02.

This module provides no concrete database adapter, HTTP client, or network fallback.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .outbox import PartitionKey, WriterLease


class OutboxBlockedError(RuntimeError):
    """Required trusted scope, durable evidence, or fencing proof is absent."""


class OutboxIdempotencyConflict(RuntimeError):
    """A stable identity was reused for a different immutable request."""


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def canonical_digest(value: Any) -> str:
    body = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _valid_digest(value: str, name: str) -> None:
    if len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError(f"{name} must be a sha256 digest")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a sha256 digest") from exc


@dataclass(frozen=True, slots=True)
class ResolvedScopeActorEvidence:
    """Server-resolved scope and authenticated actor evidence for one command."""

    partition: PartitionKey
    scope_id: str
    actor_id: str
    scope_generation: int
    evidence_id: str
    evidence_digest: str
    resolved_at: datetime

    def __post_init__(self) -> None:
        for name in ("scope_id", "actor_id", "evidence_id"):
            _required(getattr(self, name), name)
        if self.scope_generation < 1:
            raise ValueError("scope_generation must be positive")
        _valid_digest(self.evidence_digest, "evidence_digest")
        _aware(self.resolved_at, "resolved_at")


class ScopeActorVerifierPort(Protocol):
    async def verify(self, *, evidence: ResolvedScopeActorEvidence) -> bool:
        """Verify active typed scope and authenticated actor server-side."""
        ...


REQUIRED_ADAPTER_CAPABILITIES = frozenset({
    "durable_transaction",
    "atomic_business_event_enqueue",
    "unique_idempotency_with_hash_conflict",
    "lease_generation_token_cas",
    "append_only_ownership_evidence",
    "upstream_run_event_approval_linkage",
})


@dataclass(frozen=True, slots=True)
class DurableAdapterEvidence:
    adapter_id: str
    transaction_id: str
    evidence_id: str
    evidence_digest: str
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        for name in ("adapter_id", "transaction_id", "evidence_id"):
            _required(getattr(self, name), name)
        _valid_digest(self.evidence_digest, "evidence_digest")
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


class DurableAdapterVerifierPort(Protocol):
    async def verify(self, *, evidence: DurableAdapterEvidence) -> bool:
        """Verify concrete durable-adapter evidence; declarations alone are insufficient."""
        ...


@dataclass(frozen=True, slots=True)
class UpstreamRunLinkEvidence:
    partition: PartitionKey
    task_run_id: str
    source_run_id: str
    ordered_event_ids: tuple[str, ...]
    approval_ids: tuple[str, ...]
    approval_required: bool
    evidence_id: str
    evidence_digest: str

    def __post_init__(self) -> None:
        for name in ("task_run_id", "source_run_id", "evidence_id"):
            _required(getattr(self, name), name)
        if not self.ordered_event_ids or any(not item.strip() for item in self.ordered_event_ids):
            raise ValueError("durable upstream ordered event linkage is required")
        if len(set(self.ordered_event_ids)) != len(self.ordered_event_ids):
            raise ValueError("ordered event linkage cannot contain duplicate IDs")
        if any(not item.strip() for item in self.approval_ids):
            raise ValueError("approval IDs cannot be empty")
        if self.approval_required and not self.approval_ids:
            raise ValueError("required approval linkage is missing")
        _valid_digest(self.evidence_digest, "evidence_digest")


class UpstreamRunVerifierPort(Protocol):
    async def verify(self, *, evidence: UpstreamRunLinkEvidence) -> bool:
        """Verify durable MIG-AUTO-01 task/run/ordered-event/approval linkage."""
        ...


async def authorize_scope_and_adapter(
    *,
    partition: PartitionKey,
    actor_evidence: ResolvedScopeActorEvidence,
    adapter_evidence: DurableAdapterEvidence,
    scope_verifier: ScopeActorVerifierPort | None,
    adapter_verifier: DurableAdapterVerifierPort | None,
    actor_id: str | None = None,
    upstream_evidence: UpstreamRunLinkEvidence | None = None,
    upstream_verifier: UpstreamRunVerifierPort | None = None,
) -> None:
    if scope_verifier is None or adapter_verifier is None:
        raise OutboxBlockedError("scope actor and durable adapter verifiers are required")
    if actor_evidence.partition != partition or (actor_id is not None and actor_evidence.actor_id != actor_id):
        raise OutboxBlockedError("scope or actor evidence does not match the command")
    if not REQUIRED_ADAPTER_CAPABILITIES.issubset(adapter_evidence.capabilities):
        raise OutboxBlockedError("durable adapter evidence is missing required capabilities")
    if not await scope_verifier.verify(evidence=actor_evidence):
        raise OutboxBlockedError("scope actor evidence was not verified")
    if not await adapter_verifier.verify(evidence=adapter_evidence):
        raise OutboxBlockedError("durable adapter evidence was not verified")
    if upstream_evidence is not None or upstream_verifier is not None:
        if upstream_evidence is None or upstream_verifier is None:
            raise OutboxBlockedError("upstream run evidence and verifier are both required")
        if upstream_evidence.partition != partition:
            raise OutboxBlockedError("upstream run evidence does not match command partition")
        if not await upstream_verifier.verify(evidence=upstream_evidence):
            raise OutboxBlockedError("upstream run/event/approval evidence was not verified")


@dataclass(frozen=True, slots=True)
class BusinessMutationIntent:
    partition: PartitionKey
    entity_type: str
    entity_id: str
    expected_revision: int
    operation: str
    actor_id: str
    payload: Mapping[str, Any]
    payload_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("entity_type", "entity_id", "operation", "actor_id"):
            _required(getattr(self, name), name)
        if self.expected_revision < 0:
            raise ValueError("expected_revision cannot be negative")
        frozen = _freeze(self.payload)
        digest = canonical_digest(frozen)
        if self.payload_digest and self.payload_digest != digest:
            raise ValueError("business payload digest does not match immutable payload")
        object.__setattr__(self, "payload", frozen)
        object.__setattr__(self, "payload_digest", digest)


@dataclass(frozen=True, slots=True)
class TransactionalOutboxEvent:
    event_id: str
    partition: PartitionKey
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    source_event_id: str
    source_run_id: str
    occurred_at: datetime
    payload: Mapping[str, Any]
    idempotency_key: str
    payload_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("event_id", "aggregate_type", "aggregate_id", "event_type", "source_event_id", "source_run_id", "idempotency_key"):
            _required(getattr(self, name), name)
        if self.aggregate_version < 1:
            raise ValueError("aggregate_version must be positive")
        _aware(self.occurred_at, "occurred_at")
        frozen = _freeze(self.payload)
        digest = canonical_digest(frozen)
        if self.payload_digest and self.payload_digest != digest:
            raise ValueError("event payload digest does not match immutable payload")
        expected_key = self.aggregate_event_key(
            partition=self.partition,
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            aggregate_version=self.aggregate_version,
        )
        if self.idempotency_key != expected_key:
            raise ValueError("event idempotency key must match tenant aggregate version")
        object.__setattr__(self, "payload", frozen)
        object.__setattr__(self, "payload_digest", digest)

    @staticmethod
    def aggregate_event_key(*, partition: PartitionKey, aggregate_type: str, aggregate_id: str, aggregate_version: int) -> str:
        parts = (partition.tenant_id, aggregate_type, aggregate_id, str(aggregate_version))
        return ":".join(f"{len(part)}={part}" for part in parts)


@dataclass(frozen=True, slots=True)
class OutboxEnqueueIntent:
    event_id: str
    queue_name: str
    deduplication_key: str

    def __post_init__(self) -> None:
        for name in ("event_id", "queue_name", "deduplication_key"):
            _required(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class AtomicOutboxCommand:
    idempotency_key: str
    partition: PartitionKey
    actor_evidence: ResolvedScopeActorEvidence
    upstream_evidence: UpstreamRunLinkEvidence
    business_mutation: BusinessMutationIntent
    event: TransactionalOutboxEvent
    initial_state: OutboxStateRecord
    enqueue_intent: OutboxEnqueueIntent
    correlation_id: str
    request_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("idempotency_key", "correlation_id"):
            _required(getattr(self, name), name)
        if any(item != self.partition for item in (self.actor_evidence.partition, self.business_mutation.partition, self.event.partition)):
            raise ValueError("business, event, actor and command partitions must match")
        if self.business_mutation.actor_id != self.actor_evidence.actor_id:
            raise ValueError("business actor must match authenticated actor evidence")
        if (self.upstream_evidence.partition != self.partition
                or self.event.source_run_id != self.upstream_evidence.source_run_id
                or self.event.source_event_id not in self.upstream_evidence.ordered_event_ids):
            raise ValueError("event source run/event must match verified upstream partition evidence")
        if self.event.aggregate_type != self.business_mutation.entity_type or self.event.aggregate_id != self.business_mutation.entity_id:
            raise ValueError("event aggregate must match the business mutation")
        if self.event.aggregate_version != self.business_mutation.expected_revision + 1:
            raise ValueError("event version must follow the expected business revision")
        if (self.initial_state.event_id != self.event.event_id
                or self.initial_state.partition != self.partition
                or self.initial_state.state is not OutboxState.PENDING
                or self.initial_state.attempts != 0 or self.initial_state.lease_generation != 0):
            raise ValueError("initial outbox state must be pending, unclaimed and bound to the event partition")
        if self.enqueue_intent.event_id != self.event.event_id or self.enqueue_intent.deduplication_key != self.event.idempotency_key:
            raise ValueError("enqueue intent must reference the event and its stable key")
        expected = canonical_digest({
            "key": self.idempotency_key,
            "partition": (self.partition.tenant_id, self.partition.project_id, self.partition.automation_id),
            "business_payload_digest": self.business_mutation.payload_digest,
            "business_operation": self.business_mutation.operation,
            "business_revision": self.business_mutation.expected_revision,
            "business_actor": self.business_mutation.actor_id,
            "business_entity": (self.business_mutation.entity_type, self.business_mutation.entity_id),
            "scope_id": self.actor_evidence.scope_id,
            "scope_generation": self.actor_evidence.scope_generation,
            "scope_evidence_id": self.actor_evidence.evidence_id,
            "scope_evidence_digest": self.actor_evidence.evidence_digest,
            "upstream_task_run": self.upstream_evidence.task_run_id,
            "upstream_source_run": self.upstream_evidence.source_run_id,
            "upstream_ordered_events": self.upstream_evidence.ordered_event_ids,
            "upstream_approvals": self.upstream_evidence.approval_ids,
            "upstream_evidence_digest": self.upstream_evidence.evidence_digest,
            "event_id": self.event.event_id,
            "event_key": self.event.idempotency_key,
            "event_aggregate": (self.event.aggregate_type, self.event.aggregate_id, self.event.aggregate_version),
            "event_source_event": self.event.source_event_id,
            "event_source_run": self.event.source_run_id,
            "event_payload_digest": self.event.payload_digest,
            "event_type": self.event.event_type,
            "enqueue_queue": self.enqueue_intent.queue_name,
            "enqueue_key": self.enqueue_intent.deduplication_key,
            "initial_available_at": self.initial_state.available_at.isoformat(),
        })
        if self.request_digest and self.request_digest != expected:
            raise ValueError("request_digest does not match immutable command")
        object.__setattr__(self, "request_digest", expected)


@dataclass(frozen=True, slots=True)
class AtomicOutboxReceipt:
    event_id: str
    request_digest: str
    adapter_evidence_id: str
    duplicate: bool

    def __post_init__(self) -> None:
        for name in ("event_id", "adapter_evidence_id"):
            _required(getattr(self, name), name)
        _valid_digest(self.request_digest, "request_digest")


class TransactionalOutboxRepositoryPort(Protocol):
    async def commit_business_event_enqueue(
        self, *, command: AtomicOutboxCommand, adapter_evidence: DurableAdapterEvidence
    ) -> AtomicOutboxReceipt:
        """Atomic outbox ports commit business mutation, event, initial state, enqueue intent and audit in one UoW.

An implementation must apply database uniqueness to the scoped request key, compare request digests on conflict,
and return the durable winner for identical replay. It must never emulate this protocol with separate commits,
read-before-write, process memory, or broker-only dedupe.
"""
        ...

    async def repair_enqueue(
        self, *, partition: PartitionKey, event_id: str, source_run_id: str,
        source_event_id: str, deduplication_key: str,
        adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        """Repair only the pre-existing event/enqueue identity linked to its verified run/event."""
        ...


class DurableAutomationOutbox:
    """Fail-closed application boundary for atomic business/event/enqueue writes."""

    def __init__(
        self, *, repository: TransactionalOutboxRepositoryPort,
        scope_verifier: ScopeActorVerifierPort | None,
        adapter_verifier: DurableAdapterVerifierPort | None,
        upstream_verifier: UpstreamRunVerifierPort | None = None,
    ) -> None:
        self._repository = repository
        self._scope_verifier = scope_verifier
        self._adapter_verifier = adapter_verifier
        self._upstream_verifier = upstream_verifier

    async def commit(self, *, command: AtomicOutboxCommand, adapter_evidence: DurableAdapterEvidence) -> AtomicOutboxReceipt:
        if self._repository is None:
            raise OutboxBlockedError("durable outbox repository is unavailable")
        await authorize_scope_and_adapter(
            partition=command.partition, actor_evidence=command.actor_evidence,
            adapter_evidence=adapter_evidence, scope_verifier=self._scope_verifier,
            adapter_verifier=self._adapter_verifier, actor_id=command.business_mutation.actor_id,
            upstream_evidence=command.upstream_evidence, upstream_verifier=self._upstream_verifier,
        )
        receipt = await self._repository.commit_business_event_enqueue(command=command, adapter_evidence=adapter_evidence)
        if receipt.request_digest != command.request_digest:
            raise OutboxBlockedError("durable adapter returned a receipt for another command")
        if not receipt.duplicate and receipt.event_id != command.event.event_id:
            raise OutboxBlockedError("durable adapter inserted a different event identity")
        if receipt.adapter_evidence_id != adapter_evidence.evidence_id:
            raise OutboxBlockedError("durable adapter receipt evidence does not match verified adapter")
        return receipt

    async def repair_enqueue(
        self, *, partition: PartitionKey, actor_evidence: ResolvedScopeActorEvidence,
        upstream_evidence: UpstreamRunLinkEvidence, adapter_evidence: DurableAdapterEvidence,
        event_id: str, source_run_id: str, source_event_id: str, deduplication_key: str,
    ) -> bool:
        if self._repository is None:
            raise OutboxBlockedError("durable outbox repository is unavailable")
        await authorize_scope_and_adapter(
            partition=partition, actor_evidence=actor_evidence, adapter_evidence=adapter_evidence,
            scope_verifier=self._scope_verifier, adapter_verifier=self._adapter_verifier,
            upstream_evidence=upstream_evidence, upstream_verifier=self._upstream_verifier,
        )
        _required(event_id, "event_id")
        _required(source_run_id, "source_run_id")
        _required(source_event_id, "source_event_id")
        if (upstream_evidence.source_run_id != source_run_id
                or source_event_id not in upstream_evidence.ordered_event_ids):
            raise OutboxBlockedError("enqueue repair must match verified upstream run and event identity")
        _required(deduplication_key, "deduplication_key")
        return await self._repository.repair_enqueue(
            partition=partition, event_id=event_id, source_run_id=source_run_id,
            source_event_id=source_event_id, deduplication_key=deduplication_key,
            adapter_evidence=adapter_evidence,
        )


class OutboxState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RETRY_WAIT = "retry_wait"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 8
    initial_delay_seconds: int = 5
    max_delay_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.initial_delay_seconds < 1 or self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("retry policy bounds are invalid")

    def next_retry_at(self, *, attempt: int, failed_at: datetime) -> datetime | None:
        _aware(failed_at, "failed_at")
        if attempt < 1:
            raise ValueError("attempt must be positive")
        if attempt >= self.max_attempts:
            return None
        if attempt - 1 >= self.max_delay_seconds.bit_length():
            delay = self.max_delay_seconds
        else:
            delay = min(self.max_delay_seconds, self.initial_delay_seconds * (2 ** (attempt - 1)))
        return failed_at + timedelta(seconds=delay)


@dataclass(frozen=True, slots=True)
class OutboxStateRecord:
    event_id: str
    partition: PartitionKey
    state: OutboxState
    available_at: datetime
    attempts: int = 0
    lease_generation: int = 0
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_until: datetime | None = None
    last_error_code: str | None = None
    delivered_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    receipt_id: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        _required(self.event_id, "event_id")
        _aware(self.available_at, "available_at")
        for name in ("lease_until", "delivered_at", "dead_lettered_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)
        if min(self.attempts, self.lease_generation, self.revision) < 0:
            raise ValueError("attempts, generation and revision cannot be negative")
        leased = self.state is OutboxState.CLAIMED
        if leased:
            if self.attempts < 1 or self.lease_generation < 1:
                raise ValueError("claimed state requires a positive attempt and generation")
            if not all((self.lease_owner, self.lease_token, self.lease_until)):
                raise ValueError("claimed state requires owner, token and lease expiry")
        elif any((self.lease_owner, self.lease_token, self.lease_until)):
            raise ValueError("only claimed state may carry lease fields")
        if self.state is OutboxState.RETRY_WAIT:
            _validate_error_code(self.last_error_code or "")
        if self.state is OutboxState.DELIVERED:
            if not (self.delivered_at and self.receipt_id) or self.dead_lettered_at is not None:
                raise ValueError("delivered state requires only a delivered timestamp and receiver receipt")
        if self.state is OutboxState.DEAD_LETTER:
            if not (self.dead_lettered_at and self.last_error_code) or self.delivered_at is not None or self.receipt_id is not None:
                raise ValueError("dead-letter state requires only a terminal timestamp and stable error code")
        if self.state is OutboxState.DEAD_LETTER:
            _validate_error_code(self.last_error_code or "")
        if self.state not in (OutboxState.DELIVERED, OutboxState.DEAD_LETTER) and any((self.delivered_at, self.dead_lettered_at, self.receipt_id)):
            raise ValueError("non-terminal state cannot carry terminal outcome")


class OutboxStateMachine:
    """Pure transition rules; durable adapters must persist them with CAS."""

    @staticmethod
    def claim(
        current: OutboxStateRecord, *, owner: str, token: str, now: datetime, lease_until: datetime,
    ) -> OutboxStateRecord:
        _aware(now, "now")
        _aware(lease_until, "lease_until")
        _required(owner, "owner")
        _required(token, "token")
        if lease_until <= now:
            raise ValueError("lease_until must be after now")
        eligible = (
            current.state in (OutboxState.PENDING, OutboxState.RETRY_WAIT) and current.available_at <= now
        ) or (
            current.state is OutboxState.CLAIMED and current.lease_until is not None and current.lease_until <= now
        )
        if not eligible:
            raise OutboxBlockedError("outbox row is not due or is terminal")
        return OutboxStateRecord(
            event_id=current.event_id, partition=current.partition, state=OutboxState.CLAIMED,
            available_at=current.available_at, attempts=current.attempts + 1,
            lease_generation=current.lease_generation + 1, lease_owner=owner, lease_token=token,
            lease_until=lease_until, last_error_code=current.last_error_code, revision=current.revision + 1,
        )

    @staticmethod
    def delivered(
        current: OutboxStateRecord, *, generation: int, token: str, completed_at: datetime, receipt_id: str,
    ) -> OutboxStateRecord:
        _assert_outbox_lease(current, generation=generation, token=token, now=completed_at)
        _required(receipt_id, "receipt_id")
        return OutboxStateRecord(
            event_id=current.event_id, partition=current.partition, state=OutboxState.DELIVERED,
            available_at=current.available_at, attempts=current.attempts, lease_generation=current.lease_generation,
            delivered_at=completed_at, receipt_id=receipt_id, revision=current.revision + 1,
        )

    @staticmethod
    def failed(
        current: OutboxStateRecord, *, generation: int, token: str, failed_at: datetime,
        error_code: str, retry_policy: RetryPolicy,
    ) -> OutboxStateRecord:
        _assert_outbox_lease(current, generation=generation, token=token, now=failed_at)
        _validate_error_code(error_code)
        retry_at = retry_policy.next_retry_at(attempt=current.attempts, failed_at=failed_at)
        if retry_at is None:
            return OutboxStateRecord(
                event_id=current.event_id, partition=current.partition, state=OutboxState.DEAD_LETTER,
                available_at=current.available_at, attempts=current.attempts,
                lease_generation=current.lease_generation, last_error_code=error_code,
                dead_lettered_at=failed_at, revision=current.revision + 1,
            )
        return OutboxStateRecord(
            event_id=current.event_id, partition=current.partition, state=OutboxState.RETRY_WAIT,
            available_at=retry_at, attempts=current.attempts, lease_generation=current.lease_generation,
            last_error_code=error_code, revision=current.revision + 1,
        )


def _assert_outbox_lease(current: OutboxStateRecord, *, generation: int, token: str, now: datetime) -> None:
    _aware(now, "now")
    if (
        current.state is not OutboxState.CLAIMED or current.lease_generation != generation
        or current.lease_token != token or current.lease_until is None or current.lease_until <= now
    ):
        raise OutboxBlockedError("stale or expired outbox claim")


def _validate_error_code(code: str) -> None:
    value = _required(code, "error_code")
    if not value.startswith("automation.error.") or len(value) > 128 or "\n" in value or "\r" in value:
        raise ValueError("error_code must be a bounded automation.error code")


class DeliveryState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RETRY_WAIT = "retry_wait"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class OutboundDeliveryIntent:
    delivery_id: str
    partition: PartitionKey
    task_run_id: str
    subscription_id: str
    source_event_id: str
    destination_ref: str
    secret_ref: str
    payload: Mapping[str, Any]
    idempotency_key: str
    payload_digest: str = ""
    request_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("delivery_id", "task_run_id", "subscription_id", "source_event_id", "destination_ref", "secret_ref", "idempotency_key"):
            _required(getattr(self, name), name)
        frozen = _freeze(self.payload)
        payload_hash = canonical_digest(frozen)
        if self.payload_digest and self.payload_digest != payload_hash:
            raise ValueError("delivery payload digest mismatch")
        expected_key = self.stable_key(
            partition=self.partition, subscription_id=self.subscription_id, source_event_id=self.source_event_id,
        )
        if self.idempotency_key != expected_key:
            raise ValueError("delivery key must be stable for subscription and source event")
        expected_request = canonical_digest({
            "tenant": self.partition.tenant_id, "project": self.partition.project_id,
            "automation": self.partition.automation_id, "subscription": self.subscription_id,
            "source_event": self.source_event_id, "task_run_id": self.task_run_id,
            "destination_ref": self.destination_ref,
            "secret_ref": self.secret_ref, "payload_digest": payload_hash,
        })
        if self.request_digest and self.request_digest != expected_request:
            raise ValueError("delivery request digest mismatch")
        object.__setattr__(self, "payload", frozen)
        object.__setattr__(self, "payload_digest", payload_hash)
        object.__setattr__(self, "request_digest", expected_request)

    @staticmethod
    def stable_key(*, partition: PartitionKey, subscription_id: str, source_event_id: str) -> str:
        identity = canonical_digest({
            "tenant": partition.tenant_id, "project": partition.project_id,
            "automation": partition.automation_id, "subscription": subscription_id,
            "source_event": source_event_id,
        })
        return hashlib.sha256(identity.encode("ascii")).hexdigest()


class DeliveryIdentityFencePort(Protocol):
    async def insert_delivery_once(
        self, *, intent: OutboundDeliveryIntent, upstream_evidence: UpstreamRunLinkEvidence,
        adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        """Unique `(subscription_id, source_event_id)` and key; exact hash replay is false, changed hash conflicts."""
        ...


class DurableDeliveryRegistrar:
    def __init__(
        self, *, repository: DeliveryIdentityFencePort,
        scope_verifier: ScopeActorVerifierPort | None,
        adapter_verifier: DurableAdapterVerifierPort | None,
        upstream_verifier: UpstreamRunVerifierPort | None,
    ) -> None:
        self._repository = repository
        self._scope_verifier = scope_verifier
        self._adapter_verifier = adapter_verifier
        self._upstream_verifier = upstream_verifier

    async def register_once(
        self, *, intent: OutboundDeliveryIntent, actor_evidence: ResolvedScopeActorEvidence,
        upstream_evidence: UpstreamRunLinkEvidence, adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        if (intent.partition != actor_evidence.partition or intent.partition != upstream_evidence.partition
                or intent.task_run_id != upstream_evidence.task_run_id
                or intent.source_event_id not in upstream_evidence.ordered_event_ids):
            raise OutboxBlockedError("delivery identity must match verified upstream task/event scope")
        await authorize_scope_and_adapter(
            partition=intent.partition, actor_evidence=actor_evidence,
            adapter_evidence=adapter_evidence, scope_verifier=self._scope_verifier,
            adapter_verifier=self._adapter_verifier, upstream_evidence=upstream_evidence,
            upstream_verifier=self._upstream_verifier,
        )
        return await self._repository.insert_delivery_once(
            intent=intent, upstream_evidence=upstream_evidence, adapter_evidence=adapter_evidence,
        )


class DeliveryClaimPort(Protocol):
    async def register_once(
        self, *, intent: OutboundDeliveryIntent, upstream_evidence: UpstreamRunLinkEvidence,
        actor_evidence: ResolvedScopeActorEvidence, adapter_evidence: DurableAdapterEvidence,
        scope_verifier: ScopeActorVerifierPort | None, upstream_verifier: UpstreamRunVerifierPort | None,
        adapter_verifier: DurableAdapterVerifierPort | None,
    ) -> bool:
        ...


class LegacyProjectionRepositoryPort(Protocol):
    async def apply_projection(self, *, update: ProjectionUpdate, expected_revision: int,
                               legacy_marker: Any, adapter_evidence: DurableAdapterEvidence) -> bool:
        """Revision-CAS a derived legacy compatibility row; it never grants source ownership."""
        ...


class DeliveryStateMachine:
    @staticmethod
    def claim(
        *, intent: OutboundDeliveryIntent, state: DeliveryState, available_at: datetime,
        attempts: int, lease_generation: int, current_lease_until: datetime | None,
        owner: str, token: str, now: datetime, lease_until: datetime,
    ) -> tuple[DeliveryState, int, int, str, str, datetime]:
        _aware(now, "now")
        _aware(lease_until, "lease_until")
        _required(owner, "owner")
        _required(token, "token")
        if current_lease_until is not None:
            _aware(current_lease_until, "current_lease_until")
        due = state in (DeliveryState.PENDING, DeliveryState.RETRY_WAIT) and available_at <= now
        expired = state is DeliveryState.CLAIMED and current_lease_until is not None and current_lease_until <= now
        if state is DeliveryState.CLAIMED and current_lease_until is None:
            raise ValueError("claimed delivery requires its current lease expiry")
        if not due and not expired:
            raise OutboxBlockedError("delivery is not due, leaseable or is terminal")
        if lease_until <= now or attempts < 0 or lease_generation < 0:
            raise ValueError("delivery claim lease or generations are invalid")
        return DeliveryState.CLAIMED, attempts + 1, lease_generation + 1, owner, token, lease_until

    @staticmethod
    def delivered(*, claim: DeliveryClaim, completed_at: datetime, receipt_id: str) -> DeliveryOutcome:
        _aware(completed_at, "completed_at")
        if completed_at >= claim.lease_until or not claim.lease.valid_at(completed_at):
            raise OutboxBlockedError("stale or expired delivery claim")
        return DeliveryOutcome(
            claim.intent.delivery_id, claim.attempt, claim.lease_generation, claim.claim_token,
            DeliveryState.DELIVERED, completed_at, receipt_id=receipt_id,
        )

    @staticmethod
    def failed(*, claim: DeliveryClaim, failed_at: datetime, error_code: str,
               retry_policy: RetryPolicy) -> DeliveryOutcome:
        _aware(failed_at, "failed_at")
        if failed_at >= claim.lease_until or not claim.lease.valid_at(failed_at):
            raise OutboxBlockedError("stale or expired delivery claim")
        _validate_error_code(error_code)
        retry_at = retry_policy.next_retry_at(attempt=claim.attempt, failed_at=failed_at)
        return DeliveryOutcome(
            claim.intent.delivery_id, claim.attempt, claim.lease_generation, claim.claim_token,
            DeliveryState.DEAD_LETTER if retry_at is None else DeliveryState.RETRY_WAIT,
            failed_at, retry_at=retry_at, error_code=error_code,
        )


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    intent: OutboundDeliveryIntent
    attempt: int
    lease: WriterLease
    lease_generation: int
    claim_token: str
    claimed_at: datetime
    lease_until: datetime

    def __post_init__(self) -> None:
        if self.attempt < 1 or self.lease_generation < 1:
            raise ValueError("attempt and lease generation must be positive")
        _required(self.claim_token, "claim_token")
        _aware(self.claimed_at, "claimed_at")
        _aware(self.lease_until, "lease_until")
        if self.intent.partition != self.lease.partition or self.lease_until <= self.claimed_at or self.lease_until > self.lease.expires_at:
            raise ValueError("delivery claim must match a bounded same-partition writer lease")


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    delivery_id: str
    attempt: int
    lease_generation: int
    claim_token: str
    state: DeliveryState
    completed_at: datetime
    retry_at: datetime | None = None
    receipt_id: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _required(self.delivery_id, "delivery_id")
        _required(self.claim_token, "claim_token")
        if self.attempt < 1 or self.lease_generation < 1:
            raise ValueError("attempt and lease generation must be positive")
        _aware(self.completed_at, "completed_at")
        if self.retry_at is not None:
            _aware(self.retry_at, "retry_at")
        if self.state is DeliveryState.RETRY_WAIT:
            if self.retry_at is None:
                raise ValueError("retry_wait requires retry_at")
            if self.receipt_id is not None:
                raise ValueError("retry_wait cannot carry a receiver receipt")
            _validate_error_code(self.error_code or "")
        elif self.state is DeliveryState.DELIVERED:
            _required(self.receipt_id or "", "receipt_id")
            if self.retry_at is not None or self.error_code is not None:
                raise ValueError("delivered outcome cannot have retry_at")
        elif self.state is DeliveryState.DEAD_LETTER:
            _validate_error_code(self.error_code or "")
            if self.retry_at is not None or self.receipt_id is not None:
                raise ValueError("dead-letter outcome cannot carry retry time or receiver receipt")
        else:
            raise ValueError("outcome must be retry_wait, delivered, or dead_letter")


class DeliveryRepositoryPort(Protocol):
    async def register_delivery_once(
        self, *, intent: OutboundDeliveryIntent,
        upstream_evidence: UpstreamRunLinkEvidence,
        adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        """Durably fence one identity; identical replay is false, changed request digest conflicts."""
        ...

    async def claim_due(
        self, *, partition: PartitionKey, lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence], limit: int,
    ) -> tuple[DeliveryClaim, ...]:
        """Atomic due/reclaim CAS; validate linked upstream evidence and increment generations/attempts once."""
        ...

    async def record_outcome(
        self, *, claim: DeliveryClaim, outcome: DeliveryOutcome, lease: WriterLease, now: datetime,
    ) -> bool:
        """CAS identities/fences and compare lease expiry to transaction/database time; ``now`` is audit time only."""
        ...


class ExistingAutomationOutboxRepositoryPort(Protocol):
    """Method surface of the existing persistence adapter, without importing infrastructure."""

    async def register_delivery_once(
        self, *, intent: OutboundDeliveryIntent,
        upstream_evidence: UpstreamRunLinkEvidence,
        adapter_evidence: DurableAdapterEvidence,
    ) -> bool: ...

    async def claim_due_deliveries(
        self, *, partition: PartitionKey, lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence], limit: int,
    ) -> tuple[DeliveryClaim, ...]: ...

    async def record_delivery_outcome(
        self, *, claim: DeliveryClaim, outcome: DeliveryOutcome, lease: WriterLease, now: datetime,
    ) -> bool: ...

    async def claim_due_outbox(
        self, *, partition: PartitionKey, writer_lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence], limit: int,
    ) -> tuple[OutboxClaim, ...]: ...

    async def compare_and_set_outbox_state(
        self, *, claim: OutboxClaim, next_state: OutboxStateRecord,
        expected_revision: int, writer_lease: WriterLease, now: datetime,
    ) -> bool: ...


class AutomationDeliveryRepositoryAdapter:
    """Bind worker ports to the existing SQL repository method names."""

    def __init__(self, repository: ExistingAutomationOutboxRepositoryPort) -> None:
        self._repository = repository

    async def insert_delivery_once(
        self, *, intent: OutboundDeliveryIntent,
        upstream_evidence: UpstreamRunLinkEvidence,
        adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        return await self._repository.register_delivery_once(
            intent=intent, upstream_evidence=upstream_evidence,
            adapter_evidence=adapter_evidence,
        )

    async def register_delivery_once(
        self, *, intent: OutboundDeliveryIntent,
        upstream_evidence: UpstreamRunLinkEvidence,
        adapter_evidence: DurableAdapterEvidence,
    ) -> bool:
        return await self._repository.register_delivery_once(
            intent=intent, upstream_evidence=upstream_evidence,
            adapter_evidence=adapter_evidence,
        )

    async def claim_due(
        self, *, partition: PartitionKey, lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence], limit: int,
    ) -> tuple[DeliveryClaim, ...]:
        return await self._repository.claim_due_deliveries(
            partition=partition, lease=lease, now=now, lease_until=lease_until,
            upstream_evidence=upstream_evidence, limit=limit,
        )

    async def record_outcome(
        self, *, claim: DeliveryClaim, outcome: DeliveryOutcome,
        lease: WriterLease, now: datetime,
    ) -> bool:
        return await self._repository.record_delivery_outcome(
            claim=claim, outcome=outcome, lease=lease, now=now,
        )


class AutomationOutboxStateRepositoryAdapter:
    """Bind the publisher worker port to the existing SQL outbox CAS methods."""

    def __init__(self, repository: ExistingAutomationOutboxRepositoryPort) -> None:
        self._repository = repository

    async def claim_due(
        self, *, partition: PartitionKey, writer_lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence], limit: int,
    ) -> tuple[OutboxClaim, ...]:
        return await self._repository.claim_due_outbox(
            partition=partition, writer_lease=writer_lease, now=now,
            lease_until=lease_until, upstream_evidence=upstream_evidence, limit=limit,
        )

    async def compare_and_set_state(
        self, *, claim: OutboxClaim, next_state: OutboxStateRecord,
        expected_revision: int, writer_lease: WriterLease, now: datetime,
    ) -> bool:
        return await self._repository.compare_and_set_outbox_state(
            claim=claim, next_state=next_state, expected_revision=expected_revision,
            writer_lease=writer_lease, now=now,
        )


class ClockPort(Protocol):
    def now(self) -> datetime:
        """Return current timezone-aware time after a delivery effect."""
        ...


@dataclass(frozen=True, slots=True)
class ReceiverDeduplicationEvidence:
    delivery_id: str
    receiver_id: str
    idempotency_key: str
    request_digest: str
    evidence_id: str
    evidence_digest: str
    retention_until: datetime

    def __post_init__(self) -> None:
        for name in ("delivery_id", "receiver_id", "idempotency_key", "evidence_id"):
            _required(getattr(self, name), name)
        _valid_digest(self.request_digest, "request_digest")
        _valid_digest(self.evidence_digest, "evidence_digest")
        _aware(self.retention_until, "retention_until")


class ReceiverDeduplicationVerifierPort(Protocol):
    async def verify(self, *, evidence: ReceiverDeduplicationEvidence) -> bool:
        """Verify receiver-side durable dedupe and same-key/different-body conflict semantics."""
        ...


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    receiver_id: str
    receipt_id: str
    idempotency_key: str
    request_digest: str

    def __post_init__(self) -> None:
        for name in ("receiver_id", "receipt_id", "idempotency_key"):
            _required(getattr(self, name), name)
        _valid_digest(self.request_digest, "request_digest")


class DeliveryEffectPort(Protocol):
    async def deliver(
        self, *, intent: OutboundDeliveryIntent, idempotency_key: str,
        request_digest: str, receiver_evidence: ReceiverDeduplicationEvidence,
    ) -> DeliveryReceipt:
        """Abstract side-effect port only; implementations must preserve receiver dedupe key and bytes."""
        ...


class DeliveryOwnershipSnapshot(Protocol):
    partition: PartitionKey
    state: Any
    epoch: int
    fencing_token: str | None
    legacy_claims_enabled: bool
    target_claims_enabled: bool


class DeliveryOwnershipPort(Protocol):
    async def load_ownership(self, partition: PartitionKey) -> DeliveryOwnershipSnapshot | None: ...


class DurableDeliveryWorker:
    """Generic worker orchestration; no HTTP/network implementation is supplied."""

    def __init__(
        self, *, repository: DeliveryRepositoryPort, ownership: DeliveryOwnershipPort,
        effect: DeliveryEffectPort, scope_verifier: ScopeActorVerifierPort,
        adapter_verifier: DurableAdapterVerifierPort, receiver_verifier: ReceiverDeduplicationVerifierPort,
        clock: ClockPort, upstream_verifier: UpstreamRunVerifierPort | None,
        retry_policy: RetryPolicy | None = None, max_batch_size: int = 100,
    ) -> None:
        if not 1 <= max_batch_size <= 1000:
            raise ValueError("max_batch_size must be between 1 and 1000")
        self._repository = repository
        self._ownership = ownership
        self._effect = effect
        self._scope_verifier = scope_verifier
        self._adapter_verifier = adapter_verifier
        self._receiver_verifier = receiver_verifier
        self._upstream_verifier = upstream_verifier
        self._clock = clock
        self._retry_policy = retry_policy or RetryPolicy()
        self._max_batch_size = max_batch_size

    async def run_once(
        self, *, partition: PartitionKey, lease: WriterLease, now: datetime, lease_until: datetime,
        actor_evidence: ResolvedScopeActorEvidence, adapter_evidence: DurableAdapterEvidence,
        upstream_evidence: Mapping[str, UpstreamRunLinkEvidence],
        receiver_evidence: Mapping[str, ReceiverDeduplicationEvidence], limit: int = 1,
    ) -> int:
        _aware(now, "now")
        _aware(lease_until, "lease_until")
        if not 1 <= limit <= self._max_batch_size:
            raise ValueError("limit exceeds configured bounded batch size")
        if lease.partition != partition or not lease.valid_at(now) or not now < lease_until <= lease.expires_at:
            raise OutboxBlockedError("a live same-partition worker lease is required")
        for delivery_id, run_evidence in upstream_evidence.items():
            if (not delivery_id.strip() or run_evidence.partition != partition or self._upstream_verifier is None
                    or not await self._upstream_verifier.verify(evidence=run_evidence)):
                raise OutboxBlockedError("all candidate task/run/event evidence must verify before claiming")
        if self._upstream_verifier is None or not upstream_evidence:
            raise OutboxBlockedError("verified upstream linkage is required before claiming")
        await authorize_scope_and_adapter(
            partition=partition, actor_evidence=actor_evidence, adapter_evidence=adapter_evidence,
            scope_verifier=self._scope_verifier, adapter_verifier=self._adapter_verifier,
            actor_id=actor_evidence.actor_id,
        )
        owner = await self._ownership.load_ownership(partition)
        owner_state = getattr(getattr(owner, "state", None), "value", getattr(owner, "state", None))
        if (owner is None or owner.partition != partition or owner_state != "target_active"
                or not owner.target_claims_enabled or owner.legacy_claims_enabled
                or owner.epoch != lease.epoch or owner.fencing_token != lease.fencing_token):
            raise OutboxBlockedError("target worker is not the exclusive current partition owner")
        claims = await self._repository.claim_due(
            partition=partition, lease=lease, now=now, lease_until=lease_until,
            upstream_evidence=upstream_evidence, limit=limit,
        )
        if len(claims) > limit:
            raise OutboxBlockedError("delivery repository exceeded the bounded claim limit")
        completed = 0
        for claim in claims:
            if claim.intent.partition != partition or claim.lease != lease or claim.claimed_at > now:
                raise OutboxBlockedError("repository returned a claim outside the fenced partition")
            evidence = receiver_evidence.get(claim.intent.delivery_id)
            claim_check_at = _aware(self._clock.now(), "claim_check_at")
            if claim.lease_until <= claim_check_at or not lease.valid_at(claim_check_at):
                continue
            run_evidence = (
                upstream_evidence.get(claim.intent.delivery_id)
                or upstream_evidence.get(claim.intent.source_event_id)
            )
            if (run_evidence is None or self._upstream_verifier is None
                    or run_evidence.partition != partition
                    or run_evidence.task_run_id != claim.intent.task_run_id
                    or claim.intent.source_event_id not in run_evidence.ordered_event_ids
                    or not await self._upstream_verifier.verify(evidence=run_evidence)):
                raise OutboxBlockedError("verified task/run/event/approval linkage is required before delivery")
            if (evidence is None or evidence.delivery_id != claim.intent.delivery_id
                    or evidence.idempotency_key != claim.intent.idempotency_key
                    or evidence.request_digest != claim.intent.request_digest or evidence.retention_until <= claim_check_at
                    or not await self._receiver_verifier.verify(evidence=evidence)):
                raise OutboxBlockedError("verified receiver idempotency evidence is required before delivery")
            try:
                receipt = await self._effect.deliver(
                    intent=claim.intent, idempotency_key=claim.intent.idempotency_key,
                    request_digest=claim.intent.request_digest, receiver_evidence=evidence,
                )
                if (receipt.receiver_id != evidence.receiver_id
                        or receipt.idempotency_key != claim.intent.idempotency_key
                        or receipt.request_digest != claim.intent.request_digest):
                    raise OutboxBlockedError("receiver receipt does not match fenced delivery identity")
                completed_at = _aware(self._clock.now(), "completed_at")
                if completed_at >= claim.lease_until or not lease.valid_at(completed_at):
                    continue
                outcome = DeliveryOutcome(
                    claim.intent.delivery_id, claim.attempt, claim.lease_generation, claim.claim_token,
                    DeliveryState.DELIVERED, completed_at, receipt_id=receipt.receipt_id,
                )
            except OutboxBlockedError:
                raise
            except Exception as exc:
                completed_at = _aware(self._clock.now(), "failed_at")
                if completed_at >= claim.lease_until or not lease.valid_at(completed_at):
                    continue
                code = "automation.error.delivery_failed." + hashlib.sha256(
                    f"{exc.__class__.__module__}.{exc.__class__.__qualname__}".encode("utf-8")
                ).hexdigest()[:24]
                retry_at = self._retry_policy.next_retry_at(attempt=claim.attempt, failed_at=completed_at)
                outcome = DeliveryOutcome(
                    claim.intent.delivery_id, claim.attempt, claim.lease_generation, claim.claim_token,
                    DeliveryState.DEAD_LETTER if retry_at is None else DeliveryState.RETRY_WAIT,
                    completed_at, retry_at=retry_at, error_code=code,
                )
            applied = await self._repository.record_outcome(
                claim=claim, outcome=outcome, lease=lease, now=outcome.completed_at,
            )
            if applied and outcome.state is DeliveryState.DELIVERED:
                completed += 1
        return completed


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    event: TransactionalOutboxEvent
    state: OutboxStateRecord
    writer_lease: WriterLease
    claimed_at: datetime

    def __post_init__(self) -> None:
        if self.event.event_id != self.state.event_id or self.event.partition != self.state.partition:
            raise ValueError("outbox claim event and state identities must match")
        if self.event.partition != self.writer_lease.partition or self.state.state is not OutboxState.CLAIMED:
            raise ValueError("outbox claim must be leased by the same partition")
        _aware(self.claimed_at, "claimed_at")
        if (self.state.lease_until is None or self.state.lease_until <= self.claimed_at
                or self.state.lease_until > self.writer_lease.expires_at
                or self.state.lease_owner != self.writer_lease.holder
                or not self.state.lease_token):
            raise ValueError("outbox row lease must match the live writer lease")


class DurableOutboxStatePort(Protocol):
    async def claim_due(
        self, *, partition: PartitionKey, writer_lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence], limit: int,
    ) -> tuple[OutboxClaim, ...]:
        """Atomic due/reclaim claim with attempt and generation increments once."""
        ...

    async def compare_and_set_state(
        self, *, claim: OutboxClaim, next_state: OutboxStateRecord,
        expected_revision: int, writer_lease: WriterLease, now: datetime,
    ) -> bool:
        """CAS event, partition, row revision, row token/generation and current writer fence."""
        ...


class DurableOutboxWorker:
    """Fenced application worker over durable outbox state and an abstract publisher."""

    def __init__(
        self, *, repository: DurableOutboxStatePort, ownership: DeliveryOwnershipPort,
        scope_verifier: ScopeActorVerifierPort | None,
        adapter_verifier: DurableAdapterVerifierPort | None,
        clock: ClockPort,
        max_batch_size: int = 100,
    ) -> None:
        if not 1 <= max_batch_size <= 1000:
            raise ValueError("max_batch_size must be between 1 and 1000")
        self._repository = repository
        self._ownership = ownership
        self._scope_verifier = scope_verifier
        self._adapter_verifier = adapter_verifier
        self._clock = clock
        self._max_batch_size = max_batch_size

    async def claim_due(
        self, *, partition: PartitionKey, writer_lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence],
        upstream_verifier: UpstreamRunVerifierPort | None,
        actor_evidence: ResolvedScopeActorEvidence, adapter_evidence: DurableAdapterEvidence,
        limit: int = 1,
    ) -> tuple[OutboxClaim, ...]:
        _aware(now, "now")
        _aware(lease_until, "lease_until")
        if not 1 <= limit <= self._max_batch_size:
            raise ValueError("limit exceeds configured bounded batch size")
        if (partition != writer_lease.partition or not writer_lease.valid_at(now)
                or not now < lease_until <= writer_lease.expires_at):
            raise OutboxBlockedError("a live same-partition writer lease is required")
        if upstream_verifier is None or not upstream_evidence:
            raise OutboxBlockedError("verified upstream run/event evidence is required")
        await authorize_scope_and_adapter(
            partition=partition, actor_evidence=actor_evidence,
            adapter_evidence=adapter_evidence, scope_verifier=self._scope_verifier,
            adapter_verifier=self._adapter_verifier, actor_id=actor_evidence.actor_id,
        )
        for evidence in upstream_evidence.values():
            if evidence.partition != partition or not await upstream_verifier.verify(evidence=evidence):
                raise OutboxBlockedError("upstream evidence is invalid for the requested partition")
        owner = await self._ownership.load_ownership(partition)
        owner_state = getattr(getattr(owner, "state", None), "value", getattr(owner, "state", None))
        if (owner is None or owner.partition != partition or owner_state != "target_active"
                or not owner.target_claims_enabled or owner.legacy_claims_enabled
                or owner.epoch != writer_lease.epoch or owner.fencing_token != writer_lease.fencing_token):
            raise OutboxBlockedError("target outbox worker is not the exclusive partition owner")
        claims = await self._repository.claim_due(
            partition=partition, writer_lease=writer_lease, now=now,
            lease_until=lease_until, upstream_evidence=upstream_evidence, limit=limit,
        )
        if len(claims) > limit:
            raise OutboxBlockedError("repository exceeded the bounded claim limit")
        for claim in claims:
            evidence = upstream_evidence.get(claim.event.source_event_id)
            if (claim.event.partition != partition or claim.writer_lease != writer_lease
                    or claim.claimed_at > now or claim.state.lease_until is None
                    or claim.state.lease_until > lease_until or evidence is None
                    or evidence.source_run_id != claim.event.source_run_id
                    or claim.event.source_event_id not in evidence.ordered_event_ids):
                raise OutboxBlockedError("repository returned an invalid or unlinked fenced claim")
        return claims

    async def run_once(
        self, *, partition: PartitionKey, writer_lease: WriterLease, now: datetime,
        lease_until: datetime, upstream_evidence: Mapping[str, UpstreamRunLinkEvidence],
        upstream_verifier: UpstreamRunVerifierPort | None,
        actor_evidence: ResolvedScopeActorEvidence, adapter_evidence: DurableAdapterEvidence,
        publisher: OutboxEventPublisherPort, retry_policy: RetryPolicy | None = None,
        limit: int = 1,
    ) -> int:
        policy = retry_policy or RetryPolicy()
        claims = await self.claim_due(
            partition=partition, writer_lease=writer_lease, now=now, lease_until=lease_until,
            upstream_evidence=upstream_evidence, upstream_verifier=upstream_verifier,
            actor_evidence=actor_evidence, adapter_evidence=adapter_evidence, limit=limit,
        )
        delivered = 0
        for claim in claims:
            state = claim.state
            try:
                receipt_id = await publisher.publish(
                    event=claim.event, idempotency_key=claim.event.idempotency_key,
                    payload_digest=canonical_digest(claim.event.payload),
                )
                completed_at = _aware(self._clock.now(), "completed_at")
                next_state = OutboxStateMachine.delivered(
                    state, generation=state.lease_generation, token=state.lease_token or "",
                    completed_at=completed_at, receipt_id=receipt_id,
                )
                transition_at = completed_at
            except OutboxBlockedError:
                raise
            except Exception as exc:
                failed_at = _aware(self._clock.now(), "failed_at")
                error_code = "automation.error.publish_failed." + hashlib.sha256(
                    f"{exc.__class__.__module__}.{exc.__class__.__qualname__}".encode("utf-8")
                ).hexdigest()[:24]
                next_state = OutboxStateMachine.failed(
                    state, generation=state.lease_generation, token=state.lease_token or "",
                    failed_at=failed_at, error_code=error_code, retry_policy=policy,
                )
                transition_at = failed_at
            saved = await self.record_outcome(
                claim=claim, next_state=next_state, expected_revision=state.revision,
                now=transition_at,
            )
            if saved and next_state.state is OutboxState.DELIVERED:
                delivered += 1
        return delivered

    async def record_outcome(
        self, *, claim: OutboxClaim, next_state: OutboxStateRecord,
        expected_revision: int, now: datetime,
    ) -> bool:
        _aware(now, "now")
        if claim.state.revision != expected_revision or claim.state.state is not OutboxState.CLAIMED:
            return False
        if (claim.state.lease_until is None or claim.state.lease_until <= now
                or claim.state.lease_generation < 1 or not claim.state.lease_token):
            return False
        if (claim.state.revision != expected_revision or claim.event.event_id != next_state.event_id
                or claim.event.partition != next_state.partition
                or next_state.state not in (OutboxState.RETRY_WAIT, OutboxState.DELIVERED, OutboxState.DEAD_LETTER)
                or next_state.attempts != claim.state.attempts
                or next_state.lease_generation != claim.state.lease_generation
                or next_state.revision != expected_revision + 1
                or not claim.writer_lease.valid_at(now)):
            return False
        return await self._repository.compare_and_set_state(
            claim=claim, next_state=next_state, expected_revision=expected_revision,
            writer_lease=claim.writer_lease, now=now,
        )


class OutboxEventPublisherPort(Protocol):
    async def publish(
        self, *, event: TransactionalOutboxEvent, idempotency_key: str, payload_digest: str,
    ) -> str:
        """Abstract publication boundary; implementations must deduplicate the stable event key."""
        ...


__all__ = [
    "AtomicOutboxCommand", "AtomicOutboxReceipt", "BusinessMutationIntent", "ClockPort", "DeliveryClaim",
    "DeliveryEffectPort", "DeliveryIdentityFencePort", "DeliveryOutcome", "DeliveryOwnershipPort",
    "DeliveryReceipt", "DeliveryRepositoryPort", "DeliveryState", "DurableAdapterEvidence",
    "DurableAdapterVerifierPort", "DurableAutomationOutbox", "DurableDeliveryWorker", "DeliveryClaimPort",
    "DurableDeliveryRegistrar",
    "OutboxBlockedError", "OutboxEnqueueIntent", "OutboxIdempotencyConflict", "OutboxState",
    "OutboxStateMachine", "OutboxStateRecord", "OutboxClaim", "DurableOutboxStatePort", "DurableOutboxWorker",
    "OutboxEventPublisherPort", "DeliveryStateMachine", "LegacyProjectionRepositoryPort",
    "OutboundDeliveryIntent", "REQUIRED_ADAPTER_CAPABILITIES",
    "ReceiverDeduplicationEvidence", "ReceiverDeduplicationVerifierPort", "ResolvedScopeActorEvidence",
    "RetryPolicy", "ScopeActorVerifierPort", "TransactionalOutboxEvent",
    "TransactionalOutboxRepositoryPort", "canonical_digest",
]
