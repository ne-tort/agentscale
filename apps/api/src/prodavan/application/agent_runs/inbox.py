"""Scoped cursor-page reads for pending approval inbox entries."""

from __future__ import annotations

from sqlalchemy import and_, or_, select

from .service import (
    AgentRunPersistenceBlocked,
    ApprovalPage,
    ApprovalPageItem,
    decode_cursor,
    encode_cursor,
)


async def list_pending_approvals(
    *,
    session,
    tenant_ids: frozenset[str],
    project_id: str | None,
    limit: int,
    cursor: str | None,
) -> ApprovalPage:
    """Page the durable inbox; callers must still apply project visibility ACLs."""
    from prodavan.infrastructure.persistence.agent_control_plane.models import (
        AgentControlPlaneAggregateRow,
        AgentControlPlaneRunScopeRow,
    )
    from prodavan.infrastructure.persistence.models.agent_control_plane import (
        AgentRunRow,
        ApprovalRequestRow,
    )
    from prodavan.domain.agent.control_plane import ApprovalRequestStatus

    expected_scope = {
        "kind": "approval",
        "tenants": sorted(tenant_ids),
        "project": project_id,
    }
    after = decode_cursor(cursor, expected=expected_scope) if cursor else None
    query = (
        select(
            ApprovalRequestRow,
            AgentRunRow,
            AgentControlPlaneRunScopeRow,
            AgentControlPlaneAggregateRow.last_sequence,
        )
        .join(AgentRunRow, AgentRunRow.id == ApprovalRequestRow.run_id)
        .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == AgentRunRow.id)
        .join(
            AgentControlPlaneAggregateRow,
            and_(
                AgentControlPlaneAggregateRow.aggregate_type == "approval_request",
                AgentControlPlaneAggregateRow.aggregate_id == ApprovalRequestRow.id,
            ),
        )
        .where(
            AgentControlPlaneRunScopeRow.tenant_id.in_(tenant_ids),
            ApprovalRequestRow.status == ApprovalRequestStatus.PENDING.value,
        )
    )
    if project_id is not None:
        query = query.where(AgentControlPlaneRunScopeRow.project_id == project_id)
    if after is not None:
        requested_at = _parse_datetime(after.get("requested_at"))
        approval_id = _required_text(after, "approval_id")
        query = query.where(
            or_(
                ApprovalRequestRow.requested_at > requested_at,
                and_(ApprovalRequestRow.requested_at == requested_at, ApprovalRequestRow.id > approval_id),
            )
        )
    rows = (
        await session.execute(
            query.order_by(ApprovalRequestRow.requested_at, ApprovalRequestRow.id).limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items: list[ApprovalPageItem] = []
    for approval, run, scope, approval_sequence in page_rows:
        run_sequence = await session.scalar(
            select(AgentControlPlaneAggregateRow.last_sequence).where(
                AgentControlPlaneAggregateRow.aggregate_type == "agent_run",
                AgentControlPlaneAggregateRow.aggregate_id == run.id,
            )
        )
        if run_sequence is None:
            raise AgentRunPersistenceBlocked(
                "agent_control_plane_aggregate_cursor_missing",
                f"run aggregate cursor missing for {run.id}",
            )
        items.append(
            ApprovalPageItem(
                approval=approval,
                run=run,
                scope=scope,
                approval_sequence=approval_sequence,
                run_sequence=run_sequence,
            )
        )
    next_cursor = None
    if has_more and page_rows:
        last_approval = page_rows[-1][0]
        next_cursor = encode_cursor(
            {
                **expected_scope,
                "requested_at": last_approval.requested_at.isoformat(),
                "approval_id": last_approval.id,
            }
        )
    return ApprovalPage(items=tuple(items), has_more=has_more, next_cursor=next_cursor)


def _parse_datetime(value):
    from datetime import datetime

    if not isinstance(value, str):
        raise ValueError("cursor timestamp is missing")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("cursor timestamp must be timezone-aware")
    return parsed


def _required_text(payload, key):
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"cursor {key} is missing")
    return value


__all__ = ["list_pending_approvals"]
