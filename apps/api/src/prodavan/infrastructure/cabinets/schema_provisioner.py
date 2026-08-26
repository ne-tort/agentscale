"""Per-instance PG schema — module data layer (no cabinet meta_documents)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.cabinets import schema_name_for_instance
from prodavan.infrastructure.cabinets.sql import qident

_MODULE_INSTALLATIONS_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.module_installations (
    module_id TEXT PRIMARY KEY,
    installed_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_MODULE_DATA_ROWS_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.module_data_rows (
    module_id TEXT NOT NULL,
    table_slug TEXT NOT NULL,
    row_id TEXT NOT NULL,
    body JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (module_id, table_slug, row_id)
)
"""

_MODULE_DATA_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_module_data_rows_module_table
ON {schema}.module_data_rows (module_id, table_slug)
"""


class SchemaProvisioner:
    """Creates isolated schema with module runtime data tables."""

    async def provision(self, session: AsyncSession, *, instance_id: str) -> str:
        schema = schema_name_for_instance(instance_id)
        await self.ensure_data_layer(session, schema_name=schema)
        return schema

    async def ensure_data_layer(self, session: AsyncSession, *, schema_name: str) -> None:
        qschema = qident(schema_name)
        await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {qschema}"))
        for ddl in (
            _MODULE_INSTALLATIONS_DDL,
            _MODULE_DATA_ROWS_DDL,
            _MODULE_DATA_INDEX_DDL,
        ):
            await session.execute(text(ddl.format(schema=qschema)))

    async def drop_schema(self, session: AsyncSession, *, schema_name: str) -> None:
        qschema = qident(schema_name)
        await session.execute(text(f"DROP SCHEMA IF EXISTS {qschema} CASCADE"))

    async def schema_exists(self, session: AsyncSession, *, schema_name: str) -> bool:
        q = await session.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
            {"name": schema_name},
        )
        return q.scalar_one_or_none() is not None
