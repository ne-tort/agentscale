# MIG-M05-02 acceptance fixtures

Status: `blocked` (fail-closed); baseline `2026-11-01`.

These are acceptance fixtures and required behaviors, not executed verification or evidence of production readiness. Import and resolution remain disabled until upstream gates are supplied.

## Required fixture cases

1. `resume_same_digest`: same `(source_kind, source_id, schema_version, digest)` replay returns `REPLAY`, preserves original mapping/checkpoint, does not create a second target identity, and does not emit a distinct positive audit outcome.
2. `resume_changed_digest`: same source key/schema with a changed digest returns `CONFLICT` with `source_digest_changed`; prior mapping and registry state are immutable.
3. `atomic_checkpoint`: imported/quarantined record and checkpoint commit atomically; crash before commit replays the same row, crash after commit resumes strictly after `source_id`.
4. `bounded_page`: reader is called with configured page limit, sorted deterministically, duplicate source IDs rejected, and next page starts after committed source ID.
5. `dependency_gate_missing`: unavailable M04 typed tenant/scope/definition/version evidence returns `BLOCKED`; no candidate read or canonical identity creation occurs.
6. `resolve_exact_binding`: ResolvePluginInstancePort requires exact instance, definition, version, tenant, typed scope, artifact digest, subject, session, runtime generation and writer generation. Any omission/mismatch denies without exposing a record.
7. `lifecycle_denied`: archived/quarantined/missing state never resolves active; transition operations require persistence CAS and active-state evidence.
8. `runtime_generation_monotonic`: successful runtime CAS is exactly `expected + 1`; stale expected generation, no-op, rollback, skipped generation, wrong instance or non-active result denies.
9. `writer_generation_cas`: successful writer CAS advances exactly one while matching runtime generation; concurrent stale writer gets conflict and cannot write.
10. `stale_worker_deny`: worker with stale runtime or writer generation is denied even if its lease/identity otherwise matches.
11. `audit_redacted`: audit contains event, outcome, stable reason, correlation and optional numeric generations only; no raw payload, tokens, credentials, arguments, tenant/scope labels, or full identity.
12. `audit_failure`: if mandatory audit append fails, caller observes failure and no positive capability is returned.
13. `blocked_records_not_authority`: blocked, quarantined, conflict and archived evidence is never consumed as positive resolver evidence.

## Safety assertions

- Default `dependencies_ready=false`; no import acceptance or positive resolution is enabled by default.
- Caller-supplied identity fields are lookup constraints, not proof of authority.
- Persistence adapters must implement atomic record/checkpoint and database-side compare-and-set; a read-then-write implementation is insufficient.
- The existing plugin-instance write fence remains authoritative; this task does not wire a production resolver or enable writes.
- Legacy remains the read/write owner while gates are blocked.
- Scenarios are specifications only; tests/build/lint/format/compile/git checks were intentionally not run.
