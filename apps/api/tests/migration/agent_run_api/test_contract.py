"""Migration-stage contract tests for the employee Agent Run API; do not run yet."""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.api.migration_agent_runs.dependencies import (
    FEATURE_FLAG,
    REQUIRED_MODEL_TABLES,
    _feature_enabled,
    missing_model_tables,
)
from prodavan.api.migration_agent_runs.schemas import (
    ApprovalDecisionBody,
    CancelAgentRunBody,
    EventTimelineQuery,
)
from prodavan.api.v1.agent_runs import (
    _command_trace_id,
    _project_tenant_id,
)


API_ROOT = Path(__file__).resolve().parents[3]


def test_command_trace_is_stable_and_scoped_by_actor_project_and_key() -> None:
    base = {"tenant_id": "tenant-a", "actor_id": "employee-a", "project_id": "project-a", "key": "cmd-1"}
    assert _command_trace_id(**base) == _command_trace_id(**base)
    assert _command_trace_id(**base) != _command_trace_id(**{**base, "actor_id": "employee-b"})
    assert _command_trace_id(**base) != _command_trace_id(**{**base, "key": "cmd-2"})


def test_project_tenant_scope_requires_membership() -> None:
    from types import SimpleNamespace

    employee = SimpleNamespace(memberships=[SimpleNamespace(company_id="tenant-a")])
    assert _project_tenant_id(employee=employee, project_company_id="tenant-a") == "tenant-a"
    with pytest.raises(Exception) as error:
        _project_tenant_id(employee=employee, project_company_id="tenant-b")
    assert getattr(error.value, "status", None) == 404


def test_employee_run_api_is_opt_in_and_fails_closed_by_default(monkeypatch) -> None:
    monkeypatch.delenv(FEATURE_FLAG, raising=False)
    assert not _feature_enabled(None)
    assert not _feature_enabled("0")
    assert _feature_enabled("true")
    assert _feature_enabled("1")


def test_all_durable_tables_must_be_registered_before_backend_di() -> None:
    missing = missing_model_tables(set())
    assert set(missing) == REQUIRED_MODEL_TABLES
    assert missing_model_tables(set(REQUIRED_MODEL_TABLES)) == ()
    assert "agent_control_plane_events" in missing
    assert "agent_control_plane_enqueue_intents" in missing


def test_cancel_requires_explicit_cas_version_and_cancellable_status() -> None:
    with pytest.raises(ValueError):
        CancelAgentRunBody(reason="cancel")
    with pytest.raises(ValueError):
        CancelAgentRunBody(expected_sequence=4, expected_status="waiting_approval")
    body = CancelAgentRunBody(expected_sequence=4, expected_status="running")
    assert body.expected_sequence == 4


def test_approval_decision_requires_both_aggregate_sequences() -> None:
    with pytest.raises(ValueError):
        ApprovalDecisionBody(decision="approve")
    body = ApprovalDecisionBody(
        expected_approval_sequence=2,
        expected_run_sequence=7,
        decision="deny",
    )
    assert (body.expected_approval_sequence, body.expected_run_sequence) == (2, 7)


def test_event_cursor_requires_one_aggregate_identity() -> None:
    with pytest.raises(ValueError, match="aggregate_type and aggregate_id"):
        EventTimelineQuery(after_sequence=2).validate_cursor()
    with pytest.raises(ValueError, match="supplied together"):
        EventTimelineQuery(aggregate_type="agent_run").validate_cursor()
    EventTimelineQuery(
        aggregate_type="approval_request",
        aggregate_id="apr_1",
        after_sequence=2,
    ).validate_cursor()


def test_employee_api_routes_are_registered_without_replacing_chat_router() -> None:
    source = (API_ROOT / "src/prodavan/api/v1/router.py").read_text(encoding="utf-8")
    agent_source = (API_ROOT / "src/prodavan/api/v1/agent.py").read_text(encoding="utf-8")
    assert "agent_runs" in source
    assert "router.include_router(agent_runs.router)" in source
    assert "router.include_router(agent.router)" in source
    assert "AgentSessionService" in agent_source


def test_employee_api_exposes_run_approval_and_timeline_routes() -> None:
    source = (API_ROOT / "src/prodavan/api/v1/agent_runs.py").read_text(encoding="utf-8")
    required_paths = (
        '"/projects/{project_id}/agent/runs"',
        '"/projects/{project_id}/agent/runs/{run_id}"',
        '"/projects/{project_id}/agent/runs/{run_id}/cancel"',
        '"/agent/approvals/inbox"',
        '"/projects/{project_id}/agent/approvals/{approval_id}/decision"',
        '"/projects/{project_id}/agent/runs/{run_id}/events"',
    )
    for route in required_paths:
        assert route in source
    assert "Idempotency-Key" in source
    assert "DurableAgentControlPlane" not in source or "control_plane" in source


def test_employee_api_has_no_memory_or_legacy_write_fallback() -> None:
    source = (API_ROOT / "src/prodavan/api/v1/agent_runs.py").read_text(encoding="utf-8")
    dependency = (
        API_ROOT / "src/prodavan/api/migration_agent_runs/dependencies.py"
    ).read_text(encoding="utf-8")
    # The write switch is a gate result, never an unconditional constant.
    assert "writes_enabled: bool = False" in dependency
    assert "writes_enabled=True" in dependency
    assert "AGENT_RUN_API_DISABLED" in dependency
    assert "agent_run_api_feature_flag_disabled" in dependency
    assert "backend.require_writes()" in source
    assert "AgentSessionService" not in source
    assert "AgentRunRow(" not in source
    assert "ApprovalRequestRow(" not in source
    assert "in_memory" not in source.lower()
    assert "record_and_enqueue" not in source


def test_orm_registration_is_complete_and_the_queue_consumer_is_wired() -> None:
    models_init = (
        API_ROOT / "src/prodavan/infrastructure/persistence/models/__init__.py"
    ).read_text(encoding="utf-8")
    required = {
        "AgentControlPlaneAggregateRow",
        "AgentControlPlaneCommandRow",
        "AgentControlPlaneEnqueueIntentRow",
        "AgentControlPlaneEventRow",
        "AgentControlPlaneOutboxEventRow",
        "AgentControlPlaneRunScopeRow",
    }
    for name in required:
        assert f'"{name}",' in models_init, f"{name} is not registered/exported"

    dependency = (
        API_ROOT / "src/prodavan/api/migration_agent_runs/dependencies.py"
    ).read_text(encoding="utf-8")
    # Registration is proved against real metadata, not against a hardcoded list.
    assert "Base.metadata.tables" in dependency
    assert "agent_control_plane_models_not_registered" in dependency
    # The committed handoff is owned by a real durable consumer.
    assert "build_intent_dispatcher" in dependency
    assert "dispatch_batch" in dependency
    assert 'INTENT_QUEUE_NAME = "agent-control-plane"' in dependency
    assert "agent_control_plane_dispatcher_unavailable" in dependency
    # The old blanket refusal code must not be the permanent API behaviour.
    agent_source = (API_ROOT / "src/prodavan/api/v1/agent_runs.py").read_text(encoding="utf-8")
    assert "agent_control_plane_dispatcher_unwired" not in dependency
    assert "agent_control_plane_dispatcher_unwired" not in agent_source
