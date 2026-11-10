"""Main/standalone scoped activation wiring tests; not executed during freeze."""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.jobs.declarations import (
    PluginJobComposition,
    build_standalone_plugin_job_composition,
)
from prodavan.application.plugins.wiring.install import (
    _deployment_admin,
    _zone_services,
)
from prodavan.infrastructure.plugins.runtime.build import build_runtime_ports
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from tests.unit.plugins.runtime.conftest import (
    FakeBackendSender,
    FakeCosignRunner,
    FakeK8sApi,
    FakeProbe,
)


class Provider:
    async def get_token(self) -> str:
        return "scoped-main-test-token"


class Lookup:
    async def by_digest_async(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def get(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def get_by_id(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class Projection:
    async def ensure_instance_secret(self, **_kwargs: Any) -> None:
        return None


def test_main_wake_suspend_and_revoke_share_exact_scoped_identity(
    monkeypatch: pytest.MonkeyPatch,
    enabled_config: Any,
) -> None:
    lifecycle_api = FakeK8sApi()
    provider = Provider()
    runtime = build_runtime_ports(
        object(),
        config=RuntimeConfig(namespace="agentscale-dev"),
        lifecycle_api=lifecycle_api,
        token_provider=provider,
        cosign_runner=FakeCosignRunner(),
        backend_http=FakeBackendSender(),
        http_probe=FakeProbe(),
    )
    monkeypatch.setattr(
        "prodavan.application.plugins.wiring.install._activation_mint",
        lambda _repos, _config: object(),
    )
    repos = SimpleNamespace(
        artifact_store=Lookup(),
        instance_store=Lookup(),
        audit=object(),
    )
    overrides = {
        "tenant_db_secrets": object(),
        "object_buckets": object(),
        "tenant_db_admin": object(),
        "activation_mint": object(),
    }
    unwired: list[str] = []

    services = _zone_services(runtime, repos, enabled_config, overrides, unwired)
    activator = services["activator"]
    admin = _deployment_admin(
        runtime,
        repos,
        repos.instance_store,
        secret_projection=Projection(),
    )

    # Admin wake and activator suspend use these exact scale/readiness/CAS ports.
    assert activator._scale is runtime.scale
    assert activator._readiness is runtime.readiness
    assert activator._store is runtime.state_store
    assert activator._scale.api is lifecycle_api
    assert activator._readiness.api is lifecycle_api
    assert activator._store._api is lifecycle_api
    # Admin revoke/suspend/uninstall uses the very same scoped API, not a mounted
    # fallback and not a second lifecycle identity.
    assert admin.api is lifecycle_api
    assert runtime.lifecycle_api is lifecycle_api
    assert runtime.scale.api is runtime.state_store._api is lifecycle_api
    assert unwired == []


def test_standalone_factory_uses_runtime_scoped_ports_and_single_owner() -> None:
    source = inspect.getsource(build_standalone_plugin_job_composition)
    for required in (
        "scale=runtime.scale",
        "readiness=runtime.readiness",
        "state_store=runtime.state_store",
        "api=runtime.lifecycle_api",
        "token_provider=runtime.token_provider",
        "runtime=runtime",
        "owns_resources=True",
    ):
        assert required in source

    close_source = inspect.getsource(PluginJobComposition.aclose)
    assert "if self._closed or not self._owns_resources:" in close_source
    assert "self._runtime" in close_source
    assert "close = getattr(resource, \"aclose\", None)" in close_source
