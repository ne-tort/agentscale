"""In-memory фейки всех портов ``application/plugins/admin``.

Фейки намеренно «говорящие»: каждый вызов порта пишется в ``calls`` с явным
``company_id``/``plugin_id``. Это позволяет тестам доказать изоляцию компаний
(критерий №15): uninstall компании A не должен ни разу обратиться к компании B.

Ни один фейк не содержит snapshot/backup/migrate-логики — по §8 / §13.1 дизайна v4
(данные dev эфемерны). Отсутствие таких методов проверяется отдельным тестом.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from prodavan.application.plugins.admin.ports import webhook_secret_key
from prodavan.application.plugins.installation_scope.contracts import InstallationScope
from prodavan.application.plugins.tenantdb.service import StoredSecret
from prodavan.domain.errors import AppError
from prodavan.application.plugins.admin.contracts import (
    PLUGIN_CLAIMS_TTL_SECONDS,
    PLUGIN_OBJECT_PREFIX,
    PLUGIN_SCHEMA_PREFIX,
    TENANT_BUCKET_PREFIX,
    TENANT_DATABASE_PREFIX,
    AdminAuditEvent,
    AdminDecision,
    AdminIdentity,
    ArtifactPinUpdateResult,
    ArtifactPublicationStatus,
    ArtifactRecord,
    CapabilityGrantRecord,
    CapabilitySet,
    GrantStatus,
    IssuedPluginClaims,
    MintRequest,
    ObjectStoreBinding,
    PluginInstanceRecord,
    PluginInstanceStatus,
    PluginPublicationApproval,
    PluginRuntimeBinding,
    PluginRuntimeStatus,
    PluginSchema,
    PluginUpdateLockLease,
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

    async def rotate_plugin_schema(
        self, *, company_id: str, plugin_id: str, dsn_ref: str
    ) -> PluginSchema:
        self._record(
            "tenant_db", "rotate_plugin_schema", company_id=company_id, plugin_id=plugin_id
        )
        schema = await self.ensure_plugin_schema(
            company_id=company_id, plugin_id=plugin_id, dsn_ref=dsn_ref
        )
        return PluginSchema(
            company_id=schema.company_id,
            plugin_id=schema.plugin_id,
            database_name=schema.database_name,
            schema_name=schema.schema_name,
            role_name=schema.role_name,
            dsn_ref=f"{schema.dsn_ref}/rotated",
            search_path=schema.search_path,
            created=False,
        )

    async def revoke_plugin(self, *, company_id: str, plugin_id: str) -> bool:
        self._record("tenant_db", "revoke_plugin", company_id=company_id, plugin_id=plugin_id)
        return True

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


# --------------------------------------------------------------------------- instance store (test-only)


@dataclass
class FakeInstanceStore(RecordingMixin):
    records: dict[tuple[str, str], PluginInstanceRecord] = field(default_factory=dict)

    async def create(self, *, instance: PluginInstanceRecord) -> PluginInstanceRecord:
        self._record(
            "instance_store", "create", company_id=instance.company_id, plugin_id=instance.plugin_id
        )
        key = (instance.company_id, instance.plugin_id)
        existing = self.records.get(key)
        if existing is not None:
            return existing
        self.records[key] = instance
        return instance

    async def get(self, *, company_id: str, plugin_id: str) -> PluginInstanceRecord | None:
        self._record("instance_store", "get", company_id=company_id, plugin_id=plugin_id)
        return self.records.get((company_id, plugin_id))

    async def get_by_id(self, *, plugin_instance_id: str) -> PluginInstanceRecord | None:
        self._record("instance_store", "get_by_id", plugin_instance_id=plugin_instance_id)
        return next(
            (item for item in self.records.values() if item.plugin_instance_id == plugin_instance_id),
            None,
        )

    async def update_status(
        self, *, plugin_instance_id: str, status: PluginInstanceStatus
    ) -> PluginInstanceRecord:
        self._record("instance_store", "update_status", plugin_instance_id=plugin_instance_id)
        for key, item in list(self.records.items()):
            if item.plugin_instance_id == plugin_instance_id:
                updated = replace(item, status=status, updated_at=datetime.now(UTC))
                self.records[key] = updated
                return updated
        raise KeyError(plugin_instance_id)

    async def update_artifact_pin(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
        manifest_version: str,
        artifact_digest: str,
        expected_status: PluginInstanceStatus,
        status: PluginInstanceStatus,
    ) -> PluginInstanceRecord:
        self._record(
            "instance_store",
            "update_artifact_pin",
            company_id=company_id,
            plugin_id=plugin_id,
        )
        key = (company_id, plugin_id)
        current = self.records.get(key)
        if current is None or current.plugin_instance_id != plugin_instance_id:
            raise KeyError(plugin_instance_id)
        if (
            current.manifest_version == manifest_version
            and current.artifact_digest == artifact_digest
            and current.status is status
        ):
            return current
        if (
            current.manifest_version != expected_manifest_version
            or current.artifact_digest != expected_artifact_digest
            or current.status is not expected_status
        ):
            raise RuntimeError("instance old pin fence mismatch")
        updated = replace(
            current,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            status=status,
            updated_at=datetime.now(UTC),
        )
        self.records[key] = updated
        return updated

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        self._record("instance_store", "delete", company_id=company_id, plugin_id=plugin_id)
        self.records.pop((company_id, plugin_id), None)
        return True

    async def resolve(self, *, company_id: str, plugin_id: str) -> str | None:
        found = await self.get(company_id=company_id, plugin_id=plugin_id)
        return found.plugin_instance_id if found is not None else None


# --------------------------------------------------------------------------- publication approvals


@dataclass
class FakePublicationApprovalStore(RecordingMixin):
    records: dict[tuple[str, str, str, str], PluginPublicationApproval] = field(default_factory=dict)

    @staticmethod
    def _key(approval: PluginPublicationApproval) -> tuple[str, str, str, str]:
        return (
            approval.company_id,
            approval.plugin_id,
            approval.manifest_version,
            approval.artifact_digest,
        )

    def seed(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
        capabilities: CapabilitySet,
        consumed_at: datetime | None = None,
    ) -> PluginPublicationApproval:
        approval = PluginPublicationApproval(
            approval_id=f"ppr_test_{len(self.records) + 1}",
            company_id=company_id,
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            capabilities=capabilities.capabilities,
            egress_policy=capabilities.egress_policy,
            quotas=capabilities.quotas,
            created_by="platform-admin",
            created_at=default_now(),
            consumed_at=consumed_at,
        )
        self.records[self._key(approval)] = approval
        return approval

    async def get(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
    ) -> PluginPublicationApproval | None:
        self._record(
            "publication_approvals",
            "get",
            company_id=company_id,
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
        )
        return self.records.get(
            (company_id, plugin_id, manifest_version, artifact_digest)
        )

    async def create(
        self, *, approval: PluginPublicationApproval
    ) -> PluginPublicationApproval:
        self._record("publication_approvals", "create", approval_id=approval.approval_id)
        return self.records.setdefault(self._key(approval), approval)

    async def mark_consumed(
        self, *, approval_id: str, consumed_at: datetime
    ) -> PluginPublicationApproval | None:
        self._record("publication_approvals", "mark_consumed", approval_id=approval_id)
        for key, approval in list(self.records.items()):
            if approval.approval_id == approval_id:
                if approval.consumed_at is None:
                    approval = replace(approval, consumed_at=consumed_at)
                    self.records[key] = approval
                return approval
        return None


# --------------------------------------------------------------------------- webhook secret store


@dataclass
class FakeWebhookSecretStore:
    records: dict[str, StoredSecret] = field(default_factory=dict)
    put_keys: list[str] = field(default_factory=list)
    rotate_keys: list[str] = field(default_factory=list)
    deleted_refs: list[str] = field(default_factory=list)

    async def get(self, *, key: str) -> StoredSecret | None:
        return self.records.get(key)

    async def put(self, *, key: str, value: str) -> str:
        self.put_keys.append(key)
        ref = f"file://ai_keys/{key}.secret"
        self.records[key] = StoredSecret(secret_ref=ref, value=value)
        return ref

    async def rotate(self, *, key: str, value: str) -> str:
        self.rotate_keys.append(key)
        return await self.put(key=key, value=value)

    async def delete(self, *, secret_ref: str) -> None:
        self.deleted_refs.append(secret_ref)
        for key, stored in list(self.records.items()):
            if stored.secret_ref == secret_ref:
                del self.records[key]

    def value_for(self, grant: CapabilityGrantRecord) -> str:
        key = webhook_secret_key(
            company_id=grant.company_id,
            plugin_id=grant.plugin_id,
            plugin_instance_id=grant.plugin_instance_id,
        )
        stored = self.records[key]
        assert grant.webhook_secret_ref == stored.secret_ref
        return stored.value


# --------------------------------------------------------------------------- grant store


@dataclass
class FakeGrantStore(RecordingMixin):
    grants: dict[tuple[str, str], CapabilityGrantRecord] = field(default_factory=dict)
    enabled: dict[str, tuple[str, ...]] = field(default_factory=dict)
    delete_result: bool = True
    deactivate_result_status: GrantStatus = GrantStatus.REVOKED
    instance_store: Any = field(default=None, repr=False, compare=False)
    update_failures: list[Exception] = field(default_factory=list)
    update_committed_failures: list[Exception] = field(default_factory=list)

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
                    webhook_secret_ref=grant.webhook_secret_ref,
                )
                self.grants[key] = updated
                return updated
        raise KeyError(grant_id)

    async def update_artifact_pin(
        self,
        *,
        grant_id: str,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
        manifest_version: str,
        artifact_digest: str,
        expected_instance_status: PluginInstanceStatus,
        instance_status: PluginInstanceStatus,
    ) -> ArtifactPinUpdateResult:
        self._record(
            "grant_store",
            "update_artifact_pin",
            grant_id=grant_id,
            company_id=company_id,
            plugin_id=plugin_id,
            expected_artifact_digest=expected_artifact_digest,
            artifact_digest=artifact_digest,
        )
        if self.update_failures:
            raise self.update_failures.pop(0)
        key = (company_id, plugin_id)
        grant = self.grants.get(key)
        instance = self.instance_store.records.get(key)
        if (
            grant is None
            or instance is None
            or grant.grant_id != grant_id
            or grant.plugin_instance_id != plugin_instance_id
            or instance.plugin_instance_id != plugin_instance_id
            or not grant.is_active
        ):
            raise RuntimeError("atomic artifact CAS binding mismatch")
        if (
            grant.manifest_version == manifest_version
            and grant.artifact_digest == artifact_digest
            and instance.manifest_version == manifest_version
            and instance.artifact_digest == artifact_digest
            and instance.status is instance_status
        ):
            return ArtifactPinUpdateResult(instance=instance, grant=grant)
        if (
            grant.manifest_version != expected_manifest_version
            or grant.artifact_digest != expected_artifact_digest
            or instance.manifest_version != expected_manifest_version
            or instance.artifact_digest != expected_artifact_digest
            or instance.status is not expected_instance_status
        ):
            raise RuntimeError("atomic artifact CAS old pin fence mismatch")
        now = datetime.now(UTC)
        new_grant = replace(
            grant,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
        )
        new_instance = replace(
            instance,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            status=instance_status,
            updated_at=now,
        )
        # Commit both in-memory records only after every fence/post-state check.
        self.grants[key] = new_grant
        self.instance_store.records[key] = new_instance
        result = ArtifactPinUpdateResult(instance=new_instance, grant=new_grant)
        if self.update_committed_failures:
            raise self.update_committed_failures.pop(0)
        return result

    async def regrant(
        self, *, previous_grant_id: str, grant: CapabilityGrantRecord
    ) -> CapabilityGrantRecord:
        self._record("grant_store", "regrant", previous_grant_id=previous_grant_id)
        key = (grant.company_id, grant.plugin_id)
        previous = self.grants.get(key)
        if grant.grant_id == previous_grant_id:
            raise ValueError("regrant requires a fresh grant_id")
        if (
            previous is None
            or previous.grant_id != previous_grant_id
            or previous.is_active
            or previous.revoked_at is None
            or previous.plugin_instance_id != grant.plugin_instance_id
        ):
            raise KeyError(previous_grant_id)
        self.grants[key] = grant
        return grant

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


# --------------------------------------------------------------------------- distributed update lock


@dataclass
class FakeUpdateLock(RecordingMixin):
    """Test-only shared lock model; production uses PostgreSQL advisory locks."""

    held: dict[tuple[str, str, str], PluginUpdateLockLease] = field(default_factory=dict)
    released: list[PluginUpdateLockLease] = field(default_factory=list)
    counter: int = 0

    async def acquire(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
    ) -> PluginUpdateLockLease | None:
        key = (company_id, plugin_id, plugin_instance_id)
        self._record(
            "update_lock",
            "acquire",
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
        )
        if key in self.held:
            return None
        self.counter += 1
        lease = PluginUpdateLockLease(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            expected_manifest_version=expected_manifest_version,
            expected_artifact_digest=expected_artifact_digest,
            owner_token=f"update-owner-{self.counter}",
        )
        self.held[key] = lease
        return lease

    async def release(self, lease: PluginUpdateLockLease) -> None:
        key = (lease.company_id, lease.plugin_id, lease.plugin_instance_id)
        self._record(
            "update_lock",
            "release",
            company_id=lease.company_id,
            plugin_id=lease.plugin_id,
            plugin_instance_id=lease.plugin_instance_id,
        )
        if self.held.get(key) is not lease:
            raise RuntimeError("update lock release owner/binding mismatch")
        del self.held[key]
        self.released.append(lease)


# --------------------------------------------------------------------------- задача 09


@dataclass
class FakeActivation(RecordingMixin):
    deployments: dict[tuple[str, str], str] = field(default_factory=dict)
    deployment_instance_ids: dict[tuple[str, str], str] = field(default_factory=dict)
    statuses: dict[tuple[str, str], PluginRuntimeStatus] = field(default_factory=dict)
    artifact_pins: dict[tuple[str, str], str] = field(default_factory=dict)
    update_owner_pins: dict[tuple[str, str], str | None] = field(default_factory=dict)
    replicas: dict[tuple[str, str], int] = field(default_factory=dict)
    projected_dsn_refs: list[str] = field(default_factory=list)
    update_calls: int = 0
    update_failures: dict[int, Exception] = field(default_factory=dict)
    partial_update_failures: set[int] = field(default_factory=set)
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
        webhook_secret_ref: str,
    ) -> str:
        self._record(
            "activation",
            "ensure_deployment",
            company_id=company_id,
            plugin_id=plugin_id,
            artifact_digest=artifact_digest,
            webhook_secret_ref=webhook_secret_ref,
        )
        name = f"{company_id}-{plugin_id}"
        self.deployments[(company_id, plugin_id)] = name
        self.deployment_instance_ids[(company_id, plugin_id)] = plugin_instance_id
        self.statuses[(company_id, plugin_id)] = PluginRuntimeStatus.IDLE
        self.artifact_pins[(company_id, plugin_id)] = artifact_digest
        self.update_owner_pins.setdefault((company_id, plugin_id), None)
        self.replicas.setdefault((company_id, plugin_id), 0)
        return name

    async def update_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        expected_current_artifact_digest: str,
        update_owner_token: str | None,
        expected_current_update_owner_token: str | None,
        dsn_ref: str,
        webhook_secret_ref: str,
    ) -> str:
        self.update_calls += 1
        self._record(
            "activation",
            "update_deployment",
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=artifact_digest,
            expected_current_artifact_digest=expected_current_artifact_digest,
            update_owner_token=update_owner_token,
            expected_current_update_owner_token=expected_current_update_owner_token,
            dsn_ref=dsn_ref,
            webhook_secret_ref=webhook_secret_ref,
        )
        self.projected_dsn_refs.append(dsn_ref)
        key = (company_id, plugin_id)
        live_digest = self.artifact_pins.get(key)
        live_owner = self.update_owner_pins.get(key)
        if (
            live_digest == artifact_digest
            and live_owner == update_owner_token
        ):
            # Same-owner retry after an uncertain response is read-only.
            pass
        elif (
            live_digest != expected_current_artifact_digest
            or (
                expected_current_update_owner_token is not None
                and live_owner != expected_current_update_owner_token
            )
        ):
            from prodavan.application.plugins.admin.errors import PluginAdminError

            raise PluginAdminError(
                code="PLUGIN_UPDATE_CONFLICT",
                detail="live artifact digest or update owner changed",
                status=409,
                extra={"patch_attempted": False},
            )
        else:
            if self.update_calls in self.partial_update_failures:
                self.artifact_pins[key] = artifact_digest
                self.update_owner_pins[key] = update_owner_token
            failure = self.update_failures.get(self.update_calls)
            if failure is not None:
                raise failure
            self.artifact_pins[key] = artifact_digest
            self.update_owner_pins[key] = update_owner_token
        name = f"{company_id}-{plugin_id}"
        self.deployments[key] = name
        self.deployment_instance_ids[key] = plugin_instance_id
        self.statuses.setdefault(key, PluginRuntimeStatus.IDLE)
        # Update never scales: preserve the exact current desired replica count.
        self.replicas.setdefault(key, 0)
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

    async def runtime_binding(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeBinding:
        self._record("activation", "runtime_binding", company_id=company_id, plugin_id=plugin_id)
        key = (company_id, plugin_id)
        if key not in self.deployments or key not in self.artifact_pins:
            raise RuntimeError("live Deployment binding is absent")
        instance_id = self.deployment_instance_ids[key]
        return PluginRuntimeBinding(
            plugin_instance_id=instance_id,
            artifact_digest=self.artifact_pins[key],
            update_owner_token=self.update_owner_pins.get(key),
        )

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool:
        self._record("activation", "purge_infra", company_id=company_id, plugin_id=plugin_id)
        self.purged.append((company_id, plugin_id))
        self.deployments.pop((company_id, plugin_id), None)
        self.deployment_instance_ids.pop((company_id, plugin_id), None)
        self.statuses.pop((company_id, plugin_id), None)
        self.artifact_pins.pop((company_id, plugin_id), None)
        self.update_owner_pins.pop((company_id, plugin_id), None)
        self.replicas.pop((company_id, plugin_id), None)
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


@dataclass
class FakeInstallationScopeStore:
    scopes: dict[tuple[str, str], InstallationScope] = field(default_factory=dict)
    error: AppError | None = None

    async def ensure_from_source(
        self, *, company_id: str, plugin_id: str, plugin_instance_id: str
    ) -> InstallationScope:
        if self.error is not None:
            raise self.error
        key = (company_id, plugin_instance_id)
        scope = self.scopes.get(key)
        if scope is None:
            scope = InstallationScope(
                company_id=company_id,
                plugin_instance_id=plugin_instance_id,
                cabinet_id="cab_a",
                project_id="pr_a",
            )
            self.scopes[key] = scope
        return scope

    async def get(
        self, *, company_id: str, plugin_instance_id: str
    ) -> InstallationScope | None:
        return self.scopes.get((company_id, plugin_instance_id))


# --------------------------------------------------------------------------- сборка


@dataclass
class Harness:
    tenant_db: FakeTenantDb = field(default_factory=FakeTenantDb)
    object_store: FakeObjectStore = field(default_factory=FakeObjectStore)
    artifact_registry: FakeArtifactRegistry = field(default_factory=FakeArtifactRegistry)
    instance_store: FakeInstanceStore = field(default_factory=FakeInstanceStore)
    grant_store: FakeGrantStore = field(default_factory=FakeGrantStore)
    webhook_secrets: FakeWebhookSecretStore = field(default_factory=FakeWebhookSecretStore)
    publication_approvals: FakePublicationApprovalStore = field(default_factory=FakePublicationApprovalStore)
    revocation: FakeRevocation = field(default_factory=FakeRevocation)
    mint: FakeMint = field(default_factory=FakeMint)
    activation: FakeActivation = field(default_factory=FakeActivation)
    routes: FakeRoutes = field(default_factory=FakeRoutes)
    audit: FakeAudit = field(default_factory=FakeAudit)
    platform_admin: FakePlatformAdmin = field(default_factory=FakePlatformAdmin)
    installation_scopes: FakeInstallationScopeStore = field(default_factory=FakeInstallationScopeStore)
    update_lock: FakeUpdateLock = field(default_factory=FakeUpdateLock)
    #: Один сервис на Harness: генератор id не сбрасывается между вызовами, иначе
    #: два grant в разные компании получили бы одинаковый grant_id в тестах.
    _service_instance: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.grant_store.instance_store = self.instance_store

    def ports(self) -> Any:
        from prodavan.application.plugins.admin.ports import PluginAdminPorts

        return PluginAdminPorts(
            tenant_db=self.tenant_db,
            object_store=self.object_store,
            artifact_registry=self.artifact_registry,
            instance_store=self.instance_store,
            grant_store=self.grant_store,
            webhook_secrets=self.webhook_secrets,
            publication_approvals=self.publication_approvals,
            revocation=self.revocation,
            mint=self.mint,
            activation=self.activation,
            routes=self.routes,
            audit=self.audit,
            platform_admin=self.platform_admin,
            installation_scopes=self.installation_scopes,
            update_lock=self.update_lock,
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
            "instance_store",
            "grant_store",
            "publication_approvals",
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
    "FakeInstallationScopeStore",
    "FakeWebhookSecretStore",
    "FakeInstanceStore",
    "FakeMint",
    "FakeObjectStore",
    "FakePlatformAdmin",
    "FakePublicationApprovalStore",
    "FakeRevocation",
    "FakeRoutes",
    "FakeTenantDb",
    "Harness",
    "PLUGIN_CLAIMS_TTL_SECONDS",
    "AdminDecision",
    "ArtifactPublicationStatus",
    "default_now",
]
