"""Running-loop coverage for real SQL-store async artifact lookups.

These tests use the real registry and SqlArtifactRegistryStore with a session
double. They deliberately run inside asyncio so a sync SQL getter would hit the
store's explicit running-loop denial instead of passing accidentally.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressPolicy,
    GrantStatus,
    PluginInstanceRecord,
    PluginInstanceStatus,
    QuotaSet,
    SecretPreDeliveryRequest,
)
from prodavan.application.plugins.artifacts.registry import PluginArtifactRegistry
from prodavan.application.plugins.artifacts.verification import manifest_fingerprint
from prodavan.application.plugins.capabilities.adapters.secrets import (
    ControlPlaneSecretDelivery,
    SignedManifestSecretDeliveryPolicy,
)
from prodavan.application.plugins.capabilities.contracts import CapabilityEnvelope
from prodavan.application.plugins.mcp.discovery import ArtifactGrantMcpManifestSource
from prodavan.application.plugins.wiring.install import _gateway_grants
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import (
    SqlArtifactRegistryStore,
)
from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
    GrantAdapter,
    GrantRecord,
    InMemoryGrantStore,
)
from prodavan.infrastructure.plugins.runtime.secret_projection import (
    SecretDeliveryReference,
)

from .repositories.conftest import DIGEST, NOW, FakeSession
from .repositories.test_artifacts import _artifact_row

pytestmark = pytest.mark.asyncio

COMPANY = "co_123"
PLUGIN = "mod_equipment"
INSTANCE = "inst_async"
VERSION = "1.0.0"
REF = (
    "vault://ai_keys/plugins/capabilities/secrets/"
    f"{COMPANY}/{PLUGIN}/{INSTANCE}/api-key"
)


def _registry(manifest: dict[str, Any]) -> tuple[PluginArtifactRegistry, FakeSession]:
    row = _artifact_row(manifest=manifest)
    evidence = dict(row.verification_evidence)
    evidence["manifest_fingerprint"] = manifest_fingerprint(manifest)
    row.verification_evidence = evidence
    projection = dict(row.app_version_projection)
    projection["verification_evidence"] = evidence
    row.app_version_projection = projection
    session = FakeSession(rows=[row])
    store = SqlArtifactRegistryStore(session=session, clock=lambda: NOW)
    return PluginArtifactRegistry(store=store, verifier=object()), session


def _grant() -> Any:
    return SimpleNamespace(
        grant_id="grant_1",
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        artifact_digest=DIGEST,
        manifest_version=VERSION,
        capabilities=("secrets",),
        status=GrantStatus.ACTIVE,
        is_active=True,
        revoked_at=None,
        expires_at=None,
    )


class _Grants:
    async def get_by_grant_id(self, *, grant_id: str) -> Any:
        assert grant_id == "grant_1"
        return _grant()


def _claims() -> Any:
    return SimpleNamespace(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        artifact_digest=DIGEST,
        manifest_version=VERSION,
        grant_id="grant_1",
        sub=f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
    )


async def test_mcp_source_uses_real_store_async_get_in_running_loop() -> None:
    manifest = {"plugin_id": PLUGIN, "version": VERSION}
    registry, _session = _registry(manifest)
    source = ArtifactGrantMcpManifestSource(
        grant_store=_Grants(), artifact_registry=registry
    )

    signed = await source.load(claims=_claims())

    assert signed is not None
    assert signed.manifest == manifest
    assert signed.binding.artifact_digest == DIGEST


async def test_signed_secret_policy_uses_real_store_async_get_in_running_loop() -> None:
    manifest = {
        "plugin_id": PLUGIN,
        "version": VERSION,
        "secret_delivery": {"mode": "k8s_secret", "allowlist": [REF]},
    }
    registry, _session = _registry(manifest)
    policy = SignedManifestSecretDeliveryPolicy(
        artifact_registry=registry, grant_store=_Grants()
    )
    envelope = CapabilityEnvelope(
        request_id="req-async-secret",
        company_id=COMPANY,
        subject_id=f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
        plugin_instance_id=INSTANCE,
        artifact_digest=DIGEST,
        grant_id="grant_1",
    )

    await policy.authorize(envelope=envelope, plugin_id=PLUGIN, secret_ref=REF)


class _SecretStore:
    def get(self, secret_ref: str) -> str:
        assert secret_ref == REF
        return "async-test-value"


class _Projection:
    async def reconcile_secret_values(self, **kwargs: Any) -> tuple[SecretDeliveryReference, ...]:
        return tuple(
            SecretDeliveryReference(
                name="async-secret",
                namespace="agentscale-dev",
                key="SECRET_" + hashlib.sha256(ref.encode()).hexdigest().upper(),
                labels={},
                value_digest="sha256:" + hashlib.sha256(value.encode()).hexdigest(),
                version="sha256:" + hashlib.sha256(value.encode()).hexdigest(),
                delivered=True,
            )
            for ref, value in kwargs["values"].items()
        )

    async def revoke_secret(self, **kwargs: Any) -> SecretDeliveryReference:
        raise AssertionError("not used by this lookup test")


async def test_control_plane_uses_real_store_async_get_in_running_loop() -> None:
    manifest = {
        "plugin_id": PLUGIN,
        "version": VERSION,
        "secret_delivery": {"mode": "k8s_secret", "allowlist": [REF]},
    }
    registry, _session = _registry(manifest)
    service = ControlPlaneSecretDelivery(
        store=_SecretStore(),
        projection=_Projection(),
        artifact_registry=registry,
    )
    installation = PluginInstanceRecord(
        plugin_instance_id=INSTANCE,
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        status=PluginInstanceStatus.INSTALLED,
        tenant_db_name="tenant_async",
        bucket_name="tenant-async",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    request = SecretPreDeliveryRequest(
        request_id="req-async-control",
        installation=installation,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
    )

    delivered = await service.pre_deliver(request)

    assert len(delivered) == 1
    assert delivered[0].secret_ref == REF


async def test_grant_adapter_uses_real_store_async_get_in_running_loop() -> None:
    manifest = {
        "plugin_id": PLUGIN,
        "version": VERSION,
        "protocol": "plugin-rpc/1",
        "rpc_operations": [],
    }
    registry, _session = _registry(manifest)
    record = GrantRecord(
        grant_id="grant_1",
        plugin_id=PLUGIN,
        company_id=COMPANY,
        plugin_instance_id=INSTANCE,
        artifact_digest=DIGEST,
        manifest_version=VERSION,
    )
    adapter = GrantAdapter(
        InMemoryGrantStore([record]), artifact_registry=registry
    )

    assert await adapter.rpc_operations(plugin_id=PLUGIN, company_id=COMPANY) == ()


async def test_sql_gateway_wiring_awaits_real_store_async_get() -> None:
    manifest = {
        "plugin_id": PLUGIN,
        "version": VERSION,
        "protocol": "plugin-rpc/1",
        "webhooks": ["/webhook/async"],
        "public_pages": [],
    }
    registry, _session = _registry(manifest)
    grant = CapabilityGrantRecord(
        grant_id="grant_1",
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        capabilities=("cache",),
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(),
        status=GrantStatus.ACTIVE,
        created_at=NOW,
    )

    class SqlGrants:
        async def get(self, *, company_id: str, plugin_id: str) -> Any:
            assert (company_id, plugin_id) == (COMPANY, PLUGIN)
            return grant

    adapter = _gateway_grants(
        SimpleNamespace(grant_store=SqlGrants()),
        {"artifact_registry": registry},
    )

    assert adapter is not None
    assert await adapter.webhook_routes(
        plugin_id=PLUGIN, company_id=COMPANY
    ) == frozenset({"/webhook/async"})
