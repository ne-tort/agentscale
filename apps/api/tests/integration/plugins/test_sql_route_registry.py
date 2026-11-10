"""Stateful offline SQL acceptance for durable plugin routes (not run in freeze)."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.dialects.sqlite.base import SQLiteCompiler
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from prodavan.infrastructure.persistence.models import _sqlite_jsonb_deep_ne
from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.plugin_platform import (
    CapabilityGrantRow,
    ModuleSourceRow,
    PluginArtifactRow,
    PluginInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.plugin_routes import PluginRouteRow
from prodavan.infrastructure.persistence.repositories.plugins.routes import (
    RouteGrantNotActive,
    RouteRegistrationConflict,
    SqlRouteRegistry,
)
from prodavan.infrastructure.plugins.runtime.k8s_scale import plugin_resource_name

NOW = datetime(2026, 11, 9, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
QUOTA = {
    "ops_per_minute": 60,
    "max_rows": 10_000,
    "max_bytes": 10_000_000,
    "max_keys": 1_000,
    "max_object_bytes": 50_000_000,
    "max_egress_bytes": 10_000_000,
}

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_contract(dbapi, _) -> None:
        dbapi.execute("PRAGMA foreign_keys=ON")
        dbapi.create_function(
            "regexp",
            2,
            lambda pattern, value: bool(re.search(pattern, str(value))),
        )
        dbapi.create_function(
            "regexp_replace",
            3,
            lambda value, pattern, replacement: re.sub(
                pattern, replacement, str(value), count=1
            ),
        )
        dbapi.create_function(
            "jsonb_typeof",
            1,
            lambda value: (
                "object" if isinstance(json.loads(value), dict) else "array"
            ),
        )
        dbapi.create_function("jsonb_deep_ne", 2, _sqlite_jsonb_deep_ne)

    tables = (
        CompanyRow.__table__,
        ModuleSourceRow.__table__,
        PluginArtifactRow.__table__,
        PluginInstanceRow.__table__,
        CapabilityGrantRow.__table__,
        PluginRouteRow.__table__,
    )
    original = getattr(SQLiteCompiler, "visit_JSONB", None)
    SQLiteCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"  # type: ignore[attr-defined]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=tables)
            )
            # SQLite ignores postgresql_where and otherwise makes this index
            # globally unique. Recreate the same partial unique constraint.
            await connection.exec_driver_sql("DROP INDEX uq_plugin_artifact_current")
            await connection.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_plugin_artifact_current "
                "ON plugin_artifacts (plugin_id) WHERE is_current = true"
            )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await _seed(session, company="co_a", instance="pinst_a", grant="pcg_a", version="1.0.0", digest=DIGEST_A)
            await _seed(session, company="co_b", instance="pinst_b", grant="pcg_b", version="1.0.1", digest=DIGEST_B)
            await session.commit()
        yield factory
    finally:
        if original is None:
            delattr(SQLiteCompiler, "visit_JSONB")
        else:
            SQLiteCompiler.visit_JSONB = original  # type: ignore[attr-defined]
        await engine.dispose()


async def _seed(session, *, company: str, instance: str, grant: str, version: str, digest: str) -> None:
    plugin = "mod_equipment"
    session.add(
        CompanyRow(
            id=company,
            name=f"Route Company {company}",
            login_slug=f"route-{company}",
            status="active",
        )
    )
    session.add(
        PluginArtifactRow(
            id=f"part_{company}",
            plugin_id=plugin,
            version=version,
            oci_digest=digest,
            manifest={"plugin_id": plugin, "version": version},
            app_version_projection={"appId": plugin, "version": version},
            status="published",
        )
    )
    # These mappers have no ORM relationships, so flush each explicit FK anchor.
    await session.flush()
    session.add(
        PluginInstanceRow(
            id=instance,
            company_id=company,
            plugin_id=plugin,
            manifest_version=version,
            artifact_digest=digest,
            status="active",
            tenant_db_name=f"tenant_{company}",
            bucket_name=f"tenant-{company}",
        )
    )
    await session.flush()
    session.add(
        CapabilityGrantRow(
            id=grant,
            company_id=company,
            plugin_id=plugin,
            plugin_instance_id=instance,
            manifest_version=version,
            artifact_digest=digest,
            capabilities=["network"],
            egress_policy={
                "mode": "webhooks-only",
                "allowed_hosts": ["hooks.example.com"],
                "allowed_paths": ["/events"],
                "approved_for_full": False,
            },
            quota=QUOTA,
            status="active",
            issued_at=NOW,
            created_by="route-test",
        )
    )


def _registry(factory) -> SqlRouteRegistry:
    return SqlRouteRegistry(session_factory=factory, now=lambda: NOW)


@pytest.mark.asyncio
async def test_exact_replay_isolation_revoke_conflict_and_canonical_projection() -> None:
    async with _database() as factory:
        registry = _registry(factory)
        first = await registry.register(
            company_id="co_a", plugin_id="mod_equipment", grant_id="pcg_a"
        )
        assert first.webhook_path == "/webhook/mod_equipment/co_a/*"
        assert first.pluginpage_path == "/pluginpage/mod_equipment/co_a/*"
        assert first.capability_path == "/capabilities/v1/*"
        assert first.egress_rule_count == 7  # one host x one path x seven methods
        assert first.deployment == plugin_resource_name("mod_equipment", "pinst_a")

        # A fresh registry instance sees and exactly replays durable state.
        replayed = await _registry(factory).register(
            company_id="co_a", plugin_id="mod_equipment", grant_id="pcg_a"
        )
        assert replayed == first
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(PluginRouteRow)) == 1
            row = await session.get(PluginRouteRow, "pcg_a")
            assert row is not None
            assert row.instance_id == "pinst_a"
            assert row.created_at is not None and row.updated_at is not None

        # A valid grant id cannot be replayed under another company.
        with pytest.raises(RouteGrantNotActive):
            await registry.register(
                company_id="co_b", plugin_id="mod_equipment", grant_id="pcg_a"
            )
        await registry.register(
            company_id="co_b", plugin_id="mod_equipment", grant_id="pcg_b"
        )

        # A tampered persisted projection is never silently repaired/rewritten.
        async with factory() as session:
            await session.execute(
                update(PluginRouteRow)
                .where(PluginRouteRow.grant_id == "pcg_a")
                .values(deployment="not-the-canonical-name")
            )
            await session.commit()
        with pytest.raises(RouteRegistrationConflict):
            await registry.register(
                company_id="co_a", plugin_id="mod_equipment", grant_id="pcg_a"
            )
        async with factory() as session:
            tampered = await session.get(PluginRouteRow, "pcg_a")
            assert tampered is not None
            assert tampered.deployment == "not-the-canonical-name"

        # Wrong-company unregister is a no-op. Exact unregister is idempotent
        # and leaves the other company's grant/route untouched.
        assert await registry.unregister(
            company_id="co_b", plugin_id="mod_equipment", grant_id="pcg_a"
        )
        async with factory() as session:
            assert await session.get(PluginRouteRow, "pcg_a") is not None
            grant = await session.get(CapabilityGrantRow, "pcg_a")
            assert grant is not None
            grant.status = "revoked"
            grant.revoked_at = NOW
            await session.commit()
        assert await registry.unregister(
            company_id="co_a", plugin_id="mod_equipment", grant_id="pcg_a"
        )
        assert await registry.unregister(
            company_id="co_a", plugin_id="mod_equipment", grant_id="pcg_a"
        )
        with pytest.raises(RouteGrantNotActive):
            await registry.register(
                company_id="co_a", plugin_id="mod_equipment", grant_id="pcg_a"
            )
        async with factory() as session:
            assert await session.get(PluginRouteRow, "pcg_a") is None
            assert await session.get(PluginRouteRow, "pcg_b") is not None


@pytest.mark.asyncio
async def test_sqlite_checks_preserve_latest_and_jsonb_deep_semantics() -> None:
    def artifact(
        row_id: str,
        version: str,
        digest: str,
        manifest: dict,
        projection: dict,
        **extra,
    ) -> PluginArtifactRow:
        return PluginArtifactRow(
            id=row_id,
            plugin_id="mod_equipment",
            version=version,
            oci_digest=digest,
            manifest=manifest,
            app_version_projection=projection,
            status="published",
            **extra,
        )

    async with _database() as factory:
        async with factory() as session:
            session.add(
                artifact(
                    "part_latest_forbidden",
                    "2.0.0",
                    "sha256:" + "c" * 64,
                    {"plugin_id": "mod_equipment", "version": "2.0.0"},
                    {"appId": "mod-equipment", "version": "2.0.0"},
                    backend_digest=DIGEST_A,
                    backend_image_ref=(
                        f"ghcr.io/managed-plugins/mod_equipment:latest@{DIGEST_A}"
                    ),
                )
            )
            with pytest.raises(IntegrityError, match="backend_image_ref"):
                await session.commit()
            await session.rollback()

            # JSONB equality is deep and numeric-aware, not a textual comparison.
            session.add(
                artifact(
                    "part_deep_equal",
                    "2.0.1",
                    "sha256:" + "d" * 64,
                    {"a": 1, "b": [True]},
                    {"b": [True], "a": 1.0},
                )
            )
            with pytest.raises(IntegrityError, match="projection_differs"):
                await session.commit()
            await session.rollback()

            # A JSON boolean and a JSON number remain different types.
            session.add(
                artifact(
                    "part_deep_different_types",
                    "2.0.2",
                    "sha256:" + "e" * 64,
                    {"value": True},
                    {"value": 1},
                )
            )
            await session.commit()
