"""Contract tests for the project_triggers automation context/fence handoff."""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.automation_context_handoff.contracts import (
    HandoffContext,
    PartitionName,
    PromotionEvidence,
    WorkerFence,
    project_trigger_idempotency_key,
    project_trigger_request_hash,
)
from prodavan.application.automation_context_handoff.service import AutomationContextHandoffService


class FakePort:
    def __init__(self) -> None:
        self.intents = {}
        self.promoted = False

    async def record_intent(self, *, intent, claim):
        existing = self.intents.get(claim.key)
        if existing is not None:
            return False, existing
        self.intents[claim.key] = intent
        return True, intent

    async def claim(self, *, task_id, fence):
        intent = next((item for item in self.intents.values() if item.task.task_id == task_id), None)
        if intent is None or fence.partition is not PartitionName.PROJECT_TRIGGERS:
            return None
        return intent

    async def complete(self, *, task_id, fence, succeeded, error=None):
        return fence.partition is PartitionName.PROJECT_TRIGGERS and fence.generation == 2

    async def promote(self, *, evidence):
        self.promoted = evidence.promotable
        return self.promoted


def context() -> HandoffContext:
    return HandoffContext(tenant_id="tenant-1", project_id="project-1", run_id="run-1")


def evidence(**changes: object) -> PromotionEvidence:
    values = {
        "partition": PartitionName.PROJECT_TRIGGERS,
        "checkpoint_id": "checkpoint-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "legacy_claims_stopped": True,
        "active_legacy_leases_drained": True,
        "parity_zero": True,
        "idempotency_replay_verified": True,
        "worker_fence": WorkerFence(
            partition=PartitionName.PROJECT_TRIGGERS, worker_id="worker-1", generation=2
        ),
    }
    values.update(changes)
    return PromotionEvidence(**values)


@pytest.mark.asyncio
async def test_project_trigger_identity_is_parented_and_partitioned() -> None:
    port = FakePort()
    service = AutomationContextHandoffService(port=port)
    accepted, intent = await service.record_project_trigger(
        context=context(),
        trigger_id="trigger-1",
        kind="project.prepare",
        payload={"source": "migration"},
        task_id="task-1",
        event_id="event-1",
        delivery_id="delivery-1",
    )

    assert accepted is True
    assert intent.partition is PartitionName.PROJECT_TRIGGERS
    assert intent.task.project_id == intent.outbox.project_id == intent.delivery.project_id == "project-1"
    assert intent.task.idempotency_key == intent.outbox.idempotency_key == intent.delivery.idempotency_key
    assert intent.payload["source"] == "migration"
    with pytest.raises(TypeError):
        intent.payload["source"] = "mutated"


@pytest.mark.asyncio
async def test_task_source_trigger_cannot_be_rebound_to_another_trigger() -> None:
    port = FakePort()
    service = AutomationContextHandoffService(port=port)
    _, intent = await service.record_project_trigger(
        context=context(),
        trigger_id="trigger-1",
        kind="project.prepare",
        payload={"source": "migration"},
        task_id="task-1",
        event_id="event-1",
    )

    with pytest.raises(ValueError, match="source trigger"):
        type(intent)(
            context=intent.context,
            trigger_id="trigger-2",
            kind=intent.kind,
            payload=intent.payload,
            task=replace(intent.task, source_trigger_id="trigger-1"),
            outbox=intent.outbox,
        )


@pytest.mark.asyncio
async def test_duplicate_trigger_replays_original_intent_without_second_side_effect() -> None:
    port = FakePort()
    service = AutomationContextHandoffService(port=port)
    kwargs = {
        "context": context(),
        "trigger_id": "trigger-1",
        "kind": "project.prepare",
        "payload": {"source": "migration"},
        "task_id": "task-1",
        "event_id": "event-1",
    }
    first, original = await service.record_project_trigger(**kwargs)
    duplicate_kwargs = {**kwargs, "task_id": "task-2", "event_id": "event-2"}
    duplicate, replay = await service.record_project_trigger(**duplicate_kwargs)

    assert first is True
    assert duplicate is False
    assert replay.task.task_id == original.task.task_id
    assert len(port.intents) == 1


@pytest.mark.asyncio
async def test_same_source_key_has_changed_payload_conflict_hash() -> None:
    first_key = project_trigger_idempotency_key(
        tenant_id="tenant-1", project_id="project-1", trigger_id="trigger-1", payload={"value": 1}
    )
    second_key = project_trigger_idempotency_key(
        tenant_id="tenant-1", project_id="project-1", trigger_id="trigger-1", payload={"value": 2}
    )
    assert first_key == second_key
    assert project_trigger_request_hash(trigger_id="trigger-1", kind="project.prepare", payload={"value": 1}) != project_trigger_request_hash(
        trigger_id="trigger-1", kind="project.prepare", payload={"value": 2}
    )


@pytest.mark.asyncio
async def test_completion_requires_error_for_failed_run_and_generation_is_fencing_input() -> None:
    service = AutomationContextHandoffService(port=FakePort())
    with pytest.raises(ValueError, match="requires error"):
        await service.complete_project_trigger(
            task_id="task-1",
            fence=WorkerFence(partition=PartitionName.PROJECT_TRIGGERS, worker_id="w", generation=2),
            succeeded=False,
        )

    assert await service.complete_project_trigger(
        task_id="task-1",
        fence=WorkerFence(partition=PartitionName.PROJECT_TRIGGERS, worker_id="w", generation=1),
        succeeded=True,
    ) is False


@pytest.mark.asyncio
async def test_promotion_rejects_mismatched_fence_partition() -> None:
    with pytest.raises(ValueError, match="partition"):
        evidence(worker_fence=WorkerFence(partition="wrong", worker_id="w", generation=1))


@pytest.mark.asyncio
async def test_promotion_requires_all_evidence() -> None:
    service = AutomationContextHandoffService(port=FakePort())
    with pytest.raises(ValueError, match="incomplete"):
        await service.promote_project_triggers(evidence=evidence(parity_zero=False))

    assert await service.promote_project_triggers(evidence=evidence()) is True
