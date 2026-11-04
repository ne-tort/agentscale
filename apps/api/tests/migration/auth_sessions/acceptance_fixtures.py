"""Acceptance fixtures for the durable ``correlation_id -> subject`` store (MIG-AUTHSESS-01).

Three artefacts describe ``auth_sessions`` and this package compares them against each other:

* ``application/migration/principal_resolver.py::AUTH_SESSION_CORRELATION_GAP`` — the
  reviewed specification (columns, keys, semantics);
* ``models/auth_sessions.py`` — the declarative schema source of truth;
* ``alembic/versions/2026110317_auth_sessions.py`` — the DDL that reaches the database.

The revision is replayed in-process against a recording ``alembic.op`` stand-in (the same
technique ``tests/migration/m06_cutover_evidence`` uses), so the comparison is between real
:class:`sqlalchemy.Table` objects and the ORM metadata — same SQLAlchemy rendering on both
sides. No database, no ``alembic upgrade``, no network.

The row-level cases (a reader refusing an expired/revoked/foreign row, the writer's
idempotency) run on SQLite. The ``auth_sessions`` table is created from the **real** model
(``to_metadata``), so its CHECK constraints are enforced there too; the identity tables the
resolver joins are mirrored exactly as ``tests/migration/principal_resolver/db_fixtures.py``
mirrors them, because two of them are declared with PostgreSQL ``JSONB``.
"""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from prodavan.application.migration.principal_resolver import (
    AUTH_SESSION_CORRELATION_GAP,
    VerifiedCredential,
)
from prodavan.infrastructure.persistence.models.auth_sessions import AuthSessionRow
from tests.migration.principal_resolver.db_fixtures import (
    CABINET_A,
    CORRELATION,
    CORRELATION_OTHER,
    EMPLOYEE_A,
    IDENTITY_MODELS,
    PROJECT_A,
    SUBJECT_A,
    SUBJECT_B,
    TENANT_A,
    TENANT_B,
    mirror_table,
)

#: ``apps/api`` — this file is ``tests/migration/auth_sessions/<file>``.
API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = API_ROOT / "alembic" / "versions"
REVISION_FILE = VERSIONS / "2026110317_auth_sessions.py"

REVISION_ID = "2026110317"
DOWN_REVISION_ID = "2026110316"

TABLE = AUTH_SESSION_CORRELATION_GAP.table
SUBJECT_INDEX = "ix_auth_sessions_subject_expiry"

POSTGRESQL = postgresql.dialect()

#: Columns whose type is an instant; everything else is ``TEXT``. Used by the parity cases
#: so a future column of another type is a failure rather than a silently accepted extra.
TIMESTAMP_COLUMNS = ("issued_at", "expires_at", "revoked_at")

#: A raw secret, token or password must never reach this table: it stores *who*, not *how*.
FORBIDDEN_COLUMN_FRAGMENTS = ("token", "secret", "password", "claim_payload", "raw")


def moment() -> datetime:
    """The wall clock, truncated to a whole second, read **when a test asks for it**.

    Not a module constant: ``TrustedPrincipal.from_resolution`` validates the principal it
    builds against the real clock, so a fixture frozen at import time would drift out of
    agreement with it as the run proceeds (the failure mode
    ``tests/migration/principal_resolver/db_fixtures.py`` documents at length). Each test
    takes one anchor with ``now = moment()`` and every stored/expected instant is derived
    from it.
    """

    return datetime.now(UTC).replace(microsecond=0)


# ---------------------------------------------------------------------------
# Revision replay
# ---------------------------------------------------------------------------


class RecordedOps:
    """Minimal stand-in for the ``alembic.op`` proxy.

    Tables are rebuilt as real SQLAlchemy objects in an isolated metadata, so what the
    revision *declares* can be compared structurally with the ORM metadata.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.tables: dict[str, sa.Table] = {}
        self._metadata = sa.MetaData()

    def create_table(self, table_name: str, *columns: Any, **kwargs: Any) -> None:
        self.tables[table_name] = sa.Table(table_name, self._metadata, *columns, **kwargs)
        self.calls.append(("create_table", table_name))

    def create_index(
        self, index_name: str, table_name: str, columns: Any, **kwargs: Any
    ) -> None:
        self.calls.append(("create_index", table_name, index_name, tuple(columns), kwargs))

    def drop_index(
        self, index_name: str, table_name: str | None = None, **kwargs: Any
    ) -> None:
        self.calls.append(("drop_index", table_name, index_name))

    def drop_table(self, table_name: str, **kwargs: Any) -> None:
        self.calls.append(("drop_table", table_name))

    def execute(self, statement: Any, **kwargs: Any) -> None:
        self.calls.append(("execute", str(statement)))


def load_revision_module_from(path: Path) -> Any:
    """Load an Alembic revision by path: ``alembic/versions`` is not a package."""

    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_revision_module() -> Any:
    return load_revision_module_from(REVISION_FILE)


def _literals(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in {
            "revision",
            "down_revision",
            "branch_labels",
            "depends_on",
        }:
            values[target.id] = ast.literal_eval(node.value)
    return values


def revision_graph() -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        revision = _literals(path).get("revision")
        if isinstance(revision, str):
            graph[revision] = _literals(path).get("down_revision")
    return graph


def head_revisions() -> tuple[str, ...]:
    graph = revision_graph()
    children = {down for down in graph.values() if down}
    return tuple(sorted(revision for revision in graph if revision not in children))


def chain_from_head() -> tuple[str, ...]:
    graph = revision_graph()
    heads = head_revisions()
    assert len(heads) == 1, f"expected exactly one head, found {list(heads)}"
    chain: list[str] = []
    current: str | None = heads[0]
    while current is not None:
        chain.append(current)
        current = graph.get(current)
    return tuple(chain)


# ---------------------------------------------------------------------------
# Structural comparison helpers
# ---------------------------------------------------------------------------


def type_label(type_: sa.types.TypeEngine) -> str:
    return POSTGRESQL.type_compiler.process(type_)


def squash(text: str) -> str:
    return " ".join(str(text).split())


def column_signature(table: sa.Table) -> list[tuple[str, str, bool, str | None]]:
    return [
        (column.name, type_label(column.type), column.nullable, _default_text(column))
        for column in table.columns
    ]


def _default_text(column: sa.Column) -> str | None:
    if column.server_default is None:
        return None
    return squash(str(column.server_default.arg)).lower()


def check_catalog(table: sa.Table) -> dict[str | None, str]:
    return {
        constraint.name: squash(str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def index_columns(table: sa.Table) -> set[tuple[str, ...]]:
    return {tuple(column.name for column in index.columns) for index in table.indexes}


# ---------------------------------------------------------------------------
# Row-level harness
# ---------------------------------------------------------------------------


@asynccontextmanager
async def auth_session_database(
    *, with_identity: bool = True, enforce_checks: bool = True
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """In-memory database with the real ``auth_sessions`` table (+ mirrored identity rows).

    The store table is copied from the model with :meth:`sqlalchemy.Table.to_metadata`, so
    its CHECK constraints are live: a case that inserts a blank subject fails *here* the way
    it would fail in PostgreSQL.

    ``enforce_checks=False`` copies the columns and keys only. It exists for one case — a row
    that the *current* vocabulary would refuse but which a database can hold anyway (written
    before the allow-list narrowed, or by a direct-SQL writer with the constraint dropped):
    the reader must refuse such a row, and a harness that cannot store it could not prove
    that.

    The identity tables are mirrored because SQLite cannot render their ``JSONB`` columns;
    every read still goes through the real model classes.
    """

    metadata = sa.MetaData()
    if enforce_checks:
        AuthSessionRow.__table__.to_metadata(metadata)
    else:
        mirror_table(AuthSessionRow, metadata)
    if with_identity:
        for model in IDENTITY_MODELS:
            mirror_table(model, metadata)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed_session(
    session: AsyncSession,
    *,
    correlation_id: str = CORRELATION,
    subject_claim: str = SUBJECT_A,
    resolution_source: str = "bearer_jwt_verified",
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    company_id: str | None = None,
    cabinet_id: str | None = None,
    project_id: str | None = None,
) -> AuthSessionRow:
    """One stored session row, written through the real model.

    ``issued_at``/``expires_at`` default to "one minute ago" / "fifteen minutes from now"
    relative to :func:`moment`, so the row is live unless a case says otherwise.
    """

    anchor = moment()
    row = AuthSessionRow(
        correlation_id=correlation_id,
        subject_claim=subject_claim,
        resolution_source=resolution_source,
        issued_at=issued_at if issued_at is not None else anchor - timedelta(minutes=1),
        expires_at=expires_at if expires_at is not None else anchor + timedelta(minutes=15),
        revoked_at=revoked_at,
        company_id=company_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
    )
    session.add(row)
    return row


def as_utc(value: datetime | None) -> datetime | None:
    """A stored instant as UTC.

    ``sqlite``/``aiosqlite`` returns ``DateTime(timezone=True)`` values without tzinfo; the
    column is a ``TIMESTAMPTZ`` in PostgreSQL, so the comparison the tests make is
    "the same instant", not "the same tzinfo object". Normalised here, in the harness, rather
    than by weakening an assertion.
    """

    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def credential(
    *,
    correlation_id: str = CORRELATION,
    subject_claim: str = SUBJECT_A,
    resolution_source: str = "bearer_jwt_verified",
    expires_at: datetime | None = None,
    tenant: str | None = None,
    cabinet: str | None = None,
    project: str | None = None,
) -> VerifiedCredential:
    """A credential the auth boundary could have produced for this row."""

    return VerifiedCredential(
        correlation_id=correlation_id,
        subject_claim=subject_claim,
        resolution_source=resolution_source,
        expires_at=expires_at if expires_at is not None else moment() + timedelta(minutes=15),
        asserted_tenant_id=tenant,
        asserted_cabinet_id=cabinet,
        asserted_project_id=project,
    )


__all__ = [
    "API_ROOT",
    "CABINET_A",
    "CORRELATION",
    "CORRELATION_OTHER",
    "DOWN_REVISION_ID",
    "EMPLOYEE_A",
    "FORBIDDEN_COLUMN_FRAGMENTS",
    "POSTGRESQL",
    "PROJECT_A",
    "REVISION_FILE",
    "REVISION_ID",
    "RecordedOps",
    "SUBJECT_A",
    "SUBJECT_B",
    "SUBJECT_INDEX",
    "TABLE",
    "TENANT_A",
    "TENANT_B",
    "TIMESTAMP_COLUMNS",
    "auth_session_database",
    "as_utc",
    "chain_from_head",
    "check_catalog",
    "column_signature",
    "credential",
    "head_revisions",
    "index_columns",
    "load_revision_module",
    "load_revision_module_from",
    "moment",
    "revision_graph",
    "seed_session",
    "squash",
    "type_label",
]
