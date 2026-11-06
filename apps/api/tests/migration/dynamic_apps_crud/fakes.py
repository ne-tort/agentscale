"""In-memory test doubles for the dynamic-app CRUD ports.

These are fixtures only. They are never bound in a production path: the v1 router builds
``SqlAlchemyDynamicAppRepository`` / ``PlatformEventAuditAdapter`` /
``SqlAlchemyScopeAuthority`` from the request session.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from prodavan.application.dynamic_apps_definition import (
    ActorIdentity,
    DynamicAppCrudService,
    DynamicAppOperation,
    MutationContext,
)
from prodavan.application.dynamic_apps_definition.crud_ports import (
    AuditConflict,
    AuditRecord,
)
from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppGrant,
    AppInstance,
    AppLifecycleStatus,
    AppScopeKind,
    AppVersion,
)
from prodavan.domain.errors import AppError

TENANT_A = "co_alpha"
TENANT_B = "co_beta"
ACTOR = "emp_admin"
CABINET_A = "cab_alpha"
CABINET_B = "cab_beta"
PROJECT_A = "prj_alpha"
DIGEST_1 = "sha256:" + "1" * 64
DIGEST_2 = "sha256:" + "2" * 64


def now() -> datetime:
    return datetime.now(UTC)


def manifest(
    *,
    app_id: str = "equipment-review",
    version: str = "1.0.0",
    display_name: str = "Equipment review",
    capabilities: Sequence[str] = ("ui.render", "platform.queries"),
    sha256: str | None = None,
) -> dict:
    return {
        "protocol": "1.0",
        "appId": app_id,
        "version": version,
        "displayName": display_name,
        "entrypoints": {"declarative": {"route": "/equipment/review"}},
        "requestedCapabilities": [
            {"name": name, "reason": f"{name} is needed for the review screen"}
            for name in capabilities
        ],
        **({"artifact": {"sha256": sha256}} if sha256 else {}),
    }


def context(
    *,
    tenant_id: str = TENANT_A,
    operation: DynamicAppOperation = DynamicAppOperation.CREATE_DEFINITION,
    key: str = "key-1",
    actor_id: str = ACTOR,
) -> MutationContext:
    return MutationContext(
        identity=ActorIdentity(tenant_id=tenant_id, actor_id=actor_id),
        idempotency_key=key,
        operation=operation,
    )


class FakeRepository:
    """Deterministic stand-in for ``DynamicAppCrudRepository`` with CAS semantics."""

    def __init__(self) -> None:
        self.definitions: dict[str, AppDefinition] = {}
        self.versions: dict[str, AppVersion] = {}
        self.instances: dict[str, AppInstance] = {}
        self.grants: dict[str, AppGrant] = {}
        self.commits = 0
        self.rollbacks = 0

    # definitions ---------------------------------------------------------------
    async def get_definition(
        self, *, tenant_id: str, definition_id: str
    ) -> AppDefinition | None:
        row = self.definitions.get(definition_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    async def get_definition_by_app_id(
        self, *, tenant_id: str, app_id: str
    ) -> AppDefinition | None:
        for row in self.definitions.values():
            if row.tenant_id == tenant_id and row.app_id == app_id:
                return row
        return None

    async def list_definitions(
        self,
        *,
        tenant_id: str,
        limit: int,
        after_id: str | None = None,
        lifecycle: AppLifecycleStatus | None = None,
    ) -> Sequence[AppDefinition]:
        rows = [
            row
            for row in sorted(self.definitions.values(), key=lambda item: item.id)
            if row.tenant_id == tenant_id
            and (after_id is None or row.id > after_id)
            and (lifecycle is None or row.status is lifecycle)
        ]
        return tuple(rows[:limit])

    async def add_definition(self, definition: AppDefinition) -> None:
        self.definitions[definition.id] = definition

    async def transition_definition(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        expected_status: AppLifecycleStatus,
        status: AppLifecycleStatus,
        current_version_id: str | None,
    ) -> bool:
        row = self.definitions.get(definition_id)
        if row is None or row.tenant_id != tenant_id or row.status is not expected_status:
            return False
        self.definitions[definition_id] = row.model_copy(
            update={
                "status": status,
                "current_version_id": current_version_id,
                "updated_at": now(),
            }
        )
        return True

    # versions ------------------------------------------------------------------
    async def get_version(
        self, *, definition_id: str, version_id: str
    ) -> AppVersion | None:
        row = self.versions.get(version_id)
        return row if row is not None and row.app_definition_id == definition_id else None

    async def get_version_by_manifest_version(
        self, *, definition_id: str, manifest_version: str
    ) -> AppVersion | None:
        for row in self.versions.values():
            if (
                row.app_definition_id == definition_id
                and row.manifest_version == manifest_version
            ):
                return row
        return None

    async def list_versions(
        self, *, definition_id: str, limit: int, after_id: str | None = None
    ) -> Sequence[AppVersion]:
        rows = [
            row
            for row in sorted(self.versions.values(), key=lambda item: item.id)
            if row.app_definition_id == definition_id
            and (after_id is None or row.id > after_id)
        ]
        return tuple(rows[:limit])

    async def add_version(self, version: AppVersion) -> None:
        self.versions[version.id] = version

    async def transition_version(
        self,
        *,
        definition_id: str,
        version_id: str,
        expected_status: AppLifecycleStatus,
        status: AppLifecycleStatus,
        published_at: datetime | None,
    ) -> bool:
        row = self.versions.get(version_id)
        if (
            row is None
            or row.app_definition_id != definition_id
            or row.status is not expected_status
        ):
            return False
        # ``AppVersion`` is a frozen pydantic contract; ``dataclasses.replace`` refuses it.
        self.versions[version_id] = row.model_copy(
            update={"status": status, "published_at": published_at}
        )
        return True

    # grants --------------------------------------------------------------------
    async def get_grant(
        self, *, app_version_id: str, scope_kind: AppScopeKind, scope_id: str
    ) -> AppGrant | None:
        for row in self.grants.values():
            if (
                row.app_version_id == app_version_id
                and row.scope.kind is scope_kind
                and row.scope.id == scope_id
            ):
                return row
        return None

    async def list_grants(
        self, *, definition_id: str, version_id: str
    ) -> Sequence[AppGrant]:
        return tuple(
            row
            for row in self.grants.values()
            if row.app_definition_id == definition_id and row.app_version_id == version_id
        )

    async def add_grant(self, grant: AppGrant) -> None:
        self.grants[grant.id] = grant

    async def replace_grant(self, grant: AppGrant) -> None:
        self.grants[grant.id] = grant

    # instances -----------------------------------------------------------------
    async def get_instance_for_scope(
        self, *, definition_id: str, scope_kind: AppScopeKind, scope_id: str
    ) -> AppInstance | None:
        for row in self.instances.values():
            if (
                row.app_definition_id == definition_id
                and row.scope.kind is scope_kind
                and row.scope.id == scope_id
            ):
                return row
        return None

    async def list_instances(
        self, *, definition_id: str, limit: int, after_id: str | None = None
    ) -> Sequence[AppInstance]:
        rows = [
            row
            for row in sorted(self.instances.values(), key=lambda item: item.id)
            if row.app_definition_id == definition_id
            and (after_id is None or row.id > after_id)
        ]
        return tuple(rows[:limit])

    async def add_instance(self, instance: AppInstance) -> None:
        self.instances[instance.id] = instance

    # transaction ---------------------------------------------------------------
    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeAudit:
    """Stand-in for ``DynamicAppAuditPort``; keeps the idempotency + marker contract."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], AuditRecord] = {}
        self.appended: list[AuditRecord] = []

    async def find(self, *, tenant_id: str, scope_key: str) -> AuditRecord | None:
        return self.records.get((tenant_id, scope_key))

    async def append(self, record: AuditRecord) -> None:
        key = (record.tenant_id, record.scope_key)
        if key in self.records:
            raise AuditConflict("duplicate idempotency scope")
        self.records[key] = record
        self.appended.append(record)

    async def has_marker(
        self, *, tenant_id: str, marker_kind: str, marker_values: Mapping[str, str]
    ) -> bool:
        for record in self.appended:
            if record.tenant_id != tenant_id:
                continue
            if marker_kind == "grant_revoked" and record.markers.get("revoked") != "true":
                continue
            if all(record.markers.get(key) == value for key, value in marker_values.items()):
                return True
        return False


class FakeScopeAuthority:
    """Tenant-owned cabinet/project map; anything not listed is denied."""

    def __init__(self) -> None:
        self.cabinets: dict[tuple[str, str], str] = {
            (TENANT_A, CABINET_A): CABINET_A,
            (TENANT_B, CABINET_B): CABINET_B,
        }
        self.projects: dict[tuple[str, str], str] = {
            (TENANT_A, PROJECT_A): CABINET_A,
        }

    async def require(
        self, *, tenant_id: str, scope_kind: AppScopeKind, scope_id: str
    ) -> str:
        if scope_kind is AppScopeKind.CABINET:
            cabinet = self.cabinets.get((tenant_id, scope_id))
        elif scope_kind is AppScopeKind.PROJECT:
            cabinet = self.projects.get((tenant_id, scope_id))
        else:
            cabinet = None
        if cabinet is None:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="scope not found in this tenant",
            )
        return cabinet


def service() -> tuple[DynamicAppCrudService, FakeRepository, FakeAudit, FakeScopeAuthority]:
    repository = FakeRepository()
    audit = FakeAudit()
    scopes = FakeScopeAuthority()
    return (
        DynamicAppCrudService(repository, audit, scope_authority=scopes),
        repository,
        audit,
        scopes,
    )


__all__ = [
    "ACTOR",
    "CABINET_A",
    "CABINET_B",
    "DIGEST_1",
    "DIGEST_2",
    "FakeAudit",
    "FakeRepository",
    "FakeScopeAuthority",
    "PROJECT_A",
    "TENANT_A",
    "TENANT_B",
    "context",
    "manifest",
    "now",
    "service",
]
