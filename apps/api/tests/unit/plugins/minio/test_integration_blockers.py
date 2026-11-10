"""Integration-blocker acceptance tests; written, not executed during freeze."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import pytest

from prodavan.application.plugins.capabilities.adapters.build import build_capability_adapters
from prodavan.application.plugins.capabilities.adapters.objects import MinioObjectsAdapter
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    CapabilityName,
    ObjectPermission,
    ObjectPresignMethod,
    ObjectsListRequest,
    ObjectsPresignRequest,
    ObjectsScopedCredentialsRequest,
)
from prodavan.application.plugins.objects import errors as err
from prodavan.application.plugins.objects import policy as iam
from prodavan.application.plugins.wiring import install
from prodavan.config.settings import Settings
from prodavan.infrastructure.plugins.capabilities.kafka import KafkaSecurityConfig
from prodavan.infrastructure.plugins.minio import adapters as minio_adapters
from prodavan.infrastructure.plugins.minio.adapters import (
    MinioBucketAdmin, MinioPresigner, MinioSts,
)
from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig, PluginMinioConfigError
from prodavan.infrastructure.secrets.store import RoutingSecretStore
from tests.unit.plugins.minio.conftest import (
    ADMIN_ACCESS_REF,
    ADMIN_KEY,
    ADMIN_SECRET,
    ADMIN_SECRET_REF,
    ENDPOINT,
    PRESIGN_ACCESS_REF,
    PRESIGN_KEY,
    PRESIGN_SECRET,
    PRESIGN_SECRET_REF,
    FakeClock,
    FakeS3Client,
    FakeStsClient,
    make_config,
    make_env,
)

COMPANY = "co_123"
PLUGIN = "mod_x"
BUCKET = iam.bucket_name(COMPANY)
PREFIX = iam.module_prefix(PLUGIN)
KEY = f"{PREFIX}object.bin"
ARTIFACT_DIGEST = "sha256:" + "ab" * 32


def canonical_presign_query(changes: dict[str, str | None] | None = None) -> str:
    values = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{PRESIGN_KEY}/20260101/us-east-1/s3/aws4_request",
        "X-Amz-Date": "20260101T000000Z",
        "X-Amz-Expires": "600",
        "X-Amz-SignedHeaders": "host",
        "X-Amz-Signature": "ab" * 32,
    }
    for name, value in (changes or {}).items():
        if value is None:
            values.pop(name)
        else:
            values[name] = value
    return urlencode(values)


class RecordingSecretStore:
    def __init__(self) -> None:
        self.values = {
            ADMIN_ACCESS_REF: ADMIN_KEY,
            ADMIN_SECRET_REF: ADMIN_SECRET,
            PRESIGN_ACCESS_REF: PRESIGN_KEY,
            PRESIGN_SECRET_REF: PRESIGN_SECRET,
        }
        self.get_calls: list[str] = []
        self.put_calls: list[tuple[str, str]] = []
        self.close_calls = 0
        self.failure_text: str | None = None

    def get(self, ref: str) -> str:
        self.get_calls.append(ref)
        if self.failure_text is not None:
            raise RuntimeError(self.failure_text)
        if ref not in self.values:
            error = RuntimeError("SECRET_NOT_FOUND")
            error.code = "SECRET_NOT_FOUND"
            raise error
        return self.values[ref]

    def put(self, key: str, value: str) -> str:
        self.put_calls.append((key, value))
        ref = f"vault://ai_keys/{key}"
        self.values[ref] = value
        return ref

    def delete(self, ref: str) -> None:
        self.values.pop(ref, None)

    def close(self) -> None:
        self.close_calls += 1


class ChunkBody:
    def __init__(self, chunks: list[bytes], *, failure_text: str | None = None) -> None:
        self._chunks = chunks
        self._failure_text = failure_text
        self.close_calls = 0
        self.chunk_sizes: list[int] = []

    def iter_chunks(self, *, chunk_size: int) -> Any:
        self.chunk_sizes.append(chunk_size)
        for chunk in self._chunks:
            yield chunk
        if self._failure_text is not None:
            raise RuntimeError(self._failure_text)

    def close(self) -> None:
        self.close_calls += 1


class FakeChecksumS3:
    def __init__(
        self,
        *,
        head: dict[str, Any] | None = None,
        body: ChunkBody | None = None,
        contents: list[dict[str, Any]] | None = None,
        get_content_length: int | None = None,
    ) -> None:
        self.head = head if head is not None else {}
        self.body = body
        self.contents = contents or []
        self.get_content_length = get_content_length
        self.head_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.close_calls = 0

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.head_calls.append(kwargs)
        return dict(self.head)

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        if self.body is None:
            raise RuntimeError("unexpected GET")
        response: dict[str, Any] = {"Body": self.body}
        if self.get_content_length is not None:
            response["ContentLength"] = self.get_content_length
        return response

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["Bucket"] == BUCKET
        assert kwargs["Prefix"] == PREFIX
        return {"Contents": list(self.contents), "NextContinuationToken": None}

    def close(self) -> None:
        self.close_calls += 1


class CloseableStsClient(FakeStsClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class ProbeStsClient:
    """Synchronous boto3-compatible fake; every call passes production to_thread."""

    def __init__(self, clock: FakeClock, behavior: str = "success", delay: float = 0.0) -> None:
        self.clock = clock
        self.behavior = behavior
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.close_calls = 0

    def assume_role(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.delay:
            time.sleep(self.delay)
        if self.behavior == "error":
            raise RuntimeError("backend temporary-secret must not leak")
        if self.behavior == "malformed":
            return {"Credentials": "not-a-mapping"}
        ttl = int(kwargs["DurationSeconds"])
        expiration = self.clock.time() + ttl
        if self.behavior == "expired":
            expiration = self.clock.time() - 1
        elif self.behavior == "too_long":
            expiration = self.clock.time() + ttl + 60
        admin = self.behavior == "admin"
        return {
            "Credentials": {
                "AccessKeyId": ADMIN_KEY if admin else "ASIA_PROBE_TEMP",
                "SecretAccessKey": (
                    ADMIN_SECRET if admin else ("fresh-scoped-secret" if len(self.calls) > 1 else "probe-secret")
                ),
                "SessionToken": (
                    ""
                    if self.behavior == "no_token"
                    else ("fresh-scoped-session-token" if len(self.calls) > 1 else "probe-session-token")
                ),
                "Expiration": datetime.fromtimestamp(expiration, UTC),
            }
        }

    def close(self) -> None:
        self.close_calls += 1


class FakeS3AuthorizationError(RuntimeError):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class FakeCanaryS3:
    """Server-side S3 decisions; credentials are deliberately not stored."""

    def __init__(self, factory: FakeCanaryFactory) -> None:
        self.factory = factory
        self.close_calls = 0

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self.factory.operations.append(("head_bucket", dict(kwargs)))
        if not self.factory.bucket_exists:
            raise FakeS3AuthorizationError(404, "NoSuchBucket")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.factory.operations.append(("head_object", dict(kwargs)))
        exact = kwargs["Key"] == "module-sts-probe/__authorization_canary__"
        behavior = self.factory.exact if exact else self.factory.foreign
        if exact and self.factory.exact == "success":
            return {"ResponseMetadata": {"HTTPStatusCode": 200}, "ContentLength": 0}
        if exact and self.factory.exact == "nonzero":
            return {"ResponseMetadata": {"HTTPStatusCode": 200}, "ContentLength": 1}
        if behavior == "allowed":
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}
        if behavior in {"success", "denied"}:
            raise FakeS3AuthorizationError(403, "AccessDenied")
        if behavior == "not_found":
            raise FakeS3AuthorizationError(404, "NoSuchKey")
        if behavior == "returned_404_access_denied":
            return {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 404}}
        if behavior == "returned_500_access_denied":
            return {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 500}}
        if behavior == "returned_403_wrong_code":
            return {"Error": {"Code": "Forbidden"}, "ResponseMetadata": {"HTTPStatusCode": 403}}
        if behavior == "returned_403_access_denied":
            return {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}}
        if behavior == "status_200_access_denied":
            return {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 200}}
        if behavior == "status_403_numeric":
            raise FakeS3AuthorizationError(403, "403")
        if behavior == "status_404_access_denied":
            raise FakeS3AuthorizationError(404, "AccessDenied")
        if behavior == "status_500_access_denied":
            raise FakeS3AuthorizationError(500, "AccessDenied")
        if behavior == "wrong_code":
            raise FakeS3AuthorizationError(403, "Forbidden")
        if behavior == "ambiguous":
            return None  # type: ignore[return-value]
        raise FakeS3AuthorizationError(500, "InternalError")

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.factory.operations.append(("list_objects_v2", dict(kwargs)))
        behavior = self.factory.list_bucket
        if behavior == "allowed":
            return {"Contents": [], "ResponseMetadata": {"HTTPStatusCode": 200}}
        if behavior == "denied":
            raise FakeS3AuthorizationError(403, "AccessDenied")
        if behavior == "not_found":
            raise FakeS3AuthorizationError(404, "NotFound")
        if behavior == "returned_404_access_denied":
            return {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 404}}
        if behavior == "returned_500_access_denied":
            return {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 500}}
        if behavior == "returned_403_wrong_code":
            return {"Error": {"Code": "Forbidden"}, "ResponseMetadata": {"HTTPStatusCode": 403}}
        if behavior == "returned_403_access_denied":
            return {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}}
        if behavior == "status_200_access_denied":
            return {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 200}}
        if behavior == "status_403_numeric":
            raise FakeS3AuthorizationError(403, "403")
        if behavior == "status_404_access_denied":
            raise FakeS3AuthorizationError(404, "AccessDenied")
        if behavior == "status_500_access_denied":
            raise FakeS3AuthorizationError(500, "AccessDenied")
        if behavior == "wrong_code":
            raise FakeS3AuthorizationError(403, "Forbidden")
        if behavior == "ambiguous":
            return None  # type: ignore[return-value]
        raise FakeS3AuthorizationError(500, "InternalError")

    def close(self) -> None:
        self.close_calls += 1


class FakeCanaryFactory:
    def __init__(
        self,
        *,
        bucket_exists: bool = True,
        exact: str = "success",
        foreign: str = "denied",
        list_bucket: str = "denied",
    ) -> None:
        self.bucket_exists = bucket_exists
        self.exact = exact
        self.foreign = foreign
        self.list_bucket = list_bucket
        self.factory_calls = 0
        self.operations: list[tuple[str, dict[str, Any]]] = []
        self.clients: list[FakeCanaryS3] = []

    def __call__(self, *, credentials: Any, timeout_seconds: int) -> FakeCanaryS3:
        assert credentials.access_key not in {ADMIN_KEY, "agentscale", "agentscale-api", "root"}
        assert credentials.secret_key != ADMIN_SECRET
        assert credentials.access_key and credentials.secret_key and credentials.session_token
        assert timeout_seconds >= 1
        self.factory_calls += 1
        client = FakeCanaryS3(self)
        self.clients.append(client)
        return client


def build_probe_sts(
    config: MinioPluginConfig,
    clock: FakeClock,
    *,
    sts_client: Any = None,
    canary: FakeCanaryFactory | None = None,
) -> tuple[MinioSts, Any, FakeCanaryFactory]:
    client = sts_client or ProbeStsClient(clock)
    canary_factory = canary or FakeCanaryFactory()
    sts = MinioSts(
        config=config,
        sts_client=client,
        canary_client_factory=canary_factory,
        clock=clock,
    )
    return sts, client, canary_factory


async def build_wired_sts(
    config: MinioPluginConfig,
    clock: FakeClock,
    *,
    sts_client: Any = None,
) -> tuple[MinioSts, Any, FakeCanaryFactory]:
    if not config.sts_probe_enabled:
        config = replace(config, sts_probe_enabled=True)
    sts, client, canary = build_probe_sts(config, clock, sts_client=sts_client)
    assert await sts.probe() is True
    return sts, client, canary


def envelope() -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req-checksum",
        company_id=COMPANY,
        project_id="proj_1",
        subject_id=f"plugin:{PLUGIN}:{COMPANY}:inst_abc",
        plugin_instance_id="inst_abc",
        artifact_digest=ARTIFACT_DIGEST,
        grant_id="grant_01",
    )


def scoped_request() -> ObjectsScopedCredentialsRequest:
    return ObjectsScopedCredentialsRequest(
        bucket=BUCKET,
        key_prefix=PREFIX,
        permissions=frozenset({ObjectPermission.READ}),
        ttl_seconds=900,
        envelope=envelope(),
    )


def presign_request(
    *,
    method: ObjectPresignMethod = ObjectPresignMethod.GET,
    key: str = KEY,
    bucket: str = BUCKET,
    ttl_seconds: int = 600,
    request_envelope: CapabilityEnvelope | None = None,
) -> ObjectsPresignRequest:
    return ObjectsPresignRequest(
        bucket=bucket,
        object_key=key,
        method=method,
        ttl_seconds=ttl_seconds,
        envelope=request_envelope or envelope(),
    )


def build_presign_adapter(
    *, sts_enabled: bool = False, include_presigner: bool = True,
) -> tuple[MinioObjectsAdapter, FakeS3Client, FakeStsClient, RecordingSecretStore, FakeClock]:
    clock = FakeClock()
    sign_client = FakeS3Client()
    sts_client = FakeStsClient(clock=clock)
    config = make_config(sts_enabled=sts_enabled)
    store = RecordingSecretStore()
    presigner = (
        MinioPresigner(config=config, client=sign_client)
        if include_presigner else None
    )
    adapter = MinioObjectsAdapter(
        buckets=MinioBucketAdmin(config=config, client=FakeChecksumS3()),
        sts=MinioSts(config=config, sts_client=sts_client, clock=clock),
        presigner=presigner,
        secret_store=store,
        clock=clock,
    )
    return adapter, sign_client, sts_client, store, clock


class StaticPresigner:
    def __init__(self, url: str) -> None:
        self.url = url

    async def presign_get(self, **kwargs: Any) -> str:
        return self.url


async def assert_presign_url_rejected(
    url: str, code: str, *, sensitive_value: str = ADMIN_SECRET,
) -> None:
    adapter, sign_client, _, _, _ = build_presign_adapter()
    adapter._presigner = StaticPresigner(url)
    with pytest.raises(Exception, match=code) as caught:
        await adapter.presign(presign_request())
    assert sensitive_value not in str(caught.value)
    assert sign_client.presign_calls == []


def test_exact_secret_ref_resolution_and_plaintext_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RecordingSecretStore()
    env = make_env(
        PLUGIN_MINIO_STS_ENABLED="true",
        PLUGIN_MINIO_STS_ENDPOINT="https://sts.internal:9000",
        PLUGIN_MINIO_STS_SESSION_DURATION_SECONDS="900",
        PLUGIN_MINIO_CHECKSUM_MAX_BYTES="4096",
    )
    config = MinioPluginConfig.from_secret_refs(secret_store=store, env=env)

    assert store.get_calls == [
        ADMIN_ACCESS_REF,
        ADMIN_SECRET_REF,
        PRESIGN_ACCESS_REF,
        PRESIGN_SECRET_REF,
    ]
    assert config.sts_enabled is True
    assert config.sts_endpoint_url == "https://sts.internal:9000"
    assert config.sts_session_duration_seconds == 900
    assert config.checksum_max_bytes == 4096
    assert ADMIN_SECRET not in repr(config)
    assert PRESIGN_SECRET not in repr(config)

    calls_before = list(store.get_calls)
    plaintext = "plaintext-secret-must-not-leak"
    env["PLUGIN_MINIO_ADMIN_SECRET_KEY"] = plaintext
    with pytest.raises(PluginMinioConfigError) as caught:
        MinioPluginConfig.from_secret_refs(secret_store=store, env=env)
    assert store.get_calls == calls_before
    assert plaintext not in str(caught.value)
    assert plaintext not in caplog.text

    failing_store = RecordingSecretStore()
    failing_store.failure_text = plaintext
    with pytest.raises(PluginMinioConfigError) as resolution_error:
        MinioPluginConfig.from_secret_refs(secret_store=failing_store, env=make_env())
    assert plaintext not in str(resolution_error.value)
    assert plaintext not in caplog.text


def test_typed_settings_use_the_same_secret_refs_without_raw_env_fallback() -> None:
    typed = Settings(
        _env_file=None,
        plugin_platform_enabled=False,
        plugin_minio_endpoint=ENDPOINT,
        plugin_minio_admin_access_key_ref=ADMIN_ACCESS_REF,
        plugin_minio_admin_secret_key_ref=ADMIN_SECRET_REF,
        plugin_minio_presign_access_key_ref=PRESIGN_ACCESS_REF,
        plugin_minio_presign_secret_key_ref=PRESIGN_SECRET_REF,
        plugin_minio_sts_enabled=True,
        plugin_minio_sts_probe_enabled=True,
        plugin_minio_sts_probe_timeout_seconds=7,
        plugin_minio_sts_probe_revalidate_seconds=60,
        plugin_minio_sts_probe_retry_seconds=5,
        plugin_minio_sts_probe_bucket="tenant-sts-probe",
        plugin_minio_sts_probe_key="module-sts-probe/__authorization_canary__",
        plugin_minio_sts_session_duration_seconds=900,
        plugin_minio_checksum_max_bytes=2048,
    )
    store = RecordingSecretStore()
    config = MinioPluginConfig.from_secret_refs(secret_store=store, settings=typed)
    assert config.endpoint == ENDPOINT
    assert config.sts_enabled is True
    assert config.sts_probe_enabled is True
    assert config.sts_probe_timeout_seconds == 7
    assert config.sts_probe_revalidate_seconds == 60
    assert config.sts_probe_retry_seconds == 5
    assert config.sts_probe_bucket == "tenant-sts-probe"
    assert config.sts_probe_key == "module-sts-probe/__authorization_canary__"
    assert config.sts_session_duration_seconds == 900
    assert config.checksum_max_bytes == 2048
    assert store.get_calls == [
        ADMIN_ACCESS_REF,
        ADMIN_SECRET_REF,
        PRESIGN_ACCESS_REF,
        PRESIGN_SECRET_REF,
    ]


def test_kafka_security_resolves_exact_refs_from_the_shared_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    import ssl

    username_ref = "vault://ai_keys/plugin-kafka/username"
    password_ref = "vault://ai_keys/plugin-kafka/password"
    store = RecordingSecretStore()
    store.values[username_ref] = "plugin-kafka-user"
    store.values[password_ref] = "plugin-kafka-password"
    ca_file = tmp_path / "plugin-kafka-ca.pem"
    ca_file.write_text("test-only", encoding="utf-8")
    context = ssl.create_default_context()
    monkeypatch.setattr(
        "prodavan.infrastructure.plugins.capabilities.kafka.ssl.create_default_context",
        lambda cafile=None: context,
    )
    security = KafkaSecurityConfig.from_secret_refs(
        secret_store=store,
        env={
            "PLUGIN_KAFKA_SASL_USERNAME_REF": username_ref,
            "PLUGIN_KAFKA_SASL_PASSWORD_REF": password_ref,
            "PLUGIN_KAFKA_TLS_CA_FILE": str(ca_file),
        },
    )
    assert store.get_calls == [username_ref, password_ref]
    assert security.sasl_username == "plugin-kafka-user"
    assert "plugin-kafka-password" not in repr(security)


def test_install_constructs_one_real_routing_secret_store_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prodavan.infrastructure.secrets.store as store_module

    backend = RecordingSecretStore()
    monkeypatch.setattr(store_module.settings, "vault_addr", None)
    real = RoutingSecretStore(file_store=backend)  # type: ignore[arg-type]
    factory_calls = 0

    def factory() -> RoutingSecretStore:
        nonlocal factory_calls
        factory_calls += 1
        return real

    monkeypatch.setattr(store_module, "get_secret_store", factory)
    overrides: dict[str, Any] = {}
    overrides["secret_store"] = install._shared_secret_store(overrides)
    assert isinstance(overrides["secret_store"], RoutingSecretStore)
    assert install._shared_secret_store(overrides) is real
    assert factory_calls == 1


def test_sts_probe_default_target_config_is_canonical() -> None:
    config = make_config()
    assert config.sts_probe_bucket == "tenant-sts-probe"
    assert config.sts_probe_key == "module-sts-probe/__authorization_canary__"


def test_sts_probe_custom_valid_target_config_constructs() -> None:
    config = make_config(
        sts_probe_bucket="tenant-sts-canary",
        sts_probe_key="module-sts-canary-probe/__fixed.bin",
    )
    assert config.sts_probe_bucket == "tenant-sts-canary"
    assert config.sts_probe_key == "module-sts-canary-probe/__fixed.bin"
    assert MinioSts(config=config)._probe_identity() == (
        "sts-canary",
        "sts-canary-probe",
        "tenant-sts-canary",
        "module-sts-canary-probe/__fixed.bin",
        "module-sts-canary-probe/",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"sts_probe_bucket": "agentscale"},
        {"sts_probe_bucket": "tenant-INVALID"},
        {"sts_probe_key": "../secret"},
        {"sts_probe_key": "module-sts-probe/"},
        {"sts_probe_key": "module-sts-probe/*"},
        {"sts_probe_key": "module-sts-probe/nested/fixed"},
        {"sts_probe_key": "module-sts-probe//fixed"},
        {"sts_probe_key": "module-sts-probe/../fixed"},
        {"sts_probe_key": "module-/fixed"},
        {"sts_probe_key": "modulests-probe/fixed"},
        {"sts_probe_key": "module-sts-probe/fixed\\evil"},
        {"sts_probe_key": "plain.txt"},
    ],
)
def test_sts_probe_invalid_target_config_is_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(Exception):
        make_config(**overrides)


def sts_probe_config(**overrides: Any) -> MinioPluginConfig:
    return make_config(sts_enabled=True, sts_probe_enabled=True, **overrides)


def caller_role_kwargs() -> dict[str, Any]:
    return {
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "ttl_seconds": 900,
        "session_policy": iam.session_policy(COMPANY, PLUGIN),
    }


def test_sts_client_keeps_standard_model_validation() -> None:
    sts = MinioSts(config=make_config())
    client = sts._build_sts_client(timeout_seconds=1)
    try:
        from botocore.exceptions import ParamValidationError
        from botocore.validate import ParamValidationDecorator

        assert isinstance(client._serializer, ParamValidationDecorator)
        operation = client.meta.service_model.operation_model("AssumeRole")
        request = client._serializer.serialize_to_request(
            {
                "RoleArn": "arn:aws:iam::000000000000:role/plugin-object-access",
                "RoleSessionName": "plugin-mod_x-co_123",
                "DurationSeconds": 900,
                "Policy": "{}",
            },
            operation,
        )
        assert request["body"]["DurationSeconds"] == 900
        with pytest.raises(ParamValidationError):
            client._serializer.serialize_to_request(
                {
                    "RoleArn": "arn:aws:iam::000000000000:role/plugin-object-access",
                    "RoleSessionName": "plugin-mod_x-co_123",
                    "DurationSeconds": 60,
                    "Policy": "{}",
                },
                operation,
            )
        assert client.meta.config.connect_timeout == 1
        assert client.meta.config.read_timeout == 1
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl_seconds", [900, 3600])
async def test_sts_standard_durations_keep_exact_assume_role_shape(ttl_seconds: int) -> None:
    clock = FakeClock()
    client = FakeStsClient(clock=clock)
    sts, client, _canary = await build_wired_sts(
        sts_probe_config(sts_session_duration_seconds=3600), clock, sts_client=client,
    )
    policy = iam.session_policy(COMPANY, PLUGIN)
    issued = await sts.assume_role(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        ttl_seconds=ttl_seconds,
        session_policy=policy,
    )
    assert issued.expires_at == clock.time() + ttl_seconds
    call = client.calls[-1]
    assert set(call) == {"RoleArn", "RoleSessionName", "DurationSeconds", "Policy"}
    assert call["RoleArn"] == sts.config.sts_role_arn
    assert call["RoleSessionName"] == f"plugin-{PLUGIN}-{COMPANY}"
    assert call["DurationSeconds"] == ttl_seconds
    assert json.loads(call["Policy"]) == policy


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl_seconds", [0, 59, 60, 899, 3601, "900", True])
async def test_sts_duration_typed_validation_rejects_invalid_values_before_io(ttl_seconds: Any) -> None:
    clock = FakeClock()
    client = FakeStsClient(clock=clock)
    sts, client, _canary = await build_wired_sts(sts_probe_config(), clock, sts_client=client)
    with pytest.raises(err.InvalidTtl):
        await sts.assume_role(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            ttl_seconds=ttl_seconds,
            session_policy=iam.session_policy(COMPANY, PLUGIN),
        )
    assert len(client.calls) == 1  # probe only; no target request


@pytest.mark.parametrize(
    "role_arn",
    [None, "", "arn:aws:iam::000000000000:role/other", "arn:aws:iam::000000000000:role/plugin-object-access/extra"],
)
def test_sts_role_arn_typed_guard_rejects_invalid_values(role_arn: Any) -> None:
    with pytest.raises(err.StsUnavailable, match="RoleArn"):
        minio_adapters._validate_sts_role_arn(role_arn)


@pytest.mark.parametrize("session_name", [None, "", "x", "has space", "plugin/mod_x", "a" * 65, "plugin-é"])
def test_sts_role_session_name_typed_guard_rejects_invalid_values(session_name: Any) -> None:
    with pytest.raises(err.StsUnavailable, match="RoleSessionName"):
        minio_adapters._validate_sts_role_session_name(session_name)


@pytest.mark.asyncio
async def test_sts_invalid_policy_still_rejected_before_target_request() -> None:
    clock = FakeClock()
    client = FakeStsClient(clock=clock)
    sts, client, _canary = await build_wired_sts(sts_probe_config(), clock, sts_client=client)
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            ttl_seconds=900,
            session_policy={"Version": iam.POLICY_VERSION, "Statement": []},
        )
    assert len(client.calls) == 1  # probe only; no target request


@pytest.mark.asyncio
async def test_sts_probe_success_enables_only_after_discarded_scoped_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    sts, client, _canary = build_probe_sts(sts_probe_config(), clock, sts_client=client)

    assert sts.scoped_credentials_wired is False
    with pytest.raises(err.StsUnavailable, match="fresh successful post-probe"):
        await sts.assume_role(**caller_role_kwargs())
    assert client.calls == []

    assert await sts.probe() is True
    assert sts.scoped_credentials_wired is True
    assert len(client.calls) == 1
    probe_call = client.calls[0]
    assert probe_call["DurationSeconds"] == 900
    assert set(probe_call) == {"RoleArn", "RoleSessionName", "DurationSeconds", "Policy"}
    probe_policy = json.loads(probe_call["Policy"])
    assert probe_policy == sts._probe_session_policy("sts-probe", "sts-probe")
    list_statement, get_statement = probe_policy["Statement"]
    assert list_statement["Action"] == ["s3:ListBucket"]
    assert list_statement["Resource"] == ["arn:aws:s3:::tenant-sts-probe"]
    assert "Condition" not in list_statement
    assert get_statement["Action"] == ["s3:GetObject"]
    assert get_statement["Resource"] == [
        "arn:aws:s3:::tenant-sts-probe/module-sts-probe/__authorization_canary__"
    ]
    assert len(probe_policy["Statement"]) == 2
    assert [name for name, _kwargs in _canary.operations] == [
        "head_bucket", "head_object", "head_object", "list_objects_v2",
    ]
    assert _canary.operations[0][1]["Bucket"] == "tenant-sts-probe"
    assert _canary.operations[1][1]["Key"] == "module-sts-probe/__authorization_canary__"
    assert _canary.operations[3][1]["Bucket"] == "tenant-sts-probe-foreign"
    assert not any(name.startswith(("put", "delete")) for name, _kwargs in _canary.operations)
    assert all(client.close_calls == 1 for client in _canary.clients)

    store = RecordingSecretStore()
    adapter = MinioObjectsAdapter(
        buckets=MinioBucketAdmin(config=make_config(), client=FakeChecksumS3()),
        sts=sts,
        secret_store=store,
        clock=clock,
    )
    response = await adapter.scoped_credentials(scoped_request())
    assert response.credentials is not None
    assert response.credentials.bucket == BUCKET
    assert response.credentials.key_prefix == PREFIX
    assert len(store.put_calls) == 1
    assert "probe-secret" not in store.put_calls[0][1]
    assert "probe-session-token" not in store.put_calls[0][1]
    assert "fresh-scoped-secret" in store.put_calls[0][1]
    assert "fresh-scoped-session-token" in store.put_calls[0][1]
    assert len(client.calls) == 2
    assert client.calls[1]["DurationSeconds"] == 900
    assert client.calls[1] != probe_call
    tenant_policy = json.loads(client.calls[1]["Policy"])
    assert "tenant-sts-probe" not in json.dumps(tenant_policy)
    for statement in tenant_policy["Statement"]:
        if "s3:ListBucket" in statement["Action"]:
            assert statement["Condition"]["StringLike"]["s3:prefix"] == ["module-mod_x/*"]
    assert "probe-secret" not in repr(sts)
    assert "probe-session-token" not in repr(sts)
    assert "probe-secret" not in caplog.text
    assert "probe-session-token" not in caplog.text
    assert not hasattr(sts, "_probe_credentials")


@pytest.mark.asyncio
async def test_sts_probe_rejects_missing_bucket_not_as_authorization() -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    canary = FakeCanaryFactory(bucket_exists=False)
    sts, client, canary = build_probe_sts(
        sts_probe_config(), clock, sts_client=client, canary=canary,
    )
    assert await sts.probe() is False
    assert sts.scoped_credentials_wired is False
    assert [name for name, _kwargs in canary.operations] == ["head_bucket"]
    assert all(client.close_calls == 1 for client in canary.clients)


@pytest.mark.asyncio
async def test_sts_probe_rejects_nosuchkey_after_existing_bucket_without_false_success() -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    canary = FakeCanaryFactory(exact="not_found")
    sts, client, canary = build_probe_sts(
        sts_probe_config(), clock, sts_client=client, canary=canary,
    )
    # HeadBucket=200 is proven, but NoSuchKey means the required fixed object is
    # absent. It is not NoSuchBucket and still cannot produce availability.
    assert await sts.probe() is False
    assert sts.scoped_credentials_wired is False
    assert [name for name, _kwargs in canary.operations] == ["head_bucket", "head_object"]
    assert canary.operations[0][1]["Bucket"] == "tenant-sts-probe"
    assert canary.operations[1][1]["Key"] == "module-sts-probe/__authorization_canary__"
    assert all(client.close_calls == 1 for client in canary.clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["error", "malformed", "admin", "no_token", "expired", "too_long"])
async def test_sts_probe_failure_or_invalid_response_stays_unwired_without_leaks(
    behavior: str, caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock, behavior=behavior)
    sts, client, _canary = build_probe_sts(sts_probe_config(), clock, sts_client=client)

    assert await sts.probe() is False
    assert sts.scoped_credentials_wired is False
    assert len(client.calls) == 1
    assert await sts.probe() is False  # retry backoff: no second target call
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(**caller_role_kwargs())
    assert len(client.calls) == 1
    assert "probe-secret" not in repr(sts)
    assert "probe-session-token" not in repr(sts)
    assert "probe-secret" not in caplog.text
    assert "backend temporary-secret must not leak" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,behavior,expected_operations",
    [
        (
            "foreign",
            "server_error",
            ["head_bucket", "head_object", "head_object"],
        ),
        (
            "foreign",
            "status_200_access_denied",
            ["head_bucket", "head_object", "head_object"],
        ),
        (
            "foreign",
            "status_404_access_denied",
            ["head_bucket", "head_object", "head_object"],
        ),
        (
            "foreign",
            "status_500_access_denied",
            ["head_bucket", "head_object", "head_object"],
        ),
        (
            "foreign",
            "wrong_code",
            ["head_bucket", "head_object", "head_object"],
        ),
        (
            "list_bucket",
            "server_error",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
        (
            "list_bucket",
            "status_200_access_denied",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
        (
            "list_bucket",
            "status_404_access_denied",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
        (
            "list_bucket",
            "status_500_access_denied",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
        (
            "list_bucket",
            "wrong_code",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
    ],
)
async def test_sts_canary_denial_requires_exact_403_and_access_denied_code(
    field: str, behavior: str, expected_operations: list[str],
) -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    canary = FakeCanaryFactory(**{field: behavior})
    sts, client, canary = build_probe_sts(
        sts_probe_config(), clock, sts_client=client, canary=canary,
    )
    assert await sts.probe() is False
    assert sts.scoped_credentials_wired is False
    assert len(client.calls) == 1
    assert canary.factory_calls == 1
    assert [name for name, _kwargs in canary.operations] == expected_operations
    assert all(client.close_calls == 1 for client in canary.clients)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,behavior,expected_operations",
    [
        ("foreign", "returned_404_access_denied", ["head_bucket", "head_object", "head_object"]),
        ("foreign", "returned_500_access_denied", ["head_bucket", "head_object", "head_object"]),
        ("foreign", "status_200_access_denied", ["head_bucket", "head_object", "head_object"]),
        ("foreign", "returned_403_wrong_code", ["head_bucket", "head_object", "head_object"]),
        ("foreign", "ambiguous", ["head_bucket", "head_object", "head_object"]),
        (
            "list_bucket",
            "returned_404_access_denied",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
        (
            "list_bucket",
            "returned_500_access_denied",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
        (
            "list_bucket",
            "status_200_access_denied",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
        (
            "list_bucket",
            "returned_403_wrong_code",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
        (
            "list_bucket",
            "ambiguous",
            ["head_bucket", "head_object", "head_object", "list_objects_v2"],
        ),
    ],
)
async def test_sts_canary_returned_mapping_mismatch_and_ambiguous_fail_closed(
    field: str, behavior: str, expected_operations: list[str],
) -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    canary = FakeCanaryFactory(**{field: behavior})
    sts, client, canary = build_probe_sts(
        sts_probe_config(), clock, sts_client=client, canary=canary,
    )
    assert await sts.probe() is False
    assert sts.scoped_credentials_wired is False
    assert len(client.calls) == 1
    assert canary.factory_calls == 1
    assert [name for name, _kwargs in canary.operations] == expected_operations
    assert all(client.close_calls == 1 for client in canary.clients)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,expected_operations",
    [
        ("foreign", ["head_bucket", "head_object", "head_object", "list_objects_v2"]),
        ("list_bucket", ["head_bucket", "head_object", "head_object", "list_objects_v2"]),
    ],
)
async def test_sts_canary_returned_exact_403_access_denied_is_success(
    field: str, expected_operations: list[str],
) -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    canary = FakeCanaryFactory(**{field: "returned_403_access_denied"})
    sts, client, canary = build_probe_sts(
        sts_probe_config(), clock, sts_client=client, canary=canary,
    )
    assert await sts.probe() is True
    assert sts.scoped_credentials_wired is True
    assert len(client.calls) == 1
    assert canary.factory_calls == 1
    assert [name for name, _kwargs in canary.operations] == expected_operations
    assert all(client.close_calls == 1 for client in canary.clients)


@pytest.mark.asyncio
async def test_sts_canary_numeric_403_is_unambiguous_denial() -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    canary = FakeCanaryFactory(foreign="status_403_numeric")
    sts, client, canary = build_probe_sts(
        sts_probe_config(), clock, sts_client=client, canary=canary,
    )
    assert await sts.probe() is True
    assert sts.scoped_credentials_wired is True
    assert [name for name, _kwargs in canary.operations] == [
        "head_bucket", "head_object", "head_object", "list_objects_v2",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "canary_kwargs",
    [
        {"foreign": "allowed"},
        {"list_bucket": "allowed"},
        {"foreign": "not_found"},
        {"list_bucket": "not_found"},
        {"exact": "denied"},
        {"exact": "server_error"},
        {"exact": "ambiguous"},
        {"exact": "nonzero"},
    ],
)
async def test_sts_probe_rejects_wrong_server_scope_or_ambiguous_canary(
    canary_kwargs: dict[str, str],
) -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    canary = FakeCanaryFactory(**canary_kwargs)
    sts, client, canary = build_probe_sts(
        sts_probe_config(), clock, sts_client=client, canary=canary,
    )
    assert await sts.probe() is False
    assert sts.scoped_credentials_wired is False
    assert len(client.calls) == 1
    assert canary.factory_calls == 1
    assert all(client.close_calls == 1 for client in canary.clients)


@pytest.mark.asyncio
async def test_sts_probe_singleflight_and_stale_revalidation() -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock, delay=0.01)
    config = sts_probe_config(sts_probe_revalidate_seconds=10)
    sts, client, _canary = build_probe_sts(config, clock, sts_client=client)

    first = await asyncio.gather(*(sts.probe(force=True) for _ in range(8)))
    assert all(first)
    assert len(client.calls) == 1

    clock.now += config.sts_probe_revalidate_seconds
    assert sts.scoped_credentials_wired is False
    second = await asyncio.gather(*(sts.probe() for _ in range(8)))
    assert all(second)
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_sts_probe_timeout_is_bounded_and_typed_unwired() -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock, delay=1.1)
    sts, client, _canary = build_probe_sts(
        sts_probe_config(sts_probe_timeout_seconds=1), clock, sts_client=client,
    )
    assert await sts.probe() is False
    assert sts.scoped_credentials_wired is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_sts_probe_operation_error_and_close_invalidate_availability() -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    sts, client, _canary = build_probe_sts(sts_probe_config(), clock, sts_client=client)
    assert await sts.probe() is True

    client.behavior = "error"
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(**caller_role_kwargs())
    assert sts.scoped_credentials_wired is False
    assert await sts.probe() is False  # operation failure also gets retry backoff
    assert len(client.calls) == 2

    client.behavior = "success"
    assert await sts.probe(force=True) is True
    await sts.aclose()
    await sts.aclose()
    assert client.close_calls == 1
    assert sts.scoped_credentials_wired is False
    assert await sts.probe() is False
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(**caller_role_kwargs())
    assert len(client.calls) == 3


def test_standalone_jobs_use_backend_rpc_not_objects_scoped_credentials() -> None:
    """No fake worker wiring: current standalone jobs have no objects consumer."""
    import inspect
    from prodavan.application.plugins.jobs import declarations

    composition = inspect.getsource(declarations.PluginJobComposition)
    executor = inspect.getsource(declarations.ScopedManifestOperationExecutor)
    for source in (composition, executor):
        assert "objects.scoped_credentials" not in source
        assert "object_sts" not in source
        assert "MinioSts" not in source
        assert "scoped_credentials(" not in source
    assert "object_sts" not in inspect.signature(declarations.PluginJobComposition).parameters
    assert "backend_path" in executor
    assert "manifest.get(\"rpc_operations\")" in executor
    assert "ScopedManifestOperationExecutor" in composition


@pytest.mark.asyncio
async def test_sts_probe_disabled_has_no_constructor_or_startup_io() -> None:
    clock = FakeClock()
    client = ProbeStsClient(clock)
    sts = MinioSts(config=make_config(sts_enabled=True), sts_client=client, clock=clock)
    assert await sts.probe() is False
    assert sts.scoped_credentials_wired is False
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(**caller_role_kwargs())
    assert client.calls == []


@pytest.mark.asyncio
async def test_two_initializations_share_one_adapter_and_probe_only_on_startup() -> None:
    from prodavan.core.lifespan.manager import LifespanManager

    clock = FakeClock()
    client = ProbeStsClient(clock)
    sts, client, _canary = build_probe_sts(sts_probe_config(), clock, sts_client=client)
    api_manager = LifespanManager()
    worker_manager = LifespanManager()
    api_app = SimpleNamespace(state=SimpleNamespace(lifespan_manager=api_manager))
    worker_app = SimpleNamespace(state=SimpleNamespace(lifespan_manager=worker_manager))
    install._register_minio_sts_lifecycle(api_app, sts, install._Composer(api_app))
    install._register_minio_sts_lifecycle(worker_app, sts, install._Composer(worker_app))

    api_resource = api_manager.get("plugin-minio-sts-probe")
    worker_resource = worker_manager.get("plugin-minio-sts-probe")
    assert api_resource is not None and worker_resource is not None
    assert api_resource.sts is worker_resource.sts is sts
    assert client.calls == []  # registration/create_app/worker factory: no IO
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(**caller_role_kwargs())
    assert client.calls == []

    await api_manager.startup_all()
    assert sts.scoped_credentials_wired is True
    assert len(client.calls) == 1
    await worker_manager.startup_all()
    assert len(client.calls) == 2  # one explicit initialization per process
    assert sts.scoped_credentials_wired is True

    await api_manager.shutdown_all()
    assert api_resource._revalidate_task is None
    assert sts.scoped_credentials_wired is False
    await worker_manager.shutdown_all()
    assert worker_resource._revalidate_task is None
    assert client.close_calls == 0  # platform owns client close, lifecycle invalidates only


@pytest.mark.asyncio
async def test_sts_disabled_and_unprobed_fail_closed_without_network() -> None:
    for config in (make_config(sts_enabled=False), make_config(sts_enabled=True)):
        client = FakeStsClient()
        sts = MinioSts(config=config, sts_client=client)
        assert sts.scoped_credentials_wired is False
        with pytest.raises(err.StsUnavailable, match="disabled|fresh successful post-probe"):
            await sts.assume_role(
                company_id=COMPANY,
                plugin_id=PLUGIN,
                ttl_seconds=900,
                session_policy=iam.session_policy(COMPANY, PLUGIN),
            )
        assert client.calls == []


def test_legacy_sts_availability_overrides_are_typed_rejected() -> None:
    for name in ("io_test_sts_available", "minio_sts_available"):
        with pytest.raises(TypeError, match="unexpected keyword"):
            MinioSts(
                config=make_config(sts_enabled=True),
                sts_client=FakeStsClient(),
                **{name: True},  # type: ignore[arg-type]
            )


@pytest.mark.asyncio
async def test_scoped_credentials_base_config_is_typed_unwired_before_io() -> None:
    for sts_enabled in (False, True):
        config = make_config(sts_enabled=sts_enabled)
        client = FakeStsClient()
        store = RecordingSecretStore()
        adapter = MinioObjectsAdapter(
            buckets=MinioBucketAdmin(config=make_config(), client=FakeChecksumS3()),
            sts=MinioSts(
                config=config,
                sts_client=client,
            ),
            secret_store=store,
        )
        with pytest.raises(Exception, match="objects_scoped_credentials_sts_unwired"):
            await adapter.scoped_credentials(scoped_request())
        assert store.get_calls == []
        assert store.put_calls == []
        assert client.calls == []


@pytest.mark.asyncio
async def test_scoped_credentials_verified_gate_keeps_refs_scope_and_ttl() -> None:
    clock = FakeClock()
    client = FakeStsClient(clock=clock)
    config = make_config(sts_enabled=True, sts_session_duration_seconds=900)
    sts, client, _canary = await build_wired_sts(config, clock, sts_client=client)
    store = RecordingSecretStore()
    adapter = MinioObjectsAdapter(
        buckets=MinioBucketAdmin(config=make_config(), client=FakeChecksumS3()),
        sts=sts,
        secret_store=store,
        clock=clock,
    )

    response = await adapter.scoped_credentials(scoped_request())
    assert response.credentials is not None
    secret_ref = response.credentials.credentials_ref
    assert secret_ref.startswith("vault://ai_keys/plugins/capabilities/objects/")
    assert response.credentials.bucket == BUCKET
    assert response.credentials.key_prefix == PREFIX
    assert response.credentials.expires_at_epoch == int(clock.time()) + 900
    assert "temporary-secret" not in repr(response)
    assert "temporary-session-token" not in repr(response)

    record = json.loads(store.values[secret_ref])
    actions = {
        action
        for statement in record["policy"]["Statement"]
        for action in statement["Action"]
    }
    assert "s3:GetObject" in actions
    assert "s3:PutObject" not in actions
    assert record["bucket"] == BUCKET and record["key_prefix"] == PREFIX
    assert record["credentials"]["session_token"] == "temporary-session-token"
    assert len(client.calls) == 2
    assert client.calls[1]["DurationSeconds"] == 900

    await adapter.scoped_credentials(scoped_request())
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_sts_uses_requested_company_plugin_policy_and_duration_cap() -> None:
    clock = FakeClock()
    client = FakeStsClient(clock=clock)
    config = make_config(sts_enabled=True, sts_session_duration_seconds=900)
    sts, client, _canary = await build_wired_sts(config, clock, sts_client=client)
    canonical = iam.session_policy(COMPANY, PLUGIN)
    read_only = {"Version": canonical["Version"], "Statement": []}
    for raw in canonical["Statement"]:
        statement = dict(raw)
        actions = [
            action for action in raw["Action"]
            if action in {"s3:ListBucket", "s3:GetObject"}
        ]
        if actions:
            statement["Action"] = actions
            read_only["Statement"].append(statement)

    assert sts.scoped_credentials_wired is True
    issued = await sts.assume_role(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        ttl_seconds=900,
        session_policy=read_only,
    )
    assert issued.policy == read_only
    assert issued.bucket == BUCKET and issued.prefix == PREFIX
    call = client.calls[1]
    assert call["RoleArn"] == config.sts_role_arn
    assert call["DurationSeconds"] == 900
    assert json.loads(call["Policy"]) == read_only
    sent_actions = {
        action
        for statement in json.loads(call["Policy"])["Statement"]
        for action in statement["Action"]
    }
    assert "s3:PutObject" not in sent_actions

    capped_client = FakeStsClient(clock=clock)
    capped, capped_client, _capped_canary = await build_wired_sts(
        config, clock, sts_client=capped_client,
    )
    with pytest.raises(err.StsUnavailable, match="configured cap"):
        await capped.assume_role(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            ttl_seconds=901,
            session_policy=read_only,
        )
    assert len(capped_client.calls) == 1  # probe only; invalid TTL has no target call


@pytest.mark.asyncio
async def test_metadata_and_s3_checksum_are_preferred_over_get() -> None:
    digest = hashlib.sha256(b"payload").digest()
    for head in (
        {"Metadata": {"x-amz-meta-sha256": f"sha256:{digest.hex()}"}},
        {"ChecksumSHA256": base64.b64encode(digest).decode("ascii")},
    ):
        s3 = FakeChecksumS3(head=head)
        admin = MinioBucketAdmin(config=make_config(), client=s3)
        assert await admin.content_sha256(bucket=BUCKET, key=KEY) == f"sha256:{digest.hex()}"
        assert s3.get_calls == []


@pytest.mark.asyncio
async def test_objects_list_streams_real_sha256_and_returns_typed_size() -> None:
    payload = b"actual-object-bytes"
    body = ChunkBody([payload[:3], payload[3:9], payload[9:]])
    misleading_etag = "a" * 64
    s3 = FakeChecksumS3(
        head={"Metadata": {}, "ContentLength": len(payload), "ETag": misleading_etag},
        body=body,
        contents=[{"Key": KEY, "Size": len(payload), "ETag": misleading_etag}],
    )
    buckets = MinioBucketAdmin(
        config=make_config(),
        client=s3,
        checksum_max_bytes=1024,
        checksum_chunk_size=4,
    )
    adapter = MinioObjectsAdapter(
        buckets=buckets,
        sts=MinioSts(config=make_config()),
        secret_store=RecordingSecretStore(),
    )
    response = await adapter.list(ObjectsListRequest(
        bucket=BUCKET,
        key_prefix=PREFIX,
        limit=10,
        envelope=envelope(),
    ))

    expected = "sha256:" + hashlib.sha256(payload).hexdigest()
    assert response.objects[0].object_key == KEY
    assert response.objects[0].size_bytes == len(payload)
    assert response.objects[0].content_digest == expected
    assert response.total_bytes == len(payload)
    assert response.objects[0].content_digest != f"sha256:{misleading_etag}"
    assert len(s3.get_calls) == 1
    assert body.close_calls == 1
    assert body.chunk_sizes == [4]


@pytest.mark.asyncio
async def test_checksum_rejects_list_head_size_mismatch_before_get() -> None:
    s3 = FakeChecksumS3(head={"ContentLength": 1})
    admin = MinioBucketAdmin(config=make_config(), client=s3)
    with pytest.raises(err.BucketUnavailable, match="object_list_size_mismatch"):
        await admin.content_sha256(bucket=BUCKET, key=KEY, expected_size=2)
    assert s3.get_calls == []


@pytest.mark.asyncio
async def test_streaming_checksum_aborts_on_oversize_and_closes_body() -> None:
    body = ChunkBody([b"abcd", b"e"])
    s3 = FakeChecksumS3(head={"ContentLength": 4}, body=body)
    admin = MinioBucketAdmin(
        config=make_config(),
        client=s3,
        checksum_max_bytes=4,
        checksum_chunk_size=4,
    )
    with pytest.raises(err.BucketUnavailable, match="size_cap_exceeded"):
        await admin.content_sha256(bucket=BUCKET, key=KEY)
    assert len(s3.get_calls) == 1
    assert body.close_calls == 1

    declared_too_large = FakeChecksumS3(head={"ContentLength": 5})
    declared_admin = MinioBucketAdmin(
        config=make_config(), client=declared_too_large, checksum_max_bytes=4
    )
    with pytest.raises(err.BucketUnavailable, match="size_cap_exceeded"):
        await declared_admin.content_sha256(bucket=BUCKET, key=KEY)
    assert declared_too_large.get_calls == []


@pytest.mark.asyncio
async def test_streaming_errors_are_sanitized_and_body_closes_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "backend-plaintext-secret"
    body = ChunkBody([b"abc"], failure_text=secret)
    s3 = FakeChecksumS3(head={}, body=body)
    admin = MinioBucketAdmin(config=make_config(), client=s3)
    with pytest.raises(err.BucketUnavailable) as caught:
        await admin.content_sha256(bucket=BUCKET, key=KEY)
    assert secret not in str(caught.value)
    assert secret not in caplog.text
    assert body.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl_seconds", [60, 600, 3600])
@pytest.mark.parametrize(
    "method,operation",
    [
        (ObjectPresignMethod.GET, "get_object"),
        (ObjectPresignMethod.PUT, "put_object"),
        (ObjectPresignMethod.HEAD, "head_object"),
    ],
)
async def test_objects_presign_exact_key_method_ttl_and_binding(
    method: ObjectPresignMethod, operation: str, ttl_seconds: int,
) -> None:
    adapter, sign_client, sts_client, store, clock = build_presign_adapter(sts_enabled=False)
    response = await adapter.presign(presign_request(method=method, ttl_seconds=ttl_seconds))

    assert response.url.startswith(f"{ENDPOINT}/{BUCKET}/{KEY}?")
    assert response.bucket == BUCKET
    assert response.key_digest == "sha256:" + hashlib.sha256(KEY.encode("utf-8")).hexdigest()
    assert response.method is method
    assert response.ttl_seconds == ttl_seconds
    assert response.expires_at_epoch == int(clock.time()) + ttl_seconds
    assert response.company_id == COMPANY
    assert response.plugin_id == PLUGIN
    assert response.project_id == "proj_1"
    assert response.plugin_instance_id == "inst_abc"
    assert f"X-Amz-Expires={ttl_seconds}" in response.url
    assert not any(value in response.url for value in (ADMIN_KEY, ADMIN_SECRET, PRESIGN_SECRET))

    assert len(sign_client.presign_calls) == 1
    call = sign_client.presign_calls[0]
    assert call["operation"] == operation
    assert call["HttpMethod"] == method.value
    assert call["Params"] == {"Bucket": BUCKET, "Key": KEY}
    assert call["ExpiresIn"] == ttl_seconds
    assert sts_client.calls == []
    assert store.get_calls == [] and store.put_calls == []

    # A working exact-key operation never turns on or emulates prefix-wide STS.
    with pytest.raises(Exception, match="objects_scoped_credentials_sts_unwired"):
        await adapter.scoped_credentials(scoped_request())
    assert sts_client.calls == []
    assert store.get_calls == [] and store.put_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl_seconds", [0, 59, 3601, 7200])
async def test_objects_presign_rejects_ttl_outside_short_lived_bounds(ttl_seconds: int) -> None:
    _adapter, sign_client, _, _, _ = build_presign_adapter()
    with pytest.raises(ValueError, match="ttl_seconds"):
        presign_request(ttl_seconds=ttl_seconds)
    assert sign_client.presign_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["DELETE", "LIST", "GET", "list", ""])
async def test_objects_presign_rejects_unsupported_or_list_method(method: str) -> None:
    _adapter, sign_client, _, _, _ = build_presign_adapter()
    with pytest.raises(ValueError, match="method"):
        ObjectsPresignRequest(
            bucket=BUCKET, object_key=KEY, method=method,  # type: ignore[arg-type]
            ttl_seconds=900, envelope=envelope(),
        )
    assert sign_client.presign_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "module-mod_other/secret",
        "module-mod_x/",
        "module-mod_x/*",
        "module-mod_x/reports?",
        "module-mod_x/../module-mod_other/secret",
        "module-mod_x/%2e%2e/module-mod_other/secret",
        "module-mod_x/%252e%252e/module-mod_other/secret",
        "module-mod_x/reports//secret",
        "module-mod_x/reports\\..\\secret",
        "plain.txt",
        "tenant-co_other/module-mod_x/a",
    ],
)
async def test_objects_presign_denies_wildcard_list_prefix_and_traversal(key: str) -> None:
    adapter, sign_client, _, _, _ = build_presign_adapter()
    with pytest.raises(Exception, match="objects_key_outside_plugin_scope"):
        await adapter.presign(presign_request(key=key))
    assert sign_client.presign_calls == []


@pytest.mark.asyncio
async def test_objects_presign_denies_other_tenant_and_plugin_before_signing() -> None:
    adapter, sign_client, _, _, _ = build_presign_adapter()
    with pytest.raises(Exception, match="objects_bucket_tenant_mismatch"):
        await adapter.presign(presign_request(bucket="tenant-co_other"))
    with pytest.raises(Exception, match="objects_key_outside_plugin_scope"):
        await adapter.presign(presign_request(
            request_envelope=replace(
                envelope(), subject_id="plugin:mod_other:co_123:inst_abc",
            ),
        ))
    assert sign_client.presign_calls == []


@pytest.mark.asyncio
async def test_objects_presign_missing_signer_fails_closed_without_admin_fallback() -> None:
    adapter, sign_client, sts_client, store, _ = build_presign_adapter(include_presigner=False)
    with pytest.raises(Exception, match="objects_presigner_unavailable"):
        await adapter.presign(presign_request())
    assert sign_client.presign_calls == []
    assert sts_client.calls == []
    assert store.get_calls == [] and store.put_calls == []


@pytest.mark.asyncio
async def test_objects_presign_rejects_expiry_mismatch_and_nonstandard_secret_query() -> None:
    query = canonical_presign_query()
    for url, code in (
        (
            f"{ENDPOINT}/{BUCKET}/{KEY}?"
            f"{canonical_presign_query({'X-Amz-Expires': '599'})}",
            "minio_presign_ttl_mismatch",
        ),
        (
            f"{ENDPOINT}/{BUCKET}/{KEY}?{query}&secret_key={ADMIN_SECRET}",
            "minio_presign_credential_leak",
        ),
        (
            f"{ENDPOINT}/{BUCKET}/{KEY}?"
            f"{canonical_presign_query({'X-Amz-Signature': None})}",
            "minio_presign_invalid_url",
        ),
        (
            f"{ENDPOINT}/tenant-co_other/{KEY}?{query}",
            "minio_presign_scope_mismatch",
        ),
        (
            f"{ENDPOINT}/{BUCKET}/module-mod_other/secret?{query}",
            "minio_presign_scope_mismatch",
        ),
    ):
        await assert_presign_url_rejected(url, code)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,value",
    [("X-Amz-Secret-Key", ADMIN_SECRET), ("AWSAccessKeyId", ADMIN_KEY)],
)
async def test_objects_presign_rejects_noncanonical_credential_fields(
    name: str, value: str,
) -> None:
    url = f"{ENDPOINT}/{BUCKET}/{KEY}?{canonical_presign_query()}&{name}={value}"
    await assert_presign_url_rejected(
        url, "minio_presign_credential_leak", sensitive_value=value,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date", "X-Amz-SignedHeaders"],
)
async def test_objects_presign_rejects_incomplete_sigv4_query(name: str) -> None:
    query = canonical_presign_query({name: None})
    await assert_presign_url_rejected(
        f"{ENDPOINT}/{BUCKET}/{KEY}?{query}", "minio_presign_invalid_url",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"X-Amz-Algorithm": "AWS4-HMAC-SHA1"},
        {"X-Amz-Credential": "not-a-credential"},
        {"X-Amz-Credential": f"{PRESIGN_KEY}/20260102/us-east-1/s3/aws4_request"},
        {"X-Amz-Date": "2026-01-01T00:00:00Z"},
        {"X-Amz-SignedHeaders": "Host"},
        {"X-Amz-SignedHeaders": "host;x-amz-content-sha256"},
        {"X-Amz-Signature": "abc"},
        {"X-Amz-Signature": "A" * 64},
    ],
)
async def test_objects_presign_rejects_noncanonical_sigv4_values(
    changes: dict[str, str | None],
) -> None:
    query = canonical_presign_query(changes)
    await assert_presign_url_rejected(
        f"{ENDPOINT}/{BUCKET}/{KEY}?{query}", "minio_presign_invalid_url",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix",
    [
        f"X-Amz-Signature={'cd' * 32}",
        "X-Amz-Algorithm=AWS4-HMAC-SHA256",
        "unknown=value",
        "unknown=one&unknown=two",
    ],
)
async def test_objects_presign_rejects_duplicate_or_unknown_query_fields(
    suffix: str,
) -> None:
    url = f"{ENDPOINT}/{BUCKET}/{KEY}?{canonical_presign_query()}&{suffix}"
    await assert_presign_url_rejected(url, "minio_presign_invalid_url")


@pytest.mark.asyncio
async def test_objects_presign_rejects_wrong_endpoint() -> None:
    adapter, sign_client, _, _, _ = build_presign_adapter()
    sign_client.endpoint = "https://evil.example.com"
    with pytest.raises(Exception, match="minio_presign_failed"):
        await adapter.presign(presign_request())


@pytest.mark.asyncio
async def test_factory_shared_store_and_minio_resources_close_once() -> None:
    store = RecordingSecretStore()
    s3 = FakeChecksumS3()
    buckets = MinioBucketAdmin(config=make_config(), client=s3)
    sts_client = CloseableStsClient()
    presign_client = FakeS3Client()
    supplied = {
        name.value: object()
        for name in CapabilityName
        if name.value not in {"objects", "secrets"}
    }
    with pytest.raises(Exception, match="legacy_sts_availability_override_forbidden"):
        build_capability_adapters(
            redis=object(),
            repos=SimpleNamespace(grant_store=object()),
            runtime=None,
            services={"object_buckets": buckets},
            settings=None,
            overrides={
                "capability_adapters": supplied,
                "redis_client": object(),
                "secret_store": store,
                "minio_config": make_config(),
                "minio_presign_client": presign_client,
                "minio_sts_available": True,
            },
        )
    assert sts_client.calls == []

    built = build_capability_adapters(
        redis=object(),
        repos=SimpleNamespace(grant_store=object()),
        runtime=None,
        services={"object_buckets": buckets},
        settings=None,
        overrides={
            "capability_adapters": supplied,
            "redis_client": object(),
            "secret_store": store,
            "minio_config": make_config(),
            "minio_presign_client": presign_client,
            "minio_sts_client": sts_client,
        },
    )
    objects = built["adapter_objects"]
    secrets = built["adapter_secrets"]
    assert objects._secrets is store
    assert isinstance(objects._presigner, MinioPresigner)
    assert objects._sts.scoped_credentials_wired is False
    assert secrets._store is store
    response = await objects.presign(presign_request())
    assert response.bucket == BUCKET and response.method is ObjectPresignMethod.GET
    assert presign_client.presign_calls[0]["Params"] == {"Bucket": BUCKET, "Key": KEY}

    for _ in range(2):
        await objects.aclose()
        await secrets.aclose()
    assert store.close_calls == 1
    assert s3.close_calls == 1
    assert sts_client.close_calls == 1
    assert presign_client.close_calls == 1


def _tap_close(resource: Any, events: list[str], tag: str) -> None:
    original = resource.close

    def close() -> None:
        events.append(tag)
        original()

    resource.close = close


def _factory_overrides(store: Any, supplied: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "capability_adapters": supplied,
        "redis_client": object(),
        "secret_store": store,
        "minio_config": make_config(),
        "minio_presign_client": FakeS3Client(),
        **extra,
    }


@pytest.mark.asyncio
async def test_shared_secret_store_closes_once_in_order_after_consumers() -> None:
    store = RecordingSecretStore()
    s3 = FakeChecksumS3()
    buckets = MinioBucketAdmin(config=make_config(), client=s3)
    sts_client = CloseableStsClient()
    presign_client = FakeS3Client()
    events: list[str] = []
    _tap_close(store, events, "secret_store")
    _tap_close(s3, events, "s3")
    _tap_close(sts_client, events, "sts")
    _tap_close(presign_client, events, "presign")
    supplied = {
        name.value: object()
        for name in CapabilityName
        if name.value not in {"objects", "secrets"}
    }
    built = build_capability_adapters(
        redis=object(),
        repos=SimpleNamespace(grant_store=object()),
        runtime=None,
        services={"object_buckets": buckets},
        settings=None,
        overrides=_factory_overrides(
            store, supplied, minio_sts_client=sts_client, minio_presign_client=presign_client
        ),
    )
    objects = built["adapter_objects"]
    secrets = built["adapter_secrets"]
    # No premature close at composition time, and the secrets consumer is not an owner.
    assert store.close_calls == 0
    assert events == []
    await secrets.aclose()
    assert store.close_calls == 0

    await objects.aclose()
    # The single owner closes buckets/sts/presigner first and the shared store
    # exactly once, last; repeated closes of either consumer change nothing.
    assert events == ["s3", "sts", "presign", "secret_store"]
    assert store.close_calls == 1
    await objects.aclose()
    await secrets.aclose()
    assert store.close_calls == 1
    assert s3.close_calls == 1
    assert sts_client.close_calls == 1
    assert presign_client.close_calls == 1
    assert events == ["s3", "sts", "presign", "secret_store"]


@pytest.mark.asyncio
async def test_external_objects_adapter_transfers_store_ownership_to_secrets() -> None:
    store = RecordingSecretStore()
    external_objects = object()
    supplied = {
        name.value: object()
        for name in CapabilityName
        if name.value != "secrets"
    }
    supplied["objects"] = external_objects
    built = build_capability_adapters(
        redis=object(),
        repos=SimpleNamespace(grant_store=object()),
        runtime=None,
        services={},
        settings=None,
        overrides=_factory_overrides(store, supplied),
    )
    assert built["adapter_objects"] is external_objects
    secrets = built["adapter_secrets"]
    # Externally supplied objects adapters manage their own lifecycle, so the
    # secrets adapter is the single owner of the shared SecretStore.
    assert store.close_calls == 0
    await secrets.aclose()
    assert store.close_calls == 1
    await secrets.aclose()
    assert store.close_calls == 1


@pytest.mark.asyncio
async def test_unowned_factory_objects_leaves_single_store_owner_to_secrets() -> None:
    store = RecordingSecretStore()
    s3 = FakeChecksumS3()
    buckets = MinioBucketAdmin(config=make_config(), client=s3)
    sts_client = CloseableStsClient()
    supplied = {
        name.value: object()
        for name in CapabilityName
        if name.value not in {"objects", "secrets"}
    }
    built = build_capability_adapters(
        redis=object(),
        repos=SimpleNamespace(grant_store=object()),
        runtime=None,
        services={"object_buckets": buckets},
        settings=None,
        overrides=_factory_overrides(
            store, supplied, minio_sts_client=sts_client, own_object_resources=False
        ),
    )
    objects = built["adapter_objects"]
    secrets = built["adapter_secrets"]
    await objects.aclose()
    # An explicitly unowned objects adapter closes nothing, so there is still
    # exactly one owner left for the shared SecretStore.
    assert store.close_calls == 0
    assert s3.close_calls == 0
    assert sts_client.close_calls == 0
    await secrets.aclose()
    assert store.close_calls == 1
    await objects.aclose()
    await secrets.aclose()
    assert store.close_calls == 1
