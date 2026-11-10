# Regrant CAS guard fix + fd6 atomicity evidence — 2026-11-10

## Fix (production, parent-authorized)

infrastructure/persistence/repositories/plugins/grants.py regrant:
authoritative UPDATE ... RETURNING via result.scalars().all() with exactly-one-row
requirement replaces result.rowcount (unavailable on SQLite
ChunkedIteratorResult).  Zero/multiple rows => PluginRepositoryUnavailable
("regrant: SQL не подтвердил замену revoked записи"); REVOKED/instance/tenant
binding, same-id REGRANT_ID_CONFLICT pre-gate and returned-binding verification
are unchanged.

## Results

- New real-SQLite regression tests/integration/plugins/test_grant_regrant_sqlite.py:
  success on real aiosqlite + zero-row RETURNING typed failure with rollback: 2/2.
- Existing unit tests/unit/plugins/repositories/test_grant_regrant.py: 4/4 (targeted total 6/6).
- Full tests/integration/plugins: **27 passed / 0 failed / 0 skipped** (exit 0).
- Real disposable PostgreSQL 16.15-alpine inline scenario against alembic head:
  REAL_PG_REGRANT_SUCCESS (new id ACTIVE, old id gone, tenant/plugin/instance
  binding preserved); REAL_PG_ZERO_ROW_TYPED_FAILURE (race DELETE between SELECT
  FOR UPDATE and UPDATE -> typed unavailable, revoked original restored by
  rollback, no candidate committed).  Diagnostic database dropped.
- fd6 follow-up: real disposable PG tests/migration/plugin_postgres/test_artifact_atomicity.py
  after _recover_publication_replay hardening: **5 collected / 5 passed / 0 skipped**
  (plugin-postgres-atomicity-fd6-junit.xml, sanitized).

## Cleanup

- psql showed zero leftover plugin_pg_*/regrant_diag_*/diag_* databases, then
  docker rm -f agentscale-audit-pg-f4d70277 (name returned; ps -a filter empty).
- No networks/scripts/compose, no shared/dev/prod resources, no kubectl; other
  containers untouched.  resolver/install.py, artifacts replay fix,
  MinIO/Kafka/frontend not modified.
