"""Admin grant/revoke/uninstall pre-delivery ordering tests; not run under freeze."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import SecretPreDeliveryRequest
from prodavan.application.plugins.admin.service import PluginAdminService


class Control:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.deliver_requests: list[SecretPreDeliveryRequest] = []
        self.revoke_requests: list[SecretPreDeliveryRequest] = []

    async def pre_deliver(self, request: SecretPreDeliveryRequest) -> tuple:
        self.deliver_requests.append(request)
        self.events.append("pre_deliver")
        return ()

    async def pre_revoke(self, request: SecretPreDeliveryRequest) -> tuple:
        self.revoke_requests.append(request)
        self.events.append("pre_revoke:" + request.request_id.split(":", 1)[0])
        return ()


def service(harness: Any, control: Any) -> PluginAdminService:
    return PluginAdminService(replace(harness.ports(), secret_delivery=control))


@pytest.mark.asyncio
async def test_pre_delivery_precedes_deployment_and_mint_then_revoke_uninstall_cleanup(harness, grant_args) -> None:
    events: list[str] = []
    control = Control(events)
    admin = service(harness, control)

    original_ensure = harness.activation.ensure_deployment
    original_mint = harness.mint.mint
    original_suspend = harness.activation.suspend
    original_purge = harness.activation.purge_infra

    async def ensure(**kwargs: Any) -> Any:
        events.append("deployment")
        return await original_ensure(**kwargs)

    async def mint(*, request: Any) -> Any:
        events.append("mint")
        return await original_mint(request=request)

    async def suspend(**kwargs: Any) -> Any:
        events.append("suspend")
        return await original_suspend(**kwargs)

    async def purge(**kwargs: Any) -> Any:
        events.append("purge")
        return await original_purge(**kwargs)

    harness.activation.ensure_deployment = ensure
    harness.mint.mint = mint
    harness.activation.suspend = suspend
    harness.activation.purge_infra = purge

    await admin.grant(**grant_args)
    assert events[:3] == ["pre_deliver", "deployment", "mint"]
    request = control.deliver_requests[0]
    assert request.installation.company_id == grant_args["company_id"]
    assert request.manifest_version == grant_args["manifest_version"]
    assert request.artifact_digest == grant_args["artifact_digest"]
    assert "grant_id" not in request.__dataclass_fields__
    assert "envelope" not in request.__dataclass_fields__

    await admin.revoke(
        company_id=grant_args["company_id"], plugin_id=grant_args["plugin_id"]
    )
    revoke_index = events.index("pre_revoke:revoke")
    suspend_index = events.index("suspend")
    assert revoke_index < suspend_index

    await admin.uninstall(
        company_id=grant_args["company_id"], plugin_id=grant_args["plugin_id"]
    )
    uninstall_index = events.index("pre_revoke:uninstall")
    purge_index = events.index("purge")
    assert uninstall_index < purge_index


@pytest.mark.asyncio
async def test_failed_pre_delivery_blocks_deployment_grant_and_token_without_leaking(harness, grant_args) -> None:
    plaintext = "failed-pre-delivery-plaintext-sentinel"

    class Broken:
        async def pre_deliver(self, request: SecretPreDeliveryRequest) -> tuple:
            raise RuntimeError("backend leaked " + plaintext)

        async def pre_revoke(self, request: SecretPreDeliveryRequest) -> tuple:
            return ()

    admin = service(harness, Broken())
    with pytest.raises(RuntimeError, match="pre-delivery failed") as caught:
        await admin.grant(**grant_args)

    assert plaintext not in str(caught.value)
    assert harness.activation.deployments == {}
    assert harness.grant_store.grants == {}
    assert harness.mint.counter == 0
