"""In-memory фейки всех портов ``application/plugins/admin``.

Фейки намеренно «говорящие»: каждый вызов порта пишется в ``calls`` с явным
``company_id``/``plugin_id``. Это позволяет тестам доказать изоляцию компаний
(критерий №15): uninstall компании A не должен ни разу обратиться к компании B.

Ни один фейк не содержит snapshot/backup/migrate-логики — по §8 / §13.1 дизайна v4
(данные dev эфемерны). Отсутствие таких методов проверяется отдельным тестом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from prodavan.application.plugins.admin.contracts import (
    PLUGIN_CLAIMS_TTL_SECONDS,
    PLUGIN_OBJECT_PREFIX,
    PLUGIN_SCHEMA_PREFIX,
    TENANT_BUCKET_PREFIX,
    TENANT_DATABASE_PREFIX,
    AdminAuditEvent,
    AdminDecision,
    AdminIdentity,
    ArtifactPublicationStatus,
    ArtifactRecord,
    CapabilityGrantRecord,
    GrantStatus,
    IssuedPluginClaims,
    MintRequest,
    ObjectStoreBinding,
    PluginRuntimeStatus,
    PluginSchema,
    RouteRegistration,
    TenantDatabase,
    WakeResult,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
COMPANY_A = "co_a"
COMPANY_B = "co_b"
PLUGIN_X = "mod_equipment"
PLUGIN_Y = "mod_offers"


@dataclass
class Call:
    port: str
    method: str
    kwargs: dict[str, Any]


@dataclass
class RecordingMixin:
    calls: list[Call] = field(default_factory=list)

    def _record(self, port: str, method: str, **kwargs: Any) -> None:
        self.calls.append(Call(port=port, method=method, kwargs=dict(kwargs)))


# --------------------------------------------------------------------------- задача 05


@dataclass
class FakeTenantDb(RecordingMixin):
    dropped: list[tuple[str, str]] = field(default_factory=list)
    databases: set[str] = field(default_factory=set)
    schemas: dict[tuple[str, str], PluginSchema] = field(default_factory=dict)
    drop_result: tuple[bool, bool] = (True, True)

    async def ensure_company_database(self, *, company_id: str) -> TenantDatabase:
        self._record("tenant_db", "ensure_company_database", company_id=company_id)
        name = f"{TENANT_DATABASE_PREFIX}{company_id}"
        created = name not in self.databases
        self.databases.add(name)
        return TenantDatabase(
            company_id=company_id,
            database_name=name,
            dsn_ref=f"secret://tenant-db/{company_id}",
            created=created,
        )

    async def ensure_plugin_schema(
        self, *, company_id: str, plugin_id: str, dsn_ref: str
    ) -> PluginSchema:
        self._record(
            "tenant_db", "ensure_plugin_schema", company_id=company_id, plugin_id=plugin_id
        )
        key = (company_id, plugin_id)
        schema_name = f"{PLUGIN_SCHEMA_PREFIX}{plugin_id}"
        existing = self.schemas.get(key)
        value = PluginSchema(
            company_id=company_id,
            plugin_id=plugin_id,
            database_name=f"{TENANT_DATABASE_PREFIX}{company_id}",
            schema_name=schema_name,
            role_name=f"{company_id}_{plugin_id}_plugin",
            dsn_ref=dsn_ref,
            search_path=schema_name,
            created=existing is None,
        )
        self.schemas[key] = value
        return value

    async def drop_plugin_schema(self, *, company_id: str, plugin_id: str) -> tuple[bool, bool]:
        self._record("tenant_db", "drop_plugin_schema", company_id=company_id, plugin_id=plugin_id)
        self.dropped.append((company_id, plugin_id))
        self.schemas.pop((company_id, plugin_id), None)
        return self.drop_result


# --------------------------------------------------------------------------- задача 06


@dataclass
class FakeObjectStore(RecordingMixin):
    prefixes: dict[tuple[str, str], str] = field(default_factory=dict)
    dropped_prefixes: list[tuple[str, str]] = field(default_factory=list)
    dropped_buckets: list[str] = field(default_factory=list)
    bucket_empty: bool = True

    async def ensure_bucket(self, *, company_id: str, plugin_id: str) -> ObjectStoreBinding:
        self._record("object_store", "ensure_bucket", company_id=company_id, plugin_id=plugin_id)
        key = (company_id, plugin_id)
        prefix = f"{PLUGIN_OBJECT_PREFIX}{plugin_id}/*"
        created = key not in self.prefixes
        self.prefixes[key] = prefix
        return ObjectStoreBinding(
            company_id=company_id,
            plugin_id=plugin_id,
            bucket=f"{TENANT_BUCKET_PREFIX}{company_id}",
            prefix=prefix,
            created=created,
        )

    async def drop_prefix(self, *, company_id: str, plugin_id: str) -> bool:
        self._record("object_store", "drop_prefix", company_id=company_id, plugin_id=plugin_id)
        self.dropped_prefixes.append((company_id, plugin_id))
        self.prefixes.pop((company_id, plugin_id), None)
        return True

    async def drop_bucket_if_empty(self, *, company_id: str) -> bool:
        self._record("object_store", "drop_bucket_if_empty", company_id=company_id)
        if self.bucket_empty:
            self.dropped_buckets.append(company_id)
            return True
        return False


# --------------------------------------------------------------------------- задача 07


@dataclass
class FakeArtifactRegistry(RecordingMixin):
    records: dict[tuple[str, str, str], ArtifactRecord] = field(default_factory=dict)
    unlinked: list[tuple[str, str, str]] = field(default_factory=list)
    unlink_result: bool = True

    def seed(
        self,
        *,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
        status: ArtifactPublicationStatus,
    ) -> None:
        self.records[(plugin_id, manifest_version, artifact_digest)] = ArtifactRecord(
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            status=status,
        )

    async def get(
        self, *, plugin_id: str, manifest_version: str, artifact_digest: str
    ) -> ArtifactRecord | None:
        self._record(
            "artifact_registry",
            "get",
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
        )
        return self.records.get((plugin_id, manifest_version, artifact_digest))

    async def unlink_installation(
        self, *, company_id: str, plugin_id: str, artifact_digest: str
    ) -> bool:
        self._record(
            "artifact_registry",
            "unlink_installation",
            company_id=company_id,
            plugin_id=plugin_id,
            artifact_digest=artifact_digest,
        )
        self.unlinked.append((company_id, plugin_id, artifact_digest))
        return self.unlink_result


# --------------------------------------------------------------------------- grant store


@dataclass
class FakeGrantStore(RecordingMixin):
    grants: dict[tuple[str, str], CapabilityGrantRecord] = field(default_factory=dict)
    enabled: dict[str, tuple[str, ...]] = field(default_factory=dict)
    delete_result: bool = True
    deactivate_result_status: GrantStatus = GrantStatus.REVOKED

    async def get(self, *, company_id: str, plugin_id: str) -> CapabilityGrantRecord | None:
        self._record("grant_store", "get", company_id=company_id, plugin_id=plugin_id)
        return self.grants.get((company_id, plugin_id))

    async def get_by_grant_id(self, *, grant_id: str) -> CapabilityGrantRecord | None:
        self._record("grant_store", "get_by_grant_id", grant_id=grant_id)
        return next((g for g in self.grants.values() if g.grant_id == grant_id), None)

    async def create(self, *, grant: CapabilityGrantRecord) -> CapabilityGrantRecord:
        self._record("grant_store", "create", grant_id=grant.grant_id)
        self.grants.setdefault((grant.company_id, grant.plugin_id), grant)
        return self.grants[(grant.company_id, grant.plugin_id)]

    async def deactivate(self, *, grant_id: str) -> CapabilityGrantRecord:
        self._record("grant_store", "deactivate", grant_id=grant_id)
        for key, grant in list(self.grants.items()):
            if grant.grant_id == grant_id:
                updated = CapabilityGrantRecord(
                    grant_id=grant.grant_id,
                    company_id=grant.company_id,
                    plugin_id=grant.plugin_id,
                    plugin_instance_id=grant.plugin_instance_id,
                    manifest_version=grant.manifest_version,
                    artifact_digest=grant.artifact_digest,
                    capabilities=grant.capabilities,
                    egress_policy=grant.egress_policy,
                    quotas=grant.quotas,
                    status=self.deactivate_result_status,
                    created_at=grant.created_at,
                    revoked_at=datetime.now(UTC),
                )
                self.grants[key] = updated
                return updated
        raise KeyError(grant_id)

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        self._record("grant_store", "delete", company_id=company_id, plugin_id=plugin_id)
        self.grants.pop((company_id, plugin_id), None)
        return self.delete_result

    async def list_by_company(self, *, company_id: str) -> list[CapabilityGrantRecord]:
        self._record("grant_store", "list_by_company", company_id=company_id)
        return [g for (co, _), g in self.grants.items() if co == company_id]

    async def get_enabled_modules(self, *, company_id: str) -> tuple[str, ...]:
        self._record("grant_store", "get_enabled_modules", company_id=company_id)
        return self.enabled.get(company_id, ())

    async def set_enabled_modules(
        self, *, company_id: str, plugin_ids: Any
    ) -> tuple[str, ...]:
        self._record("grant_store", "set_enabled_modules", company_id=company_id)
        value = tuple(plugin_ids)
        self.enabled[company_id] = value
        return value


# --------------------------------------------------------------------------- задача 02


@dataclass
class FakeRevocation(RecordingMixin):
    revoked: set[str] = field(default_factory=set)
    revoke_result: bool = True

    async def revoke(self, *, grant_id: str, reason: str) -> bool:
        self._record("revocation", "revoke", grant_id=grant_id, reason=reason)
        if self.revoke_result:
            self.revoked.add(grant_id)
        return self.revoke_result

    async def is_revoked(self, *, grant_id: str) -> bool:
        self._record("revocation", "is_revoked", grant_id=grant_id)
        return grant_id in self.revoked


@dataclass
class FakeMint(RecordingMixin):
    issued: list[MintRequest] = field(default_factory=list)
    counter: int = 0

    async def mint(self, *, request: MintRequest) -> IssuedPluginClaims:
        self._record("mint", "mint", grant_id=request.grant_id)
        self.counter += 1
        self.issued.append(request)
        return IssuedPluginClaims(
            token=f"token-{self.counter}",
            grant_id=request.grant_id,
            jti=f"jti-{self.counter}",
            expires_at=datetime.now(UTC) + timedelta(seconds=request.ttl_seconds),
            ttl_seconds=request.ttl_seconds,
        )


# --------------------------------------------------------------------------- задача 09


@dataclass
class FakeActivation(RecordingMixin):
    deployments: dict[tuple[str, str], str] = field(default_factory=dict)
    statuses: dict[tuple[str, str], PluginRuntimeStatus] = field(default_factory=dict)
    wake_ready: bool = True
    purged: list[tuple[str, str]] = field(default_factory=list)
    purge_result: bool = True

    async def ensure_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        dsn_ref: str,
    ) -> str:
        self._record(
            "activation",
            "ensure_deployment",
            company_id=company_id,
            plugin_id=plugin_id,
            artifact_digest=artifact_digest,
        )
        name = f"{company_id}-{plugin_id}"
        self.deployments[(company_id, plugin_id)] = name
        self.statuses[(company_id, plugin_id)] = PluginRuntimeStatus.IDLE
        return name

    async def suspend(self, *, company_id: str, plugin_id: str) -> bool:
        self._record("activation", "suspend", company_id=company_id, plugin_id=plugin_id)
        self.statuses[(company_id, plugin_id)] = PluginRuntimeStatus.SUSPENDED
        return True

    async def wake(self, *, company_id: str, plugin_id: str) -> WakeResult:
        self._record("activation", "wake", company_id=company_id, plugin_id=plugin_id)
        self.statuses[(company_id, plugin_id)] = (
            PluginRuntimeStatus.RUNNING if self.wake_ready else PluginRuntimeStatus.IDLE
        )
        return WakeResult(ready=self.wake_ready, replicas=1 if self.wake_ready else 0, waited_ms=12)

    async def runtime_status(self, *, company_id: str, plugin_id: str) -> PluginRuntimeStatus:
        self._record("activation", "runtime_status", company_id=company_id, plugin_id=plugin_id)
        return self.statuses.get((company_id, plugin_id), PluginRuntimeStatus.ABSENT)

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool:
        self._record("activation", "purge_infra", company_id=company_id, plugin_id=plugin_id)
        self.purged.append((company_id, plugin_id))
        self.deployments.pop((company_id, plugin_id), None)
        self.statuses.pop((company_id, plugin_id), None)
        return self.purge_result


# --------------------------------------------------------------------------- маршруты


@dataclass
class FakeRoutes(RecordingMixin):
    registered: dict[tuple[str, str], RouteRegistration] = field(default_factory=dict)
    unregistered: list[tuple[str, str, str]] = field(default_factory=list)
    unregister_result: bool = True

    async def register(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> RouteRegistration:
        self._record(
            "routes", "register", company_id=company_id, plugin_id=plugin_id, grant_id=grant_id
        )
        value = RouteRegistration(
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant_id,
            webhook_path=f"/webhook/{plugin_id}/{company_id}/*",
            pluginpage_path=f"/pluginpage/{plugin_id}/{company_id}/*",
            capability_path="/capabilities/v1/*",
            egress_rule_count=0,
            deployment=f"{company_id}-{plugin_id}",
        )
        self.registered[(company_id, plugin_id)] = value
        return value

    async def unregister(self, *, company_id: str, plugin_id: str, grant_id: str) -> bool:
        self._record(
            "routes", "unregister", company_id=company_id, plugin_id=plugin_id, grant_id=grant_id
        )
        self.unregistered.append((company_id, plugin_id, grant_id))
        self.registered.pop((company_id, plugin_id), None)
        return self.unregister_result


# --------------------------------------------------------------------------- аудит / auth


@dataclass
class FakeAudit(RecordingMixin):
    events: list[AdminAuditEvent] = field(default_factory=list)
    append_result: bool = True

    async def append(self, *, event: AdminAuditEvent) -> bool:
        self._record("audit", "append", event_id=event.event_id)
        self.events.append(event)
        return self.append_result


@dataclass
class FakePlatformAdmin(RecordingMixin):
    is_admin: bool = True
    actor_id: str = "platform-admin"

    async def require_platform_admin(self, *, authorization: str | None) -> AdminIdentity:
        self._record("platform_admin", "require_platform_admin", authorization=authorization)
        return AdminIdentity(actor_id=self.actor_id, is_platform_admin=self.is_admin)


# --------------------------------------------------------------------------- сборка


@dataclass
class Harness:
    tenant_db: FakeTenantDb = field(default_factory=FakeTenantDb)
    object_store: FakeObjectStore = field(default_factory=FakeObjectStore)
    artifact_registry: FakeArtifactRegistry = field(default_factory=FakeArtifactRegistry)
    grant_store: FakeGrantStore = field(default_factory=FakeGrantStore)
    revocation: FakeRevocation = field(default_factory=FakeRevocation)
    mint: FakeMint = field(default_factory=FakeMint)
    activation: FakeActivation = field(default_factory=FakeActivation)
    routes: FakeRoutes = field(default_factory=FakeRoutes)
    audit: FakeAudit = field(default_factory=FakeAudit)
    platform_admin: FakePlatformAdmin = field(default_factory=FakePlatformAdmin)
    #: Один сервис на Harness: генератор id не сбрасывается между вызовами, иначе
    #: два grant в разные компании получили бы одинаковый grant_id в тестах.
    _service_instance: Any = field(default=None, repr=False, compare=False)

    def ports(self) -> Any:
        from prodavan.application.plugins.admin.ports import PluginAdminPorts

        return PluginAdminPorts(
            tenant_db=self.tenant_db,
            object_store=self.object_store,
            artifact_registry=self.artifact_registry,
            grant_store=self.grant_store,
            revocation=self.revocation,
            mint=self.mint,
            activation=self.activation,
            routes=self.routes,
            audit=self.audit,
            platform_admin=self.platform_admin,
        )

    def service(self) -> Any:
        from prodavan.application.plugins.admin.service import PluginAdminService

        if self._service_instance is None:
            self._service_instance = PluginAdminService(
                self.ports(), id_factory=_sequential_ids()
            )
        return self._service_instance

    @property
    def all_calls(self) -> list[Call]:
        result: list[Call] = []
        for name in (
            "tenant_db",
            "object_store",
            "artifact_registry",
            "grant_store",
            "revocation",
            "mint",
            "activation",
            "routes",
            "audit",
            "platform_admin",
        ):
            result.extend(getattr(self, name).calls)
        return result

    def calls_for_company(self, company_id: str) -> list[Call]:
        return [
            call
            for call in self.all_calls
            if call.kwargs.get("company_id") == company_id
        ]


def _sequential_ids():
    counter = 0

    def factory() -> str:
        nonlocal counter
        counter += 1
        return f"id-{counter}"

    return factory


def default_now() -> datetime:
    return datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


__all__ = [
    "COMPANY_A",
    "COMPANY_B",
    "DIGEST_A",
    "DIGEST_B",
    "PLUGIN_X",
    "PLUGIN_Y",
    "Call",
    "FakeActivation",
    "FakeArtifactRegistry",
    "FakeAudit",
    "FakeGrantStore",
    "FakeMint",
    "FakeObjectStore",
    "FakePlatformAdmin",
    "FakeRevocation",
    "FakeRoutes",
    "FakeTenantDb",
    "Harness",
    "PLUGIN_CLAIMS_TTL_SECONDS",
    "AdminDecision",
    "ArtifactPublicationStatus",
    "default_now",
]
