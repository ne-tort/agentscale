"""Ports for durable agent run orchestration and human approvals."""

from __future__ import annotations

from typing import Protocol, Sequence

from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunView,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestView,
    EventEnvelope,
)


class AgentRunRepository(Protocol):
    async def create_or_get(self, command: AgentRunCreate) -> AgentRunView: ...

    async def get(self, run_id: str) -> AgentRunView | None: ...

    async def append_event(self, envelope: EventEnvelope) -> None: ...


class ApprovalRequestRepository(Protocol):
    async def create_or_get(self, command: ApprovalRequestCreate) -> ApprovalRequestView: ...

    async def get(self, approval_id: str) -> ApprovalRequestView | None: ...

    async def decide(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRequestView: ...

    async def list_pending(self, run_id: str) -> Sequence[ApprovalRequestView]: ...


class AgentControlPlane(Protocol):
    async def submit_run(self, command: AgentRunCreate) -> AgentRunView: ...

    async def request_approval(self, command: ApprovalRequestCreate) -> ApprovalRequestView: ...

    async def decide_approval(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRequestView: ...
