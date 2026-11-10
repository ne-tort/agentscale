"""Token-free control-plane pre-delivery tests; intentionally not run under freeze."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    PluginInstanceRecord,
    PluginInstanceStatus,
    SecretPreDeliveryRequest,
)
from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
    CapabilityBackendError,
    digest_text,
)
from prodavan.application.plugins.capabilities.adapters.secrets import (
    ControlPlaneSecretDelivery,
)
from prodavan.infrastructure.plugins.runtime.secret_projection import SecretDeliveryReference

COMPANY = "co_123"
PLUGIN = "mod_x"
INSTANCE = "inst_abc"
VERSION = "1.2.3"
NEXT_VERSION = "1.3.0"
DIGEST = "sha256:" + "a" * 64
NEXT_DIGEST = "sha256:" + "b" * 64
REF = f"vault://ai_keys/plugins/capabilities/secrets/{COMPANY}/{PLUGIN}/{INSTANCE}/api-key"
OTHER_REF = f"vault://ai_keys/plugins/capabilities/secrets/{COMPANY}/{PLUGIN}/{INSTANCE}/other"
PLAINTEXT = "control-plane-pre-delivery-plaintext"
VAULT_TOKEN = "control-plane-vault-token-never-downstream"


def installation() -> PluginInstanceRecord:
    now = datetime.now(UTC)
    return PluginInstanceRecord(
        plugin_instance_id=INSTANCE,
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        status=PluginInstanceStatus.INSTALLED,
        tenant_db_name="tenant_co_123",
        bucket_name="tenant-co-123",
        created_at=now,
        updated_at=now,
    )


def request(
    *,
    version: str = VERSION,
    digest: str = DIGEST,
    operation: str = "grant",
) -> SecretPreDeliveryRequest:
    return SecretPreDeliveryRequest(
        request_id=f"{operation}:req-1",
        installation=installation(),
        manifest_version=version,
        artifact_digest=digest,
    )


def artifact(
    *,
    version: str = VERSION,
    digest: str = DIGEST,
    refs: tuple[str, ...] | None = (REF, OTHER_REF),
    verified: bool = True,
    published: bool = True,
) -> Any:
    manifest = {} if refs is None else {
        "secret_delivery": {"mode": "k8s_secret", "allowlist": list(refs)}
    }
    return SimpleNamespace(
        plugin_id=PLUGIN,
        version=version,
        digest=digest,
        published=published,
        quarantine=None if published else SimpleNamespace(reason="quarantined"),
        verification=(
            SimpleNamespace(artifact_digest=digest) if verified else None
        ),
        manifest=manifest,
    )


class Artifacts:
    def __init__(self, *records: Any) -> None:
        self.records = {record.version: record for record in records}
        self.calls: list[tuple[str, str]] = []

    async def get_async(self, plugin_id: str, version: str) -> Any:
        self.calls.append((plugin_id, version))
        return self.records.get(version)


class Store:
    def __init__(self) -> None:
        self.values = {REF: PLAINTEXT, OTHER_REF: "other-control-value"}
        self.get_calls: list[str] = []

    def get(self, secret_ref: str) -> str:
        self.get_calls.append(secret_ref)
        return self.values[secret_ref]


class Projection:
    def __init__(self) -> None:
        self.reconcile_calls: list[dict[str, Any]] = []
        self.revoke_calls: list[dict[str, str]] = []

    @staticmethod
    def key(secret_ref: str) -> str:
        return "SECRET_" + hashlib.sha256(secret_ref.encode()).hexdigest().upper()

    def reference(self, secret_ref: str, digest: str, delivered: bool) -> SecretDeliveryReference:
        return SecretDeliveryReference(
            name="plugin-capability-secrets-mod-x-inst-abc",
            namespace="agentscale-dev",
            key=self.key(secret_ref),
            labels={},
            value_digest=digest,
            version=digest,
            delivered=delivered,
        )

    async def reconcile_secret_values(self, **kwargs: Any) -> tuple[SecretDeliveryReference, ...]:
        self.reconcile_calls.append(kwargs)
        return tuple(
            self.reference(ref, digest_text(value), True)
            for ref, value in kwargs["values"].items()
        )

    async def revoke_secret(self, **kwargs: str) -> SecretDeliveryReference:
        self.revoke_calls.append(kwargs)
        return self.reference(kwargs["secret_ref"], digest_text(""), False)


def control(
    artifacts: Artifacts,
    projection: Projection,
    store: Store,
    **kwargs: Any,
) -> ControlPlaneSecretDelivery:
    return ControlPlaneSecretDelivery(
        store=store,
        projection=projection,
        artifact_registry=artifacts,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_pre_deliver_and_revoke_use_exact_approved_refs_without_plugin_token() -> None:
    artifacts = Artifacts(artifact())
    projection = Projection()
    store = Store()
    service = control(artifacts, projection, store)
    req = request()

    delivered = await service.pre_deliver(req)

    assert [item.secret_ref for item in delivered] == [REF, OTHER_REF]
    assert all(item.delivered and item.version.startswith("sha256:") for item in delivered)
    assert store.get_calls == [REF, OTHER_REF]
    assert projection.reconcile_calls[0]["values"] == {
        REF: PLAINTEXT, OTHER_REF: "other-control-value"
    }
    assert projection.reconcile_calls[0]["company_id"] == COMPANY
    assert projection.reconcile_calls[0]["plugin_id"] == PLUGIN
    assert projection.reconcile_calls[0]["plugin_instance_id"] == INSTANCE
    assert "envelope" not in req.__dataclass_fields__
    assert "grant_id" not in req.__dataclass_fields__
    assert "subject_id" not in req.__dataclass_fields__
    for surface in (repr(delivered), repr(req)):
        assert PLAINTEXT not in surface
        assert VAULT_TOKEN not in surface

    revoked = await service.pre_revoke(req)
    assert [item.secret_ref for item in revoked] == [REF, OTHER_REF]
    assert all(not item.delivered for item in revoked)
    assert [call["secret_ref"] for call in projection.revoke_calls] == [REF, OTHER_REF]
    assert store.get_calls == [REF, OTHER_REF]


@pytest.mark.asyncio
async def test_update_pin_pre_delivers_new_artifact_but_revoke_requires_current_installation() -> None:
    artifacts = Artifacts(artifact(), artifact(version=NEXT_VERSION, digest=NEXT_DIGEST))
    projection = Projection()
    store = Store()
    service = control(artifacts, projection, store)
    update = request(version=NEXT_VERSION, digest=NEXT_DIGEST, operation="update")

    delivered = await service.pre_deliver(update)
    assert len(delivered) == 2
    assert artifacts.calls[-1] == (PLUGIN, NEXT_VERSION)

    with pytest.raises(CapabilityAccessDenied, match="installation_pin_mismatch"):
        await service.pre_revoke(update)
    assert projection.revoke_calls == []


@pytest.mark.asyncio
async def test_unapproved_artifact_wrong_tenant_and_size_cap_fail_before_projection() -> None:
    projection = Projection()
    store = Store()

    unverified = control(Artifacts(artifact(verified=False)), projection, store)
    with pytest.raises(CapabilityAccessDenied, match="artifact_not_approved"):
        await unverified.pre_deliver(request())
    assert store.get_calls == []
    assert projection.reconcile_calls == []

    foreign_ref = REF.replace(COMPANY, "co_other")
    wrong_tenant = control(Artifacts(artifact(refs=(foreign_ref,))), projection, store)
    with pytest.raises(CapabilityAccessDenied, match="outside_plugin_scope"):
        await wrong_tenant.pre_deliver(request())
    assert store.get_calls == []

    capped = control(Artifacts(artifact()), projection, store, max_secret_bytes=8)
    with pytest.raises(CapabilityBackendError, match="secret_value_exceeds_cap") as caught:
        await capped.pre_deliver(request())
    assert PLAINTEXT not in str(caught.value)
    assert projection.reconcile_calls == []


@pytest.mark.asyncio
async def test_revoke_after_quarantine_cleans_installation_but_new_delivery_is_denied() -> None:
    projection = Projection()
    store = Store()
    service = control(
        Artifacts(artifact(published=False)), projection, store
    )

    with pytest.raises(CapabilityAccessDenied, match="artifact_not_approved"):
        await service.pre_deliver(request())
    revoked = await service.pre_revoke(request())
    assert len(revoked) == 2
    assert store.get_calls == []
    assert len(projection.revoke_calls) == 2


@pytest.mark.asyncio
async def test_absent_declaration_is_noop_but_still_requires_approved_artifact() -> None:
    projection = Projection()
    store = Store()
    service = control(Artifacts(artifact(refs=None)), projection, store)

    assert await service.pre_deliver(request()) == ()
    assert await service.pre_revoke(request()) == ()
    assert store.get_calls == []
    assert projection.reconcile_calls == []
    assert projection.revoke_calls == []
