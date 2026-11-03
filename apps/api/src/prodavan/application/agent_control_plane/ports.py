"""Exclusive durable transaction ports for Agent Runs/Approvals.

The application layer is deliberately unable to compose independent run,
approval, and outbox repositories.  A production adapter must implement the
single ``commit`` operation and bind all writes to one database transaction.
Until that adapter is wired, :class:`DurableAgentControlPlane` fails closed.
"""

from __future__ import annotations

from typing import Protocol

from .contracts import ControlPlaneAtomicWrite, ControlPlaneWriteResult
from prodavan.domain.agent.control_plane import AgentRunView, ApprovalRequestView


WIRING_BLOCKER = "agent_control_plane_atomic_transaction_adapter_unwired"


class AgentControlPlaneTransactionPort(Protocol):
    """Repository-owned atomic state/event/outbox transaction.

    Implementations must, in one durable transaction:

    * claim ``command_idempotency_key`` together with its immutable command
      digest, scoped to tenant, project, operation, aggregate IDs, and command
      key, then replay the first result for an identical command;
    * resolve the run under the command tenant and verify its project/session
      linkage; resolve approval ownership through that run in the same
      transaction, never by trusting caller-supplied IDs;
    * compare-and-set every aggregate status and sequence in the command;
    * append every event, outbox event, and queue intent exactly once;
    * reject a reused command/transition key with a changed digest;
    * roll back all state, event, and outbox rows on any write error.

    Aggregate version allocation is never a read-then-write operation. The
    repository owns the uniqueness/CAS constraints and returns every persisted
    run/approval view plus one result per event+queue-intent pair. A partial
    event/outbox result is not a successful commit.
    """

    async def commit(self, *, write: ControlPlaneAtomicWrite) -> ControlPlaneWriteResult:
        ...


class AgentControlPlaneReadPort(Protocol):
    """Optional read seam for a future adapter; it is not a write fallback."""

    async def get_run(self, *, tenant_id: str, run_id: str) -> AgentRunView | None: ...

    async def get_approval(self, *, tenant_id: str, approval_id: str) -> ApprovalRequestView | None: ...


__all__ = ["AgentControlPlaneReadPort", "AgentControlPlaneTransactionPort", "WIRING_BLOCKER"]
