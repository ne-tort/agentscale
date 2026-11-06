"""Persistence and worker ports for the project trigger handoff."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    IdempotencyClaim,
    ProjectTriggerIntent,
    PromotionEvidence,
    WorkerFence,
)


class AutomationContextHandoffPort(Protocol):
    async def record_intent(
        self, *, intent: ProjectTriggerIntent, claim: IdempotencyClaim
    ) -> tuple[bool, ProjectTriggerIntent]:
        """Atomically claim idempotency and persist task/outbox intent."""
        ...

    async def claim(
        self, *, task_id: str, fence: WorkerFence
    ) -> ProjectTriggerIntent | None:
        """Claim only with the current partition/worker generation fence."""
        ...

    async def complete(
        self,
        *,
        task_id: str,
        fence: WorkerFence,
        succeeded: bool,
        error: str | None = None,
    ) -> bool:
        """Complete through an owner+generation conditional update."""
        ...

    async def promote(self, *, evidence: PromotionEvidence) -> bool:
        """Atomically change ownership after validated promotion evidence."""
        ...
