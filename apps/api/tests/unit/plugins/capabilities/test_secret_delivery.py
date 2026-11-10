"""Typed secret delivery contracts with fake stores/projections; not run under freeze."""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
    digest_text,
)
from prodavan.application.plugins.admin.contracts import (
    PluginInstanceRecord,
    PluginInstanceStatus,
    SecretPreDeliveryRequest,
)
from prodavan.application.plugins.capabilities.adapters.secrets import (
    ControlPlaneSecretDelivery,
    SecretStoreSecretsAdapter,
    SecretsDeliveryRequest,
    SecretsRevokeRequest,
    SignedManifestSecretDeliveryPolicy,
)
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    SecretsReadRequest,
)
from prodavan.infrastructure.plugins.runtime.config import DEFAULT_DEPLOYMENT_PREFIX
from prodavan.infrastructure.plugins.runtime.k8s_scale import plugin_resource_name
from prodavan.infrastructure.plugins.runtime.secret_projection import (
    DELIVERY_SECRET_PREFIX,
    SecretDeliveryReference,
    _ownership_labels,
)

DIGEST = "sha256:" + "a" * 64
COMPANY = "co_123"
PLUGIN = "mod_x"
INSTANCE = "inst_abc"
REF = f"vault://ai_keys/plugins/capabilities/secrets/{COMPANY}/{PLUGIN}/{INSTANCE}/api-key"
OTHER_REF = f"vault://ai_keys/plugins/capabilities/secrets/{COMPANY}/{PLUGIN}/{INSTANCE}/other"
PLAINTEXT = "unit-test-delivery-plaintext"
VAULT_TOKEN = "unit-test-control-plane-vault-token"


def envelope(**changes: Any) -> CapabilityEnvelope:
    values = {
        "request_id": "req-delivery-1",
        "company_id": COMPANY,
        "subject_id": f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
        "plugin_instance_id": INSTANCE,
        "artifact_digest": DIGEST,
        "grant_id": "grant_01",
    }
    values.update(changes)
    return CapabilityEnvelope(**values)


class FakeStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {REF: PLAINTEXT})
        self.get_calls: list[str] = []
        self.close_calls = 0

    def get(self, secret_ref: str) -> str:
        self.get_calls.append(secret_ref)
        if secret_ref not in self.values:
            raise LookupError("missing test secret")
        return self.values[secret_ref]

    def put(self, key_id: str, secret: str) -> str:
        self.values[key_id] = secret
        return key_id

    def delete(self, secret_ref: str) -> None:
        self.values.pop(secret_ref, None)

    def close(self) -> None:
        self.close_calls += 1


class ApprovedPolicy:
    def __init__(self, allowed: frozenset[str] = frozenset({REF})) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, str]] = []

    async def authorize(self, *, envelope: CapabilityEnvelope, plugin_id: str, secret_ref: str) -> None:
        self.calls.append((envelope.company_id, plugin_id, secret_ref))
        if secret_ref not in self.allowed:
            raise CapabilityAccessDenied("secret_ref_not_allowlisted")


class FakeProjection:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str, str], str] = {}
        self.digests: dict[tuple[str, str, str, str], str] = {}
        self.delivery_calls: list[dict[str, str]] = []
        self.revoke_calls: list[dict[str, str]] = []
        self.reconcile_calls: list[dict[str, Any]] = []
        self.references: list[SecretDeliveryReference] = []
        self.close_calls = 0

    @staticmethod
    def _key(kwargs: dict[str, str]) -> tuple[str, str, str, str]:
        return (
            kwargs["company_id"], kwargs["plugin_id"],
            kwargs["plugin_instance_id"], kwargs["secret_ref"],
        )

    @staticmethod
    def _reference(kwargs: dict[str, str], digest: str, delivered: bool) -> SecretDeliveryReference:
        secret_ref = kwargs["secret_ref"]
        return SecretDeliveryReference(
            # Canonical scoped name/labels — те же production-функции, что у
            # K8sSecretProjection._delivery_binding (НЕ hardcoded строка).
            name=plugin_resource_name(
                kwargs["plugin_id"],
                kwargs["plugin_instance_id"],
                prefix=DEFAULT_DEPLOYMENT_PREFIX + DELIVERY_SECRET_PREFIX,
            ),
            namespace="agentscale-dev",
            # Canonical data key: ``SECRET_`` + UPPER(SHA256(exact secret_ref)),
            # как K8sSecretProjection._delivery_key и _delivery_data_key в
            # adapters/secrets.py. Ключ вычисляется из kwargs["secret_ref"],
            # поэтому другой ref = другой key (mismatch detectable).
            key="SECRET_" + hashlib.sha256(secret_ref.encode("utf-8")).hexdigest().upper(),
            labels=_ownership_labels(
                kwargs["company_id"],
                kwargs["plugin_id"],
                kwargs["plugin_instance_id"],
            ),
            value_digest=digest,
            version=digest,
            delivered=delivered,
        )

    async def deliver_secret(self, **kwargs: str) -> SecretDeliveryReference:
        self.delivery_calls.append(dict(kwargs))
        key = self._key(kwargs)
        self.values[key] = kwargs["value"]
        digest = digest_text(kwargs["value"])
        self.digests[key] = digest
        reference = self._reference(kwargs, digest, True)
        self.references.append(reference)
        return reference

    async def reconcile_secret_values(self, **kwargs: Any) -> tuple[SecretDeliveryReference, ...]:
        """Control-plane exact-set reconcile с canonical reference на КАЖДЫЙ ref."""

        base = {
            name: kwargs[name]
            for name in ("company_id", "plugin_id", "plugin_instance_id")
        }
        values = dict(kwargs["values"])
        self.reconcile_calls.append({**base, "values": dict(values)})
        references: list[SecretDeliveryReference] = []
        for secret_ref, value in values.items():
            call = {**base, "secret_ref": secret_ref, "value": value}
            key = self._key(call)
            self.values[key] = value
            digest = digest_text(value)
            self.digests[key] = digest
            self.delivery_calls.append(dict(call))
            references.append(self._reference(call, digest, True))
        result = tuple(references)
        self.references.extend(result)
        return result

    async def revoke_secret(self, **kwargs: str) -> SecretDeliveryReference:
        self.revoke_calls.append(dict(kwargs))
        key = self._key(kwargs)
        self.values.pop(key, None)
        digest = self.digests.pop(key, digest_text(""))
        reference = self._reference(kwargs, digest, False)
        self.references.append(reference)
        return reference

    async def aclose(self) -> None:
        self.close_calls += 1


def adapter(
    store: FakeStore,
    projection: FakeProjection | None,
    policy: Any = None,
    **kwargs: Any,
) -> SecretStoreSecretsAdapter:
    return SecretStoreSecretsAdapter(
        store=store,
        projection=projection,
        delivery_policy=policy,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_exact_delivery_is_metadata_only_and_does_not_forward_vault_token(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    store = FakeStore()
    projection = FakeProjection()
    policy = ApprovedPolicy()
    secrets = adapter(store, projection, policy, owns_store=True)

    result = await secrets.deliver(SecretsDeliveryRequest(secret_ref=REF, envelope=envelope()))

    assert result.secret_ref == REF
    assert result.version == digest_text(PLAINTEXT)
    assert result.value_digest == result.version
    assert result.digest.startswith("sha256:")
    assert result.mode == "k8s_secret"
    assert result.delivered is True
    assert projection.delivery_calls == [{
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "plugin_instance_id": INSTANCE,
        "secret_ref": REF,
        "value": PLAINTEXT,
    }]
    assert policy.calls == [(COMPANY, PLUGIN, REF)]
    # Canonical projection binding: scoped name/labels и SHA256 data key.
    reference = projection.references[-1]
    assert reference.name == plugin_resource_name(
        PLUGIN, INSTANCE, prefix=DEFAULT_DEPLOYMENT_PREFIX + DELIVERY_SECRET_PREFIX
    )
    assert (
        reference.key
        == "SECRET_" + hashlib.sha256(REF.encode("utf-8")).hexdigest().upper()
    )
    assert reference.key != (
        "SECRET_" + hashlib.sha256(OTHER_REF.encode("utf-8")).hexdigest().upper()
    ), "другой secret_ref обязан давать другой canonical data key"
    assert dict(reference.labels) == _ownership_labels(COMPANY, PLUGIN, INSTANCE)
    for surface in (repr(result), str(result), caplog.text, repr(projection.delivery_calls[0]["secret_ref"])):
        assert PLAINTEXT not in surface
        assert VAULT_TOKEN not in surface
    assert "value" not in result.__dataclass_fields__
    assert "plaintext" not in result.__dataclass_fields__
    assert "token" not in result.__dataclass_fields__

    revoked = await secrets.revoke(SecretsRevokeRequest(secret_ref=REF, envelope=envelope()))
    assert revoked.delivered is False
    assert projection.references[-1].delivered is False
    assert projection.references[-1].key == reference.key
    assert revoked.version == result.version
    assert store.values[REF] == PLAINTEXT
    assert PLAINTEXT not in repr(revoked)

    await secrets.aclose()
    await secrets.aclose()
    assert store.close_calls == 1
    assert projection.close_calls == 0


@pytest.mark.asyncio
async def test_digest_read_remains_available_when_delivery_is_typed_unwired() -> None:
    store = FakeStore()
    secrets = adapter(store, None)

    result = await secrets.read(SecretsReadRequest(secret_ref=REF, envelope=envelope()))
    assert result.value_digest == digest_text(PLAINTEXT)
    with pytest.raises(CapabilityConfigurationError, match="secret_delivery_unwired"):
        await secrets.deliver(SecretsDeliveryRequest(secret_ref=REF, envelope=envelope()))
    assert store.get_calls == [REF]


@pytest.mark.asyncio
async def test_allowlist_wrong_tenant_and_size_cap_fail_before_side_effects() -> None:
    store = FakeStore({REF: PLAINTEXT, OTHER_REF: "other-value"})
    projection = FakeProjection()
    secrets = adapter(store, projection, ApprovedPolicy())

    with pytest.raises(CapabilityAccessDenied, match="secret_ref_not_allowlisted"):
        await secrets.deliver(SecretsDeliveryRequest(secret_ref=OTHER_REF, envelope=envelope()))
    assert store.get_calls == []
    assert projection.delivery_calls == []

    foreign = REF.replace(COMPANY, "co_other")
    with pytest.raises(CapabilityAccessDenied, match="secret_ref_outside_plugin_scope"):
        await secrets.deliver(SecretsDeliveryRequest(secret_ref=foreign, envelope=envelope()))
    assert store.get_calls == []

    noncanonical = REF + "/nested"
    with pytest.raises(CapabilityAccessDenied, match="secret_ref_not_canonical"):
        await secrets.deliver(SecretsDeliveryRequest(secret_ref=noncanonical, envelope=envelope()))
    assert store.get_calls == []

    capped = adapter(store, projection, ApprovedPolicy(), max_secret_bytes=8)
    with pytest.raises(CapabilityBackendError, match="secret_value_exceeds_cap") as caught:
        await capped.deliver(SecretsDeliveryRequest(secret_ref=REF, envelope=envelope()))
    assert PLAINTEXT not in str(caught.value)
    assert projection.delivery_calls == []


@pytest.mark.asyncio
async def test_signed_manifest_allowlist_is_bound_to_grant_and_verified_digest() -> None:
    store = FakeStore({REF: PLAINTEXT, OTHER_REF: "other-value"})
    projection = FakeProjection()
    grant = SimpleNamespace(
        grant_id="grant_01",
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        manifest_version="1.2.3",
        artifact_digest=DIGEST,
        capabilities=("secrets",),
        is_active=True,
        revoked_at=None,
        expires_at=None,
    )

    class Grants:
        async def get_by_grant_id(self, *, grant_id: str) -> Any:
            assert grant_id == "grant_01"
            return grant

    artifact = SimpleNamespace(
        plugin_id=PLUGIN,
        version="1.2.3",
        digest=DIGEST,
        published=True,
        quarantine=None,
        verification=SimpleNamespace(artifact_digest=DIGEST),
        manifest={"secret_delivery": {"mode": "k8s_secret", "allowlist": [REF]}},
    )

    class Artifacts:
        async def get_async(self, plugin_id: str, version: str) -> Any:
            assert (plugin_id, version) == (PLUGIN, "1.2.3")
            return artifact

    policy = SignedManifestSecretDeliveryPolicy(
        artifact_registry=Artifacts(), grant_store=Grants()
    )
    secrets = adapter(store, projection, policy)
    result = await secrets.deliver(SecretsDeliveryRequest(secret_ref=REF, envelope=envelope()))
    assert result.delivered is True

    with pytest.raises(CapabilityAccessDenied, match="secret_ref_not_allowlisted"):
        await secrets.deliver(SecretsDeliveryRequest(secret_ref=OTHER_REF, envelope=envelope()))
    assert store.get_calls == [REF]
    assert len(projection.delivery_calls) == 1

    grant.is_active = False
    with pytest.raises(CapabilityAccessDenied, match="secret_delivery_grant_not_approved"):
        await secrets.deliver(SecretsDeliveryRequest(secret_ref=REF, envelope=envelope()))
    assert len(projection.delivery_calls) == 1


@pytest.mark.asyncio
async def test_projection_reference_mismatch_is_fail_closed() -> None:
    """Reference, не совпадающий с exact value/phase, = typed denial, не ACK."""

    class ForeignDigestProjection(FakeProjection):
        @staticmethod
        def _reference(kwargs: dict[str, str], digest: str, delivered: bool) -> SecretDeliveryReference:
            # Digest вычислен из ДРУГОГО значения: name/labels/key канонические,
            # но value_digest/version не соответствуют доставленному secret_ref.
            return FakeProjection._reference(kwargs, digest_text("other-value"), delivered)

    store = FakeStore()
    projection = ForeignDigestProjection()
    secrets = adapter(store, projection, ApprovedPolicy())
    with pytest.raises(CapabilityBackendError, match="secret_delivery_projection_invalid"):
        await secrets.deliver(SecretsDeliveryRequest(secret_ref=REF, envelope=envelope()))
    assert store.values[REF] == PLAINTEXT, "источник секрета не трогается"

    class NotRevokedProjection(FakeProjection):
        @staticmethod
        def _reference(kwargs: dict[str, str], digest: str, delivered: bool) -> SecretDeliveryReference:
            # «Успешный» delivered=True даже на revoke: поддельный ACK revoke.
            return FakeProjection._reference(kwargs, digest, True)

    revoke_projection = NotRevokedProjection()
    revoke_secrets = adapter(store, revoke_projection, ApprovedPolicy())
    with pytest.raises(CapabilityBackendError, match="secret_revoke_projection_invalid"):
        await revoke_secrets.revoke(SecretsRevokeRequest(secret_ref=REF, envelope=envelope()))
    assert store.values[REF] == PLAINTEXT


def _control_plane_request() -> SecretPreDeliveryRequest:
    """Typed control request на canonical pin (company/plugin/instance/artifact)."""

    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    installation = PluginInstanceRecord(
        plugin_instance_id=INSTANCE,
        company_id=COMPANY,
        plugin_id=PLUGIN,
        manifest_version="1.2.3",
        artifact_digest=DIGEST,
        status=PluginInstanceStatus.ACTIVE,
        tenant_db_name=f"tenant_{COMPANY}",
        bucket_name=f"tenant-{COMPANY}",
        created_at=timestamp,
        updated_at=timestamp,
    )
    return SecretPreDeliveryRequest(
        request_id=f"grant:{INSTANCE}:{DIGEST}",
        installation=installation,
        manifest_version="1.2.3",
        artifact_digest=DIGEST,
    )


def _control_plane_artifacts() -> Any:
    artifact = SimpleNamespace(
        plugin_id=PLUGIN,
        version="1.2.3",
        digest=DIGEST,
        published=True,
        quarantine=None,
        verification=SimpleNamespace(artifact_digest=DIGEST),
        manifest={"secret_delivery": {"mode": "k8s_secret", "allowlist": [REF]}},
    )

    class Artifacts:
        async def get_async(self, plugin_id: str, version: str) -> Any:
            assert (plugin_id, version) == (PLUGIN, "1.2.3")
            return artifact

    return Artifacts()


@pytest.mark.asyncio
async def test_control_plane_delivery_binds_reference_key_to_exact_secret_ref() -> None:
    """Control-plane принимает ТОЛЬКО canonical key; чужой data key = denial.

    Сначала canonical ``FakeProjection`` подтверждает, что вычисленные из
    kwargs["secret_ref"] key/name/labels проходят production-валидацию
    ``ControlPlaneSecretDelivery``. Затем projection с key от ДРУГОГО ref
    (те же scoped name/labels) обязан быть отклонён и на pre_deliver, и на
    pre_revoke: иначе mismatch был бы ложно-зелёным «успехом».
    """

    store = FakeStore({REF: PLAINTEXT, OTHER_REF: "other-value"})
    artifacts = _control_plane_artifacts()
    request = _control_plane_request()

    canonical_projection = FakeProjection()
    canonical = ControlPlaneSecretDelivery(
        store=store, projection=canonical_projection, artifact_registry=artifacts
    )
    delivered = await canonical.pre_deliver(request)
    assert [item.secret_ref for item in delivered] == [REF]
    assert [item.delivered for item in delivered] == [True]
    reference = canonical_projection.references[-1]
    assert reference.key == (
        "SECRET_" + hashlib.sha256(REF.encode("utf-8")).hexdigest().upper()
    )
    revoked = await canonical.pre_revoke(request)
    assert [item.delivered for item in revoked] == [False]

    class ForeignKeyProjection(FakeProjection):
        @staticmethod
        def _reference(kwargs: dict[str, str], digest: str, delivered: bool) -> SecretDeliveryReference:
            # Scoped name/labels остаются каноническими, но data key вычислен
            # из ЧУЖОГО secret_ref: production обязан detect mismatch.
            return FakeProjection._reference(
                {**kwargs, "secret_ref": OTHER_REF}, digest, delivered
            )

    foreign = ControlPlaneSecretDelivery(
        store=store, projection=ForeignKeyProjection(), artifact_registry=artifacts
    )
    with pytest.raises(CapabilityBackendError, match="secret_control_projection_invalid"):
        await foreign.pre_deliver(request)
    with pytest.raises(CapabilityBackendError, match="secret_control_revoke_invalid"):
        await foreign.pre_revoke(request)
    assert store.values[REF] == PLAINTEXT


def test_missing_store_is_typed_unwired_without_file_fallback() -> None:
    with pytest.raises(CapabilityConfigurationError, match="secret_store_unwired"):
        SecretStoreSecretsAdapter(store=None)
