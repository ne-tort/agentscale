"""SandboxClientResource unit tests (Wave 1 / PR-3).

The SDK (k8s-agent-sandbox) is NOT a test dependency — startup is exercised
with the import mocked out, matching the repo's MagicMock/AsyncMock style.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import prodavan.core.infra.sandbox_client as sandbox_client_module
from prodavan.core.infra.sandbox_client import (
    SandboxClientResource,
    get_sandbox_client_manager,
    sandbox_client_resource_from_settings,
)


def _make_resource(enabled: bool) -> SandboxClientResource:
    return SandboxClientResource(
        enabled=enabled,
        router_url="http://sandbox-router-svc.agent-sandbox-system.svc:8080",
        namespace="prodavan-sandboxes",
        warmpool="prodavan-agent-pool",
        shutdown_ttl_sec=604800,
    )


@pytest.mark.asyncio
async def test_startup_inert_when_not_sandbox_mode() -> None:
    resource = _make_resource(enabled=False)
    with patch.object(sandbox_client_module, "_SDK_IMPORT_ERROR", None):
        await resource.startup()
    assert resource.client is None
    assert await resource.health() is None
    assert get_sandbox_client_manager() is resource
    await resource.shutdown()
    assert get_sandbox_client_manager() is None


@pytest.mark.asyncio
async def test_startup_creates_sdk_client_when_enabled() -> None:
    resource = _make_resource(enabled=True)
    client = MagicMock()
    ctor = MagicMock(return_value=client)
    cfg_ctor = MagicMock(return_value=MagicMock())
    with (
        patch.object(sandbox_client_module, "_SDK_IMPORT_ERROR", None),
        patch.object(sandbox_client_module, "AsyncSandboxClient", ctor),
        patch.object(sandbox_client_module, "SandboxDirectConnectionConfig", cfg_ctor),
    ):
        await resource.startup()
    assert resource.client is client
    ctor.assert_called_once()
    cfg_ctor.assert_called_once_with(api_url=resource.router_url)
    assert await resource.health() is True


@pytest.mark.asyncio
async def test_startup_raises_without_sdk() -> None:
    resource = _make_resource(enabled=True)
    with patch.object(sandbox_client_module, "_SDK_IMPORT_ERROR", ImportError("no sdk")):
        with pytest.raises(RuntimeError, match="k8s-agent-sandbox"):
            await resource.startup()
    await resource.shutdown()


@pytest.mark.asyncio
async def test_shutdown_closes_async_client() -> None:
    resource = _make_resource(enabled=True)
    client = MagicMock()
    client.close = MagicMock()  # AsyncSandboxClient.close is async
    ctor = MagicMock(return_value=client)
    with (
        patch.object(sandbox_client_module, "_SDK_IMPORT_ERROR", None),
        patch.object(sandbox_client_module, "AsyncSandboxClient", ctor),
        patch.object(
            sandbox_client_module,
            "SandboxDirectConnectionConfig",
            MagicMock(return_value=MagicMock()),
        ),
    ):
        await resource.startup()
        await resource.shutdown()
    client.close.assert_called_once()


def test_from_settings_maps_settings() -> None:
    resource = sandbox_client_resource_from_settings()
    assert isinstance(resource, SandboxClientResource)
    assert resource.namespace == "prodavan-sandboxes"
    assert resource.warmpool == "prodavan-agent-pool"
    assert resource.shutdown_ttl_sec > 0
    assert resource.router_url.startswith("http")
