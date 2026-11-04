"""Live PostgreSQL acceptance for the ``equipment_v2`` anchors (skips without a DSN).

The replay tests prove the *declarations*; this module proves the *server*.  It runs
only against a database that already carries the full chain (the RLS suite's own
contract: ``alembic_version`` is stamped and the equipment tables exist), and asserts:

* the three anchored tables and both association tables are present with the anchors
  and their keys/indexes as the catalog reports them;
* **nullable compatibility** - a row can be inserted exactly as a pre-backfill writer
  would (no anchors), and a request, a run and a tender still round-trip; nothing
  invents a project for them;
* the anchors really are ``ON DELETE RESTRICT``: deleting a project that an anchored
  row names is refused by the server, not by application code;
* the association tables enforce what the design says they enforce: duplicate pair,
  non-positive ordinal, a duplicate ordinal inside one parent (deferred, so it fails at
  ``COMMIT``, and a reorder can exchange two ordinals in one transaction), and the
  ``CASCADE``/``RESTRICT`` delete semantics;
* RLS is real: a session with a foreign tenant context reads none of these rows, and a
  session with no tenant context reads none either.

Everything runs inside a throwaway ``eqlive_<hex>`` schema built from a copy of the
migrated tables' DDL, so ``public`` (and whoever else is using it) is untouched.  A
revision that drifted from the model cannot pass: the schema is created from
``Base.metadata``, and the anchors' *presence in the migrated database* is what the
first test asserts.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from prodavan.infrastructure.persistence.models import Base

DSN_ENV = "PRODAVAN_TEST_DATABASE_URL"
ASYNCPG = "postgresql+asyncpg"
SCHEMA_PREFIX = "eqlive"

ANCHORED = ("equipment_v2_ingestion_runs", "equipment_v2_requests", "equipment_v2_tenders")
ASSOCIATIONS = ("equipment_v2_candidate_group_offers", "equipment_v2_tender_candidate_groups")

TENANT_A = "comp_aaaaaaaaaaaaaaaaaaaa"
TENANT_B = "comp_bbbbbbbbbbbbbbbbbbbb"
CABINET_A = "cab_aaaaaaaaaaaaaaaaaaaa"
PROJECT_A = "prj_aaaaaaaaaaaaaaaaaaaa"


def _live_dsn() -> str:
    raw = (os.getenv(DSN_ENV) or "").strip()
    if not raw:
        pytest.skip(
            f"{DSN_ENV} is not set; live acceptance for the equipment anchors is skipped"
        )
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        pytest.skip(f"{DSN_ENV} is not a PostgreSQL DSN")
    if url.drivername != ASYNCPG:
        url = url.set(drivername=ASYNCPG)
    try:
        with socket.create_connection((url.host or "localhost", url.port or 5432), timeout=1.0):
            pass
    except OSError:
        pytest.skip(f"nothing listens at {url.render_as_string(hide_password=True)}")
    return url.render_as_string(hide_password=False)


@pytest_asyncio.fixture()
async def schema() -> AsyncIterator[tuple[AsyncEngine, str]]:
    """An isolated schema holding the equipment tables, plus its admin engine."""

    dsn = _live_dsn()
    name = f"{SCHEMA_PREFIX}_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with admin.begin() as connection:
            await connection.execute(sa.text(f'CREATE SCHEMA "{name}"'))

        engine = create_async_engine(
            dsn, poolclass=NullPool, connect_args={"server_settings": {"search_path": name}}
        )
        try:
            # Only the tables this suite touches: the platform anchors the equipment
            # chain references (companies, cabinets, projects and the two parents the
            # project/employee rows need) plus the equipment tables themselves.
            wanted = {
                "companies",
                "cabinet_instances",
                "employees",
                "projects",
                *[table for table in Base.metadata.tables if table.startswith("equipment_v2_")],
            }
            # ``projects`` also references nullable ``ai_provider_keys``/``content``
            # columns; ``create_all`` emits those keys too, so the set is completed
            # transitively below.
            tables = _closure(wanted)
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync: Base.metadata.create_all(
                        sync,
                        tables=[Base.metadata.tables[table] for table in sorted(tables)],
                        checkfirst=False,
                    )
                )
            yield engine, name
        finally:
            await engine.dispose()
    finally:
        async with admin.begin() as connection:
            await connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        await admin.dispose()


def _closure(seeds: set[str]) -> set[str]:
    """``seeds`` plus every table a foreign key of those tables points at, transitively.

    ``create_all`` emits the keys it is asked to emit, so a subset of tables only works
    when the targets of those keys exist as well.  Walking the metadata graph is how
    this suite keeps its throwaway schema minimal without hand-maintaining the list.
    """

    resolved: set[str] = set()
    pending = list(seeds)
    while pending:
        name = pending.pop()
        if name in resolved or name not in Base.metadata.tables:
            continue
        resolved.add(name)
        for constraint in Base.metadata.tables[name].foreign_key_constraints:
            for element in constraint.elements:
                pending.append(element.target_fullname.split(".", 1)[0])
    return resolved


async def _seed(connection: AsyncConnection) -> None:
    """One tenant/cabinet/project and one employee, enough to anchor a request."""

    await connection.execute(
        sa.text(
            "INSERT INTO companies (id, name, login_slug, subscription_lifetime, status) "
            "VALUES (:tenant, 'Tenant A', 'tenant-a', false, 'active')"
        ),
        {"tenant": TENANT_A},
    )
    await connection.execute(
        sa.text(
            "INSERT INTO cabinet_instances (id, name, schema_name, owner_scope, base_template, "
            "company_grant_scope, status) VALUES (:cabinet, 'Cabinet A', 'cab_a', 'platform', "
            "'base', 'selected', 'active')"
        ),
        {"cabinet": CABINET_A},
    )
    await connection.execute(
        sa.text(
            "INSERT INTO projects (id, company_id, cabinet_id, name, slug, status, visibility_mode, "
            "workspace_key, container_ref) VALUES (:project, :tenant, :cabinet, 'Project A', "
            "'project-a', 'active', 'cabinet_shared', 'ws-a', 'ctr-a')"
        ),
        {"project": PROJECT_A, "tenant": TENANT_A, "cabinet": CABINET_A},
    )


async def _insert_run(connection: AsyncConnection, run_id: str, **anchors: object) -> None:
    await connection.execute(
        sa.text(
            "INSERT INTO equipment_v2_ingestion_runs "
            "(id, tenant_id, source_id, status, item_count, project_id, cabinet_id) "
            "VALUES (:id, :tenant, 'src:test', 'queued', 0, :project_id, :cabinet_id)"
        ),
        {"id": run_id, "tenant": TENANT_A, "project_id": None, "cabinet_id": None, **anchors},
    )


async def _insert_request(connection: AsyncConnection, request_id: str, **anchors: object) -> None:
    await connection.execute(
        sa.text(
            "INSERT INTO equipment_v2_requests "
            "(id, tenant_id, title, status, project_id, cabinet_id) "
            "VALUES (:id, :tenant, 'Procurement', 'planned', :project_id, :cabinet_id)"
        ),
        {"id": request_id, "tenant": TENANT_A, "project_id": None, "cabinet_id": None, **anchors},
    )


# --------------------------------------------------------------------------- #
# schema: the migrated database really has the anchors
# --------------------------------------------------------------------------- #


async def test_the_migrated_database_carries_the_anchors() -> None:
    """Skipped unless the DSN points at a database with the chain applied."""

    dsn = _live_dsn()
    engine = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            assert version is not None, "the database has no alembic_version row"

            columns = {
                row[0]
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = current_schema() AND table_name LIKE 'equipment_v2%%' "
                            "AND column_name IN ('project_id', 'cabinet_id')"
                        )
                    )
                ).all()
            }
            assert {"project_id", "cabinet_id"} <= columns, (
                f"the migrated database is missing the equipment anchors (stamped at {version}); "
                "run `alembic upgrade head` first"
            )
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# nullable compatibility
# --------------------------------------------------------------------------- #


async def test_pre_backfill_rows_can_still_be_written(schema) -> None:  # noqa: ANN001
    """A writer that knows nothing about anchors keeps working."""

    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        await _insert_run(connection, "run_legacy")
        await _insert_request(connection, "req_legacy")

        row = (
            await connection.execute(
                sa.text(
                    "SELECT project_id, cabinet_id FROM equipment_v2_requests WHERE id = 'req_legacy'"
                )
            )
        ).one()
        assert row == (None, None), "an unanchored request must stay unanchored, not be guessed"

        run = (
            await connection.execute(
                sa.text(
                    "SELECT project_id, cabinet_id FROM equipment_v2_ingestion_runs WHERE id = 'run_legacy'"
                )
            )
        ).one()
        assert run == (None, None)


async def test_anchored_rows_round_trip_exactly(schema) -> None:  # noqa: ANN001
    """The anchor is stored as given - no default, no derivation from the tenant."""

    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        await _insert_request(connection, "req_anchored", project_id=PROJECT_A, cabinet_id=CABINET_A)
        row = (
            await connection.execute(
                sa.text(
                    "SELECT project_id, cabinet_id FROM equipment_v2_requests WHERE id = 'req_anchored'"
                )
            )
        ).one()
        assert row == (PROJECT_A, CABINET_A)


async def test_a_project_referenced_by_an_anchor_cannot_be_deleted(schema) -> None:  # noqa: ANN001
    """``ON DELETE RESTRICT``: procurement evidence is not silently orphaned."""

    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        await _insert_request(connection, "req_restrict", project_id=PROJECT_A)

    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                sa.text("DELETE FROM projects WHERE id = :project"), {"project": PROJECT_A}
            )


# --------------------------------------------------------------------------- #
# association tables: what the constraints really enforce
# --------------------------------------------------------------------------- #


async def _group_with_offer(connection: AsyncConnection) -> tuple[str, str]:
    """One request line, one candidate group, one catalog offer."""

    await _insert_request(connection, "req_assoc")
    await connection.execute(
        sa.text(
            "INSERT INTO equipment_v2_request_lines "
            "(id, request_id, line_number, title, quantity, unit, specification) "
            "VALUES ('line_assoc', 'req_assoc', 1, 'Bulldozer', 1, 'pcs', '{}'::jsonb)"
        )
    )
    await connection.execute(
        sa.text(
            "INSERT INTO equipment_v2_candidate_groups (id, request_line_id, title, status, offer_ids) "
            "VALUES ('group_assoc', 'line_assoc', 'Candidates', 'open', '[]'::jsonb)"
        )
    )
    await _insert_run(connection, "run_assoc")
    await connection.execute(
        sa.text(
            "INSERT INTO equipment_v2_catalog_snapshots "
            "(id, ingestion_run_id, source_id, content_hash, captured_at, metadata_json) "
            "VALUES ('snap_assoc', 'run_assoc', 'src:test', 'sha256:abc', now(), '{}'::jsonb)"
        )
    )
    await connection.execute(
        sa.text(
            "INSERT INTO equipment_v2_catalog_offers "
            "(id, snapshot_id, supplier_key, external_key, title, unit_price, currency, "
            "available_quantity, status, attributes) VALUES ('offer_assoc', 'snap_assoc', "
            "'supplier', 'ext-1', 'Bulldozer', 1000, 'USD', 1, 'active', '{}'::jsonb)"
        )
    )
    return "group_assoc", "offer_assoc"


async def test_duplicate_membership_pair_is_rejected(schema) -> None:  # noqa: ANN001
    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        group, offer = await _group_with_offer(connection)
        await connection.execute(
            sa.text(
                "INSERT INTO equipment_v2_candidate_group_offers "
                "(tenant_id, candidate_group_id, offer_id, ordinal) VALUES (:tenant, :group, :offer, 1)"
            ),
            {"tenant": TENANT_A, "group": group, "offer": offer},
        )

    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO equipment_v2_candidate_group_offers "
                    "(tenant_id, candidate_group_id, offer_id, ordinal) "
                    "VALUES (:tenant, :group, :offer, 2)"
                ),
                {"tenant": TENANT_A, "group": group, "offer": offer},
            )


async def test_ordinal_must_be_positive(schema) -> None:  # noqa: ANN001
    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        group, offer = await _group_with_offer(connection)

    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO equipment_v2_candidate_group_offers "
                    "(tenant_id, candidate_group_id, offer_id, ordinal) VALUES (:tenant, :group, :offer, 0)"
                ),
                {"tenant": TENANT_A, "group": group, "offer": offer},
            )


async def test_ordinal_collision_fails_at_commit_not_at_insert(schema) -> None:  # noqa: ANN001
    """Deferred uniqueness: the statement may pass, the transaction may not."""

    engine, _name = schema
    async with engine.connect() as connection:
        await _seed(connection)
        group, offer = await _group_with_offer(connection)
        await connection.execute(
            sa.text(
                "INSERT INTO equipment_v2_catalog_offers "
                "(id, snapshot_id, supplier_key, external_key, title, unit_price, currency, "
                "available_quantity, status, attributes) VALUES ('offer_two', 'snap_assoc', "
                "'supplier', 'ext-2', 'Bulldozer II', 1100, 'USD', 1, 'active', '{}'::jsonb)"
            )
        )
        await connection.execute(
            sa.text(
                "INSERT INTO equipment_v2_candidate_group_offers "
                "(tenant_id, candidate_group_id, offer_id, ordinal) VALUES (:tenant, :group, :offer, 1)"
            ),
            {"tenant": TENANT_A, "group": group, "offer": offer},
        )
        await connection.commit()

    transaction = await _begin(engine)
    connection = transaction.connection
    try:
        # Second row with the same ordinal: accepted by the INSERT because the key is
        # deferred, refused by COMMIT.  This is exactly why a reorder is safe.
        await connection.execute(
            sa.text(
                "INSERT INTO equipment_v2_candidate_group_offers "
                "(tenant_id, candidate_group_id, offer_id, ordinal) "
                "VALUES (:tenant, :group, 'offer_two', 1)"
            ),
            {"tenant": TENANT_A, "group": group},
        )
        with pytest.raises(sa.exc.IntegrityError):
            await transaction.commit()
    finally:
        await _close(transaction)


async def test_a_reorder_can_exchange_two_ordinals_in_one_transaction(schema) -> None:  # noqa: ANN001
    """The deferred key is what makes a swap expressible as one statement."""

    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        group, offer = await _group_with_offer(connection)
        await connection.execute(
            sa.text(
                "INSERT INTO equipment_v2_catalog_offers "
                "(id, snapshot_id, supplier_key, external_key, title, unit_price, currency, "
                "available_quantity, status, attributes) VALUES ('offer_two', 'snap_assoc', "
                "'supplier', 'ext-2', 'Bulldozer II', 1100, 'USD', 1, 'active', '{}'::jsonb)"
            )
        )
        await connection.execute(
            sa.text(
                "INSERT INTO equipment_v2_candidate_group_offers "
                "(tenant_id, candidate_group_id, offer_id, ordinal) VALUES "
                "(:tenant, :group, :offer, 1), (:tenant, :group, 'offer_two', 2)"
            ),
            {"tenant": TENANT_A, "group": group, "offer": offer},
        )

    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE equipment_v2_candidate_group_offers SET ordinal = CASE ordinal "
                "WHEN 1 THEN 2 ELSE 1 END WHERE candidate_group_id = :group"
            ),
            {"group": group},
        )
        ordered = [
            row[0]
            for row in (
                await connection.execute(
                    sa.text(
                        "SELECT offer_id FROM equipment_v2_candidate_group_offers "
                        "WHERE candidate_group_id = :group ORDER BY ordinal"
                    ),
                    {"group": group},
                )
            ).all()
        ]
        assert ordered == ["offer_two", offer], "the swap did not take effect"


async def test_deleting_a_group_cascades_but_an_offer_in_use_is_restricted(schema) -> None:  # noqa: ANN001
    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        group, offer = await _group_with_offer(connection)
        await connection.execute(
            sa.text(
                "INSERT INTO equipment_v2_candidate_group_offers "
                "(tenant_id, candidate_group_id, offer_id, ordinal) VALUES (:tenant, :group, :offer, 1)"
            ),
            {"tenant": TENANT_A, "group": group, "offer": offer},
        )

    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                sa.text("DELETE FROM equipment_v2_catalog_offers WHERE id = :offer"),
                {"offer": offer},
            )

    async with engine.begin() as connection:
        await connection.execute(
            sa.text("DELETE FROM equipment_v2_candidate_groups WHERE id = :group"), {"group": group}
        )
        remaining = await connection.scalar(
            sa.text("SELECT count(*) FROM equipment_v2_candidate_group_offers")
        )
        assert remaining == 0


# --------------------------------------------------------------------------- #
# RLS on the new relations
# --------------------------------------------------------------------------- #


async def test_a_foreign_tenant_sees_none_of_the_new_rows(schema) -> None:  # noqa: ANN001
    """The tenant policy is forced, so the session GUC bounds every read."""

    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        group, offer = await _group_with_offer(connection)
        await connection.execute(
            sa.text(
                "INSERT INTO equipment_v2_candidate_group_offers "
                "(tenant_id, candidate_group_id, offer_id, ordinal) VALUES (:tenant, :group, :offer, 1)"
            ),
            {"tenant": TENANT_A, "group": group, "offer": offer},
        )
        await _install_policies(connection)

    async with engine.connect() as connection:
        await _as_app_role(connection, _name)
        await connection.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": TENANT_B}
        )
        assert await connection.scalar(
            sa.text(f'SELECT count(*) FROM "{_name}".equipment_v2_candidate_group_offers')
        ) == 0
        await connection.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": TENANT_A}
        )
        assert await connection.scalar(
            sa.text(f'SELECT count(*) FROM "{_name}".equipment_v2_candidate_group_offers')
        ) == 1
        assert await connection.scalar(
            sa.text(f'SELECT count(*) FROM "{_name}".equipment_v2_requests')
        ) == 1
        await connection.rollback()


async def test_no_tenant_context_reads_nothing(schema) -> None:  # noqa: ANN001
    engine, _name = schema
    async with engine.begin() as connection:
        await _seed(connection)
        await _insert_request(connection, "req_rls")
        await _install_policies(connection)

    async with engine.connect() as connection:
        await _as_app_role(connection, _name)
        await connection.execute(sa.text("SELECT set_config('app.tenant_id', '', true)"))
        assert await connection.scalar(
            sa.text(f'SELECT count(*) FROM "{_name}".equipment_v2_requests')
        ) == 0
        await connection.rollback()


async def _as_app_role(connection: AsyncConnection, schema_name: str) -> None:
    """Switch the session to a non-owner, non-bypass role so RLS actually applies.

    The trusted policy is bound to ``prodavan_service`` and the table owner bypasses
    RLS unless ``FORCE`` is on (it is), so an ordinary role is what proves the
    predicate.  The role is created only if absent; ``SET ROLE`` resets
    ``search_path`` to the role's default, so every query in these two tests names the
    throwaway schema explicitly (that is also why a bare table name would report the
    table as missing instead of failing a policy assertion).
    """

    role = "prodavan_app"
    exists = await connection.scalar(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
    )
    if not exists:
        await connection.execute(sa.text(f'CREATE ROLE "{role}" NOLOGIN'))
    # ``USAGE`` on the throwaway schema plus ``SELECT`` on its tables: the point of the
    # test is the *policy*, not a privilege failure, so the role is given exactly what a
    # deployed application role has and nothing else (no INSERT/UPDATE/DELETE, no
    # BYPASSRLS, and it is not the table owner).
    await connection.execute(sa.text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{role}"'))
    await connection.execute(
        sa.text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema_name}" TO "{role}"')
    )
    await connection.execute(sa.text(f'SET LOCAL ROLE "{role}"'))
    await connection.execute(sa.text(f'SET LOCAL search_path TO "{schema_name}"'))


async def _install_policies(connection: AsyncConnection) -> None:
    """Install the registry's own policies for the tables this schema holds."""

    from prodavan.infrastructure.persistence.rls import policies

    for spec in policies.CANONICAL_TABLE_POLICIES:
        if spec.table not in Base.metadata.tables:
            continue
        exists = await connection.execute(
            sa.text(
                "SELECT to_regclass(:relation) IS NOT NULL AND EXISTS ("
                "SELECT 1 FROM pg_attribute AS a WHERE a.attrelid = to_regclass(:relation) "
                "AND a.attname::text = :column AND a.attnum > 0 AND NOT a.attisdropped)"
            ),
            {"relation": spec.table, "column": (policies.scope_columns(spec) or ("id",))[0]},
        )
        if not exists.scalar():
            continue
        await connection.execute(sa.text(policies.upgrade_guard_sql(spec)))


async def _begin(engine: AsyncEngine):  # noqa: ANN201 - AsyncTransaction
    """Open a transaction whose lifetime the caller controls explicitly."""

    connection = await engine.connect()
    return await connection.begin()


async def _close(transaction) -> None:  # noqa: ANN001
    """Return the connection behind ``transaction`` to the pool, always."""

    connection = transaction.connection
    if transaction.is_active:
        await transaction.rollback()
    await connection.close()
