"""MIG-AUTO-03 acceptance fixtures; test execution is intentionally frozen."""

from datetime import UTC, datetime, timedelta

from prodavan.application.agent_control_plane import (
    ControlPlaneLinkage,
    RunTransitionOperation,
)
from prodavan.domain.agent.control_plane import AgentRunStatus, ApprovalRequestStatus

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
LINKAGE = ControlPlaneLinkage(
    tenant_id="tenant-1",
    actor_id="employee-1",
    project_id="project-1",
    session_id="session-1",
    trace_id="trace-1",
)
RUN_ID = "agr_fixture"
APPROVAL_ID = "apr_fixture"
APPROVAL_EXPIRY = NOW + timedelta(minutes=10)

CASES = (
    "run creation allocates aggregate sequence one through the durable transaction",
    "run transitions use expected-sequence compare-and-set and reject stale writers",
    "approval creation atomically moves the run to waiting_approval and creates approval sequence one",
    "approval approve/deny/cancel/expire transitions update approval and linked run in one transaction",
    "run cancellation and timeout are terminal transitions with durable reason/error evidence",
    "command replay returns the original result without a second event or queue intent",
    "same command identity with changed digest is a conflict and preserves first provenance",
    "transition idempotency keys and aggregate queue intent keys are stable across retries",
    "event, outbox event, and queue intent share event identity and one atomic result",
    "write exceptions are surfaced as stable control-plane errors with no fallback write",
    "actor, tenant, project, session, and trace linkage is present in command and event payload",
    "missing transaction wiring returns an explicit blocker and performs no write",
    "approval expiry requires an aware timestamp at or after expires_at",
    "run-level fail/cancel/timeout cannot orphan an active approval while the run waits for approval",
    "the durable port result confirms matching event, outbox key, and queue deduplication key",
)

assert RUN_ID and APPROVAL_ID and LINKAGE.tenant_id
assert AgentRunStatus.WAITING_APPROVAL.value == "waiting_approval"
assert ApprovalRequestStatus.EXPIRED.value == "expired"
assert RunTransitionOperation.TIMEOUT.value == "timeout"
