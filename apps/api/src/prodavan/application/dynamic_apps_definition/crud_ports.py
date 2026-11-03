"""Persistence and audit ports for the dynamic-app CRUD service.

The ports are deliberately narrow so the acceptance suite can exercise every lifecycle and
idempotency rule with an in-memory fixture while production binds the SQLAlchemy adapter
over ``infrastructure/persistence/models/dynamic_apps.py``.

Transaction contract: a command service performs *all* row changes and appends exactly one
audit record, then calls :meth:`DynamicAppCrudRepository.commit` once. A failure anywhere
therefore leaves neither the mutation nor its audit trail behind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppGrant,
    AppInstance,
    AppLifecycleStatus,
    AppScopeKind,
    AppVersion,
)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Redacted durable audit entry; raw manifests and secrets never appear here."""

    event_type: str
    tenant_id: str
    actor_id: str
    scope_key: str
    operation: str
    request_digest: str
    result: Mapping[str, Any]
    trace_id: str
    project_id: str | None = None
    cabinet_id: str | None = None
    reason: str | None = None
    markers: Mapping[str, str] = field(default_factory=dict)


class AuditConflict(Exception):
    """A concurrent writer won the idempotency key; caller must re-read and replay."""


class DynamicAppAuditPort(Protocol):
    async def find(self, *, tenant_id: str, scope_key: str) -> AuditRecord | None:
        """Return the audit record previously written for this idempotency scope."""
        ...

    async def append(self, record: AuditRecord) -> None:
        """Append without committing; raises :class:`AuditConflict` on a key race."""
        ...

    async def has_marker(
        self, *, tenant_id: str, marker_kind: str, marker_values: Mapping[str, str]
    ) -> bool:
        """Check for a durable domain marker (for example a revoked grant)."""
        ...


class DynamicAppCrudRepository(Protocol):
    # Definitions -------------------------------------------------------------------
    async def get_definition(
        self, *, tenant_id: str, definition_id: str
    ) -> AppDefinition | None: ...

    async def get_definition_by_app_id(
        self, *, tenant_id: str, app_id: str
    ) -> AppDefinition | None: ...

    async def list_definitions(
        self,
        *,
        tenant_id: str,
        limit: int,
        after_id: str | None = None,
        lifecycle: AppLifecycleStatus | None = None,
    ) -> Sequence[AppDefinition]: ...

    async def add_definition(self, definition: AppDefinition) -> None: ...

    async def transition_definition(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        expected_status: AppLifecycleStatus,
        status: AppLifecycleStatus,
        current_version_id: str | None,
    ) -> bool:
        """Compare-and-set; ``False`` means the row moved under us."""
        ...

    # Versions ----------------------------------------------------------------------
    async def get_version(
        self, *, definition_id: str, version_id: str
    ) -> AppVersion | None: ...

    async def get_version_by_manifest_version(
        self, *, definition_id: str, manifest_version: str
    ) -> AppVersion | None: ...

    async def list_versions(
        self, *, definition_id: str, limit: int, after_id: str | None = None
    ) -> Sequence[AppVersion]: ...

    async def add_version(self, version: AppVersion) -> None: ...

    async def transition_version(
        self,
        *,
        definition_id: str,
        version_id: str,
        expected_status: AppLifecycleStatus,
        status: AppLifecycleStatus,
        published_at: datetime | None,
    ) -> bool: ...

    # Grants ------------------------------------------------------------------------
    async def get_grant(
        self,
        *,
        app_version_id: str,
        scope_kind: AppScopeKind,
        scope_id: str,
    ) -> AppGrant | None: ...

    async def list_grants(
        self, *, definition_id: str, version_id: str
    ) -> Sequence[AppGrant]: ...

    async def add_grant(self, grant: AppGrant) -> None: ...

    async def replace_grant(self, grant: AppGrant) -> None:
        """Persist an updated capability set for an existing (version, scope) row."""
        ...

    # Instances ---------------------------------------------------------------------
    async def get_instance_for_scope(
        self, *, definition_id: str, scope_kind: AppScopeKind, scope_id: str
    ) -> AppInstance | None: ...

    async def list_instances(
        self, *, definition_id: str, limit: int, after_id: str | None = None
    ) -> Sequence[AppInstance]: ...

    async def add_instance(self, instance: AppInstance) -> None: ...

    # Transaction -------------------------------------------------------------------
    async def flush(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class DynamicAppScopeAuthority(Protocol):
    """Proves that a cabinet/project target really belongs to the resolved tenant."""

    async def require(
        self, *, tenant_id: str, scope_kind: AppScopeKind, scope_id: str
    ) -> str:
        """Return the cabinet id the scope resolves to, or raise a fail-closed AppError."""
        ...


__all__ = [
    "AuditConflict",
    "AuditRecord",
    "DynamicAppAuditPort",
    "DynamicAppCrudRepository",
    "DynamicAppScopeAuthority",
]
