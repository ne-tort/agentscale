"""Digest payload store acceptance tests; covered by a run in this worktree."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
)
from prodavan.application.plugins.capabilities.adapters.network import (
    K8sEgressNetworkAdapter,
)
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    NetworkEgressRequest,
    NetworkHttpMethod,
)
from prodavan.application.plugins.capabilities.payloads import (
    DigestPayloadBackendError,
    DigestPayloadClosedError,
    DigestPayloadIntegrityError,
    DigestPayloadResolverFactory,
    DigestPayloadResolverNotConfiguredError,
    DigestPayloadStore,
    DigestPayloadStoreConfig,
    DigestPayloadTooLargeError,
    DigestPayloadValidationError,
    require_body_digest_resolver,
)
from prodavan.application.plugins.wiring import install
from prodavan.core.lifespan.manager import LifespanManager


COMPANY = "co_123"
PLUGIN = "mod_example"
INSTANCE = "inst_456"


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def identity(**changes: str) -> dict[str, str]:
    values = {
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "plugin_instance_id": INSTANCE,
    }
    values.update(changes)
    return values


class StatefulRedis:
    """Async Redis fake with real NX semantics and deterministic expiry."""

    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.expiry: dict[str, int] = {}
        self.now = 0
        self.set_calls = 0
        self.successful_writes = 0
        self.close_calls = 0

    def _expire(self, key: str) -> None:
        deadline = self.expiry.get(key)
        if deadline is not None and deadline <= self.now:
            self.values.pop(key, None)
            self.expiry.pop(key, None)

    async def set(
        self,
        key: str,
        value: str | bytes,
        *,
        ex: int | None = None,
        nx: bool | None = None,
    ) -> bool | None:
        await asyncio.sleep(0)
        self._expire(key)
        self.set_calls += 1
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = self.now + ex
        self.successful_writes += 1
        return True

    async def get(self, key: str) -> str | bytes | None:
        await asyncio.sleep(0)
        self._expire(key)
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        await asyncio.sleep(0)
        self._expire(key)
        self.expiry.pop(key, None)
        return int(self.values.pop(key, None) is not None)

    def advance(self, seconds: int) -> None:
        self.now += seconds

    async def aclose(self) -> None:
        self.close_calls += 1


def store(
    redis: StatefulRedis,
    *,
    max_payload_bytes: int = 1024,
    ttl_seconds: int = 30,
) -> DigestPayloadStore:
    return DigestPayloadStore(
        client=redis,
        config=DigestPayloadStoreConfig(
            max_payload_bytes=max_payload_bytes,
            ttl_seconds=ttl_seconds,
        ),
    )


@pytest.mark.asyncio
async def test_exact_bytes_roundtrip_exists_resolver_and_release_retry() -> None:
    redis = StatefulRedis()
    payloads = store(redis)
    body = b"\x00\xff\xfe\r\n\x80canonical-bytes"

    actual_digest = await payloads.put(**identity(), payload=body)

    assert actual_digest == digest(body)
    assert await payloads.get(**identity(), digest=actual_digest) == body
    assert await payloads.exists(**identity(), digest=actual_digest) is True
    assert len(redis.values) == 1
    stored = next(iter(redis.values.values()))
    assert isinstance(stored, str)
    assert base64.b64decode(stored, validate=True) == body

    resolver = DigestPayloadResolverFactory(store=payloads)()
    assert await resolver.resolve_body_digest(
        COMPANY, PLUGIN, INSTANCE, actual_digest
    ) == body
    assert await payloads.release(**identity(), digest=actual_digest) is True
    assert await payloads.get(**identity(), digest=actual_digest) is None
    assert await payloads.release(**identity(), digest=actual_digest) is False
    assert await payloads.put(**identity(), payload=body) == actual_digest


@pytest.mark.asyncio
async def test_wrong_digest_and_tampered_bytes_fail_closed() -> None:
    redis = StatefulRedis()
    payloads = store(redis)
    body = b"expected-body"
    actual_digest = await payloads.put(**identity(), payload=body)

    assert await payloads.get(**identity(), digest=digest(b"other")) is None
    key = next(iter(redis.values))
    redis.values[key] = base64.b64encode(b"tampered-body").decode("ascii")
    with pytest.raises(DigestPayloadIntegrityError) as caught:
        await payloads.get(**identity(), digest=actual_digest)
    assert "tampered-body" not in str(caught.value)
    with pytest.raises(DigestPayloadIntegrityError):
        await payloads.exists(**identity(), digest=actual_digest)
    with pytest.raises(DigestPayloadIntegrityError):
        await payloads.release(**identity(), digest=actual_digest)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    ["", "sha256:xyz", "sha256:" + "A" * 64, "sha256:" + "0" * 63, None],
)
async def test_malformed_digest_is_rejected(malformed: Any) -> None:
    payloads = store(StatefulRedis())
    with pytest.raises(DigestPayloadValidationError) as caught:
        await payloads.get(**identity(), digest=malformed)
    assert str(caught.value) == "digest_payload_digest_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        {"company_id": "co_other"},
        {"plugin_id": "mod_other"},
        {"plugin_instance_id": "inst_other"},
    ],
)
async def test_tenant_plugin_and_instance_isolation(scope: dict[str, str]) -> None:
    redis = StatefulRedis()
    payloads = store(redis)
    body = b"one-tenant-only"
    actual_digest = await payloads.put(**identity(), payload=body)
    other = identity(**scope)

    assert await payloads.get(**other, digest=actual_digest) is None
    assert await payloads.exists(**other, digest=actual_digest) is False
    assert await payloads.release(**other, digest=actual_digest) is False
    assert await payloads.get(**identity(), digest=actual_digest) == body


@pytest.mark.asyncio
async def test_ttl_expiry_and_release_lifecycle() -> None:
    redis = StatefulRedis()
    payloads = store(redis, ttl_seconds=7)
    actual_digest = await payloads.put(**identity(), payload=b"short-lived")

    assert redis.expiry == {next(iter(redis.values)): 7}
    redis.advance(6)
    assert await payloads.get(**identity(), digest=actual_digest) == b"short-lived"
    redis.advance(1)
    assert await payloads.get(**identity(), digest=actual_digest) is None
    assert await payloads.exists(**identity(), digest=actual_digest) is False


@pytest.mark.asyncio
async def test_oversize_put_never_reaches_redis() -> None:
    redis = StatefulRedis()
    payloads = store(redis, max_payload_bytes=4)

    with pytest.raises(DigestPayloadTooLargeError) as caught:
        await payloads.put(**identity(), payload=b"12345")

    assert str(caught.value) == "digest_payload_oversize"
    assert redis.set_calls == 0
    assert redis.values == {}


@pytest.mark.asyncio
async def test_backend_error_does_not_leak_raw_payload() -> None:
    class FailingRedis(StatefulRedis):
        async def set(self, *args: Any, **kwargs: Any) -> bool | None:
            raise RuntimeError("redis://raw-secret-body@private-host")

    payloads = store(FailingRedis())
    with pytest.raises(DigestPayloadBackendError) as caught:
        await payloads.put(**identity(), payload=b"raw-secret-body")
    assert str(caught.value) == "digest_payload_put_failed"
    assert "raw-secret-body" not in str(caught.value)
    assert "private-host" not in str(caught.value)


@pytest.mark.asyncio
async def test_concurrent_put_has_one_successful_write() -> None:
    redis = StatefulRedis()
    payloads = store(redis)
    body = b"same-exact-body"

    results = await asyncio.gather(
        *(payloads.put(**identity(), payload=body) for _ in range(16))
    )

    assert results == [digest(body)] * 16
    assert redis.set_calls == 16
    assert redis.successful_writes == 1
    assert len(redis.values) == 1
    assert await payloads.get(**identity(), digest=digest(body)) == body


@pytest.mark.asyncio
async def test_missing_payload_is_none_not_empty_body_substitution() -> None:
    payloads = store(StatefulRedis())
    resolver = DigestPayloadResolverFactory(store=payloads)()
    missing = digest(b"missing")

    assert await resolver.resolve_body_digest(COMPANY, PLUGIN, INSTANCE, missing) is None


@pytest.mark.asyncio
async def test_real_empty_bytes_never_use_fabricated_zero_digest() -> None:
    payloads = store(StatefulRedis())
    actual_digest = await payloads.put(**identity(), payload=b"")

    assert actual_digest == digest(b"")
    assert actual_digest != "sha256:" + "0" * 64
    assert await payloads.get(**identity(), digest=actual_digest) == b""
    assert await payloads.exists(**identity(), digest=actual_digest) is True
    assert await payloads.get(**identity(), digest="sha256:" + "0" * 64) is None


def test_missing_resolver_is_a_typed_failure() -> None:
    with pytest.raises(DigestPayloadResolverNotConfiguredError) as caught:
        require_body_digest_resolver(None)
    assert caught.value.code == "network_payload_resolver_not_configured"


@pytest.mark.asyncio
async def test_network_nonempty_digest_without_resolver_fails_before_transport() -> None:
    class Transport:
        scoped_identity = True
        digest_bound_channel = False
        max_request_bytes = 1024

        async def execute(self, *, identity: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("transport must not be called")

    class Policy:
        async def allows(self, **kwargs: Any) -> bool:
            return True

    adapter = K8sEgressNetworkAdapter(
        transport=Transport(),
        egress=SimpleNamespace(check=lambda *args, **kwargs: None),
        initial_policy=Policy(),
        owns_transport=False,
    )
    request = NetworkEgressRequest(
        host="api.example.com",
        port=443,
        path="/v1/resource",
        method=NetworkHttpMethod.POST,
        body_digest=digest(b"nonempty-request"),
        envelope=CapabilityEnvelope(
            request_id="req_payload",
            company_id=COMPANY,
            subject_id=f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
            plugin_instance_id=INSTANCE,
            artifact_digest=digest(b"artifact"),
            grant_id="grant_payload",
        ),
    )

    with pytest.raises(CapabilityAccessDenied) as caught:
        await adapter.egress(request)
    assert str(caught.value) == "network_payload_resolver_not_configured"


@pytest.mark.asyncio
async def test_install_publishes_singleton_factory_and_close_once() -> None:
    manager = LifespanManager()
    app = SimpleNamespace(state=SimpleNamespace(lifespan_manager=manager))
    redis = StatefulRedis()
    composer = install._Composer(app)
    first = install._publish_digest_payload_store(
        object(), {"redis_client": redis}, composer
    )
    second = install._publish_digest_payload_store(
        object(), {"redis_client": redis}, composer
    )

    assert first is second
    assert app.state.plugin_digest_payload_store is first
    factory = app.state.plugin_body_digest_resolver_factory
    assert factory.store is first
    assert factory()._store is first
    assert factory.create()._store is first

    install._register_digest_payload_shutdown(app, first, composer)
    install._register_digest_payload_shutdown(app, first, composer)
    assert len(manager.resources) == 1
    resource = manager.get("plugin-digest-payload-store")
    assert resource is not None
    assert resource.owner is first

    await resource.shutdown()
    await resource.shutdown()
    await first.aclose()
    assert first.closed is True
    assert redis.close_calls == 0
    with pytest.raises(DigestPayloadClosedError):
        await first.get(**identity(), digest=digest(b"closed"))
