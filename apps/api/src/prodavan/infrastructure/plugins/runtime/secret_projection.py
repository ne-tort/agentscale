"""Per-instance Kubernetes Secret projection for tenant DSN/webhook secrets.

The projection resolves the existing tenant SecretPort and a separate trusted
webhook resolver only through their canonical references. Plaintext stays in local variables,
is base64-encoded for the Kubernetes API, and is never returned, logged,
persisted by this module, or included in exception messages.

A Secret is bound to the exact source dsn_ref by a SHA-256 annotation. That
hash is only an ownership/binding marker, never a value-version marker. Managed
values are compared on every call: any DSN/gateway/plugin change PATCHes the
same Secret with resourceVersion CAS, including rotation under an unchanged
dsn_ref. Ownership, exact data keys, and non-empty values are checked before
every reconcile decision. Foreign or structurally malformed Secrets are never
deleted or adopted.

Optional capability delivery uses a separate canonical per-instance Secret. Its
data keys are SHA-256 bindings to exact approved source refs, and annotations
contain only content digests. Rotation uses resourceVersion CAS; revoke removes
only the matching data key. Lifecycle Secret key validation is not relaxed.

Only the existing injectable K8sApiPort REST client is used: no Kubernetes SDK,
kubectl, subprocess, file access, Vault access, or database writes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote, urlsplit

from prodavan.application.plugins.tenantdb.service import SecretPort, StoredSecret

from .config import RuntimeConfig
from .errors import PluginRuntimeError, RuntimeConfigError
from .k8s_scale import (
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
MANAGED_BY_VALUE = "plugin-deployment-admin"
BASE_SECRET_KEYS = ("GATEWAY_URL", "PLUGIN_ID", "TENANT_DB_DSN")
WEBHOOK_SECRET_KEY = "WEBHOOK_SECRET"
SECRET_KEYS = (*BASE_SECRET_KEYS, WEBHOOK_SECRET_KEY)
DELIVERY_VERSIONS_ANNOTATION = "agentscale.ne-tort/delivered-secret-versions"
DELIVERY_SECRET_PREFIX = "capability-secrets-"

_TENANT_KEY_PREFIX = "plugins/tenantdb"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_VALUE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DELIVERY_DATA_KEY = re.compile(r"^SECRET_[0-9A-F]{64}$")
_OPERATION = "ensure_instance_secret"


class SecretProjectionError(PluginRuntimeError):
    """Safe projection failure; messages never contain refs or secret values."""

    def __init__(
        self,
        *,
        code: str,
        detail: str,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.operation = _OPERATION
        self.retryable = bool(retryable)
        super().__init__(f"{_OPERATION}: {detail} [{code}]")


class SecretProjectionConfigError(SecretProjectionError):
    def __init__(self, detail: str) -> None:
        super().__init__(code="SECRET_PROJECTION_CONFIG_INVALID", detail=detail)


class TenantSecretResolutionError(SecretProjectionError):
    def __init__(self, detail: str, *, retryable: bool = False) -> None:
        super().__init__(
            code="TENANT_DSN_RESOLUTION_FAILED",
            detail=detail,
            retryable=retryable,
        )


class WebhookSecretResolutionError(SecretProjectionError):
    def __init__(self, detail: str, *, retryable: bool = False) -> None:
        super().__init__(
            code="WEBHOOK_SECRET_RESOLUTION_FAILED",
            detail=detail,
            retryable=retryable,
        )


class SecretOwnershipError(SecretProjectionError):
    def __init__(self) -> None:
        super().__init__(
            code="SECRET_OWNERSHIP_MISMATCH",
            detail="Secret is absent, foreign, or not managed for this instance",
        )


class SecretDriftError(SecretProjectionError):
    def __init__(self, detail: str = "Secret drift or invalid projected state detected") -> None:
        super().__init__(code="SECRET_DRIFT_DETECTED", detail=detail)


class SecretConflictError(SecretProjectionError):
    def __init__(self) -> None:
        super().__init__(
            code="K8S_CONFLICT",
            detail="Secret changed concurrently; retry the lifecycle operation",
            retryable=True,
        )


class SecretUnavailableError(SecretProjectionError):
    def __init__(self, detail: str = "Kubernetes Secret API unavailable") -> None:
        super().__init__(
            code="SECRET_PROJECTION_UNAVAILABLE",
            detail=detail,
            retryable=True,
        )


@dataclass(frozen=True, slots=True)
class SecretReference:
    """Plaintext-free canonical reference consumed by DeploymentAdmin.

    labels contains only canonical ownership labels. dsn_ref_sha256 is the full
    lowercase SHA-256 of the source reference, never of the DSN value.
    """

    name: str
    namespace: str
    labels: Mapping[str, str]
    dsn_ref_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


@dataclass(frozen=True, slots=True)
class SecretDeliveryReference:
    """Safe projection metadata; never contains a source ref or plaintext."""

    name: str
    namespace: str
    key: str
    labels: Mapping[str, str]
    value_digest: str
    version: str
    delivered: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


@runtime_checkable
class WebhookSecretResolverPort(Protocol):
    """Trusted internal resolver; no manifest, URL, or DSN-key resolution."""

    async def resolve(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
    ) -> str: ...


@runtime_checkable
class SecretProjectionPort(Protocol):
    """Stable composition seam for DeploymentAdmin/install overrides."""

    async def ensure_instance_secret(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        dsn_ref: str,
        gateway_url: str | None = None,
        webhook_secret_ref: str | None = None,
    ) -> SecretReference: ...


@runtime_checkable
class SecretDeliveryProjectionPort(Protocol):
    """Optional typed-delivery seam used by the secrets capability adapter."""

    async def deliver_secret(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
        value: str,
    ) -> SecretDeliveryReference: ...

    async def reconcile_secret_values(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        values: Mapping[str, str],
    ) -> tuple[SecretDeliveryReference, ...]: ...

    async def revoke_secret(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
    ) -> SecretDeliveryReference: ...


@dataclass(frozen=True, slots=True)
class _SecretState:
    resource_version: str
    dsn_ref_sha256: str
    values: Mapping[str, bytes] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _DeliveryState:
    resource_version: str
    values: Mapping[str, bytes] = field(repr=False)
    versions: Mapping[str, str] = field(default_factory=dict)


def _hash(value: str, *, size: int = 40) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


def _ownership_labels(company_id: str, plugin_id: str, plugin_instance_id: str) -> dict[str, str]:
    return {
        MANAGED_BY: MANAGED_BY_VALUE,
        COMPANY_LABEL: _hash(company_id),
        PLUGIN_LABEL: _hash(plugin_id),
        INSTANCE_LABEL: _hash(f"{company_id}\0{plugin_id}\0{plugin_instance_id}"),
    }


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


class K8sSecretProjection:
    """Reconcile lifecycle and optional delivery Secrets for one exact instance."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        tenant_secrets: SecretPort,
        webhook_resolver: WebhookSecretResolverPort | None = None,
        gateway_url: str | None = None,
        api: K8sApiPort | None = None,
        client: Any = None,
        auth: Any = None,
        apply_attempts: int = 3,
        max_delivery_secret_bytes: int = 65536,
    ) -> None:
        if not isinstance(config, RuntimeConfig):
            raise RuntimeConfigError("K8sSecretProjection requires RuntimeConfig")
        if not callable(getattr(tenant_secrets, "get", None)):
            raise RuntimeConfigError("K8sSecretProjection requires tenant SecretPort.get()")
        if (
            not isinstance(apply_attempts, int)
            or isinstance(apply_attempts, bool)
            or apply_attempts < 1
        ):
            raise RuntimeConfigError("apply_attempts must be a positive integer")
        if (
            not isinstance(max_delivery_secret_bytes, int)
            or isinstance(max_delivery_secret_bytes, bool)
            or not 1 <= max_delivery_secret_bytes <= 1024 * 1024
        ):
            raise RuntimeConfigError("max_delivery_secret_bytes must be between 1 and 1048576")
        if webhook_resolver is not None and not callable(getattr(webhook_resolver, "resolve", None)):
            raise RuntimeConfigError("webhook_resolver must provide resolve()")
        self._config = config
        self._tenant_secrets = tenant_secrets
        self._webhook_resolver = webhook_resolver
        self._gateway_url = gateway_url
        self._api: K8sApiPort = api or K8sApiClient(auth=auth, client=client)
        self._apply_attempts = apply_attempts
        self._max_delivery_secret_bytes = max_delivery_secret_bytes

    @property
    def api(self) -> K8sApiPort:
        return self._api

    @staticmethod
    def _validate_identity(company_id: str, plugin_id: str, plugin_instance_id: str) -> None:
        for value in (company_id, plugin_id, plugin_instance_id):
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > 256
                or any(char.isspace() or char == "\0" for char in value)
            ):
                raise SecretProjectionConfigError("lifecycle identity is empty or invalid")

    @staticmethod
    def _gateway(value: str | None, configured: str | None) -> str:
        candidate = configured if value is None else value
        if (
            not isinstance(candidate, str)
            or not candidate
            or candidate != candidate.strip()
            or len(candidate) > 2048
            or any(char.isspace() or char == "\0" for char in candidate)
        ):
            raise SecretProjectionConfigError("trusted gateway_url is absent or invalid")
        try:
            parsed = urlsplit(candidate)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
            )
            parsed.port  # Validate malformed ports without logging the URL.
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise SecretProjectionConfigError("trusted gateway_url is absent or invalid")
        return candidate

    @staticmethod
    def _tenant_key_and_validate_ref(
        *,
        company_id: str,
        plugin_id: str,
        dsn_ref: str,
    ) -> str:
        if (
            not isinstance(dsn_ref, str)
            or not dsn_ref
            or len(dsn_ref) > 2048
            or any(char.isspace() or char == "\0" for char in dsn_ref)
        ):
            raise SecretProjectionConfigError("dsn_ref is not a canonical tenant secret reference")
        key = (
            f"{_TENANT_KEY_PREFIX}/{quote(company_id, safe='')}/"
            f"{quote(plugin_id, safe='')}/TENANT_DB_DSN"
        )
        allowed_refs = {
            f"file://ai_keys/{key}.secret",
            f"vault://ai_keys/{key}",
        }
        if dsn_ref not in allowed_refs and not dsn_ref.startswith("k8s-secret://"):
            # File/Vault retain their exact per-key canonical shape. A mounted
            # Kubernetes ref is opaque here; _resolve_dsn still requires the
            # trusted SecretPort record for this key to carry that exact ref.
            raise SecretProjectionConfigError("dsn_ref is not a canonical tenant secret reference")
        return key

    async def _resolve_dsn(self, *, key: str, dsn_ref: str) -> str:
        try:
            stored = await self._tenant_secrets.get(key=key)
        except Exception:
            # Backend exceptions may contain plaintext; never chain their text.
            raise TenantSecretResolutionError(
                "tenant secret backend read failed", retryable=True
            ) from None
        if stored is None:
            raise TenantSecretResolutionError("tenant DSN secret is not provisioned")
        if (
            not isinstance(stored, StoredSecret)
            or stored.secret_ref != dsn_ref
            or not isinstance(stored.value, str)
            or not stored.value.strip()
        ):
            raise TenantSecretResolutionError("tenant DSN secret is missing, foreign, or empty")
        return stored.value

    async def _resolve_webhook(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
    ) -> str:
        if (
            not isinstance(secret_ref, str)
            or not secret_ref
            or secret_ref != secret_ref.strip()
            or len(secret_ref) > 2048
            or any(char.isspace() or char == "\0" for char in secret_ref)
            or not secret_ref.startswith(("file://", "vault://", "k8s-secret://"))
        ):
            raise SecretProjectionConfigError("webhook_secret_ref is not an opaque secret reference")
        if self._webhook_resolver is None:
            raise WebhookSecretResolutionError("trusted webhook resolver is not configured")
        try:
            value = await self._webhook_resolver.resolve(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=plugin_instance_id,
                secret_ref=secret_ref,
            )
        except Exception:
            raise WebhookSecretResolutionError(
                "trusted webhook secret resolution failed", retryable=True
            ) from None
        if not isinstance(value, str) or not value.strip():
            raise WebhookSecretResolutionError("webhook secret is missing or empty")
        return value

    def _path(self, name: str | None = None) -> str:
        base = f"{CORE_API}/namespaces/{self._config.namespace}/secrets"
        return f"{base}/{name}" if name else base

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        content_type: str | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        try:
            response = await self._api.request(
                method,
                path,
                body=body,
                content_type=content_type,
            )
        except K8sApiNotFound:
            if allow_not_found:
                return None
            raise SecretUnavailableError("required Kubernetes Secret is absent") from None
        except K8sApiConflict:
            raise SecretConflictError() from None
        except K8sApiError as exc:
            raise SecretUnavailableError(
                f"Kubernetes API rejected Secret operation ({type(exc).__name__})"
            ) from None
        except Exception as exc:
            raise SecretUnavailableError(
                f"Kubernetes Secret transport failed ({type(exc).__name__})"
            ) from None
        payload = getattr(response, "body", None)
        if not isinstance(payload, dict):
            raise SecretUnavailableError("Kubernetes returned an invalid Secret object")
        return payload

    def _inspect(
        self,
        secret: Mapping[str, Any],
        *,
        name: str,
        labels: Mapping[str, str],
        plugin_id: str,
    ) -> _SecretState:
        metadata = _mapping(secret.get("metadata"))
        actual_labels = _mapping(metadata.get("labels"))
        if (
            secret.get("apiVersion") != "v1"
            or secret.get("kind") != "Secret"
            or metadata.get("name") != name
            or metadata.get("namespace") != self._config.namespace
            or metadata.get("deletionTimestamp") is not None
            or any(actual_labels.get(key) != value for key, value in labels.items())
        ):
            raise SecretOwnershipError()

        resource_version = metadata.get("resourceVersion")
        if not isinstance(resource_version, str) or not resource_version:
            raise SecretDriftError("Secret resourceVersion is absent or invalid")
        annotations = _mapping(metadata.get("annotations"))
        source_hash = annotations.get(DSN_REF_ANNOTATION)
        if not isinstance(source_hash, str) or not _SHA256_HEX.fullmatch(source_hash):
            raise SecretDriftError("Secret source reference binding is absent or invalid")
        if secret.get("type") != "Opaque" or secret.get("immutable") is True:
            raise SecretDriftError("Secret type or mutability is invalid")
        if "stringData" in secret:
            raise SecretDriftError("Secret stringData is not accepted")

        data = secret.get("data")
        allowed_key_sets = (set(BASE_SECRET_KEYS), set(SECRET_KEYS))
        if not isinstance(data, Mapping) or set(data) not in allowed_key_sets:
            raise SecretDriftError("Secret keys are missing or unexpected")
        values: dict[str, bytes] = {}
        for key in data:
            encoded = data.get(key)
            if not isinstance(encoded, str):
                raise SecretDriftError("Secret contains an invalid value")
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise SecretDriftError("Secret contains invalid base64 data") from None
            if not decoded:
                raise SecretDriftError("Secret contains an empty value")
            values[key] = decoded
        if values["PLUGIN_ID"] != plugin_id.encode("utf-8"):
            raise SecretDriftError("Secret PLUGIN_ID belongs to another plugin")
        return _SecretState(
            resource_version=resource_version,
            dsn_ref_sha256=source_hash,
            values=MappingProxyType(values),
        )

    def _reference(
        self,
        *,
        name: str,
        labels: Mapping[str, str],
        source_hash: str,
    ) -> SecretReference:
        return SecretReference(
            name=name,
            namespace=self._config.namespace,
            labels=dict(labels),
            dsn_ref_sha256=source_hash,
        )

    def _verify_write(
        self,
        secret: Mapping[str, Any],
        *,
        name: str,
        labels: Mapping[str, str],
        plugin_id: str,
        source_hash: str,
        values: Mapping[str, bytes],
    ) -> SecretReference:
        state = self._inspect(secret, name=name, labels=labels, plugin_id=plugin_id)
        if state.dsn_ref_sha256 != source_hash or dict(state.values) != dict(values):
            raise SecretUnavailableError("Kubernetes write did not return the desired Secret state")
        return self._reference(name=name, labels=labels, source_hash=source_hash)

    async def ensure_instance_secret(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        dsn_ref: str,
        gateway_url: str | None = None,
        webhook_secret_ref: str | None = None,
    ) -> SecretReference:
        """Create/reconcile the instance Secret and return only its reference.

        gateway_url overrides the constructor's trusted value for this call. A
        repeat with identical ref/data performs no write. Any managed value or
        ref-binding change PATCHes the same name with the fetched
        resourceVersion. Foreign ownership and structural/key drift are rejected
        instead of silently adopted.
        """

        self._validate_identity(company_id, plugin_id, plugin_instance_id)
        key = self._tenant_key_and_validate_ref(
            company_id=company_id,
            plugin_id=plugin_id,
            dsn_ref=dsn_ref,
        )
        gateway = self._gateway(gateway_url, self._gateway_url)
        try:
            name = plugin_resource_name(
                plugin_id,
                plugin_instance_id,
                prefix=self._config.deployment_prefix,
            )
        except Exception:
            raise SecretProjectionConfigError("cannot derive canonical Secret name") from None
        labels = _ownership_labels(company_id, plugin_id, plugin_instance_id)
        source_hash = _hash(dsn_ref, size=64)
        dsn_value = await self._resolve_dsn(key=key, dsn_ref=dsn_ref)
        desired_values = {
            "GATEWAY_URL": gateway.encode("utf-8"),
            "PLUGIN_ID": plugin_id.encode("utf-8"),
            "TENANT_DB_DSN": dsn_value.encode("utf-8"),
        }
        if webhook_secret_ref is not None:
            webhook_value = await self._resolve_webhook(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=plugin_instance_id,
                secret_ref=webhook_secret_ref,
            )
            desired_values[WEBHOOK_SECRET_KEY] = webhook_value.encode("utf-8")
        desired_data = {
            key: base64.b64encode(value).decode("ascii")
            for key, value in desired_values.items()
        }
        annotations = {DSN_REF_ANNOTATION: source_hash}

        for _attempt in range(self._apply_attempts):
            existing = await self._request("GET", self._path(name), allow_not_found=True)
            if existing is None:
                try:
                    created = await self._request(
                        "POST",
                        self._path(),
                        body={
                            "apiVersion": "v1",
                            "kind": "Secret",
                            "metadata": {
                                "name": name,
                                "namespace": self._config.namespace,
                                "labels": dict(labels),
                                "annotations": dict(annotations),
                            },
                            "type": "Opaque",
                            "data": dict(desired_data),
                        },
                    )
                except SecretConflictError:
                    # AlreadyExists/racing creator: re-read and reconcile.
                    continue
                if created is None:
                    raise SecretUnavailableError("Kubernetes create returned no Secret")
                return self._verify_write(
                    created,
                    name=name,
                    labels=labels,
                    plugin_id=plugin_id,
                    source_hash=source_hash,
                    values=desired_values,
                )

            state = self._inspect(
                existing,
                name=name,
                labels=labels,
                plugin_id=plugin_id,
            )
            if webhook_secret_ref is None and WEBHOOK_SECRET_KEY in state.values:
                # Never remove a still referenced webhook key through a legacy call.
                raise SecretDriftError("webhook_secret_ref is required for the projected webhook key")
            if (
                state.dsn_ref_sha256 == source_hash
                and dict(state.values) == dict(desired_values)
            ):
                return self._reference(name=name, labels=labels, source_hash=source_hash)

            # The ref hash is only binding metadata. Managed plaintext values are
            # compared directly so same-ref DSN rotation is reconciled too. CAS
            # prevents overwriting a concurrent update between GET and PATCH.
            try:
                updated = await self._request(
                    "PATCH",
                    self._path(name),
                    body={
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {
                            "name": name,
                            "namespace": self._config.namespace,
                            "resourceVersion": state.resource_version,
                            "labels": dict(labels),
                            "annotations": dict(annotations),
                        },
                        "type": "Opaque",
                        "data": dict(desired_data),
                    },
                    content_type=MERGE_PATCH,
                )
            except SecretConflictError:
                # CAS conflict: fetch the fresh resourceVersion before retrying.
                continue
            if updated is None:
                raise SecretUnavailableError("Kubernetes patch returned no Secret")
            return self._verify_write(
                updated,
                name=name,
                labels=labels,
                plugin_id=plugin_id,
                source_hash=source_hash,
                values=desired_values,
            )

        raise SecretConflictError()

    @staticmethod
    def _delivery_key(secret_ref: str) -> str:
        return "SECRET_" + _hash(secret_ref, size=64).upper()

    def _delivery_binding(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
    ) -> tuple[str, dict[str, str], str]:
        self._validate_identity(company_id, plugin_id, plugin_instance_id)
        if not isinstance(secret_ref, str) or len(secret_ref) > 2048:
            raise SecretProjectionConfigError("delivery secret_ref is invalid")
        suffix = (
            "plugins/capabilities/secrets/"
            f"{quote(company_id, safe='')}/"
            f"{quote(plugin_id, safe='')}/"
            f"{quote(plugin_instance_id, safe='')}/"
        )
        candidates = {
            scheme: f"{scheme}://ai_keys/{suffix}"
            for scheme in ("vault", "file")
        }
        scheme = next(
            (name for name, prefix in candidates.items() if secret_ref.startswith(prefix)),
            None,
        )
        if scheme is None:
            raise SecretProjectionConfigError("delivery secret_ref is outside instance scope")
        tail = secret_ref[len(candidates[scheme]):]
        if scheme == "file":
            if not tail.endswith(".secret"):
                raise SecretProjectionConfigError("delivery secret_ref is not canonical")
            tail = tail[:-len(".secret")]
        if (
            not tail
            or "/" in tail
            or tail in {".", ".."}
            or quote(tail, safe="") != tail
        ):
            raise SecretProjectionConfigError("delivery secret_ref is not canonical")
        try:
            name = plugin_resource_name(
                plugin_id,
                plugin_instance_id,
                prefix=self._config.deployment_prefix + DELIVERY_SECRET_PREFIX,
            )
        except Exception:
            raise SecretProjectionConfigError("cannot derive delivery Secret name") from None
        return name, _ownership_labels(company_id, plugin_id, plugin_instance_id), self._delivery_key(secret_ref)

    def _delivery_reference(
        self,
        *,
        name: str,
        labels: Mapping[str, str],
        key: str,
        value_digest: str,
        delivered: bool,
    ) -> SecretDeliveryReference:
        return SecretDeliveryReference(
            name=name,
            namespace=self._config.namespace,
            key=key,
            labels=dict(labels),
            value_digest=value_digest,
            version=value_digest,
            delivered=delivered,
        )

    def _inspect_delivery(
        self,
        secret: Mapping[str, Any],
        *,
        name: str,
        labels: Mapping[str, str],
    ) -> _DeliveryState:
        metadata = _mapping(secret.get("metadata"))
        actual_labels = _mapping(metadata.get("labels"))
        if (
            secret.get("apiVersion") != "v1"
            or secret.get("kind") != "Secret"
            or metadata.get("name") != name
            or metadata.get("namespace") != self._config.namespace
            or metadata.get("deletionTimestamp") is not None
            or any(actual_labels.get(key) != value for key, value in labels.items())
        ):
            raise SecretOwnershipError()
        resource_version = metadata.get("resourceVersion")
        if not isinstance(resource_version, str) or not resource_version:
            raise SecretDriftError("delivery Secret resourceVersion is absent or invalid")
        if secret.get("type") != "Opaque" or secret.get("immutable") is True:
            raise SecretDriftError("delivery Secret type or mutability is invalid")
        if "stringData" in secret:
            raise SecretDriftError("delivery Secret stringData is not accepted")

        raw_data = secret.get("data", {})
        data = {} if raw_data is None else raw_data
        if (
            not isinstance(data, Mapping)
            or len(data) > 256
            or any(not isinstance(key, str) or not _DELIVERY_DATA_KEY.fullmatch(key) for key in data)
        ):
            raise SecretDriftError("delivery Secret keys are invalid or unexpected")
        values: dict[str, bytes] = {}
        for key, encoded in data.items():
            if not isinstance(encoded, str):
                raise SecretDriftError("delivery Secret contains an invalid value")
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise SecretDriftError("delivery Secret contains invalid base64 data") from None
            if not decoded:
                raise SecretDriftError("delivery Secret contains an empty value")
            values[key] = decoded

        annotations = _mapping(metadata.get("annotations"))
        raw_versions = annotations.get(DELIVERY_VERSIONS_ANNOTATION)
        if not isinstance(raw_versions, str) or len(raw_versions) > 131072:
            raise SecretDriftError("delivery Secret version metadata is absent or invalid")
        try:
            versions = json.loads(raw_versions)
        except (TypeError, ValueError):
            raise SecretDriftError("delivery Secret version metadata is invalid") from None
        if (
            not isinstance(versions, dict)
            or len(versions) > 256
            or set(versions) != set(values)
            or any(
                not isinstance(digest, str) or not _VALUE_DIGEST.fullmatch(digest)
                for digest in versions.values()
            )
        ):
            raise SecretDriftError("delivery Secret version binding is invalid")
        return _DeliveryState(
            resource_version=resource_version,
            values=MappingProxyType(values),
            versions=MappingProxyType(dict(versions)),
        )

    @staticmethod
    def _delivery_annotations(versions: Mapping[str, str]) -> dict[str, str]:
        return {
            DELIVERY_VERSIONS_ANNOTATION: json.dumps(
                dict(versions), sort_keys=True, separators=(",", ":")
            )
        }

    def _verify_delivery_write(
        self,
        secret: Mapping[str, Any],
        *,
        name: str,
        labels: Mapping[str, str],
        key: str,
        values: Mapping[str, bytes],
        versions: Mapping[str, str],
        value_digest: str,
        delivered: bool,
    ) -> SecretDeliveryReference:
        state = self._inspect_delivery(secret, name=name, labels=labels)
        if dict(state.values) != dict(values) or dict(state.versions) != dict(versions):
            raise SecretUnavailableError("Kubernetes write did not return desired delivery state")
        return self._delivery_reference(
            name=name,
            labels=labels,
            key=key,
            value_digest=value_digest,
            delivered=delivered,
        )

    async def deliver_secret(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
        value: str,
    ) -> SecretDeliveryReference:
        """Project one exact value with resourceVersion CAS on every rotation."""
        name, labels, key = self._delivery_binding(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            secret_ref=secret_ref,
        )
        if not isinstance(value, str) or not value:
            raise SecretProjectionConfigError("delivery secret value is empty or invalid")
        raw_value = value.encode("utf-8")
        if len(raw_value) > self._max_delivery_secret_bytes:
            raise SecretProjectionConfigError("delivery secret value exceeds size cap")
        value_digest = "sha256:" + hashlib.sha256(raw_value).hexdigest()

        for _attempt in range(self._apply_attempts):
            existing = await self._request("GET", self._path(name), allow_not_found=True)
            state = (
                None
                if existing is None
                else self._inspect_delivery(existing, name=name, labels=labels)
            )
            if state is None:
                values = {key: raw_value}
                versions = {key: value_digest}
            else:
                if key not in state.values and len(state.values) >= 256:
                    raise SecretProjectionConfigError("delivery Secret key limit exceeded")
                if state.values.get(key) == raw_value and state.versions.get(key) == value_digest:
                    return self._delivery_reference(
                        name=name, labels=labels, key=key,
                        value_digest=value_digest, delivered=True,
                    )
                values = dict(state.values)
                values[key] = raw_value
                versions = dict(state.versions)
                versions[key] = value_digest
            data = {
                item_key: base64.b64encode(item_value).decode("ascii")
                for item_key, item_value in values.items()
            }
            annotations = self._delivery_annotations(versions)
            if state is None:
                try:
                    created = await self._request(
                        "POST",
                        self._path(),
                        body={
                            "apiVersion": "v1",
                            "kind": "Secret",
                            "metadata": {
                                "name": name,
                                "namespace": self._config.namespace,
                                "labels": dict(labels),
                                "annotations": annotations,
                            },
                            "type": "Opaque",
                            "data": data,
                        },
                    )
                except SecretConflictError:
                    continue
                if created is None:
                    raise SecretUnavailableError("Kubernetes create returned no delivery Secret")
                return self._verify_delivery_write(
                    created, name=name, labels=labels, key=key, values=values,
                    versions=versions, value_digest=value_digest, delivered=True,
                )
            try:
                updated = await self._request(
                    "PATCH",
                    self._path(name),
                    body={
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {
                            "name": name,
                            "namespace": self._config.namespace,
                            "resourceVersion": state.resource_version,
                            "labels": dict(labels),
                            "annotations": annotations,
                        },
                        "type": "Opaque",
                        "data": data,
                    },
                    content_type=MERGE_PATCH,
                )
            except SecretConflictError:
                continue
            if updated is None:
                raise SecretUnavailableError("Kubernetes patch returned no delivery Secret")
            return self._verify_delivery_write(
                updated, name=name, labels=labels, key=key, values=values,
                versions=versions, value_digest=value_digest, delivered=True,
            )
        raise SecretConflictError()

    async def reconcile_secret_values(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        values: Mapping[str, str],
    ) -> tuple[SecretDeliveryReference, ...]:
        """Control-phase exact-set reconcile with one CAS and no plaintext result."""
        if not isinstance(values, Mapping) or not 1 <= len(values) <= 256:
            raise SecretProjectionConfigError("delivery reconcile requires 1..256 values")
        source_values = dict(values)
        first_ref = next(iter(source_values))
        name, labels, _first_key = self._delivery_binding(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            secret_ref=first_ref,
        )
        raw_values: dict[str, bytes] = {}
        versions: dict[str, str] = {}
        binding_order: list[tuple[str, str]] = []
        for secret_ref, value in source_values.items():
            item_name, item_labels, key = self._delivery_binding(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=plugin_instance_id,
                secret_ref=secret_ref,
            )
            if item_name != name or dict(item_labels) != dict(labels) or key in raw_values:
                raise SecretProjectionConfigError("delivery reconcile binding is inconsistent")
            if not isinstance(value, str) or not value:
                raise SecretProjectionConfigError("delivery secret value is empty or invalid")
            raw_value = value.encode("utf-8")
            if len(raw_value) > self._max_delivery_secret_bytes:
                raise SecretProjectionConfigError("delivery secret value exceeds size cap")
            raw_values[key] = raw_value
            versions[key] = "sha256:" + hashlib.sha256(raw_value).hexdigest()
            binding_order.append((secret_ref, key))

        def references() -> tuple[SecretDeliveryReference, ...]:
            return tuple(
                self._delivery_reference(
                    name=name,
                    labels=labels,
                    key=key,
                    value_digest=versions[key],
                    delivered=True,
                )
                for _secret_ref, key in binding_order
            )

        for _attempt in range(self._apply_attempts):
            existing = await self._request("GET", self._path(name), allow_not_found=True)
            state = (
                None
                if existing is None
                else self._inspect_delivery(existing, name=name, labels=labels)
            )
            if (
                state is not None
                and dict(state.values) == raw_values
                and dict(state.versions) == versions
            ):
                return references()
            desired_data = {
                key: base64.b64encode(value).decode("ascii")
                for key, value in raw_values.items()
            }
            annotations = self._delivery_annotations(versions)
            if state is None:
                try:
                    created = await self._request(
                        "POST",
                        self._path(),
                        body={
                            "apiVersion": "v1",
                            "kind": "Secret",
                            "metadata": {
                                "name": name,
                                "namespace": self._config.namespace,
                                "labels": dict(labels),
                                "annotations": annotations,
                            },
                            "type": "Opaque",
                            "data": desired_data,
                        },
                    )
                except SecretConflictError:
                    continue
                if created is None:
                    raise SecretUnavailableError("Kubernetes create returned no delivery Secret")
                first_key = binding_order[0][1]
                self._verify_delivery_write(
                    created, name=name, labels=labels, key=first_key,
                    values=raw_values, versions=versions,
                    value_digest=versions[first_key], delivered=True,
                )
                return references()

            patch_data = dict(desired_data)
            for stale_key in set(state.values) - set(raw_values):
                # Merge patch must explicitly remove refs absent from the new pin.
                patch_data[stale_key] = None
            try:
                updated = await self._request(
                    "PATCH",
                    self._path(name),
                    body={
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {
                            "name": name,
                            "namespace": self._config.namespace,
                            "resourceVersion": state.resource_version,
                            "labels": dict(labels),
                            "annotations": annotations,
                        },
                        "type": "Opaque",
                        "data": patch_data,
                    },
                    content_type=MERGE_PATCH,
                )
            except SecretConflictError:
                continue
            if updated is None:
                raise SecretUnavailableError("Kubernetes patch returned no delivery Secret")
            first_key = binding_order[0][1]
            self._verify_delivery_write(
                updated, name=name, labels=labels, key=first_key,
                values=raw_values, versions=versions,
                value_digest=versions[first_key], delivered=True,
            )
            return references()
        raise SecretConflictError()

    async def revoke_secret(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
    ) -> SecretDeliveryReference:
        """Remove only this source key; the source SecretStore ref is untouched."""
        name, labels, key = self._delivery_binding(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            secret_ref=secret_ref,
        )
        empty_digest = "sha256:" + hashlib.sha256(b"").hexdigest()
        for _attempt in range(self._apply_attempts):
            existing = await self._request("GET", self._path(name), allow_not_found=True)
            if existing is None:
                return self._delivery_reference(
                    name=name, labels=labels, key=key,
                    value_digest=empty_digest, delivered=False,
                )
            state = self._inspect_delivery(existing, name=name, labels=labels)
            if key not in state.values:
                return self._delivery_reference(
                    name=name, labels=labels, key=key,
                    value_digest=empty_digest, delivered=False,
                )
            previous_digest = state.versions[key]
            values = dict(state.values)
            del values[key]
            versions = dict(state.versions)
            del versions[key]
            try:
                updated = await self._request(
                    "PATCH",
                    self._path(name),
                    body={
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {
                            "name": name,
                            "namespace": self._config.namespace,
                            "resourceVersion": state.resource_version,
                            "labels": dict(labels),
                            "annotations": self._delivery_annotations(versions),
                        },
                        "type": "Opaque",
                        # JSON merge patch null removes only this data key.
                        "data": {key: None},
                    },
                    content_type=MERGE_PATCH,
                )
            except SecretConflictError:
                continue
            if updated is None:
                raise SecretUnavailableError("Kubernetes revoke returned no delivery Secret")
            return self._verify_delivery_write(
                updated, name=name, labels=labels, key=key, values=values,
                versions=versions, value_digest=previous_digest, delivered=False,
            )
        raise SecretConflictError()

    async def delete_secret(self, **kwargs: Any) -> SecretDeliveryReference:
        """Lifecycle delete is the same scoped projection revoke operation."""
        return await self.revoke_secret(**kwargs)


__all__ = [
    "COMPANY_LABEL",
    "DSN_REF_ANNOTATION",
    "DELIVERY_SECRET_PREFIX",
    "DELIVERY_VERSIONS_ANNOTATION",
    "INSTANCE_LABEL",
    "MANAGED_BY",
    "MANAGED_BY_VALUE",
    "PLUGIN_LABEL",
    "SECRET_KEYS",
    "K8sSecretProjection",
    "SecretConflictError",
    "SecretDeliveryProjectionPort",
    "SecretDeliveryReference",
    "SecretDriftError",
    "SecretOwnershipError",
    "SecretProjectionConfigError",
    "SecretProjectionError",
    "SecretProjectionPort",
    "SecretReference",
    "SecretUnavailableError",
    "TenantSecretResolutionError",
    "BASE_SECRET_KEYS",
    "WEBHOOK_SECRET_KEY",
    "WebhookSecretResolutionError",
    "WebhookSecretResolverPort",
]
