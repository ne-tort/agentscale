"""Persistence-neutral contracts for handing project triggers to automation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class PartitionName(StrEnum):
    PROJECT_TRIGGERS = "project_triggers"


@dataclass(frozen=True, slots=True)
class HandoffContext:
    tenant_id: str
    project_id: str
    run_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.tenant_id, self.project_id, self.run_id)):
            raise ValueError("tenant_id, project_id and run_id must not be empty")


@dataclass(frozen=True, slots=True)
class TaskIdentity:
    tenant_id: str
    project_id: str
    run_id: str
    task_id: str
    source_trigger_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        values = (
            self.tenant_id,
            self.project_id,
            self.run_id,
            self.task_id,
            self.source_trigger_id,
            self.idempotency_key,
        )
        if not all(value.strip() for value in values):
            raise ValueError("task identity fields must not be empty")


@dataclass(frozen=True, slots=True)
class OutboxIdentity:
    tenant_id: str
    project_id: str
    run_id: str
    task_id: str
    event_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.tenant_id, self.project_id, self.run_id, self.task_id, self.event_id, self.idempotency_key)):
            raise ValueError("outbox identity fields must not be empty")


@dataclass(frozen=True, slots=True)
class DeliveryIdentity:
    tenant_id: str
    project_id: str
    run_id: str
    task_id: str
    event_id: str
    delivery_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.tenant_id, self.project_id, self.run_id, self.task_id, self.event_id, self.delivery_id, self.idempotency_key)):
            raise ValueError("delivery identity fields must not be empty")


@dataclass(frozen=True, slots=True)
class ProjectTriggerIntent:
    context: HandoffContext
    trigger_id: str
    kind: str
    payload: Mapping[str, Any]
    task: TaskIdentity
    outbox: OutboxIdentity
    delivery: DeliveryIdentity | None = None
    partition: PartitionName = PartitionName.PROJECT_TRIGGERS

    def __post_init__(self) -> None:
        if not self.trigger_id.strip() or not self.kind.strip():
            raise ValueError("trigger_id and kind must not be empty")
        object.__setattr__(self, "payload", _freeze(_canonical(self.payload)))
        if self.partition is not PartitionName.PROJECT_TRIGGERS:
            raise ValueError("project trigger handoff must use project_triggers partition")
        expected = (self.context.tenant_id, self.context.project_id, self.context.run_id)
        if (self.task.tenant_id, self.task.project_id, self.task.run_id) != expected:
            raise ValueError("task identity parent must match handoff context")
        if (self.outbox.tenant_id, self.outbox.project_id, self.outbox.run_id) != expected:
            raise ValueError("outbox identity parent must match handoff context")
        if self.delivery is not None and (self.delivery.tenant_id, self.delivery.project_id, self.delivery.run_id) != expected:
            raise ValueError("delivery identity parent must match handoff context")
        if self.task.source_trigger_id != self.trigger_id:
            raise ValueError("task source trigger must match handoff trigger")
        if self.task.idempotency_key != self.outbox.idempotency_key:
            raise ValueError("task and outbox idempotency keys must match")
        if self.outbox.task_id != self.task.task_id:
            raise ValueError("outbox task identity must match task")
        if self.delivery is not None:
            if self.delivery.idempotency_key != self.task.idempotency_key:
                raise ValueError("delivery idempotency key must match task")
            if (self.delivery.task_id, self.delivery.event_id) != (self.task.task_id, self.outbox.event_id):
                raise ValueError("delivery parent identity must match task and outbox")


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    tenant_id: str
    partition: PartitionName
    key: str
    request_hash: str
    task_id: str
    accepted: bool
    duplicate: bool = False

    def __post_init__(self) -> None:
        if self.partition is not PartitionName.PROJECT_TRIGGERS:
            raise ValueError("idempotency claim must use project_triggers partition")
        if not all(value.strip() for value in (self.tenant_id, self.key, self.request_hash, self.task_id)):
            raise ValueError("idempotency claim identity fields must not be empty")


@dataclass(frozen=True, slots=True)
class WorkerFence:
    partition: PartitionName
    worker_id: str
    generation: int

    def __post_init__(self) -> None:
        if self.partition is not PartitionName.PROJECT_TRIGGERS:
            raise ValueError("worker fence must use project_triggers partition")
        if not self.worker_id.strip() or self.generation < 1:
            raise ValueError("worker_id and positive generation are required")


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    partition: PartitionName
    checkpoint_id: str
    tenant_id: str
    project_id: str
    legacy_claims_stopped: bool
    active_legacy_leases_drained: bool
    parity_zero: bool
    idempotency_replay_verified: bool
    worker_fence: WorkerFence

    def __post_init__(self) -> None:
        if self.partition is not PartitionName.PROJECT_TRIGGERS:
            raise ValueError("promotion must use project_triggers partition")
        if not all(value.strip() for value in (self.checkpoint_id, self.tenant_id, self.project_id)):
            raise ValueError("promotion checkpoint and scope must not be empty")
        if self.worker_fence.partition is not self.partition:
            raise ValueError("worker fence partition must match promotion partition")

    @property
    def promotable(self) -> bool:
        return all((self.legacy_claims_stopped, self.active_legacy_leases_drained, self.parity_zero, self.idempotency_replay_verified))


def project_trigger_idempotency_key(*, tenant_id: str, project_id: str, trigger_id: str, payload: Mapping[str, Any] | None = None) -> str:
    """Stable source identity; payload belongs in the separate request hash."""
    if not all(value.strip() for value in (tenant_id, project_id, trigger_id)):
        raise ValueError("tenant_id, project_id and trigger_id must not be empty")
    envelope = f"project_triggers|{len(tenant_id)}:{tenant_id}|{len(project_id)}:{project_id}|{len(trigger_id)}:{trigger_id}"
    return sha256(envelope.encode("utf-8")).hexdigest()


def project_trigger_request_hash(*, trigger_id: str, kind: str, payload: Mapping[str, Any]) -> str:
    """Hash the full request so same source identity with changed data conflicts."""
    if not trigger_id.strip() or not kind.strip():
        raise ValueError("trigger_id and kind must not be empty")
    encoded = repr((trigger_id, kind, _canonical(payload))).encode("utf-8")
    return sha256(encoded).hexdigest()
