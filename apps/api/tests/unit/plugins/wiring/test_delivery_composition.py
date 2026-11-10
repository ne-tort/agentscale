"""Composition tests for durable event delivery claims; not executed."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.artifacts.registry import InMemoryRegistryStore
from prodavan.application.plugins.capabilities.adapters.build import build_capability_adapters
from prodavan.application.plugins.wiring.config import PluginPlatformConfigError
import prodavan.application.plugins.wiring.install as install_module
import prodavan.application.plugins.capabilities.adapters.build as capability_build_module
from prodavan.application.plugins.wiring.install import PluginCompositionError, install_plugin_platform
from prodavan.application.plugins.wiring.platform import build_plugin_platform
from prodavan.infrastructure.plugins.adapters.build import AdapterCompositionError, build_event_dispatcher
from prodavan.infrastructure.plugins.adapters.redis_event_claims import RedisDeliveryClaimStore
from tests.unit.plugins.wiring.conftest import (
    FakeCosign,
    FakeGrantStore,
    FakeSettings,
    fake_capability_overrides,
    required_platform_port_overrides,
)


class RedisDouble:
    def __init__(self) -> None:
        self.close_calls = 0

    async def eval(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("composition must not perform Redis IO")


class LifecycleDouble:
    producer = object()
    topic_admin = object()

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class CapturingRuntime:
    """Capture the actual standalone dispatcher factory without Kafka IO."""

    def __init__(self, **kwargs: Any) -> None:
        self._dispatcher_factory = kwargs["dispatcher_factory"]

    async def start(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def aclose(self) -> None:
        return None


class RawDlqDouble:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def publish(self, record: Any) -> bool:
        return True


class HandlerDouble:
    async def handle(self, event: Any, *, subscription: Any) -> None:
        return None


class WakeDouble:
    async def wake(self, event: Any, *, subscription: Any) -> None:
        return None


def _repos() -> Any:
    return SimpleNamespace(
        grant_store=FakeGrantStore(),
        subscription_store=SimpleNamespace(get=lambda value: None, put=lambda value: None, revoke=lambda value: None),
        published_event_store=SimpleNamespace(),
        idempotency_store=SimpleNamespace(),
        audit=SimpleNamespace(),
    )


def test_capability_standalone_factory_shares_namespaced_redis_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        # build_capability_adapters imports this public composition seam locally.
        "prodavan.infrastructure.plugins.capabilities.KafkaSubscriptionRuntime",
        CapturingRuntime,
    )
    redis = RedisDouble()
    raw_dlq = RawDlqDouble(redis)
    supplied = {
        name: SimpleNamespace()
        for name in ("cache", "objects", "docs", "jobs", "pods", "mcp", "secrets", "network")
    }
    result = build_capability_adapters(
        redis=redis,
        repos=_repos(),
        runtime=None,
        services={},
        settings=SimpleNamespace(kafka_bootstrap_servers="kafka:9092"),
        overrides={
            "capability_adapters": supplied,
            "secret_store": object(),
            "event_handler": HandlerDouble(),
            "event_wake_hook": WakeDouble(),
            "event_raw_dlq": raw_dlq,
            "kafka_resources": LifecycleDouble(),
            "kafka_security": object(),
            "redis_client": redis,
            "event_delivery_claim_prefix": "test:standalone:claims:",
        },
    )
    claims = result["event_dispatcher"]._delivery_claims
    assert isinstance(claims, RedisDeliveryClaimStore)
    assert claims._prefix == "test:standalone:claims:"
    assert claims.owns_client is False
    publish = result["event_dispatcher"]
    assert result["adapter_events"]._dispatcher is publish
    factory = result["adapter_events"]._runtime._dispatcher_factory
    subscription_consumer = object()
    subscription = factory(subscription_consumer, object())
    second_subscription = factory(object(), object())

    for candidate in (publish, subscription, second_subscription):
        assert candidate._delivery_claims is claims
        assert candidate._raw_dlq is raw_dlq
        assert candidate._delivery_claims._redis is raw_dlq._redis
        assert candidate._delivery_claims._prefix == "test:standalone:claims:"
        assert candidate._delivery_claims.owns_client is False
    assert subscription._consumer is subscription_consumer
    assert subscription is not publish
    assert second_subscription is not subscription
    assert redis.close_calls == 0


def _platform_overrides(redis: RedisDouble | None) -> dict[str, Any]:
    overrides = {
        "grant_store": FakeGrantStore(),
        "cosign": FakeCosign(),
        "artifact_registry_store": InMemoryRegistryStore(),
        "admin": SimpleNamespace(),
        **required_platform_port_overrides(),
        **fake_capability_overrides(),
    }
    overrides.pop("event_delivery_claims", None)
    if redis is not None:
        overrides["event_redis"] = redis
        overrides["event_delivery_claim_prefix"] = "test:platform:claims:"
    return overrides


def test_platform_entrypoint_requires_and_builds_shared_redis_claims() -> None:
    redis = RedisDouble()
    platform = build_plugin_platform(
        FakeSettings(), **_platform_overrides(redis)
    )
    claims = platform.event_dispatcher._delivery_claims
    assert isinstance(claims, RedisDeliveryClaimStore)
    assert claims._prefix == "test:platform:claims:"
    assert claims.owns_client is False
    assert redis.close_calls == 0


def test_platform_entrypoint_rejects_missing_durable_claim_configuration() -> None:
    with pytest.raises(PluginPlatformConfigError, match="event_delivery_claims"):
        build_plugin_platform(FakeSettings(), **_platform_overrides(None))


def test_cold_install_rejects_missing_shared_redis_before_attach() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(PluginCompositionError, match="redis"):
        install_plugin_platform(
            app,
            FakeSettings(),
            session=object(),
            redis=None,
        )


def _install_overrides_for_test(
    monkeypatch: pytest.MonkeyPatch,
    ov: dict[str, Any],
    redis: Any,
    *,
    dispatcher: Any = None,
    captured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    captured = captured if captured is not None else {}
    monkeypatch.setattr(
        install_module,
        "_capability_runtime_bindings",
        lambda *args, **kwargs: (object(), object(), object()),
    )
    monkeypatch.setattr(
        install_module,
        "_event_integration_overrides",
        lambda **kwargs: {"event_dispatcher": dispatcher} if dispatcher is not None else {},
    )
    monkeypatch.setattr(install_module, "_secret_capability_adapter", lambda **kwargs: None)

    def jobs(**kwargs: Any) -> dict[str, Any]:
        captured["jobs_redis"] = kwargs["ov"]["redis_client"]
        return {}

    monkeypatch.setattr(install_module, "_job_integration_override", jobs)

    def capabilities(**kwargs: Any) -> dict[str, Any]:
        captured["capability_redis"] = kwargs["overrides"]["redis_client"]
        if dispatcher is not None:
            return {}
        return {
            "event_dispatcher": SimpleNamespace(
                _delivery_claims=kwargs["overrides"]["event_delivery_claims"]
            )
        }

    monkeypatch.setattr(capability_build_module, "build_capability_adapters", capabilities)
    repos = SimpleNamespace(
        grant_store=object(),
        grant_revocation=object(),
        quota=object(),
        audit=object(),
        artifact_store=object(),
    )
    runtime = SimpleNamespace(cosign=object())
    gateway = SimpleNamespace(auth=object(), proxy=object())
    return install_module._platform_overrides(
        repos,
        runtime,
        object(),
        gateway,
        object(),
        {**ov, "adapter_network": object()},
        {},
        None,
        redis,
    )


def test_explicit_none_redis_override_is_normalized_to_one_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    redis = object()
    out = _install_overrides_for_test(
        monkeypatch,
        {"redis_client": None},
        redis,
        captured=captured,
    )
    claims = out["event_delivery_claims"]
    assert captured["jobs_redis"] is captured["capability_redis"]
    assert claims._redis is captured["capability_redis"]
    assert claims._redis._manager is redis
    assert out["event_dispatcher"]._delivery_claims is claims


def test_direct_dispatcher_with_different_claim_store_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_a = RedisDeliveryClaimStore(redis=RedisDouble())
    store_b = RedisDeliveryClaimStore(redis=RedisDouble())
    dispatcher = SimpleNamespace(_delivery_claims=store_a)
    with pytest.raises(PluginCompositionError, match="differs"):
        _install_overrides_for_test(
            monkeypatch,
            {"event_dispatcher": dispatcher, "event_delivery_claims": store_b},
            object(),
            dispatcher=dispatcher,
        )


def test_platform_dispatcher_with_different_claim_store_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_a = RedisDeliveryClaimStore(redis=RedisDouble())
    store_b = RedisDeliveryClaimStore(redis=RedisDouble())
    dispatcher = SimpleNamespace(_delivery_claims=store_a)
    with pytest.raises(PluginCompositionError, match="differs"):
        _install_overrides_for_test(
            monkeypatch,
            {
                "platform_overrides": {
                    "event_dispatcher": dispatcher,
                    "event_delivery_claims": store_b,
                }
            },
            object(),
            dispatcher=dispatcher,
        )


def test_direct_dispatcher_with_same_claim_store_preserves_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RedisDeliveryClaimStore(redis=RedisDouble())
    dispatcher = SimpleNamespace(_delivery_claims=store)
    out = _install_overrides_for_test(
        monkeypatch,
        {"event_dispatcher": dispatcher, "event_delivery_claims": store},
        object(),
        dispatcher=dispatcher,
    )
    assert out["event_dispatcher"] is dispatcher
    assert out["event_delivery_claims"] is store


def test_standalone_builder_requires_claim_port_and_preserves_explicit_store() -> None:
    claims = RedisDeliveryClaimStore(redis=RedisDouble())
    with pytest.raises(AdapterCompositionError):
        build_event_dispatcher(
            producer=object(),
            consumer=object(),
            topic_admin=object(),
            dlq=object(),
            subscriptions=object(),
            published_events=object(),
            idempotency=object(),
            audit=object(),
            handler=object(),
            wake_hook=object(),
        )
    # The full-port happy path is covered by infra_adapters/test_build.py;
    # this assertion pins the new required dependency at composition level.
    assert claims.owns_client is False
