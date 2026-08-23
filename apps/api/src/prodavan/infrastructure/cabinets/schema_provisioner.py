"""Per-instance PG schema provisioning — meta catalog + Base seed (L06)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.cabinets import BASE_SYSTEM_TABS, schema_name_for_instance
from prodavan.infrastructure.cabinets.sql import qident


def _meta_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SchemaProvisioner:
    """Creates isolated schema + meta tables; no cross-schema reads."""

    async def provision(self, session: AsyncSession, *, instance_id: str) -> str:
        schema = schema_name_for_instance(instance_id)
        qschema = qident(schema)

        await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {qschema}"))

        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_tables (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    storage_kind TEXT NOT NULL DEFAULT 'physical',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_columns (
                    id TEXT PRIMARY KEY,
                    table_id TEXT NOT NULL REFERENCES {qschema}.meta_tables(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    col_type TEXT NOT NULL,
                    required BOOLEAN NOT NULL DEFAULT false,
                    unique_col BOOLEAN NOT NULL DEFAULT false,
                    ref_table_slug TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (table_id, name)
                )
                """
            )
        )
        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_views (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    table_slug TEXT,
                    ui_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    version INT NOT NULL DEFAULT 1,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_tabs (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    tab_order INT NOT NULL,
                    view_id TEXT REFERENCES {qschema}.meta_views(id) ON DELETE SET NULL,
                    system_tab BOOLEAN NOT NULL DEFAULT false,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_mcp_tools (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    config JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_mcp_packages (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    runtime TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    artifact_ref TEXT NOT NULL,
                    manifest JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    content_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (name, version)
                )
                """
            )
        )

        for title, order, slug in BASE_SYSTEM_TABS:
            view_id = _meta_id("view")
            tab_id = _meta_id("tab")
            await session.execute(
                text(
                    f"""
                    INSERT INTO {qschema}.meta_views (id, slug, ui_json)
                    VALUES (:vid, :slug, CAST(:ui AS jsonb))
                    ON CONFLICT (slug) DO NOTHING
                    """
                ),
                {
                    "vid": view_id,
                    "slug": slug,
                    "ui": '{"version":1,"kind":"collection","title_field":"name"}',
                },
            )
            await session.execute(
                text(
                    f"""
                    INSERT INTO {qschema}.meta_tabs (id, title, tab_order, view_id, system_tab)
                    VALUES (:tid, :title, :ord, :vid, true)
                    """
                ),
                {"tid": tab_id, "title": title, "ord": order, "vid": view_id},
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
