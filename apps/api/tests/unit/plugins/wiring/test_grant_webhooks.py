"""GrantRecord.webhooks из подписанного manifest."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressPolicy,
    GrantStatus,
    QuotaSet,
)
from prodavan.application.plugins.wiring.install import _gateway_grants, _gateway_secrets

from .conftest import TEST_DIGEST

COMPANY = "co_webhooks"
PLUGIN = "mod_equipment"
VERSION = "1.2.0"


def grant() -> CapabilityGrantRecord:
    from datetime import UTC, datetime

    return CapabilityGrantRecord(
        grant_id="grant_webhooks",
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id="pinst_webhooks",
        manifest_version=VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=("cache",),
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(),
        status=GrantStatus.ACTIVE,
        created_at=datetime(2026, 11, 9, tzinfo=UTC),
    )


class Store:
    async def get(self, *, company_id: str, plugin_id: str) -> Any:
        return grant()


class Registry:
    def __init__(self, *, manifest: Any = None, digest: str = TEST_DIGEST) -> None:
        self.record = SimpleNamespace(
            digest=digest, manifest={"webhooks": ["/webhook/one", "/webhook/two", "/webhook/one"]}
        )
        if manifest is not None:
            self.record.manifest = manifest

    async def get_async(self, *, plugin_id: str, version: str) -> Any:
        return self.record


async def test_webhook_routes_come_from_signed_manifest() -> None:
    adapter = _gateway_grants(
        SimpleNamespace(grant_store=Store()),
        {"artifact_registry": Registry()},
    )
    assert adapter is not None
    routes = await adapter.webhook_routes(plugin_id=PLUGIN, company_id=COMPANY)
    assert routes == frozenset({"/webhook/one", "/webhook/two"})


async def test_webhook_routes_fail_closed_on_manifest_mismatch_or_bad_shape() -> None:
    adapter = _gateway_grants(
        SimpleNamespace(grant_store=Store()),
        {"artifact_registry": Registry(digest="sha256:" + "0" * 64)},
    )
    assert adapter is not None
    with pytest.raises(RuntimeError, match="artifact digest"):
        await adapter.webhook_routes(plugin_id=PLUGIN, company_id=COMPANY)

    bad = _gateway_grants(
        SimpleNamespace(grant_store=Store()),
        {"artifact_registry": Registry(manifest={"webhooks": "not-a-list"})},
    )
    assert bad is not None
    with pytest.raises(RuntimeError, match="webhooks"):
        await bad.webhook_routes(plugin_id=PLUGIN, company_id=COMPANY)


async def test_gateway_secret_resolver_uses_actual_active_grant_ref_only() -> None:
    from datetime import UTC, datetime

    ref = "file://ai_keys/plugins/webhooks/co_webhooks/mod_equipment/WEBHOOK_SECRET.secret"
    sentinel = "unit-test-actual-webhook-secret"
    record = replace(grant(), webhook_secret_ref=ref)

    class ActiveStore:
        async def get(self, *, company_id: str, plugin_id: str) -> Any:
            return record

    adapter = _gateway_secrets(SimpleNamespace(grant_store=ActiveStore()))
    assert adapter is not None
    adapter._secret_reader = lambda actual_ref: {ref: sentinel}[actual_ref]
    assert await adapter.webhook_secret(plugin_id=PLUGIN, company_id=COMPANY) == sentinel

    revoked = replace(
        record,
        status=GrantStatus.REVOKED,
        revoked_at=datetime(2026, 11, 9, tzinfo=UTC),
    )

    class RevokedStore:
        async def get(self, *, company_id: str, plugin_id: str) -> Any:
            return revoked

    revoked_adapter = _gateway_secrets(SimpleNamespace(grant_store=RevokedStore()))
    assert revoked_adapter is not None
    revoked_adapter._secret_reader = lambda _ref: sentinel
    assert await revoked_adapter.webhook_secret(plugin_id=PLUGIN, company_id=COMPANY) is None

    legacy_adapter = _gateway_secrets(SimpleNamespace(grant_store=Store()))
    assert legacy_adapter is not None
    legacy_adapter._secret_reader = lambda _ref: sentinel
    assert await legacy_adapter.webhook_secret(plugin_id=PLUGIN, company_id=COMPANY) is None
