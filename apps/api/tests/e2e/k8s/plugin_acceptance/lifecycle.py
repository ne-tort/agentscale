"""Composition of PRODUCTION lifecycle adapters against the disposable cluster.

Only durable/control-plane collaborators are in-memory test doubles (artifact
registry, instance registry, tenant SecretPort, webhook resolver): they replace
PostgreSQL-backed stores, never Kubernetes. Every Deployment/Service/Secret
call goes through the real ServiceAccountTokenProvider, K8sApiClient,
K8sDeploymentAdmin and K8sSecretProjection against the disposable namespace.

Two independent lifecycle stacks (agentscale-api and plugin-gateway broker
identities) emulate two separate control-plane processes sharing the same
durable registries. SanitizedRecorder keeps only HTTP metadata and Secret KEY
names: tokens, DSN and delivered values are never recorded or logged.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator, Mapping

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import PluginInstanceStatus
from prodavan.application.plugins.artifacts.registry import (
    PluginArtifactRecord,
    PublicationMetadata,
)
from prodavan.application.plugins.artifacts.verification import VerificationEvidence
from prodavan.application.plugins.tenantdb.service import StoredSecret
from prodavan.domain.dynamic_apps.types import AppLifecycleStatus
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.deployment_admin import K8sDeploymentAdmin
from prodavan.infrastructure.plugins.runtime.k8s_scale import K8sRawResponse
from prodavan.infrastructure.plugins.runtime.secret_projection import K8sSecretProjection

from .cluster import API_SA, GATEWAY_SA, AcceptanceCluster, LifecycleTransport


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def value_digest(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def instance_label(ids: AcceptanceIds) -> str:
    """Canonical agentscale.ne-tort/instance-hash value for this instance."""
    raw = f"{ids.company_id}\0{ids.plugin_id}\0{ids.plugin_instance_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def delivery_key(secret_ref: str) -> str:
    """Hashed delivery data key; the source ref never becomes a key name."""
    return "SECRET_" + hashlib.sha256(secret_ref.encode("utf-8")).hexdigest().upper()


@dataclass(frozen=True, slots=True)
class AcceptanceIds:
    """Run-scoped synthetic identities, digests, refs and sentinel values."""

    run_id: str
    label: str
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    version: str
    bundle_v1: str
    bundle_v2: str
    bundle_v3: str
    backend_v1: str
    backend_v2: str
    backend_v3: str
    dsn_ref: str
    dsn_key: str
    webhook_ref: str
    delivery_ref_a: str
    delivery_ref_b: str
    dsn_value_v1: str = field(repr=False)
    dsn_value_v2: str = field(repr=False)
    webhook_value: str = field(repr=False)
    delivery_value_a1: str = field(repr=False)
    delivery_value_a2: str = field(repr=False)
    delivery_value_b1: str = field(repr=False)

    @classmethod
    def new(cls, run_id: str, label: str) -> AcceptanceIds:
        safe_label = label.strip().lower()
        company = f"co-accept-{safe_label}"
        plugin = f"mod-accept-{safe_label}"
        instance = f"inst-{safe_label}"
        delivery_prefix = f"vault://ai_keys/plugins/capabilities/secrets/{company}/{plugin}/{instance}"
        return cls(
            run_id=run_id,
            label=safe_label,
            company_id=company,
            plugin_id=plugin,
            plugin_instance_id=instance,
            version="1.2.3",
            bundle_v1=_digest(f"{run_id}:{safe_label}:bundle:v1"),
            bundle_v2=_digest(f"{run_id}:{safe_label}:bundle:v2"),
            bundle_v3=_digest(f"{run_id}:{safe_label}:bundle:v3"),
            backend_v1=_digest(f"{run_id}:{safe_label}:backend:v1"),
            backend_v2=_digest(f"{run_id}:{safe_label}:backend:v2"),
            backend_v3=_digest(f"{run_id}:{safe_label}:backend:v3"),
            dsn_ref=f"vault://ai_keys/plugins/tenantdb/{company}/{plugin}/TENANT_DB_DSN",
            dsn_key=f"plugins/tenantdb/{company}/{plugin}/TENANT_DB_DSN",
            webhook_ref=f"vault://ai_keys/plugins/webhooks/{company}/{plugin}/{instance}",
            delivery_ref_a=f"{delivery_prefix}/api-key",
            delivery_ref_b=f"{delivery_prefix}/other-key",
            dsn_value_v1=f"acceptance-dsn-{run_id}-{safe_label}-v1",
            dsn_value_v2=f"acceptance-dsn-{run_id}-{safe_label}-v2",
            webhook_value=f"acceptance-webhook-{run_id}-{safe_label}",
            delivery_value_a1=f"acceptance-delivery-a1-{run_id}-{safe_label}",
            delivery_value_a2=f"acceptance-delivery-a2-{run_id}-{safe_label}",
            delivery_value_b1=f"acceptance-delivery-b1-{run_id}-{safe_label}",
        )

    def sibling(self) -> AcceptanceIds:
        """A second plugin/instance in the same namespace for preservation checks."""
        return AcceptanceIds.new(self.run_id, f"{self.label}sib")

    def backend_ref(self, backend_digest: str) -> str:
        return f"ghcr.io/agentscale-acceptance/{self.label}-backend@{backend_digest}"

    def bundle_ref(self, bundle_digest: str) -> str:
        return f"ghcr.io/agentscale-acceptance/{self.label}-bundle@{bundle_digest}"

    def manifest(self) -> dict[str, Any]:
        return {
            "backend": {"port": 8080, "health": "/health"},
            "secret_delivery": {
                "mode": "k8s_secret",
                "allowlist": [self.delivery_ref_a, self.delivery_ref_b],
            },
        }

    def versions(self) -> tuple[tuple[str, str], ...]:
        return (
            (self.bundle_v1, self.backend_v1),
            (self.bundle_v2, self.backend_v2),
            (self.bundle_v3, self.backend_v3),
        )

    def scope(self) -> dict[str, str]:
        return {
            "company_id": self.company_id,
            "plugin_id": self.plugin_id,
            "plugin_instance_id": self.plugin_instance_id,
        }

    def delivery_values(self, *, second_a: bool = False) -> dict[str, str]:
        return {
            self.delivery_ref_a: self.delivery_value_a2 if second_a else self.delivery_value_a1,
            self.delivery_ref_b: self.delivery_value_b1,
        }


def artifact_record(ids: AcceptanceIds, bundle_digest: str, backend_digest: str) -> PluginArtifactRecord:
    now = datetime.now(UTC)
    bundle_ref = ids.bundle_ref(bundle_digest)
    fingerprint = hashlib.sha256(bundle_digest.encode("utf-8")).hexdigest()
    return PluginArtifactRecord(
        plugin_id=ids.plugin_id,
        version=ids.version,
        digest=bundle_digest,
        image_ref=bundle_ref,
        manifest=ids.manifest(),
        manifest_fingerprint=fingerprint,
        status=AppLifecycleStatus.PUBLISHED,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        published_at=now,
        created_at=now,
        verification=VerificationEvidence(
            image_ref=bundle_ref,
            artifact_digest=bundle_digest,
            manifest_fingerprint=fingerprint,
            identity="acceptance-identity",
            issuer="acceptance-issuer",
            sbom_verified=True,
            provenance_verified=True,
            observed_at=now,
            outcome=RegistryOutcome.APPLIED,
            quarantine=None,
            seal=object(),
            scan_verified=True,
            csp_verified=True,
        ),
        publication=PublicationMetadata(
            source_id="agentscale/acceptance",
            commit_sha="a" * 40,
            backend_digest=backend_digest,
            backend_image_ref=ids.backend_ref(backend_digest),
            run_url="https://example.invalid/acceptance/run",
        ),
    )


class AcceptanceArtifactStore:
    """In-memory stand-in for the durable artifact registry (not for K8s)."""

    def __init__(self) -> None:
        self.records: dict[str, PluginArtifactRecord] = {}

    def add(self, record: PluginArtifactRecord) -> None:
        self.records[record.digest] = record

    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None:
        return self.records.get(digest)


class AcceptanceInstanceStore:
    """Shared durable instance registry used by both lifecycle processes."""

    def __init__(self) -> None:
        self.records: dict[str, SimpleNamespace] = {}

    def add(self, ids: AcceptanceIds, artifact_digest: str) -> SimpleNamespace:
        record = SimpleNamespace(
            plugin_instance_id=ids.plugin_instance_id,
            company_id=ids.company_id,
            plugin_id=ids.plugin_id,
            manifest_version=ids.version,
            artifact_digest=artifact_digest,
            status=PluginInstanceStatus.ACTIVE,
        )
        self.records[ids.plugin_instance_id] = record
        return record

    def pin(self, ids: AcceptanceIds, artifact_digest: str) -> None:
        self.records[ids.plugin_instance_id].artifact_digest = artifact_digest

    async def get(self, *, company_id: str, plugin_id: str) -> SimpleNamespace | None:
        return next(
            (
                record
                for record in self.records.values()
                if record.company_id == company_id and record.plugin_id == plugin_id
            ),
            None,
        )

    async def get_by_id(self, *, plugin_instance_id: str) -> SimpleNamespace | None:
        return self.records.get(plugin_instance_id)


class AcceptanceTenantSecrets:
    """Tenant SecretPort double; plaintext stays in this process only."""

    def __init__(self) -> None:
        self._records: dict[str, StoredSecret] = {}

    def seed(self, *, key: str, secret_ref: str, value: str) -> None:
        self._records[key] = StoredSecret(secret_ref=secret_ref, value=value)

    async def get(self, *, key: str) -> StoredSecret | None:
        return self._records.get(key)

    async def put(self, *, key: str, value: str) -> str:
        secret_ref = f"vault://ai_keys/{key}"
        self._records[key] = StoredSecret(secret_ref=secret_ref, value=value)
        return secret_ref

    async def rotate(self, *, key: str, value: str) -> str:
        return await self.put(key=key, value=value)

    async def delete(self, *, secret_ref: str) -> None:
        for key, record in list(self._records.items()):
            if record.secret_ref == secret_ref:
                del self._records[key]


class AcceptanceWebhookResolver:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def add(self, secret_ref: str, value: str) -> None:
        self._values[secret_ref] = value

    async def resolve(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
    ) -> str:
        return self._values[secret_ref]


class SanitizedRecorder:
    """Delegating K8sApiPort that records metadata only, never secret values."""

    def __init__(self, api: Any) -> None:
        self._api = api
        self.records: list[dict[str, Any]] = []

    @property
    def scoped_identity(self) -> bool:
        return getattr(self._api, "scoped_identity", True) is True

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> K8sRawResponse:
        resource_version = None
        data_keys = None
        if isinstance(body, dict):
            metadata = body.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("resourceVersion"), str):
                resource_version = metadata["resourceVersion"]
            data = body.get("data")
            if isinstance(data, dict):
                # Key names and null-removal markers only; values are dropped.
                data_keys = sorted(data)
        self.records.append(
            {
                "method": method.upper(),
                "path": path,
                "content_type": content_type,
                "resource_version": resource_version,
                "data_keys": data_keys,
            }
        )
        return await self._api.request(method, path, body=body, content_type=content_type)

    async def aclose(self) -> None:
        await self._api.aclose()


@dataclass(slots=True)
class LifecycleSide:
    transport: LifecycleTransport = field(repr=False)
    recorder: SanitizedRecorder = field(repr=False)
    projection: K8sSecretProjection = field(repr=False)
    admin: K8sDeploymentAdmin = field(repr=False)


@dataclass(slots=True)
class LifecyclePair:
    ids: AcceptanceIds
    runtime_config: RuntimeConfig
    artifacts: AcceptanceArtifactStore = field(repr=False)
    instances: AcceptanceInstanceStore = field(repr=False)
    tenant_secrets: AcceptanceTenantSecrets = field(repr=False)
    webhook: AcceptanceWebhookResolver = field(repr=False)
    a: LifecycleSide = field(repr=False)
    b: LifecycleSide = field(repr=False)


def _side(
    cluster: AcceptanceCluster,
    transport: LifecycleTransport,
    runtime_config: RuntimeConfig,
    artifacts: AcceptanceArtifactStore,
    instances: AcceptanceInstanceStore,
    tenant_secrets: AcceptanceTenantSecrets,
    webhook: AcceptanceWebhookResolver,
) -> LifecycleSide:
    recorder = SanitizedRecorder(transport.api)
    projection = K8sSecretProjection(
        config=runtime_config,
        tenant_secrets=tenant_secrets,
        webhook_resolver=webhook,
        gateway_url=runtime_config.gateway_url,
        api=recorder,
    )
    admin = K8sDeploymentAdmin(
        config=runtime_config,
        artifact_store=artifacts,
        instance_store=instances,
        secret_projection=projection,
        api=recorder,
    )
    return LifecycleSide(transport=transport, recorder=recorder, projection=projection, admin=admin)


@asynccontextmanager
async def lifecycle_pair(cluster: AcceptanceCluster, ids: AcceptanceIds) -> AsyncIterator[LifecyclePair]:
    """Two independent production lifecycle stacks over one durable state."""
    gateway_url = cluster.config.gateway_url or f"http://plugin-gateway.{cluster.namespace}.svc:8080"
    runtime_config = RuntimeConfig(namespace=cluster.namespace, gateway_url=gateway_url)
    runtime_config.internal_gateway_url()  # fail fast on an untrusted URL
    artifacts = AcceptanceArtifactStore()
    for bundle_digest, backend_digest in ids.versions():
        artifacts.add(artifact_record(ids, bundle_digest, backend_digest))
    instances = AcceptanceInstanceStore()
    instances.add(ids, ids.bundle_v1)
    tenant_secrets = AcceptanceTenantSecrets()
    tenant_secrets.seed(key=ids.dsn_key, secret_ref=ids.dsn_ref, value=ids.dsn_value_v1)
    webhook = AcceptanceWebhookResolver({ids.webhook_ref: ids.webhook_value})
    async with cluster.lifecycle(API_SA) as transport_a:
        async with cluster.lifecycle(GATEWAY_SA) as transport_b:
            side_a = _side(cluster, transport_a, runtime_config, artifacts, instances, tenant_secrets, webhook)
            side_b = _side(cluster, transport_b, runtime_config, artifacts, instances, tenant_secrets, webhook)
            yield LifecyclePair(
                ids=ids,
                runtime_config=runtime_config,
                artifacts=artifacts,
                instances=instances,
                tenant_secrets=tenant_secrets,
                webhook=webhook,
                a=side_a,
                b=side_b,
            )


async def provision(pair: LifecyclePair) -> str:
    """Pre-deliver capability values, then ensure the initial V1 deployment."""
    ids = pair.ids
    await pair.a.projection.reconcile_secret_values(**ids.scope(), values=ids.delivery_values())
    return await pair.a.admin.ensure_deployment(
        **ids.scope(),
        artifact_digest=ids.bundle_v1,
        dsn_ref=ids.dsn_ref,
        webhook_secret_ref=ids.webhook_ref,
    )


def assert_value_digest_matches(actual: bytes, expected: str, *, label: str) -> None:
    """Compare projected plaintext via SHA-256 only: failures leak no values."""
    if hashlib.sha256(actual).digest() != hashlib.sha256(expected.encode("utf-8")).digest():
        raise AssertionError(f"{label}: projected value digest mismatch")


def assert_no_sensitive_material(haystack: str, needle: str, *, code: str) -> None:
    """Membership guard whose failure reveals only a safe code, never operands."""
    if needle and needle in haystack:
        raise AssertionError(f"sensitive material detected ({code})")


def assert_objects_equal(actual: object, expected: object, *, label: str) -> None:
    """Full-object equality without printing secret-bearing dicts on failure."""
    if actual != expected:
        raise AssertionError(f"{label}: objects differ")


def assert_same_token(actual: str, expected: str, *, code: str) -> None:
    """Token equality with no JWT ever appearing in assertion output."""
    if actual != expected:
        raise AssertionError(f"token identity mismatch ({code})")


def assert_token_rotated(previous: str, refreshed: str, *, code: str) -> None:
    """Require a different token without printing either JWT on failure."""
    if previous == refreshed:
        raise AssertionError(f"token was not rotated ({code})")


def resource_version(body: Mapping[str, Any] | None) -> str:
    if body is None:
        return "<absent>"
    metadata = body.get("metadata")
    value = metadata.get("resourceVersion") if isinstance(metadata, dict) else None
    return str(value) if value is not None else "<none>"


def annotations_of(body: Mapping[str, Any], *, template: bool = False) -> dict[str, Any]:
    current: Any = body
    if template:
        current = ((body.get("spec") or {}).get("template") or {}).get("metadata") or {}
    else:
        current = body.get("metadata") or {}
    annotations = current.get("annotations") if isinstance(current, dict) else None
    return dict(annotations) if isinstance(annotations, dict) else {}


def decoded_secret_value(secret: Mapping[str, Any], key: str) -> bytes:
    """Test-side comparison helper; the returned bytes must never be logged."""
    import base64

    data = secret.get("data") or {}
    return base64.b64decode(data[key], validate=True)


__all__ = [
    "AcceptanceArtifactStore",
    "AcceptanceIds",
    "AcceptanceInstanceStore",
    "AcceptanceTenantSecrets",
    "AcceptanceWebhookResolver",
    "LifecyclePair",
    "LifecycleSide",
    "SanitizedRecorder",
    "annotations_of",
    "artifact_record",
    "assert_no_sensitive_material",
    "assert_objects_equal",
    "assert_same_token",
    "assert_token_rotated",
    "assert_value_digest_matches",
    "decoded_secret_value",
    "delivery_key",
    "instance_label",
    "lifecycle_pair",
    "provision",
    "resource_version",
    "value_digest",
]
