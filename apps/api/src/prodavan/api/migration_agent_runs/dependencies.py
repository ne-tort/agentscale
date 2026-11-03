"""Feature-gated dependencies for the durable employee Agent Run API.

Readiness is three explicit, read-only gates:

1. ``PRODAVAN_AGENT_RUN_API_ENABLED`` — opt-in feature flag, disabled by default;
2. ORM metadata registration — every required table is present in
   ``Base.metadata`` (the six ``AgentControlPlane*Row`` classes are registered by
   ``prodavan.infrastructure.persistence.models``);
3. database readiness — a read-only probe of the required tables. Missing or
   unavailable tables fail the request; nothing here creates schema.

Passing all three flips ``AgentRunBackend.writes_enabled`` to ``True`` and binds
the durable consumer that owns enqueue intents. Mutating routes then
1) refuse to commit when the flag is off or a table is missing, and
2) after the control-plane transaction committed, hand the committed intents to
   the durable dispatcher (``SqlAlchemyIntentConsumer`` through
   :func:`build_intent_dispatcher`). The trigger runs *after* the commit: the
   domain events, the outbox projection and the enqueue-intent rows are already
   durable in one transaction. A failed, timed-out or skipped trigger therefore
   only delays delivery — the pending intent row stays claimable by the next
   consumer (another request, an operator drain, a poll loop).

Nothing here falls back to process memory or to a legacy run/approval
repository, and no code path enables writes without the feature flag.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.api.deps import SessionDep
from prodavan.api.migration_agent_runs.schemas import AgentRunDispatchReport
from prodavan.application.agent_control_plane import DurableAgentControlPlane
from prodavan.application.agent_control_plane.contracts import ControlPlaneAtomicWrite, ControlPlaneWriteResult
from prodavan.application.agent_control_plane.ports import AgentControlPlaneTransactionPort
from prodavan.application.agent_runs import DispatchRunner
from prodavan.domain.agent.control_plane import AgentRunView, ApprovalRequestView, EventEnvelope
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.database import get_session_factory
from prodavan.infrastructure.persistence.models.base import Base

logger = logging.getLogger(__name__)

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

#: Queue name the control-plane writes its enqueue intents to; the consumer must
#: poll the same queue or the handoff is never claimed.
INTENT_QUEUE_NAME = "agent-control-plane"
DISPATCH_OWNER_PREFIX = "agent-run-api"
DISPATCH_OWNER_MAX = 128
#: The trigger is bounded so one request can never claim an unbounded batch.
MAX_DISPATCH_BATCH = 25
#: The HTTP response must not wait on a wedged database. A timed-out trigger is
#: not a lost intent: the row keeps its (short) lease and is reclaimed later.
DISPATCH_TRIGGER_TIMEOUT_SECONDS = 2.0
#: Durable retry ceiling while no execution handler is registered:
#: 288 attempts x 300 s backoff cap is about one day of retries before an
#: undeliverable intent is parked terminally by the poison-pill guard.
DISPATCH_MAX_ATTEMPTS = 288

BLOCKER_FEATURE_FLAG_DISABLED = "agent_run_api_feature_flag_disabled"
BLOCKER_MODELS_NOT_REGISTERED = "agent_control_plane_models_not_registered"
BLOCKER_DATABASE_NOT_READY = "agent_control_plane_database_not_ready"
BLOCKER_DISPATCHER_UNAVAILABLE = "agent_control_plane_dispatcher_unavailable"

DISPATCH_REASON_FAILED = "agent_control_plane_dispatch_failed"
DISPATCH_REASON_TIMEOUT = "agent_control_plane_dispatch_timeout"
DISPATCH_REASON_IDLE = "agent_control_plane_dispatch_idle"
DISPATCH_REASON_NO_INTENTS = "agent_run_no_queue_intents"


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
    """Repository seam, request session, write gate and durable dispatch owner."""

    transaction: AgentControlPlaneRepository
    session: AsyncSession
    writes_enabled: bool = False
    dispatcher: DispatchRunner | None = None

    def control_plane(self, *, tenant_id: str) -> DurableAgentControlPlane:
        transaction = self.transaction if self.writes_enabled else None
        return DurableAgentControlPlane(
            transaction=transaction,
            tenant_id=tenant_id,
            queue_name=INTENT_QUEUE_NAME,
        )

    def require_writes(self) -> None:
        """Fail closed before any commit when mutation is not fully wired."""
        if not self.writes_enabled:
            raise _blocked(
                code="AGENT_RUN_API_DISABLED",
                blocker=BLOCKER_FEATURE_FLAG_DISABLED,
                detail=f"durable Agent Run API writes are disabled; set {FEATURE_FLAG}=1",
            )
        if self.dispatcher is None:
            # Committing a state transition whose enqueue intent has no consumer
            # would leave work "queued" forever, so refuse instead.
            raise _blocked(
                code="AGENT_RUN_PERSISTENCE_NOT_READY",
                blocker=BLOCKER_DISPATCHER_UNAVAILABLE,
                detail="durable enqueue-intent consumer is unavailable",
            )

    async def dispatch_after_commit(
        self,
        *,
        trace_id: str,
        intent_count: int,
    ) -> AgentRunDispatchReport:
        """Trigger the durable consumer for intents committed by this request.

        Must be called strictly after the control-plane commit returned an
        ``applied``/``duplicate`` result. Intents are already durable, so every
        failure mode here degrades to "delivery not attempted now" and is
        reported with a reason code instead of raising.
        """
        if not self.writes_enabled:
            return AgentRunDispatchReport(
                triggered=False,
                reason=BLOCKER_FEATURE_FLAG_DISABLED,
            )
        if intent_count < 1:
            # A replay may restore a receipt whose intents exist already; an
            # empty write genuinely has nothing to hand over.
            return AgentRunDispatchReport(triggered=False, reason=DISPATCH_REASON_NO_INTENTS)
        dispatcher = self.dispatcher
        if dispatcher is None:
            logger.warning(
                "agent run dispatch trigger skipped blocker=%s",
                BLOCKER_DISPATCHER_UNAVAILABLE,
            )
            return AgentRunDispatchReport(
                triggered=False,
                reason=BLOCKER_DISPATCHER_UNAVAILABLE,
            )

        owner = dispatch_owner_id(trace_id)
        limit = min(max(int(intent_count), 1), MAX_DISPATCH_BATCH)
        try:
            async with asyncio.timeout(DISPATCH_TRIGGER_TIMEOUT_SECONDS):
                report = await dispatcher.dispatch_batch(owner_id=owner, limit=limit)
        except TimeoutError:
            logger.warning("agent run dispatch trigger timed out owner=%s", owner)
            return AgentRunDispatchReport(triggered=True, reason=DISPATCH_REASON_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - the durable row still owns delivery
            logger.warning(
                "agent run dispatch trigger failed error=%s", type(exc).__qualname__
            )
            return AgentRunDispatchReport(triggered=True, reason=DISPATCH_REASON_FAILED)

        return AgentRunDispatchReport(
            triggered=True,
            claimed=report.claimed,
            delivered=report.delivered,
            retried=report.retried,
            rejected=report.rejected,
            quarantined=report.quarantined,
            lease_lost=report.lease_lost,
            reason=None if report.has_work else DISPATCH_REASON_IDLE,
        )


def dispatch_owner_id(trace_id: str) -> str:
    """Stable, bounded consumer lease owner derived from the command trace."""
    token = (trace_id or "").strip() or "unattributed"
    return f"{DISPATCH_OWNER_PREFIX}:{token}"[:DISPATCH_OWNER_MAX]


def _feature_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def missing_model_tables(registered_tables: set[str] | frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(REQUIRED_MODEL_TABLES - registered_tables))


def _blocked(*, code: str, blocker: str, detail: str, extra: dict | None = None) -> AppError:
    payload: dict = {"blocker": blocker, "feature_flag": FEATURE_FLAG}
    if extra:
        payload.update(extra)
    return AppError(
        code=code,
        title="Agent Run API unavailable",
        status=503,
        detail=detail,
        extra=payload,
    )


def _repository_factory() -> AgentControlPlaneRepository:
    """Compose the real repository; imports stay lazy to keep import cycles out."""
    from prodavan.infrastructure.persistence.agent_control_plane import (
        SqlAlchemyAgentControlPlaneRepository,
    )

    return SqlAlchemyAgentControlPlaneRepository(session_factory=get_session_factory())


def _dispatcher_factory() -> DispatchRunner:
    """Compose the durable enqueue-intent consumer for this queue.

    The execution handler registry is empty while ``AgentRunExecutionPort`` has
    no adapter, so the consumer claims intents and records a redacted
    ``intent_handler_not_wired`` retry instead of pretending to deliver them.
    """
    from prodavan.infrastructure.persistence.agent_control_plane.intent_consumer import (
        IntentConsumerSettings,
        build_intent_dispatcher,
    )

    dispatcher, _consumer = build_intent_dispatcher(
        session_factory=get_session_factory(),
        queue_name=INTENT_QUEUE_NAME,
        settings=IntentConsumerSettings(max_attempts=DISPATCH_MAX_ATTEMPTS),
        max_attempts=DISPATCH_MAX_ATTEMPTS,
    )
    return dispatcher


async def get_agent_run_backend(session: SessionDep) -> AgentRunBackend:
    """Fail closed unless opt-in, ORM registration, and the database are ready.

    This dependency is deliberately not backed by process memory or legacy
    run/approval repositories. Readiness probes are read-only and never create
    or migrate tables.
    """
    if not _feature_enabled(os.getenv(FEATURE_FLAG)):
        raise _blocked(
            code="AGENT_RUN_API_DISABLED",
            blocker=BLOCKER_FEATURE_FLAG_DISABLED,
            detail=f"durable Agent Run API is disabled; set {FEATURE_FLAG}=1 to enable it",
        )

    registered = set(Base.metadata.tables)
    missing = missing_model_tables(registered)
    if missing:
        raise _blocked(
            code="AGENT_RUN_PERSISTENCE_NOT_READY",
            blocker=BLOCKER_MODELS_NOT_REGISTERED,
            detail="Agent Run ORM models are not registered: " + ", ".join(missing),
            extra={"missing_tables": list(missing)},
        )

    try:
        await session.execute(text("SELECT 1"))
        for table_name in sorted(REQUIRED_MODEL_TABLES):
            await session.execute(text(f'SELECT 1 FROM "{table_name}" LIMIT 0'))
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _blocked(
            code="AGENT_RUN_PERSISTENCE_NOT_READY",
            blocker=BLOCKER_DATABASE_NOT_READY,
            detail="Agent Run persistence tables are unavailable",
        ) from exc

    try:
        transaction = _repository_factory()
    except Exception as exc:  # noqa: BLE001 - composition failure must stay closed
        logger.warning(
            "agent run repository composition failed error=%s", type(exc).__qualname__
        )
        raise _blocked(
            code="AGENT_RUN_PERSISTENCE_NOT_READY",
            blocker=BLOCKER_DATABASE_NOT_READY,
            detail="Agent Run persistence repository is unavailable",
        ) from exc

    try:
        dispatcher: DispatchRunner | None = _dispatcher_factory()
    except Exception as exc:  # noqa: BLE001 - reads stay available, writes will refuse
        logger.warning(
            "agent run dispatcher composition failed error=%s", type(exc).__qualname__
        )
        dispatcher = None

    return AgentRunBackend(
        transaction=transaction,
        session=session,
        writes_enabled=True,
        dispatcher=dispatcher,
    )


AgentRunBackendDep = Annotated[AgentRunBackend, Depends(get_agent_run_backend)]

__all__ = [
    "AgentRunBackend",
    "AgentRunBackendDep",
    "BLOCKER_DATABASE_NOT_READY",
    "BLOCKER_DISPATCHER_UNAVAILABLE",
    "BLOCKER_FEATURE_FLAG_DISABLED",
    "BLOCKER_MODELS_NOT_REGISTERED",
    "DISPATCH_MAX_ATTEMPTS",
    "DISPATCH_REASON_FAILED",
    "DISPATCH_REASON_IDLE",
    "DISPATCH_REASON_NO_INTENTS",
    "DISPATCH_REASON_TIMEOUT",
    "DISPATCH_TRIGGER_TIMEOUT_SECONDS",
    "FEATURE_FLAG",
    "INTENT_QUEUE_NAME",
    "MAX_DISPATCH_BATCH",
    "REQUIRED_MODEL_TABLES",
    "_feature_enabled",
    "dispatch_owner_id",
    "get_agent_run_backend",
    "missing_model_tables",
]
