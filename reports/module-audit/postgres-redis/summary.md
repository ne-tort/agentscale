# PostgreSQL + Redis disposable acceptance — 2026-11-10 (run f4d70277)

## Verdict

- tests/migration/plugin_postgres: **27 collected / 27 passed / 0 failed / 0 skipped** (exit 0).
- tests/integration/redis_plugin_acceptance: **22 collected / 22 passed / 0 failed / 0 skipped** (exit 0).
- Acceptance criterion collected>0, skipped=0, failures=0, errors=0 was re-asserted
  programmatically from the sanitized JUnit files in this directory.
- Static source guards from the PostgreSQL harness README: 3/3 green.
- Offline supporting checks: tests/unit/plugins/admin/test_update_lock.py **6/6**;
  secret-ref parity units (test_ci_publication_models.py,
  test_ci_publication_revision.py, test_source_reservation_revision.py) **11/11**.
- STOP-SHIP and live-production-unverified status are unchanged; this is
  disposable-only evidence, not dev/prod/live enablement.

## Exact invocations (cwd apps/api; PYTHONPATH=<abs src>; PYTHONUTF8=1; ambient
DATABASE_URL/proxy/application Redis env removed)

PostgreSQL:
  python -m pytest tests/migration/plugin_postgres -q --no-header --strict-markers -rs --confcutdir=tests/migration/plugin_postgres --junitxml=plugin-postgres-junit.xml
  Env names only: PRODAVAN_PLUGIN_TEST_ADMIN_DSN, PRODAVAN_PLUGIN_TEST_DSN
  (both pointed at the same unique disposable postgres:16.15-alpine container,
   dynamic 127.0.0.1 port; test-only credentials are not recorded here).

Redis:
  python -m pytest tests/integration/redis_plugin_acceptance --confcutdir=tests/integration/redis_plugin_acceptance -q -p no:cacheprovider --junitxml=redis-plugin-acceptance-junit.xml
  Env names only: REDIS_PLUGIN_ACCEPTANCE_REDIS_URL (unique disposable
  redis:7-alpine container, dynamic 127.0.0.1 port), REDIS_PLUGIN_ACCEPTANCE_REDIS_DB=14,
  REDIS_PLUGIN_ACCEPTANCE_REQUIRED=1,
  REDIS_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV=I_OWN_ISOLATED_TEST_RESOURCES.

Offline (package --confcutdir, -p no:cacheprovider):
  python -m pytest tests/unit/plugins/admin/test_update_lock.py --confcutdir=tests/unit/plugins/admin
  python -m pytest tests/unit/plugins/persistence/test_ci_publication_models.py tests/unit/plugins/persistence/test_ci_publication_revision.py tests/unit/plugins/persistence/test_source_reservation_revision.py --confcutdir=tests/unit/plugins/persistence

## Production fixes (parent-authorized, real-PG verified)

1. Secret-ref CHECK regex (PostgreSQL ARE rejects repeat counts >255; every
   plugin_module_sources INSERT failed with InvalidRegularExpressionError):
   - alembic/versions/2026110901_plugin_ci_publication.py
   - alembic/versions/2026110903_plugin_source_reservations.py
   - src/prodavan/infrastructure/persistence/models/plugin_platform.py
   Former {2,511} replaced by PG-compatible anchored pattern plus explicit
   length(regexp_replace(...)) BETWEEN 3 AND 512, preserving the exact original
   3..512-after-scheme contract (1 + 2..511).  Model/migration parity unit tests
   updated and green (11/11).
2. update_lock.py cleanup (infrastructure/persistence/repositories/plugins/):
   close-failure after successful rollback left the physical connection pooled
   (Connection.invalidate() on a closed connection raises, detach on
   AsyncConnection does not exist).  New staged fallback: async invalidate ->
   detach -> sync Connection.invalidate() on the still-open sync connection
   (bare sync detach leaves xact locks held) -> fresh-checkout invalidate when
   the session already returned the connection to the pool.  No session.bind
   mutation (that intermediate design was reverted; it also caused the unit
   '_OwnedConnection has no attribute connect' failure reported by triage).
   Real-PG lock file 11/11; offline unit 6/6.
3. artifacts.py: added missing dataclasses.replace import used by rollback
   replay/fingerprint callsites (NameError at line 1066 and siblings).

## Harness-only fixes (no production behavior changed)

- redis test_mcp_registration_acceptance.py: instrumentation assertions now read
  redis_pair.gateway.nx_miss_reread_values (was store attribute typo).
- redis test_quota_acceptance.py: OPS case now uses a valid ops=1 meter with a
  one-op grant exhausted by a real allowed invocation (production
  _CoreQuota._usage() rejects usage.ops != 1 fail-closed).
- PG test_installation_scope_migration.py: contype/confdeltype ::text casts and
  deparse-tolerant CHECK assertions (pg_get_constraintdef adds casts/parens).
- PG _scope_fixtures.py: FK-safe phased ORM seed flushes (unit-of-work emitted
  relationship-less CabinetInstanceRow before Company/Employee).
- PG test_update_lock.py: strong refs for dbapi_connection ids (CPython id
  reuse), and the detach double now really discards the physical connection via
  sync Connection.invalidate() (AsyncConnection has no detach(); bare sync
  detach keeps locks).
- unit admin/test_update_lock.py: pre-SQL cancellation test now uses the
  prepared single-session factory (the generic factory created a fresh session,
  deadlocking entered.wait()).

## Cleanup evidence

- docker rm -f agentscale-audit-pg-f4d70277 agentscale-audit-redis-f4d70277 —
  both returned names; follow-up docker ps -a filter shows no audit containers.
- Before removal, psql showed zero leftover plugin_pg_*/diag_* databases on the
  disposable server (per-test drops and diagnostic drops all completed).
- No Docker networks, no shell scripts, no docker-compose were created; no
  shared/dev/prod resources, no kubectl actions.  Pre-existing containers
  (itvzlet-*, pg-test, mig-int-pg, etc.) were not touched.
- Host incidents recovered during the window: one transient asyncpg WinError 121
  (semaphore timeout) and one Docker daemon restart that stopped both containers
  (exit 255).  Containers were restarted (new dynamic ports) and final green runs
  were executed after recovery; no evidence from the aborted run is used.

## Artifacts

- plugin-postgres-junit.xml (27/0/0/0), redis-plugin-acceptance-junit.xml (22/0/0/0)
  — sanitized (DSN/userinfo/secret-ref values stripped), post-sanitize scans empty.
- sha256-manifest.json — per-file sha256 + copy check + scan results + counts.
- docs/migration/plugin-migration-status.md rows and both harness READMEs updated
  with these final counts (merged with concurrent K8s/Kafka evidence rows).
- Not committed (per instruction).  No unit/integration/frontend tasks of other
  agents were run or modified beyond the listed harness/unit files.
