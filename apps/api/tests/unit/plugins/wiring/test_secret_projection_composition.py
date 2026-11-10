"""SecretProjection composition and lifecycle rollback tests (not run: freeze)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from prodavan.application.plugins.wiring.install import (
    RUNTIME_PORTS_STATE_KEY,
    RUNTIME_SHUTDOWN_STATE_KEY,
    _Composer,
    _UnwiredPort,
    _register_runtime_shutdown,
    _secret_projection,
)
from prodavan.core.lifespan.manager import LifespanManager
from prodavan.infrastructure.plugins.runtime.config import (
    InternalGatewayUrl,
    RuntimeConfig,
)
from prodavan.infrastructure.plugins.runtime.secret_projection import K8sSecretProjection

GATEWAY_URL = "http://plugin-gateway.agentscale-dev.svc:8080"


class Secrets:
    async def get(self, *, key: str) -> None:
        return None


class Api:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> None:
        self.calls.append({"method": method, "path": path, **kwargs})
        return None


def test_projection_uses_runtime_config_tenant_port_and_shared_lifecycle_api() -> None:
    config = RuntimeConfig(namespace="agentscale-dev", gateway_url=GATEWAY_URL)
    api = Api()
    secrets = Secrets()
    runtime = SimpleNamespace(config=config, lifecycle_api=api)
    projection = _secret_projection(runtime, secrets)
    assert isinstance(projection, K8sSecretProjection)
    assert projection.api is api
    assert projection._config is config
    assert projection._tenant_secrets is secrets
    assert isinstance(projection._gateway_url, InternalGatewayUrl)
    assert projection._gateway_url == GATEWAY_URL


def test_missing_or_invalid_gateway_url_leaves_typed_unwired_projection() -> None:
    api = Api()
    secrets = Secrets()
    for value in (None, "https://public.example.com", "http://user:secret@gateway"):
        config = RuntimeConfig(namespace="agentscale-dev", gateway_url=value)
        projection = _secret_projection(
            SimpleNamespace(config=config, lifecycle_api=api), secrets
        )
        assert isinstance(projection, _UnwiredPort)
        assert projection._name == "secret_projection"
    assert api.calls == []


def test_missing_shared_lifecycle_api_cannot_fall_back_to_mounted_identity() -> None:
    config = RuntimeConfig(namespace="agentscale-dev", gateway_url=GATEWAY_URL)
    projection = _secret_projection(SimpleNamespace(config=config), Secrets())
    assert isinstance(projection, _UnwiredPort)


def test_runtime_shutdown_registration_is_single_and_app_state_rolls_back() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    manager = LifespanManager()
    app = SimpleNamespace(state=SimpleNamespace(lifespan_manager=manager))
    runtime = Runtime()
    composer = _Composer(app)
    _register_runtime_shutdown(app, runtime, composer)
    _register_runtime_shutdown(app, runtime, composer)
    assert len(manager.resources) == 1
    resource = manager.get("plugin-runtime")
    assert resource is not None
    assert resource.owner is runtime
    assert getattr(app.state, RUNTIME_PORTS_STATE_KEY) is runtime
    assert getattr(app.state, RUNTIME_SHUTDOWN_STATE_KEY) is True

    composer.rollback()
    assert manager.get("plugin-runtime") is None
    assert not hasattr(app.state, RUNTIME_PORTS_STATE_KEY)
    assert not hasattr(app.state, RUNTIME_SHUTDOWN_STATE_KEY)
