"""SecretStore and optional delivery composition tests; not run under freeze."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters.secrets import (
    ControlPlaneSecretDelivery,
    SecretStoreSecretsAdapter,
    SecretsDeliveryRequest,
)
from prodavan.application.plugins.capabilities.contracts import CapabilityEnvelope
from prodavan.application.plugins.wiring.install import (
    _secret_capability_adapter,
    _secret_delivery_control,
    _shared_secret_store,
)
from prodavan.infrastructure.plugins.runtime.secret_projection import (
    SecretDeliveryReference,
)

DIGEST = "sha256:" + "a" * 64
REF = "vault://ai_keys/plugins/capabilities/secrets/co_123/mod_x/inst_abc/api-key"


class Store:
    def __init__(self) -> None:
        self.values = {REF: "composition-secret"}
        self.close_calls = 0

    def get(self, secret_ref: str) -> str:
        return self.values[secret_ref]

    def put(self, key_id: str, secret: str) -> str:
        self.values[key_id] = secret
        return key_id

    def delete(self, secret_ref: str) -> None:
        self.values.pop(secret_ref, None)

    def close(self) -> None:
        self.close_calls += 1


class Projection:
    async def deliver_secret(self, **kwargs: str) -> SecretDeliveryReference:
        digest = "sha256:" + "b" * 64
        return SecretDeliveryReference(
            name="delivery", namespace="agentscale-dev", key="SECRET_" + "A" * 64,
            labels={}, value_digest=digest, version=digest, delivered=True,
        )

    async def revoke_secret(self, **kwargs: str) -> SecretDeliveryReference:
        digest = "sha256:" + "b" * 64
        return SecretDeliveryReference(
            name="delivery", namespace="agentscale-dev", key="SECRET_" + "A" * 64,
            labels={}, value_digest=digest, version=digest, delivered=False,
        )


class ReconcileProjection(Projection):
    async def reconcile_secret_values(self, **kwargs: Any) -> tuple:
        return ()


class Policy:
    async def authorize(self, **kwargs: Any) -> None:
        return None


def repos() -> Any:
    return SimpleNamespace(grant_store=object())


def envelope() -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req-composition",
        company_id="co_123",
        subject_id="plugin:mod_x:co_123:inst_abc",
        plugin_instance_id="inst_abc",
        artifact_digest=DIGEST,
        grant_id="grant_01",
    )


def test_missing_shared_store_is_typed_unwired_without_file_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prodavan.infrastructure.secrets.store as store_module
    from prodavan.application.plugins.capabilities.adapters._shared import (
        CapabilityConfigurationError,
    )

    monkeypatch.setattr(store_module, "get_secret_store", lambda: None)
    with pytest.raises(CapabilityConfigurationError, match="secret_store_unwired"):
        _shared_secret_store({})


def test_existing_routing_store_is_reused_and_delivery_projection_is_shared() -> None:
    store = Store()
    projection = ReconcileProjection()
    policy = Policy()
    assert _shared_secret_store({"secret_store": store}) is store

    secrets = _secret_capability_adapter(
        repos=repos(),
        ov={
            "secret_store": store,
            "secret_delivery_policy": policy,
        },
        services={"secret_projection": projection},
    )
    assert isinstance(secrets, SecretStoreSecretsAdapter)
    assert secrets._store is store
    assert secrets._projection is projection
    assert secrets._delivery_policy is policy
    # The default objects adapter is the sole owner of the shared store.
    assert secrets._owned_resources == ()

    async def get_async(plugin_id: str, version: str) -> None:
        return None

    registry = SimpleNamespace(
        get=lambda plugin_id, version: None,
        get_async=get_async,
    )
    control = _secret_delivery_control(
        store=store, projection=projection, artifact_registry=registry
    )
    assert isinstance(control, ControlPlaneSecretDelivery)
    assert control._store is store
    assert control._projection is projection
    assert control._artifacts is registry


@pytest.mark.asyncio
async def test_without_k8s_delivery_config_read_works_and_delivery_is_unwired() -> None:
    store = Store()
    secrets = _secret_capability_adapter(
        repos=repos(), ov={"secret_store": store}, services={}
    )
    from prodavan.application.plugins.capabilities.contracts import SecretsReadRequest
    from prodavan.application.plugins.capabilities.adapters._shared import (
        CapabilityConfigurationError,
    )

    result = await secrets.read(SecretsReadRequest(secret_ref=REF, envelope=envelope()))
    assert result.secret_ref == REF
    with pytest.raises(CapabilityConfigurationError, match="secret_delivery_unwired"):
        await secrets.deliver(SecretsDeliveryRequest(secret_ref=REF, envelope=envelope()))


@pytest.mark.asyncio
async def test_single_store_owner_closes_once_when_objects_does_not_own_it() -> None:
    store = Store()
    secrets = _secret_capability_adapter(
        repos=repos(),
        ov={"secret_store": store, "own_object_resources": False},
        services={},
    )
    assert secrets._owned_resources == (store,)
    await secrets.aclose()
    await secrets.aclose()
    assert store.close_calls == 1

    external_objects = Store()
    secrets = _secret_capability_adapter(
        repos=repos(),
        ov={"secret_store": store, "adapter_objects": external_objects},
        services={},
    )
    assert secrets._owned_resources == (store,)
