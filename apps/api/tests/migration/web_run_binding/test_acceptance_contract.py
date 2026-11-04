"""MIG-WEB-RUNBIND-01 acceptance contract; deliberately not executed."""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.agent.run_binding import (
    BLOCKER_SEQUENCE,
    BLOCKER_STREAM_FRAME_CONTRACT,
    BLOCKER_TENANT_SCOPE,
    BLOCKER_TURN_TERMINAL,
    BLOCKER_TURN_UNBOUND,
    CODE_ATOMIC_WRITE_REJECTED,
    CODE_CONCURRENT_ACTIVE_RUN,
    CODE_NO_ACTIVE_RUN,
    CODE_SCOPE_DENIED,
    CODE_SEQUENCE_UNAVAILABLE,
    CODE_STREAM_FRAME_UNSUPPORTED,
    CODE_TURN_TERMINAL,
    RUN_AGGREGATE_TYPE,
    STREAM_FRAME_EVENT_TYPE,
    STREAM_FRAME_OPERATION,
    DurableRunBindingBridge,
    DurableRunState,
    RunBindingError,
    RunBindingScope,
    TurnBindingKey,
    TurnOutcome,
    build_stream_frame_write,
    frame_idempotency_key,
)
from prodavan.application.agent_control_plane import (
    AtomicEventOutboxResult,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
)
from prodavan.domain.agent.control_plane import AgentRunStatus, AgentRunView

from .acceptance_fixtures import (
    ACTOR,
    CASES,
    NOW,
    OTHER_PROJECT,
    OTHER_SESSION,
    OTHER_TENANT,
    PROJECT,
    SESSION,
    TENANT,
    TRACE,
    TURN_A,
    TURN_B,
)

LIVE = {
    AgentRunStatus.QUEUED.value,
    AgentRunStatus.RUNNING.value,
    AgentRunStatus.WAITING_APPROVAL.value,
}
TERMINAL = {
    AgentRunStatus.SUCCEEDED.value,
    AgentRunStatus.FAILED.value,
    AgentRunStatus.CANCELLED.value,
}

#: Exactly the lifecycle operations today's repository accepts, with the event
#: name and the source status set it requires (see
#: ``repository._validate_atomic_write``).
_LIFECYCLE: dict[str, tuple[str, object]] = {
    "agent_run.create": ("agent_run.created", None),
    "agent_run.start": ("agent_run.started", {AgentRunStatus.QUEUED}),
    "agent_run.succeed": ("agent_run.succeeded", {AgentRunStatus.RUNNING}),
    "agent_run.fail": ("agent_run.failed", {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING}),
    "agent_run.cancel": ("agent_run.cancelled", {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING}),
}


class DoublesAtomicPort:
    """One object acting as the control-plane atomic port **and** its store.

    ``stream_frames_supported`` mirrors the production contract of
    ``SqlAlchemyAgentControlPlaneRepository._validate_atomic_write``: when it is
    ``False`` (today's behaviour) a status-preserving ``agent_run.stream_frame``
    write is rejected with ``ValueError`` before anything is persisted.
    """

    def __init__(self, *, stream_frames_supported: bool = True) -> None:
        self.stream_frames_supported = stream_frames_supported
        self.projects: dict[str, tuple[str, bool]] = {PROJECT: (TENANT, True)}
        self.sessions: dict[str, str] = {SESSION: PROJECT}
        self.runs: dict[str, dict] = {}
        self.aggregates: dict[tuple[str, str], tuple[int, str]] = {}
        self.events: dict[tuple[str, str], int] = {}
        self.event_rows: list = []
        self.receipts: dict[tuple[str, str, str], tuple[str, ControlPlaneWriteResult]] = {}
        self.writes: list = []
        self.fail_with: BaseException | None = None

    # -- write port ---------------------------------------------------------

    async def commit(self, *, write):
        self.writes.append(write)
        if self.fail_with is not None:
            raise self.fail_with
        command = write.command
        expected_event, allowed_from = self._contract(command.operation, command)

        run_event = next(
            event
            for event in write.events
            if event.aggregate_type == RUN_AGGREGATE_TYPE
            and event.aggregate_id == command.run_id
        )
        if run_event.event_type != expected_event:
            raise ValueError("run event type does not match the command operation")
        if allowed_from is None:
            if command.expected_run_status is not None:
                raise ValueError("run creation may not carry an expected status")
        else:
            if command.expected_run_status not in allowed_from:
                raise ValueError("run event transition is not allowed for this operation")
            if run_event.payload.get("from_status") != command.expected_run_status.value:
                raise ValueError("run event source status must match the command precondition")

        receipt_key = (
            command.linkage.tenant_id,
            command.command_idempotency_key,
            command.run_id,
        )
        digest = command.command_digest
        previous = self.receipts.get(receipt_key)
        if previous is not None:
            if previous[0] != digest:
                return ControlPlaneWriteResult(
                    status=ControlPlaneWriteStatus.CONFLICT,
                    reason="idempotency_key_payload_conflict",
                )
            return replace(
                previous[1],
                status=ControlPlaneWriteStatus.DUPLICATE,
                event_results=tuple(
                    replace(item, duplicate=True) for item in previous[1].event_results
                ),
            )

        for version in command.aggregates:
            cursor = self.aggregates.get(
                (version.aggregate_type, version.aggregate_id), (0, None)
            )
            if cursor[0] != version.expected_sequence:
                return _conflict("aggregate_sequence_conflict")
            if (
                version.aggregate_type == RUN_AGGREGATE_TYPE
                and command.expected_run_status is not None
                and cursor[1] != command.expected_run_status.value
            ):
                return _conflict("run_status_conflict")
        for event in write.events:
            if (command.linkage.tenant_id, event.idempotency_key) in self.events:
                return _conflict("atomic_constraint_conflict")

        run = self.runs.get(command.run_id)
        if command.expected_run_status is None:
            if run is not None:
                return _conflict("run_identity_conflict")
            details = command.details
            run = {
                "run_id": command.run_id,
                "project_id": command.linkage.project_id,
                "session_id": command.linkage.session_id,
                "tenant_id": command.linkage.tenant_id,
                "idempotency_key": str(
                    details.get("idempotency_key", command.command_idempotency_key)
                ),
                "requested_by_employee_id": details.get("requested_by_employee_id"),
                "input_payload": dict(details.get("input_payload") or {}),
                "status": command.target_run_status.value,
            }
            self.runs[command.run_id] = run
        elif run is None:
            return _conflict("run_scope_not_found")

        run["status"] = command.target_run_status.value
        for version in command.aggregates:
            self.aggregates[(version.aggregate_type, version.aggregate_id)] = (
                version.next_sequence,
                command.target_run_status.value,
            )
        results = []
        for index, event in enumerate(write.events):
            self.events[(command.linkage.tenant_id, event.idempotency_key)] = int(
                event.sequence
            )
            self.event_rows.append(event)
            results.append(
                AtomicEventOutboxResult(
                    event_id=str(event.event_id),
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    aggregate_version=int(event.sequence),
                    outbox_idempotency_key=write.outbox_events[index].idempotency_key,
                    queue_deduplication_key=write.outbox_intents[index].deduplication_key,
                    event_recorded=True,
                    queue_intent_recorded=True,
                )
            )
        result = ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.APPLIED,
            event_results=tuple(results),
            run=AgentRunView(
                project_id=run["project_id"],
                session_id=run["session_id"],
                requested_by_employee_id=run["requested_by_employee_id"],
                idempotency_key=run["idempotency_key"],
                input_payload=run["input_payload"],
                id=run["run_id"],
                status=AgentRunStatus(run["status"]),
                finished_at=NOW if run["status"] in TERMINAL else None,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.receipts[receipt_key] = (digest, result)
        return result

    def _contract(self, operation: str, command):
        if operation == STREAM_FRAME_OPERATION:
            if not self.stream_frames_supported:
                # Faithful to today's repository: an unknown operation has no
                # expected event name, so the write is rejected before the DB.
                raise ValueError("run event type does not match the command operation")
            if command.expected_run_status != command.target_run_status:
                raise ValueError("a stream frame must preserve the run status")
            return STREAM_FRAME_EVENT_TYPE, {command.target_run_status}
        entry = _LIFECYCLE.get(operation)
        if entry is None:
            raise ValueError("run event type does not match the command operation")
        return entry

    # -- read store ---------------------------------------------------------

    async def read_scope(self, *, key: TurnBindingKey) -> RunBindingScope | None:
        project = self.projects.get(key.project_id)
        if project is None:
            return None
        return RunBindingScope(
            tenant_id=project[0],
            project_id=key.project_id,
            session_id=key.session_id,
            project_active=project[1],
            session_bound=self.sessions.get(key.session_id) == key.project_id,
        )

    async def read_binding(
        self, *, key: TurnBindingKey, tenant_id: str
    ) -> DurableRunState | None:
        for run in self.runs.values():
            if (
                run["project_id"] == key.project_id
                and run["session_id"] == key.session_id
                and run["idempotency_key"] == key.run_command_key
                and run["tenant_id"] == tenant_id
            ):
                return DurableRunState(run_id=run["run_id"], status=run["status"])
        return None

    async def read_run_sequence(self, *, run_id: str, tenant_id: str) -> int | None:
        run = self.runs.get(run_id)
        if run is None or run["tenant_id"] != tenant_id:
            return None
        cursor = self.aggregates.get((RUN_AGGREGATE_TYPE, run_id))
        return cursor[0] if cursor else None

    async def read_frame_sequence(self, *, tenant_id: str, idempotency_key: str) -> int | None:
        return self.events.get((tenant_id, idempotency_key))

    async def read_active_run(
        self,
        *,
        project_id: str,
        session_id: str,
        tenant_id: str,
        exclude_binding_key: str,
    ) -> DurableRunState | None:
        for run in self.runs.values():
            if (
                run["project_id"] == project_id
                and run["session_id"] == session_id
                and run["tenant_id"] == tenant_id
                and run["idempotency_key"] != exclude_binding_key
                and run["status"] in LIVE
            ):
                return DurableRunState(run_id=run["run_id"], status=run["status"])
        return None

    # -- helpers ------------------------------------------------------------

    def seed_project(
        self,
        *,
        project_id: str = PROJECT,
        tenant_id: str = TENANT,
        active: bool = True,
        session_id: str | None = SESSION,
    ) -> None:
        self.projects[project_id] = (tenant_id, active)
        if session_id is not None:
            self.sessions[session_id] = project_id

    def advance_cursor(self, run_id: str, sequence: int, status: str | None = None) -> None:
        current = self.aggregates.get((RUN_AGGREGATE_TYPE, run_id), (0, None))
        self.aggregates[(RUN_AGGREGATE_TYPE, run_id)] = (
            sequence,
            status or current[1] or AgentRunStatus.RUNNING.value,
        )


def _conflict(reason: str) -> ControlPlaneWriteResult:
    return ControlPlaneWriteResult(status=ControlPlaneWriteStatus.CONFLICT, reason=reason)


@pytest.fixture
def port() -> DoublesAtomicPort:
    return DoublesAtomicPort()


def bridge(port: DoublesAtomicPort, *, frames: bool = False) -> DurableRunBindingBridge:
    return DurableRunBindingBridge(
        transaction=port,
        store=port,
        clock=lambda: NOW,
        stream_frames_enabled=frames,
    )


async def _bound(port: DoublesAtomicPort, *, frames: bool = False):
    component = bridge(port, frames=frames)
    binding = await component.bind_turn(
        key=TURN_A, tenant_id=TENANT, actor_id=ACTOR, trace_id=TRACE
    )
    return component, binding


# -- matrix -------------------------------------------------------------------


def test_acceptance_matrix_is_explicit_and_unexecuted() -> None:
    assert len(CASES) >= 13
    assert TURN_A.project_id == PROJECT and TURN_A.session_id == SESSION
    assert TURN_A.run_command_key == "turn-run:session-runbind:turn-a"
    assert TURN_B.run_command_key != TURN_A.run_command_key
    assert STREAM_FRAME_OPERATION == "agent_run.stream_frame"
    assert STREAM_FRAME_EVENT_TYPE == "agent_run.stream_frame"


# -- binding ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_creates_exactly_one_run_and_replay_returns_it(port) -> None:
    component, binding = await _bound(port)
    replayed = await component.bind_turn(
        key=TURN_A, tenant_id=TENANT, actor_id=ACTOR, trace_id=TRACE
    )

    assert replayed.run_id == binding.run_id
    assert binding.status is AgentRunStatus.RUNNING
    assert binding.run_sequence == 2
    assert len(port.runs) == 1
    assert [write.command.operation for write in port.writes] == [
        "agent_run.create",
        "agent_run.start",
    ]
    created = port.writes[0]
    assert created.command.command_idempotency_key == TURN_A.run_command_key
    assert created.command.details["input_payload"]["turn_id"] == "turn-a"
    assert created.command.details["session_id"] == SESSION
    assert created.events[0].payload["control_plane"]["tenant_id"] == TENANT
    # the replay added no third write
    assert len(port.writes) == 2


@pytest.mark.asyncio
async def test_binding_is_resolved_from_the_durable_store_only(port) -> None:
    component = bridge(port)

    with pytest.raises(RunBindingError) as missing:
        await component.resolve_turn(key=TURN_A, tenant_id=TENANT)
    assert missing.value.code == CODE_NO_ACTIVE_RUN
    assert missing.value.blocker == BLOCKER_TURN_UNBOUND
    assert port.writes == [] and port.runs == {}

    _, binding = await _bound(port)
    writes_before = len(port.writes)
    resolved = await component.resolve_turn(
        key=TURN_A, tenant_id=TENANT, actor_id=ACTOR, trace_id=TRACE
    )

    assert resolved.run_id == binding.run_id
    assert resolved.run_sequence == binding.run_sequence
    assert len(port.writes) == writes_before
    # A binding of another tenant is invisible, not merely forbidden.
    with pytest.raises(RunBindingError) as foreign:
        await component.resolve_turn(key=TURN_A, tenant_id=OTHER_TENANT)
    assert foreign.value.code == CODE_NO_ACTIVE_RUN


@pytest.mark.asyncio
async def test_foreign_tenant_is_refused_before_any_write(port) -> None:
    port.seed_project(project_id=OTHER_PROJECT, tenant_id=OTHER_TENANT, session_id=OTHER_SESSION)
    component = bridge(port)

    with pytest.raises(RunBindingError) as denied:
        await component.bind_turn(
            key=TurnBindingKey(
                project_id=OTHER_PROJECT, session_id=OTHER_SESSION, turn_id="turn-x"
            ),
            tenant_id=TENANT,
            actor_id=ACTOR,
        )
    assert denied.value.code == CODE_SCOPE_DENIED
    assert denied.value.blocker == BLOCKER_TENANT_SCOPE
    assert port.writes == [] and port.runs == {}


@pytest.mark.asyncio
async def test_inactive_project_and_foreign_session_are_refused(port) -> None:
    port.seed_project(project_id=OTHER_PROJECT, tenant_id=TENANT, active=False, session_id=OTHER_SESSION)
    component = bridge(port)

    with pytest.raises(RunBindingError) as inactive:
        await component.bind_turn(
            key=TurnBindingKey(project_id=OTHER_PROJECT, session_id=OTHER_SESSION, turn_id="turn-x"),
            tenant_id=TENANT,
            actor_id=ACTOR,
        )
    assert inactive.value.code == CODE_SCOPE_DENIED

    # A real session, but bound to another project: the routed pair is invalid.
    with pytest.raises(RunBindingError) as mismatch:
        await component.bind_turn(
            key=TurnBindingKey(project_id=PROJECT, session_id="session-of-nowhere", turn_id="turn-y"),
            tenant_id=TENANT,
            actor_id=ACTOR,
        )
    assert mismatch.value.code == CODE_SCOPE_DENIED
    assert port.writes == []


@pytest.mark.asyncio
async def test_terminal_turn_does_not_get_a_second_run(port) -> None:
    component, binding = await _bound(port)
    finished = await component.finish_turn(binding=binding, outcome=TurnOutcome.SUCCEEDED)
    assert finished.terminal and finished.status is AgentRunStatus.SUCCEEDED

    writes_before = len(port.writes)
    again = await component.bind_turn(
        key=TURN_A, tenant_id=TENANT, actor_id=ACTOR, trace_id=TRACE
    )

    assert again.run_id == binding.run_id
    assert again.terminal
    assert len(port.runs) == 1
    assert len(port.writes) == writes_before


@pytest.mark.asyncio
async def test_second_concurrent_active_run_on_one_session_is_refused(port) -> None:
    component, binding = await _bound(port)

    with pytest.raises(RunBindingError) as concurrent:
        await component.bind_turn(
            key=TURN_B, tenant_id=TENANT, actor_id=ACTOR, trace_id=TRACE
        )
    assert concurrent.value.code == CODE_CONCURRENT_ACTIVE_RUN
    assert len(port.runs) == 1

    await component.finish_turn(binding=binding, outcome=TurnOutcome.CANCELLED)
    next_binding = await component.bind_turn(
        key=TURN_B, tenant_id=TENANT, actor_id=ACTOR, trace_id=TRACE
    )
    assert next_binding.run_id != binding.run_id
    assert len(port.runs) == 2


# -- frames -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frames_receive_monotonic_run_sequences(port) -> None:
    component, binding = await _bound(port, frames=True)
    sequences = []
    for index in range(3):
        frame = await component.append_stream_frame(
            binding=binding,
            frame_key=f"agent-event:{index + 1}",
            event_type="text_delta",
            payload={"text": f"chunk-{index}"},
        )
        sequences.append(frame.sequence)

    assert sequences == [3, 4, 5]
    assert sequences == sorted(set(sequences))
    assert all(
        (binding.run_id, sequence) in {
            (event.aggregate_id, event.sequence) for event in port.event_rows
        }
        for sequence in sequences
    )
    assert port.aggregates[(RUN_AGGREGATE_TYPE, binding.run_id)][0] == 5


@pytest.mark.asyncio
async def test_redelivered_frame_is_idempotent(port) -> None:
    component, binding = await _bound(port, frames=True)
    first = await component.append_stream_frame(
        binding=binding, frame_key="agent-event:7", event_type="tool_call", payload={"a": 1}
    )
    events_after_first = len(port.event_rows)
    replay = await component.append_stream_frame(
        binding=binding, frame_key="agent-event:7", event_type="tool_call", payload={"a": 1}
    )

    assert replay.sequence == first.sequence
    assert replay.duplicate is True
    assert len(port.event_rows) == events_after_first
    # A different frame still advances the aggregate.
    other = await component.append_stream_frame(
        binding=binding, frame_key="agent-event:8", event_type="tool_call"
    )
    assert other.sequence == first.sequence + 1


@pytest.mark.asyncio
async def test_frame_is_one_atomic_write_with_event_outbox_and_intent(port) -> None:
    component, binding = await _bound(port, frames=True)
    writes_before = len(port.writes)
    await component.append_stream_frame(
        binding=binding, frame_key="agent-event:1", event_type="text_delta"
    )

    write = port.writes[writes_before]
    assert write.command.operation == STREAM_FRAME_OPERATION
    assert len(write.events) == len(write.outbox_events) == len(write.outbox_intents) == 1
    event = write.events[0]
    aggregate = write.command.aggregates[0]
    assert (aggregate.aggregate_type, aggregate.aggregate_id) == (RUN_AGGREGATE_TYPE, binding.run_id)
    assert aggregate.next_sequence == event.sequence == aggregate.expected_sequence + 1
    assert write.outbox_events[0].event_id == str(event.event_id)
    assert write.outbox_intents[0].deduplication_key == write.outbox_events[0].idempotency_key
    assert event.payload["control_plane"]["actor_id"] == ACTOR
    assert event.payload["control_plane"]["session_id"] == SESSION


@pytest.mark.asyncio
async def test_frame_sequence_follows_the_durable_cursor(port) -> None:
    component, binding = await _bound(port, frames=True)
    # An approved write of the same aggregate advanced the cursor behind the
    # bridge's back; the next frame must continue from the durable value.
    port.advance_cursor(binding.run_id, 9)
    frame = await component.append_stream_frame(
        binding=binding, frame_key="agent-event:1", event_type="text_delta"
    )

    assert frame.sequence == 10
    assert port.aggregates[(RUN_AGGREGATE_TYPE, binding.run_id)][0] == 10


@pytest.mark.asyncio
async def test_frame_payload_never_reaches_the_audit_trail(port) -> None:
    component, binding = await _bound(port, frames=True)
    secret = "user prompt that must not be audited"
    await component.append_stream_frame(
        binding=binding,
        frame_key="agent-event:1",
        event_type="text_delta",
        payload={"text": secret},
    )

    event = port.event_rows[-1]
    serialised = str(event.payload)
    assert secret not in serialised
    assert event.payload["frame_payload_digest"]
    assert event.payload["frame_event_type"] == "text_delta"


@pytest.mark.asyncio
async def test_current_contract_rejects_the_frame_operation_as_named_blocker(port) -> None:
    port.stream_frames_supported = False
    component, binding = await _bound(port, frames=True)
    events_before = len(port.event_rows)

    with pytest.raises(RunBindingError) as unsupported:
        await component.append_stream_frame(
            binding=binding, frame_key="agent-event:1", event_type="text_delta"
        )
    assert unsupported.value.code == CODE_STREAM_FRAME_UNSUPPORTED
    assert unsupported.value.blocker == BLOCKER_STREAM_FRAME_CONTRACT
    assert len(port.event_rows) == events_before


@pytest.mark.asyncio
async def test_frames_are_disabled_by_default_without_touching_the_port(port) -> None:
    component, binding = await _bound(port)
    writes_before = len(port.writes)

    with pytest.raises(RunBindingError) as disabled:
        await component.append_stream_frame(
            binding=binding, frame_key="agent-event:1", event_type="text_delta"
        )
    assert disabled.value.code == CODE_STREAM_FRAME_UNSUPPORTED
    assert disabled.value.blocker == BLOCKER_STREAM_FRAME_CONTRACT
    assert len(port.writes) == writes_before


@pytest.mark.asyncio
async def test_missing_sequence_cursor_refuses_without_synthesising(port) -> None:
    _, binding = await _bound(port, frames=True)
    port.aggregates.pop((RUN_AGGREGATE_TYPE, binding.run_id))

    component = bridge(port, frames=True)
    with pytest.raises(RunBindingError) as missing:
        await component.append_stream_frame(
            binding=binding, frame_key="agent-event:1", event_type="text_delta"
        )
    assert missing.value.code == CODE_SEQUENCE_UNAVAILABLE
    assert missing.value.blocker == BLOCKER_SEQUENCE


# -- finish -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_is_terminal_and_idempotent_and_frames_are_refused(port) -> None:
    component, binding = await _bound(port)
    failed = await component.finish_turn(
        binding=binding, outcome=TurnOutcome.FAILED, failure_code="TURN_ERROR"
    )
    writes_after_finish = len(port.writes)
    repeat = await component.finish_turn(
        binding=binding, outcome=TurnOutcome.FAILED, failure_code="TURN_ERROR"
    )

    assert failed.terminal and failed.status is AgentRunStatus.FAILED
    assert repeat.terminal
    assert len(port.writes) == writes_after_finish
    assert port.runs[binding.run_id]["status"] == AgentRunStatus.FAILED.value

    with pytest.raises(RunBindingError) as terminal:
        await component.append_stream_frame(
            binding=failed, frame_key="agent-event:1", event_type="text_delta"
        )
    assert terminal.value.code == CODE_TURN_TERMINAL
    assert terminal.value.blocker == BLOCKER_TURN_TERMINAL


@pytest.mark.asyncio
async def test_stale_terminal_transition_is_resolved_from_the_durable_row(port) -> None:
    component, binding = await _bound(port)
    # Another writer already cancelled the run: the stale succeed must not be
    # reported as applied, and must not create a new run either.
    port.runs[binding.run_id]["status"] = AgentRunStatus.CANCELLED.value
    port.advance_cursor(binding.run_id, 3, AgentRunStatus.CANCELLED.value)

    settled = await component.finish_turn(binding=binding, outcome=TurnOutcome.CANCELLED)
    assert settled.status is AgentRunStatus.CANCELLED
    assert settled.run_sequence == 3
    assert len(port.runs) == 1


@pytest.mark.asyncio
async def test_write_failure_maps_to_a_named_error_without_fallback(port) -> None:
    component, binding = await _bound(port)
    port.fail_with = OSError("database unavailable")

    with pytest.raises(RunBindingError) as rejected:
        await component.finish_turn(binding=binding, outcome=TurnOutcome.SUCCEEDED)
    assert rejected.value.code == CODE_ATOMIC_WRITE_REJECTED
    assert "database unavailable" not in str(rejected.value)
    assert port.runs[binding.run_id]["status"] == AgentRunStatus.RUNNING.value


# -- pure write builder -------------------------------------------------------


def test_stream_frame_key_is_sequence_free_so_replay_is_stable(port) -> None:
    binding = __import__(
        "prodavan.application.agent.run_binding", fromlist=["TurnRunBinding"]
    ).TurnRunBinding(
        run_id="agr_fixture",
        project_id=PROJECT,
        session_id=SESSION,
        turn_id="turn-a",
        tenant_id=TENANT,
        actor_id=ACTOR,
        status=AgentRunStatus.RUNNING,
        run_sequence=2,
        trace_id=TRACE,
        frames_enabled=True,
    )
    key = frame_idempotency_key(
        tenant_id=TENANT, session_id=SESSION, turn_id="turn-a", frame_key="agent-event:1"
    )
    assert key == frame_idempotency_key(
        tenant_id=TENANT, session_id=SESSION, turn_id="turn-a", frame_key="agent-event:1"
    )
    assert len(key) <= 128
    assert "3" not in key.split(":", 1)[1]

    first = build_stream_frame_write(
        binding=binding,
        frame_key="agent-event:1",
        event_type="text_delta",
        payload={"text": "x"},
        expected_sequence=2,
        occurred_at=NOW,
        queue_name="agent-control-plane-test",
        producer="agent-run-binding-test",
    )
    second = build_stream_frame_write(
        binding=binding,
        frame_key="agent-event:1",
        event_type="text_delta",
        payload={"text": "x"},
        expected_sequence=3,
        occurred_at=NOW,
        queue_name="agent-control-plane-test",
        producer="agent-run-binding-test",
    )
    # Same frame identity, different cursor: the event identity moves with the
    # sequence, while the *frame key* (the redelivery guard) does not.
    assert first.command.command_idempotency_key == second.command.command_idempotency_key
    assert first.events[0].sequence == 3 and second.events[0].sequence == 4
