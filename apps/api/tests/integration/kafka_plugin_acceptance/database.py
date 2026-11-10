"""Isolated real PostgreSQL resources for durable event state.

Each test gets a new schema. The production plugin tables needed by this
acceptance path and their current ORM parent dependencies are created there.
Production SQL repositories are used without in-memory fallbacks. The schema
is the unit of cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressMode,
    EgressPolicy,
    GrantStatus,
    QuotaSet,
)
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.persistence.models.plugin_platform import (
    CapabilityGrantRow,
    ModuleSourceRow,
    PluginAuditEventRow,
    PluginArtifactRow,
    PluginEventDeliveryRow,
    PluginInstanceRow,
)
from prodavan.infrastructure.persistence.repositories.plugins.audit import AppendOnlyAuditSink
from prodavan.infrastructure.persistence.repositories.plugins.events import (
    DbIdempotencyStore,
    DbPublishedEventStore,
    DbSubscriptionStore,
)
from prodavan.infrastructure.persistence.repositories.plugins.grants import SqlCapabilityGrantRepository

from .resources import AcceptanceIdentity

_TABLE_ORDER = (
    # Current ModuleSourceRow has a composite projects FK; ProjectRow itself
    # requires these parent tables even though this harness seeds no source row.
    CompanyRow.__table__,
    EmployeeRow.__table__,
    CabinetInstanceRow.__table__,
    AiProviderKeyRow.__table__,
    ProjectRow.__table__,
    ModuleSourceRow.__table__,
    PluginArtifactRow.__table__,
    PluginInstanceRow.__table__,
    CapabilityGrantRow.__table__,
    PluginEventDeliveryRow.__table__,
    PluginAuditEventRow.__table__,
)


@dataclass(frozen=True, slots=True)
class SqlAcceptanceState:
    session_factory: async_sessionmaker[Any]
    subscriptions: DbSubscriptionStore
    idempotency: DbIdempotencyStore
    published_events: DbPublishedEventStore
    audit: AppendOnlyAuditSink
    grants: SqlCapabilityGrantRepository
    grant: CapabilityGrantRecord
    artifact_digest: str
    schema_name: str


class RealSqlAcceptance:
    def __init__(self, *, config: Any, identity: AcceptanceIdentity) -> None:
        self._config = config
        self._identity = identity
        self._admin_engine = None
        self._schema_engine = None
        self.state: SqlAcceptanceState | None = None

    async def open(self) -> SqlAcceptanceState:
        self._admin_engine = create_async_engine(
            self._config.database_dsn,
            poolclass=NullPool,
            connect_args={"timeout": min(self._config.operation_timeout_seconds, 15.0)},
        )
        quoted_schema = '"' + self._identity.schema_name + '"'
        try:
            async with self._admin_engine.connect() as connection:
                await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
                await connection.commit()
        except Exception as exc:
            await self._dispose_admin()
            raise RuntimeError("could not create the isolated acceptance schema") from exc

        self._schema_engine = create_async_engine(
            self._config.database_dsn,
            poolclass=NullPool,
            connect_args={
                "timeout": min(self._config.operation_timeout_seconds, 15.0),
                "server_settings": {
                    "search_path": self._identity.schema_name,
                    "statement_timeout": str(int(self._config.operation_timeout_seconds * 1000)),
                },
            },
        )
        try:
            async with self._schema_engine.begin() as connection:
                for table in _TABLE_ORDER:
                    await connection.run_sync(
                        lambda sync_connection, bound_table=table: bound_table.create(
                            sync_connection,
                            checkfirst=True,
                        )
                    )
            state = await self._seed()
        except Exception as exc:
            await self.aclose()
            raise RuntimeError("could not prepare production acceptance SQL state") from exc
        self.state = state
        return state

    async def aclose(self) -> None:
        if self._schema_engine is not None:
            engine = self._schema_engine
            self._schema_engine = None
            await engine.dispose()
        if self._admin_engine is not None:
            try:
                async with self._admin_engine.connect() as connection:
                    await connection.execute(text(f'DROP SCHEMA "' + self._identity.schema_name + '" CASCADE'))
                    await connection.commit()
            finally:
                await self._dispose_admin()

    async def _dispose_admin(self) -> None:
        engine = self._admin_engine
        self._admin_engine = None
        if engine is not None:
            await engine.dispose()

    async def _seed(self) -> SqlAcceptanceState:
        identity = self._identity
        # The generated suffix is lowercase hex; zero padding keeps the exact
        # production sha256:<64 lowercase hex> artifact identity shape.
        artifact_digest = "sha256:" + identity.run_id[3:].ljust(64, "0")
        session_factory = async_sessionmaker(self._schema_engine, expire_on_commit=False)
        async with session_factory() as session:
            # source_id remains NULL: the artifact is an isolated acceptance
            # fixture, not evidence of CI publication or a live source.
            session.add(PluginArtifactRow(
                id=f"part_{identity.run_id[3:]}",
                plugin_id=identity.plugin_id,
                version="1.0.0",
                oci_digest=artifact_digest,
                manifest={"plugin_id": identity.plugin_id, "acceptance": True},
                app_version_projection={"appId": identity.plugin_id.replace("_", "-"), "version": "1.0.0", "acceptance": "projection"},
                cosign_verified=True,
                status="published",
                is_current=True,
            ))
            session.add(PluginInstanceRow(
                id=identity.plugin_instance_id,
                company_id=identity.company_id,
                plugin_id=identity.plugin_id,
                manifest_version="1.0.0",
                artifact_digest=artifact_digest,
                status="active",
                desired_replicas=1,
                tenant_db_name=f"tenant_{identity.company_id}",
                bucket_name=f"tenant-{identity.company_id}",
            ))
            await session.commit()

        grants = SqlCapabilityGrantRepository(session_factory=session_factory)
        grant = CapabilityGrantRecord(
            grant_id=identity.grant_id,
            company_id=identity.company_id,
            plugin_id=identity.plugin_id,
            plugin_instance_id=identity.plugin_instance_id,
            manifest_version="1.0.0",
            artifact_digest=artifact_digest,
            capabilities=("events",),
            egress_policy=EgressPolicy(mode=EgressMode.NONE),
            quotas=QuotaSet(),
            status=GrantStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
        grant = await grants.create(grant=grant)
        return SqlAcceptanceState(
            session_factory=session_factory,
            subscriptions=DbSubscriptionStore(session_factory=session_factory),
            idempotency=DbIdempotencyStore(session_factory=session_factory),
            published_events=DbPublishedEventStore(session_factory=session_factory),
            audit=AppendOnlyAuditSink(session_factory=session_factory),
            grants=grants,
            grant=grant,
            artifact_digest=artifact_digest,
            schema_name=identity.schema_name,
        )


__all__ = ["RealSqlAcceptance", "SqlAcceptanceState"]
