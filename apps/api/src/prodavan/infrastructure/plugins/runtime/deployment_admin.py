"""Kubernetes lifecycle adapter for durable plugin instances.

The adapter implements the existing admin DeploymentAdminPort and also exposes
explicit update/suspend/resume/uninstall operations. It talks to the same
injectable REST client as K8sScalePort; no SDK, kubectl, shell, exec, or GitOps
mutation is used.

Security invariants
-------------------
* The container image comes only from verified publication.backend_image_ref
  and its digest must equal publication.backend_digest. The bundle digest is
  used only to find the artifact, never as the backend image digest.
* Deployment, Service and the runtime Secret share the canonical
  plugin_resource_name(plugin_id, instance_id). Ownership labels contain only
  bounded hashes of company/plugin/instance. A Service selector contains
  exactly one instance label.
* TENANT_DB_DSN, GATEWAY_URL, PLUGIN_ID and WEBHOOK_SECRET are injected only through secretKeyRef. The
  injected SecretProjectionPort owns the per-instance Secret projection and is
  called on every ensure/replay. This adapter validates the result but never
  receives or logs plaintext DSN material.
* imagePullSecrets contains only the explicitly configured Secret NAME. No
  registry token or dockerconfigjson is accepted, projected, or logged here;
  without a valid configured name the field is omitted entirely.
* Actual plugin_instance_id and backend_digest are non-secret pod annotations,
  projected read-only through one downwardAPI volume at
  /etc/agentscale/plugin-runtime. No init container, shell, or identity Secret
  is used; a missing projection is a typed manifest generation failure.
* Every mutation is name- and ownership-checked. Namespace, ServiceAccount,
  CRD, NetworkPolicy and tenant DB objects are never created or deleted.

Missing projection wiring is an explicit composition debt and a fail-closed
operation error, never a manual pre-step disguised as a successful deployment.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlencode

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import (
    PluginInstanceStatus,
    PluginRuntimeBinding,
    PluginRuntimeStatus,
)
from prodavan.application.plugins.artifacts.oci import is_valid_digest, parse_oci_ref
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.application.plugins.capabilities.adapters.secrets import secret_delivery_allowlist
from prodavan.domain.dynamic_apps.types import AppLifecycleStatus

from .config import RuntimeConfig
from .errors import PluginRuntimeError, RuntimeConfigError
from .secret_projection import (
    DELIVERY_SECRET_PREFIX,
    DELIVERY_VERSIONS_ANNOTATION,
    SecretDeliveryProjectionPort,
    SecretProjectionPort,
    SecretReference,
)
from .k8s_scale import (
    APPS_API,
    CORE_API,
    MERGE_PATCH,
    K8sApiClient,
    K8sApiConflict,
    K8sApiError,
    K8sApiNotFound,
    K8sApiPort,
    plugin_resource_name,
)

MANAGED_BY = "agentscale.ne-tort/managed-by"
COMPANY_LABEL = "agentscale.ne-tort/company-hash"
PLUGIN_LABEL = "agentscale.ne-tort/plugin-hash"
INSTANCE_LABEL = "agentscale.ne-tort/instance-hash"
DSN_REF_ANNOTATION = "agentscale.ne-tort/dsn-ref-sha256"
ARTIFACT_ANNOTATION = "agentscale.ne-tort/artifact-digest"
BACKEND_ANNOTATION = "agentscale.ne-tort/backend-digest"
PLUGIN_INSTANCE_ANNOTATION = "agentscale.ne-tort/plugin-instance-id"
UPDATE_OWNER_ANNOTATION = "agentscale.ne-tort/update-owner-token"
RUNTIME_IDENTITY_VOLUME_NAME = "plugin-runtime-identity"
RUNTIME_IDENTITY_MOUNT_PATH = "/etc/agentscale/plugin-runtime"
RUNTIME_TMP_VOLUME_NAME = "plugin-runtime-tmp"
RUNTIME_TMP_MOUNT_PATH = "/tmp"
READINESS_PROBE_PATH = "/health/ready"
LIVENESS_PROBE_PATH = "/health/live"
MANAGED_BY_VALUE = "plugin-deployment-admin"
SECRET_KEYS = ("TENANT_DB_DSN", "GATEWAY_URL", "PLUGIN_ID", "WEBHOOK_SECRET")
DELIVERY_VOLUME_PREFIX = "capability-delivery-"
DELIVERY_MOUNT_PATH = "/var/run/agentscale/capability-secrets"
DELIVERY_DEFAULT_MODE = 0o400
_ALLOWED_DSN_REF_SCHEMES = ("file://", "vault://", "secret://", "k8s-secret://")
_BASE64_VALUE = re.compile(
    r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=|[A-Za-z0-9+/]{4})$"
)


class ArtifactDigestLookupPort(Protocol):
    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None: ...


class DeploymentInstanceLookupPort(Protocol):
    async def get(self, *, company_id: str, plugin_id: str) -> Any | None: ...
    async def get_by_id(self, *, plugin_instance_id: str) -> Any | None: ...


class DeploymentAdminError(PluginRuntimeError):
    """Safe typed lifecycle failure; details never contain reference values."""

    def __init__(
        self,
        *,
        code: str,
        operation: str,
        detail: str,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.operation = operation
        self.retryable = bool(retryable)
        super().__init__(f"{operation}: {detail} [{code}]")


class DeploymentConfigurationError(DeploymentAdminError):
    def __init__(self, operation: str, detail: str) -> None:
        super().__init__(
            code="DEPLOYMENT_CONFIG_INVALID", operation=operation, detail=detail
        )


class ArtifactProvenanceError(DeploymentAdminError):
    def __init__(self, operation: str, detail: str = "unverified backend provenance") -> None:
        super().__init__(
            code="ARTIFACT_PROVENANCE_INVALID", operation=operation, detail=detail
        )


class DeploymentSecretError(DeploymentAdminError):
    def __init__(self, operation: str, detail: str, *, retryable: bool = False) -> None:
        super().__init__(
            code="INSTANCE_SECRET_INVALID",
            operation=operation,
            detail=detail,
            retryable=retryable,
        )


class DeploymentOwnershipError(DeploymentAdminError):
    def __init__(self, operation: str, kind: str) -> None:
        super().__init__(
            code="RESOURCE_OWNERSHIP_MISMATCH",
            operation=operation,
            detail=f"{kind} is absent, foreign, or not managed for this instance",
        )


class DeploymentConflictError(DeploymentAdminError):
    def __init__(self, operation: str, kind: str) -> None:
        super().__init__(
            code="K8S_CONFLICT",
            operation=operation,
            detail=f"{kind} changed concurrently; retry the lifecycle operation",
            retryable=True,
        )


class DeploymentArtifactDigestConflictError(DeploymentConflictError):
    """Live Deployment no longer matches the update's expected state/owner."""

    def __init__(self, operation: str) -> None:
        DeploymentAdminError.__init__(
            self,
            code="DEPLOYMENT_ARTIFACT_DIGEST_CONFLICT",
            operation=operation,
            detail="live Deployment artifact digest or update owner changed; mutation denied",
            retryable=True,
        )


class DeploymentUnavailableError(DeploymentAdminError):
    def __init__(self, operation: str, detail: str = "Kubernetes API unavailable") -> None:
        super().__init__(
            code="K8S_UNAVAILABLE",
            operation=operation,
            detail=detail,
            retryable=True,
        )


class DeploymentStateError(DeploymentAdminError):
    def __init__(self, operation: str, detail: str) -> None:
        super().__init__(
            code="DEPLOYMENT_STATE_INVALID", operation=operation, detail=detail
        )


class DeploymentNotFoundError(DeploymentAdminError):
    def __init__(self, operation: str, kind: str) -> None:
        super().__init__(
            code="DEPLOYMENT_NOT_FOUND",
            operation=operation,
            detail=f"required {kind} is absent",
        )


def _hash(value: str, *, size: int = 40) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


def _instance_hash(company_id: str, plugin_id: str, instance_id: str) -> str:
    return _hash(f"{company_id}\0{plugin_id}\0{instance_id}")


def _labels(company_id: str, plugin_id: str, instance_id: str) -> dict[str, str]:
    return {
        MANAGED_BY: MANAGED_BY_VALUE,
        COMPANY_LABEL: _hash(company_id),
        PLUGIN_LABEL: _hash(plugin_id),
        INSTANCE_LABEL: _instance_hash(company_id, plugin_id, instance_id),
    }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _contains(expected: Any, actual: Any) -> bool:
    """Expected subset comparison; Kubernetes-defaulted extra fields are okay."""
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(key in actual and _contains(value, actual[key]) for key, value in expected.items())
    return expected == actual


def _plugin_id_value(secret: Mapping[str, Any]) -> str:
    data = secret.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("missing secret data")
    # Validate the encoded shape without decoding tenant DSN, gateway URL, or
    # webhook secret plaintext. Only the non-secret PLUGIN_ID is decoded below.
    if set(data) != set(SECRET_KEYS):
        raise ValueError("secret keys differ from the projection contract")
    for key in SECRET_KEYS:
        if (
            not isinstance(data.get(key), str)
            or not data[key]
            or _BASE64_VALUE.fullmatch(data[key]) is None
        ):
            raise ValueError("missing or invalid secret key")
    return base64.b64decode(data["PLUGIN_ID"], validate=True).decode("utf-8")


class K8sDeploymentAdmin:
    """Real per-instance Deployment/Service lifecycle over K8sApiPort."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        artifact_store: ArtifactDigestLookupPort,
        instance_store: DeploymentInstanceLookupPort,
        secret_projection: SecretProjectionPort,
        secret_delivery_projection: SecretDeliveryProjectionPort | None = None,
        api: K8sApiPort | None = None,
        client: Any = None,
        auth: Any = None,
        apply_attempts: int = 3,
    ) -> None:
        if not isinstance(config, RuntimeConfig):
            raise RuntimeConfigError("K8sDeploymentAdmin requires RuntimeConfig")
        if not callable(getattr(artifact_store, "by_digest_async", None)):
            raise RuntimeConfigError("K8sDeploymentAdmin requires artifact digest lookup")
        if not callable(getattr(secret_projection, "ensure_instance_secret", None)):
            raise RuntimeConfigError(
                "K8sDeploymentAdmin requires SecretProjectionPort.ensure_instance_secret()"
            )
        for method in ("get", "get_by_id"):
            if not callable(getattr(instance_store, method, None)):
                raise RuntimeConfigError(f"K8sDeploymentAdmin requires instance_store.{method}()")
        if not isinstance(apply_attempts, int) or isinstance(apply_attempts, bool) or apply_attempts < 1:
            raise RuntimeConfigError("apply_attempts must be a positive integer")
        self._config = config
        self._artifacts = artifact_store
        self._instances = instance_store
        self._secret_projection = secret_projection
        # The production K8sSecretProjection implements both ports. Keep an
        # explicit override for composition/tests without requiring new wiring.
        self._secret_delivery_projection = (
            secret_projection if secret_delivery_projection is None else secret_delivery_projection
        )
        self._api: K8sApiPort = api or K8sApiClient(auth=auth, client=client)
        self._apply_attempts = apply_attempts

    @property
    def api(self) -> K8sApiPort:
        return self._api

    @property
    def secret_projection(self) -> SecretProjectionPort:
        return self._secret_projection

    @property
    def secret_delivery_projection(self) -> SecretDeliveryProjectionPort | None:
        return self._secret_delivery_projection

    def resource_name(self, plugin_id: str, plugin_instance_id: str) -> str:
        return plugin_resource_name(
            plugin_id, plugin_instance_id, prefix=self._config.deployment_prefix
        )

    def _path(self, group: str, plural: str, name: str | None = None) -> str:
        base = f"{APPS_API if group == 'apps' else CORE_API}/namespaces/{self._config.namespace}/{plural}"
        return f"{base}/{name}" if name else base

    async def _request(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        content_type: str | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        try:
            response = await self._api.request(
                method, path, body=body, content_type=content_type
            )
        except K8sApiNotFound:
            if allow_not_found:
                return None
            raise DeploymentNotFoundError(operation, "Kubernetes resource") from None
        except K8sApiConflict:
            raise DeploymentConflictError(operation, "Kubernetes resource") from None
        except K8sApiError as exc:
            raise DeploymentUnavailableError(
                operation, f"Kubernetes API rejected the request ({type(exc).__name__})"
            ) from None
        except Exception as exc:  # noqa: BLE001 - arbitrary injected transports
            raise DeploymentUnavailableError(
                operation, f"Kubernetes transport failed ({type(exc).__name__})"
            ) from None
        if not isinstance(response.body, dict):
            raise DeploymentUnavailableError(operation, "Kubernetes returned an invalid object")
        return response.body

    async def _get(self, operation: str, group: str, plural: str, name: str) -> dict[str, Any] | None:
        return await self._request(
            operation, "GET", self._path(group, plural, name), allow_not_found=True
        )

    def _validate_owner(
        self,
        operation: str,
        body: Mapping[str, Any],
        *,
        kind: str,
        name: str,
        labels: Mapping[str, str],
    ) -> None:
        metadata = _mapping(body.get("metadata"))
        actual_labels = _mapping(metadata.get("labels"))
        if (
            body.get("kind") != kind
            or metadata.get("name") != name
            or metadata.get("namespace") != self._config.namespace
            or any(actual_labels.get(key) != value for key, value in labels.items())
        ):
            raise DeploymentOwnershipError(operation, kind)

    async def _instance_by_id(
        self,
        operation: str,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str | None = None,
    ) -> Any:
        for value in (company_id, plugin_id, plugin_instance_id):
            if not isinstance(value, str) or not value.strip():
                raise DeploymentConfigurationError(operation, "empty lifecycle identity")
        try:
            record = await self._instances.get_by_id(plugin_instance_id=plugin_instance_id)
        except Exception as exc:  # noqa: BLE001
            raise DeploymentUnavailableError(
                operation, f"durable instance lookup failed ({type(exc).__name__})"
            ) from None
        if (
            record is None
            or getattr(record, "company_id", None) != company_id
            or getattr(record, "plugin_id", None) != plugin_id
            or getattr(record, "plugin_instance_id", None) != plugin_instance_id
            or (
                artifact_digest is not None
                and getattr(record, "artifact_digest", None) != artifact_digest
            )
        ):
            raise DeploymentOwnershipError(operation, "PluginInstance")
        return record

    async def _instance(self, operation: str, *, company_id: str, plugin_id: str) -> Any:
        for value in (company_id, plugin_id):
            if not isinstance(value, str) or not value.strip():
                raise DeploymentConfigurationError(operation, "empty lifecycle identity")
        try:
            record = await self._instances.get(company_id=company_id, plugin_id=plugin_id)
        except Exception as exc:  # noqa: BLE001
            raise DeploymentUnavailableError(
                operation, f"durable instance lookup failed ({type(exc).__name__})"
            ) from None
        if (
            record is None
            or getattr(record, "company_id", None) != company_id
            or getattr(record, "plugin_id", None) != plugin_id
            or not str(getattr(record, "plugin_instance_id", "")).strip()
        ):
            raise DeploymentNotFoundError(operation, "PluginInstance")
        return record

    async def _backend_image(
        self,
        operation: str,
        *,
        plugin_id: str,
        artifact_digest: str,
        expected_version: str | None,
    ) -> tuple[str, str, Mapping[str, Any]]:
        if not is_valid_digest(artifact_digest):
            raise ArtifactProvenanceError(operation, "invalid bundle digest")
        try:
            record = await self._artifacts.by_digest_async(artifact_digest)
        except Exception as exc:  # noqa: BLE001
            raise DeploymentUnavailableError(
                operation, f"artifact provenance lookup failed ({type(exc).__name__})"
            ) from None
        if not isinstance(record, PluginArtifactRecord):
            raise ArtifactProvenanceError(operation, "published artifact is absent")
        evidence = record.verification
        publication = record.publication
        if (
            record.plugin_id != plugin_id
            or record.digest != artifact_digest
            or (expected_version is not None and record.version != expected_version)
            or record.status is not AppLifecycleStatus.PUBLISHED
            or record.outcome is not RegistryOutcome.APPLIED
            or record.quarantine is not None
            or record.published_at is None
            or publication is None
            or evidence is None
            or not evidence.verified
            or not evidence.identity.strip()
            or not evidence.issuer.strip()
            or evidence.artifact_digest != record.digest
            or evidence.image_ref != record.image_ref
            or evidence.manifest_fingerprint != record.manifest_fingerprint
            or not all(
                (
                    evidence.sbom_verified,
                    evidence.provenance_verified,
                    evidence.scan_verified,
                    evidence.csp_verified,
                )
            )
        ):
            raise ArtifactProvenanceError(operation)
        backend_digest = publication.backend_digest
        if not is_valid_digest(backend_digest) or backend_digest == artifact_digest:
            raise ArtifactProvenanceError(
                operation, "backend digest is absent, invalid, or aliases the bundle digest"
            )
        try:
            backend_ref = parse_oci_ref(publication.backend_image_ref)
        except (TypeError, ValueError):
            raise ArtifactProvenanceError(operation, "backend image ref is invalid") from None
        if backend_ref.digest != backend_digest:
            raise ArtifactProvenanceError(operation, "backend image digest mismatch")
        manifest = record.manifest
        backend = manifest.get("backend") if isinstance(manifest, Mapping) else None
        if not isinstance(backend, Mapping) or set(backend) != {"port", "health"}:
            raise DeploymentConfigurationError(
                operation, "backend descriptor must contain only port and health"
            )
        if backend.get("port") != self._config.service_port or backend.get("health") != self._config.health_path:
            raise DeploymentConfigurationError(
                operation,
                f"manifest override unsupported; MVP requires {self._config.service_port} and {self._config.health_path}",
            )
        unsupported = {"deployment", "runtime", "resources", "replicas", "securityContext", "networkPolicy"}
        if any(key in manifest for key in unsupported):
            raise DeploymentConfigurationError(operation, "manifest deployment overrides are unsupported")
        return backend_ref.image_ref(), backend_digest, manifest

    @staticmethod
    def _validate_dsn_ref(operation: str, dsn_ref: str) -> str:
        if (
            not isinstance(dsn_ref, str)
            or not dsn_ref.strip()
            or len(dsn_ref) > 2048
            or any(char.isspace() or char == "\0" for char in dsn_ref)
            or not dsn_ref.startswith(_ALLOWED_DSN_REF_SCHEMES)
        ):
            raise DeploymentSecretError(
                operation, "DSN must be an opaque secret reference, never plaintext"
            )
        return dsn_ref

    @staticmethod
    def _validate_webhook_secret_ref(operation: str, webhook_secret_ref: str) -> str:
        if (
            not isinstance(webhook_secret_ref, str)
            or not webhook_secret_ref.strip()
            or len(webhook_secret_ref) > 2048
            or any(char.isspace() or char == "\0" for char in webhook_secret_ref)
            or not webhook_secret_ref.startswith(_ALLOWED_DSN_REF_SCHEMES)
        ):
            raise DeploymentSecretError(
                operation,
                "webhook_secret_ref must be a durable opaque secret reference",
            )
        return webhook_secret_ref

    async def _project_secret(
        self,
        operation: str,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        name: str,
        labels: Mapping[str, str],
        dsn_ref: str,
        webhook_secret_ref: str,
    ) -> SecretReference:
        source_ref = self._validate_dsn_ref(operation, dsn_ref)
        webhook_ref = self._validate_webhook_secret_ref(operation, webhook_secret_ref)
        try:
            reference = await self._secret_projection.ensure_instance_secret(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=plugin_instance_id,
                dsn_ref=source_ref,
                webhook_secret_ref=webhook_ref,
            )
        except DeploymentAdminError:
            raise
        except Exception as exc:  # noqa: BLE001 - projection must not leak values
            raise DeploymentSecretError(
                operation,
                f"Secret projection failed ({type(exc).__name__})",
                retryable=True,
            ) from None
        reference_labels = getattr(reference, "labels", None)
        if (
            getattr(reference, "namespace", None) != self._config.namespace
            or getattr(reference, "name", None) != name
            or not isinstance(reference_labels, Mapping)
            or dict(reference_labels) != dict(labels)
            or getattr(reference, "dsn_ref_sha256", None) != _hash(source_ref, size=64)
        ):
            raise DeploymentSecretError(
                operation, "Secret projection returned an out-of-scope SecretReference"
            )
        await self._validate_secret(
            operation,
            reference=reference,
            plugin_id=plugin_id,
        )
        return reference

    async def _validate_secret(
        self,
        operation: str,
        *,
        reference: SecretReference,
        plugin_id: str,
    ) -> None:
        secret = await self._get(operation, "core", "secrets", reference.name)
        if secret is None:
            raise DeploymentSecretError(operation, "per-instance runtime Secret is absent")
        self._validate_owner(
            operation,
            secret,
            kind="Secret",
            name=reference.name,
            labels=reference.labels,
        )
        annotations = _mapping(_mapping(secret.get("metadata")).get("annotations"))
        if annotations.get(DSN_REF_ANNOTATION) != reference.dsn_ref_sha256:
            raise DeploymentSecretError(operation, "Secret is not bound to the current DSN reference")
        try:
            plugin_value = _plugin_id_value(secret)
        except (ValueError, binascii.Error, UnicodeError):
            raise DeploymentSecretError(
                operation, "Secret must contain TENANT_DB_DSN/GATEWAY_URL/PLUGIN_ID/WEBHOOK_SECRET data"
            ) from None
        if plugin_value != plugin_id:
            raise DeploymentSecretError(operation, "Secret PLUGIN_ID belongs to another plugin")

    def canonical_delivery_secret_name(self, plugin_id: str, plugin_instance_id: str) -> str:
        """Derive the same bounded canonical Secret name as the delivery projection."""

        return plugin_resource_name(
            plugin_id,
            plugin_instance_id,
            prefix=self._config.deployment_prefix + DELIVERY_SECRET_PREFIX,
        )

    @staticmethod
    def _delivery_key(secret_ref: str) -> str:
        return "SECRET_" + hashlib.sha256(secret_ref.encode("utf-8")).hexdigest().upper()

    def _delivery_refs(self, operation: str, manifest: Any) -> tuple[str, ...]:
        try:
            refs = secret_delivery_allowlist(manifest)
        except ValueError:
            raise DeploymentConfigurationError(
                operation, "secret_delivery declaration is invalid or not approved"
            ) from None
        return refs

    async def _delivery_mount(
        self,
        operation: str,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        manifest: Any,
        labels: Mapping[str, str],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        refs = self._delivery_refs(operation, manifest)
        if not refs:
            return None, None
        secret_name = self.canonical_delivery_secret_name(plugin_id, plugin_instance_id)
        volume_name = DELIVERY_VOLUME_PREFIX + _instance_hash(
            company_id, plugin_id, plugin_instance_id
        )[:24]
        secret = await self._get(operation, "core", "secrets", secret_name)
        if secret is None:
            raise DeploymentSecretError(operation, "capability delivery Secret is absent")
        self._validate_owner(
            operation,
            secret,
            kind="Secret",
            name=secret_name,
            labels=labels,
        )
        if (
            secret.get("type") != "Opaque"
            or secret.get("immutable") is True
            or "stringData" in secret
        ):
            raise DeploymentSecretError(operation, "capability delivery Secret type is invalid")
        expected_key_by_ref = {ref: self._delivery_key(ref) for ref in refs}
        expected_keys = set(expected_key_by_ref.values())
        data = secret.get("data")
        if not isinstance(data, Mapping) or set(data) != expected_keys:
            raise DeploymentSecretError(
                operation, "capability delivery Secret does not match approved refs"
            )
        annotations = _mapping(_mapping(secret.get("metadata")).get("annotations"))
        raw_versions = annotations.get(DELIVERY_VERSIONS_ANNOTATION)
        try:
            versions = json.loads(raw_versions) if isinstance(raw_versions, str) else None
        except (TypeError, ValueError):
            versions = None
        if (
            not isinstance(versions, dict)
            or set(versions) != expected_keys
            or any(not isinstance(value, str) for value in versions.values())
        ):
            raise DeploymentSecretError(
                operation, "capability delivery Secret version binding is invalid"
            )
        for secret_ref, key in expected_key_by_ref.items():
            encoded = data.get(key)
            if not isinstance(encoded, str) or _BASE64_VALUE.fullmatch(encoded) is None:
                raise DeploymentSecretError(
                    operation, "capability delivery Secret contains invalid data"
                )
            try:
                value = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise DeploymentSecretError(
                    operation, "capability delivery Secret contains invalid data"
                ) from None
            expected_digest = versions.get(key)
            if (
                not value
                or expected_digest
                != "sha256:" + hashlib.sha256(value).hexdigest()
            ):
                raise DeploymentSecretError(
                    operation, "capability delivery Secret digest does not match its version"
                )
        # No items projection: every key remains under its canonical projected
        # name, and no plaintext ever enters this Deployment object.
        volume = {
            "name": volume_name,
            "secret": {
                "secretName": secret_name,
                "defaultMode": DELIVERY_DEFAULT_MODE,
            },
        }
        mount = {
            "name": volume_name,
            "mountPath": DELIVERY_MOUNT_PATH,
            "readOnly": True,
        }
        return volume, mount

    async def _cleanup_manifest(self, operation: str, record: Any) -> Any:
        digest = getattr(record, "artifact_digest", None)
        plugin_id = getattr(record, "plugin_id", None)
        if not isinstance(digest, str) or not is_valid_digest(digest):
            raise DeploymentStateError(operation, "instance has no valid delivery artifact pin")
        try:
            artifact = await self._artifacts.by_digest_async(digest)
        except Exception as exc:  # noqa: BLE001 - registry text may contain refs
            raise DeploymentUnavailableError(
                operation, f"artifact lookup for delivery cleanup failed ({type(exc).__name__})"
            ) from None
        if (
            not isinstance(artifact, PluginArtifactRecord)
            or artifact.digest != digest
            or artifact.plugin_id != plugin_id
            or not isinstance(artifact.manifest, Mapping)
        ):
            raise DeploymentSecretError(
                operation, "artifact manifest for delivery cleanup is absent or foreign"
            )
        return artifact.manifest

    async def _delete_owned_delivery_secret(
        self,
        operation: str,
        *,
        name: str,
        labels: Mapping[str, str],
    ) -> None:
        for _attempt in range(self._apply_attempts):
            secret = await self._get(operation, "core", "secrets", name)
            if secret is None:
                return
            self._validate_owner(operation, secret, kind="Secret", name=name, labels=labels)
            resource_version = _mapping(secret.get("metadata")).get("resourceVersion")
            if not isinstance(resource_version, str) or not resource_version:
                raise DeploymentUnavailableError(
                    operation, "delivery Secret has no resourceVersion for a safe delete"
                )
            try:
                await self._request(
                    operation,
                    "DELETE",
                    self._path("core", "secrets", name),
                    body={
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "preconditions": {"resourceVersion": resource_version},
                    },
                    content_type="application/json",
                )
            except DeploymentConflictError:
                continue
            if await self._get(operation, "core", "secrets", name) is None:
                return
        raise DeploymentConflictError(operation, "Secret")

    async def _cleanup_delivery_secret(
        self,
        operation: str,
        *,
        record: Any,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        labels: Mapping[str, str],
    ) -> None:
        """Revoke exact current refs, then delete only the owned delivery Secret."""

        refs = self._delivery_refs(operation, await self._cleanup_manifest(operation, record))
        secret_name = self.canonical_delivery_secret_name(plugin_id, plugin_instance_id)
        projection = self._secret_delivery_projection
        method = (
            getattr(projection, "revoke_secret", None)
            or getattr(projection, "delete_secret", None)
            if projection is not None
            else None
        )
        if refs and not callable(method):
            raise DeploymentSecretError(operation, "capability delivery projection is not wired")
        if refs:
            expected_key_by_ref = {ref: self._delivery_key(ref) for ref in refs}
            for secret_ref in refs:
                try:
                    reference = await method(
                        company_id=company_id,
                        plugin_id=plugin_id,
                        plugin_instance_id=plugin_instance_id,
                        secret_ref=secret_ref,
                    )
                except DeploymentAdminError:
                    raise
                except Exception as exc:  # noqa: BLE001 - projection must not leak values
                    raise DeploymentSecretError(
                        operation,
                        f"delivery projection revoke failed ({type(exc).__name__})",
                        retryable=True,
                    ) from None
                reference_labels = getattr(reference, "labels", None)
                if (
                    getattr(reference, "name", None) != secret_name
                    or getattr(reference, "namespace", None) != self._config.namespace
                    or getattr(reference, "key", None) != expected_key_by_ref[secret_ref]
                    or getattr(reference, "delivered", None) is not False
                    or not isinstance(reference_labels, Mapping)
                    or dict(reference_labels) != dict(labels)
                ):
                    raise DeploymentSecretError(
                        operation, "delivery projection returned an out-of-scope revoke"
                    )

        existing = await self._get(operation, "core", "secrets", secret_name)
        if not refs:
            if existing is not None:
                raise DeploymentSecretError(
                    operation,
                    "delivery Secret exists without an active k8s_secret declaration",
                )
            return
        await self._delete_owned_delivery_secret(
            operation, name=secret_name, labels=labels
        )

    @staticmethod
    def _runtime_identity_projection() -> tuple[dict[str, Any], dict[str, Any]]:
        """Project non-secret actual identity without init containers or shell."""

        return (
            {
                "name": RUNTIME_IDENTITY_VOLUME_NAME,
                "downwardAPI": {
                    "items": [
                        {
                            "path": "plugin_instance_id",
                            "fieldRef": {
                                "fieldPath": (
                                    f"metadata.annotations['{PLUGIN_INSTANCE_ANNOTATION}']"
                                )
                            },
                        },
                        {
                            "path": "backend_digest",
                            "fieldRef": {
                                "fieldPath": f"metadata.annotations['{BACKEND_ANNOTATION}']"
                            },
                        },
                    ]
                },
            },
            {
                "name": RUNTIME_IDENTITY_VOLUME_NAME,
                "mountPath": RUNTIME_IDENTITY_MOUNT_PATH,
                "readOnly": True,
            },
        )

    def _deployment_manifest(
        self,
        *,
        operation: str,
        name: str,
        labels: Mapping[str, str],
        image: str,
        plugin_instance_id: str,
        artifact_digest: str,
        backend_digest: str,
        replicas: int | None,
        secret: SecretReference,
        update_owner_token: str | None = None,
        delivery_volume: dict[str, Any] | None = None,
        delivery_mount: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(plugin_instance_id, str) or not plugin_instance_id.strip():
            raise DeploymentConfigurationError(
                operation, "runtime identity projection requires plugin_instance_id"
            )
        projection = self._runtime_identity_projection()
        if (
            not isinstance(projection, tuple)
            or len(projection) != 2
            or not all(isinstance(entry, dict) and entry for entry in projection)
        ):
            raise DeploymentConfigurationError(
                operation, "runtime identity projection is absent or invalid"
            )
        identity_volume, identity_mount = projection
        if (
            identity_volume.get("name") != RUNTIME_IDENTITY_VOLUME_NAME
            or not isinstance(identity_volume.get("downwardAPI"), Mapping)
            or identity_mount
            != {
                "name": RUNTIME_IDENTITY_VOLUME_NAME,
                "mountPath": RUNTIME_IDENTITY_MOUNT_PATH,
                "readOnly": True,
            }
        ):
            raise DeploymentConfigurationError(
                operation, "runtime identity projection is absent or invalid"
            )
        annotations = {
            ARTIFACT_ANNOTATION: artifact_digest,
            BACKEND_ANNOTATION: backend_digest,
            PLUGIN_INSTANCE_ANNOTATION: plugin_instance_id,
        }
        if update_owner_token is not None:
            annotations[UPDATE_OWNER_ANNOTATION] = update_owner_token
        selector = {INSTANCE_LABEL: labels[INSTANCE_LABEL]}
        env = [
            {
                "name": key,
                "valueFrom": {"secretKeyRef": {"name": secret.name, "key": key}},
            }
            for key in SECRET_KEYS
        ]
        env.extend(
            (
                {"name": "HOME", "value": RUNTIME_TMP_MOUNT_PATH},
                {"name": "TMPDIR", "value": RUNTIME_TMP_MOUNT_PATH},
            )
        )
        env.append(
            {
                "name": "PLUGIN_ARTIFACT_DIGEST",
                "valueFrom": {
                    "fieldRef": {
                        "fieldPath": f"metadata.annotations['{ARTIFACT_ANNOTATION}']"
                    }
                },
            }
        )
        probe = {
            "httpGet": {"path": LIVENESS_PROBE_PATH, "port": "http", "scheme": "HTTP"},
            "timeoutSeconds": 2,
            "periodSeconds": 5,
            "failureThreshold": 6,
        }
        readiness_probe = {
            **dict(probe),
            "httpGet": {"path": READINESS_PROBE_PATH, "port": "http", "scheme": "HTTP"},
        }
        startup_probe = {**dict(probe), "failureThreshold": 60}
        spec: dict[str, Any] = {
            "selector": {"matchLabels": selector},
            "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}},
            "revisionHistoryLimit": 3,
            "progressDeadlineSeconds": 300,
            "template": {
                "metadata": {"labels": dict(labels), "annotations": annotations},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "fsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "backend",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"name": "http", "containerPort": self._config.service_port, "protocol": "TCP"}],
                            "env": env,
                            "resources": {
                                "requests": {
                                    "cpu": self._config.cpu_request,
                                    "memory": self._config.memory_request,
                                },
                                "limits": {
                                    "cpu": self._config.cpu_limit,
                                    "memory": self._config.memory_limit,
                                },
                            },
                            "readinessProbe": readiness_probe,
                            "livenessProbe": {**dict(probe), "initialDelaySeconds": 10},
                            "startupProbe": startup_probe,
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                },
            },
        }
        pod_spec = spec["template"]["spec"]
        pod_spec["volumes"] = [
            identity_volume,
            {"name": RUNTIME_TMP_VOLUME_NAME, "emptyDir": {}},
        ]
        pod_spec["containers"][0]["volumeMounts"] = [
            identity_mount,
            {
                "name": RUNTIME_TMP_VOLUME_NAME,
                "mountPath": RUNTIME_TMP_MOUNT_PATH,
                "readOnly": False,
            },
        ]
        if delivery_volume is not None and delivery_mount is not None:
            pod_spec["volumes"].append(delivery_volume)
            pod_spec["containers"][0]["volumeMounts"].append(delivery_mount)
        if self._config.image_pull_secret is not None:
            # LocalObjectReference only: registry credentials stay in the named
            # SealedSecret-managed Secret and never enter this Deployment.
            pod_spec["imagePullSecrets"] = [
                {"name": self._config.image_pull_secret}
            ]
        if replicas is not None:
            spec["replicas"] = replicas
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": name,
                "namespace": self._config.namespace,
                "labels": dict(labels),
                "annotations": annotations,
            },
            "spec": spec,
        }

    def _service_manifest(self, *, name: str, labels: Mapping[str, str]) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": name,
                "namespace": self._config.namespace,
                "labels": dict(labels),
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {INSTANCE_LABEL: labels[INSTANCE_LABEL]},
                "ports": [
                    {
                        "name": "http",
                        "port": self._config.service_port,
                        "targetPort": "http",
                        "protocol": "TCP",
                    }
                ],
            },
        }

    @staticmethod
    def _is_owned_target_state(
        deployment: Mapping[str, Any],
        *,
        manifest: Mapping[str, Any],
        update_owner_token: str | None,
    ) -> bool:
        """Match the canonical desired publication, never a caller-side digest.

        Both live annotation projections and the actual image must match the
        backend digest/ref from the already verified desired manifest. Artifact
        annotations are compared from that same manifest as an additional fence.
        """
        metadata = _mapping(deployment.get("metadata"))
        annotations = _mapping(metadata.get("annotations"))
        template_metadata = _mapping(
            _mapping(_mapping(deployment.get("spec")).get("template")).get("metadata")
        )
        template_annotations = _mapping(template_metadata.get("annotations"))
        desired_annotations = _mapping(
            _mapping(manifest.get("metadata")).get("annotations")
        )
        desired_backend = desired_annotations.get(BACKEND_ANNOTATION)
        desired_artifact = desired_annotations.get(ARTIFACT_ANNOTATION)
        desired_pod = _mapping(
            _mapping(_mapping(manifest.get("spec")).get("template")).get("spec")
        )
        desired_containers = desired_pod.get("containers")
        desired_image = (
            _mapping(desired_containers[0]).get("image")
            if isinstance(desired_containers, list) and desired_containers
            else None
        )
        actual_pod = _mapping(
            _mapping(_mapping(deployment.get("spec")).get("template")).get("spec")
        )
        actual_containers = actual_pod.get("containers")
        actual_image = (
            _mapping(actual_containers[0]).get("image")
            if isinstance(actual_containers, list) and actual_containers
            else None
        )
        return (
            isinstance(desired_backend, str)
            and is_valid_digest(desired_backend)
            and isinstance(desired_artifact, str)
            and is_valid_digest(desired_artifact)
            and isinstance(desired_image, str)
            and desired_image.endswith("@" + desired_backend)
            and annotations.get(BACKEND_ANNOTATION) == desired_backend
            and template_annotations.get(BACKEND_ANNOTATION) == desired_backend
            and annotations.get(ARTIFACT_ANNOTATION) == desired_artifact
            and template_annotations.get(ARTIFACT_ANNOTATION) == desired_artifact
            and actual_image == desired_image
            and annotations.get(UPDATE_OWNER_ANNOTATION) == update_owner_token
            and template_annotations.get(UPDATE_OWNER_ANNOTATION) == update_owner_token
        )

    @staticmethod
    def _matches_canonical_update(
        deployment: Mapping[str, Any],
        *,
        image: str,
        artifact_digest: str,
        backend_digest: str,
        update_owner_token: str | None,
    ) -> bool:
        """Match live state against canonical update inputs before any mutation.

        The replay short-circuit runs before Secret/Service mutation, when the
        full Deployment manifest is not assembled yet. Compare the very inputs
        the manifest builder projects into annotations and the container image
        instead of the artifact descriptor (which has no Deployment annotations).
        """
        if (
            not is_valid_digest(artifact_digest)
            or not is_valid_digest(backend_digest)
            or not isinstance(image, str)
            or not image
        ):
            return False
        metadata = _mapping(deployment.get("metadata"))
        annotations = _mapping(metadata.get("annotations"))
        template_metadata = _mapping(
            _mapping(_mapping(deployment.get("spec")).get("template")).get("metadata")
        )
        template_annotations = _mapping(template_metadata.get("annotations"))
        actual_pod = _mapping(
            _mapping(_mapping(deployment.get("spec")).get("template")).get("spec")
        )
        actual_containers = actual_pod.get("containers")
        actual_image = (
            _mapping(actual_containers[0]).get("image")
            if isinstance(actual_containers, list) and actual_containers
            else None
        )
        return (
            annotations.get(ARTIFACT_ANNOTATION) == artifact_digest
            and template_annotations.get(ARTIFACT_ANNOTATION) == artifact_digest
            and annotations.get(BACKEND_ANNOTATION) == backend_digest
            and template_annotations.get(BACKEND_ANNOTATION) == backend_digest
            and actual_image == image
            and annotations.get(UPDATE_OWNER_ANNOTATION) == update_owner_token
            and template_annotations.get(UPDATE_OWNER_ANNOTATION) == update_owner_token
        )

    def _validate_update_fence(
        self,
        operation: str,
        deployment: Mapping[str, Any],
        *,
        expected_current_artifact_digest: str,
        expected_current_update_owner_token: str | None,
    ) -> None:
        """Require both live annotation projections to match before every PATCH."""

        metadata = _mapping(deployment.get("metadata"))
        template_metadata = _mapping(
            _mapping(_mapping(deployment.get("spec")).get("template")).get("metadata")
        )
        annotations = _mapping(metadata.get("annotations"))
        template_annotations = _mapping(template_metadata.get("annotations"))
        live_digests = (
            annotations.get(ARTIFACT_ANNOTATION),
            template_annotations.get(ARTIFACT_ANNOTATION),
        )
        if any(value != expected_current_artifact_digest for value in live_digests):
            raise DeploymentArtifactDigestConflictError(operation)
        if expected_current_update_owner_token is not None:
            live_owners = (
                annotations.get(UPDATE_OWNER_ANNOTATION),
                template_annotations.get(UPDATE_OWNER_ANNOTATION),
            )
            if any(value != expected_current_update_owner_token for value in live_owners):
                raise DeploymentArtifactDigestConflictError(operation)

    async def _apply(
        self,
        operation: str,
        *,
        group: str,
        plural: str,
        kind: str,
        name: str,
        labels: Mapping[str, str],
        manifest: dict[str, Any],
        expected_current_artifact_digest: str | None = None,
        expected_current_update_owner_token: str | None = None,
        update_owner_token: str | None = None,
    ) -> None:
        if expected_current_artifact_digest is not None and kind != "Deployment":
            raise DeploymentConfigurationError(
                operation, "live artifact fence is valid only for Deployment updates"
            )
        collection = self._path(group, plural)
        for _attempt in range(self._apply_attempts):
            existing = await self._get(operation, group, plural, name)
            if existing is None:
                if expected_current_artifact_digest is not None:
                    raise DeploymentNotFoundError(operation, kind)
                try:
                    await self._request(operation, "POST", collection, body=manifest)
                    return
                except DeploymentConflictError:
                    continue
            self._validate_owner(operation, existing, kind=kind, name=name, labels=labels)
            if expected_current_artifact_digest is not None and not self._is_owned_target_state(
                existing,
                manifest=manifest,
                update_owner_token=update_owner_token,
            ):
                self._validate_update_fence(
                    operation,
                    existing,
                    expected_current_artifact_digest=expected_current_artifact_digest,
                    expected_current_update_owner_token=expected_current_update_owner_token,
                )
            existing_spec = _mapping(existing.get("spec"))
            desired_spec = copy.deepcopy(manifest["spec"])
            if kind == "Deployment":
                # Replay and a lost create race must never reset active replicas.
                desired_spec.pop("replicas", None)
                # JSON merge-patch replaces lists wholesale. Replace only our
                # managed identity/delivery entries and retain every sibling so
                # an update cannot drop delivery or other existing mounts.
                desired_pod = desired_spec["template"]["spec"]
                existing_pod = _mapping(_mapping(existing_spec.get("template")).get("spec"))
                desired_volumes = desired_pod.get("volumes")
                existing_volumes = existing_pod.get("volumes")
                if not isinstance(desired_volumes, list) or not any(
                    _mapping(volume).get("name") == RUNTIME_IDENTITY_VOLUME_NAME
                    and "downwardAPI" in _mapping(volume)
                    for volume in desired_volumes
                ):
                    raise DeploymentConfigurationError(
                        operation, "runtime identity projection is absent from Deployment manifest"
                    )
                if isinstance(existing_volumes, list):
                    retained = [
                        volume
                        for volume in existing_volumes
                        if _mapping(volume).get("name")
                        not in {RUNTIME_IDENTITY_VOLUME_NAME, RUNTIME_TMP_VOLUME_NAME}
                        and not str(_mapping(volume).get("name", "")).startswith(
                            DELIVERY_VOLUME_PREFIX
                        )
                    ]
                    desired_pod["volumes"] = [*desired_volumes, *retained]
                desired_containers = desired_pod.get("containers")
                existing_containers = existing_pod.get("containers")
                if (
                    isinstance(desired_containers, list)
                    and desired_containers
                    and isinstance(existing_containers, list)
                    and existing_containers
                ):
                    desired_backend = desired_containers[0]
                    existing_backend = _mapping(existing_containers[0])
                    desired_mounts = desired_backend.get("volumeMounts")
                    existing_mounts = existing_backend.get("volumeMounts")
                    if not isinstance(desired_mounts, list) or not any(
                        mount
                        == {
                            "name": RUNTIME_IDENTITY_VOLUME_NAME,
                            "mountPath": RUNTIME_IDENTITY_MOUNT_PATH,
                            "readOnly": True,
                        }
                        for mount in desired_mounts
                    ):
                        raise DeploymentConfigurationError(
                            operation,
                            "runtime identity mount is absent from Deployment manifest",
                        )
                    if isinstance(existing_mounts, list):
                        retained_mounts = [
                            mount
                            for mount in existing_mounts
                            if _mapping(mount).get("name")
                            not in {RUNTIME_IDENTITY_VOLUME_NAME, RUNTIME_TMP_VOLUME_NAME}
                            and not str(_mapping(mount).get("name", "")).startswith(
                                DELIVERY_VOLUME_PREFIX
                            )
                        ]
                        desired_backend["volumeMounts"] = [
                            *desired_mounts,
                            *retained_mounts,
                        ]
            expected_metadata: dict[str, Any] = {"labels": dict(labels)}
            expected_annotations = dict(manifest["metadata"].get("annotations", {}))
            # An absent annotations map and an empty desired map are equivalent.
            # Requiring the key for an empty map caused a write on every Service
            # replay even when labels/spec already matched exactly.
            if expected_annotations:
                expected_metadata["annotations"] = expected_annotations
            expected = {
                "metadata": expected_metadata,
                "spec": desired_spec,
            }
            if kind == "Service":
                actual_selector = _mapping(existing_spec.get("selector"))
                desired_selector = _mapping(desired_spec.get("selector"))
            else:
                actual_selector = _mapping(existing_spec.get("selector"))
                desired_selector = _mapping(desired_spec.get("selector"))
            selector_matches = actual_selector == desired_selector
            if selector_matches and _contains(expected, existing):
                return
            if not selector_matches:
                # Merge-patch maps merge keys, so explicitly null out broadened
                # selector labels instead of silently adopting other instances.
                removals = {key: None for key in actual_selector.keys() - desired_selector.keys()}
                if kind == "Service":
                    desired_spec["selector"] = {**desired_selector, **removals}
                else:
                    actual_match = _mapping(actual_selector.get("matchLabels"))
                    desired_match = _mapping(desired_selector.get("matchLabels"))
                    match_removals = {
                        key: None for key in actual_match.keys() - desired_match.keys()
                    }
                    desired_spec["selector"] = {
                        **desired_selector,
                        **removals,
                        "matchLabels": {**desired_match, **match_removals},
                    }
            if expected_current_artifact_digest is not None:
                desired_pod_annotations = _mapping(
                    _mapping(desired_spec["template"]).get("metadata")
                ).setdefault("annotations", {})
                desired_pod_annotations[UPDATE_OWNER_ANNOTATION] = update_owner_token
            patch = {
                "metadata": {
                    "name": name,
                    "namespace": self._config.namespace,
                    "labels": dict(labels),
                    "annotations": dict(manifest["metadata"].get("annotations", {})),
                },
                "spec": desired_spec,
            }
            if expected_current_artifact_digest is not None:
                patch["metadata"]["annotations"][UPDATE_OWNER_ANNOTATION] = update_owner_token
            resource_version = _mapping(existing.get("metadata")).get("resourceVersion")
            if not isinstance(resource_version, str) or not resource_version:
                raise DeploymentUnavailableError(
                    operation, f"{kind} has no resourceVersion for a safe conditional patch"
                )
            patch["metadata"]["resourceVersion"] = resource_version
            try:
                await self._request(
                    operation,
                    "PATCH",
                    self._path(group, plural, name),
                    body=patch,
                    content_type=MERGE_PATCH,
                )
                return
            except DeploymentConflictError:
                continue
        raise DeploymentConflictError(operation, kind)

    async def _ensure_or_update(
        self,
        operation: str,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        dsn_ref: str,
        webhook_secret_ref: str,
        expected_current_artifact_digest: str | None = None,
        update_owner_token: str | None = None,
        expected_current_update_owner_token: str | None = None,
    ) -> str:
        is_update = operation == "update"
        if is_update and not is_valid_digest(expected_current_artifact_digest):
            raise DeploymentStateError(
                operation, "expected_current_artifact_digest is required and must be a valid digest"
            )
        if not is_update and expected_current_artifact_digest is not None:
            raise DeploymentConfigurationError(
                operation, "expected_current_artifact_digest is valid only for update"
            )
        owner_token_valid = (
            isinstance(update_owner_token, str)
            and bool(update_owner_token)
            and len(update_owner_token) <= 256
            and not any(char.isspace() or char == "\0" for char in update_owner_token)
        )
        if is_update and (
            (expected_current_update_owner_token is None and not owner_token_valid)
            or (update_owner_token is not None and not owner_token_valid)
        ):
            raise DeploymentStateError(
                operation,
                "forward update requires an owner; rollback may restore a previously absent owner",
            )
        if expected_current_update_owner_token is not None and (
            not expected_current_update_owner_token
            or len(expected_current_update_owner_token) > 256
            or any(
                char.isspace() or char == "\0"
                for char in expected_current_update_owner_token
            )
        ):
            raise DeploymentStateError(operation, "expected live update owner is invalid")
        if not is_update and (
            update_owner_token is not None or expected_current_update_owner_token is not None
        ):
            raise DeploymentConfigurationError(
                operation, "update owner fence is valid only for update"
            )
        durable_pin = (
            artifact_digest
            if is_update and expected_current_update_owner_token is not None
            else (expected_current_artifact_digest if is_update else artifact_digest)
        )
        instance = await self._instance_by_id(
            operation,
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=durable_pin,
        )
        image, backend_digest, artifact_manifest = await self._backend_image(
            operation,
            plugin_id=plugin_id,
            artifact_digest=artifact_digest,
            expected_version=(
                None if is_update else str(getattr(instance, "manifest_version", ""))
            ),
        )
        name = self.resource_name(plugin_id, plugin_instance_id)
        labels = _labels(company_id, plugin_id, plugin_instance_id)
        existing: dict[str, Any] | None = None
        if is_update:
            # Deny a stale/foreign live runtime before Secret or Service mutation too.
            existing = await self._get(operation, "apps", "deployments", name)
            if existing is None:
                raise DeploymentNotFoundError(operation, "Deployment")
            self._validate_owner(
                operation, existing, kind="Deployment", name=name, labels=labels
            )
            assert expected_current_artifact_digest is not None
            if not self._matches_canonical_update(
                existing,
                image=image,
                artifact_digest=artifact_digest,
                backend_digest=backend_digest,
                update_owner_token=update_owner_token,
            ):
                self._validate_update_fence(
                    operation,
                    existing,
                    expected_current_artifact_digest=expected_current_artifact_digest,
                    expected_current_update_owner_token=expected_current_update_owner_token,
                )
        delivery_volume, delivery_mount = await self._delivery_mount(
            operation,
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            manifest=artifact_manifest,
            labels=labels,
        )
        secret_reference = await self._project_secret(
            operation,
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            name=name,
            labels=labels,
            dsn_ref=dsn_ref,
            webhook_secret_ref=webhook_secret_ref,
        )
        await self._apply(
            operation,
            group="core",
            plural="services",
            kind="Service",
            name=name,
            labels=labels,
            manifest=self._service_manifest(name=name, labels=labels),
        )
        if not is_update:
            existing = await self._get(operation, "apps", "deployments", name)
        replicas = None if existing is not None else 0
        await self._apply(
            operation,
            group="apps",
            plural="deployments",
            kind="Deployment",
            name=name,
            labels=labels,
            manifest=self._deployment_manifest(
                operation=operation,
                name=name,
                labels=labels,
                image=image,
                plugin_instance_id=plugin_instance_id,
                artifact_digest=artifact_digest,
                backend_digest=backend_digest,
                replicas=replicas,
                secret=secret_reference,
                update_owner_token=update_owner_token,
                delivery_volume=delivery_volume,
                delivery_mount=delivery_mount,
            ),
            expected_current_artifact_digest=expected_current_artifact_digest,
            expected_current_update_owner_token=expected_current_update_owner_token,
            update_owner_token=update_owner_token,
        )
        return name

    async def ensure_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        dsn_ref: str,
        webhook_secret_ref: str,
    ) -> str:
        return await self._ensure_or_update(
            "ensure",
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=artifact_digest,
            dsn_ref=dsn_ref,
            webhook_secret_ref=webhook_secret_ref,
        )

    async def ensure(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        dsn_ref: str,
        webhook_secret_ref: str,
    ) -> str:
        return await self.ensure_deployment(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=artifact_digest,
            dsn_ref=dsn_ref,
            webhook_secret_ref=webhook_secret_ref,
        )

    async def update_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        expected_current_artifact_digest: str,
        dsn_ref: str,
        webhook_secret_ref: str,
        update_owner_token: str | None,
        expected_current_update_owner_token: str | None = None,
    ) -> str:
        return await self._ensure_or_update(
            "update",
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=artifact_digest,
            expected_current_artifact_digest=expected_current_artifact_digest,
            dsn_ref=dsn_ref,
            webhook_secret_ref=webhook_secret_ref,
            update_owner_token=update_owner_token,
            expected_current_update_owner_token=expected_current_update_owner_token,
        )

    async def update(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        expected_current_artifact_digest: str,
        dsn_ref: str,
        webhook_secret_ref: str,
        update_owner_token: str | None,
        expected_current_update_owner_token: str | None = None,
    ) -> str:
        return await self.update_deployment(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=artifact_digest,
            expected_current_artifact_digest=expected_current_artifact_digest,
            dsn_ref=dsn_ref,
            webhook_secret_ref=webhook_secret_ref,
            update_owner_token=update_owner_token,
            expected_current_update_owner_token=expected_current_update_owner_token,
        )

    async def runtime_binding(
        self, *, company_id: str, plugin_id: str
    ) -> PluginRuntimeBinding:
        record = await self._instance("binding", company_id=company_id, plugin_id=plugin_id)
        instance_id = record.plugin_instance_id
        name = self.resource_name(plugin_id, instance_id)
        deployment = await self._get("binding", "apps", "deployments", name)
        if deployment is None:
            raise DeploymentNotFoundError("binding", "Deployment")
        self._validate_owner(
            "binding",
            deployment,
            kind="Deployment",
            name=name,
            labels=_labels(company_id, plugin_id, instance_id),
        )
        metadata = _mapping(deployment.get("metadata"))
        annotations = _mapping(metadata.get("annotations"))
        template_metadata = _mapping(
            _mapping(_mapping(deployment.get("spec")).get("template")).get("metadata")
        )
        template_annotations = _mapping(template_metadata.get("annotations"))
        digest = annotations.get(ARTIFACT_ANNOTATION)
        if (
            not isinstance(digest, str)
            or not is_valid_digest(digest)
            or template_annotations.get(ARTIFACT_ANNOTATION) != digest
            or annotations.get(PLUGIN_INSTANCE_ANNOTATION) != instance_id
            or template_annotations.get(PLUGIN_INSTANCE_ANNOTATION) != instance_id
        ):
            raise DeploymentStateError(
                "binding", "live Deployment identity/artifact annotations are absent or split"
            )
        owner = annotations.get(UPDATE_OWNER_ANNOTATION)
        if owner is not None and (not isinstance(owner, str) or not owner):
            raise DeploymentStateError("binding", "live Deployment update owner is invalid")
        if template_annotations.get(UPDATE_OWNER_ANNOTATION) != owner:
            raise DeploymentStateError("binding", "live Deployment update owner is split")
        return PluginRuntimeBinding(
            plugin_instance_id=instance_id,
            artifact_digest=digest,
            update_owner_token=owner,
        )

    async def runtime_status(self, *, company_id: str, plugin_id: str) -> PluginRuntimeStatus:
        try:
            record = await self._instance("status", company_id=company_id, plugin_id=plugin_id)
        except DeploymentNotFoundError:
            return PluginRuntimeStatus.ABSENT
        instance_id = record.plugin_instance_id
        name = self.resource_name(plugin_id, instance_id)
        deployment = await self._get("status", "apps", "deployments", name)
        if deployment is None:
            return PluginRuntimeStatus.ABSENT
        self._validate_owner(
            "status",
            deployment,
            kind="Deployment",
            name=name,
            labels=_labels(company_id, plugin_id, instance_id),
        )
        spec = _mapping(deployment.get("spec"))
        status = _mapping(deployment.get("status"))
        replicas = spec.get("replicas", 0)
        if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 0:
            raise DeploymentUnavailableError("status", "Deployment has invalid spec.replicas")
        if replicas > 0:
            ready = status.get("readyReplicas", 0)
            available = status.get("availableReplicas", 0)
            if (
                isinstance(ready, int)
                and not isinstance(ready, bool)
                and isinstance(available, int)
                and not isinstance(available, bool)
                and ready >= replicas
                and available >= replicas
            ):
                return PluginRuntimeStatus.RUNNING
            return PluginRuntimeStatus.IDLE
        if getattr(record, "status", None) in {
            PluginInstanceStatus.SUSPENDED,
            PluginInstanceStatus.REVOKED,
        }:
            return PluginRuntimeStatus.SUSPENDED
        return PluginRuntimeStatus.IDLE

    async def status(self, *, company_id: str, plugin_id: str) -> PluginRuntimeStatus:
        return await self.runtime_status(company_id=company_id, plugin_id=plugin_id)

    async def _pods_gone(self, operation: str, instance_selector: str) -> None:
        query = urlencode({"labelSelector": f"{INSTANCE_LABEL}={instance_selector}"})
        body = await self._request(
            operation, "GET", f"{self._path('core', 'pods')}?{query}"
        )
        if body is None or body.get("kind") != "PodList" or not isinstance(body.get("items"), list):
            raise DeploymentUnavailableError(operation, "Kubernetes returned an invalid PodList")
        if body["items"]:
            raise DeploymentUnavailableError(
                operation, "scale-to-zero is not confirmed: instance Pods still exist"
            )

    async def _scale(
        self,
        operation: str,
        *,
        company_id: str,
        plugin_id: str,
        replicas: int,
    ) -> str:
        record = await self._instance(operation, company_id=company_id, plugin_id=plugin_id)
        if operation == "resume" and getattr(record, "status", None) not in {
            PluginInstanceStatus.ACTIVE,
            PluginInstanceStatus.SUSPENDED,
        }:
            raise DeploymentStateError(
                operation, "only an active or deliberately suspended instance can resume"
            )
        instance_id = record.plugin_instance_id
        name = self.resource_name(plugin_id, instance_id)
        labels = _labels(company_id, plugin_id, instance_id)
        for _attempt in range(self._apply_attempts):
            deployment = await self._get(operation, "apps", "deployments", name)
            if deployment is None:
                if replicas == 0:
                    await self._pods_gone(operation, labels[INSTANCE_LABEL])
                    return name
                raise DeploymentNotFoundError(operation, "Deployment")
            self._validate_owner(
                operation, deployment, kind="Deployment", name=name, labels=labels
            )
            current = _mapping(deployment.get("spec")).get("replicas")
            if current != replicas:
                resource_version = _mapping(deployment.get("metadata")).get("resourceVersion")
                if not isinstance(resource_version, str) or not resource_version:
                    raise DeploymentUnavailableError(
                        operation, "Deployment has no resourceVersion for a safe scale"
                    )
                scale_body = {
                    "apiVersion": "autoscaling/v1",
                    "kind": "Scale",
                    "metadata": {
                        "name": name,
                        "namespace": self._config.namespace,
                        "resourceVersion": resource_version,
                    },
                    "spec": {"replicas": replicas},
                }
                try:
                    await self._request(
                        operation,
                        "PATCH",
                        f"{self._path('apps', 'deployments', name)}/scale",
                        body=scale_body,
                        content_type=MERGE_PATCH,
                    )
                except DeploymentConflictError:
                    continue
            scale = await self._request(
                operation, "GET", f"{self._path('apps', 'deployments', name)}/scale"
            )
            if _mapping(scale).get("kind") != "Scale":
                raise DeploymentUnavailableError(
                    operation, "scale subresource returned a non-Scale object"
                )
            actual = _mapping(_mapping(scale).get("spec")).get("replicas")
            # autoscaling/v1 omits Scale.spec.replicas for the zero value on GET.
            # Read an omitted field as zero ONLY while scaling to zero; a missing
            # field for any non-zero target remains a hard confirmation failure.
            effective = 0 if actual is None and replicas == 0 else actual
            if effective != replicas:
                raise DeploymentUnavailableError(
                    operation, f"scale subresource did not confirm replicas={replicas}"
                )
            if replicas == 0:
                await self._pods_gone(operation, labels[INSTANCE_LABEL])
            return name
        raise DeploymentConflictError(operation, "Deployment")

    async def suspend(
        self,
        *,
        company_id: str,
        plugin_id: str,
        cleanup_delivery: bool = False,
    ) -> bool:
        """Scale to zero, retaining delivery projection unless this is a security revoke.

        Idle suspension keeps the existing Secret values and Deployment volume/mount so a
        later scale-up can mount the same projection without implicit pre-delivery. Admin
        revoke opts into the scoped revoke/delete cleanup explicitly; resume after revoke
        still requires a new grant/pre-delivery lifecycle.
        """
        await self._scale("suspend", company_id=company_id, plugin_id=plugin_id, replicas=0)
        if not cleanup_delivery:
            return True
        record = await self._instance("suspend", company_id=company_id, plugin_id=plugin_id)
        instance_id = record.plugin_instance_id
        await self._cleanup_delivery_secret(
            "suspend",
            record=record,
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=instance_id,
            labels=_labels(company_id, plugin_id, instance_id),
        )
        return True

    async def resume(self, *, company_id: str, plugin_id: str) -> str:
        return await self._scale("resume", company_id=company_id, plugin_id=plugin_id, replicas=1)

    async def uninstall(self, *, company_id: str, plugin_id: str) -> bool:
        record = await self._instance("uninstall", company_id=company_id, plugin_id=plugin_id)
        instance_id = record.plugin_instance_id
        name = self.resource_name(plugin_id, instance_id)
        labels = _labels(company_id, plugin_id, instance_id)
        await self._cleanup_delivery_secret(
            "uninstall",
            record=record,
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=instance_id,
            labels=labels,
        )
        for group, plural, kind in (
            ("apps", "deployments", "Deployment"),
            ("core", "services", "Service"),
            ("core", "secrets", "Secret"),
        ):
            for _attempt in range(self._apply_attempts):
                body = await self._get("uninstall", group, plural, name)
                if body is None:
                    break
                self._validate_owner(
                    "uninstall", body, kind=kind, name=name, labels=labels
                )
                resource_version = _mapping(body.get("metadata")).get("resourceVersion")
                if not isinstance(resource_version, str) or not resource_version:
                    raise DeploymentUnavailableError(
                        "uninstall", f"{kind} has no resourceVersion for a safe delete"
                    )
                delete_options = {
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "preconditions": {"resourceVersion": resource_version},
                }
                try:
                    await self._request(
                        "uninstall",
                        "DELETE",
                        self._path(group, plural, name),
                        body=delete_options,
                        content_type="application/json",
                    )
                except DeploymentConflictError:
                    continue
                if await self._get("uninstall", group, plural, name) is None:
                    break
            else:
                raise DeploymentConflictError("uninstall", kind)
        return True

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool:
        return await self.uninstall(company_id=company_id, plugin_id=plugin_id)


__all__ = [
    "ARTIFACT_ANNOTATION",
    "ArtifactDigestLookupPort",
    "DELIVERY_DEFAULT_MODE",
    "DELIVERY_MOUNT_PATH",
    "DELIVERY_VOLUME_PREFIX",
    "ArtifactProvenanceError",
    "BACKEND_ANNOTATION",
    "COMPANY_LABEL",
    "DSN_REF_ANNOTATION",
    "DeploymentAdminError",
    "DeploymentConfigurationError",
    "DeploymentConflictError",
    "DeploymentInstanceLookupPort",
    "DeploymentNotFoundError",
    "DeploymentOwnershipError",
    "DeploymentSecretError",
    "DeploymentStateError",
    "DeploymentUnavailableError",
    "INSTANCE_LABEL",
    "K8sDeploymentAdmin",
    "MANAGED_BY",
    "MANAGED_BY_VALUE",
    "PLUGIN_INSTANCE_ANNOTATION",
    "PLUGIN_LABEL",
    "RUNTIME_IDENTITY_MOUNT_PATH",
    "RUNTIME_IDENTITY_VOLUME_NAME",
    "RUNTIME_TMP_MOUNT_PATH",
    "RUNTIME_TMP_VOLUME_NAME",
    "SECRET_KEYS",
]
