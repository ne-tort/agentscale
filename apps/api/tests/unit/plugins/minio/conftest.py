"""In-memory fakes для MinIO-адаптеров (БЕЗ MinIO/сети/БД, тесты не запускаются).

Фейки намеренно повторяют форму boto3-ответов, чтобы проверять именно код
адаптеров, а не «удобную» альтернативу: paginator, get/put_bucket_policy,
generate_presigned_url, assume_role, delete_objects. Никаких ``bytes``/payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig

ENDPOINT = "http://agentscale-minio:9000"
PUBLIC_ENDPOINT = "https://plugins.example.com"

ADMIN_KEY = "plugin-object-admin"
ADMIN_SECRET = "plugin-object-admin-secret"
PRESIGN_KEY = "plugin-object-presigner"
PRESIGN_SECRET = "plugin-object-presigner-secret"
ADMIN_ACCESS_REF = "vault://ai_keys/minio/admin-access"
ADMIN_SECRET_REF = "vault://ai_keys/minio/admin-secret"
PRESIGN_ACCESS_REF = "vault://ai_keys/minio/presign-access"
PRESIGN_SECRET_REF = "vault://ai_keys/minio/presign-secret"


class FakeClock:
    """Фиксированное время для детерминированной проверки ``expires_at``."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def make_config(**overrides: Any) -> MinioPluginConfig:
    """Валидная конфигурация с РАЗНЫМИ admin/presigner principal."""
    values: dict[str, Any] = {
        "endpoint": ENDPOINT,
        "admin_access_key": ADMIN_KEY,
        "admin_secret_key": ADMIN_SECRET,
        "presign_access_key": PRESIGN_KEY,
        "presign_secret_key": PRESIGN_SECRET,
        "public_endpoint": None,
        "sts_enabled": False,
        "region": "us-east-1",
    }
    values.update(overrides)
    return MinioPluginConfig(**values)


def make_env(**overrides: Any) -> dict[str, str]:
    """Полный набор env для ``MinioPluginConfig.from_env``."""
    env: dict[str, str] = {
        "PLUGIN_MINIO_ENDPOINT": ENDPOINT,
        "PLUGIN_MINIO_ADMIN_ACCESS_KEY_REF": ADMIN_ACCESS_REF,
        "PLUGIN_MINIO_ADMIN_SECRET_KEY_REF": ADMIN_SECRET_REF,
        "PLUGIN_MINIO_PRESIGN_ACCESS_KEY_REF": PRESIGN_ACCESS_REF,
        "PLUGIN_MINIO_PRESIGN_SECRET_KEY_REF": PRESIGN_SECRET_REF,
        "PLUGIN_MINIO_STS_ENABLED": "false",
        "PLUGIN_MINIO_REGION": "us-east-1",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return env


class FakeSecretStore:
    def __init__(self) -> None:
        self.values = {
            ADMIN_ACCESS_REF: ADMIN_KEY,
            ADMIN_SECRET_REF: ADMIN_SECRET,
            PRESIGN_ACCESS_REF: PRESIGN_KEY,
            PRESIGN_SECRET_REF: PRESIGN_SECRET,
        }
        self.get_calls: list[str] = []

    def get(self, ref: str) -> str:
        self.get_calls.append(ref)
        return self.values[ref]


def make_secret_store() -> FakeSecretStore:
    return FakeSecretStore()


@dataclass
class FakePaginator:
    client: "FakeS3Client"

    def paginate(self, *, Bucket: str, Prefix: str = "", **_: Any) -> list[dict[str, Any]]:
        bucket = self.client.buckets.get(Bucket)
        if bucket is None:
            raise RuntimeError("NoSuchBucket")
        contents = [
            {"Key": key, "Size": size, "ETag": f'"{hash(key) & 0xFFFFFFFF:x}"'}
            for key, size in sorted(bucket["objects"].items())
            if key.startswith(Prefix)
        ]
        # Одна страница: для юнит-тестов достаточно; адаптер всё равно итерирует.
        return [{"Contents": contents}]


@dataclass
class FakeS3Client:
    """Минимальная имитация boto3 S3-клиента, используемого адаптерами."""

    endpoint: str = ENDPOINT
    buckets: dict[str, dict[str, Any]] = field(default_factory=dict)
    presign_calls: list[dict[str, Any]] = field(default_factory=list)
    policy_calls: list[dict[str, Any]] = field(default_factory=list)
    deleted_buckets: list[str] = field(default_factory=list)
    created_buckets: list[str] = field(default_factory=list)
    close_calls: int = 0

    def head_bucket(self, *, Bucket: str) -> dict[str, Any]:
        if Bucket not in self.buckets:
            raise RuntimeError("NoSuchBucket")
        return {}

    def create_bucket(self, *, Bucket: str, **_: Any) -> dict[str, Any]:
        self.created_buckets.append(Bucket)
        self.buckets.setdefault(Bucket, {"objects": {}, "policy": None})
        return {}

    def get_bucket_policy(self, *, Bucket: str) -> dict[str, Any]:
        bucket = self.buckets.get(Bucket)
        if bucket is None or not bucket.get("policy"):
            raise RuntimeError("NoSuchBucketPolicy")
        return {"Policy": bucket["policy"]}

    def put_bucket_policy(self, *, Bucket: str, Policy: str) -> dict[str, Any]:
        if Bucket not in self.buckets:
            raise RuntimeError("NoSuchBucket")
        self.buckets[Bucket]["policy"] = Policy
        self.policy_calls.append({"Bucket": Bucket, "Policy": Policy})
        return {}

    def get_paginator(self, name: str) -> FakePaginator:
        if name != "list_objects_v2":
            raise RuntimeError(f"unexpected paginator: {name}")
        return FakePaginator(self)

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        bucket = self.buckets.get(Bucket)
        if bucket is None:
            raise RuntimeError("NoSuchBucket")
        for obj in Delete.get("Objects", []):
            bucket["objects"].pop(obj["Key"], None)
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        bucket = self.buckets.get(Bucket)
        if bucket is None:
            raise RuntimeError("NoSuchBucket")
        bucket["objects"].pop(Key, None)
        return {}

    def delete_bucket(self, *, Bucket: str) -> dict[str, Any]:
        self.deleted_buckets.append(Bucket)
        self.buckets.pop(Bucket, None)
        return {}

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, Any],
        ExpiresIn: int,
        HttpMethod: str | None = None,
    ) -> str:
        self.presign_calls.append(
            {
                "operation": operation,
                "Params": dict(Params),
                "ExpiresIn": ExpiresIn,
                "HttpMethod": HttpMethod,
            }
        )
        return (
            f"{self.endpoint}/{Params['Bucket']}/{Params['Key']}"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            f"&X-Amz-Credential={PRESIGN_KEY}%2F20260101%2Fus-east-1%2Fs3%2Faws4_request"
            "&X-Amz-Date=20260101T000000Z"
            f"&X-Amz-Expires={ExpiresIn}"
            "&X-Amz-SignedHeaders=host"
            f"&X-Amz-Signature={'ab' * 32}"
        )

    def close(self) -> None:
        self.close_calls += 1


@dataclass
class FakeQuotaAdmin:
    """Рабочая заглушка QuotaAdminPort (реальная — mc/Admin API родителя)."""

    calls: list[tuple[str, int]] = field(default_factory=list)
    fail: bool = False

    async def set_hard_quota(self, bucket: str, quota_bytes: int) -> None:
        if self.fail:
            raise RuntimeError("quota admin down")
        self.calls.append((bucket, quota_bytes))

    async def enable_quota_enforcement(self) -> None:
        return None

    async def probe(self) -> None:
        if self.fail:
            raise RuntimeError("quota admin down")
        return None


@dataclass
class FakeAuditSink:
    """Append-only приёмник: хранит ТОЛЬКО уже redacted проекции."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    async def append(self, event: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("audit sink down")
        self.rows.append(dict(event))


class _FakeExpiration:
    """Имитация ``datetime`` из ответа STS (``Expiration.timestamp()``)."""

    def __init__(self, ts: float) -> None:
        self._ts = ts

    def timestamp(self) -> float:
        return self._ts


@dataclass
class FakeStsClient:
    """Имитация boto3 STS-клиента. НИКОГДА не возвращает admin/root-ключи."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    clock: FakeClock = field(default_factory=FakeClock)
    fail: bool = False
    return_admin_creds: bool = False
    return_without_token: bool = False

    def assume_role(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("STS down")
        if self.return_admin_creds:
            return {
                "Credentials": {
                    "AccessKeyId": ADMIN_KEY,
                    "SecretAccessKey": ADMIN_SECRET,
                    "SessionToken": "",
                    "Expiration": _FakeExpiration(self.clock.time() + int(kwargs.get("DurationSeconds", 900))),
                }
            }
        return {
            "Credentials": {
                "AccessKeyId": "ASIA_PLUGIN_TEMP",
                "SecretAccessKey": "temporary-secret",
                "SessionToken": "" if self.return_without_token else "temporary-session-token",
                "Expiration": _FakeExpiration(self.clock.time() + int(kwargs.get("DurationSeconds", 900))),
            }
        }


__all__ = [
    "ENDPOINT",
    "PUBLIC_ENDPOINT",
    "ADMIN_KEY",
    "ADMIN_SECRET",
    "PRESIGN_KEY",
    "PRESIGN_SECRET",
    "ADMIN_ACCESS_REF",
    "ADMIN_SECRET_REF",
    "PRESIGN_ACCESS_REF",
    "PRESIGN_SECRET_REF",
    "FakeClock",
    "make_config",
    "make_env",
    "FakeSecretStore",
    "make_secret_store",
    "FakePaginator",
    "FakeS3Client",
    "FakeQuotaAdmin",
    "FakeAuditSink",
    "FakeStsClient",
]
