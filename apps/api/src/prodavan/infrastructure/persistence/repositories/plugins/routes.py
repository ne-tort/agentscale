"""Durable SQL implementation of the admin RouteRegistryPort."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.plugins.admin.contracts import RouteRegistration
from prodavan.application.plugins.egress.errors import EgressPolicyError
from prodavan.application.plugins.egress.policy import resolve_policy
from prodavan.infrastructure.persistence.models.plugin_platform import (
    CapabilityGrantRow,
    PluginInstanceRow,
)
from prodavan.infrastructure.persistence.models.plugin_routes import PluginRouteRow
from prodavan.infrastructure.plugins.runtime.k8s_scale import plugin_resource_name

from ._schema import (
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    json_mapping,
    text,
)

_BINDING = ("company_id", "plugin_id", "grant_id")
_EXACT_PROJECTION = (
    "instance_id",
    "webhook_path",
    "pluginpage_path",
    "capability_path",
    "egress_rule_count",
    "deployment",
)


class RouteRegistrationError(PluginRepositoryError):
    """Base class for fail-closed route registration errors."""


class RouteGrantNotActive(RouteRegistrationError):
    """The exact active grant/instance binding does not exist."""


class RouteRegistrationConflict(RouteRegistrationError):
    """A different projection already occupies the exact durable key."""


class SqlRouteRegistry:
    """Persist route projections using one shared session factory.

    Every call performs a fresh authoritative grant lookup. No route, grant or
    egress data is cached in the registry instance, so replicas and restarts see
    the same state. A borrowed session is supported for caller-owned sagas; an
    owned session is committed before success is returned.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        now: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                "SqlRouteRegistry requires session_factory or session"
            )
        self._session_factory = session_factory
        self._session = session
        self._clock = now or (lambda: datetime.now(UTC))

    def _session_ctx(self) -> Any:
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None
        return self._session_factory()

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise PluginRepositoryError("route registry clock must be aware")
        return value.astimezone(UTC)

    @staticmethod
    def _egress_rule_count(grant: CapabilityGrantRow) -> int:
        """Count the same host/path/method rules generated from the grant."""
        try:
            raw = json_mapping(grant.egress_policy, field="egress_policy")
            approved = raw.get("approved", raw.get("approved_for_full", False))
            policy = resolve_policy(
                {
                    "grant_id": grant.id,
                    "egress": {
                        "mode": raw.get("mode", "none"),
                        "allowed_hosts": raw.get("allowed_hosts", ()),
                        "allowed_paths": raw.get("allowed_paths", ()),
                        "max_egress_bytes": raw.get("max_egress_bytes", 0),
                        "approved": approved,
                    },
                }
            )
            return len(policy.rules)
        except (EgressPolicyError, PluginRepositoryError, TypeError, ValueError) as exc:
            raise PluginRepositorySchemaError(
                f"persisted grant egress policy is invalid: {exc}"
            ) from exc

    @staticmethod
    def _to_registration(row: PluginRouteRow) -> RouteRegistration:
        return RouteRegistration(
            company_id=row.company_id,
            plugin_id=row.plugin_id,
            grant_id=row.grant_id,
            webhook_path=row.webhook_path,
            pluginpage_path=row.pluginpage_path,
            capability_path=row.capability_path,
            egress_rule_count=row.egress_rule_count,
            deployment=row.deployment,
        )

    async def register(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> RouteRegistration:
        company_id = text(company_id, field="company_id")
        plugin_id = text(plugin_id, field="plugin_id")
        grant_id = text(grant_id, field="grant_id")
        moment = self._now()
        try:
            async with self._session_ctx() as session:
                # The instance join proves the complete company/plugin/instance
                # binding; grant_id alone must never authorize another tenant.
                grant = (
                    await session.execute(
                        select(CapabilityGrantRow)
                        .join(
                            PluginInstanceRow,
                            PluginInstanceRow.id
                            == CapabilityGrantRow.plugin_instance_id,
                        )
                        .where(
                            CapabilityGrantRow.id == grant_id,
                            CapabilityGrantRow.company_id == company_id,
                            CapabilityGrantRow.plugin_id == plugin_id,
                            CapabilityGrantRow.status == "active",
                            CapabilityGrantRow.revoked_at.is_(None),
                            or_(
                                CapabilityGrantRow.expires_at.is_(None),
                                CapabilityGrantRow.expires_at > moment,
                            ),
                            PluginInstanceRow.company_id == company_id,
                            PluginInstanceRow.plugin_id == plugin_id,
                        )
                        # Serialize register against revoke/unregister on the
                        # authoritative grant row (SQLite ignores FOR UPDATE).
                        .with_for_update()
                    )
                ).scalars().first()
                if grant is None:
                    raise RouteGrantNotActive(
                        "exact active grant/company/plugin/instance binding not found"
                    )

                # Use persisted IDs, never caller-supplied path/deployment data.
                authoritative_company = text(grant.company_id, field="company_id")
                authoritative_plugin = text(grant.plugin_id, field="plugin_id")
                authoritative_instance = text(
                    grant.plugin_instance_id, field="instance_id"
                )
                values = {
                    "company_id": authoritative_company,
                    "plugin_id": authoritative_plugin,
                    "grant_id": text(grant.id, field="grant_id"),
                    "instance_id": authoritative_instance,
                    "webhook_path": (
                        f"/webhook/{authoritative_plugin}/{authoritative_company}/*"
                    ),
                    "pluginpage_path": (
                        f"/pluginpage/{authoritative_plugin}/{authoritative_company}/*"
                    ),
                    "capability_path": "/capabilities/v1/*",
                    "egress_rule_count": self._egress_rule_count(grant),
                    "deployment": plugin_resource_name(
                        authoritative_plugin, authoritative_instance
                    ),
                    "created_at": moment,
                    "updated_at": moment,
                }

                dialect = session.get_bind().dialect.name
                if dialect == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert as dialect_insert
                elif dialect == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert as dialect_insert
                else:
                    raise PluginRepositoryUnavailable(
                        f"SqlRouteRegistry does not support dialect {dialect!r}"
                    )

                exact_match = [
                    getattr(PluginRouteRow, name) == values[name]
                    for name in (*_BINDING, *_EXACT_PROJECTION)
                ]
                statement = (
                    dialect_insert(PluginRouteRow)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=list(_BINDING),
                        set_={"updated_at": moment},
                        where=and_(*exact_match),
                    )
                    .returning(PluginRouteRow)
                )
                row = (await session.execute(statement)).scalars().first()
                if row is None:
                    # DO UPDATE's exact-match predicate rejected a mismatched row.
                    # Never overwrite it and never report a successful replay.
                    raise RouteRegistrationConflict(
                        "durable route projection differs from the authoritative grant"
                    )
                registration = self._to_registration(row)
                if self._session is None:
                    await session.commit()
                return registration
        except RouteRegistrationError:
            raise
        except IntegrityError as exc:
            # A grant_id PK collision with different company/plugin values does
            # not match the composite arbiter and is still a conflict, not an
            # opportunity to overwrite another tenant's route.
            raise RouteRegistrationConflict(
                "durable route key conflicts with an existing projection"
            ) from exc
        except PluginRepositorySchemaError:
            raise
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(
                f"register plugin route failed: {exc}"
            ) from exc

    async def unregister(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> bool:
        """Delete only the exact grant route; absence is an idempotent success."""
        company_id = text(company_id, field="company_id")
        plugin_id = text(plugin_id, field="plugin_id")
        grant_id = text(grant_id, field="grant_id")
        try:
            async with self._session_ctx() as session:
                await session.execute(
                    delete(PluginRouteRow).where(
                        PluginRouteRow.company_id == company_id,
                        PluginRouteRow.plugin_id == plugin_id,
                        PluginRouteRow.grant_id == grant_id,
                    )
                )
                if self._session is None:
                    await session.commit()
                return True
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(
                f"unregister plugin route failed: {exc}"
            ) from exc


class _BorrowedSession:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


__all__ = [
    "RouteGrantNotActive",
    "RouteRegistrationConflict",
    "RouteRegistrationError",
    "SqlRouteRegistry",
]
