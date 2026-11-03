# MIG-API-AGENT-RUNS-02 blockers and acceptance contract

Status: scoped query pages and route contracts are authored; production run/approval writes remain fail-closed.

## Endpoints in `api/v1/agent_runs.py`

- `GET /projects/{project_id}/agent/runs`: tenant/project scoped stable keyset page (`cursor`), legacy `offset` remains supported; `X-Has-More` and `X-Next-Cursor` response headers advertise continuation.
- `POST /projects/{project_id}/agent/runs`: durable create with `Idempotency-Key`; currently returns the explicit write blocker before attempting a commit.
- `GET /projects/{project_id}/agent/runs/{run_id}`: tenant/project scoped detail with durable run sequence and original session linkage from run-scope row.
- `POST /projects/{project_id}/agent/runs/{run_id}/cancel`: command receipt replay, expected sequence/status CAS and idempotency key; currently fail-closed until dispatcher wiring is enabled.
- `GET /agent/approvals/inbox`: tenant-set/project ACL scoped approval inbox, stable cursor page with `X-Has-More`/`X-Next-Cursor`.
- `POST /projects/{project_id}/agent/approvals/{approval_id}/decision`, `/approve`, and `/reject`: approve/reject with approval and run CAS versions plus idempotency key; action aliases validate that the requested decision matches the path. Currently fail-closed.
- `POST /projects/{project_id}/agent/approvals/{approval_id}/cancel`: cancel approval and linked run atomically with both CAS versions plus idempotency key; currently fail-closed.
- `GET /projects/{project_id}/agent/runs/{run_id}/events`: append-only ordered timeline; aggregate sequence cursor remains available, and an opaque cursor pages the full ordered stream.

## Missing production adapters / blockers

1. `prodavan.infrastructure.persistence.models.__init__` does not register/export six new control-plane tables. The feature-gated dependency rejects missing tables with `agent_control_plane_models_not_registered`.
2. The `2026110202_agent_control_plane_persistence` migration must be applied through approved migration delivery. No request path runs DDL.
3. No consumer claims `agent_control_plane_enqueue_intents`; create/cancel/approval writes remain blocked with `agent_control_plane_dispatcher_unwired`. A durable enqueue-intent row is not broker delivery.
4. There is no durable Agent Run execution worker/lease adapter, provider attempt journal, generation fencing, or unknown-outcome reconciler. Blockers are `agent_run_durable_claim_and_lease_unwired` and `agent_run_provider_outcome_reconciliation_unwired`. The new `AgentRunExecutionPort` is a contract, not an implementation. No in-memory queue is used.
5. No durable Agent Run retry scheduler/state exists. `schedule_retry` is an execution-port requirement only; unknown provider outcomes must be reconciled and cannot be scheduled for blind retry.
6. PostgreSQL integration coverage for simultaneous repository idempotency reservation, aggregate CAS, transaction abort, and migration apply remains pending the verification window.

## Acceptance vectors authored (not executed)

- Tenant/project/run cursor binding and malformed cursor rejection.
- Timeline cursor orders by aggregate type/id/sequence/event ID; event occurred-at remains data, not the aggregate order key.
- Session association is read from immutable run scope; no session lifecycle mutation occurs.
- Approve-vs-deny same expected approval/run sequences: one winner, stale peer conflicts; two aggregate events/queue intents are atomic.
- Run cancel replay returns the original durable receipt; create replay does not duplicate event/outbox/intent.
- Stale lease generation/token/sequence fence requirements and cancel generation increment are explicit in the execution port.
- Unknown provider outcome blocks blind retry; only known retryable failures can carry a durable `retry_at`.

No tests, build, compile, lint, format or Git checks were run, per migration verification freeze and the task request.
