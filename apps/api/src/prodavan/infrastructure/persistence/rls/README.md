# RLS: trusted tenant context and canonical policy registry

Installed by Alembic revision `2026110307` (parent `2026110306`). Two modules:

| Module | Role |
|---|---|
| `policies.py` | Declarative registry of canonical tables, predicates and guarded DDL. Imported by the revision *and* by tests so they cannot drift. |
| `context.py` | The only supported writer of transaction-local `app.tenant_id` / `app.cabinet_id` / `app.project_id`. |

## Invariants

1. **Server-resolved identity only.** `TrustedPrincipal` can be built only from a
   `ServerResolution` produced by `issue_server_resolution()` at the
   authentication boundary, and only for the closed set of sources
   (`session_cookie`, `bearer_jwt_verified`, `internal_service_mtls`,
   `migration_operator`). Direct construction raises
   `principal_not_server_resolved`, so a dict, a header or a plugin claim cannot
   be passed where a principal is expected.
2. **Claims are compared, never trusted.** A `CallerAssertion` that disagrees with
   the resolved identity is rejected with `cross_tenant_context`; a matching
   assertion is still not the authority - only the resolved values are written.
3. **Transaction-local, always.** Every statement uses `set_config(name, value,
   true)`. A session-level `SET` is never emitted (`transaction_local_set_forbidden`),
   and calling the context outside an explicit transaction fails with
   `transaction_required`. A pooled connection therefore cannot leak a tenant.
4. **Proven, not assumed.** After applying, the settings are read back; a
   mismatch aborts the transaction (`context_readback_mismatch`), as does
   `row_security` being off. A database role with `BYPASSRLS` or superuser is
   rejected (`database_role_bypasses_rls`) because the policies would not apply
   there.
5. **Restored on exit.** Previous values are captured before the write and
   restored afterwards, so nesting composes and a reused transaction cannot keep
   a stale tenant. On an exception path the caller rolls back and the
   transaction-local settings die with it (`context_restore_failed` is raised if
   a successful exit cannot prove restoration).
6. **Exactly one policy per operation per table.** `scope_registry` denies
   non-`active` rows to tenant sessions; archived/quarantined evidence stays
   reachable through the trusted service policy only. Tables without a tenant
   anchor render `USING (false)` - tenant sessions are denied, and no
   "platform-wide" default is invented.
7. **Fail-closed rows.** Child tables inherit scope through a typed parent chain
   (`materialization_entries -> project_runtime_revisions -> projects`,
   `scope_owner_mappings -> scope_registry`, grant mappings/quarantine through
   the grant). A row whose scope link is `NULL` or orphaned is invisible to
   tenant sessions - it is not treated as "unknown = allow".

## Trusted service path

`trusted_service_transaction()` is the only cross-tenant entry point. It requires
the exact database role `prodavan_service` **and** sets
`app.trusted_service='true'`; the second policy on every table checks both. A
tenant session that sets the flag itself still fails the role check, and a
session on the service role that forgets the flag still sees only its own
tenant.

## Not in scope

RLS bounds *visibility*. It does not authorize a canonical positive write:
registry rows, parent `EXISTS`, definition/version/artifact equality, exact
plugin instance, subject/session binding, writer epoch, audit append and role
privileges all remain separate gates. Privilege grants (`SELECT/INSERT/UPDATE`
per role) and append-only enforcement for audit tables are still missing and are
recorded as blockers, not silently assumed.

## Rollback

Downgrade drops the two policies per table, `NO FORCE`s and `DISABLE`s row level
security. No rows, grants or evidence are touched. Re-applying upgrade converges
(`DROP POLICY IF EXISTS` precedes every `CREATE POLICY`).

## Verification freeze

Acceptance artifacts under `apps/api/tests/migration/rls/` are authored, not
executed. No test, build, lint, compile, Alembic command or database operation
was run for this task.
