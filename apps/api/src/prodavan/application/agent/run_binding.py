"""MIG-WEB-RUNBIND-01 — durable binding of a legacy chat turn to a control-plane run.

Why this module exists
----------------------
Two write paths disagree about what a "run" is.

* The legacy chat turn
  (:meth:`prodavan.application.agent.session_service.AgentSessionService.
  iter_chat_turn_sse` → ``start_detached_turn`` → ``_iter_send_events``)
  persists every frame into ``agent_events`` and a turn-ledger row into
  ``agent_turns``. It never creates an ``agent_runs`` row.
* The canonical web stream
  (:mod:`prodavan.application.web_facade_adapter.runtime_factory`,
  ``DurableRunIndexProvider``) builds its trusted index exclusively from
  ``agent_runs`` + ``agent_control_plane_events``. A session without a durable
  run is a deliberate 503 (``web_chat_stream_run_index_evidence_missing``), and
  every frame whose ``(run_id, sequence)`` pair is not provable is rejected
  (``STREAM_EVENT_ID_UNRESOLVED``).

Until the legacy turn *also* writes a durable run and durable run-sequenced
frames, canonical frame identity is unprovable and the web stream refuses.
This module is that bridge. It is a self-contained application component: it
edits no existing file and is attached through one explicit call per phase of a
turn (see "Call sites" below).

Design rules enforced here
--------------------------
1. **One run per turn, created through the existing atomic port.** All writes
   go through :class:`prodavan.application.agent_control_plane.service.
   DurableAgentControlPlane` (create/start/finish) and, for stream frames,
   straight through ``AgentControlPlaneTransactionPort.commit(write=
   ControlPlaneAtomicWrite)`` — i.e. ``infrastructure/persistence/
   agent_control_plane/repository.py``. This module contains no INSERT and keeps
   no run state of its own.
2. **Durable, reproducible resolution.** ``(project_id, session_id, turn_id)``
   is resolved **only** from the database: the run's ``idempotency_key`` is the
   deterministic binding key ``turn-run:<session>:<turn>`` (covered by
   ``uq_agent_runs_project_idempotency``) and the run's tenant is proven through
   ``agent_control_plane_run_scopes``. A missing binding refuses; a run id is
   never synthesised, derived or guessed by a caller.
3. **Fail-closed.** Foreign tenant, inactive project, missing session, missing
   binding, terminal turn, unsupported run status: each is a named
   :class:`RunBindingError` — never a degraded write, never process memory.
4. **Idempotency.** Replaying the same turn start returns the same run (the
   command receipt plus the deterministic idempotency key make a second run
   impossible). Re-delivering the same stream frame returns the sequence already
   allocated for it and creates no second row, because the frame's event
   idempotency key is content-derived and therefore sequence-free.
5. **No raw content in the audit trail.** A frame's durable event carries the
   event type, the frame-key digest and the payload digest — never the frame
   payload itself (prompt text, tool output, file bytes).

Call sites (exact; the owner of those files wires them — nothing here imports
``session_service`` or ``runtime_factory``, so there is no import cycle):

1. ``session_service.AgentSessionService.start_detached_turn`` — after the
   durable ``AgentTurnRow`` reservation has been committed (the ``await
   self._session.commit()`` inside the ``durable_turn_id`` branch) and
   **before** ``detached_turns.start(...)``::

       binding = await bridge.bind_turn(
           key=TurnBindingKey(
               project_id=project_id,
               session_id=session_id,
               turn_id=stable_turn_id,
           ),
           tenant_id=project.company_id,
           actor_id=employee.id,
           trace_id=f"chat-turn:{session_id}:{stable_turn_id}",
       )

   The replay branches (``remember_completed``) must skip the call: those turns
   are terminal by construction and ``bind_turn`` would answer ``terminal``.

2. ``session_service._iter_send_events`` — the ``user_message`` frame and both
   ``_append_stream_event`` branches. The legacy ``agent_events.seq`` value is
   the natural stable ``frame_key``; the returned ``sequence`` then replaces the
   session-local ``seq`` that leaves on the wire::

       frame = await bridge.append_stream_frame(
           binding=binding,
           frame_key=f"agent-event:{seq}",
           event_type=normalized.type,
           payload=normalized.data,
       )
       yield {**normalized.to_dict(), "seq": frame.sequence,
              "run_id": binding.run_id}

3. ``session_service._detached_events`` — in the block that marks the
   ``AgentTurnRow`` ``completed``/``failed`` (and in the ``CancelledError``
   branch)::

       await bridge.finish_turn(
           binding=binding,
           outcome=TurnOutcome.SUCCEEDED,  # FAILED / CANCELLED
           failure_code=turn.error_code,
       )

4. ``web_facade_adapter.runtime_factory`` — **no change**. The existing
   ``DurableRunIndexProvider`` starts returning evidence as soon as (1)–(3)
   have written ``agent_runs`` + ``agent_control_plane_events`` rows.

   Note for that owner: ``_bound_run`` claims a live tail only when the session
   has exactly **one** run. One run per turn means a multi-turn chat has several,
   so live-tail mode stops applying and frame identity rests entirely on the
   durable ``(run_id, sequence)`` pairs produced by (2) — the stronger guarantee,
   but it requires every emitted frame to be persisted before it is emitted.

Known blocker on the frame path (precise, single, actionable)
-------------------------------------------------------------
``SqlAlchemyAgentControlPlaneRepository._validate_atomic_write`` accepts only the
fixed run-lifecycle operations: every ``agent_run`` aggregate advance must be one
of ``agent_run.create|start|wait_for_approval|resume|succeed|fail|cancel|timeout``
with the matching status transition, and
``ControlPlaneAggregateVersion.aggregate_type`` admits only
``agent_run|approval_request``. A stream frame is a *status-preserving* advance
(``running -> running``) and therefore cannot be expressed yet: the atomic port
rejects it with ``ValueError("run event type does not match the command
operation")``.

This module builds that write anyway (:func:`build_stream_frame_write`), so the
required extension is a small, mechanical contract change owned by the
control-plane package — add the ``agent_run.stream_frame`` operation to
``expected_run_events`` (same event name) and
``expected_run_transitions`` (``{(running, running), (waiting_approval,
waiting_approval)}``) in ``repository._validate_atomic_write``, and add the same
pair to ``contracts.validate_run_transition``/``RunTransitionOperation``.

Until that lands, ``stream_frames_enabled=False`` is the only honest setting:
``append_stream_frame`` then refuses with
``RUN_BINDING_STREAM_FRAME_UNSUPPORTED`` /
``agent_run_stream_frame_write_contract_missing`` instead of writing a frame the
index could not prove. ``bind_turn``/``finish_turn`` are unaffected — they use
operations the contract already supports, so the durable run itself is real
today.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Callable, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.agent_control_plane import (
    AgentControlPlaneTransactionPort,
    ControlPlaneAggregateVersion,
    ControlPlaneAtomicWrite,
    ControlPlaneLinkage,
    ControlPlaneWriteCommand,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
    DurableAgentControlPlane,
)
from prodavan.domain.agent.control_plane import AgentRunCreate, AgentRunStatus, EventEnvelope
from prodavan.domain.automation.outbox import OutboxEnqueueIntent, OutboxEvent

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
Clock = Callable[[], datetime]

#: Operation/event name of one durable stream frame. It advances the run
#: aggregate sequence without changing the run status (see the module docstring
#: for the contract extension that must accept it).
STREAM_FRAME_OPERATION = "agent_run.stream_frame"
STREAM_FRAME_EVENT_TYPE = "agent_run.stream_frame"
RUN_AGGREGATE_TYPE = "agent_run"

#: Statuses in which a run still owns the turn and may accept frames.
LIVE_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_APPROVAL,
    }
)
#: Frames are only appended while the turn is actually executing.
FRAMING_RUN_STATUSES = frozenset({AgentRunStatus.RUNNING})
TERMINAL_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }
)

#: Stable, non-secret prefixes of the three command keys of one turn. The first
#: one doubles as the durable binding key stored in ``agent_runs.idempotency_key``
#: (unique per project), which is what ``resolve_turn`` queries.
TURN_RUN_KEY_PREFIX = "turn-run"
TURN_START_KEY_PREFIX = "turn-start"
TURN_FINISH_KEY_PREFIX = "turn-finish"
_COMMAND_KEY_MAX = 128

#: Redacted reason codes. Nothing else about a failure leaves this module.
CODE_SCOPE_DENIED = "RUN_BINDING_SCOPE_DENIED"
CODE_NO_ACTIVE_RUN = "RUN_BINDING_NO_ACTIVE_RUN"
CODE_TURN_TERMINAL = "RUN_BINDING_TURN_TERMINAL"
CODE_RUN_IDENTITY_CONFLICT = "RUN_BINDING_RUN_IDENTITY_CONFLICT"
CODE_SEQUENCE_UNAVAILABLE = "RUN_BINDING_SEQUENCE_UNAVAILABLE"
CODE_SEQUENCE_CONFLICT = "RUN_BINDING_SEQUENCE_CONFLICT"
CODE_STREAM_FRAME_UNSUPPORTED = "RUN_BINDING_STREAM_FRAME_UNSUPPORTED"
CODE_ATOMIC_WRITE_REJECTED = "RUN_BINDING_ATOMIC_WRITE_REJECTED"
CODE_STORE_UNAVAILABLE = "RUN_BINDING_STORE_UNAVAILABLE"
CODE_STATUS_UNSUPPORTED = "RUN_BINDING_RUN_STATUS_UNSUPPORTED"
CODE_CONCURRENT_ACTIVE_RUN = "RUN_BINDING_CONCURRENT_ACTIVE_RUN"

BLOCKER_TURN_UNBOUND = "legacy_chat_turn_run_unbound"
BLOCKER_TENANT_SCOPE = "legacy_chat_turn_tenant_scope_denied"
BLOCKER_TURN_TERMINAL = "legacy_chat_turn_run_terminal"
BLOCKER_STREAM_FRAME_CONTRACT = "agent_run_stream_frame_write_contract_missing"
BLOCKER_SEQUENCE = "run_aggregate_sequence_unavailable"
BLOCKER_STORE = "run_binding_store_unavailable"

_UNAVAILABLE_DETAIL = (
    "the durable run binding for this chat turn is not available; "
    "the turn is refused instead of synthesising a run identity"
)


class RunBindingError(RuntimeError):
    """A fail-closed refusal of the run binding.

    ``code`` is a stable, redacted reason code; ``blocker`` names the single
    missing fact or part. Neither the message nor ``detail`` carries SQL, DSN,
    payload content or exception text from the underlying layer.
    """

    def __init__(self, code: str, blocker: str, *, detail: str | None = None) -> None:
        self.code = code
        self.blocker = blocker
        self.detail = detail or _UNAVAILABLE_DETAIL
        super().__init__(f"{code}:{blocker}")


class TurnOutcome(StrEnum):
    """Terminal outcome of a chat turn, mapped onto run lifecycle operations."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _required(value: str, name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return cleaned


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _command_key(prefix: str, *parts: str) -> str:
    """Bounded, stable command key.

    ``ControlPlaneWriteCommand`` allows 512 characters, but the durable receipt
    and ``agent_runs.idempotency_key`` are far tighter, so an over-long readable
    key degrades to a digest form instead of failing at the database.
    """
    readable = ":".join((prefix, *parts))
    if len(readable) <= _COMMAND_KEY_MAX:
        return readable
    return f"{prefix}:{_digest(*parts)[:48]}"


@dataclass(frozen=True, slots=True)
class TurnBindingKey:
    """The identity of one chat turn inside one session.

    ``turn_id`` is the legacy turn-ledger id (``agent_turns.turn_id``): stable
    per session and already deduplicated there, which makes it the natural
    binding key.
    """

    project_id: str
    session_id: str
    turn_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "project_id", _required(self.project_id, "project_id", max_length=64)
        )
        object.__setattr__(
            self, "session_id", _required(self.session_id, "session_id", max_length=64)
        )
        object.__setattr__(self, "turn_id", _required(self.turn_id, "turn_id", max_length=64))

    @property
    def run_command_key(self) -> str:
        """Idempotency key of ``agent_run.create`` **and** the binding lookup key."""
        return _command_key(TURN_RUN_KEY_PREFIX, self.session_id, self.turn_id)

    def start_command_key(self) -> str:
        return _command_key(TURN_START_KEY_PREFIX, self.session_id, self.turn_id)

    def finish_command_key(self, outcome: TurnOutcome) -> str:
        return _command_key(
            TURN_FINISH_KEY_PREFIX, self.session_id, self.turn_id, outcome.value
        )


@dataclass(frozen=True, slots=True)
class TurnRunBinding:
    """The durable run a chat turn is bound to.

    ``run_sequence`` is the run aggregate's last allocated durable sequence, so
    :meth:`frame_sequence` yields the next provable ``(run_id, sequence)`` pair
    without any local counter. ``actor_id``/``trace_id`` are carried because
    every subsequent atomic write must repeat the authenticated linkage that
    created the run.
    """

    run_id: str
    project_id: str
    session_id: str
    turn_id: str
    tenant_id: str
    actor_id: str
    status: AgentRunStatus
    run_sequence: int
    trace_id: str = ""
    frames_enabled: bool = False

    def __post_init__(self) -> None:
        if self.run_sequence < 1:
            raise ValueError("run_sequence must be positive")

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES

    @property
    def live(self) -> bool:
        return self.status in LIVE_RUN_STATUSES

    @property
    def framing_supported(self) -> bool:
        return self.frames_enabled and self.status in FRAMING_RUN_STATUSES

    @property
    def next_sequence(self) -> int:
        return self.run_sequence + 1

    def frame_sequence(self, ordinal: int) -> int:
        """Deterministic sequence of the ``ordinal``-th frame of this turn."""
        if ordinal < 1:
            raise ValueError("frame ordinal starts at 1")
        return self.run_sequence + ordinal

    @property
    def key(self) -> TurnBindingKey:
        return TurnBindingKey(
            project_id=self.project_id,
            session_id=self.session_id,
            turn_id=self.turn_id,
        )

    def with_status(self, status: AgentRunStatus, *, run_sequence: int) -> "TurnRunBinding":
        return TurnRunBinding(
            run_id=self.run_id,
            project_id=self.project_id,
            session_id=self.session_id,
            turn_id=self.turn_id,
            tenant_id=self.tenant_id,
            actor_id=self.actor_id,
            status=status,
            run_sequence=run_sequence,
            trace_id=self.trace_id,
            frames_enabled=self.frames_enabled,
        )


@dataclass(frozen=True, slots=True)
class StreamFrameRecord:
    """Durable identity of one persisted stream frame."""

    run_id: str
    sequence: int
    event_type: str
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class RunBindingScope:
    """Durable ownership facts read before any write is attempted."""

    tenant_id: str
    project_id: str
    session_id: str
    project_active: bool
    session_bound: bool


@dataclass(frozen=True, slots=True)
class DurableRunState:
    """One binding row as read back from ``agent_runs``."""

    run_id: str
    status: str


class RunBindingStore(Protocol):
    """Read-only durable seam used by :class:`DurableRunBindingBridge`.

    Reads only, and never mutating: a bridge that lacked a durable read would
    have to guess a run id, which the fail-closed rule forbids.
    """

    async def read_scope(self, *, key: TurnBindingKey) -> RunBindingScope | None: ...

    async def read_binding(self, *, key: TurnBindingKey, tenant_id: str) -> DurableRunState | None: ...

    async def read_run_sequence(self, *, run_id: str, tenant_id: str) -> int | None: ...

    async def read_frame_sequence(self, *, tenant_id: str, idempotency_key: str) -> int | None: ...


class SqlAlchemyRunBindingStore:
    """Production read seam over ``agent_runs`` + control-plane tables.

    Every run query proves ownership through ``agent_control_plane_run_scopes``;
    a run of another tenant is therefore indistinguishable from a missing run.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def read_scope(self, *, key: TurnBindingKey) -> RunBindingScope | None:
        from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
        from prodavan.infrastructure.persistence.models.projects import ProjectRow

        try:
            async with self._session_factory() as session:
                project = await session.scalar(
                    select(ProjectRow).where(ProjectRow.id == key.project_id)
                )
                if project is None:
                    return None
                agent_session = await session.scalar(
                    select(AgentSessionRow).where(AgentSessionRow.id == key.session_id)
                )
                session_project = getattr(agent_session, "project_id", None)
                return RunBindingScope(
                    tenant_id=str(project.company_id),
                    project_id=str(project.id),
                    session_id=key.session_id,
                    project_active=str(project.status) == "active",
                    session_bound=agent_session is not None
                    and str(session_project) == key.project_id,
                )
        except Exception as exc:  # noqa: BLE001 - redacted into a stable code
            logger.warning("run binding scope read failed error=%s", type(exc).__name__)
            raise RunBindingError(
                CODE_STORE_UNAVAILABLE,
                BLOCKER_STORE,
                detail="the durable turn scope could not be read",
            ) from exc

    async def read_binding(self, *, key: TurnBindingKey, tenant_id: str) -> DurableRunState | None:
        from prodavan.infrastructure.persistence.agent_control_plane.models import (
            AgentControlPlaneRunScopeRow,
        )
        from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow

        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(AgentRunRow.id, AgentRunRow.status)
                        .join(
                            AgentControlPlaneRunScopeRow,
                            AgentControlPlaneRunScopeRow.run_id == AgentRunRow.id,
                        )
                        .where(
                            AgentRunRow.project_id == key.project_id,
                            AgentRunRow.session_id == key.session_id,
                            AgentRunRow.idempotency_key == key.run_command_key,
                            AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                        )
                        .order_by(AgentRunRow.created_at.desc(), AgentRunRow.id.desc())
                        .limit(1)
                    )
                ).first()
        except Exception as exc:  # noqa: BLE001 - redacted into a stable code
            logger.warning("run binding read failed error=%s", type(exc).__name__)
            raise RunBindingError(
                CODE_STORE_UNAVAILABLE,
                BLOCKER_STORE,
                detail="the durable turn binding could not be read",
            ) from exc
        if row is None:
            return None
        return DurableRunState(run_id=str(row[0]), status=str(row[1]))

    async def read_run_sequence(self, *, run_id: str, tenant_id: str) -> int | None:
        from prodavan.infrastructure.persistence.agent_control_plane.models import (
            AgentControlPlaneAggregateRow,
            AgentControlPlaneRunScopeRow,
        )

        try:
            async with self._session_factory() as session:
                value = await session.scalar(
                    select(AgentControlPlaneAggregateRow.last_sequence)
                    .join(
                        AgentControlPlaneRunScopeRow,
                        AgentControlPlaneRunScopeRow.run_id
                        == AgentControlPlaneAggregateRow.aggregate_id,
                    )
                    .where(
                        AgentControlPlaneAggregateRow.aggregate_type == RUN_AGGREGATE_TYPE,
                        AgentControlPlaneAggregateRow.aggregate_id == run_id,
                        AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - redacted into a stable code
            logger.warning("run aggregate sequence read failed error=%s", type(exc).__name__)
            raise RunBindingError(
                CODE_SEQUENCE_UNAVAILABLE,
                BLOCKER_SEQUENCE,
                detail="the run aggregate sequence could not be read",
            ) from exc
        return int(value) if value is not None else None

    async def read_frame_sequence(self, *, tenant_id: str, idempotency_key: str) -> int | None:
        from prodavan.infrastructure.persistence.agent_control_plane.models import (
            AgentControlPlaneEventRow,
        )

        try:
            async with self._session_factory() as session:
                value = await session.scalar(
                    select(AgentControlPlaneEventRow.sequence)
                    .where(
                        AgentControlPlaneEventRow.tenant_id == tenant_id,
                        AgentControlPlaneEventRow.idempotency_key == idempotency_key,
                        AgentControlPlaneEventRow.aggregate_type == RUN_AGGREGATE_TYPE,
                    )
                    .order_by(AgentControlPlaneEventRow.sequence.desc())
                    .limit(1)
                )
        except Exception as exc:  # noqa: BLE001 - redacted into a stable code
            logger.warning("run frame sequence read failed error=%s", type(exc).__name__)
            raise RunBindingError(
                CODE_SEQUENCE_UNAVAILABLE,
                BLOCKER_SEQUENCE,
                detail="the durable stream frame sequence could not be read",
            ) from exc
        return int(value) if value is not None else None


def frame_idempotency_key(
    *,
    tenant_id: str,
    session_id: str,
    turn_id: str,
    frame_key: str,
) -> str:
    """Content-derived, sequence-free key of one durable stream frame.

    Sequence-free on purpose: a re-delivered frame must resolve to the sequence
    already allocated for it, so the key may not contain the sequence it is about
    to claim. ``EventEnvelope.idempotency_key`` and
    ``AgentControlPlaneEventRow.idempotency_key`` both cap at 128 characters,
    hence the digest form.
    """
    return "chat-frame:" + _digest("frame", tenant_id, session_id, turn_id, frame_key)


def build_stream_frame_write(
    *,
    binding: TurnRunBinding,
    frame_key: str,
    event_type: str,
    payload: Mapping[str, Any] | None,
    expected_sequence: int,
    occurred_at: datetime,
    queue_name: str,
    producer: str,
) -> ControlPlaneAtomicWrite:
    """Build the single atomic write that durably records one stream frame.

    The write advances the ``agent_run`` aggregate by exactly one sequence and
    keeps the run status unchanged, which is what makes ``(run_id, sequence)``
    provable for the canonical stream index. It is a complete
    :class:`ControlPlaneAtomicWrite` (event + outbox projection + queue intent +
    transition key), so a repository that accepts ``agent_run.stream_frame``
    commits it exactly like any other transition.
    """
    if expected_sequence < 1:
        raise ValueError("expected_sequence must be positive")
    key = frame_idempotency_key(
        tenant_id=binding.tenant_id,
        session_id=binding.session_id,
        turn_id=binding.turn_id,
        frame_key=frame_key,
    )
    sequence = expected_sequence + 1
    status = binding.status.value
    linkage = ControlPlaneLinkage(
        tenant_id=binding.tenant_id,
        actor_id=binding.actor_id,
        project_id=binding.project_id,
        session_id=binding.session_id,
        trace_id=binding.trace_id or f"chat-turn:{binding.session_id}:{binding.turn_id}",
    )
    body = {
        # Redacted evidence only: the frame payload never reaches the audit
        # trail (prompt text, tool output, file bytes).
        "from_status": status,
        "status": status,
        "reason": None,
        "failure_code": None,
        "output_payload": None,
        "frame_key_digest": _digest("frame-key", frame_key),
        "frame_event_type": str(event_type)[:128],
        "frame_payload_digest": _payload_digest(payload),
        "control_plane": {
            "tenant_id": linkage.tenant_id,
            "actor_id": linkage.actor_id,
            "project_id": linkage.project_id,
            "session_id": linkage.session_id,
            "trace_id": linkage.trace_id,
            "command_idempotency_key": key,
        },
    }
    command = ControlPlaneWriteCommand(
        operation=STREAM_FRAME_OPERATION,
        command_idempotency_key=key,
        linkage=linkage,
        run_id=binding.run_id,
        aggregates=(
            ControlPlaneAggregateVersion(
                aggregate_type=RUN_AGGREGATE_TYPE,
                aggregate_id=binding.run_id,
                expected_sequence=expected_sequence,
                next_sequence=sequence,
            ),
        ),
        expected_run_status=binding.status,
        target_run_status=binding.status,
        details={
            "frame_key_digest": body["frame_key_digest"],
            "frame_event_type": body["frame_event_type"],
            "frame_sequence": sequence,
            "frame_payload_digest": body["frame_payload_digest"],
            "reason": None,
            "failure_code": None,
            "output_payload": None,
        },
    )
    transition_key = _frame_transition_key(
        tenant_id=binding.tenant_id,
        run_id=binding.run_id,
        sequence=sequence,
        command_key=key,
    )
    event = EventEnvelope(
        event_id=_frame_event_id(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            sequence=sequence,
            transition_key=transition_key,
        ),
        event_type=STREAM_FRAME_EVENT_TYPE,
        occurred_at=occurred_at,
        producer=producer,
        aggregate_type=RUN_AGGREGATE_TYPE,
        aggregate_id=binding.run_id,
        run_id=binding.run_id,
        session_id=binding.session_id,
        correlation_id=_frame_correlation_id(key),
        idempotency_key=transition_key,
        sequence=sequence,
        payload=body,
    )
    aggregate_key = OutboxEvent.aggregate_event_key(
        tenant_id=binding.tenant_id,
        aggregate_type=RUN_AGGREGATE_TYPE,
        aggregate_id=binding.run_id,
        aggregate_version=sequence,
    )
    outbox_event = OutboxEvent(
        event_id=str(event.event_id),
        tenant_id=binding.tenant_id,
        aggregate_type=RUN_AGGREGATE_TYPE,
        aggregate_id=binding.run_id,
        aggregate_version=sequence,
        event_type=event.event_type,
        payload=event.model_dump(mode="json"),
        occurred_at=occurred_at,
        idempotency_key=aggregate_key,
    )
    return ControlPlaneAtomicWrite(
        command=command,
        events=(event,),
        outbox_events=(outbox_event,),
        outbox_intents=(
            OutboxEnqueueIntent(
                event_id=outbox_event.event_id,
                queue_name=queue_name,
                deduplication_key=aggregate_key,
            ),
        ),
        transition_idempotency_keys=(transition_key,),
    )


class DurableRunBindingBridge:
    """Create, resolve and advance the durable run of a legacy chat turn.

    The bridge owns no state: every answer (including "this turn has no run") is
    read from, and every change is written to, the durable control plane.
    """

    def __init__(
        self,
        *,
        transaction: AgentControlPlaneTransactionPort,
        store: RunBindingStore,
        queue_name: str = "agent-control-plane",
        producer: str = "agent-run-binding",
        clock: Clock | None = None,
        stream_frames_enabled: bool = False,
    ) -> None:
        if not callable(getattr(transaction, "commit", None)):
            raise TypeError("transaction must expose async commit(write=...)")
        if not queue_name.strip():
            raise ValueError("queue_name must not be empty")
        if not producer.strip():
            raise ValueError("producer must not be empty")
        self._transaction = transaction
        self._store = store
        self._queue_name = queue_name.strip()
        self._producer = producer.strip()
        self._clock: Clock = clock or (lambda: datetime.now(UTC))
        #: The durable stream-frame write needs the contract extension named in
        #: the module docstring. Off by default: a frame the index could not prove
        #: is worse than a named refusal.
        self._stream_frames_enabled = bool(stream_frames_enabled)

    # -- resolution -----------------------------------------------------------

    async def resolve_turn(
        self,
        *,
        key: TurnBindingKey,
        tenant_id: str,
        actor_id: str = "",
        trace_id: str | None = None,
    ) -> TurnRunBinding:
        """Resolve ``(project, session, turn) -> run`` strictly from the database.

        Never creates, never derives an id, never falls back to memory: a turn
        without a durable run is refused with ``RUN_BINDING_NO_ACTIVE_RUN``.
        ``actor_id`` is optional for a pure read, but a binding that is later
        advanced (frame/finish) must carry the authenticated actor, otherwise the
        control-plane linkage cannot be reconstructed and the operation refuses.
        """
        tenant = _required(tenant_id, "tenant_id", max_length=64)
        state = await self._store.read_binding(key=key, tenant_id=tenant)
        if state is None:
            raise RunBindingError(
                CODE_NO_ACTIVE_RUN,
                BLOCKER_TURN_UNBOUND,
                detail="no durable agent run is bound to this chat turn",
            )
        return await self._binding_from_state(
            key=key,
            tenant_id=tenant,
            state=state,
            trace_id=(trace_id or "").strip(),
            actor_id=actor_id.strip(),
        )

    # -- turn start -----------------------------------------------------------

    async def bind_turn(
        self,
        *,
        key: TurnBindingKey,
        tenant_id: str,
        actor_id: str,
        trace_id: str | None = None,
        entrypoint: str = "legacy_chat_turn",
    ) -> TurnRunBinding:
        """Bind a starting turn to exactly one durable run.

        * an existing binding is reused — a replay never creates a second run;
        * a terminal binding is returned as terminal — a finished turn never gets
          a new run;
        * a foreign tenant, an inactive project, or a session that does not
          belong to the routed project is refused before any write;
        * a run of another turn still active on the same session is refused (the
          caller must finish that turn first): two overlapping runs would make
          the stream index ambiguous.
        """
        tenant = _required(tenant_id, "tenant_id", max_length=64)
        actor = _required(actor_id, "actor_id", max_length=128)
        trace = (trace_id or "").strip() or f"chat-turn:{key.session_id}:{key.turn_id}"
        scope = await self._read_scope(key)
        self._require_scope(scope, tenant_id=tenant)

        state = await self._store.read_binding(key=key, tenant_id=tenant)
        if state is not None:
            binding = await self._binding_from_state(
                key=key, tenant_id=tenant, state=state, trace_id=trace, actor_id=actor
            )
            if binding.terminal or binding.status is not AgentRunStatus.QUEUED:
                return binding
            return await self._start(binding=binding)

        await self._refuse_if_other_active_run(key=key, tenant_id=tenant)

        control_plane = self._control_plane(tenant)
        created = await control_plane.submit_run(
            AgentRunCreate(
                project_id=key.project_id,
                session_id=key.session_id,
                requested_by_employee_id=actor,
                idempotency_key=key.run_command_key,
                # Metadata only: the prompt never enters the run record.
                input_payload={
                    "turn_id": key.turn_id,
                    "session_id": key.session_id,
                    "entrypoint": entrypoint[:128],
                },
            ),
            linkage=self._linkage(key=key, tenant_id=tenant, actor_id=actor, trace_id=trace),
        )
        if created.status is ControlPlaneWriteStatus.BLOCKED:
            raise RunBindingError(
                CODE_SCOPE_DENIED,
                BLOCKER_TENANT_SCOPE,
                detail="the turn is outside the durable tenant/project scope",
            )
        if created.status is ControlPlaneWriteStatus.CONFLICT:
            # A concurrent writer may have won the identity; the durable row is
            # authoritative, so re-read instead of inventing a run.
            state = await self._store.read_binding(key=key, tenant_id=tenant)
            if state is None:
                raise RunBindingError(
                    CODE_RUN_IDENTITY_CONFLICT,
                    BLOCKER_TURN_UNBOUND,
                    detail="the durable run identity for this turn is already taken",
                )
            binding = await self._binding_from_state(
                key=key, tenant_id=tenant, state=state, trace_id=trace, actor_id=actor
            )
            if binding.terminal or binding.status is not AgentRunStatus.QUEUED:
                return binding
            return await self._start(binding=binding)
        if created.status not in {
            ControlPlaneWriteStatus.APPLIED,
            ControlPlaneWriteStatus.DUPLICATE,
        }:
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the durable run could not be created",
            )
        if created.run is None or not created.run.id:
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the durable run creation returned no run identity",
            )
        sequence = await self._require_sequence(run_id=created.run.id, tenant_id=tenant)
        binding = TurnRunBinding(
            run_id=created.run.id,
            project_id=key.project_id,
            session_id=key.session_id,
            turn_id=key.turn_id,
            tenant_id=tenant,
            actor_id=actor,
            status=created.run.status,
            run_sequence=sequence,
            trace_id=trace,
            frames_enabled=self._stream_frames_enabled,
        )
        if binding.terminal:
            return binding
        return await self._start(binding=binding)

    # -- frames ---------------------------------------------------------------

    async def append_stream_frame(
        self,
        *,
        binding: TurnRunBinding,
        frame_key: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> StreamFrameRecord:
        """Allocate the durable run sequence of one stream frame.

        Idempotent by ``frame_key`` (a stable identity of the frame inside the
        turn, e.g. the legacy ``agent_events.seq``): a re-delivered frame gets the
        sequence already allocated to it and produces no second row.
        """
        key_text = _required(frame_key, "frame_key", max_length=255)
        event_text = _required(event_type, "event_type", max_length=128)
        self._require_actor(binding)
        if binding.terminal:
            raise RunBindingError(
                CODE_TURN_TERMINAL,
                BLOCKER_TURN_TERMINAL,
                detail="the turn's run is already terminal; a frame cannot be appended",
            )
        if not binding.live:
            raise RunBindingError(
                CODE_NO_ACTIVE_RUN,
                BLOCKER_TURN_UNBOUND,
                detail="the turn's run is not active",
            )
        if not self._stream_frames_enabled:
            raise RunBindingError(
                CODE_STREAM_FRAME_UNSUPPORTED,
                BLOCKER_STREAM_FRAME_CONTRACT,
                detail=(
                    "the control-plane atomic write does not accept a status-preserving "
                    "agent_run.stream_frame transition yet; see the module docstring"
                ),
            )
        if binding.status not in FRAMING_RUN_STATUSES:
            raise RunBindingError(
                CODE_STATUS_UNSUPPORTED,
                BLOCKER_STREAM_FRAME_CONTRACT,
                detail="frames are only appended to a running run",
            )

        idempotency_key = frame_idempotency_key(
            tenant_id=binding.tenant_id,
            session_id=binding.session_id,
            turn_id=binding.turn_id,
            frame_key=key_text,
        )
        already = await self._store.read_frame_sequence(
            tenant_id=binding.tenant_id, idempotency_key=idempotency_key
        )
        if already is not None:
            return StreamFrameRecord(
                run_id=binding.run_id,
                sequence=already,
                event_type=event_text,
                duplicate=True,
            )

        expected = await self._require_sequence(
            run_id=binding.run_id, tenant_id=binding.tenant_id
        )
        write = build_stream_frame_write(
            binding=binding,
            frame_key=key_text,
            event_type=event_text,
            payload=payload,
            expected_sequence=expected,
            occurred_at=self._clock(),
            queue_name=self._queue_name,
            producer=self._producer,
        )
        return await self._commit_frame(
            write=write,
            binding=binding,
            event_type=event_text,
            idempotency_key=idempotency_key,
        )

    # -- turn end -------------------------------------------------------------

    async def finish_turn(
        self,
        *,
        binding: TurnRunBinding,
        outcome: TurnOutcome,
        failure_code: str | None = None,
        reason: str | None = None,
        output_payload: Mapping[str, Any] | None = None,
    ) -> TurnRunBinding:
        """Move the turn's run to its terminal status (idempotently).

        A run that is already terminal is returned unchanged: a finished turn is
        terminal, and a retried completion never creates a new run.
        """
        self._require_actor(binding)
        if binding.terminal:
            return binding
        if binding.status not in LIVE_RUN_STATUSES:
            raise RunBindingError(
                CODE_STATUS_UNSUPPORTED,
                BLOCKER_TURN_UNBOUND,
                detail="the turn's run status cannot be terminated",
            )
        if outcome is TurnOutcome.SUCCEEDED and binding.status is not AgentRunStatus.RUNNING:
            raise RunBindingError(
                CODE_STATUS_UNSUPPORTED,
                BLOCKER_TURN_UNBOUND,
                detail="only a running run can succeed",
            )
        expected = await self._require_sequence(
            run_id=binding.run_id, tenant_id=binding.tenant_id
        )
        control_plane = self._control_plane(binding.tenant_id)
        linkage = self._linkage(
            key=binding.key,
            tenant_id=binding.tenant_id,
            actor_id=binding.actor_id,
            trace_id=binding.trace_id,
        )
        command_key = binding.key.finish_command_key(outcome)
        if outcome is TurnOutcome.SUCCEEDED:
            result = await control_plane.succeed_run(
                run_id=binding.run_id,
                linkage=linkage,
                expected_sequence=expected,
                command_idempotency_key=command_key,
                output_payload=output_payload,
            )
        elif outcome is TurnOutcome.FAILED:
            result = await control_plane.fail_run(
                run_id=binding.run_id,
                linkage=linkage,
                expected_sequence=expected,
                expected_status=binding.status,
                command_idempotency_key=command_key,
                failure_code=_bounded_failure_code(failure_code),
                reason=reason,
            )
        else:
            result = await control_plane.cancel_run(
                run_id=binding.run_id,
                linkage=linkage,
                expected_sequence=expected,
                expected_status=binding.status,
                command_idempotency_key=command_key,
                reason=reason,
            )
        return await self._settle_terminal(result=result, fallback=binding)

    # -- internals ------------------------------------------------------------

    def _control_plane(self, tenant_id: str) -> DurableAgentControlPlane:
        return DurableAgentControlPlane(
            transaction=self._transaction,
            tenant_id=tenant_id,
            queue_name=self._queue_name,
            producer=self._producer,
            clock=self._clock,
        )

    async def _atomic(self, awaitable: Any) -> ControlPlaneWriteResult:
        """Await one control-plane write, mapping every failure to a named code.

        ``DurableAgentControlPlane`` raises for a rejected command
        (``ValueError``), for a failed port call (``ControlPlaneWriteError``) and
        for an inconsistent port result (``ControlPlaneResultError``). None of
        those may escape as an opaque exception or degrade into a fallback write.
        """
        try:
            return await awaitable
        except RunBindingError:
            raise
        except ValueError as exc:
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the control-plane command was rejected by its contract",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - redacted into a stable code
            logger.warning("run binding atomic write failed error=%s", type(exc).__name__)
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the control-plane write failed",
            ) from exc

    async def _commit_frame(
        self,
        *,
        write: ControlPlaneAtomicWrite,
        binding: TurnRunBinding,
        event_type: str,
        idempotency_key: str,
    ) -> StreamFrameRecord:
        try:
            result = await self._transaction.commit(write=write)
        except ValueError as exc:
            # The repository validates the operation shape before touching the
            # database; an unsupported transition is a named blocker, never a
            # silently dropped frame.
            raise RunBindingError(
                CODE_STREAM_FRAME_UNSUPPORTED,
                BLOCKER_STREAM_FRAME_CONTRACT,
                detail=(
                    "the control-plane atomic write rejected the stream frame "
                    "transition; see the run_binding module docstring"
                ),
            ) from exc
        except Exception as exc:  # noqa: BLE001 - redacted into a stable code
            logger.warning("stream frame write failed error=%s", type(exc).__name__)
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the durable stream frame could not be written",
            ) from exc
        if not isinstance(result, ControlPlaneWriteResult):
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the durable stream frame returned an invalid result",
            )
        if result.status is ControlPlaneWriteStatus.CONFLICT:
            again = await self._store.read_frame_sequence(
                tenant_id=binding.tenant_id, idempotency_key=idempotency_key
            )
            if again is not None:
                return StreamFrameRecord(
                    run_id=binding.run_id,
                    sequence=again,
                    event_type=event_type,
                    duplicate=True,
                )
            raise RunBindingError(
                CODE_SEQUENCE_CONFLICT,
                BLOCKER_SEQUENCE,
                detail="the run aggregate sequence advanced concurrently",
            )
        if result.status not in {
            ControlPlaneWriteStatus.APPLIED,
            ControlPlaneWriteStatus.DUPLICATE,
        }:
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the durable stream frame was not committed",
            )
        if result.run is None or result.run.id != binding.run_id:
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the committed stream frame names a different run",
            )
        if len(result.event_results) != 1:
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the committed stream frame returned no single event",
            )
        record = result.event_results[0]
        if record.aggregate_type != RUN_AGGREGATE_TYPE or record.aggregate_id != binding.run_id:
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the committed stream frame is not a run aggregate event",
            )
        return StreamFrameRecord(
            run_id=binding.run_id,
            sequence=int(record.aggregate_version),
            event_type=event_type,
            duplicate=result.status is ControlPlaneWriteStatus.DUPLICATE,
        )

    async def _start(self, *, binding: TurnRunBinding) -> TurnRunBinding:
        """Move a queued run to running (idempotent) so frames have a live owner."""
        control_plane = self._control_plane(binding.tenant_id)
        result = await control_plane.start_run(
            run_id=binding.run_id,
            linkage=self._linkage(
                key=binding.key,
                tenant_id=binding.tenant_id,
                actor_id=binding.actor_id,
                trace_id=binding.trace_id,
            ),
            expected_sequence=binding.run_sequence,
            command_idempotency_key=binding.key.start_command_key(),
        )
        if result.status is ControlPlaneWriteStatus.BLOCKED:
            raise RunBindingError(
                CODE_SCOPE_DENIED,
                BLOCKER_TENANT_SCOPE,
                detail="the turn is outside the durable tenant/project scope",
            )
        if result.status is ControlPlaneWriteStatus.CONFLICT:
            # Another writer started it (or the cursor moved): re-read and trust
            # only what the durable row says.
            state = await self._store.read_binding(
                key=binding.key, tenant_id=binding.tenant_id
            )
            if state is None:
                raise RunBindingError(
                    CODE_NO_ACTIVE_RUN,
                    BLOCKER_TURN_UNBOUND,
                    detail="the durable run disappeared while starting",
                )
            return await self._binding_from_state(
                key=binding.key,
                tenant_id=binding.tenant_id,
                state=state,
                trace_id=binding.trace_id,
                actor_id=binding.actor_id,
            )
        if result.status not in {
            ControlPlaneWriteStatus.APPLIED,
            ControlPlaneWriteStatus.DUPLICATE,
        }:
            raise RunBindingError(
                CODE_ATOMIC_WRITE_REJECTED,
                BLOCKER_TURN_UNBOUND,
                detail="the durable run could not be started",
            )
        sequence = await self._require_sequence(
            run_id=binding.run_id, tenant_id=binding.tenant_id
        )
        return binding.with_status(
            result.run.status if result.run is not None else AgentRunStatus.RUNNING,
            run_sequence=sequence,
        )

    async def _settle_terminal(
        self,
        *,
        result: ControlPlaneWriteResult,
        fallback: TurnRunBinding,
    ) -> TurnRunBinding:
        if result.status in {
            ControlPlaneWriteStatus.APPLIED,
            ControlPlaneWriteStatus.DUPLICATE,
        }:
            sequence = await self._require_sequence(
                run_id=fallback.run_id, tenant_id=fallback.tenant_id
            )
            status = (
                result.run.status if result.run is not None else AgentRunStatus.SUCCEEDED
            )
            return fallback.with_status(status, run_sequence=sequence)
        if result.status is ControlPlaneWriteStatus.CONFLICT:
            state = await self._store.read_binding(
                key=fallback.key, tenant_id=fallback.tenant_id
            )
            if state is not None:
                return await self._binding_from_state(
                    key=fallback.key,
                    tenant_id=fallback.tenant_id,
                    state=state,
                    trace_id=fallback.trace_id,
                    actor_id=fallback.actor_id,
                )
        if result.status is ControlPlaneWriteStatus.BLOCKED:
            raise RunBindingError(
                CODE_SCOPE_DENIED,
                BLOCKER_TENANT_SCOPE,
                detail="the turn is outside the durable tenant/project scope",
            )
        raise RunBindingError(
            CODE_ATOMIC_WRITE_REJECTED,
            BLOCKER_TURN_UNBOUND,
            detail="the terminal run transition was not applied",
        )

    async def _binding_from_state(
        self,
        *,
        key: TurnBindingKey,
        tenant_id: str,
        state: DurableRunState,
        trace_id: str = "",
        actor_id: str = "",
    ) -> TurnRunBinding:
        try:
            status = AgentRunStatus(state.status)
        except ValueError as exc:
            raise RunBindingError(
                CODE_STATUS_UNSUPPORTED,
                BLOCKER_TURN_UNBOUND,
                detail="the durable run carries an unknown status",
            ) from exc
        sequence = await self._require_sequence(run_id=state.run_id, tenant_id=tenant_id)
        return TurnRunBinding(
            run_id=state.run_id,
            project_id=key.project_id,
            session_id=key.session_id,
            turn_id=key.turn_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            status=status,
            run_sequence=sequence,
            trace_id=trace_id,
            frames_enabled=self._stream_frames_enabled,
        )

    async def _read_scope(self, key: TurnBindingKey) -> RunBindingScope:
        scope = await self._store.read_scope(key=key)
        if scope is None:
            raise RunBindingError(
                CODE_SCOPE_DENIED,
                BLOCKER_TENANT_SCOPE,
                detail="the project of this turn does not exist",
            )
        return scope

    @staticmethod
    def _require_scope(scope: RunBindingScope, *, tenant_id: str) -> None:
        """Tenant, project and session must all agree with the routed turn."""
        if scope.tenant_id != tenant_id:
            raise RunBindingError(
                CODE_SCOPE_DENIED,
                BLOCKER_TENANT_SCOPE,
                detail="the turn's project belongs to another tenant",
            )
        if not scope.project_active or not scope.session_bound:
            raise RunBindingError(
                CODE_SCOPE_DENIED,
                BLOCKER_TENANT_SCOPE,
                detail="the turn is outside an active project/session scope",
            )

    async def _refuse_if_other_active_run(
        self, *, key: TurnBindingKey, tenant_id: str
    ) -> None:
        """Two overlapping runs on one session would make the index ambiguous.

        The check is optional on the store (a store that cannot answer it simply
        does not block); when it can answer, a second run is refused instead of
        silently splitting one session's frames across two runs.
        """
        reader = getattr(self._store, "read_active_run", None)
        if reader is None:
            return
        other = await reader(
            project_id=key.project_id,
            session_id=key.session_id,
            tenant_id=tenant_id,
            exclude_binding_key=key.run_command_key,
        )
        if other is None:
            return
        raise RunBindingError(
            CODE_CONCURRENT_ACTIVE_RUN,
            BLOCKER_TURN_UNBOUND,
            detail=(
                "another turn of this session still owns an active run; "
                "finish that turn as cancelled before binding a new one"
            ),
        )

    @staticmethod
    def _require_actor(binding: TurnRunBinding) -> None:
        """A second write of one run must repeat the actor that created it.

        ``ControlPlaneLinkage`` cannot be reconstructed without it, and a
        binding read by ``resolve_turn`` (a pure read) may legitimately not carry
        one — so the refusal is named instead of letting the contract raise.
        """
        if not binding.actor_id.strip():
            raise RunBindingError(
                CODE_SCOPE_DENIED,
                BLOCKER_TENANT_SCOPE,
                detail=(
                    "the turn binding carries no authenticated actor; "
                    "resolve it with actor_id before advancing the run"
                ),
            )

    async def _require_sequence(self, *, run_id: str, tenant_id: str) -> int:
        sequence = await self._store.read_run_sequence(run_id=run_id, tenant_id=tenant_id)
        if sequence is None:
            raise RunBindingError(
                CODE_SEQUENCE_UNAVAILABLE,
                BLOCKER_SEQUENCE,
                detail="the run aggregate has no durable sequence cursor",
            )
        return sequence

    @staticmethod
    def _linkage(
        *,
        key: TurnBindingKey,
        tenant_id: str,
        actor_id: str,
        trace_id: str,
    ) -> ControlPlaneLinkage:
        trace = (trace_id or "").strip() or f"chat-turn:{key.session_id}:{key.turn_id}"
        return ControlPlaneLinkage(
            tenant_id=tenant_id,
            actor_id=actor_id,
            project_id=key.project_id,
            session_id=key.session_id,
            trace_id=trace[:160],
        )


def _bounded_failure_code(value: str | None) -> str:
    cleaned = (value or "agent_run.failed").strip() or "agent_run.failed"
    return cleaned[:128]


def _payload_digest(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return _digest("payload", "")
    try:
        canonical = json.dumps(
            _plain(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    except (TypeError, ValueError):
        # An unserialisable payload still gets a stable marker rather than
        # leaking its content into the audit trail.
        return _digest("payload", "unserialisable")
    return _digest("payload", canonical)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _frame_transition_key(
    *, tenant_id: str, run_id: str, sequence: int, command_key: str
) -> str:
    return "chat-frame-transition:" + _digest(
        "transition", tenant_id, RUN_AGGREGATE_TYPE, run_id, str(sequence), command_key
    )


def _frame_event_id(
    *, tenant_id: str, run_id: str, sequence: int, transition_key: str
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"prodavan://agent-run-binding/{tenant_id}/{RUN_AGGREGATE_TYPE}/{run_id}/{sequence}/{transition_key}",
    )


def _frame_correlation_id(command_key: str) -> UUID:
    return UUID(
        bytes=hashlib.sha256(f"correlation\x00{command_key}".encode("utf-8")).digest()[:16]
    )


__all__ = [
    "BLOCKER_SEQUENCE",
    "BLOCKER_STORE",
    "BLOCKER_STREAM_FRAME_CONTRACT",
    "BLOCKER_TENANT_SCOPE",
    "BLOCKER_TURN_TERMINAL",
    "BLOCKER_TURN_UNBOUND",
    "CODE_ATOMIC_WRITE_REJECTED",
    "CODE_CONCURRENT_ACTIVE_RUN",
    "CODE_NO_ACTIVE_RUN",
    "CODE_RUN_IDENTITY_CONFLICT",
    "CODE_SCOPE_DENIED",
    "CODE_SEQUENCE_CONFLICT",
    "CODE_SEQUENCE_UNAVAILABLE",
    "CODE_STATUS_UNSUPPORTED",
    "CODE_STORE_UNAVAILABLE",
    "CODE_STREAM_FRAME_UNSUPPORTED",
    "CODE_TURN_TERMINAL",
    "DurableRunBindingBridge",
    "DurableRunState",
    "FRAMING_RUN_STATUSES",
    "LIVE_RUN_STATUSES",
    "RUN_AGGREGATE_TYPE",
    "RunBindingError",
    "RunBindingScope",
    "RunBindingStore",
    "STREAM_FRAME_EVENT_TYPE",
    "STREAM_FRAME_OPERATION",
    "SqlAlchemyRunBindingStore",
    "StreamFrameRecord",
    "TERMINAL_RUN_STATUSES",
    "TurnBindingKey",
    "TurnOutcome",
    "TurnRunBinding",
    "build_stream_frame_write",
    "frame_idempotency_key",
]
