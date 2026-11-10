"""In-memory fakes для портов объектной capability (без MinIO/БД/сети)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prodavan.application.plugins.objects.ports import (
    ObjectInfo,
    ScopedCredentials,
)
from prodavan.application.plugins.objects.service import PluginObjectStore


class FakeClock:
    """Фиксированное время: детерминированная проверка ``expires_at``."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


@dataclass
class FakeAudit:
    events: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    async def record(self, event: dict) -> None:
        if self.fail:
            raise RuntimeError("audit sink down")
        self.events.append(dict(event))

    def for_op(self, operation: str) -> list[dict]:
        return [e for e in self.events if e["operation"] == operation]


@dataclass
class FakeBuckets:
    ensured: list[tuple[str, dict, int]] = field(default_factory=list)
    quotas: list[tuple[str, int]] = field(default_factory=list)
    deleted_buckets: list[str] = field(default_factory=list)
    deleted_prefixes: list[tuple[str, str]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    objects: dict[str, list[ObjectInfo]] = field(default_factory=dict)
    owns_bucket: bool = True
    ensure_error: Exception | None = None
    delete_error: Exception | None = None

    async def ensure_bucket(self, bucket: str, *, policy: dict, quota_bytes: int) -> None:
        if self.ensure_error:
            raise self.ensure_error
        self.ensured.append((bucket, policy, quota_bytes))

    async def delete_bucket(self, bucket: str) -> None:
        if self.delete_error:
            raise self.delete_error
        self.deleted_buckets.append(bucket)

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        self.deleted_prefixes.append((bucket, prefix))
        before = self.objects.get(bucket, [])
        self.objects[bucket] = [o for o in before if not o.key.startswith(prefix)]
        return len(before) - len(self.objects[bucket])

    async def set_quota(self, bucket: str, quota_bytes: int) -> None:
        self.quotas.append((bucket, quota_bytes))

    async def get_usage(self, bucket: str) -> int:
        return self.usage.get(bucket, 0)

    async def list_prefix(self, bucket: str, prefix: str) -> list[ObjectInfo]:
        return [o for o in self.objects.get(bucket, []) if o.key.startswith(prefix)]


@dataclass
class FakeSts:
    calls: list[dict[str, Any]] = field(default_factory=list)
    clock: FakeClock = field(default_factory=FakeClock)
    error: Exception | None = None
    override: ScopedCredentials | None = None
    return_garbage: bool = False

    async def assume_role(
        self,
        *,
        company_id: str,
        plugin_id: str,
        ttl_seconds: int,
        session_policy: dict,
    ) -> ScopedCredentials:
        self.calls.append(
            {
                "company_id": company_id,
                "plugin_id": plugin_id,
                "ttl_seconds": ttl_seconds,
                "session_policy": session_policy,
            }
        )
        if self.error:
            raise self.error
        if self.return_garbage:
            return "not-scoped-credentials"  # type: ignore[return-value]
        if self.override is not None:
            return self.override
        return ScopedCredentials(
            access_key="ASIA_PLUGIN_TEMP",
            secret_key="temporary-secret",
            session_token="temporary-session-token",
            bucket=f"tenant-{company_id}",
            prefix=f"module-{plugin_id}/",
            expires_at=self.clock.time() + ttl_seconds,
            policy=session_policy,
        )


@dataclass
class FakePresigner:
    calls: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None
    empty_url: bool = False

    async def presign_get(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        return self._sign("get", bucket, key, ttl_seconds)

    async def presign_put(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        return self._sign("put", bucket, key, ttl_seconds)

    async def presign_head(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        return self._sign("head", bucket, key, ttl_seconds)

    def _sign(self, method: str, bucket: str, key: str, ttl_seconds: int) -> str:
        self.calls.append(
            {"method": method, "bucket": bucket, "key": key, "ttl_seconds": ttl_seconds}
        )
        if self.error:
            raise self.error
        if self.empty_url:
            return ""
        return (
            f"https://minio:9000/{bucket}/{key}"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=fake-presigner%2F20260101%2Fus-east-1%2Fs3%2Faws4_request"
            "&X-Amz-Date=20260101T000000Z"
            f"&X-Amz-Expires={ttl_seconds}"
            "&X-Amz-SignedHeaders=host"
            f"&X-Amz-Signature={'ab' * 32}"
        )


def build_store(
    *,
    sts: FakeSts | None = None,
    buckets: FakeBuckets | None = None,
    presigner: Any = None,
    audit: FakeAudit | None = None,
    clock: FakeClock | None = None,
    quota_bytes: int = 1024,
) -> tuple[PluginObjectStore, FakeBuckets, FakeSts, Any, FakeAudit, FakeClock]:
    clock = clock or FakeClock()
    buckets = buckets or FakeBuckets()
    sts = sts or FakeSts(clock=clock)
    presigner = presigner or FakePresigner()
    audit = audit or FakeAudit()
    store = PluginObjectStore(
        buckets=buckets,
        sts=sts,
        presigner=presigner,
        audit=audit,
        quota_bytes=quota_bytes,
        clock=clock,
    )
    return store, buckets, sts, presigner, audit, clock


__all__ = [
    "FakeClock",
    "FakeAudit",
    "FakeBuckets",
    "FakeSts",
    "FakePresigner",
    "build_store",
]
