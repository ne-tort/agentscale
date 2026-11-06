"""MIG-WEB-RUNBIND-01 acceptance fixtures; test execution is intentionally frozen."""

from datetime import UTC, datetime

from prodavan.application.agent.run_binding import TurnBindingKey

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)

TENANT = "tenant-runbind"
OTHER_TENANT = "tenant-runbind-other"
PROJECT = "project-runbind"
OTHER_PROJECT = "project-runbind-other"
SESSION = "session-runbind"
OTHER_SESSION = "session-runbind-other"
ACTOR = "employee-runbind"
TRACE = "trace-runbind"

TURN_A = TurnBindingKey(project_id=PROJECT, session_id=SESSION, turn_id="turn-a")
TURN_B = TurnBindingKey(project_id=PROJECT, session_id=SESSION, turn_id="turn-b")

CASES = (
    "a turn start creates exactly one durable run and a replay returns that same run",
    "the binding key is the durable agent_runs.idempotency_key, so resolution is a database read",
    "a turn without a durable run is refused and no run id is synthesised",
    "a foreign tenant is refused before any write reaches the atomic port",
    "an inactive project or a session of another project is refused",
    "a terminal turn is terminal: no second run is created for it",
    "stream frames receive unique, strictly monotonic run-aggregate sequences",
    "re-delivering the same frame returns its sequence and creates no second row",
    "a frame is one atomic write carrying its own event, outbox projection and queue intent",
    "frame sequences follow the durable aggregate cursor, never a local counter",
    "the stream-frame operation is rejected by today's contract as a named fail-closed blocker",
    "stream frames are disabled by default and refuse without touching the atomic port",
    "a second concurrently active run on one session is refused",
    "a frame payload never reaches the audit trail, only its digest",
    "finishing a turn is terminal and idempotent, and later frames are refused",
)

assert TURN_A.run_command_key != TURN_B.run_command_key
assert TURN_A.run_command_key.startswith("turn-run:")
assert len(TURN_A.run_command_key) <= 128
assert NOW.tzinfo is not None
