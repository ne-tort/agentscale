"""Read-query scope contracts, authored but intentionally not executed."""

from __future__ import annotations

import asyncio

from prodavan.application.agent_runs.service import AgentRunsQueryService


class _Rows:
    def all(self):
        return []


class _Scalars:
    def all(self):
        return []


class _StatementCapture:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Rows()

    async def scalars(self, statement):
        self.statement = statement
        return _Scalars()


def test_run_list_query_is_scoped_by_tenant_project_and_stable_keyset_order() -> None:
    session = _StatementCapture()
    asyncio.run(
        AgentRunsQueryService(session).list_runs(
            tenant_id="tenant-a",
            project_id="project-a",
            limit=25,
            offset=0,
        )
    )
    sql = str(session.statement)
    assert "agent_control_plane_run_scopes.tenant_id" in sql
    assert "agent_control_plane_run_scopes.project_id" in sql
    assert "agent_runs.created_at DESC" in sql
    assert "agent_runs.id DESC" in sql


def test_event_query_is_scoped_to_run_project_and_orders_aggregate_sequences() -> None:
    session = _StatementCapture()
    asyncio.run(
        AgentRunsQueryService(session).list_events(
            tenant_id="tenant-a",
            project_id="project-a",
            run_id="run-a",
            limit=25,
        )
    )
    sql = str(session.statement)
    assert "agent_control_plane_run_scopes.tenant_id" in sql
    assert "agent_control_plane_run_scopes.project_id" in sql
    assert "agent_control_plane_events.run_id" in sql
    assert "ORDER BY agent_control_plane_events.aggregate_type" in sql
    assert "agent_control_plane_events.sequence" in sql


def test_approval_inbox_query_binds_cursor_to_membership_set_and_project() -> None:
    from prodavan.application.agent_runs import decode_cursor, encode_cursor

    scope = {
        "kind": "approval",
        "tenants": ["tenant-a", "tenant-b"],
        "project": "project-a",
    }
    cursor = encode_cursor({**scope, "requested_at": "2026-11-03T12:00:00+00:00", "approval_id": "apr-a"})
    assert decode_cursor(cursor, expected=scope)["approval_id"] == "apr-a"

    for altered in (
        {**scope, "tenants": ["tenant-a"]},
        {**scope, "project": "project-b"},
    ):
        try:
            decode_cursor(cursor, expected=altered)
        except ValueError:
            continue
        raise AssertionError("approval cursor unexpectedly crossed its authorization scope")
