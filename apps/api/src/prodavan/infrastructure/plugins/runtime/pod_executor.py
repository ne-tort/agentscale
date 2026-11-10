"""Canonical plugin-backend pod actions over typed Kubernetes JSON.

The pods capability never selects an agent Pod and never executes a shell command.
A request is resolved through the durable PluginInstanceStore to the signed
backend revision, then to the one canonical Deployment/Service pair. The only
wire surface is a fixed HTTP action behind the Kubernetes Service proxy.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import (
    PluginInstanceRecord,
    PluginInstanceStatus,
)
from prodavan.application.plugins.artifacts.oci import is_valid_digest, parse_oci_ref
from prodavan.application.plugins.activation.state import WakeResult
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.domain.dynamic_apps.types import AppLifecycleStatus

from .config import RuntimeConfig
from .deployment_admin import (
    ARTIFACT_ANNOTATION,
    BACKEND_ANNOTATION,
    COMPANY_LABEL,
    INSTANCE_LABEL,
    MANAGED_BY,
    MANAGED_BY_VALUE,
    PLUGIN_LABEL,
)
from .errors import PluginRuntimeError, RuntimeConfigError
from .k8s_scale import (
    APPS_API,
    CORE_API,
    K8sApiClient,
    K8sRawResponse,
    plugin_resource_name,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACTIONS = frozenset({"read_status", "read_logs", "send_agent_message", "read_artifact"})
_ENDPOINTS = {
    "read_status": "/v1/status",
    "read_logs": "/v1/logs",
    "send_agent_message": "/v1/messages",
    "read_artifact": "/v1/artifacts",
}
_RESULT_KEYS = {
    "read_status": frozenset({"status", "ready"}),
    "read_logs": frozenset({"job_id", "content_digest", "byte_count"}),
    "send_agent_message": frozenset({"job_id", "arguments_digest", "accepted"}),
    "read_artifact": frozenset({"job_id", "artifact_digest", "byte_count"}),
}
_MAX_PROXY_JSON_BYTES = 64 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_EMPTY_DIGEST = "sha256:" + "0" * 64
_CAPABILITY_HEADER = "X-Plugin-Capability-Token"
_INSTANCE_HEADER = "X-Plugin-Instance-ID"
_PLUGIN_HEADER = "X-Plugin-ID"
_ARTIFACT_HEADER = "X-Plugin-Artifact-Digest"
_PLUGIN_HEADERS = frozenset(
    {_CAPABILITY_HEADER, _INSTANCE_HEADER, _PLUGIN_HEADER, _ARTIFACT_HEADER}
)


class PodExecutorError(PluginRuntimeError):
    """Sanitized fail-closed executor failure with a safe code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class InstanceStore(Protocol):
    async def get_by_id(self, *, plugin_instance_id: str) -> PluginInstanceRecord | None: ...


class ArtifactStore(Protocol):
    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None: ...


@dataclass(frozen=True, slots=True)
class CanonicalBackend:
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    artifact_digest: str
    backend_digest: str
    resource_name: str
    namespace: str
    service_port: int
    manifest: Mapping[str, Any]


def _text(value: Any, *, code: str, max_length: int = 2048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PodExecutorError(code)
    return value


def _digest(value: Any, *, code: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise PodExecutorError(code)
    return value


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise PodExecutorError("backend_json_not_canonicalizable") from None


def _canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _validate_scoped_api(api: Any, token_provider: Any) -> None:
    if not callable(getattr(api, "request", None)):
        raise RuntimeConfigError("canonical backend requires the lifecycle K8s API")
    if not callable(getattr(token_provider, "get_token", None)):
        raise RuntimeConfigError("default_mounted_service_account_forbidden")
    # A real K8sApiClient must use the exact provider. Custom API ports must
    # explicitly declare scoped authentication; absence of that evidence is not
    # treated as permission to use the Pod's mounted ServiceAccount token.
    provider_marker = getattr(api, "_token_provider", ...)
    if provider_marker is not ...:
        if provider_marker is not token_provider:
            raise RuntimeConfigError("default_mounted_service_account_forbidden")
    elif getattr(api, "scoped_identity", False) is not True:
        raise RuntimeConfigError("default_mounted_service_account_forbidden")


def _labels(company_id: str, plugin_id: str, instance_id: str) -> dict[str, str]:
    def hashed(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]

    return {
        MANAGED_BY: MANAGED_BY_VALUE,
        COMPANY_LABEL: hashed(company_id),
        PLUGIN_LABEL: hashed(plugin_id),
        INSTANCE_LABEL: hashed(f"{company_id}\0{plugin_id}\0{instance_id}"),
    }


def _metadata(resource: Any, *, kind: str, name: str, namespace: str) -> Mapping[str, Any]:
    body = _mapping(getattr(resource, "body", None))
    if body is None or body.get("kind") != kind:
        raise PodExecutorError("canonical_backend_resource_invalid")
    metadata = _mapping(body.get("metadata"))
    if (
        metadata is None
        or metadata.get("name") != name
        or metadata.get("namespace") != namespace
    ):
        raise PodExecutorError("canonical_backend_ownership_mismatch")
    return body


def _validate_ownership(
    body: Mapping[str, Any], expected_labels: Mapping[str, str]
) -> None:
    metadata = _mapping(body.get("metadata"))
    labels = _mapping(metadata.get("labels")) if metadata is not None else None
    if labels is None or any(labels.get(key) != value for key, value in expected_labels.items()):
        raise PodExecutorError("canonical_backend_ownership_mismatch")


class CanonicalBackendResolver:
    """Resolve one digest-pinned backend; every missing/mismatched fact denies."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        instance_store: InstanceStore,
        artifact_store: ArtifactStore,
        api: Any,
        token_provider: Any,
    ) -> None:
        if not isinstance(config, RuntimeConfig):
            raise RuntimeConfigError("canonical backend requires RuntimeConfig")
        if not callable(getattr(instance_store, "get_by_id", None)):
            raise RuntimeConfigError("durable PluginInstanceStore is required")
        if not callable(getattr(artifact_store, "by_digest_async", None)):
            raise RuntimeConfigError("durable artifact digest lookup is required")
        _validate_scoped_api(api, token_provider)
        self._config = config
        self._instances = instance_store
        self._artifacts = artifact_store
        self._api = api

    @staticmethod
    def identity(identity: Mapping[str, Any]) -> tuple[str, str, str, str]:
        if not isinstance(identity, Mapping):
            raise PodExecutorError("canonical_backend_identity_required")
        return (
            _text(identity.get("company_id"), code="canonical_backend_identity_required", max_length=256),
            _text(identity.get("plugin_id"), code="canonical_backend_identity_required", max_length=256),
            _text(identity.get("plugin_instance_id"), code="canonical_backend_identity_required", max_length=256),
            _digest(identity.get("artifact_digest"), code="canonical_backend_digest_required"),
        )

    async def _instance(self, identity: Mapping[str, Any]) -> PluginInstanceRecord:
        company_id, plugin_id, instance_id, artifact_digest = self.identity(identity)
        try:
            record = await self._instances.get_by_id(plugin_instance_id=instance_id)
        except Exception:
            raise PodExecutorError("canonical_instance_lookup_failed") from None
        if (
            not isinstance(record, PluginInstanceRecord)
            or record.status is not PluginInstanceStatus.ACTIVE
            or record.company_id != company_id
            or record.plugin_id != plugin_id
            or record.plugin_instance_id != instance_id
            or record.artifact_digest != artifact_digest
        ):
            raise PodExecutorError("canonical_instance_binding_mismatch")
        return record

    async def _artifact(self, record: PluginInstanceRecord) -> tuple[PluginArtifactRecord, str]:
        try:
            artifact = await self._artifacts.by_digest_async(record.artifact_digest)
        except Exception:
            raise PodExecutorError("canonical_artifact_lookup_failed") from None
        if not isinstance(artifact, PluginArtifactRecord):
            raise PodExecutorError("canonical_artifact_missing")
        evidence = artifact.verification
        publication = artifact.publication
        if (
            artifact.plugin_id != record.plugin_id
            or artifact.version != record.manifest_version
            or artifact.digest != record.artifact_digest
            or artifact.status is not AppLifecycleStatus.PUBLISHED
            or artifact.outcome is not RegistryOutcome.APPLIED
            or artifact.quarantine is not None
            or artifact.published_at is None
            or evidence is None
            or publication is None
            or not evidence.verified
            or not evidence.identity.strip()
            or not evidence.issuer.strip()
            or evidence.artifact_digest != artifact.digest
            or evidence.image_ref != artifact.image_ref
            or evidence.manifest_fingerprint != artifact.manifest_fingerprint
            or not all(
                (
                    evidence.sbom_verified,
                    evidence.provenance_verified,
                    evidence.scan_verified,
                    evidence.csp_verified,
                )
            )
        ):
            raise PodExecutorError("canonical_artifact_provenance_mismatch")
        backend_digest = publication.backend_digest
        if not is_valid_digest(backend_digest) or backend_digest == artifact.digest:
            raise PodExecutorError("canonical_backend_digest_mismatch")
        try:
            backend_ref = parse_oci_ref(publication.backend_image_ref)
        except (TypeError, ValueError):
            raise PodExecutorError("canonical_backend_image_invalid") from None
        if backend_ref.digest != backend_digest:
            raise PodExecutorError("canonical_backend_digest_mismatch")
        return artifact, backend_digest

    async def _get(self, path: str) -> Any:
        try:
            return await self._api.request("GET", path)
        except Exception:
            raise PodExecutorError("canonical_backend_k8s_lookup_failed") from None

    async def resolve(self, identity: Mapping[str, Any]) -> CanonicalBackend:
        record = await self._instance(identity)
        artifact, backend_digest = await self._artifact(record)
        name = plugin_resource_name(
            record.plugin_id,
            record.plugin_instance_id,
            prefix=self._config.deployment_prefix,
        )
        namespace = self._config.namespace
        service_path = f"{CORE_API}/namespaces/{namespace}/services/{name}"
        deployment_path = f"{APPS_API}/namespaces/{namespace}/deployments/{name}"
        service = _metadata(
            await self._get(service_path), kind="Service", name=name, namespace=namespace
        )
        deployment = _metadata(
            await self._get(deployment_path),
            kind="Deployment",
            name=name,
            namespace=namespace,
        )
        expected_labels = _labels(record.company_id, record.plugin_id, record.plugin_instance_id)
        _validate_ownership(service, expected_labels)
        _validate_ownership(deployment, expected_labels)

        service_spec = _mapping(service.get("spec"))
        selector = _mapping(service_spec.get("selector")) if service_spec is not None else None
        ports = service_spec.get("ports") if service_spec is not None else None
        if (
            service_spec is None
            or service_spec.get("type") != "ClusterIP"
            or selector != {INSTANCE_LABEL: expected_labels[INSTANCE_LABEL]}
            or not isinstance(ports, (list, tuple))
            or len(ports) != 1
            or not isinstance(ports[0], Mapping)
            or ports[0].get("name") != "http"
            or ports[0].get("port") != self._config.service_port
            or ports[0].get("targetPort") != "http"
            or ports[0].get("protocol") != "TCP"
        ):
            raise PodExecutorError("canonical_backend_service_mismatch")

        deployment_metadata = _mapping(deployment.get("metadata"))
        annotations = (
            _mapping(deployment_metadata.get("annotations"))
            if deployment_metadata is not None
            else None
        )
        deployment_spec = _mapping(deployment.get("spec"))
        template = _mapping(deployment_spec.get("template")) if deployment_spec is not None else None
        template_metadata = _mapping(template.get("metadata")) if template is not None else None
        template_annotations = (
            _mapping(template_metadata.get("annotations"))
            if template_metadata is not None
            else None
        )
        template_labels = (
            _mapping(template_metadata.get("labels"))
            if template_metadata is not None
            else None
        )
        pod_spec = _mapping(template.get("spec")) if template is not None else None
        if (
            annotations is None
            or annotations.get(ARTIFACT_ANNOTATION) != record.artifact_digest
            or annotations.get(BACKEND_ANNOTATION) != backend_digest
            or template_annotations is None
            or template_annotations.get(ARTIFACT_ANNOTATION) != record.artifact_digest
            or template_annotations.get(BACKEND_ANNOTATION) != backend_digest
            or template_labels is None
            or any(
                template_labels.get(key) != value
                for key, value in expected_labels.items()
            )
            or pod_spec is None
            or pod_spec.get("automountServiceAccountToken") is not False
        ):
            raise PodExecutorError("canonical_backend_digest_mismatch")
        containers = pod_spec.get("containers")
        if (
            not isinstance(containers, (list, tuple))
            or len(containers) != 1
            or not isinstance(containers[0], Mapping)
            or containers[0].get("name") != "backend"
        ):
            raise PodExecutorError("canonical_backend_container_mismatch")
        try:
            running_image = parse_oci_ref(containers[0].get("image"))
        except (TypeError, ValueError):
            raise PodExecutorError("canonical_backend_image_invalid") from None
        if running_image.digest != backend_digest:
            raise PodExecutorError("canonical_backend_digest_mismatch")
        manifest = artifact.manifest
        if not isinstance(manifest, Mapping):
            raise PodExecutorError("canonical_backend_declaration_invalid")
        return CanonicalBackend(
            company_id=record.company_id,
            plugin_id=record.plugin_id,
            plugin_instance_id=record.plugin_instance_id,
            artifact_digest=record.artifact_digest,
            backend_digest=backend_digest,
            resource_name=name,
            namespace=namespace,
            service_port=self._config.service_port,
            manifest=manifest,
        )


def service_proxy_path(backend: CanonicalBackend, endpoint: str) -> str:
    """Build only a canonical Service URL; target hosts never become URL input."""
    if endpoint not in _ENDPOINTS.values() and endpoint != "/v1/egress":
        raise PodExecutorError("backend_endpoint_not_allowed")
    return (
        f"{CORE_API}/namespaces/{backend.namespace}/services/"
        f"{backend.resource_name}:{backend.service_port}/proxy{endpoint}"
    )


def backend_response_body(response: Any) -> Mapping[str, Any]:
    body = getattr(response, "body", None)
    if not isinstance(body, Mapping):
        raise PodExecutorError("backend_typed_response_required")
    forbidden = {"authorization", "secret", "body", "payload", "raw_body"}
    if forbidden.intersection(body) or any(
        isinstance(key, str) and "token" in key.lower() for key in body
    ):
        raise PodExecutorError("backend_sensitive_response_field")
    return body


async def _plugin_capability_headers(
    *,
    activator: Any,
    backend: CanonicalBackend,
    grant_id: str,
    project_id: str | None,
    required_scope: str,
    error_code: str,
) -> dict[str, str]:
    """Mint a fresh wake token and return only fixed authentication headers."""
    try:
        lease = await activator.wake(backend.plugin_instance_id, timeout=None)
    except Exception:
        raise PodExecutorError(error_code) from None
    if not isinstance(lease, WakeResult) or lease.plugin_instance_id != backend.plugin_instance_id:
        raise PodExecutorError(error_code)
    claims = lease.claims
    if not isinstance(claims, Mapping):
        raise PodExecutorError(error_code)
    expected = {
        "company_id": backend.company_id,
        "plugin_id": backend.plugin_id,
        "plugin_instance_id": backend.plugin_instance_id,
        "artifact_digest": backend.artifact_digest,
        "grant_id": grant_id,
        "sub": f"plugin:{backend.plugin_id}:{backend.company_id}:{backend.plugin_instance_id}",
    }
    if project_id is not None:
        expected["project_id"] = project_id
    if any(claims.get(key) != value for key, value in expected.items()):
        raise PodExecutorError(error_code)
    scopes = claims.get("scopes")
    if (
        not isinstance(scopes, (list, tuple))
        or required_scope not in scopes
        or any(not isinstance(scope, str) for scope in scopes)
    ):
        raise PodExecutorError(error_code)
    try:
        token = _text(lease.token, code=error_code, max_length=16_384)
    except PodExecutorError:
        raise PodExecutorError(error_code) from None
    if not all(33 <= ord(character) <= 126 for character in token):
        raise PodExecutorError(error_code)
    # Rebuild from trusted resolver/lease fields. No caller header collection is
    # accepted here, and the token never enters identity, JSON, result or URL.
    return {
        _CAPABILITY_HEADER: token,
        _INSTANCE_HEADER: backend.plugin_instance_id,
        _PLUGIN_HEADER: backend.plugin_id,
        _ARTIFACT_HEADER: backend.artifact_digest,
    }


async def _service_proxy_request(
    api: Any,
    *,
    method: str,
    path: str,
    payload: Mapping[str, Any],
    plugin_headers: Mapping[str, str],
    content_type: str = "application/json",
) -> Any:
    """Call Service proxy with fixed plugin headers and scoped K8s identity."""
    if not isinstance(plugin_headers, Mapping) or set(plugin_headers) != _PLUGIN_HEADERS:
        raise PodExecutorError("backend_plugin_header_set_invalid")
    # This allowlist drops every other client-supplied X-Plugin-* or HTTP header.
    headers = {name: plugin_headers[name] for name in (
        _CAPABILITY_HEADER, _INSTANCE_HEADER, _PLUGIN_HEADER, _ARTIFACT_HEADER
    )}
    if not isinstance(api, K8sApiClient):
        return await api.request(
            method,
            path,
            body=dict(payload),
            content_type=content_type,
            headers=headers,
        )

    # K8sApiClient's generic resource API predates per-request plugin headers.
    # Keep its scoped identity/client ownership, but do not use the mounted-SA
    # branch or forward its resource-request headers blindly.
    provider = getattr(api, "_token_provider", None)
    if provider is None or not callable(getattr(provider, "get_token", None)):
        raise PodExecutorError("default_mounted_service_account_forbidden")
    try:
        api_base = api._auth.api_base()
        lifecycle_token = await provider.get_token()
        client = api._get_client()
    except Exception:
        raise PodExecutorError("backend_scoped_lifecycle_identity_unavailable") from None
    if (
        not isinstance(lifecycle_token, str)
        or not 0 < len(lifecycle_token) <= 16_384
        or not all(33 <= ord(character) <= 126 for character in lifecycle_token)
    ):
        raise PodExecutorError("backend_scoped_lifecycle_identity_unavailable")
    request_headers = {
        "Authorization": f"Bearer {lifecycle_token}",
        "Content-Type": content_type,
        "Accept": "application/json",
    }
    for name in tuple(request_headers):
        if name.lower().startswith("x-plugin-"):
            del request_headers[name]
    request_headers.update(headers)
    response = await client.request(
        method.upper(),
        f"{api_base}{path}",
        headers=request_headers,
        json=dict(payload),
        timeout=api._timeout,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 200 or status >= 400:
        raise PodExecutorError("backend_service_proxy_http_error")
    if status == 204:
        return K8sRawResponse(status_code=status, body={})
    try:
        response_body = response.json()
    except Exception:
        raise PodExecutorError("backend_typed_response_required") from None
    if not isinstance(response_body, Mapping):
        raise PodExecutorError("backend_typed_response_required")
    return K8sRawResponse(status_code=status, body=dict(response_body))


class K8sPodExecutor:
    """Fixed typed actions on the caller's own canonical plugin backend."""

    scoped_identity = True
    digest_bound_channel = True

    def __init__(
        self,
        *,
        resolver: CanonicalBackendResolver | None = None,
        config: RuntimeConfig | None = None,
        instance_store: InstanceStore | None = None,
        artifact_store: ArtifactStore | None = None,
        api: Any = None,
        token_provider: Any = None,
        activator: Any = None,
        allowlist: Iterable[tuple[str, str, str]] | None = None,
        max_result_bytes: int = 1024 * 1024,
    ) -> None:
        if resolver is None:
            resolver = CanonicalBackendResolver(
                config=config,  # type: ignore[arg-type]
                instance_store=instance_store,  # type: ignore[arg-type]
                artifact_store=artifact_store,  # type: ignore[arg-type]
                api=api,
                token_provider=token_provider,
            )
        elif any(
            value is not None
            for value in (config, instance_store, artifact_store, api, token_provider)
        ):
            raise RuntimeConfigError("pod executor resolver and its dependencies are exclusive")
        if (
            not isinstance(max_result_bytes, int)
            or isinstance(max_result_bytes, bool)
            or not 1 <= max_result_bytes <= _MAX_RESULT_BYTES
        ):
            raise RuntimeConfigError("pod result byte limit is invalid")
        if not callable(getattr(activator, "wake", None)):
            raise RuntimeConfigError("fresh PluginActivator wake is required")
        self._resolver = resolver
        self._activator = activator
        self._max_result_bytes = max_result_bytes
        self._allowlist = self._parse_static_allowlist(allowlist)
        self._closed = False

    @staticmethod
    def _parse_static_allowlist(
        values: Iterable[tuple[str, str, str]] | None,
    ) -> frozenset[tuple[str, str, str]] | None:
        if values is None:
            return None
        if isinstance(values, (str, bytes, Mapping)):
            raise RuntimeConfigError("pod allowlist must contain typed scope tuples")
        parsed: set[tuple[str, str, str]] = set()
        try:
            for item in values:
                if not isinstance(item, (tuple, list)) or len(item) != 3:
                    raise ValueError("scope shape")
                scope = tuple(
                    _text(value, code="pod_allowlist_invalid", max_length=256)
                    for value in item
                )
                if scope in parsed:
                    raise ValueError("duplicate scope")
                parsed.add(scope)  # type: ignore[arg-type]
        except (TypeError, ValueError, PodExecutorError):
            raise RuntimeConfigError("pod allowlist is invalid") from None
        if not parsed or len(parsed) > 256:
            raise RuntimeConfigError("pod allowlist must contain 1..256 exact scopes")
        return frozenset(parsed)

    @property
    def max_result_bytes(self) -> int:
        return self._max_result_bytes

    def _allowed(
        self, backend: CanonicalBackend, *, project_id: str, job_id: str
    ) -> None:
        scope = (backend.plugin_instance_id, project_id, job_id)
        if self._allowlist is not None:
            if scope not in self._allowlist:
                raise PodExecutorError("pod_project_job_not_allowlisted")
            return
        declaration = backend.manifest.get("pods")
        if not isinstance(declaration, Mapping) or set(declaration) != {"allowlist"}:
            raise PodExecutorError("pod_allowlist_declaration_required")
        entries = declaration.get("allowlist")
        if not isinstance(entries, (list, tuple)) or not entries or len(entries) > 256:
            raise PodExecutorError("pod_allowlist_declaration_invalid")
        matched = False
        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {
                "plugin_instance_id",
                "project_id",
                "job_id",
            }:
                raise PodExecutorError("pod_allowlist_declaration_invalid")
            candidate = (
                _text(entry["plugin_instance_id"], code="pod_allowlist_declaration_invalid", max_length=256),
                _text(entry["project_id"], code="pod_allowlist_declaration_invalid", max_length=256),
                _text(entry["job_id"], code="pod_allowlist_declaration_invalid", max_length=256),
            )
            if candidate in seen:
                raise PodExecutorError("pod_allowlist_declaration_invalid")
            seen.add(candidate)
            matched = matched or candidate == scope
        if not matched:
            raise PodExecutorError("pod_project_job_not_allowlisted")

    @staticmethod
    def _action_scope(
        backend: CanonicalBackend, identity: Mapping[str, Any]
    ) -> tuple[str, str, str, str, str, int]:
        action = _text(identity.get("action"), code="pod_action_not_allowed", max_length=64)
        if action not in _ACTIONS:
            raise PodExecutorError("pod_action_not_allowed")
        project_id = _text(identity.get("project_id"), code="pod_project_scope_required", max_length=256)
        job_id = _text(identity.get("run_id"), code="pod_job_scope_required", max_length=256)
        pod_id = _text(identity.get("pod_id"), code="pod_target_not_allowed", max_length=256)
        if pod_id != backend.plugin_instance_id:
            raise PodExecutorError("pod_target_not_allowed")
        target_ref = _text(identity.get("target_ref"), code="pod_target_ref_invalid")
        arguments_digest = _digest(
            identity.get("arguments_digest"), code="pod_arguments_digest_invalid"
        )
        if action in {"read_status", "read_logs"} and target_ref != job_id:
            raise PodExecutorError("pod_status_job_binding_required")
        if action == "send_agent_message":
            if target_ref != job_id or arguments_digest == _EMPTY_DIGEST:
                raise PodExecutorError("pod_message_digest_channel_required")
        if action == "read_artifact":
            _digest(target_ref, code="pod_artifact_digest_channel_required")
            if arguments_digest != _EMPTY_DIGEST:
                raise PodExecutorError("pod_artifact_arguments_not_allowed")
        max_result = identity.get("max_result_bytes", 1024 * 1024)
        if (
            not isinstance(max_result, int)
            or isinstance(max_result, bool)
            or not 1 <= max_result <= _MAX_RESULT_BYTES
        ):
            raise PodExecutorError("pod_result_byte_limit_invalid")
        return action, project_id, job_id, target_ref, arguments_digest, max_result

    async def execute(self, *, identity: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._closed:
            raise PodExecutorError("pod_executor_closed")
        backend = await self._resolver.resolve(identity)
        action, project_id, job_id, target_ref, arguments_digest, requested_max = (
            self._action_scope(backend, identity)
        )
        self._allowed(backend, project_id=project_id, job_id=job_id)
        max_result = min(requested_max, self._max_result_bytes)
        request_id = _text(identity.get("request_id"), code="pod_request_id_required", max_length=256)
        grant_id = _text(identity.get("grant_id"), code="pod_grant_required", max_length=256)
        payload = {
            "company_id": backend.company_id,
            "project_id": project_id,
            "plugin_id": backend.plugin_id,
            "plugin_instance_id": backend.plugin_instance_id,
            "artifact_digest": backend.artifact_digest,
            "backend_digest": backend.backend_digest,
            "grant_id": grant_id,
            "request_id": request_id,
            "pod_id": backend.plugin_instance_id,
            "job_id": job_id,
            "action": action,
            "target_ref": target_ref,
            "arguments_digest": arguments_digest,
            "max_result_bytes": max_result,
        }
        if len(_canonical_json(payload)) > _MAX_PROXY_JSON_BYTES:
            raise PodExecutorError("pod_proxy_request_too_large")
        plugin_headers = await _plugin_capability_headers(
            activator=self._activator,
            backend=backend,
            grant_id=grant_id,
            project_id=project_id,
            required_scope="pods",
            error_code="pod_backend_activation_failed",
        )
        try:
            response = await _service_proxy_request(
                self._resolver._api,
                method="POST",
                path=service_proxy_path(backend, _ENDPOINTS[action]),
                payload=payload,
                plugin_headers=plugin_headers,
            )
        except Exception:
            raise PodExecutorError("pod_backend_action_failed") from None
        body = backend_response_body(response)
        for key, value in payload.items():
            if body.get(key) != value:
                raise PodExecutorError("pod_backend_identity_mismatch")
        result = body.get("result")
        if not isinstance(result, Mapping) or set(result) != _RESULT_KEYS[action]:
            raise PodExecutorError("pod_backend_result_invalid")
        result_digest = _canonical_digest(result)
        if body.get("result_digest") != result_digest:
            raise PodExecutorError("pod_backend_result_digest_mismatch")
        if action == "read_status":
            if result.get("status") not in {"ready", "degraded", "unavailable"} or not isinstance(
                result.get("ready"), bool
            ):
                raise PodExecutorError("pod_backend_status_invalid")
            expected_bytes = len(_canonical_json(result))
        elif action == "send_agent_message":
            if (
                result.get("job_id") != job_id
                or result.get("arguments_digest") != arguments_digest
                or result.get("accepted") is not True
            ):
                raise PodExecutorError("pod_backend_message_binding_mismatch")
            expected_bytes = len(_canonical_json(result))
        else:
            byte_count = result.get("byte_count")
            if (
                result.get("job_id") != job_id
                or not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count < 0
            ):
                raise PodExecutorError("pod_backend_result_invalid")
            if action == "read_logs":
                _digest(result.get("content_digest"), code="pod_backend_result_invalid")
            else:
                if result.get("artifact_digest") != target_ref:
                    raise PodExecutorError("pod_backend_artifact_binding_mismatch")
            expected_bytes = byte_count
        bytes_received = body.get("bytes_received")
        if (
            not isinstance(bytes_received, int)
            or isinstance(bytes_received, bool)
            or bytes_received != expected_bytes
            or bytes_received > max_result
        ):
            raise PodExecutorError("pod_backend_result_byte_limit_exceeded")
        return {
            "company_id": backend.company_id,
            "project_id": project_id,
            "plugin_id": backend.plugin_id,
            "plugin_instance_id": backend.plugin_instance_id,
            "artifact_digest": backend.artifact_digest,
            "backend_digest": backend.backend_digest,
            "grant_id": grant_id,
            "request_id": request_id,
            "run_id": job_id,
            "pod_id": backend.plugin_instance_id,
            "action": action,
            "target_ref": target_ref,
            "arguments_digest": arguments_digest,
            "max_result_bytes": max_result,
            "result_digest": result_digest,
            "bytes_received": bytes_received,
        }

    async def aclose(self) -> None:
        """Close local state once; shared lifecycle API/token owner closes them."""
        if self._closed:
            return
        self._closed = True


__all__ = [
    "ArtifactStore",
    "CanonicalBackend",
    "CanonicalBackendResolver",
    "InstanceStore",
    "K8sPodExecutor",
    "PodExecutorError",
    "backend_response_body",
    "service_proxy_path",
]
