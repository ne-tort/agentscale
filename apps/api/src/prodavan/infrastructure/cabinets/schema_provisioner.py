"""Per-instance PG schema — free-form meta_documents JSONB only."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.cabinets import schema_name_for_instance
from prodavan.infrastructure.cabinets.sql import qident


class SchemaProvisioner:
    """Creates isolated schema with meta_documents; no typed catalog."""

    async def provision(self, session: AsyncSession, *, instance_id: str) -> str:
        schema = schema_name_for_instance(instance_id)
        qschema = qident(schema)

        await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {qschema}"))
        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_documents (
                    slug TEXT PRIMARY KEY,
                    body JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        return schema

    async def drop_schema(self, session: AsyncSession, *, schema_name: str) -> None:
        qschema = qident(schema_name)
        await session.execute(text(f"DROP SCHEMA IF EXISTS {qschema} CASCADE"))

    async def schema_exists(self, session: AsyncSession, *, schema_name: str) -> bool:
        q = await session.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
            {"name": schema_name},
        )
        return q.scalar_one_or_none() is not None
