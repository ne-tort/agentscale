# MIG-AGENT-RUN-API-02 readiness blockers

Status: employee API wiring is authored and the durable dispatcher is connected behind the feature
gate. `PRODAVAN_AGENT_RUN_API_ENABLED` remains off by default; when it is off the dependency still
fails closed with `AGENT_RUN_API_DISABLED` / `agent_run_api_feature_flag_disabled`.

## Closed by this task

1. **Model registration.** All six `AgentControlPlane*Row` classes are imported and exported by
   `prodavan.infrastructure.persistence.models.__init__` and appear in `Base.metadata`. The
   dependency now compares `REQUIRED_MODEL_TABLES` against the live `Base.metadata.tables` and
   returns HTTP 503 blocker `agent_control_plane_models_not_registered` (code
   `AGENT_RUN_PERSISTENCE_NOT_READY`, with `missing_tables`) only if a table is genuinely absent.
2. **Dispatch.** `api/migration_agent_runs/dependencies.py` binds the durable consumer
   (`build_intent_dispatcher` over `agent_control_plane_enqueue_intents`, queue
   `agent-control-plane`). Create, cancel and every approval decision/cancel call
   `dispatch_after_commit(...)` after the atomic commit, so a write no longer leaves an enqueue
   intent without a consumer. The consumer is transport-neutral and durable: it claims with
   `FOR UPDATE SKIP LOCKED` under a database-clock lease, fences every later write and records a
   redacted attempt record. A failed/timed-out/skipped trigger degrades to a reported reason code;
   the pending intent row stays the durable handoff for the next round.
3. **Write gate.** `writes_enabled` is a gate result, not a constant: `True` only after the flag,
   the metadata gate and the read-only database probe pass, and only with a composed consumer. With
   the flag off, `require_writes()` refuses with `AGENT_RUN_API_DISABLED`; with the flag on but no
   consumer, it refuses with `agent_control_plane_dispatcher_unavailable` rather than committing
   intents nobody can claim.
4. **Atomicity and contract invariants kept.** Events, outbox projection and enqueue intents remain
   one `ControlPlaneAtomicWrite` in one repository transaction; the dispatch trigger never writes
   domain state. `Idempotency-Key` stays mandatory, CAS stays on observed sequence/status, replay
   returns the original receipt, the timeline cursor stays ordered, and a run is still not a
   session.

## Still required before enabling the flag in a deployed environment

1. Apply the separately reviewed `2026110202_agent_control_plane_persistence` migration through the
   approved delivery path. The dependency only performs read-only probes; missing tables answer 503
   `agent_control_plane_database_not_ready`.
2. Wire an `AgentRunExecutionPort` adapter (durable claim/lease, provider attempt journal,
   generation fencing, cancel bump). Until then the consumer records
   `intent_handler_not_wired` retries and never claims delivery; unknown provider outcomes still
   cannot be reconciled (`agent_run_provider_outcome_reconciliation_unwired`).
3. Complete RLS policy verification and security review for the write paths.
4. Obtain explicit production approval for `PRODAVAN_AGENT_RUN_API_ENABLED=1`.
5. Add the PostgreSQL concurrency and transaction-abort integration suite (simultaneous idempotency
   reservation, aggregate CAS races, rollback) and an explicit verification-window authorization.

## API contract

- Runs are execution records; they may reference a conversation `session_id` but never own or mutate its lifecycle.
- Run list/detail/create/cancel, tenant-scoped approval inbox/decision and ordered event timeline routes live in `api/v1/agent_runs.py`.
- Project access uses employee membership, cabinet assignment/grant and restricted-project visibility checks. The repository independently validates tenant/project/session/actor linkage in the atomic write transaction.
- Create, cancel and approval decisions require `Idempotency-Key`; cancel and decision requests also carry observed aggregate sequence(s) and expected status to make CAS/replay semantics explicit.
- Mutation responses carry `dispatch` (redacted counters + reason) next to `queue_intents_recorded`; `queue_intents_recorded` still means "durable intent rows written", never "delivered".
- Reads and writes use only the gated durable repository. There is no memory-backed or legacy run/approval fallback.
- Existing chat/session routes remain registered and unchanged; `run` remains distinct from `session`.

## Verification freeze

Tests, build, compile, lint, format, git checks, Alembic, DDL and live database writes were not run.
Read-only import/metadata introspection was used to confirm the gate now passes. No commit hash is
available; changes remain uncommitted in the assigned migration worktree.
