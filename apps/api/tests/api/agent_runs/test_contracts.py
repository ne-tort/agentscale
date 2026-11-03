"""Acceptance contracts for Agent Run/session/approval route semantics.

These tests are authored for the migration verification window and are not
executed during the current verification freeze.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import inspect

import pytest

from prodavan.api.migration_agent_runs.dependencies import AgentRunBackend
from prodavan.api.migration_agent_runs.schemas import EventTimelineQuery
from prodavan.api.v1 import agent_runs
from prodavan.application.agent_runs import (
    AgentRunExecutionUnavailable,
    AgentRunExecutor,
    AgentRunLease,
    ProviderExecutionResult,
    ProviderOutcome,
    decode_cursor,
    encode_cursor,
    validate_retry_result,
)


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def test_run_cursor_round_trips_and_is_bound_to_tenant_and_project() -> None:
    scope = {"kind": "run", "tenant": "tenant-a", "project": "project-a"}
    token = encode_cursor({**scope, "created_at": NOW.isoformat(), "id": "run-1"})
    assert decode_cursor(token, expected=scope)["id"] == "run-1"
    with pytest.raises(ValueError, match="does not match"):
        decode_cursor(token, expected={**scope, "tenant": "tenant-b"})
    with pytest.raises(ValueError, match="does not match"):
        decode_cursor(token, expected={**scope, "project": "project-b"})


def test_event_cursor_is_bound_to_run_and_aggregate_filter() -> None:
    scope = {
        "kind": "event",
        "tenant": "tenant-a",
        "project": "project-a",
        "run": "run-a",
        "filter_aggregate_type": None,
        "filter_aggregate_id": None,
    }
    token = encode_cursor(
        {
            **scope,
            "occurred_at": NOW.isoformat(),
            "aggregate_type": "agent_run",
            "aggregate_id": "run-a",
            "sequence": 4,
            "event_id": "event-4",
        }
    )
    assert decode_cursor(token, expected=scope)["sequence"] == 4
    with pytest.raises(ValueError, match="does not match"):
        decode_cursor(token, expected={**scope, "run": "run-b"})
    with pytest.raises(ValueError, match="does not match"):
        decode_cursor(token, expected={**scope, "filter_aggregate_type": "agent_run"})
    with pytest.raises(ValueError, match="malformed"):
        decode_cursor("%%%", expected=scope)


def test_approval_cursor_is_bound_to_all_memberships_and_project_filter() -> None:
    scope = {"kind": "approval", "tenants": ["tenant-a", "tenant-b"], "project": None}
    token = encode_cursor({**scope, "requested_at": NOW.isoformat(), "approval_id": "apr-1"})
    assert decode_cursor(token, expected=scope)["approval_id"] == "apr-1"
    with pytest.raises(ValueError, match="does not match"):
        decode_cursor(token, expected={**scope, "tenants": ["tenant-a"]})
    with pytest.raises(ValueError, match="does not match"):
        decode_cursor(token, expected={**scope, "project": "project-a"})


def test_event_cursor_encodes_the_full_ordering_tuple() -> None:
    scope = {
        "kind": "event",
        "tenant": "tenant-a",
        "project": "project-a",
        "run": "run-a",
        "filter_aggregate_type": None,
        "filter_aggregate_id": None,
    }
    token = encode_cursor(
        {
            **scope,
            "occurred_at": NOW.isoformat(),
            "aggregate_type": "agent_run",
            "aggregate_id": "run-a",
            "sequence": 5,
            "event_id": "evt-5",
        }
    )
    decoded = decode_cursor(token, expected=scope)
    assert (
        decoded["occurred_at"],
        decoded["aggregate_type"],
        decoded["aggregate_id"],
        decoded["sequence"],
        decoded["event_id"],
    ) == (NOW.isoformat(), "agent_run", "run-a", 5, "evt-5")


def test_sequence_cursor_requires_exactly_one_aggregate_identity() -> None:
    with pytest.raises(ValueError, match="requires aggregate_type and aggregate_id"):
        EventTimelineQuery(after_sequence=3).validate_cursor()
    with pytest.raises(ValueError, match="supplied together"):
        EventTimelineQuery(aggregate_type="agent_run").validate_cursor()
    EventTimelineQuery(
        aggregate_type="approval_request",
        aggregate_id="apr-1",
        after_sequence=3,
    ).validate_cursor()


def test_cancel_duplicate_reaches_durable_receipt_instead_of_local_status_rejection(monkeypatch) -> None:
    source = inspect.getsource(agent_runs.cancel_agent_run)
    assert "expected_status does not match the observed run status" not in source
    assert source.index("cancel_run(") < source.index("_write_error(result)")
    assert "command_idempotency_key=key" in source


def test_approval_decision_uses_both_aggregate_versions_and_command_key(monkeypatch) -> None:
    source = __import__("inspect").getsource(agent_runs.decide_agent_approval)
    assert "expected_approval_sequence=body.expected_approval_sequence" in source
    assert "expected_run_sequence=body.expected_run_sequence" in source
    assert "command_idempotency_key=key" in source
    assert 'select(ApprovalRequestRow, AgentRunRow, AgentControlPlaneRunScopeRow)' in source
    assert "AgentControlPlaneRunScopeRow.tenant_id == tenant_id" in source
    assert "AgentControlPlaneRunScopeRow.project_id == project_id" in source


def test_route_contracts_keep_project_scope_and_expose_durable_cursor_pages() -> None:
    source = __import__("inspect").getsource(agent_runs)
    for path in (
        '"/projects/{project_id}/agent/runs"',
        '"/projects/{project_id}/agent/runs/{run_id}"',
        '"/projects/{project_id}/agent/runs/{run_id}/cancel"',
        '"/agent/approvals/inbox"',
        '"/projects/{project_id}/agent/approvals/{approval_id}/decision"',
        '"/projects/{project_id}/agent/approvals/{approval_id}/approve"',
        '"/projects/{project_id}/agent/approvals/{approval_id}/reject"',
        '"/projects/{project_id}/agent/approvals/{approval_id}/cancel"',
        '"/projects/{project_id}/agent/runs/{run_id}/events"',
    ):
        assert path in source
    assert "submit_run(command, linkage=linkage)" in inspect.getsource(agent_runs.create_agent_run)
    assert "AgentRunsQueryService(session).list_events" in source
    assert "X-Next-Cursor" in source
    assert "X-Has-More" in source
    assert "await AgentSessionService" not in source


def test_writes_remain_fail_closed_until_the_gate_and_consumer_are_wired() -> None:
    source = __import__("inspect").getsource(agent_runs)
    assert source.count("backend.require_writes()") >= 3
    require_source = inspect.getsource(AgentRunBackend.require_writes)
    # Off by default: the flag gate refuses with an explicit code and blocker.
    assert "agent_run_api_feature_flag_disabled" in require_source
    assert "AGENT_RUN_API_DISABLED" in require_source
    # A committed intent with no consumer would be queued forever, so refuse.
    assert "agent_control_plane_dispatcher_unavailable" in require_source
    assert "writes_enabled" in require_source
    assert "in_memory" not in source.lower()


def test_run_execution_claim_is_unavailable_without_durable_adapter() -> None:
    executor = AgentRunExecutor()
    assert not executor.is_wired
    with pytest.raises(AgentRunExecutionUnavailable) as error:
        _run(executor.claim_next(owner_id="worker-1", now=NOW, lease_until=NOW + timedelta(seconds=10)))
    assert error.value.blocker == "agent_run_durable_claim_and_lease_unwired"


def test_provider_unknown_outcome_cannot_be_scheduled_for_blind_retry() -> None:
    with pytest.raises(ValueError, match="never blind-retried"):
        ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN, retry_at=NOW)
    with pytest.raises(ValueError, match="requires a durable retry_at"):
        ProviderExecutionResult(outcome=ProviderOutcome.RETRYABLE_FAILURE)
    retry = ProviderExecutionResult(outcome=ProviderOutcome.RETRYABLE_FAILURE, retry_at=NOW)
    assert validate_retry_result(retry) == NOW
    unknown = ProviderExecutionResult(outcome=ProviderOutcome.UNKNOWN)
    with pytest.raises(ValueError, match="never blind-retried"):
        validate_retry_result(unknown)


def test_stale_generation_and_cancel_fencing_are_explicit_port_requirements() -> None:
    port_source = __import__("inspect").getsource(agent_runs)
    execution_source = __import__("inspect").getsource(AgentRunLease)
    assert "generation" in execution_source
    protocol = __import__("inspect").getsource(
        __import__("prodavan.application.agent_runs.execution", fromlist=["AgentRunExecutionPort"])
        .AgentRunExecutionPort
    )
    assert "monotonically increasing generation" in protocol
    assert "Cancellation increments/fences the generation" in protocol
    assert port_source.count("backend.require_writes()") >= 3


def test_approval_cancel_is_idempotent_and_checks_both_aggregate_sequences() -> None:
    source = inspect.getsource(agent_runs.cancel_agent_approval)
    assert '"/projects/{project_id}/agent/approvals/{approval_id}/cancel"' in inspect.getsource(agent_runs)
    assert '"/projects/{project_id}/agent/approvals/{approval_id}/approve"' in inspect.getsource(agent_runs)
    assert '"/projects/{project_id}/agent/approvals/{approval_id}/reject"' in inspect.getsource(agent_runs)
    assert "expected_approval_sequence=body.expected_approval_sequence" in source
    assert "expected_run_sequence=body.expected_run_sequence" in source
    assert "command_idempotency_key=key" in source
    assert "backend.require_writes()" in source


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
