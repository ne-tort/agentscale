"""SQLAlchemy adapter binding the dynamic-app CRUD ports to the legacy ORM rows.

Row ownership note: the target ``m04e_*``/``m05_*`` tables are migration evidence and are
explicitly not positive-writable yet (see ``dynamic_app_definition_version.py`` module
docstring). The tenant CRUD surface therefore binds the *existing* runtime tables from
``infrastructure/persistence/models/dynamic_apps.py``:

    dynamic_app_definitions -> AppDefinitionRow
    dynamic_app_versions    -> AppVersionRow
    dynamic_app_instances   -> AppInstanceRow
    dynamic_app_grants      -> AppGrantRow

Audit and idempotency ride on the existing ``platform_events`` bus: its
``uq_platform_events_idempotency_key`` constraint provides the durable, race-proof
idempotency identity, and each audit record is appended in the same transaction as the
mutation it describes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppGrant,
    AppInstance,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
    CapabilityGrant,
    RequestedCapability,
    RollbackMetadata,
)
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_apps import (
    AppDefinitionRow,
    AppGrantRow,
    AppInstanceRow,
    AppVersionRow,
)
from prodavan.infrastructure.persistence.models.platform_events import PlatformEventRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

from .crud_contracts import DynamicAppAuditEvent
from .crud_ports import AuditConflict, AuditRecord

_AUDIT_EVENT_TYPES = frozenset(event.value for event in DynamicAppAuditEvent)
_IdempotencyKey = "dapp-idempotency"
_IDEMPOTENCY_CONSTRAINT = "uq_platform_events_idempotency_key"
# Cabinet/project states that can never receive a new installation.
_DEAD_CABINET_STATES = frozenset({"deleted", "archived", "purged"})
_DEAD_PROJECT_STATES = frozenset({"deleted", "completed"})


def _now() -> datetime:
    return datetime.now(UTC)


def _grant_payload(grants: Sequence[CapabilityGrant]) -> list[dict[str, Any]]:
    return [grant.model_dump(mode="json") for grant in grants]


def _grant_from_payload(payload: object) -> tuple[CapabilityGrant, ...]:
    if not isinstance(payload, list):
        raise ValueError("grant payload must be a JSON array")
    return tuple(CapabilityGrant.model_validate(item) for item in payload)


def _requested_from_payload(payload: object) -> tuple[RequestedCapability, ...]:
    if not isinstance(payload, list):
        raise ValueError("requested capabilities must be a JSON array")
    result: list[RequestedCapability] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError("requested capability entries must be objects")
        result.append(
            RequestedCapability(
                name=item["name"],
                reason=item["reason"],
                constraints=item.get("constraints"),
                optional=bool(item.get("optional", False)),
            )
        )
    return tuple(result)


class SqlAlchemyDynamicAppRepository:
    """ORM-backed implementation of :class:`DynamicAppCrudRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------- definitions --

    async def get_definition(
        self, *, tenant_id: str, definition_id: str
    ) -> AppDefinition | None:
        row = await self._session.get(AppDefinitionRow, definition_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return self._definition(row)

    async def get_definition_by_app_id(
        self, *, tenant_id: str, app_id: str
    ) -> AppDefinition | None:
        row = await self._session.scalar(
            select(AppDefinitionRow).where(
                AppDefinitionRow.tenant_id == tenant_id,
                AppDefinitionRow.app_id == app_id,
            )
        )
        return self._definition(row) if row is not None else None

    async def list_definitions(
        self,
        *,
        tenant_id: str,
        limit: int,
        after_id: str | None = None,
        lifecycle: AppLifecycleStatus | None = None,
    ) -> Sequence[AppDefinition]:
        stmt = select(AppDefinitionRow).where(AppDefinitionRow.tenant_id == tenant_id)
        if lifecycle is not None:
            stmt = stmt.where(AppDefinitionRow.status == lifecycle.value)
        if after_id:
            stmt = stmt.where(AppDefinitionRow.id > after_id)
        stmt = stmt.order_by(AppDefinitionRow.id).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(self._definition(row) for row in rows)

    async def add_definition(self, definition: AppDefinition) -> None:
        self._session.add(
            AppDefinitionRow(
                id=definition.id,
                tenant_id=definition.tenant_id,
                app_id=definition.app_id,
                display_name=definition.display_name,
                description=definition.description,
                scope_kind=definition.scope.kind.value,
                scope_id=definition.scope.id,
                status=definition.status.value,
                current_version_id=definition.current_version_id,
            )
        )

    async def transition_definition(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        expected_status: AppLifecycleStatus,
        status: AppLifecycleStatus,
        current_version_id: str | None,
    ) -> bool:
        result = await self._session.execute(
            update(AppDefinitionRow)
            .where(
                AppDefinitionRow.id == definition_id,
                AppDefinitionRow.tenant_id == tenant_id,
                AppDefinitionRow.status == expected_status.value,
            )
            .values(
                status=status.value,
                current_version_id=current_version_id,
                updated_at=_now(),
            )
        )
        return bool(result.rowcount)

    # ------------------------------------------------------------------- versions --

    async def get_version(
        self, *, definition_id: str, version_id: str
    ) -> AppVersion | None:
        row = await self._session.get(AppVersionRow, version_id)
        if row is None or row.app_definition_id != definition_id:
            return None
        return self._version(row)

    async def get_version_by_manifest_version(
        self, *, definition_id: str, manifest_version: str
    ) -> AppVersion | None:
        row = await self._session.scalar(
            select(AppVersionRow).where(
                AppVersionRow.app_definition_id == definition_id,
                AppVersionRow.manifest_version == manifest_version,
            )
        )
        return self._version(row) if row is not None else None

    async def list_versions(
        self, *, definition_id: str, limit: int, after_id: str | None = None
    ) -> Sequence[AppVersion]:
        stmt = select(AppVersionRow).where(
            AppVersionRow.app_definition_id == definition_id
        )
        if after_id:
            stmt = stmt.where(AppVersionRow.id > after_id)
        stmt = stmt.order_by(AppVersionRow.created_at, AppVersionRow.id).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(self._version(row) for row in rows)

    async def add_version(self, version: AppVersion) -> None:
        self._session.add(
            AppVersionRow(
                id=version.id,
                app_definition_id=version.app_definition_id,
                manifest_version=version.manifest_version,
                artifact_digest=version.artifact_digest,
                manifest=dict(version.manifest),
                requested_capabilities=[
                    item.model_dump(mode="json") for item in version.requested_capabilities
                ],
                status=version.status.value,
                published_at=version.published_at,
            )
        )

    async def transition_version(
        self,
        *,
        definition_id: str,
        version_id: str,
        expected_status: AppLifecycleStatus,
        status: AppLifecycleStatus,
        published_at: datetime | None,
    ) -> bool:
        result = await self._session.execute(
            update(AppVersionRow)
            .where(
                AppVersionRow.id == version_id,
                AppVersionRow.app_definition_id == definition_id,
                AppVersionRow.status == expected_status.value,
            )
            .values(status=status.value, published_at=published_at)
        )
        return bool(result.rowcount)

    # --------------------------------------------------------------------- grants --

    async def get_grant(
        self,
        *,
        app_version_id: str,
        scope_kind: AppScopeKind,
        scope_id: str,
    ) -> AppGrant | None:
        row = await self._session.scalar(
            select(AppGrantRow).where(
                AppGrantRow.app_version_id == app_version_id,
                AppGrantRow.scope_kind == scope_kind.value,
                AppGrantRow.scope_id == scope_id,
            )
        )
        return self._grant(row) if row is not None else None

    async def list_grants(
        self, *, definition_id: str, version_id: str
    ) -> Sequence[AppGrant]:
        rows = (
            await self._session.execute(
                select(AppGrantRow)
                .where(
                    AppGrantRow.app_definition_id == definition_id,
                    AppGrantRow.app_version_id == version_id,
                )
                .order_by(AppGrantRow.scope_kind, AppGrantRow.scope_id)
            )
        ).scalars().all()
        return tuple(self._grant(row) for row in rows)

    async def add_grant(self, grant: AppGrant) -> None:
        self._session.add(
            AppGrantRow(
                id=grant.id,
                app_definition_id=grant.app_definition_id,
                app_version_id=grant.app_version_id,
                scope_kind=grant.scope.kind.value,
                scope_id=grant.scope.id,
                grants=_grant_payload(grant.grants),
                issued_at=grant.issued_at,
                expires_at=grant.expires_at,
            )
        )

    async def replace_grant(self, grant: AppGrant) -> None:
        result = await self._session.execute(
            update(AppGrantRow)
            .where(
                AppGrantRow.id == grant.id,
                AppGrantRow.app_definition_id == grant.app_definition_id,
                AppGrantRow.app_version_id == grant.app_version_id,
            )
            .values(grants=_grant_payload(grant.grants), expires_at=grant.expires_at)
        )
        if not result.rowcount:
            raise RuntimeError("grant row disappeared during update")

    # ------------------------------------------------------------------ instances --

    async def get_instance_for_scope(
        self, *, definition_id: str, scope_kind: AppScopeKind, scope_id: str
    ) -> AppInstance | None:
        row = await self._session.scalar(
            select(AppInstanceRow).where(
                AppInstanceRow.app_definition_id == definition_id,
                AppInstanceRow.scope_kind == scope_kind.value,
                AppInstanceRow.scope_id == scope_id,
            )
        )
        return self._instance(row) if row is not None else None

    async def list_instances(
        self, *, definition_id: str, limit: int, after_id: str | None = None
    ) -> Sequence[AppInstance]:
        stmt = select(AppInstanceRow).where(
            AppInstanceRow.app_definition_id == definition_id
        )
        if after_id:
            stmt = stmt.where(AppInstanceRow.id > after_id)
        stmt = stmt.order_by(AppInstanceRow.id).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(self._instance(row) for row in rows)

    async def add_instance(self, instance: AppInstance) -> None:
        self._session.add(
            AppInstanceRow(
                id=instance.id,
                app_definition_id=instance.app_definition_id,
                app_version_id=instance.app_version_id,
                scope_kind=instance.scope.kind.value,
                scope_id=instance.scope.id,
                status=instance.status.value,
                runtime_ref=instance.runtime_ref,
            )
        )

    # ---------------------------------------------------------------- transaction --

    async def flush(self) -> None:
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    # -------------------------------------------------------------------- mapping --

    @staticmethod
    def _definition(row: AppDefinitionRow) -> AppDefinition:
        return AppDefinition(
            id=row.id,
            tenant_id=row.tenant_id,
            app_id=row.app_id,
            display_name=row.display_name,
            description=row.description,
            scope=AppScope(kind=AppScopeKind(row.scope_kind), id=row.scope_id),
            status=AppLifecycleStatus(row.status),
            current_version_id=row.current_version_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _version(row: AppVersionRow) -> AppVersion:
        if not isinstance(row.manifest, Mapping):
            raise ValueError("manifest must be persisted as an object")
        return AppVersion(
            id=row.id,
            app_definition_id=row.app_definition_id,
            manifest_version=row.manifest_version,
            artifact_digest=row.artifact_digest,
            manifest=dict(row.manifest),
            requested_capabilities=_requested_from_payload(row.requested_capabilities),
            status=AppLifecycleStatus(row.status),
            rollback=RollbackMetadata(
                from_version_id=row.rollback_from_version_id,
                to_version_id=row.rollback_to_version_id,
                reason=row.rollback_reason,
                occurred_at=row.rollback_at,
            ),
            created_at=row.created_at,
            published_at=row.published_at,
        )

    @staticmethod
    def _grant(row: AppGrantRow) -> AppGrant:
        return AppGrant(
            id=row.id,
            app_definition_id=row.app_definition_id,
            app_version_id=row.app_version_id,
            scope=AppScope(kind=AppScopeKind(row.scope_kind), id=row.scope_id),
            grants=_grant_from_payload(row.grants),
            issued_at=row.issued_at,
            expires_at=row.expires_at,
        )

    @staticmethod
    def _instance(row: AppInstanceRow) -> AppInstance:
        return AppInstance(
            id=row.id,
            app_definition_id=row.app_definition_id,
            app_version_id=row.app_version_id,
            scope=AppScope(kind=AppScopeKind(row.scope_kind), id=row.scope_id),
            status=AppLifecycleStatus(row.status),
            rollback=RollbackMetadata(
                from_version_id=row.rollback_from_version_id,
                to_version_id=row.rollback_to_version_id,
                reason=row.rollback_reason,
                occurred_at=row.rollback_at,
            ),
            runtime_ref=row.runtime_ref,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class PlatformEventAuditAdapter:
    """Durable audit/idempotency adapter over the ``platform_events`` bus.

    The audit row's ``idempotency_key`` is the tenant/operation-scoped idempotency identity,
    so a duplicate concurrent mutation cannot create a second record: the unique constraint
    raises and :class:`AuditConflict` makes the caller fail closed (replay or 409).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, *, tenant_id: str, scope_key: str) -> AuditRecord | None:
        row = await self._session.scalar(
            select(PlatformEventRow).where(
                PlatformEventRow.company_id == tenant_id,
                PlatformEventRow.idempotency_key == scope_key,
                PlatformEventRow.event_type.in_(tuple(_AUDIT_EVENT_TYPES)),
            )
        )
        return self._record(row) if row is not None else None

    async def append(self, record: AuditRecord) -> None:
        if record.event_type not in _AUDIT_EVENT_TYPES:
            raise ValueError(f"unregistered dynamic-app audit event: {record.event_type}")
        row = PlatformEventRow(
            event_type=record.event_type,
            company_id=record.tenant_id,
            project_id=record.project_id,
            cabinet_id=record.cabinet_id,
            actor_sub=record.actor_id,
            idempotency_key=record.scope_key,
            payload={
                "_idempotency_kind": _IdempotencyKey,
                "scope_key": record.scope_key,
                "operation": record.operation,
                "request_digest": record.request_digest,
                "trace_id": record.trace_id,
                "actor_id": record.actor_id,
                # Already redacted by the service; never the raw request body.
                "result": dict(record.result),
                "reason": record.reason,
                "markers": dict(record.markers),
            },
        )
        try:
            # Nested transaction: a concurrent writer winning the unique key must not
            # poison the caller's transaction, which still needs to read the winner.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            # Only the idempotency key race is a replay. Any other integrity failure
            # (for example a domain unique key) must keep its own meaning.
            if _IDEMPOTENCY_CONSTRAINT not in str(getattr(exc, "orig", exc)):
                raise
            raise AuditConflict(str(getattr(exc, "orig", exc))) from exc

    async def has_marker(
        self, *, tenant_id: str, marker_kind: str, marker_values: Mapping[str, str]
    ) -> bool:
        stmt = select(PlatformEventRow).where(
            PlatformEventRow.company_id == tenant_id,
            PlatformEventRow.event_type.in_(tuple(_AUDIT_EVENT_TYPES)),
        )
        if marker_kind == "grant_revoked":
            stmt = stmt.where(
                PlatformEventRow.event_type == DynamicAppAuditEvent.GRANT_REVOKED.value
            )
        rows = (await self._session.execute(stmt)).scalars().all()
        for row in rows:
            markers = (row.payload or {}).get("markers") or {}
            if all(markers.get(key) == value for key, value in marker_values.items()):
                return True
        return False

    @staticmethod
    def _record(row: PlatformEventRow) -> AuditRecord:
        payload = row.payload if isinstance(row.payload, Mapping) else {}
        result = payload.get("result")
        markers = payload.get("markers")
        return AuditRecord(
            event_type=row.event_type,
            tenant_id=row.company_id or "",
            actor_id=payload.get("actor_id") or row.actor_sub or "",
            scope_key=payload.get("scope_key") or row.idempotency_key or "",
            operation=payload.get("operation") or "",
            request_digest=payload.get("request_digest") or "",
            result=dict(result) if isinstance(result, Mapping) else {},
            trace_id=payload.get("trace_id") or "",
            project_id=row.project_id,
            cabinet_id=row.cabinet_id,
            reason=payload.get("reason"),
            markers=dict(markers) if isinstance(markers, Mapping) else {},
        )


class SqlAlchemyScopeAuthority:
    """Prove a cabinet/project target belongs to the server-resolved tenant.

    Fail-closed: a scope that cannot be *proved* to belong to the tenant raises
    ``NOT_FOUND`` (the service converts it back to a 404), so a caller cannot probe
    foreign cabinets/projects through installs or grants.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require(
        self, *, tenant_id: str, scope_kind: AppScopeKind, scope_id: str
    ) -> str:
        if scope_kind is AppScopeKind.CABINET:
            cabinet = await self._session.get(CabinetInstanceRow, scope_id)
            if cabinet is None or cabinet.status in _DEAD_CABINET_STATES:
                raise _scope_not_found()
            if not await self._tenant_holds_cabinet(
                tenant_id=tenant_id, cabinet_id=cabinet.id
            ):
                raise _scope_not_found()
            return cabinet.id

        if scope_kind is AppScopeKind.PROJECT:
            project = await self._session.get(ProjectRow, scope_id)
            if project is None or project.status in _DEAD_PROJECT_STATES:
                raise _scope_not_found()
            if project.company_id != tenant_id:
                raise _scope_not_found()
            if not await self._tenant_holds_cabinet(
                tenant_id=tenant_id, cabinet_id=project.cabinet_id
            ):
                raise _scope_not_found()
            return project.cabinet_id

        raise _scope_not_found()

    async def _tenant_holds_cabinet(self, *, tenant_id: str, cabinet_id: str) -> bool:
        granted = await self._session.scalar(
            select(CabinetCompanyGrantRow.id).where(
                CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                CabinetCompanyGrantRow.company_id == tenant_id,
                CabinetCompanyGrantRow.status == "active",
            )
        )
        return granted is not None


def _scope_not_found() -> AppError:
    return AppError(
        code="NOT_FOUND",
        title="Not Found",
        status=404,
        detail="scope not found in this tenant",
    )


__all__ = [
    "PlatformEventAuditAdapter",
    "SqlAlchemyDynamicAppRepository",
    "SqlAlchemyScopeAuthority",
]
