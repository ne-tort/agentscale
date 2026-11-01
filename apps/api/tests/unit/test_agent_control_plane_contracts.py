"""Acceptance fixtures for durable agent control-plane contracts.

These tests are intentionally pure contract checks: they do not require a
Postgres instance and describe the invariants repositories must preserve.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestStatus,
    EventEnvelope,
)
from prodavan.infrastructure.persistence.models.agent_control_plane import (
    AgentRunRow,
    ApprovalRequestRow,
)


def test_run_contract_keeps_session_optional_and_idempotency_explicit() -> None:
    command = AgentRunCreate(
        project_id="project-1",
        idempotency_key="send:turn-42",
        input_payload={"text": "continue"},
    )

    assert command.session_id is None
    assert command.idempotency_key == "send:turn-42"
    assert command.input_payload == {"text": "continue"}


def test_run_and_approval_orm_tables_are_distinct() -> None:
    assert AgentRunRow.__tablename__ == "agent_runs"
    assert ApprovalRequestRow.__tablename__ == "approval_requests"
    assert "session_id" in AgentRunRow.__table__.columns
    assert "run_id" in ApprovalRequestRow.__table__.columns
    assert "session_id" not in ApprovalRequestRow.__table__.columns


def test_approval_decision_only_allows_terminal_human_decisions() -> None:
    approved = ApprovalDecision(
        status=ApprovalRequestStatus.APPROVED,
        decided_by_employee_id="employee-1",
    )
    assert approved.status is ApprovalRequestStatus.APPROVED

    with pytest.raises(ValidationError):
        ApprovalDecision(status=ApprovalRequestStatus.PENDING, decided_by_employee_id="employee-1")


def test_event_envelope_round_trips_correlation_and_ordering_metadata() -> None:
    correlation_id = UUID("00000000-0000-0000-0000-000000000042")
    envelope = EventEnvelope(
        event_type="agent_run.waiting_approval",
        occurred_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        producer="agent-control-plane",
        aggregate_type="agent_run",
        aggregate_id="agr_42",
        run_id="agr_42",
        session_id="ags_7",
        correlation_id=correlation_id,
        idempotency_key="agr_42:approval:tool-1",
        sequence=3,
        payload={"approval_id": "apr_1"},
    )

    wire = envelope.model_dump(mode="json")
    restored = EventEnvelope.model_validate(wire)
    assert restored.event_id == envelope.event_id
    assert restored.correlation_id == correlation_id
    assert restored.sequence == 3
    assert wire["payload"] == {"approval_id": "apr_1"}


def test_event_envelope_rejects_unknown_fields_and_negative_sequence() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_type="agent_run.created",
            occurred_at=datetime.now(UTC),
            producer="test",
            aggregate_type="agent_run",
            aggregate_id="agr_1",
            unexpected="nope",
        )

    with pytest.raises(ValidationError):
        EventEnvelope(
            event_type="agent_run.created",
            occurred_at=datetime.now(UTC),
            producer="test",
            aggregate_type="agent_run",
            aggregate_id="agr_1",
            sequence=-1,
        )


def test_status_values_are_wire_stable() -> None:
    assert AgentRunStatus.WAITING_APPROVAL.value == "waiting_approval"
    assert ApprovalRequestStatus.DENIED.value == "denied"


def test_approval_command_is_scoped_to_run_and_tool_call() -> None:
    request = ApprovalRequestCreate(
        run_id="agr_1",
        tool_call_id="tool-1",
        approval_kind="filesystem.write",
        requested_payload={"path": "/workspace/a.txt"},
    )

    assert request.run_id == "agr_1"
    assert request.tool_call_id == "tool-1"
    assert request.requested_payload["path"] == "/workspace/a.txt"
