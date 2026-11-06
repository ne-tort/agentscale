# MIG-AUTO-03 promotion record

- Task: `MIG-AUTO-03`
- Status: `blocked: durable persistence wiring absent`
- Scope: `application/agent_control_plane/**`; tests: `tests/migration/agent_control_plane/**`
- Runtime/API/ORM/legacy outbox changes: `none`
- Canonical run/approval writes enabled: `false`
- Acceptance tests executed: `false`

## Delivered contracts

- Run lifecycle transitions use explicit expected status and expected aggregate sequence, with terminal cancellation and timeout commands.
- Approval create/decision/cancel/expiry commands carry run linkage and CAS versions for both the approval and its run.
- One `ControlPlaneAtomicWrite` carries the command digest, ordered domain events, outbox events, and queue intents. The outbox intent key is derived from tenant, aggregate identity, and aggregate version.
- Event and command identifiers are deterministic. The durable adapter contract requires command/transition replay to return the original result, rejects changed-digest key reuse, and requires a single transaction for run/approval state, events, and queue intents.
- Actor, tenant, project, session, and trace linkage is validated and preserved in each event envelope.
- Persistence failures are raised as stable `ControlPlaneWriteError`; there is no separate-repository, in-memory, or legacy-outbox fallback.

## Blocker

No production repository implements `AgentControlPlaneTransactionPort.commit(write=...)`, and no application wiring supplies that port. Existing run/approval repository contracts and the legacy outbox `record_and_enqueue` port are separate operations; they cannot prove an atomic state + event + outbox transaction or aggregate sequence CAS. The service therefore returns `blocked` with `agent_control_plane_atomic_transaction_adapter_unwired` when no transaction port is supplied. Do not enable durable writes until a transaction adapter and its wiring are implemented in an authorized zone and the acceptance suite is explicitly opened.

## Verification gate

Tests were authored but intentionally not run. No backend tests, builds, compilation, lint/format, git checks, API/ORM edits, or legacy outbox edits were performed.
