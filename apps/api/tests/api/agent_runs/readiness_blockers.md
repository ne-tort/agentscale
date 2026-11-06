# MIG-AR-API-02 blockers and acceptance contract

Status: the durable dispatcher is wired behind the feature gate. Reads are tenant/project scoped;
writes are opt-in (`PRODAVAN_AGENT_RUN_API_ENABLED`) and fail closed otherwise. Several
production prerequisites are still open (see below) — the API is not claimed as generally
available.

## Endpoints in `api/v1/agent_runs.py`

- `GET /projects/{project_id}/agent/runs`: tenant/project scoped stable keyset page (`cursor`), legacy `offset` remains supported; `X-Has-More` and `X-Next-Cursor` response headers advertise continuation.
- `POST /projects/{project_id}/agent/runs`: durable create with mandatory `Idempotency-Key`. After the atomic commit it triggers the durable consumer (see "Dispatch") and returns `dispatch`.
- `GET /projects/{project_id}/agent/runs/{run_id}`: tenant/project scoped detail with durable run sequence and original session linkage from run-scope row.
- `POST /projects/{project_id}/agent/runs/{run_id}/cancel`: command receipt replay, expected sequence/status CAS and idempotency key; dispatches after commit.
- `GET /agent/approvals/inbox`: tenant-set/project ACL scoped approval inbox, stable cursor page with `X-Has-More`/`X-Next-Cursor`.
- `POST /projects/{project_id}/agent/approvals/{approval_id}/decision`, `/approve`, and `/reject`: approve/reject with approval and run CAS versions plus idempotency key; action aliases validate that the requested decision matches the path; dispatches after commit.
- `POST /projects/{project_id}/agent/approvals/{approval_id}/cancel`: cancel approval and linked run atomically with both CAS versions plus idempotency key; dispatches after commit.
- `GET /projects/{project_id}/agent/runs/{run_id}/events`: append-only ordered timeline; aggregate sequence cursor remains available, and an opaque cursor pages the full ordered stream.

## Resolved by MIG-AR-API-02

1. **Model registration gate is real.** `models/__init__` exports all six `AgentControlPlane*Row`
   classes, so `dependencies.missing_model_tables(set(Base.metadata.tables))` is empty in this
   worktree. The gate still computes the difference against live metadata and returns HTTP 503
   `AGENT_RUN_PERSISTENCE_NOT_READY` / blocker `agent_control_plane_models_not_registered` (with
   `missing_tables`) if a table is ever dropped from registration.
2. **Dispatch is wired.** `api/migration_agent_runs/dependencies.py` composes the durable consumer
   from `infrastructure/persistence/agent_control_plane/intent_consumer.py`
   (`build_intent_dispatcher`, queue `agent-control-plane`). Mutating routes call
   `AgentRunBackend.dispatch_after_commit(...)` strictly after the control-plane transaction
   returned `applied`/`duplicate`; the trigger runs a bounded (`<= 25` intents, 2 s timeout)
   `dispatch_batch` and reports redacted counters in `dispatch`. A commit therefore never leaves an
   intent "queued without a consumer": the consumer claims it in the same request, and if the
   trigger fails/times out, the pending row stays claimable by the next round. The old blanket
   `agent_control_plane_dispatcher_unwired` refusal is gone; the remaining refusal is
   `agent_control_plane_dispatcher_unavailable`, raised only when the consumer cannot be composed
   (writes stay closed instead of writing intents nobody can claim).
3. **Feature gate is explicit.** Default off. `writes_enabled` is no longer a constant `False`: it
   becomes `True` only after the flag, the metadata gate and the read-only database probe pass.
4. **Atomicity preserved.** Domain events, the outbox projection and the enqueue intents are still
   built by `DurableAgentControlPlane` into one `ControlPlaneAtomicWrite` and committed by one
   repository transaction. The dispatch trigger runs after that commit and never writes domain
   state.

## Remaining blockers

1. Revision `2026110202_agent_control_plane_persistence` must be applied through the approved
   migration delivery path. The request dependency performs only read-only `SELECT 1`/`LIMIT 0`
   probes; no request path runs DDL. Missing/unavailable tables return HTTP 503 with blocker
   `agent_control_plane_database_not_ready`.
2. No `AgentRunExecutionPort` adapter exists, so the consumer has an empty handler registry:
   committed intents are claimed and recorded as a redacted `intent_handler_not_wired` retry (up to
   `DISPATCH_MAX_ATTEMPTS`, ~1 day of exponential backoff) instead of being reported delivered.
   Actual execution still needs the durable claim/lease worker, provider attempt journal and
   generation fencing (`agent_run_durable_claim_and_lease_unwired`), plus the unknown-outcome
   reconciler (`agent_run_provider_outcome_reconciliation_unwired`). No in-memory queue is used.
3. No durable retry scheduler exists for runs. `schedule_retry` remains an execution-port
   requirement only; unknown provider outcomes must be reconciled and are never blind-retried.
4. RLS policy verification and security review for the write paths are not done; metadata
   registration and the flag are not authorization signals.
5. Explicit production approval is required before enabling the flag in a deployed environment.
6. PostgreSQL integration coverage for simultaneous repository idempotency reservation, aggregate
   CAS, transaction abort, and migration apply remains pending the verification window.

## Acceptance vectors authored (not executed)

- `tests/api/agent_runs/test_dispatch_after_commit.py`
  - create writes event + outbox + enqueue intent in one `ControlPlaneAtomicWrite`;
  - the consumer is triggered only after the commit returned, ordered `commit → dispatch`;
  - create replay returns the original receipt (same run/event/intent) and never writes twice;
  - cancel with a stale `expected_sequence` writes nothing and dispatches nothing, then succeeds at
    the observed sequence;
  - approve-vs-deny with the same expected sequences: one `applied`, one `conflict`, exactly one
    committed write with two events/two intents, one dispatch;
  - every mutation route dispatches after its `_write_error` guard;
  - `AgentRunDispatchReport` rejects negative counters.
- `tests/migration/agent_run_api/test_dispatch_wiring.py`
  - flag off → 503 `AGENT_RUN_API_DISABLED`, no probe, no repository composition;
  - missing table in metadata → 503 `agent_control_plane_models_not_registered` with
    `missing_tables`, before any database probe;
  - unavailable table → 503 `agent_control_plane_database_not_ready`, session rolled back;
  - all gates passing → `writes_enabled=True`, repository bound, `control_plane().is_wired` true,
    consumer bound;
  - consumer composition failure → reads available, `require_writes()` refuses with
    `agent_control_plane_dispatcher_unavailable`;
  - dispatch trigger is bounded (`MAX_DISPATCH_BATCH`), skipped when no intent was written,
    reported (not raised) on failure and on timeout.
- Pre-existing vectors kept: tenant/project/run cursor binding and malformed cursor rejection;
  timeline ordering by aggregate type/id/sequence/event ID; session association read from immutable
  run scope; stale lease generation/token/sequence requirements; unknown provider outcome blocks
  blind retry.

No tests, build, compile, lint, format or Git checks were run, per migration verification freeze.
