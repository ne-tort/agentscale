# MIG-DDL-RLS-01 — acceptance artifact (authored, not executed)

Status: `done` for authoring; **not verified by execution**. Verification freeze applies:
no test, build, lint, compile, `alembic upgrade`, database or git command was run.

Deliverables under review:

- `apps/api/alembic/versions/2026110307_rls_policies.py` (revision `2026110307`, parent `2026110306`)
- `apps/api/src/prodavan/infrastructure/persistence/rls/policies.py` (policy registry, single source of truth)
- `apps/api/src/prodavan/infrastructure/persistence/rls/context.py` (trusted transaction context)
- `apps/api/src/prodavan/infrastructure/persistence/rls/__init__.py`, `README.md`
- `apps/api/tests/migration/rls/` (this catalog)

## 1. Coverage: one policy per operation per table

The revision walks `CANONICAL_TABLE_POLICIES`; the tests assert the registry they walk is the same object.
Every registered table gets exactly two policies with unique names:

| Policy | Command | Predicate | Purpose |
|---|---|---|---|
| `<table>_tenant_isolation` | `FOR ALL` | `USING` **and** `WITH CHECK` on `current_setting('app.tenant_id'|'app.cabinet_id'|'app.project_id', true)` | tenant traffic |
| `<table>_trusted_service_access` | `FOR SELECT` (or `FOR ALL` for evidence tables) | `current_user = 'prodavan_service' AND nullif(current_setting('app.trusted_service', true), '') = 'true'` | service/trusted read |

Registered tables (23), by owning revision:

| Owner | Tables |
|---|---|
| `2026110301` | `scope_owner_tenant_anchors`, `scope_owner_cabinet_anchors`, `scope_owner_project_anchors`, `scope_registry`, `scope_owner_mappings`, `scope_owner_quarantine`, `scope_owner_audit` |
| `2026110302` | `m04e_dynamic_app_definitions`, `m04e_dynamic_app_versions`, `m04e_definition_version_promotion_evidence` |
| `2026110303` | `m05_plugin_instances`, `m05_plugin_instance_registry_links`, `m05_plugin_instance_promotion_evidence` |
| `2026110304` | `dynamic_app_capability_grants`, `dynamic_app_capability_grant_mappings`, `dynamic_app_capability_grant_quarantine`, `dynamic_app_capability_audit`, `dynamic_app_capability_writer_epochs`, `m06_canonical_grant_promotion_evidence` |
| `2026110306` | `project_runtime_revisions`, `materialization_entries` |

Scope columns: direct `tenant_id` everywhere it exists; `cabinet_id`/`project_id` added where the model actually
carries that anchor (`scope_registry`, anchors, `m05_plugin_instances`(+links), `dynamic_app_capability_grants`).
Inherited (no copied column): `scope_owner_mappings -> scope_registry`,
`dynamic_app_capability_grant_mappings -> dynamic_app_capability_grants`,
`project_runtime_revisions -> projects(company_id, cabinet_id)`,
`materialization_entries -> project_runtime_revisions -> projects`.
Missing link ⇒ `EXISTS` is false ⇒ deny (never "unknown = allow").

Reviewed exclusions (registry `EXCLUDED_TABLES`): `capability_bundles` (global, no tenant anchor),
`resources`/`resource_versions`/`resource_bindings` (polymorphic scope, deferred with a recorded blocker),
equipment v2 tables (owned by `2026110305`), legacy platform anchors (`companies`, `cabinet_instances`,
`cabinet_company_grants`, `projects`, `memberships`). `uncovered_relations()` plus the revision's catalog query
log a warning for any canonical-prefixed relation that has neither a policy nor an exclusion, so a sibling
revision cannot add a table silently.

## 2. Idempotence and partial schema

Per table, one `DO $rls$ ... $rls$` block:

1. `to_regclass('<table>') IS NULL` ⇒ `RAISE NOTICE 'rls 2026110307 upgrade skipped: relation <t> is absent'` + `RETURN`;
2. `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` / `... FORCE ROW LEVEL SECURITY` (repeat-safe);
3. `DROP POLICY IF EXISTS` before every `CREATE POLICY` (repeat-safe, converges after manual repair);
4. no `EXCEPTION` handler — a genuine DDL failure aborts instead of being swallowed.

After the loop the revision logs every absent registry relation at `WARNING` and every uncovered canonical
relation at `WARNING`; the skip is reported twice (server `NOTICE` + Alembic log), never silent.
Upgrade runs its own catalog query, so `env.py` is unchanged and offline/sql mode is handled explicitly.

## 3. Downgrade

`DROP POLICY IF EXISTS` (both policies) → `NO FORCE ROW LEVEL SECURITY` → `DISABLE ROW LEVEL SECURITY`,
guarded by the same `to_regclass` check. No `DROP TABLE`, `DELETE` or `TRUNCATE` anywhere in the generated SQL
(asserted by test). Data is untouched.

## 4. Trusted tenant context

`persistence/rls/context.py`:

| Guarantee | Mechanism | Reason code |
|---|---|---|
| Only server-resolved identity | `ServerResolution` (sealed, `init=False`, only `issue_server_resolution`) → `TrustedPrincipal` (sealed, `from_resolution` only, allow-listed sources) | `principal_not_server_resolved` |
| Claim ≠ authority | `CallerAssertion` compared, never written | `cross_tenant_context` |
| Transaction-local only | `set_config(..., true)` in every statement; static guard rejects bare `SET`; `in_transaction()` required | `transaction_local_set_forbidden`, `transaction_required` |
| Proof of application | readback of all five GUCs + `row_security` on | `context_readback_mismatch`, `row_security_disabled` |
| No bypass | `pg_roles.rolbypassrls`/`rolsuper` rejected; non-PostgreSQL dialect rejected | `database_role_bypasses_rls`, `postgresql_required` |
| Restore | previous values captured, restored and re-verified on success; on error the caller's rollback ends the transaction | `context_restore_failed` |
| Service path | `trusted_service_transaction` requires role `prodavan_service` **and** sets `app.trusted_service='true'`; flag cleared on exit | `trusted_service_role_mismatch` |
| Expiry / shape | timezone-aware expiry checked per transaction; project without cabinet rejected | `tenant_context_expired`, `tenant_context_invalid` |

Available entry points: `trusted_tenant_context`, `trusted_tenant_transaction`, `trusted_service_transaction`
(async) and `trusted_tenant_context_sync`, `trusted_tenant_transaction_sync` (sync).

## 5. Negative cases (catalog)

Proven by `test_trusted_context.py` / `test_visibility_matrix.py`; IDs match the existing M04/M06 review vocabulary.

| ID | Case | Expected |
|---|---|---|
| RLS-001 | no tenant context at all (`current_setting` unset) | predicate is NULL ⇒ 0 rows in every table |
| RLS-002 | empty context (`''`) | `nullif('')` is NULL ⇒ 0 rows |
| RLS-003 | cross-tenant `SELECT` (tenant A context, tenant B row) | 0 rows |
| RLS-004 | cross-tenant `INSERT` (row of tenant B under tenant A `WITH CHECK`) | violation ⇒ rejected |
| RLS-005 | cross-tenant `UPDATE` (A sees nothing; moved row fails `WITH CHECK`) | 0 rows / rejected |
| — | cross-cabinet within one tenant | 0 rows |
| — | project GUC differing from `project_id` | 0 rows |
| RLS-006 | context set outside a transaction | `transaction_required` |
| RLS-007 | caller-supplied tenant/cabinet/project that contradicts the resolution | `cross_tenant_context`, no SQL emitted |
| RLS-008 | dict/claim used as a principal or resolution | `principal_not_server_resolved` |
| RLS-009 | expired resolution | `tenant_context_expired` |
| RLS-010 | GUC not applied / corrupted | `context_readback_mismatch` |
| RLS-011 | `BYPASSRLS` or superuser role | `database_role_bypasses_rls` |
| RLS-012 | tenant session sets `app.trusted_service` itself | trusted policy requires the role too ⇒ no cross-tenant rows |
| RLS-013 | archived/quarantined `scope_registry` row | `owner_state='active'` ⇒ 0 rows |
| RLS-014 | child row with missing/orphan parent | parent `EXISTS` false ⇒ 0 rows |
| RLS-015 | session-level `SET app.tenant_id` | structurally impossible (`transaction_local_set_forbidden`) |
| — | sqlite/other dialect | `postgresql_required` |
| — | tampered principal (tenant re-pointed after resolution) | digest mismatch ⇒ `tenant_context_invalid` |
| — | `migration_operator` source without `migration_role` | `principal_not_server_resolved` |

## 6. Known risks / blockers (not silently accepted)

1. **Deployment ordering.** `FORCE ROW LEVEL SECURITY` applies to the table owner. A session that is neither
   `prodavan_service` nor carrying a transaction-local context reads 0 rows from all 23 tables. The revision
   must ship together with runtime wiring of `rls/context.py`; applying it earlier fail-closes (no data loss,
   but no API reads either).
2. **Privileges are not granted here.** `GRANT SELECT/INSERT/UPDATE` per role and the `prodavan_service` role
   itself (with `BYPASSRLS` explicitly *not* set) remain a separate, reviewed operation. `APP_DB_URL` must use a
   non-superuser, non-`BYPASSRLS` role.
3. **Audit append-only enforcement** (`dynamic_app_capability_audit`, `scope_owner_audit`) still needs
   `REVOKE UPDATE, DELETE` + triggers; RLS bounds visibility only.
4. **`resources*` tables** are created by `2026110306` with polymorphic `(scope_kind, scope_id)` and have no safe
   policy yet — recorded as an exclusion, requires a follow-up revision (blocker, not a silent skip).
5. **`equipment_*` v2** tables are excluded here; their policy review belongs to `2026110305`'s adapter.
6. **Legacy platform anchors** (`companies`, `cabinet_instances`, `cabinet_company_grants`, `projects`) remain
   without RLS; the canonical predicates read them. A platform-level RLS decision is still open.
7. **No live verification.** Predicate semantics are reasoned from PostgreSQL behaviour (NULL comparison denies,
   `current_setting(..., true)` returns NULL when unset) and exercised only through the in-memory stand-in.
