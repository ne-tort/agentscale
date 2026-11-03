"""Feature-gated dependencies for the durable employee Agent Run API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.api.deps import SessionDep
from prodavan.application.agent_control_plane import DurableAgentControlPlane
from prodavan.application.agent_control_plane.contracts import ControlPlaneAtomicWrite, ControlPlaneWriteResult
from prodavan.application.agent_control_plane.ports import AgentControlPlaneTransactionPort
from prodavan.domain.agent.control_plane import AgentRunView, ApprovalRequestView, EventEnvelope
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.database import get_session_factory
from prodavan.infrastructure.persistence.models.base import Base

FEATURE_FLAG = "PRODAVAN_AGENT_RUN_API_ENABLED"
REQUIRED_MODEL_TABLES = frozenset(
    {
        "agent_runs",
        "approval_requests",
        "agent_control_plane_run_scopes",
        "agent_control_plane_aggregates",
        "agent_control_plane_commands",
        "agent_control_plane_events",
        "agent_control_plane_outbox_events",
        "agent_control_plane_enqueue_intents",
    }
)


class AgentControlPlaneRepository(AgentControlPlaneTransactionPort, Protocol):
    """Tenant-scoped reads for the real, gated transaction repository."""

    _session_factory: async_sessionmaker[AsyncSession]

    async def commit(self, *, write: ControlPlaneAtomicWrite) -> ControlPlaneWriteResult: ...

    async def get_run(self, *, tenant_id: str, run_id: str) -> AgentRunView | None:
        raise NotImplementedError

    async def get_approval(self, *, tenant_id: str, approval_id: str) -> ApprovalRequestView | None:
        raise NotImplementedError

    async def list_events(
        self,
        *,
        tenant_id: str,
        run_id: str,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[EventEnvelope, ...]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class AgentRunBackend:
    """Repository seam, request-scoped DB session and explicit dispatcher gate."""

    transaction: AgentControlPlaneRepository
    session: AsyncSession
    writes_enabled: bool = False

    def control_plane(self, *, tenant_id: str) -> DurableAgentControlPlane:
        transaction = self.transaction if self.writes_enabled else None
        return DurableAgentControlPlane(transaction=transaction, tenant_id=tenant_id)

    def require_writes(self) -> None:
        if not self.writes_enabled:
            raise _blocked(
                code="AGENT_RUN_PERSISTENCE_BLOCKED",
                blocker="agent_control_plane_dispatcher_unwired",
                detail="durable enqueue-intent dispatcher is not wired",
            )


def _feature_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def missing_model_tables(registered_tables: set[str] | frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(REQUIRED_MODEL_TABLES - registered_tables))


def _blocked(*, code: str, blocker: str, detail: str) -> AppError:
    return AppError(
        code=code,
        title="Agent Run API unavailable",
        status=503,
        detail=detail,
        extra={"blocker": blocker, "feature_flag": FEATURE_FLAG},
    )


async def get_agent_run_backend(session: SessionDep) -> AgentRunBackend:
    """Fail closed unless opt-in, ORM registration, and the database are ready.

    This dependency is deliberately not backed by process memory or legacy
    run/approval repositories. Readiness probes are read-only and never create
    or migrate tables.
    """
    if not _feature_enabled(os.getenv(FEATURE_FLAG)):
        raise _blocked(
            code="AGENT_RUN_API_DISABLED",
            blocker="agent_run_api_feature_flag_disabled",
            detail="durable Agent Run API is disabled",
        )

    missing = missing_model_tables(set(Base.metadata.tables))
    if missing:
        raise _blocked(
            code="AGENT_RUN_PERSISTENCE_NOT_READY",
            blocker="agent_control_plane_models_not_registered",
            detail="Agent Run ORM models are not registered: " + ", ".join(missing),
        )

    try:
        await session.execute(text("SELECT 1"))
        for table_name in sorted(REQUIRED_MODEL_TABLES):
            await session.execute(text(f'SELECT 1 FROM "{table_name}" LIMIT 0'))
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _blocked(
            code="AGENT_RUN_PERSISTENCE_NOT_READY",
            blocker="agent_control_plane_database_not_ready",
            detail="Agent Run persistence tables are unavailable",
        ) from exc

    from prodavan.infrastructure.persistence.agent_control_plane import SqlAlchemyAgentControlPlaneRepository
    transaction = SqlAlchemyAgentControlPlaneRepository(session_factory=get_session_factory())
    return AgentRunBackend(transaction=transaction, session=session, writes_enabled=False)


AgentRunBackendDep = Annotated[AgentRunBackend, Depends(get_agent_run_backend)]

__all__ = [
    "AgentRunBackend",
    "AgentRunBackendDep",
    "FEATURE_FLAG",
    "REQUIRED_MODEL_TABLES",
    "_feature_enabled",
    "get_agent_run_backend",
    "missing_model_tables",
]
