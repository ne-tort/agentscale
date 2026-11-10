"""Production capability adapters: stateful external-IO fakes only.

Written during the migration verification freeze; intentionally not executed.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters.build import build_capability_adapters
from prodavan.application.plugins.capabilities.adapters.cache import RedisCacheAdapter
from prodavan.application.plugins.capabilities.adapters.docs import DocumentStoreDocsAdapter
from prodavan.application.plugins.capabilities.adapters.events import KafkaEventsAdapter
from prodavan.application.plugins.capabilities.adapters.jobs import CeleryJobsAdapter
from prodavan.application.plugins.capabilities.adapters.mcp import DurableMcpAdapter
from prodavan.application.plugins.capabilities.adapters.network import K8sEgressNetworkAdapter
from prodavan.application.plugins.capabilities.adapters.objects import MinioObjectsAdapter
from prodavan.application.plugins.capabilities.adapters.pods import ScopedPodsAdapter
from prodavan.application.plugins.capabilities.adapters.secrets import SecretStoreSecretsAdapter
from prodavan.application.plugins.capabilities.contracts import (
    CacheDeleteRequest, CacheGetRequest, CacheIncrRequest, CachePublishRequest,
    CacheSetRequest, CapabilityEnvelope, CapabilityName, DocsQueryRequest,
    EventsPublishRequest, EventsStartOffset, EventsSubscribeRequest, JobsCancelRequest,
    JobsGetRequest, JobsSubmitRequest, McpRegisterRequest, McpToolDefinition,
    NetworkEgressRequest, NetworkHttpMethod, ObjectPermission, ObjectsDeleteRequest,
    ObjectsListRequest, ObjectPresignMethod, ObjectsPresignRequest,
    ObjectsScopedCredentialsRequest, PodActionName, PodsInvokeRequest,
    SecretsReadRequest,
)
from prodavan.application.plugins.capabilities.quotas import QuotaUsage
from prodavan.application.plugins.events.contracts import RawDlqRecord
from prodavan.application.plugins.objects.ports import ObjectInfo, ScopedCredentials
from prodavan.application.plugins.wiring.adapters import (
    UnavailableAdapter, adapter_report, build_adapter_registry,
)
from prodavan.application.plugins.wiring.config import (
    PluginPlatformConfig, PluginPlatformConfigError,
)
from prodavan.infrastructure.plugins.capabilities.celery import RedisCeleryJobQueue
from prodavan.infrastructure.plugins.capabilities.kafka import (
    KafkaEventResourceManager, KafkaRawDlqAdapter, KafkaSecurityConfig, KafkaSecurityError,
)
from prodavan.infrastructure.plugins.capabilities.redis import RedisMcpRegistrationStore
from prodavan.infrastructure.plugins.minio.adapters import MinioSts
from tests.unit.plugins.wiring.conftest import TEST_DIGEST, TEST_SECRET, TEST_STATIC_ORIGIN
from tests.unit.plugins.jobs.conftest import (
    OPERATION as JOB_OPERATION,
    RESULT_DIGEST as JOB_RESULT_DIGEST,
    make_tasks as make_job_tasks,
    request as make_job_request,
    envelope as job_envelope,
)

def envelope(*, company_id: str = "co_123", project_id: str | None = "proj_1") -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req1",
        company_id=company_id,
        project_id=project_id,
        subject_id=f"plugin:mod_x:{company_id}:inst_abc",
        plugin_instance_id="inst_abc",
        artifact_digest=TEST_DIGEST,
        grant_id="grant_01",
    )


class StatefulRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.expiry: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []
        self.fail = False
        self.close_calls = 0

    def _guard(self) -> None:
        if self.fail:
            raise RuntimeError("redis://user:plaintext-password@private-host")

    async def get(self, key: str) -> Any:
        self._guard()
        return self.values.get(key)

    async def set(self, key: str, value: Any, *, ex: int | None = None, nx: bool | None = None) -> bool:
        self._guard()
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = ex
        return True

    async def delete(self, key: str) -> int:
        self._guard()
        return int(self.values.pop(key, None) is not None)

    async def eval(self, script: str, count: int, key: str, amount: int, ttl: int) -> int:
        self._guard()
        assert count == 1 and "INCRBY" in script
        value = int(self.values.get(key, 0)) + amount
        self.values[key] = str(value)
        if value == amount:
            self.expiry[key] = ttl
        return value

    async def publish(self, channel: str, value: str) -> int:
        self._guard()
        self.published.append((channel, value))
        return 2

    def close(self) -> None:
        self.close_calls += 1


class FakeSecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.close_calls = 0

    def get(self, ref: str) -> str:
        if ref not in self.values:
            error = RuntimeError("SECRET_NOT_FOUND")
            error.code = "SECRET_NOT_FOUND"
            raise error
        return self.values[ref]

    def put(self, key: str, value: str) -> str:
        ref = f"vault://ai_keys/{key}"
        self.values[ref] = value
        return ref

    def close(self) -> None:
        self.close_calls += 1


class FullAdapter:
    def __init__(self, *, meter: bool = True) -> None:
        self._meter = meter
        if meter:
            self.quota_usage = lambda request: QuotaUsage(
                ops=1, rows=0, bytes=0, keys=1, object_bytes=0, egress_bytes=0
            )

    def __getattr__(self, name: str) -> Any:
        if name == "quota_usage" and not self._meter:
            raise AttributeError(name)
        async def operation(request: object) -> object:
            return object()
        return operation


def config() -> PluginPlatformConfig:
    return PluginPlatformConfig(
        enabled=True,
        claims_secret=TEST_SECRET,
        event_signing_secret_ref="vault://unit/plugin-event-signing",
        static_origin=TEST_STATIC_ORIGIN,
        shell_origin="https://agentscale.example.test",
        cosign_identity="https://example.test/workflow@refs/heads/main",
    )


def full_adapters() -> dict[str, object]:
    return {name.value: FullAdapter() for name in CapabilityName}


def test_factory_fails_closed_for_missing_secret_store_and_pod_executor() -> None:
    repos = SimpleNamespace(grant_store=object())
    with pytest.raises(Exception, match="secret_store_required"):
        build_capability_adapters(
            redis=object(), repos=repos, runtime=None, services={}, settings=None, overrides={}
        )
    supplied = {name.value: FullAdapter() for name in CapabilityName if name is not CapabilityName.PODS}
    with pytest.raises(Exception, match="pod_executor_required"):
        build_capability_adapters(
            redis=object(), repos=repos, runtime=None, services={}, settings=None,
            overrides={"capability_adapters": supplied},
        )


def test_registry_is_complete_and_requires_quota_meter() -> None:
    registry = build_adapter_registry(config(), **full_adapters())
    assert len(registry.registered) == 9
    assert adapter_report(registry)["stub_count"] == 0
    with pytest.raises(PluginPlatformConfigError, match="missing"):
        build_adapter_registry(config(), **{k: v for k, v in full_adapters().items() if k != "cache"})
    broken = full_adapters()
    broken["cache"] = FullAdapter(meter=False)
    with pytest.raises(PluginPlatformConfigError, match="quota_usage"):
        build_adapter_registry(config(), **broken)
    with pytest.raises(PluginPlatformConfigError, match="UnavailableAdapter"):
        build_adapter_registry(config(), **{**full_adapters(), "cache": UnavailableAdapter("cache")})


@pytest.mark.asyncio
async def test_per_operation_quota_usage_dimensions() -> None:
    cache = RedisCacheAdapter(client=StatefulRedis())
    assert cache.quota_usage(CacheDeleteRequest(key="k", envelope=envelope())).keys == 1
    assert cache.quota_usage(CacheIncrRequest(key="k", amount=1, envelope=envelope())).keys == 1
    publish_usage = cache.quota_usage(CachePublishRequest(
        channel="events", message_digest=TEST_DIGEST, envelope=envelope()
    ))
    assert publish_usage.keys == 1 and publish_usage.bytes == 71

    class Queue:
        async def submit(self, *, identity: dict[str, str]) -> dict[str, Any]:
            return {}
        async def get(self, *, identity: dict[str, str]) -> dict[str, Any]:
            return {}
        async def cancel(self, *, identity: dict[str, str]) -> dict[str, Any]:
            return {}

    class UnusedResolver:
        async def resolve(self, request: Any) -> Any:
            raise AssertionError("get/cancel quota must not resolve a submit operation")

    jobs = CeleryJobsAdapter(queue=Queue(), allowed_tasks=UnusedResolver())
    get_usage = jobs.quota_usage(JobsGetRequest(job_id="job1", run_id="run1", envelope=envelope()))
    cancel_usage = jobs.quota_usage(JobsCancelRequest(
        job_id="job1", run_id="run1", lease_id="lease1", envelope=envelope()
    ))
    assert get_usage.rows == cancel_usage.rows == 1
    assert get_usage.keys == cancel_usage.keys == 1

    objects = MinioObjectsAdapter(
        buckets=Buckets(), sts=Sts(), secret_store=FakeSecretStore(),
        clock=SimpleNamespace(time=lambda: 1000.0),
    )
    delete_usage = await objects.quota_usage(ObjectsDeleteRequest(
        bucket="tenant-co_123", object_key="module-mod_x/a.bin", envelope=envelope()
    ))
    scoped_usage = await objects.quota_usage(ObjectsScopedCredentialsRequest(
        bucket="tenant-co_123", key_prefix="module-mod_x/",
        permissions=frozenset({ObjectPermission.READ}), ttl_seconds=900, envelope=envelope(),
    ))
    presign_usage = await objects.quota_usage(ObjectsPresignRequest(
        bucket="tenant-co_123", object_key="module-mod_x/a.bin",
        method=ObjectPresignMethod.HEAD, ttl_seconds=60, envelope=envelope(),
    ))
    assert delete_usage.object_bytes > 0 and delete_usage.keys == 1
    assert scoped_usage.rows == 1 and scoped_usage.object_bytes == 0
    assert presign_usage.rows == 1 and presign_usage.keys == 1
    assert presign_usage.object_bytes == 0 and presign_usage.egress_bytes == 0


@pytest.mark.asyncio
async def test_cache_success_tenant_binding_quota_sanitizing_and_close_once() -> None:
    redis = StatefulRedis()
    adapter = RedisCacheAdapter(client=redis, owns_client=True)
    request = CacheSetRequest(key="alpha", value="payload", envelope=envelope())
    assert adapter.quota_usage(request).bytes == 7
    await adapter.set(request)
    keys = set(redis.values)
    assert len(keys) == 1
    key = keys.pop()
    assert "tenant:co_123:plugin:mod_x:inst_abc:key:alpha" in key
    found = await adapter.get(CacheGetRequest(key="alpha", envelope=envelope()))
    assert found.found and found.value_digest.startswith("sha256:")
    other = await adapter.get(CacheGetRequest(key="alpha", envelope=envelope(company_id="co_other")))
    assert not other.found
    redis.fail = True
    with pytest.raises(Exception) as caught:
        await adapter.set(request)
    assert "plaintext-password" not in str(caught.value)
    assert "private-host" not in str(caught.value)
    redis.fail = False
    await adapter.aclose()
    await adapter.aclose()
    assert redis.close_calls == 1


@pytest.mark.asyncio
async def test_docs_success_tenant_scope_and_quota() -> None:
    class Service:
        async def find(self, **kwargs: Any) -> Any:
            clauses = kwargs["filter"]["$and"]
            assert {"company_id": "co_123"} in clauses
            assert {"plugin_instance_id": "inst_abc"} in clauses
            item = {
                "id": "doc1", "company_id": "co_123", "plugin_instance_id": "inst_abc",
                "kind": "offer", "body": "not returned",
            }
            return SimpleNamespace(items=[item], count=1)

    adapter = DocumentStoreDocsAdapter(service=Service())
    request = DocsQueryRequest(
        namespace="tenant_infra", collection="docs", predicates=(), limit=5, envelope=envelope()
    )
    assert adapter.quota_usage(request).rows == 5
    result = await adapter.query(request)
    assert result.matched == 1 and result.documents[0].document_id == "doc1"
    assert result.documents[0].content_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_secrets_read_only_digest_and_exact_ref() -> None:
    store = FakeSecretStore()
    ref = "vault://ai_keys/plugins/capabilities/secrets/co_123/mod_x/inst_abc/api-key"
    store.values[ref] = "super-secret-plaintext"
    adapter = SecretStoreSecretsAdapter(store=store, owns_store=True)
    result = await adapter.read(SecretsReadRequest(secret_ref=ref, envelope=envelope()))
    assert result.value_digest.startswith("sha256:")
    assert "super-secret-plaintext" not in repr(result)
    with pytest.raises(Exception, match="secret_ref_outside_plugin_scope"):
        await adapter.read(SecretsReadRequest(
            secret_ref="vault://ai_keys/plugins/capabilities/secrets/co_other/mod_x/inst_abc/api-key",
            envelope=envelope(),
        ))
    await adapter.aclose()
    await adapter.aclose()
    assert store.close_calls == 1


@pytest.mark.asyncio
async def test_mcp_durable_idempotent_registration_and_quota() -> None:
    redis = StatefulRedis()
    adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    tool = McpToolDefinition(
        tool_name="cache-read", tool_version="1", capability=CapabilityName.MCP,
        operation="mcp.invoke", input_schema_digest=TEST_DIGEST,
    )
    request = McpRegisterRequest(server_id="server1", tools=(tool,), envelope=envelope())
    assert adapter.quota_usage(request).rows == 1
    first = await adapter.register(request)
    second = await adapter.register(request)
    assert first.registered == second.registered == 1
    assert len(redis.values) == 1
    changed = McpRegisterRequest(
        server_id="server1",
        tools=(McpToolDefinition(
            tool_name="cache-read", tool_version="2", capability=CapabilityName.MCP,
            operation="mcp.invoke", input_schema_digest=TEST_DIGEST,
        ),),
        envelope=envelope(),
    )
    with pytest.raises(Exception, match="mcp_registration_conflict"):
        await adapter.register(changed)


class Buckets:
    def __init__(self) -> None:
        self.deleted: set[str] = set()
        self.close_calls = 0

    async def list_prefix_page(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["bucket"] == "tenant-co_123"
        assert kwargs["prefix"] == "module-mod_x/"
        return {
            "objects": [ObjectInfo(key="module-mod_x/a.bin", size=123, etag="not-sha")],
            "next_cursor": None,
        }

    async def content_sha256(
        self, *, bucket: str, key: str, expected_size: int | None = None
    ) -> str:
        assert expected_size == 123
        return TEST_DIGEST

    async def delete_object(self, *, bucket: str, key: str) -> bool:
        if key in self.deleted:
            return False
        self.deleted.add(key)
        return True

    def close(self) -> None:
        self.close_calls += 1


class Sts:
    # Explicit test-only confirmation of the same port gate required in production.
    scoped_credentials_wired = True

    def __init__(self) -> None:
        self.calls = 0

    async def assume_role(self, **kwargs: Any) -> ScopedCredentials:
        self.calls += 1
        return ScopedCredentials(
            access_key="scoped-access", secret_key="scoped-secret", session_token="scoped-token",
            bucket="tenant-co_123", prefix="module-mod_x/",
            expires_at=1600.0, policy=kwargs["session_policy"],
        )


@pytest.mark.asyncio
async def test_objects_sts_idempotency_scope_and_actual_list_quota() -> None:
    buckets = Buckets()
    sts = Sts()
    secrets = FakeSecretStore()
    adapter = MinioObjectsAdapter(
        buckets=buckets, sts=sts, secret_store=secrets, owns_resources=True,
        owns_secret_store=True, clock=SimpleNamespace(time=lambda: 1000.0),
    )
    request = ObjectsScopedCredentialsRequest(
        bucket="tenant-co_123", key_prefix="module-mod_x/",
        permissions=frozenset({ObjectPermission.READ}), ttl_seconds=900, envelope=envelope(),
    )
    result = await adapter.scoped_credentials(request)
    assert result.credentials is not None
    assert result.credentials.credentials_ref.startswith("vault://")
    assert "scoped-secret" not in repr(result)
    stored = json.loads(secrets.values[result.credentials.credentials_ref])
    actions = {
        action
        for statement in stored["policy"]["Statement"]
        for action in statement["Action"]
    }
    assert "s3:GetObject" in actions and "s3:PutObject" not in actions
    await adapter.scoped_credentials(request)
    assert sts.calls == 1
    listing = ObjectsListRequest(
        bucket="tenant-co_123", key_prefix="module-mod_x/", limit=10, envelope=envelope()
    )
    usage = await adapter.quota_usage(listing)
    assert usage.rows == 1 and usage.object_bytes == 123
    page = await adapter.list(listing)
    assert page.total_bytes == 123 and page.objects[0].content_digest == TEST_DIGEST
    deleted = await adapter.delete(ObjectsDeleteRequest(
        bucket="tenant-co_123", object_key="module-mod_x/a.bin", envelope=envelope()
    ))
    assert deleted.deleted
    await adapter.aclose()
    await adapter.aclose()
    assert buckets.close_calls == 1
    assert secrets.close_calls == 1


@pytest.mark.asyncio
async def test_events_publish_and_replay_safe_subscribe() -> None:
    class Dispatcher:
        topic = "agentscale.plugin.events"
        def __init__(self) -> None:
            self.events = []
        async def publish(self, event: Any) -> bool:
            self.events.append(event)
            return True
        async def consume(self, subscription: Any) -> None:
            return None

    class Subscriptions:
        def __init__(self) -> None:
            self.items = {}
        async def get(self, subscription_id: str) -> Any:
            return self.items.get(subscription_id)
        async def put(self, subscription: Any, *, active: bool = True) -> None:
            self.items[subscription.id] = subscription
        async def revoke(self, subscription_id: str) -> bool:
            self.items.pop(subscription_id, None)
            return True

    class Runtime:
        def __init__(self) -> None:
            self.started = []
        async def start(self, **kwargs: Any) -> dict[str, Any]:
            self.started.append(kwargs)
            subscription = kwargs["subscription"]
            return {
                "subscription_id": subscription.id,
                "consumer_group": subscription.consumer_group,
                "enable_auto_commit": False,
                "auto_offset_reset": "earliest",
                "manual_commit": True,
                "max_retries": 3,
            }
        async def aclose(self) -> None:
            return None

    class Lifecycle:
        def __init__(self) -> None:
            self.starts = 0
        async def start(self) -> None:
            self.starts += 1
        async def aclose(self) -> None:
            return None

    dispatcher = Dispatcher()
    runtime = Runtime()
    lifecycle = Lifecycle()
    adapter = KafkaEventsAdapter(
        dispatcher=dispatcher, subscriptions=Subscriptions(), runtime=runtime, lifecycle=lifecycle
    )
    publish = EventsPublishRequest(
        topic=dispatcher.topic, event_type="offer.updated", partition_key="co_123",
        payload_digest=TEST_DIGEST, envelope=envelope(),
    )
    published = await adapter.publish(publish)
    assert published.event_id.startswith("evt_") and dispatcher.events[0].company_id == "co_123"
    subscribed = await adapter.subscribe(EventsSubscribeRequest(
        topic=dispatcher.topic, consumer_group="workers", subscription_id="sub1",
        event_types=("offer.updated",), start_offset=EventsStartOffset.EARLIEST,
        max_retries=3, envelope=envelope(),
    ))
    assert subscribed.manual_commit
    assert subscribed.subscription_id.startswith("co_123:inst_abc:sub1:")
    with pytest.raises(Exception, match="events_latest_offset_replay_unsafe"):
        await adapter.subscribe(EventsSubscribeRequest(
            topic=dispatcher.topic, consumer_group="workers", subscription_id="sub2",
            event_types=("offer.updated",), start_offset=EventsStartOffset.LATEST,
            max_retries=3, envelope=envelope(),
        ))


class Worker:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.calls = []
    def send_task(self, name: str, *, kwargs: dict[str, Any], task_id: str) -> Any:
        self.calls.append((name, kwargs, task_id))
        return SimpleNamespace(id=task_id)


class Celery:
    def __init__(self) -> None:
        self.status = "PENDING"
        self.revocations = []
        self.control = SimpleNamespace(revoke=lambda job_id, terminate: self.revocations.append((job_id, terminate)))
    def AsyncResult(self, job_id: str) -> Any:
        return SimpleNamespace(status=self.status, result=JOB_RESULT_DIGEST)


@pytest.mark.asyncio
async def test_jobs_submit_get_cancel_idempotency_and_lease() -> None:
    redis = StatefulRedis()
    celery = Celery()
    holder: dict[str, Any] = {}
    def worker() -> Worker:
        return holder["worker"]
    queue = RedisCeleryJobQueue(
        redis=redis, worker_provider=worker, celery_provider=lambda: celery
    )
    adapter = CeleryJobsAdapter(queue=queue, allowed_tasks=make_job_tasks())
    request = make_job_request(run_id="run1")
    # Deterministic job id is computed from the resolved operation and digests.
    resolution = await adapter._resolve(request)
    identity = adapter._identity(request, resolution=resolution)
    holder["worker"] = Worker(identity["job_id"])
    submitted = await adapter.submit(request)
    assert submitted.job_id == identity["job_id"] and submitted.lease is not None
    again = await adapter.submit(request)
    assert again.job_id == submitted.job_id and len(holder["worker"].calls) == 1
    celery.status = "SUCCESS"
    got = await adapter.get(JobsGetRequest(
        job_id=submitted.job_id, run_id="run1", envelope=job_envelope()
    ))
    assert got.state.value == "completed" and got.result_digest.startswith("sha256:")
    celery.status = "PENDING"
    pending_request = make_job_request(run_id="run2")
    pending = await adapter.submit(pending_request)
    with pytest.raises(Exception):
        await adapter.cancel(JobsCancelRequest(
            job_id=pending.job_id, run_id="run2", lease_id="wrong", envelope=job_envelope()
        ))
    cancelled = await adapter.cancel(JobsCancelRequest(
        job_id=pending.job_id, run_id="run2",
        lease_id=pending.lease.lease_id, envelope=job_envelope(),
    ))
    assert cancelled.state.value == "cancelled"


@pytest.mark.asyncio
async def test_pods_executor_result_and_project_binding() -> None:
    class Executor:
        async def execute(self, *, identity: dict[str, str]) -> dict[str, Any]:
            return {**identity, "result_digest": TEST_DIGEST}

    adapter = ScopedPodsAdapter(executor=Executor())
    request = PodsInvokeRequest(
        pod_id="pod1", action=PodActionName.READ_ARTIFACT,
        target_ref="artifact1", arguments_digest=TEST_DIGEST, envelope=envelope(),
    )
    assert adapter.quota_usage(request).object_bytes > 0
    result = await adapter.invoke(request)
    assert result.result_digest == TEST_DIGEST
    without_project = PodsInvokeRequest(
        pod_id="pod1", action=PodActionName.READ_STATUS, target_ref="status",
        envelope=envelope(project_id=None),
    )
    with pytest.raises(Exception, match="pod_project_scope_required"):
        await adapter.invoke(without_project)


@pytest.mark.asyncio
async def test_network_policy_hops_quota_and_default_sa_rejected() -> None:
    class Transport:
        scoped_identity = True
        async def execute(self, *, identity: dict[str, Any]) -> dict[str, Any]:
            return {
                **identity, "status_code": 200, "response_digest": TEST_DIGEST,
                "bytes_sent": 0, "bytes_received": 42, "resolved_host": "api.example.com",
                "hops": ("api.example.com",), "redirect_checked": True, "tls_verified": True,
            }

    class Egress:
        def check(self, *args: Any, **kwargs: Any) -> Any:
            assert kwargs["resolved_host"] == "api.example.com"
            assert kwargs["redirect_checked"] and kwargs["tls_verified"]
            return SimpleNamespace(allowed=True)

    class Policy:
        async def allows(self, **kwargs: Any) -> bool:
            return True

    adapter = K8sEgressNetworkAdapter(
        transport=Transport(), egress=Egress(), initial_policy=Policy(), max_response_bytes=1024
    )
    request = NetworkEgressRequest(
        host="api.example.com", port=443, path="/v1/status",
        method=NetworkHttpMethod.GET, envelope=envelope(),
    )
    assert adapter.quota_usage(request).egress_bytes == 1024
    result = await adapter.egress(request)
    assert result.status_code == 200 and result.bytes_received == 42
    denied = K8sEgressNetworkAdapter(
        transport=Transport(), egress=Egress(),
        initial_policy=SimpleNamespace(allows=lambda **kwargs: False),
    )
    with pytest.raises(Exception, match="network_initial_host_denied"):
        await denied.egress(request)

    with pytest.raises(Exception, match="default_mounted_service_account_forbidden"):
        K8sEgressNetworkAdapter(
            transport=SimpleNamespace(execute=Transport().execute, scoped_identity=False),
            egress=Egress(), initial_policy=Policy(),
        )


@pytest.mark.asyncio
async def test_raw_dlq_is_digest_only_and_deduplicated() -> None:
    class Producer:
        def __init__(self) -> None:
            self.sent = []
            self.flushes = 0
        async def send_and_wait(self, topic: str, **kwargs: Any) -> None:
            self.sent.append((topic, kwargs))
        async def flush(self) -> None:
            self.flushes += 1

    redis = StatefulRedis()
    producer = Producer()
    resources = SimpleNamespace(
        _started=True,
        producer=SimpleNamespace(_producer=producer),
    )
    adapter = KafkaRawDlqAdapter(resources=resources, redis=redis)
    record = RawDlqRecord(
        topic="agentscale.plugin.events", partition=0, offset=7,
        consumer_group="tenant-group", payload_digest=TEST_DIGEST, error_kind="ValueError",
    )
    assert await adapter.publish(record) is True
    assert await adapter.publish(record) is False
    assert len(producer.sent) == 1
    assert b"raw-secret-value" not in producer.sent[0][1]["value"]
    assert b"payload_digest" in producer.sent[0][1]["value"]


def test_kafka_requires_sasl_tls_and_secret_refs(tmp_path: Path) -> None:
    import ssl

    with pytest.raises(KafkaSecurityError):
        KafkaSecurityConfig(
            sasl_username="user", sasl_password="pass",
            ssl_context=ssl._create_unverified_context(),
        )
    secure = KafkaSecurityConfig(
        sasl_username="user", sasl_password="pass",
        ssl_context=ssl.create_default_context(),
    )
    assert secure.as_aiokafka_kwargs()["security_protocol"] == "SASL_SSL"
    assert "pass" not in repr(secure)
    ca_certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
    assert ca_certificates
    ca_file = tmp_path / "plugin-kafka-ca.pem"
    ca_file.write_text(
        ssl.DER_cert_to_PEM_cert(ca_certificates[0]), encoding="utf-8"
    )
    with pytest.raises(KafkaSecurityError, match="SecretStore"):
        KafkaSecurityConfig.from_secret_refs(
            secret_store=FakeSecretStore(),
            env={"PLUGIN_KAFKA_TLS_CA_FILE": str(ca_file)},
        )


@pytest.mark.asyncio
async def test_kafka_resources_close_once() -> None:
    import ssl

    manager = KafkaEventResourceManager(
        bootstrap_servers="kafka:9092",
        security=KafkaSecurityConfig(
            sasl_username="user", sasl_password="pass",
            ssl_context=ssl.create_default_context(),
        ),
    )
    class Resource:
        def __init__(self) -> None:
            self.calls = 0
        async def stop(self) -> None:
            self.calls += 1
        async def close(self) -> None:
            self.calls += 1
    producer = Resource()
    admin = Resource()
    manager.producer = producer
    manager.topic_admin = admin
    await manager.aclose()
    await manager.aclose()
    assert producer.calls == 1
    assert admin.calls == 1


def test_minio_sts_rejects_action_superset() -> None:
    sts = MinioSts()
    with pytest.raises(Exception):
        import asyncio
        asyncio.run(sts.assume_role(
            company_id="co_123", plugin_id="mod_x", ttl_seconds=900,
            session_policy={"Version": "2012-10-17", "Statement": []},
        ))
