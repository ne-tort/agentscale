"""Application service for the project_triggers context/fence handoff."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import (
    HandoffContext,
    IdempotencyClaim,
    ProjectTriggerIntent,
    PromotionEvidence,
    TaskIdentity,
    OutboxIdentity,
    DeliveryIdentity,
    WorkerFence,
    project_trigger_idempotency_key,
    project_trigger_request_hash,
)
from .ports import AutomationContextHandoffPort


class AutomationContextHandoffService:
    """Builds and hands off project triggers without owning persistence or routes."""

    def __init__(self, *, port: AutomationContextHandoffPort) -> None:
        self._port = port

    async def record_project_trigger(
        self,
        *,
        context: HandoffContext,
        trigger_id: str,
        kind: str,
        payload: Mapping[str, Any],
        task_id: str,
        event_id: str,
        delivery_id: str | None = None,
    ) -> tuple[bool, ProjectTriggerIntent]:
        key = project_trigger_idempotency_key(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            trigger_id=trigger_id,
        )
        task = TaskIdentity(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            run_id=context.run_id,
            task_id=task_id,
            source_trigger_id=trigger_id,
            idempotency_key=key,
        )
        outbox = OutboxIdentity(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            run_id=context.run_id,
            task_id=task_id,
            event_id=event_id,
            idempotency_key=key,
        )
        delivery = (
            DeliveryIdentity(
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                run_id=context.run_id,
                task_id=task_id,
                event_id=event_id,
                delivery_id=delivery_id,
                idempotency_key=key,
            )
            if delivery_id is not None
            else None
        )
        intent = ProjectTriggerIntent(
            context=context,
            trigger_id=trigger_id,
            kind=kind,
            payload=dict(payload),
            task=task,
            outbox=outbox,
            delivery=delivery,
        )
        claim = IdempotencyClaim(
            tenant_id=context.tenant_id,
            partition=intent.partition,
            key=key,
            request_hash=project_trigger_request_hash(trigger_id=trigger_id, kind=kind, payload=payload),
            task_id=task_id,
            accepted=True,
        )
        return await self._port.record_intent(intent=intent, claim=claim)

    async def claim_project_trigger(self, *, task_id: str, fence: WorkerFence) -> ProjectTriggerIntent | None:
        return await self._port.claim(task_id=task_id, fence=fence)

    async def complete_project_trigger(
        self, *, task_id: str, fence: WorkerFence, succeeded: bool, error: str | None = None
    ) -> bool:
        if not succeeded and not error:
            raise ValueError("failed completion requires error")
        return await self._port.complete(task_id=task_id, fence=fence, succeeded=succeeded, error=error)

    async def promote_project_triggers(self, *, evidence: PromotionEvidence) -> bool:
        if not evidence.promotable:
            raise ValueError("promotion evidence is incomplete")
        return await self._port.promote(evidence=evidence)
