# MIG-AUTO-01 promotion record

- Record ID: `MIG-AUTO-01:blocked:preflight:2026-11-01`
- Task: `MIG-AUTO-01`
- Status: `blocked`
- Partition: `none admitted; tenant/project/endpoint/target ownership inventory absent`
- Source event/schema/signature policy: `fixture only; upstream contract not evidenced`
- Run/task/event/approval counts: `0; no ingress accepted or acceptance fixture executed`
- Canonical automation read/write owner: `unchanged; no mapping or durable adapter promoted`
- `canonical_positive_writes`: `false`
- Promotion/cutover authorized: `false`
- Actor: `delegated implementation agent`
- Decision owner: `migration coordinator`
- Reviewer/signature: `pending`

## Deliverables

- Endpoint/target mapping and compatibility-route contract with exact-one resolution.
- Inbound verification gate; no persistence call occurs for missing/ambiguous mapping or invalid signature.
- Scoped idempotency identity and digest-bound claim contract with task intent, source provenance, agent-run idempotency key and route linkage.
- Durable control-plane port specifies one atomic transaction for claim, task intent, `agent_runs`, ordered event(s), and durable approval linkage. Replay returns original identifiers; changed digest conflicts.
- Acceptance fixtures and contract declarations are present under the exclusive migration test zone and were not executed.

## Blocker and gate

No production persistence adapter was found that atomically coordinates automation ingress claim/task intent with agent-run, ordered-event, outbox and approval records. Existing standalone automation and agent control-plane contracts do not prove this cross-aggregate transaction. This is a hard blocker: implement and review a durable transactional adapter/schema in its authorized zone before enabling ingress. Do not substitute process memory, cache, queue-level dedupe, read-then-write, or implicit legacy fallback. Until then, adapter absence must yield `blocked` and produce no accepted task/run/event.

Promotion remains blocked until endpoint/target ownership and source verification policy are evidenced; transactional uniqueness and atomic commit across the linked records are demonstrated; event ordering and approval lifecycle linkage are proven; compatibility routes are reviewed; acceptance is explicitly authorized and passes; and the migration coordinator records a superseding decision.

## Rollback

No endpoint mapping was activated and no ingress data or canonical state was written; rollback is a no-op. Keep existing route/read/write ownership unchanged. If a later partition is enabled and fails, disable only that mapping, stop new claims, retain accepted immutable claims/runs/events/approvals and outbox intents for audit/recovery, and reconcile forward from durable evidence. Never delete accepted run or event history as rollback.

This is an immutable blocked preflight, not a promotion, persistence-readiness claim, or cutover authorization. Any correction requires a superseding record linked to this ID.
