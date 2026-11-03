# MIG-AGENT-RUN-API-01 readiness blockers

Status: employee API implementation authored; production calls are fail-closed.

## Required before enabling `PRODAVAN_AGENT_RUN_API_ENABLED=1`

1. Register/export all six `AgentControlPlane*Row` classes from `prodavan.infrastructure.persistence.models.__init__`. `AgentRunRow` and `ApprovalRequestRow` are registered, but the aggregate cursor, command receipt, scope, event, outbox and enqueue-intent tables are not. The dependency checks `Base.metadata.tables` and returns HTTP 503 with blocker `agent_control_plane_models_not_registered` before importing or constructing the repository.
2. Apply the separately reviewed `2026110202_agent_control_plane_persistence` migration through the approved migration delivery path. The request dependency performs read-only `SELECT 1`/table probes; it never creates schema or performs DDL. Missing/unavailable tables return HTTP 503 with blocker `agent_control_plane_database_not_ready`.
until persisted enqueue intents are consumed, mutation dependencies return HTTP 503 blocker `agent_control_plane_dispatcher_unwired`. Even if the feature flag and model/DB gates pass, no run/approval command commits without a consumer explicitly being wired.

4. Add the required PostgreSQL concurrency and transaction-abort integration suite and obtain an explicit verification-window authorization. The API migration acceptance tests are authored and intentionally unexecuted.

## API contract

- Runs are execution records; they may reference a conversation `session_id` but never own or mutate its lifecycle.
- Run list/detail/create/cancel, tenant-scoped approval inbox/decision and ordered event timeline routes live in `api/v1/agent_runs.py`.
- Project access uses employee membership, cabinet assignment/grant and restricted-project visibility checks. The repository independently validates tenant/project/session/actor linkage in the atomic write transaction.
- Create, cancel and approval decisions require `Idempotency-Key`; cancel and decision requests also carry observed aggregate sequence(s) and expected status to make CAS/replay semantics explicit.
- Reads may use the new repository only after feature, model-registration and DB readiness gates pass. There is no memory-backed or legacy run/approval write fallback.
- Existing chat/session routes remain registered and unchanged; `run` remains distinct from `session`.

## Verification freeze

Tests, build, compile, lint, format, git checks, Alembic, DDL and live database writes were not run. No commit hash is available; changes remain uncommitted in the assigned migration worktree.
