"""Общие фейки «реальных» реализаций для тестов адаптеров (НЕ запускаются).

Фейки повторяют СИГНАТУРЫ соседних зон (прочитаны обе стороны стыка),
а не сигнатуры admin-портов: так тест ловит именно расхождение, которое
должен закрыть адаптер. Ни один фейк не «подсказывает успех»: по умолчанию
результаты пустые/отрицательные, а каждый вызов пишется в ``calls`` для
проверки изоляции ``(company_id, plugin_id)``.

ВАЖНО (verification freeze): тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЛИСЬ — ни pytest,
ни сборки, ни линт, ни компиляция, ни git.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

DIGEST = "sha256:" + "a" * 64
DIGEST_OTHER = "sha256:" + "b" * 64
COMPANY = "co_a"
COMPANY_OTHER = "co_b"
PLUGIN = "mod_equipment"
INSTANCE = "inst_equipment_001"
SECRET = "unit-test-plugin-signing-secret"  # только для тестов, не prod


@dataclass
class Call:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass
class Recorder:
    calls: list[Call] = field(default_factory=list)

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(Call(method=method, args=args, kwargs=dict(kwargs)))


# --------------------------------------------------------------------------- identity


@dataclass
class FakeSyncRevocation(Recorder):
    """Синхронный ``identity.RevocationPort``: revoke(grant_id) + is_active."""

    revoked: set[str] = field(default_factory=set)
    active_override: dict[str, bool] = field(default_factory=dict)

    def revoke(self, grant_id: str) -> None:
        self._record("revoke", grant_id)
        self.revoked.add(grant_id)

    def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        self._record("is_active", grant_id, now=now)
        if grant_id in self.active_override:
            return self.active_override[grant_id]
        return grant_id not in self.revoked

    def is_revoked(self, grant_id: str) -> bool:
        self._record("is_revoked", grant_id)
        return not self.is_active(grant_id)


# --------------------------------------------------------------------------- tenantdb


@dataclass
class FakeAdminConnection(Recorder):
    """``tenantdb.AdminConnectionPort.execute(text, params, *, target)``."""

    existing_databases: set[str] = field(default_factory=set)
    fail: bool = False

    async def execute(
        self,
        text: str,
        params: dict[str, Any] | None = None,
        *,
        target: Any,
    ) -> Any:
        self._record("execute", text, params, target=target)
        if self.fail:
            raise RuntimeError("admin connection down")
        from prodavan.application.plugins.tenantdb.service import ExecuteResult

        name = (params or {}).get("name")
        rows = ((1,),) if name in self.existing_databases else ()
        return ExecuteResult(rows=rows)


@dataclass
class FakeSecretPort(Recorder):
    async def put(self, *, key: str, value: str) -> str:
        self._record("put", key=key, value=value)
        return f"secret_ref://{key}"

    async def rotate(self, *, key: str, value: str) -> str:
        self._record("rotate", key=key, value=value)
        return f"secret_ref://{key}#rotated"

    async def delete(self, *, secret_ref: str) -> None:
        self._record("delete", secret_ref=secret_ref)


@dataclass
class FakeTenantDbAudit(Recorder):
    async def record(
        self, *, event: str, company_id: str, plugin_id: str, payload: dict[str, Any]
    ) -> None:
        self._record("record", event=event, company_id=company_id, plugin_id=plugin_id, payload=payload)


@dataclass
class FakeObjectAudit(Recorder):
    async def record(self, event: dict[str, Any]) -> None:
        self._record("record", event=event)


# --------------------------------------------------------------------------- objects


@dataclass
class FakeBucketAdmin(Recorder):
    """``objects.ports.BucketAdminPort`` (позиционные методы)."""

    objects: dict[str, list[Any]] = field(default_factory=dict)
    deleted_buckets: list[str] = field(default_factory=list)
    deleted_prefixes: list[tuple[str, str]] = field(default_factory=list)
    list_fails: bool = False
    delete_fails: bool = False

    async def ensure_bucket(
        self, bucket: str, *, policy: dict, quota_bytes: int
    ) -> None:
        self._record("ensure_bucket", bucket, policy=policy, quota_bytes=quota_bytes)
        self.objects.setdefault(bucket, [])

    async def delete_bucket(self, bucket: str) -> None:
        self._record("delete_bucket", bucket)
        if self.delete_fails:
            raise RuntimeError("delete_bucket failed")
        self.deleted_buckets.append(bucket)
        self.objects.pop(bucket, None)

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        self._record("delete_prefix", bucket, prefix)
        if self.delete_fails:
            raise RuntimeError("delete_prefix failed")
        self.deleted_prefixes.append((bucket, prefix))
        return 0

    async def set_quota(self, bucket: str, quota_bytes: int) -> None:
        self._record("set_quota", bucket, quota_bytes)

    async def get_usage(self, bucket: str) -> int:
        self._record("get_usage", bucket)
        return 0

    async def list_prefix(self, bucket: str, prefix: str) -> list[Any]:
        self._record("list_prefix", bucket, prefix)
        if self.list_fails:
            raise RuntimeError("list_prefix failed")
        return list(self.objects.get(bucket, []))


# --------------------------------------------------------------------------- artifacts


def make_artifact_record(
    *,
    digest: str = DIGEST,
    plugin_id: str = PLUGIN,
    version: str = "1.2.0",
    published: bool = True,
    applied: bool = True,
    quarantine: bool = False,
    published_at: bool = True,
) -> Any:
    """Собрать настоящий ``PluginArtifactRecord`` (не фейк) для проверки маппинга."""

    from prodavan.application.dynamic_app_registry.contracts import (
        QuarantineEvidence,
        RegistryOutcome,
        RegistryReason,
    )
    from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
    from prodavan.domain.dynamic_apps.types import AppLifecycleStatus

    now = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
    evidence = (
        QuarantineEvidence(
            reason=RegistryReason.INVALID_TRANSITION,
            source_kind="test",
            source_id=f"{plugin_id}@{digest}",
            source_digest=digest,
            observed_at=now,
        )
        if quarantine
        else None
    )
    return PluginArtifactRecord(
        plugin_id=plugin_id,
        version=version,
        digest=digest,
        image_ref=f"ghcr.io/ne-tort/plugins/{plugin_id}@{digest}",
        manifest={"plugin_id": plugin_id, "version": version},
        manifest_fingerprint="f" * 64,
        status=(
            AppLifecycleStatus.PUBLISHED if published else AppLifecycleStatus.REVOKED
        ),
        outcome=(
            RegistryOutcome.APPLIED if applied else RegistryOutcome.QUARANTINED
        ),
        quarantine=evidence,
        published_at=now if published_at else None,
        created_at=now,
    )


# --------------------------------------------------------------------------- activation


@dataclass
class FakeDeploymentAdmin(Recorder):
    """k8s-часть lifecycle (сигнатура ``DeploymentAdminPort``)."""

    deployments: dict[tuple[str, str], str] = field(default_factory=dict)
    statuses: dict[tuple[str, str], Any] = field(default_factory=dict)
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
            "ensure_deployment",
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=artifact_digest,
            dsn_ref=dsn_ref,
        )
        from prodavan.application.plugins.admin.contracts import PluginRuntimeStatus

        name = f"{company_id}-{plugin_id}"
        self.deployments[(company_id, plugin_id)] = name
        self.statuses[(company_id, plugin_id)] = PluginRuntimeStatus.IDLE
        return name

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool:
        self._record("purge_infra", company_id=company_id, plugin_id=plugin_id)
        self.deployments.pop((company_id, plugin_id), None)
        return self.purge_result

    async def runtime_status(self, *, company_id: str, plugin_id: str) -> Any:
        self._record("runtime_status", company_id=company_id, plugin_id=plugin_id)
        from prodavan.application.plugins.admin.contracts import PluginRuntimeStatus

        return self.statuses.get((company_id, plugin_id), PluginRuntimeStatus.ABSENT)


def make_activator_wake_result(*, token: str = "fresh.jwt.token") -> Any:
    """Настоящий ``activation.state.WakeResult`` (не фейк) для маппингов."""

    from prodavan.application.plugins.activation.state import WakeResult

    now = datetime.now(UTC)
    return WakeResult(
        plugin_instance_id=INSTANCE,
        token=token,
        jti="jti-wake-1",
        claims={"sub": f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}"},
        issued_at=now,
        expires_at=now + timedelta(seconds=300),
        ready_at=now,
        waited_seconds=0.012,
        previous_replicas=0,
    )


__all__ = [
    "COMPANY",
    "COMPANY_OTHER",
    "DIGEST",
    "DIGEST_OTHER",
    "INSTANCE",
    "PLUGIN",
    "SECRET",
    "Call",
    "FakeAdminConnection",
    "FakeBucketAdmin",
    "FakeDeploymentAdmin",
    "FakeObjectAudit",
    "FakeSecretPort",
    "FakeSyncRevocation",
    "FakeTenantDbAudit",
    "Recorder",
    "make_activator_wake_result",
    "make_artifact_record",
]
