"""SQLite mirror of the four M04E ledger tables, for executable store acceptance.

The ledger tables are declared with PostgreSQL ``JSONB`` and several CHECK
constraints that use PostgreSQL-only operators (``~`` for a sha256 regex,
``jsonb_typeof``/``jsonb_array_length``, ``btrim``), none of which SQLite can render or
evaluate. ``Base.metadata.create_all`` therefore cannot build them here, so this module
mirrors the **registered** models column for column:

* every column keeps its name, type (``JSONB`` -> ``JSON``), nullability and primary
  key, taken from the model, never hand-written;
* every unique constraint is copied (``uq_m04e_mapping_source``,
  ``uq_m04e_quarantine_observation``, ``uq_m04e_checkpoint_partition``,
  ``uq_m04e_checkpoint_token``) — they are the exact-key/idempotency contract the store
  depends on, and SQLite enforces them;
* CHECK constraints are dropped, because SQLite cannot evaluate the PostgreSQL
  predicates. That is not a licence to skip the rules: the constraints' *effects* are
  asserted separately against the metadata (``tests/migration/m04e_ledger``), and the
  store is additionally tested here for refusing to write a row the checks would reject
  (an empty checkpoint, a non-sha256 digest, an observation naming two digests).

The mirror is test-only. Nothing in ``src/`` imports this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionAuditRow,
    M04EDefinitionVersionCheckpointRow,
    M04EDefinitionVersionMappingRow,
    M04EDefinitionVersionQuarantineRow,
)

#: The registered models, in the order the ledger revision creates them.
LEDGER_MODELS = (
    M04EDefinitionVersionMappingRow,
    M04EDefinitionVersionQuarantineRow,
    M04EDefinitionVersionCheckpointRow,
    M04EDefinitionVersionAuditRow,
)

MAPPING_TABLE = M04EDefinitionVersionMappingRow.__tablename__
QUARANTINE_TABLE = M04EDefinitionVersionQuarantineRow.__tablename__
CHECKPOINT_TABLE = M04EDefinitionVersionCheckpointRow.__tablename__
AUDIT_TABLE = M04EDefinitionVersionAuditRow.__tablename__


def _mirror_column(column: sa.Column) -> sa.Column:
    """One model column rendered for SQLite, with its real type, nullability and default.

    ``JSONB`` becomes ``JSON``: the wire shape is identical (both are serialised JSON
    text) and the store reads back a ``list``/``dict`` either way. Every other type is
    copied verbatim, so a changed column type in the model changes the mirror too.

    ``server_default`` is copied because it is part of the column contract the store
    relies on: ``recorded_at`` has no Python-side default, so dropping the server
    default here would make every insert fail on a column the real table fills itself —
    a mirror defect that would look like a store defect.
    """

    return sa.Column(
        column.name,
        sa.JSON() if isinstance(column.type, JSONB) else column.type,
        primary_key=column.primary_key,
        nullable=column.nullable,
        server_default=column.server_default,
    )


def mirror_table(model: Any, metadata: sa.MetaData) -> sa.Table:
    """Copy one registered ledger model into ``metadata`` as a SQLite-renderable table."""

    source = model.__table__
    keys = [
        sa.UniqueConstraint(
            *(column.name for column in constraint.columns), name=constraint.name,
        )
        for constraint in source.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    ]
    return sa.Table(
        source.name,
        metadata,
        *(_mirror_column(column) for column in source.columns),
        *keys,
    )


class LedgerMirror:
    """One ephemeral SQLite database holding the four ledger tables.

    ``insert``/``count``/``rows`` exist so a test can seed a pre-existing cursor or
    probe what a page actually wrote without going through the store (a store method
    that is wrong must not be the only way to observe its own effect).
    """

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {
            model.__tablename__: mirror_table(model, self.metadata) for model in LEDGER_MODELS
        }
        self.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False,
        )

    async def create(self) -> "LedgerMirror":
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda sync: self.metadata.create_all(sync))
        return self

    async def close(self) -> None:
        await self.engine.dispose()

    async def insert(self, table_name: str, **values: Any) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(self.tables[table_name].insert().values(**values))

    async def count(self, table_name: str) -> int:
        table = self.tables[table_name]
        async with self.engine.connect() as connection:
            return int(
                (await connection.scalar(sa.select(sa.func.count()).select_from(table))) or 0
            )

    async def rows(self, table_name: str) -> list[dict[str, Any]]:
        table = self.tables[table_name]
        async with self.engine.connect() as connection:
            result = await connection.execute(sa.select(table).order_by(table.c.id))
            return [dict(row) for row in result.mappings()]

    async def single(self, table_name: str) -> dict[str, Any] | None:
        rows = await self.rows(table_name)
        assert len(rows) <= 1, f"{table_name} has {len(rows)} rows, expected at most one"
        return rows[0] if rows else None

    async def execute(self, statement: str, **params: Any) -> None:
        """Run one raw statement (used to simulate a peer's cursor advance)."""

        async with self.engine.begin() as connection:
            await connection.execute(sa.text(statement), params)

    async def statement_count(self) -> int:
        """How many rows the ledger holds in total (any table).

        A page that writes nothing must leave this number unchanged; a test that only
        checked "no mapping row" could miss an audit or quarantine row appended behind
        its back.
        """

        total = 0
        for name in self.tables:
            total += await self.count(name)
        return total


def rows_to_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Sum the counters of a list of audit rows (test-side arithmetic only)."""

    totals: dict[str, int] = {}
    for row in rows:
        for key in (
            "record_count", "accepted_count", "replay_count",
            "quarantined_count", "conflict_count",
        ):
            totals[key] = totals.get(key, 0) + int(row.get(key) or 0)
    return totals


__all__ = [
    "AUDIT_TABLE",
    "CHECKPOINT_TABLE",
    "LEDGER_MODELS",
    "LedgerMirror",
    "MAPPING_TABLE",
    "QUARANTINE_TABLE",
    "mirror_table",
    "rows_to_counts",
]
