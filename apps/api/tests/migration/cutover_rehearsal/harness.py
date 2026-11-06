"""Harness for the end-to-end cutover rehearsal (task ``MIG-REHEARSAL-01``).

Why this package exists
=======================
Every stage of the migration chain (``M04`` scope evidence -> ``M04E``
definition/version ledger -> ``M05`` instance resolution -> ``M06`` parity ->
cutover) has unit coverage, but the chain was never executed *together against a
migrated PostgreSQL*.  A port that satisfies its protocol on SQLite and its own
unit test can still be unusable in the composed pipeline: the RLS predicates are
``FORCE ROW LEVEL SECURITY``, the ledgers are append-only by trigger, the
checkpoints are advanced by CAS, and the trusted writer is a real database role.
This harness runs the real services against a real database and refuses to
pretend otherwise.

Isolation: one database per session, one schema per test
========================================================
The rehearsal needs three things a throwaway *schema* cannot give it on its own:

* the row level security the migration installs (``rls.policies`` registry);
* the append-only / artifact guards, which are **trigger functions** -- created
  unqualified by the revisions, so bound to whichever schema is first on
  ``search_path`` at install time;
* the ``prodavan_service`` database role, which the trusted-service policies name
  and which ``rls.context`` refuses to substitute with a superuser.

So each test gets a fresh ``rehearsal_<hex>`` schema, built from
``Base.metadata`` and then dressed with the *revision modules' own* guard and
policy DDL (see :func:`_install_schema_guards`).  Nothing is written to
``public``; the schema is dropped in teardown, so the rehearsal cannot disturb
``postgres_integration``, ``rls`` or anything else sharing the database.

Why the rehearsal connects *as a role*
=====================================
``rls.context`` refuses to bind a tenant context on a ``BYPASSRLS``/superuser
role: setting the GUCs there would only *look* isolated.  Every port connection
here therefore opens with ``server_settings={"role": "prodavan_service"}`` --
``current_user`` is the trusted writer role the policies name, ``session_user``
is the harness login, ``row_security`` is ``on``.  That is the shape a deployed
worker has, and it is what makes the RLS and append-only assertions meaningful.
The role is created ``NOLOGIN`` with grants on the rehearsal schema alone: it is
a fixture, never a production configuration.

Environment
===========
``PRODAVAN_TEST_DATABASE_URL`` is the only accepted DSN; without it (or with an
unreachable server) the package *skips* with a printed reason instead of silently
testing nothing.  Importing this module opens no connection and runs no DDL --
every side effect lives inside a fixture.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import socket
import uuid
import warnings
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillCheckpoint,
    MigrationEvidenceContext,
    ScopeCandidate,
    ScopeMigrationCoordinator,
    ScopeStreamIdentity,
    SourceRowState,
)
from prodavan.application.migration.m04_scope.scope_persistence import (
    SqlAlchemyScopeBackfillAdapter,
)
from prodavan.application.migration.m04e_definition_version.artifact import (
    ArtifactContentPort,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillContext,
    CandidateDecision,
    EvidenceCandidate,
    EvidenceStatus,
    UpstreamGateEvidence,
)
from prodavan.application.migration.m04e_ports import (
    SqlM04ECandidateReader,
    SqlM04EEvidenceStore,
    SqlM04EUpstreamGate,
)
from prodavan.application.migration.principal_resolver import (
    build_durable_principal_resolver,
)
from prodavan.application.migration.tenant_context import (
    RlsTenantScopeContextAdapter,
    RlsTrustedTenantContextAdapter,
)
from prodavan.application.scope_registry.contracts import RlsContext, ScopeAnchors, ScopeKind
from prodavan.infrastructure.persistence.rls.policies import (
    CANONICAL_TABLE_POLICIES,
    TRUSTED_SERVICE_ROLE,
)

TEST_DATABASE_URL_ENV: Final = "PRODAVAN_TEST_DATABASE_URL"
ASYNC_PG_DRIVER: Final = "postgresql+asyncpg"
SCHEMA_PREFIX: Final = "rehearsal"

#: ``23514`` -- ``check_violation``, the SQLSTATE every guard in this chain raises.
CHECK_VIOLATION: Final = "23514"

API_ROOT: Final = Path(__file__).resolve().parents[3]
"""``apps/api`` -- this file is ``tests/migration/cutover_rehearsal/<file>``."""

VERSIONS_DIR: Final = API_ROOT / "alembic" / "versions"


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #
def _as_asyncpg_url(raw: str) -> str:
    """Normalise a PostgreSQL DSN to the asyncpg driver, or fail loudly."""

    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError(
            f"{TEST_DATABASE_URL_ENV} must be a PostgreSQL DSN, got driver {url.drivername!r}"
        )
    if url.drivername != ASYNC_PG_DRIVER:
        url = url.set(drivername=ASYNC_PG_DRIVER)
    if not url.database:
        raise pytest.UsageError(f"{TEST_DATABASE_URL_ENV} must name a database")
    return url.render_as_string(hide_password=False)


def _tcp_reachable(url: str, *, timeout: float = 1.0) -> bool:
    """TCP reachability only -- a plain socket, no event loop involved."""

    parsed = make_url(url)
    try:
        with socket.create_connection(
            (parsed.host or "localhost", parsed.port or 5432), timeout=timeout
        ):
            return True
    except OSError:
        return False


def database_url_from_env() -> str | None:
    """The normalised rehearsal DSN, or ``None`` when the variable is unset."""

    raw = (os.getenv(TEST_DATABASE_URL_ENV) or "").strip()
    if not raw:
        return None
    return _as_asyncpg_url(raw)


@pytest.fixture(scope="session")
def rehearsal_dsn() -> str:
    """The migrated PostgreSQL under rehearsal; skips (with a reason) when absent."""

    url = database_url_from_env()
    if url is None:
        pytest.skip(
            f"{TEST_DATABASE_URL_ENV} is not set: the cutover rehearsal runs only against a "
            "PostgreSQL with the migration chain applied. Point it at "
            "postgresql+asyncpg://test:test@127.0.0.1:55432/mig_fresh (see README.md)."
        )
    if not _tcp_reachable(url):
        pytest.skip(
            "PostgreSQL is not reachable at "
            f"{make_url(url).render_as_string(hide_password=True)} "
            f"({TEST_DATABASE_URL_ENV} is set but nothing listens on that endpoint)."
        )
    return url


def _run_in_new_loop(factory) -> Any:
    """Run one coroutine on a private loop and close it.

    Used only from *synchronous* fixtures: ``asyncio.run`` refuses to start when
    pytest-asyncio already installed a loop in this thread, the same
    Windows-Proactor problem ``tests/migration/postgres_integration`` records.
    """

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(factory())
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# revision modules: the only honest source of the guard DDL
# --------------------------------------------------------------------------- #
def load_revision(prefix: str) -> Any:
    """Import one alembic revision module by its numeric prefix.

    Alembic executes revisions; it never imports them into the application, so the
    guard SQL lives only in the revision file.  Loading that file is what keeps this
    rehearsal from *restating* (and eventually misstating) the guards: the triggers a
    rehearsal schema gets are byte-for-byte the ones a migrated database got.
    """

    matches = sorted(VERSIONS_DIR.glob(f"{prefix}_*.py"))
    if len(matches) != 1:
        raise RuntimeError(f"revision {prefix} resolves to {len(matches)} files: {matches}")
    spec = importlib.util.spec_from_file_location(f"rehearsal_rev_{prefix}", matches[0])
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"revision {prefix} is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True, slots=True)
class GuardStatement:
    """One guard the chain installs: a function plus the triggers that call it."""

    revision: str
    function_sql: str
    function_name: str
    #: ``(trigger_name, table, full CREATE TRIGGER statement)`` per trigger.
    triggers: tuple[tuple[str, str, str], ...]
    reason: str


def guard_statements() -> tuple[GuardStatement, ...]:
    """Every guard (function + triggers + reason code) the migration chain installs."""

    ledger = load_revision("2026110315")
    cutover = load_revision("2026110316")
    artifact = load_revision("2026110302")

    def trigger(name: str, table: str, events: str, function: str) -> tuple[str, str, str]:
        return (name, table, f"CREATE TRIGGER {name} {events} ON {table} "
                             f"FOR EACH ROW EXECUTE FUNCTION {function}()")

    return (
        GuardStatement(
            revision=str(ledger.revision),
            function_sql=ledger._CREATE_APPEND_ONLY_FUNCTION,
            function_name=ledger.APPEND_ONLY_FUNCTION,
            triggers=tuple(
                trigger(
                    ledger.APPEND_ONLY_TRIGGER.format(table=table),
                    table,
                    "BEFORE UPDATE OR DELETE",
                    ledger.APPEND_ONLY_FUNCTION,
                )
                for table in ledger.APPEND_ONLY_TABLES
            ),
            reason=str(ledger.APPEND_ONLY_REASON),
        ),
        GuardStatement(
            revision=str(cutover.revision),
            function_sql=cutover._CREATE_APPEND_ONLY_FUNCTION,
            function_name=cutover.APPEND_ONLY_FUNCTION,
            triggers=(
                trigger(
                    cutover.APPEND_ONLY_TRIGGER,
                    cutover.LEDGER_TABLE,
                    "BEFORE UPDATE OR DELETE",
                    cutover.APPEND_ONLY_FUNCTION,
                ),
            ),
            reason=str(cutover.APPEND_ONLY_REASON),
        ),
        GuardStatement(
            revision=str(artifact.revision),
            function_sql=artifact._CREATE_ARTIFACT_GUARD_FUNCTION,
            function_name=artifact.ARTIFACT_GUARD_FUNCTION,
            triggers=(
                trigger(
                    artifact.ARTIFACT_GUARD_TRIGGER,
                    artifact.VERSION_TABLE,
                    "BEFORE UPDATE OR DELETE",
                    artifact.ARTIFACT_GUARD_FUNCTION,
                ),
            ),
            reason=str(artifact.ARTIFACT_GUARD_REASON),
        ),
        GuardStatement(
            revision=str(ledger.revision),
            function_sql=ledger._CREATE_CHECKPOINT_GUARD_FUNCTION,
            function_name=ledger.CHECKPOINT_GUARD_FUNCTION,
            triggers=(
                trigger(
                    ledger.CHECKPOINT_GUARD_TRIGGER,
                    ledger.CHECKPOINT_TABLE,
                    "BEFORE UPDATE",
                    ledger.CHECKPOINT_GUARD_FUNCTION,
                ),
            ),
            reason=str(ledger.CHECKPOINT_REGRESSION_REASON),
        ),
    )


async def _install_schema_guards(
    admin: AsyncEngine, schema: str, rows_schema: str
) -> None:
    """Install the chain's guard functions and triggers over ``rows_schema``'s tables.

    Two things have to be respected, and getting either wrong silently disables the
    guards:

    * ``CREATE TRIGGER ... ON <table>`` resolves the relation through
      ``search_path`` **at install time** and then stores a table OID.  A trigger
      created while ``search_path`` pointed at the rehearsal schema would therefore
      attach to the rehearsal copy of the table, not to the shared one the ports
      actually read.  The relation is qualified explicitly for that reason.
    * ``CREATE FUNCTION`` is *not* schema-qualified by the revisions, so the function
      lands in the first schema on ``search_path``.  That is where the trigger's
      ``EXECUTE FUNCTION`` name is resolved too (function names are bound late, by
      name + ``search_path``), so the guidance functions must exist in the schema the
      **ports** run in -- ``rows_schema``.

    ``search_path`` is pinned to ``rows_schema`` and every table is written
    ``rows_schema.<table>``, so the triggers end up on exactly the relations the stage
    code writes to, whether or not the rehearsal schema shadows them.
    """

    async with admin.begin() as conn:
        await conn.execute(text(f'SET LOCAL search_path TO "{rows_schema}"'))
        for guard in guard_statements():
            await conn.execute(text(guard.function_sql))
            for trigger_name, table, ddl in guard.triggers:
                qualified = ddl.replace(f" ON {table} ", f' ON "{rows_schema}".{table} ', 1)
                # ``CREATE TRIGGER`` has no ``IF NOT EXISTS``.  The ``rows_in_public``
                # mode runs against a schema where ``alembic upgrade head`` already
                # installed this exact trigger, and a second ``CREATE`` is an error;
                # dropping first makes the install idempotent.  It is safe because the
                # trigger is recreated from the revision's own DDL immediately after,
                # and the fixture is the only writer at this point.
                await conn.execute(
                    text(f'DROP TRIGGER IF EXISTS {trigger_name} ON "{rows_schema}".{table}')
                )
                await conn.execute(text(qualified))


async def _install_schema_policies(admin: AsyncEngine, schema: str) -> None:
    """Install the reviewed tenant/trusted policies from ``rls.policies``.

    ``tests/migration/rls`` proves a migrated database carries them; a rehearsal schema
    is created by hand, so it has to be dressed the same way or the isolation assertions
    would be measuring an unprotected table.  The statements come from the registry
    revision ``2026110307`` renders, so the rehearsal cannot drift from a migrated
    schema.  Only relations present in the schema are touched.
    """

    from prodavan.infrastructure.persistence.rls import policies

    async with admin.begin() as conn:
        await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        present = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class AS c "
                        "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relkind = 'r'"
                    ),
                    {"schema": schema},
                )
            ).all()
        }
        for spec in CANONICAL_TABLE_POLICIES:
            if spec.table not in present:
                continue
            for statement in policies.upgrade_statements(spec):
                await conn.execute(text(statement))


async def _ensure_trusted_role(admin: AsyncEngine, schema: str) -> None:
    """Create the trusted writer role (idempotently) and grant it the schema.

    ``NOLOGIN`` on purpose: nothing connects *as* the role.  The harness and the ports
    reach it through ``server_settings={"role": ...}`` on their own authenticated
    connection, which is how a deployed worker runs.
    """

    async with admin.begin() as conn:
        await conn.execute(
            text(
                "DO $$ BEGIN\n"
                f"    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{TRUSTED_SERVICE_ROLE}') "
                "THEN\n"
                f"        CREATE ROLE {TRUSTED_SERVICE_ROLE} NOLOGIN;\n"
                "    END IF;\n"
                "END $$"
            )
        )
        await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {TRUSTED_SERVICE_ROLE}'))
        await conn.execute(
            text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" '
                f"TO {TRUSTED_SERVICE_ROLE}"
            )
        )
        await conn.execute(
            text(
                f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" '
                f"TO {TRUSTED_SERVICE_ROLE}"
            )
        )


# --------------------------------------------------------------------------- #
# session-scoped precondition
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RehearsalDatabase:
    """The migrated database under rehearsal plus the role it is reached as."""

    dsn: str
    alembic_version: str
    head_revision: str
    required_revision: str
    trusted_role: str
    guards: tuple[GuardStatement, ...]

    @property
    def at_head(self) -> bool:
        return self.alembic_version == self.head_revision

    @property
    def satisfies_chain(self) -> bool:
        """Is the stamp at least the revision this package actually rehearses?

        Revision ids are fixed-width decimal strings, so comparing them as text is
        the same as comparing them chronologically.
        """

        return self.alembic_version >= self.required_revision


#: The newest revision this rehearsal *depends on*: ``2026110316`` creates the cutover
#: ledger and its append-only guard, ``2026110315`` the M04E ledger, ``2026110317``
#: ``auth_sessions`` (the credential the principal resolver reads) and ``2026110319``
#: the equipment anchors.  A database below it cannot host the rehearsal at all, so a
#: stamp below this is a hard failure; a stamp *between* it and the script head means
#: the database is simply not upgraded yet, which is a fixture state and skips with the
#: command to fix it rather than failing every stage for the same reason.
REQUIRED_REVISION: Final = "2026110319"


def _head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("version_path_separator", "os")
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:  # pragma: no cover - a script directory always has a head
        raise RuntimeError("the alembic script directory has no head revision")
    return head


@pytest.fixture(scope="session")
def rehearsal_database(rehearsal_dsn: str) -> RehearsalDatabase:
    """Prove the DSN belongs to a *migrated* database before anything is written.

    A database that never ran the chain has no policies, no ledgers and no history to
    rehearse; the fixture skips with the exact revision it found instead of letting
    every stage test fail for the same uninteresting reason.  A database *below*
    :data:`REQUIRED_REVISION` fails instead of skipping: that is not a missing fixture,
    it is a database the rehearsal cannot honestly describe.
    """

    async def _probe() -> str | None:
        engine = create_async_engine(rehearsal_dsn, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return await conn.scalar(text("SELECT version_num FROM alembic_version"))
        finally:
            await engine.dispose()

    version = _run_in_new_loop(_probe)
    if version is None:
        pytest.skip(
            "the database has no alembic_version row, so the migration chain was never "
            "applied to it; point PRODAVAN_TEST_DATABASE_URL at a migrated database"
        )
    stamped = str(version)
    head = _head_revision()
    database = RehearsalDatabase(
        dsn=rehearsal_dsn,
        alembic_version=stamped,
        head_revision=head,
        required_revision=REQUIRED_REVISION,
        trusted_role=TRUSTED_SERVICE_ROLE,
        guards=guard_statements(),
    )
    if not database.satisfies_chain:
        raise AssertionError(
            f"the rehearsal database is stamped {stamped!r}, below the newest revision it "
            f"depends on ({REQUIRED_REVISION!r}); the chain is not applied far enough to "
            "rehearse: run `alembic upgrade head` against PRODAVAN_TEST_DATABASE_URL"
        )
    return database


# --------------------------------------------------------------------------- #
# one isolated schema
# --------------------------------------------------------------------------- #
def _create_metadata_tables(connection) -> None:
    """Create every ORM table in the first schema on ``search_path``.

    ``checkfirst=False`` on purpose: the existence probe would look through
    ``search_path`` and could conclude "already there" from the ``public`` copy the
    chain built, quietly binding the rehearsal to the shared schema.
    """

    from prodavan.infrastructure.persistence.models import Base

    Base.metadata.create_all(connection, checkfirst=False)


def _schema_engine(dsn: str, schema: str, *, as_role: str | None) -> AsyncEngine:
    """A ``NullPool`` engine pinned to one schema, optionally acting as a role.

    ``NullPool`` is required: the RLS and append-only assertions need *independent*
    connections that cannot inherit a neighbour's ``search_path`` or transaction-local
    GUCs, and ``server_settings`` is applied to every new connection.
    """

    settings = {"search_path": f"{schema},public"}
    if as_role is not None:
        settings["role"] = as_role
    return create_async_engine(
        dsn, poolclass=NullPool, connect_args={"server_settings": settings}
    )


async def _drop_schema(admin: AsyncEngine, name: str) -> None:
    with contextlib.suppress(Exception):
        async with admin.begin() as conn:
            await conn.execute(text("SET lock_timeout = '5s'"))
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))


#: Every table a rehearsal writes, in an order that satisfies the foreign keys on the
#: way out (children before parents).  Used only for the ``rows_in_public`` mode, where
#: the rows share the migrated schema and must be removed explicitly.
PURGE_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("m06_partition_cutover_evidence", "tenant_id"),
    ("m06_partition_cutover_source_tail", "tenant_id"),
    ("m06_canonical_grant_promotion_evidence", "tenant_id"),
    ("dynamic_app_capability_audit", "tenant_id"),
    ("dynamic_app_capability_grant_mappings", "id"),
    ("dynamic_app_capability_grant_quarantine", "resolved_tenant_id"),
    ("dynamic_app_capability_grants", "tenant_id"),
    ("dynamic_app_capability_writer_epochs", "tenant_id"),
    ("m05_plugin_instance_promotion_evidence", "tenant_id"),
    ("m05_plugin_instance_registry_links", "tenant_id"),
    ("m05_plugin_instances", "tenant_id"),
    ("m04_scope_promotion_evidence", "tenant_id"),
    ("m04e_definition_version_promotion_evidence", "tenant_id"),
    ("m04e_definition_version_audit", "tenant_id"),
    ("m04e_definition_version_quarantine", "tenant_id"),
    ("m04e_definition_version_checkpoints", "tenant_id"),
    ("m04e_definition_version_mappings", "tenant_id"),
    ("m04e_dynamic_app_versions", "tenant_id"),
    ("m04e_dynamic_app_definitions", "tenant_id"),
    ("scope_registry", "tenant_id"),
    ("scope_owner_mappings", "scope_registry_id"),
    ("scope_owner_quarantine", "resolved_tenant_id"),
    ("scope_owner_audit", "id"),
    ("scope_owner_project_anchors", "tenant_id"),
    ("scope_owner_cabinet_anchors", "tenant_id"),
    ("scope_owner_tenant_anchors", "tenant_id"),
    ("runtime_materialization_audit", "project_id"),
    ("runtime_materialization_attempts", "project_id"),
    ("runtime_revision_heads", "project_id"),
    ("materialization_entries", "revision_id"),
    ("project_runtime_revisions", "project_id"),
    ("dynamic_app_versions", "app_definition_id"),
    ("dynamic_app_definitions", "tenant_id"),
    ("auth_sessions", "company_id"),
    ("cabinet_company_grants", "company_id"),
    ("projects", "company_id"),
    ("memberships", "company_id"),
    ("cabinet_instances", "id"),
    ("employees", "id"),
    ("companies", "id"),
)


async def _purge_partitions(admin: AsyncEngine, tenant_ids: list[str]) -> None:
    """Remove every row a shared-schema rehearsal wrote, for exactly these tenants.

    ``rows_in_public`` mode exists to exercise the guards the *migration* installed, so
    it writes into the migrated schema.  That makes cleanup the harness's job: every
    delete is scoped by the tenant (or by a child key derived from it), so a concurrent
    suite using the same database cannot lose a row -- and an immutable ledgers row is
    only reproducible because it belongs to this tenant.

    Failures are ``warnings``, not errors: a cleanup problem must not replace the real
    assertion result with a teardown traceback.
    """

    if not tenant_ids:
        return
    statements = _purge_statements(tenant_ids)
    try:
        async with admin.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            for statement, params in statements:
                await conn.execute(text(statement), params)
    except Exception as exc:  # pragma: no cover - only on a leaked connection
        warnings.warn(
            f"could not purge rehearsal rows for {tenant_ids}: {exc}. Delete them manually; "
            "a leaked connection is the usual cause.",
            stacklevel=2,
        )


def _purge_statements(tenant_ids: list[str]) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The deletes :func:`_purge_partitions` runs, as ``(sql, params)`` pairs.

    Split out so the ordering can be asserted by a test without opening a database.
    """

    scoped: list[tuple[str, dict[str, Any]]] = []
    for table, column in PURGE_ORDER:
        if column == "tenant_id":
            scoped.append(
                (f'DELETE FROM "{table}" WHERE tenant_id = ANY(:tenants)', {"tenants": tenant_ids})
            )
        elif column == "scope_registry_id":
            scoped.append((
                f'DELETE FROM "{table}" WHERE scope_registry_id IN '
                "(SELECT id FROM scope_registry WHERE tenant_id = ANY(:tenants))",
                {"tenants": tenant_ids},
            ))
        elif column == "resolved_tenant_id":
            scoped.append((
                f'DELETE FROM "{table}" WHERE resolved_tenant_id = ANY(:tenants)',
                {"tenants": tenant_ids},
            ))
        elif column == "project_id":
            scoped.append((
                f'DELETE FROM "{table}" WHERE project_id IN '
                "(SELECT id FROM projects WHERE company_id = ANY(:tenants))",
                {"tenants": tenant_ids},
            ))
        elif column == "revision_id":
            scoped.append((
                f'DELETE FROM "{table}" WHERE revision_id IN '
                "(SELECT revision_id FROM project_runtime_revisions WHERE project_id IN "
                "(SELECT id FROM projects WHERE company_id = ANY(:tenants)))",
                {"tenants": tenant_ids},
            ))
        elif column == "app_definition_id":
            scoped.append((
                f'DELETE FROM "{table}" WHERE app_definition_id IN '
                "(SELECT id FROM dynamic_app_definitions WHERE tenant_id = ANY(:tenants))",
                {"tenants": tenant_ids},
            ))
        elif column == "id":
            # ``employees``/``companies``/``cabinet_instances`` and the two ledgers keyed
            # by their own id: the seeded ids embed the tenant suffix, so the tenant list
            # reaches them through the parent rows above.  ``companies`` is last.
            if table == "employees":
                scoped.append((
                    'DELETE FROM "employees" WHERE id IN '
                    "(SELECT employee_id FROM memberships WHERE company_id = ANY(:tenants))",
                    {"tenants": tenant_ids},
                ))
            elif table == "cabinet_instances":
                scoped.append((
                    'DELETE FROM "cabinet_instances" WHERE company_id = ANY(:tenants)',
                    {"tenants": tenant_ids},
                ))
            elif table == "companies":
                scoped.append(
                    ('DELETE FROM "companies" WHERE id = ANY(:tenants)', {"tenants": tenant_ids})
                )
            else:
                scoped.append(
                    (f'DELETE FROM "{table}" WHERE id = ANY(:tenants)', {"tenants": tenant_ids})
                )
        else:  # pragma: no cover - PURGE_ORDER is a closed list
            raise AssertionError(f"unhandled purge key for {table}.{column}")
    return tuple(scoped)


# --------------------------------------------------------------------------- #
# deterministic value helpers
# --------------------------------------------------------------------------- #
def new_suffix() -> str:
    return uuid.uuid4().hex[:10]


def sha256_digest(value: Any) -> str:
    """``sha256:<64 hex>`` over the canonical JSON encoding the chain itself uses."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def jsonb(value: Any) -> str:
    """Canonical JSON text for a ``CAST(:value AS jsonb)`` parameter."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def as_text(value: object) -> str | None:
    """Normalise a PostgreSQL ``"char"`` value to ``str``.

    ``polcmd``/``relkind``/``tgtype``-adjacent columns are ``"char"`` and asyncpg hands
    those back as ``bytes`` (``b'r'``); decoding once here keeps every assertion honest
    about the type it compares.
    """

    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def utcnow() -> datetime:
    """A *fresh* aware instant.

    Deliberately a function: an ``EXP_IN_15_MIN``-style value computed at import drifts
    during a long run and turns a lease assertion into a clock assertion.
    """

    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# the seeded partition
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SeededPartition:
    """Every durable row the chain hangs off, on the *real* tables.

    ``tenant_id`` is a ``companies.id``, ``cabinet_id`` a ``cabinet_instances.id``,
    ``definition_id`` an ``m04e_dynamic_app_definitions.id``, and so on: the rehearsal
    never invents a table or a column, and every digest is derived from values actually
    persisted, so an assertion comparing them compares database content with database
    content.
    """

    tenant_id: str
    cabinet_id: str
    project_id: str
    employee_id: str
    membership_id: str
    app_id: str
    definition_id: str
    version_id: str
    artifact_bytes: bytes
    artifact_digest: str
    artifact_uri: str
    manifest_version: str
    manifest: dict[str, Any]
    legacy_definition_status: str
    plugin_instance_id: str
    plugin_registry_id: str
    scope_registry_id: str
    scope_anchors: ScopeAnchors
    source_kind: str
    source_id: str
    source_schema_version: str
    source_digest: str
    source_payload: dict[str, Any]
    snapshot_id: str
    snapshot_digest: str
    correlation_id: str
    subject_claim: str
    writer_generation: int

    @property
    def partition(self) -> tuple[str, str]:
        return self.tenant_id, self.definition_id


def _derive(seed: SeededPartition) -> SeededPartition:
    """Fill the three digests derived from the other values."""

    return replace(
        seed,
        artifact_digest=sha256_bytes(seed.artifact_bytes),
        source_digest=sha256_digest(seed.source_payload),
        snapshot_digest=sha256_digest({"snapshot": seed.snapshot_id}),
    )


def new_seed(*, legacy_definition_status: str = "published") -> SeededPartition:
    """A fresh, unique partition description (no rows written)."""

    suffix = new_suffix()
    app_id = f"reh-app-{suffix}"
    manifest_version = "1.0.0"
    artifact_bytes = b"plugin-bytes-" + suffix.encode()
    return _derive(
        SeededPartition(
            tenant_id=f"co_reh{suffix}",
            cabinet_id=f"cab_reh{suffix}",
            project_id=f"prj_reh{suffix}",
            employee_id=f"emp_reh{suffix}",
            membership_id=f"mem_reh{suffix}",
            app_id=app_id,
            definition_id=f"def_reh{suffix}",
            version_id=f"ver_reh{suffix}",
            artifact_bytes=artifact_bytes,
            artifact_digest="",
            artifact_uri=f"content://reh/{suffix}/plugin.wasm",
            manifest_version=manifest_version,
            manifest={
                "protocol": "1.0",
                "version": manifest_version,
                "appId": app_id,
                "displayName": f"Rehearsal app {suffix}",
                "entrypoints": {
                    "plugin": {
                        "artifact": {
                            "sha256": sha256_bytes(artifact_bytes).removeprefix("sha256:")
                        }
                    }
                },
                "requestedCapabilities": [],
            },
            legacy_definition_status=legacy_definition_status,
            plugin_instance_id=f"pli_reh{suffix}",
            plugin_registry_id=f"scopepli_reh{suffix}",
            scope_registry_id=f"scopecab_reh{suffix}",
            scope_anchors=ScopeAnchors(
                scope_kind=ScopeKind.CABINET,
                scope_id=f"cab_reh{suffix}",
                tenant_id=f"co_reh{suffix}",
                cabinet_id=f"cab_reh{suffix}",
            ),
            source_kind="rehearsal.legacy_scope",
            source_id=f"src_reh{suffix}",
            source_schema_version="rehearsal-src.v1",
            source_digest="",
            source_payload={"scope_kind": "cabinet", "scope_id": f"cab_reh{suffix}"},
            snapshot_id=f"snapshot_reh{suffix}",
            snapshot_digest="",
            correlation_id=f"corr_reh{suffix}",
            subject_claim=f"sub_reh{suffix}",
            writer_generation=1,
        )
    )


async def seed_partition(
    harness: RehearsalHarness, seed: SeededPartition | None = None, **kwargs: Any
) -> SeededPartition:
    """Write the rows the whole chain reads, through explicit SQL.

    Raw SQL rather than the ORM because the rehearsal must exercise *the constraints
    the migrations created* -- composite FKs, the manifest CHECKs, the deferred M05
    registry cycle -- instead of ORM-side defaults that would hide a missing server
    default.  Timestamps come from the database, never from a Python clock.
    """

    seed = seed or new_seed(**kwargs)
    harness.seeded.append(seed.tenant_id)
    async with harness.owner.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO companies (id, name, login_slug, status) "
                "VALUES (:id, :name, :slug, 'active')"
            ),
            {"id": seed.tenant_id, "name": f"Rehearsal {seed.tenant_id}", "slug": f"reh{seed.tenant_id[-10:]}"},
        )
        await conn.execute(
            text(
                "INSERT INTO employees (id, login, email, status, keycloak_sub) "
                "VALUES (:id, :login, :email, 'active', :sub)"
            ),
            {
                "id": seed.employee_id,
                "login": f"{seed.employee_id}@reh.local",
                "email": f"{seed.employee_id}@reh.local",
                "sub": seed.subject_claim,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO memberships (id, company_id, employee_id, role) "
                "VALUES (:id, :company, :employee, 'company_admin')"
            ),
            {"id": seed.membership_id, "company": seed.tenant_id, "employee": seed.employee_id},
        )
        await conn.execute(
            text(
                "INSERT INTO cabinet_instances "
                "(id, company_id, name, schema_name, owner_scope, base_template, "
                " company_grant_scope, status) "
                "VALUES (:id, :company, :name, :schema, 'platform', 'base', 'all', 'active')"
            ),
            {
                "id": seed.cabinet_id,
                "company": seed.tenant_id,
                "name": f"Rehearsal cabinet {seed.cabinet_id}",
                "schema": f"reh_cab_{seed.cabinet_id[-10:]}",
            },
        )
        # The M04 parent chain proves cabinet ownership through a *grant row*, not
        # through ``cabinet_instances.company_grant_scope``; without this the cabinet
        # scope is quarantined as ``cabinet_membership_missing``.
        await conn.execute(
            text(
                "INSERT INTO cabinet_company_grants (id, cabinet_id, company_id, mode, status) "
                "VALUES (:id, :cabinet, :company, 'assigned_ro', 'active')"
            ),
            {
                "id": f"ccg_{seed.cabinet_id[-10:]}",
                "cabinet": seed.cabinet_id,
                "company": seed.tenant_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO projects "
                "(id, company_id, cabinet_id, name, slug, status, visibility_mode, "
                " workspace_key, container_ref) "
                "VALUES (:id, :company, :cabinet, :name, :slug, 'active', 'cabinet_shared', "
                " :workspace, :container)"
            ),
            {
                "id": seed.project_id,
                "company": seed.tenant_id,
                "cabinet": seed.cabinet_id,
                "name": f"Rehearsal project {seed.project_id}",
                "slug": f"reh-{seed.project_id[-10:]}",
                "workspace": f"reh_ws_{seed.project_id[-10:]}",
                "container": f"reh_ct_{seed.project_id[-10:]}",
            },
        )
        # A verified credential row, written the way the auth boundary writes it.  The
        # principal resolver only ever reads it; nothing here is a token.
        await conn.execute(
            text(
                "INSERT INTO auth_sessions "
                "(correlation_id, subject_claim, resolution_source, issued_at, expires_at, "
                " company_id, cabinet_id) "
                "VALUES (:correlation, :subject, 'session_cookie', now(), "
                " now() + interval '6 hours', :company, :cabinet)"
            ),
            {
                "correlation": seed.correlation_id,
                "subject": seed.subject_claim,
                "company": seed.tenant_id,
                "cabinet": seed.cabinet_id,
            },
        )
        # Legacy source rows.  A published definition must name its current version
        # (``ck_dynamic_app_definition_published_version``) while the version row points
        # back at the definition, so the pair is written in one transaction: definition
        # first (draft), version second, then the definition is stamped published with
        # its pointer.  Doing it in another order would require a deferred FK the
        # legacy table does not need.
        await conn.execute(
            text(
                "INSERT INTO dynamic_app_definitions "
                "(id, tenant_id, app_id, display_name, description, scope_kind, scope_id, status) "
                "VALUES (:id, :tenant, :app, :name, :description, 'cabinet', :scope, 'draft')"
            ),
            {
                "id": seed.definition_id,
                "tenant": seed.tenant_id,
                "app": seed.app_id,
                "name": f"Rehearsal definition {seed.definition_id}",
                "description": "end-to-end cutover rehearsal",
                "scope": seed.cabinet_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO dynamic_app_versions "
                "(id, app_definition_id, manifest_version, artifact_digest, artifact_uri, "
                " manifest, requested_capabilities, status) "
                "VALUES (:id, :definition, :manifest_version, :artifact, :uri, "
                " CAST(:manifest AS jsonb), CAST(:capabilities AS jsonb), 'published')"
            ),
            {
                "id": seed.version_id,
                "definition": seed.definition_id,
                "manifest_version": seed.manifest_version,
                "artifact": seed.artifact_digest,
                "uri": seed.artifact_uri,
                "manifest": jsonb(seed.manifest),
                "capabilities": jsonb([]),
            },
        )
        # One statement per case rather than a ``CASE WHEN :status = ...``: the same
        # placeholder reaching two differently-typed columns makes asyncpg infer two
        # types for one parameter (``AmbiguousParameterError``).
        if seed.legacy_definition_status == "published":
            await conn.execute(
                text(
                    "UPDATE dynamic_app_definitions SET status = 'published', "
                    "current_version_id = :version WHERE id = :id"
                ),
                {"version": seed.version_id, "id": seed.definition_id},
            )
        else:
            await conn.execute(
                text("UPDATE dynamic_app_definitions SET status = :status WHERE id = :id"),
                {"status": seed.legacy_definition_status, "id": seed.definition_id},
            )
        # Canonical (target-side) definition/version rows: the M04E proof port compares
        # them with the legacy rows and refuses identity drift.  The target schema pins
        # two more invariants than the legacy one -- a published version must carry
        # ``published_at`` (``ck_m04e_version_published_lifecycle_timestamp``) and a
        # published definition must name its current version
        # (``ck_m04e_definition_published_has_current``) -- so the pair is written as
        # draft rows and stamped afterwards, in the same transaction.
        await conn.execute(
            text(
                "INSERT INTO m04e_dynamic_app_definitions "
                "(id, tenant_id, app_id, display_name, scope_kind, scope_id, lifecycle, "
                " source_snapshot_id, source_digest) "
                "VALUES (:id, :tenant, :app, :name, 'cabinet', :scope, 'draft', "
                " :snapshot, :digest)"
            ),
            {
                "id": seed.definition_id,
                "tenant": seed.tenant_id,
                "app": seed.app_id,
                "name": f"Rehearsal definition {seed.definition_id}",
                "scope": seed.cabinet_id,
                "snapshot": seed.snapshot_id,
                "digest": seed.snapshot_digest,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO m04e_dynamic_app_versions "
                "(id, definition_id, tenant_id, app_id, manifest_version, artifact_digest, "
                " artifact_uri, manifest, requested_capabilities, lifecycle, "
                " source_snapshot_id, source_digest) "
                "VALUES (:id, :definition, :tenant, :app, :manifest_version, :artifact, :uri, "
                " CAST(:manifest AS jsonb), CAST(:capabilities AS jsonb), 'draft', "
                " :snapshot, :digest)"
            ),
            {
                "id": seed.version_id,
                "definition": seed.definition_id,
                "tenant": seed.tenant_id,
                "app": seed.app_id,
                "manifest_version": seed.manifest_version,
                "artifact": seed.artifact_digest,
                "uri": seed.artifact_uri,
                "manifest": jsonb(seed.manifest),
                "capabilities": jsonb([]),
                "snapshot": seed.snapshot_id,
                "digest": seed.snapshot_digest,
            },
        )
        if seed.legacy_definition_status == "published":
            await conn.execute(
                text(
                    "UPDATE m04e_dynamic_app_versions SET lifecycle = 'published', "
                    "published_at = now() WHERE id = :id"
                ),
                {"id": seed.version_id},
            )
            await conn.execute(
                text(
                    "UPDATE m04e_dynamic_app_definitions SET lifecycle = 'published', "
                    "current_version_id = :version WHERE id = :id"
                ),
                {"version": seed.version_id, "id": seed.definition_id},
            )
    return seed


async def seed_canonical_scope(
    harness: RehearsalHarness, seed: SeededPartition
) -> None:
    """Write the M04 *canonical* rows (anchors + registry) for the cabinet scope.

    Normally the M04 backfill writes these; the M05/M06/cutover stages need a partition
    whose scope is already accepted, and re-running M04 for every one of those tests
    would only make them slower.  The rows are the same shape the accepted path
    produces (``owner_state='active'``, sha256 provenance, a checkpoint token).
    """

    token = f"c1:1:{sha256_digest({'cursor': seed.source_id})[:16]}"
    await harness.execute(
        "INSERT INTO scope_owner_tenant_anchors "
        "(id, tenant_id, tenant_type, owner_state, source_kind, source_id, schema_version, "
        " payload_digest) VALUES "
        "(:id, :tenant, 'company', 'active', :kind, :sid, :schema, :digest) "
        "ON CONFLICT ON CONSTRAINT uq_scope_owner_tenant_identity DO NOTHING",
        {
            "id": f"scope_{uuid.uuid4().hex[:24]}",
            "tenant": seed.tenant_id,
            "kind": seed.source_kind,
            "sid": f"anchor-tenant-{seed.tenant_id}",
            "schema": seed.source_schema_version,
            "digest": sha256_digest({"anchor": seed.tenant_id}),
        },
    )
    await harness.execute(
        "INSERT INTO scope_owner_cabinet_anchors "
        "(id, cabinet_id, tenant_id, tenant_type, owner_state, source_kind, source_id, "
        " schema_version, payload_digest) VALUES "
        "(:id, :cabinet, :tenant, 'company', 'active', :kind, :sid, :schema, :digest) "
        "ON CONFLICT ON CONSTRAINT uq_scope_owner_cabinet_anchor_typed_tuple DO NOTHING",
        {
            "id": f"scope_{uuid.uuid4().hex[:24]}",
            "cabinet": seed.cabinet_id,
            "tenant": seed.tenant_id,
            "kind": seed.source_kind,
            "sid": f"anchor-cabinet-{seed.cabinet_id}",
            "schema": seed.source_schema_version,
            "digest": sha256_digest({"anchor": seed.cabinet_id}),
        },
    )
    await harness.execute(
        "INSERT INTO scope_registry "
        "(id, scope_kind, scope_id, tenant_id, tenant_type, cabinet_id, project_id, "
        " project_status, plugin_instance_id, owner_state, source_kind, source_id, "
        " schema_version, payload_digest, checkpoint) VALUES "
        "(:id, 'cabinet', :cabinet, :tenant, 'company', :cabinet, NULL, NULL, NULL, "
        " 'active', :kind, :sid, :schema, :digest, :checkpoint)",
        {
            "id": seed.scope_registry_id,
            "cabinet": seed.cabinet_id,
            "tenant": seed.tenant_id,
            "kind": seed.source_kind,
            "sid": seed.source_id,
            "schema": seed.source_schema_version,
            "digest": seed.source_digest,
            "checkpoint": token,
        },
    )


class StaticArtifactContent(ArtifactContentPort):
    """``ArtifactContentPort`` over the seeded artifact bytes.

    The content store is *external* to the repository (object storage), so a rehearsal
    necessarily supplies bytes rather than reading a bucket.  The double cannot lie
    about the digest: ``DefinitionVersionEvidenceBackfill._verify_artifact`` hashes what
    it receives and compares it with the digest the proof established, so a wrong body
    is quarantined as ``artifact_content_digest_mismatch``.  What it *does* pin is that
    only the exact ``(tenant, definition, version, uri)`` quadruple is served.
    """

    def __init__(self, seed: SeededPartition) -> None:
        self._seed = seed

    async def resolve_artifact_uri(
        self, *, tenant_id: str, app_definition_id: str, app_version_id: str
    ) -> str | None:
        if (tenant_id, app_definition_id, app_version_id) != (
            self._seed.tenant_id,
            self._seed.definition_id,
            self._seed.version_id,
        ):
            return None
        return self._seed.artifact_uri

    async def read_artifact(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        artifact_uri: str,
        max_bytes: int,
    ) -> bytes | None:
        resolved = await self.resolve_artifact_uri(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
        )
        if resolved is None or resolved != artifact_uri:
            return None
        return self._seed.artifact_bytes[:max_bytes]


# --------------------------------------------------------------------------- #
# the harness object
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RehearsalHarness:
    """One isolated schema plus the production objects bound to it.

    ``owner`` is the harness login (seeding and direct-SQL assertions); ``trusted`` is
    the same schema reached as ``prodavan_service``, which is what every application
    port uses.  Keeping both is what lets a test show *what a role change does* instead
    of asserting it from a comment.
    """

    schema: str
    dsn: str
    owner: AsyncEngine
    trusted: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    tenant_context: RlsTenantScopeContextAdapter
    m05_tenant_context: RlsTrustedTenantContextAdapter
    scope_adapter: SqlAlchemyScopeBackfillAdapter
    coordinator: ScopeMigrationCoordinator
    principal_resolver: Any
    #: Tenant ids seeded in this rehearsal, so a shared schema can be cleaned up
    #: precisely.  Filled by :func:`seed_partition`.
    seeded: list[str]

    # ------------------------------------------------------------ reading
    async def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        async with self.owner.connect() as conn:
            return await conn.scalar(text(sql), params or {})

    async def rows(self, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
        async with self.owner.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())

    async def count(
        self, table: str, where: str = "", params: dict[str, Any] | None = None
    ) -> int:
        clause = f" WHERE {where}" if where else ""
        value = await self.scalar(f'SELECT count(*) FROM "{table}"{clause}', params)
        return int(value or 0)

    async def trusted_rows(self, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Run one read as the trusted role, so RLS applies to it."""

        async with self.trusted.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())

    async def trusted_scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        async with self.trusted.connect() as conn:
            return await conn.scalar(text(sql), params or {})

    # ------------------------------------------------------------ writing
    async def execute(
        self, sql: str, params: dict[str, Any] | None = None, *, as_role: bool = False
    ) -> None:
        engine = self.trusted if as_role else self.owner
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})

    # ------------------------------------------------------------ contexts
    def evidence_context(
        self,
        seed: SeededPartition,
        *,
        transaction_id: str,
        correlation_id: str | None = None,
        writer_generation: int | None = None,
    ) -> MigrationEvidenceContext:
        """A server-derived M04 transaction context for the seeded partition.

        ``transaction_id`` is supplied by the caller because M04 compares it with the
        database transaction id the binder reads *inside* the open transaction; a
        constant would only ever pass for one connection.
        """

        return MigrationEvidenceContext(
            tenant_id=seed.tenant_id,
            authenticated=True,
            transaction_id=transaction_id,
            correlation_id=correlation_id or seed.correlation_id,
            user_id=seed.employee_id,
            role="company_admin",
            rls_context=RlsContext(
                tenant_id=seed.tenant_id,
                cabinet_id=seed.cabinet_id,
                project_id=None,
                user_id=seed.employee_id,
                role="company_admin",
            ),
            writer_generation=(
                seed.writer_generation if writer_generation is None else writer_generation
            ),
        )

    def backfill_context(self, seed: SeededPartition, **overrides: Any) -> BackfillContext:
        """The M04E ``BackfillContext`` for the seeded partition."""

        values: dict[str, Any] = {
            "tenant_id": seed.tenant_id,
            "app_definition_id": seed.definition_id,
            "source_snapshot_id": seed.snapshot_id,
            "source_snapshot_digest": seed.snapshot_digest,
            "source_schema_version": seed.source_schema_version,
            "canonical_schema_version": "m04e.v1",
            "transaction_id": f"tx-{seed.definition_id}",
            "correlation_id": seed.correlation_id,
            "actor": seed.employee_id,
            "decision_owner": "migration",
            "writer_generation": seed.writer_generation,
        }
        values.update(overrides)
        return BackfillContext(**values)

    def artifact_content(self, seed: SeededPartition) -> StaticArtifactContent:
        return StaticArtifactContent(seed)

    def m04e_reader(self) -> SqlM04ECandidateReader:
        return SqlM04ECandidateReader(session_factory=self.session_factory)

    def m04e_store(self) -> SqlM04EEvidenceStore:
        return SqlM04EEvidenceStore(session_factory=self.session_factory)

    def m04e_gates(self) -> SqlM04EUpstreamGate:
        return SqlM04EUpstreamGate(session_factory=self.session_factory)


@pytest_asyncio.fixture
async def rehearsal(
    rehearsal_database: RehearsalDatabase, request: pytest.FixtureRequest
) -> AsyncIterator[RehearsalHarness]:
    """One rehearsal bound to a schema, cleaned up however the test asked.

    ``rows_schema`` selects where the rows live, and it exists because of one hard fact
    about the migration chain: every guard function and trigger is created **qualifiably
    by ``public``** (``CREATE TRIGGER ... ON <unqualified table>`` binds the relation at
    install time, and the revisions run with ``public`` as the first schema).  A
    rehearsal schema created by hand therefore *cannot* carry those guards on its own
    tables unless the guards are reinstalled over it -- which this fixture does, so
    both modes are honest about what they measure:

    * ``"schema"`` (the default) -- rows in a private ``rehearsal_<hex>`` schema, with
      the chain's guards and policies reinstalled over it.  Every stage runs end to end
      and nothing outside the schema is touched.
    * ``"public"`` -- rows in the migrated schema itself, where the guards are the ones
      ``alembic upgrade head`` installed (this is the only way to prove that *the
      migration's own* guards fire, rather than the copies the fixture just made).  The
      cleanup is an explicit delete scoped to the partition this test seeded.

    A test opts in with ``@pytest.mark.rows_in_public``.
    """

    in_public = "rows_in_public" in request.keywords
    rows_schema = "public" if in_public else f"{SCHEMA_PREFIX}_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(rehearsal_database.dsn, poolclass=NullPool)
    await _ensure_trusted_role(admin, "public")
    created: set[str] = set()
    seeded: list[str] = []
    try:
        if not in_public:
            async with admin.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{rows_schema}"'))
            created.add(rows_schema)
            await _ensure_trusted_role(admin, rows_schema)
        else:
            # ``rows_in_public`` puts rows in the migrated schema *and* relies on its
            # guards, so the legacy tables a seed needs must actually be there.  A
            # database stamped below the revision that creates them cannot host this
            # mode; say so instead of failing later with ``relation does not exist``.
            present = await _scalar(
                admin, "SELECT to_regclass('public.dynamic_app_definitions') IS NOT NULL"
            )
            if not present:
                pytest.skip(
                    "this database has no public.dynamic_app_definitions, so the legacy "
                    "source rows a rehearsal seeds cannot exist; `alembic upgrade head` "
                    "creates them (revision 2026110320)"
                )
        owner = _schema_engine(rehearsal_database.dsn, rows_schema, as_role=None)
        try:
            if not in_public:
                async with owner.begin() as conn:
                    await conn.run_sync(_create_metadata_tables)
            await _install_schema_guards(admin, rows_schema, rows_schema)
            if not in_public:
                await _install_schema_policies(admin, rows_schema)
            await _ensure_trusted_role(admin, rows_schema)
            trusted = _schema_engine(
                rehearsal_database.dsn, rows_schema, as_role=rehearsal_database.trusted_role
            )
            try:
                factory = async_sessionmaker(trusted, expire_on_commit=False)
                resolver = build_durable_principal_resolver(session_factory=factory)
                tenant_context = RlsTenantScopeContextAdapter(principal_resolver=resolver)
                adapter = SqlAlchemyScopeBackfillAdapter(
                    session_factory=factory, tenant_context=tenant_context
                )
                yield RehearsalHarness(
                    schema=rows_schema,
                    dsn=rehearsal_database.dsn,
                    owner=owner,
                    trusted=trusted,
                    session_factory=factory,
                    tenant_context=tenant_context,
                    m05_tenant_context=RlsTrustedTenantContextAdapter(
                        principal_resolver=resolver
                    ),
                    scope_adapter=adapter,
                    coordinator=ScopeMigrationCoordinator(adapter),
                    principal_resolver=resolver,
                    seeded=seeded,
                )
            finally:
                await trusted.dispose()
        finally:
            await owner.dispose()
    finally:
        if in_public and seeded:
            await _purge_partitions(admin, seeded)
        for name in created:
            await _drop_schema(admin, name)
        await admin.dispose()


# --------------------------------------------------------------------------- #
# chain helpers shared by the stage tests
# --------------------------------------------------------------------------- #
def scope_candidate(seed: SeededPartition, *, source_id: str | None = None) -> ScopeCandidate:
    """One M04 source row for the seeded cabinet scope."""

    return ScopeCandidate(
        source_kind=seed.source_kind,
        source_id=source_id or seed.source_id,
        schema_version=seed.source_schema_version,
        payload=dict(seed.source_payload),
        anchors=seed.scope_anchors,
        source_state=SourceRowState.ACTIVE,
        asserted_tenant_id=seed.tenant_id,
        asserted_cabinet_id=seed.cabinet_id,
    )


def scope_stream(seed: SeededPartition) -> ScopeStreamIdentity:
    return ScopeStreamIdentity(
        source_snapshot_id=seed.snapshot_id,
        source_schema_version=seed.source_schema_version,
        source_snapshot_digest=seed.snapshot_digest,
        tenant_id=seed.tenant_id,
        writer_generation=seed.writer_generation,
    )


def initial_scope_checkpoint(seed: SeededPartition) -> BackfillCheckpoint:
    return scope_stream(seed).initial_checkpoint()


def m04e_checkpoint(seed: SeededPartition, **overrides: Any) -> Any:
    """The M04E page cursor for the seeded partition (phase ``definition`` by default)."""

    from prodavan.application.migration.m04e_definition_version.backfill import (
        BackfillCheckpoint as M04ECheckpoint,
    )

    values: dict[str, Any] = {
        "partition": seed.partition,
        "phase": "definition",
        "after_source_id": None,
        "sequence": 0,
        "source_snapshot_id": seed.snapshot_id,
        "source_schema_version": seed.source_schema_version,
        "source_snapshot_digest": seed.snapshot_digest,
        "canonical_schema_version": "m04e.v1",
        "writer_generation": seed.writer_generation,
        "phase_complete": False,
    }
    values.update(overrides)
    return M04ECheckpoint(**values)


def upstream_gates(seed: SeededPartition, **overrides: Any) -> UpstreamGateEvidence:
    """Well-formed gate *shape* for a store call.

    These digests are not a claim that a gate passed: ``SqlM04EEvidenceStore`` uses
    them only to bind the page to its partition/snapshot/correlation lineage
    (``_require_gates``), and the M04E-01 promotion gate itself is asserted separately
    by :func:`test_m04e_backfill_stage` -- it refuses by name, which is the reviewed
    state of that gate.
    """

    values: dict[str, Any] = {
        "tenant_id": seed.tenant_id,
        "app_definition_id": seed.definition_id,
        "source_snapshot_digest": seed.snapshot_digest,
        "transaction_id": f"tx-{seed.definition_id}",
        "correlation_id": seed.correlation_id,
        "m04_gate_digest": sha256_digest({"gate": "MIG-M04-01", "seed": seed.tenant_id}),
        "m04e_gate_digest": sha256_digest({"gate": "MIG-M04E-01", "seed": seed.definition_id}),
        "active_scope_digest": sha256_digest({"gate": "active_typed_parent_scope", "seed": seed.cabinet_id}),
    }
    values.update(overrides)
    return UpstreamGateEvidence(**values)


def quarantine_decisions(
    candidates: Iterable[EvidenceCandidate], reason: str
) -> tuple[CandidateDecision, ...]:
    """Classify a page as quarantined for ``reason`` (the ledger still records it)."""

    return tuple(
        CandidateDecision(
            candidate=candidate,
            status=EvidenceStatus.QUARANTINED,
            reason_code=reason,
            proof=None,
        )
        for candidate in candidates
    )


async def run_m04_stage(harness: RehearsalHarness, seed: SeededPartition) -> Any:
    """Run the real M04 backfill for ``seed`` and require an accepted result.

    Later stages (M04E gates, M05 identity, M06 parity, cutover) all read the canonical
    scope M04 produces.  Re-implementing that shape by hand would let a defect in M04
    stay invisible behind a hand-built fixture -- the M04E gate would then be "proven"
    against rows no stage actually wrote.  This runs the stage and asserts its outcome,
    so every downstream assertion rests on real stage output.

    ``txid_current()`` is read immediately before the call: M04 compares the application
    transaction id with the one its binder reads *inside* the transaction it opens, and
    a stale id from an earlier connection would fail that comparison for a reason that
    has nothing to do with the pipeline.
    """

    context = harness.evidence_context(seed, transaction_id=await current_transaction_id(harness))
    resume = await harness.coordinator.resume_checkpoint(
        stream=scope_stream(seed), context=context
    )
    assert resume.checkpoint is not None, (
        f"the M04 stream could not be resumed: {resume.reason_code}"
    )
    result = await harness.coordinator.process_row(
        candidate=scope_candidate(seed), checkpoint=resume.checkpoint, context=context
    )
    assert result.outcome.value == "accepted", (
        f"the M04 stage did not accept the seeded scope: {result.outcome.value} "
        f"({result.reason_code}); later stages read the rows it is supposed to write"
    )
    return result


async def current_transaction_id(harness: RehearsalHarness) -> str:
    """``txid_current()`` of a fresh connection's transaction, as a caller issues it."""

    async with harness.trusted.connect() as conn:
        async with conn.begin():
            return str(await conn.scalar(text("SELECT txid_current()::text")))


__all__ = [
    "ASYNC_PG_DRIVER",
    "CHECK_VIOLATION",
    "GuardStatement",
    "RehearsalDatabase",
    "RehearsalHarness",
    "SCHEMA_PREFIX",
    "PURGE_ORDER",
    "SeededPartition",
    "StaticArtifactContent",
    "TEST_DATABASE_URL_ENV",
    "as_text",
    "current_transaction_id",
    "database_url_from_env",
    "guard_statements",
    "initial_scope_checkpoint",
    "jsonb",
    "load_revision",
    "m04e_checkpoint",
    "new_seed",
    "new_suffix",
    "quarantine_decisions",
    "rehearsal",
    "rehearsal_database",
    "rehearsal_dsn",
    "run_m04_stage",
    "scope_candidate",
    "scope_stream",
    "seed_canonical_scope",
    "seed_partition",
    "sha256_bytes",
    "sha256_digest",
    "upstream_gates",
    "utcnow",
]
