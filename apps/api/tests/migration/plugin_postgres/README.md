# Disposable real-PostgreSQL plugin acceptance harness

**Status (2026-11-10):** executed against a uniquely named disposable Docker
PostgreSQL 16.15-alpine container: **27 collected / 27 passed / 0 failed /
0 skipped** using the exact invocation below (PYTHONPATH=src, PYTHONUTF8=1).
Sanitized JUnit + summary + SHA256 manifest: reports/module-audit/postgres-redis/.
No live, dev, prod or shared database was touched; containers were removed after
the run.  Earlier authoring was static-only.

## Coverage

* fresh and historical pre-0901 upgrades to head ``2026110909``;
* no downgrade or stamping: every database starts empty and is dropped after each test;
* repeat ``upgrade head`` and ``alembic check``;
* constraints/defaults/FK for ``plugin_artifact_lifecycle`` and
  ``command_fingerprint``;
* historical 0907 rollback row without ``command_fingerprint`` upgraded through
  0908 to 0909, then rejected as exact replay evidence without mutation;
* 0909 ``plugin_installation_scopes`` PK/FK/unique/checks/defaults and nullable
  cabinet/project pairs on ModuleSource/source reservations;
* legacy/random/ambiguous pre-0909 rows remain NULL with no installation-scope
  backfill; paired scope CHECK violations are rejected by real PostgreSQL;
* real READY ModuleSource + Company/Cabinet/Project + active installation/grant
  through ``SqlInstallationScopeRepository.ensure_from_source``;
* private bootstrap returns exact company/cabinet/project, wrong/missing binding
  fails closed, and public bootstrap stays company-only with/without durable scope;
* real concurrent publish first-INSERT exact replay and semantic mismatch;
* publish/promote/rollback failure injection with complete stored-state
  assertions;
* full A→B→A→B→A rollback generations and same-generation lost-response replay;
* distributed ``SqlPluginUpdateLock`` contention, owner-only release, xact/rollback
  cleanup, direct close, pre-SQL and post-SQL/before-adapter-response cancellation,
  rollback/close invalidation, real detach fallback, new physical connection on retry,
  production-pool connection reuse, and no cross-tenant collision.

## Boundaries

* Disposable PostgreSQL only. The harness never reads ``DATABASE_URL`` or the
  application ``settings.database_url`` except to patch that setting to the
  random test database while invoking committed Alembic commands.
* Pending ``2026110802`` is never copied, registered, stamped or applied.
* No ``stamp``, ``revision --autogenerate`` or manual ALTER is performed.
* No SQLite substitute or in-memory registry is used.
* Post-SQL cancellation pauses after the real driver result but before the adapter
  receives it. No packet-level/network-loss evidence is claimed. Detach fallback is
  exercised through a test-only proxy that calls the real disposable connection detach.

## Required environment

Both names are explicit and required for an acceptance run. Database-backed
acceptance tests use the real-PostgreSQL fixture and the ``integration`` plus
``plugin_postgres`` markers:

* ``PRODAVAN_PLUGIN_TEST_ADMIN_DSN`` — privileged DSN to a disposable server
  (must be able to CREATE/DROP DATABASE), for example
  ``postgresql+asyncpg://test:test@127.0.0.1:55432/postgres``;
* ``PRODAVAN_PLUGIN_TEST_DSN`` — required DSN used for each random database; its
  database name is replaced per test, and it must point at the same disposable
  server, for example
  ``postgresql+asyncpg://test:test@127.0.0.1:55432/plugin_acceptance``.

If ``PRODAVAN_PLUGIN_TEST_ADMIN_DSN`` is absent, test setup skips locally with
an explicit reason. If the variables are set and an acceptance run still
contains skipped tests, that run is invalid; acceptance evidence requires
``collected > 0`` and ``skipped == 0``.


## Static guards (no database or pytest startup)

Run these source-only checks before the acceptance command. They do not import
project settings, start PostgreSQL, read DATABASE_URL, or execute migrations:

```bash
cd apps/api
python -c "from pathlib import Path; p=Path('tests/migration/plugin_postgres/conftest.py').read_text(encoding='utf-8'); assert 'pool_pre_ping=True' in p and 'poolclass=NullPool' not in p; assert p.index('    _assert_alembic_chain_is_safe()\n    from prodavan') < p.index('command.upgrade'); assert 'actual_head == HEAD_REVISION' in p and 'assert_no_pending_0802_in_chain()' in p"
python -c "from pathlib import Path; p=Path('src/prodavan/infrastructure/persistence/repositories/plugins/update_lock.py').read_text(encoding='utf-8'); assert 'pg_try_advisory_xact_lock' in p and 'pg_advisory_unlock' not in p and 'connection.invalidate()' in p and 'connection.detach()' in p"
python -c "from pathlib import Path; root=Path('tests/migration/plugin_postgres'); readme=(root/'README.md').read_text(encoding='utf-8'); assert '--confcutdir=tests/migration/plugin_postgres' in readme and '--no' + 'noconftest' not in readme; assert all('command.upgrade' not in p.read_text(encoding='utf-8') for p in root.glob('test_*.py'))"
```

The first guard proves production pool options and that the centralized head/0802
check precedes command.upgrade. The second forbids a return to session-level
unlock semantics. The third proves isolated conftest loading and that no test
module bypasses the centralized Alembic seam. These static checks complement,
but never replace, the JUnit assertion below requiring collected > 0, skipped = 0,
failures = 0 and errors = 0.

## Disposable database

Reuse the project CI pattern (``postgres:16.15-alpine``). This container is
test-only and must not hold application/shared data:

```bash
docker run -d --name plugin-pg-acceptance \
  -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=postgres \
  -p 55432:5432 postgres:16.15-alpine

docker exec plugin-pg-acceptance pg_isready -U test -d postgres

export PRODAVAN_PLUGIN_TEST_ADMIN_DSN="postgresql+asyncpg://test:test@127.0.0.1:55432/postgres"
export PRODAVAN_PLUGIN_TEST_DSN="postgresql+asyncpg://test:test@127.0.0.1:55432/plugin_acceptance"
cd apps/api
python -m pytest tests/migration/plugin_postgres -q --no-header --strict-markers -rs \
  --confcutdir=tests/migration/plugin_postgres \
  --junitxml=plugin-postgres-junit.xml
# confcutdir prevents parent tests/conftest.py (and its default DSN/probes) from loading.
python -c "import xml.etree.ElementTree as ET; r=ET.parse('plugin-postgres-junit.xml').getroot(); assert int(r.attrib['tests']) > 0 and int(r.attrib['skipped']) == 0 and int(r.attrib['failures']) == 0 and int(r.attrib['errors']) == 0"
```

On Windows PowerShell use the same disposable-only values:

```powershell
$env:PRODAVAN_PLUGIN_TEST_ADMIN_DSN = "postgresql+asyncpg://test:test@127.0.0.1:55432/postgres"
$env:PRODAVAN_PLUGIN_TEST_DSN = "postgresql+asyncpg://test:test@127.0.0.1:55432/plugin_acceptance"
```

Cleanup:

```bash
docker rm -f plugin-pg-acceptance
```

Each test creates and drops its own ``plugin_pg_<20 hex>`` database. A failed
run may leave one such database behind; it is recognisable by the prefix and can
be removed from the disposable server only. Never point the DSNs at live,
shared, dev, staging or prod infrastructure.
