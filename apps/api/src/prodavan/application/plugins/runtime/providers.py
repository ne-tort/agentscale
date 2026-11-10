"""Trusted platform-scoped runtime providers backed by real durable/OCI ports.

The service deliberately does not call K8sPodExecutor.execute: that executor's
fixed Service-proxy operations target the calling hello backend and would recurse
through the provider endpoint.  Declarations and OCI metadata are real.  Status,
logs and agent messages remain typed-unwired until an independent platform data
source exists.  SecretStore values stay inside the control-plane digest reader.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, NoReturn

from pydantic import ValidationError

from prodavan.application.plugins.admin.contracts import CapabilityGrantRecord
from prodavan.application.plugins.artifacts.oci import is_valid_digest, parse_oci_ref
from prodavan.application.plugins.artifacts.publication import HttpxOciArtifactLoader
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
)
from prodavan.application.plugins.capabilities.adapters.secrets import (
    SecretStoreSecretsAdapter,
    secret_delivery_allowlist,
)
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    SecretsReadRequest,
    SecretsReadResponse,
)
from prodavan.application.plugins.egress.policy import EgressPolicy
from prodavan.application.plugins.identity.types import PluginClaims
from prodavan.application.plugins.runtime.provider_api import (
    ArtifactMetadataRequest,
    ArtifactMetadataResponse,
    EMPTY_DIGEST,
    MAX_PROVIDER_ARTIFACT_BYTES,
    EgressDeclarationRequest,
    EgressDeclarationResponse,
    EgressRule,
    LogsProviderResponse,
    MessageProviderResponse,
    PodActionRequest,
    ProviderError,
    SecretMetadataRequest,
    SecretMetadataResponse,
    StatusProviderResponse,
)
from prodavan.infrastructure.plugins.runtime.egress_transport import (
    DurableGrantEgressPolicy,
    EgressDeclaration,
)
from prodavan.infrastructure.plugins.runtime.pod_executor import (
    CanonicalBackend,
    CanonicalBackendResolver,
    K8sPodExecutor,
    PodExecutorError,
)

_SUPPORTED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
_RESOLVER_UNAVAILABLE = frozenset(
    {
        "canonical_instance_lookup_failed",
        "canonical_artifact_lookup_failed",
        "canonical_backend_k8s_lookup_failed",
    }
)


@dataclass(frozen=True, slots=True)
class _Binding:
    claims: PluginClaims
    grant: CapabilityGrantRecord
    backend: CanonicalBackend
    artifact: PluginArtifactRecord


class ScopedRuntimeProvider:
    """Revalidate claims, grant, manifest, artifact and backend on every call."""

    def __init__(
        self,
        *,
        resolver: CanonicalBackendResolver,
        declarations: DurableGrantEgressPolicy,
        pod_executor: K8sPodExecutor,
        secret_reader: SecretStoreSecretsAdapter,
        artifact_loader: Any = None,
    ) -> None:
        if not isinstance(resolver, CanonicalBackendResolver):
            raise ValueError("canonical resolver required")
        if not isinstance(declarations, DurableGrantEgressPolicy):
            raise ValueError("durable grant egress policy required")
        if not isinstance(pod_executor, K8sPodExecutor) or pod_executor._resolver is not resolver:
            raise ValueError("pod allowlist must use the same canonical resolver")
        if not isinstance(secret_reader, SecretStoreSecretsAdapter):
            raise ValueError("digest-only control-plane SecretStore reader required")
        self._resolver = resolver
        self._declarations = declarations
        self._pod_executor = pod_executor
        self._secret_reader = secret_reader
        self._grants = declarations._store
        self._artifacts = resolver._artifacts
        self._artifact_loader = artifact_loader
        if artifact_loader is not None and not callable(
            getattr(artifact_loader, "load_manifest", None)
        ):
            raise ValueError("digest-verified OCI metadata loader required")

    @staticmethod
    def _claims_identity(claims: PluginClaims, *, request_id: str) -> dict[str, str]:
        return {
            "company_id": claims.company_id,
            "plugin_id": claims.plugin_id,
            "plugin_instance_id": claims.plugin_instance_id,
            "artifact_digest": claims.artifact_digest,
            "grant_id": claims.grant_id,
            "request_id": request_id,
        }

    async def _bind(
        self,
        claims: PluginClaims,
        *,
        backend_digest: str,
        request_id: str,
        capability: str,
    ) -> _Binding:
        if not isinstance(claims, PluginClaims) or capability not in claims.scopes:
            raise ProviderError(403, "RUNTIME_CAPABILITY_NOT_GRANTED")
        expected_sub = PluginClaims.build_sub(
            claims.plugin_id, claims.company_id, claims.plugin_instance_id
        )
        if claims.sub != expected_sub:
            raise ProviderError(403, "RUNTIME_SUBJECT_BINDING_MISMATCH")
        identity = self._claims_identity(claims, request_id=request_id)
        try:
            grant = await self._grants.get_by_grant_id(grant_id=claims.grant_id)
        except Exception:
            raise ProviderError(503, "RUNTIME_GRANT_LOOKUP_UNAVAILABLE") from None
        if (
            not isinstance(grant, CapabilityGrantRecord)
            or not grant.is_active
            or grant.revoked_at is not None
            or capability not in grant.capabilities
            or any(
                (
                    grant.grant_id != claims.grant_id,
                    grant.company_id != claims.company_id,
                    grant.plugin_id != claims.plugin_id,
                    grant.plugin_instance_id != claims.plugin_instance_id,
                    grant.manifest_version != claims.manifest_version,
                    grant.artifact_digest != claims.artifact_digest,
                )
            )
        ):
            raise ProviderError(403, "RUNTIME_GRANT_BINDING_MISMATCH")
        try:
            backend = await self._resolver.resolve(identity)
        except PodExecutorError as exc:
            if exc.code in _RESOLVER_UNAVAILABLE:
                raise ProviderError(503, "RUNTIME_BACKEND_LOOKUP_UNAVAILABLE") from None
            raise ProviderError(403, "RUNTIME_BACKEND_OWNERSHIP_MISMATCH") from None
        except Exception:
            raise ProviderError(503, "RUNTIME_BACKEND_LOOKUP_UNAVAILABLE") from None
        if (
            backend.company_id != claims.company_id
            or backend.plugin_id != claims.plugin_id
            or backend.plugin_instance_id != claims.plugin_instance_id
            or backend.artifact_digest != claims.artifact_digest
            or backend.backend_digest != backend_digest
            or backend.manifest.get("plugin_id") != claims.plugin_id
            or backend.manifest.get("version") != claims.manifest_version
        ):
            raise ProviderError(403, "RUNTIME_BACKEND_DIGEST_MISMATCH")
        try:
            artifact = await self._artifacts.by_digest_async(claims.artifact_digest)
        except Exception:
            raise ProviderError(503, "RUNTIME_ARTIFACT_LOOKUP_UNAVAILABLE") from None
        publication = getattr(artifact, "publication", None)
        if (
            not isinstance(artifact, PluginArtifactRecord)
            or artifact.plugin_id != claims.plugin_id
            or artifact.version != claims.manifest_version
            or artifact.digest != claims.artifact_digest
            or artifact.manifest != backend.manifest
            or publication is None
            or publication.backend_digest != backend.backend_digest
        ):
            raise ProviderError(403, "RUNTIME_ARTIFACT_BINDING_MISMATCH")
        return _Binding(claims=claims, grant=grant, backend=backend, artifact=artifact)

    @staticmethod
    def _identity(binding: _Binding, *, request_id: str) -> dict[str, str]:
        return {
            "company_id": binding.claims.company_id,
            "plugin_id": binding.claims.plugin_id,
            "plugin_instance_id": binding.claims.plugin_instance_id,
            "artifact_digest": binding.claims.artifact_digest,
            "backend_digest": binding.backend.backend_digest,
            "grant_id": binding.claims.grant_id,
            "request_id": request_id,
        }

    async def _pod_binding(
        self,
        claims: PluginClaims,
        request: PodActionRequest,
    ) -> _Binding:
        binding = await self._bind(
            claims,
            backend_digest=request.backend_digest,
            request_id=request.request_id,
            capability="pods",
        )
        if not binding.claims.project_id:
            raise ProviderError(403, "POD_PROJECT_SCOPE_REQUIRED")
        try:
            self._pod_executor._allowed(
                binding.backend,
                project_id=binding.claims.project_id,
                job_id=request.job_id,
            )
        except PodExecutorError:
            raise ProviderError(403, "POD_JOB_NOT_ALLOWLISTED") from None
        return binding

    def _pod_identity(self, binding: _Binding, request: PodActionRequest) -> dict[str, Any]:
        project_id = binding.claims.project_id
        if project_id is None:
            raise ProviderError(403, "POD_PROJECT_SCOPE_REQUIRED")
        return {
            **self._identity(binding, request_id=request.request_id),
            "project_id": project_id,
            "pod_id": binding.backend.plugin_instance_id,
            "job_id": request.job_id,
            "target_ref": request.target_ref,
            "arguments_digest": request.arguments_digest,
            "max_result_bytes": request.max_result_bytes,
        }

    async def egress_declaration(
        self,
        *,
        claims: PluginClaims,
        request: EgressDeclarationRequest,
    ) -> EgressDeclarationResponse:
        binding = await self._bind(
            claims,
            backend_digest=request.backend_digest,
            request_id=request.request_id,
            capability="network",
        )
        identity = self._claims_identity(claims, request_id=request.request_id)
        try:
            declaration = await self._declarations.declaration(identity=identity)
        except PodExecutorError as exc:
            if exc.code in {"egress_grant_lookup_failed"}:
                raise ProviderError(503, "EGRESS_GRANT_LOOKUP_UNAVAILABLE") from None
            raise ProviderError(403, "EGRESS_GRANT_BINDING_MISMATCH") from None
        except Exception:
            raise ProviderError(503, "EGRESS_DECLARATION_UNAVAILABLE") from None
        if not isinstance(declaration, EgressDeclaration) or not isinstance(
            declaration.policy, EgressPolicy
        ):
            raise ProviderError(503, "EGRESS_DECLARATION_INVALID")
        policy = declaration.policy
        rules = [
            EgressRule(host=rule.host, path_prefix=rule.path_prefix, method=rule.method)
            for rule in policy.rules
            if rule.method in _SUPPORTED_METHODS
        ]
        if not rules:
            raise ProviderError(404, "EGRESS_DECLARATION_NOT_FOUND")
        if not 1 <= policy.max_egress_bytes <= MAX_PROVIDER_ARTIFACT_BYTES:
            raise ProviderError(503, "EGRESS_BYTE_LIMIT_UNSUPPORTED")
        try:
            # The durable grant has no egress-secret binding.  The independent
            # secret_delivery declaration is never promoted to Authorization
            # material, so this response cannot enable plaintext secret egress.
            return EgressDeclarationResponse(
                **self._identity(binding, request_id=request.request_id),
                rules=rules,
                max_egress_bytes=policy.max_egress_bytes,
                secret_refs=[],
            )
        except ValidationError:
            raise ProviderError(503, "EGRESS_DECLARATION_INVALID") from None

    async def artifact_metadata(
        self,
        *,
        claims: PluginClaims,
        request: ArtifactMetadataRequest,
    ) -> ArtifactMetadataResponse:
        binding = await self._pod_binding(claims, request)
        if request.arguments_digest != EMPTY_DIGEST:
            raise ProviderError(403, "POD_ARTIFACT_ARGUMENTS_NOT_ALLOWED")
        if not is_valid_digest(request.target_ref):
            raise ProviderError(403, "POD_ARTIFACT_DIGEST_BINDING_REQUIRED")
        artifact = binding.artifact
        publication = artifact.publication
        if publication is None:
            raise ProviderError(503, "ARTIFACT_PUBLICATION_METADATA_UNAVAILABLE")
        if request.target_ref == artifact.digest:
            image_ref = artifact.image_ref
        elif request.target_ref == binding.backend.backend_digest:
            image_ref = publication.backend_image_ref
        else:
            # Arbitrary job-output references have no trustworthy metadata
            # source in this slice; never turn the requested digest into a size.
            raise ProviderError(404, "ARTIFACT_METADATA_NOT_FOUND")
        try:
            ref = parse_oci_ref(image_ref)
            if ref.digest != request.target_ref:
                raise ProviderError(403, "ARTIFACT_OCI_BINDING_MISMATCH")
            loader = self._artifact_loader
            if loader is None:
                # Bound the actual fetch by this request, not just the response.
                loader = HttpxOciArtifactLoader(
                    allowed_registries=("ghcr.io",),
                    max_manifest_bytes=request.max_result_bytes,
                    max_blob_bytes=MAX_PROVIDER_ARTIFACT_BYTES,
                )
            loaded = await loader.load_manifest(ref)
        except ProviderError:
            raise
        except Exception as exc:
            # The existing loader proves a configured byte-cap breach with this
            # fixed safe message. Never propagate registry text to the client.
            if str(exc) == "OCI object size cap exceeded":
                raise ProviderError(413, "ARTIFACT_RESULT_BYTE_LIMIT_EXCEEDED") from None
            raise ProviderError(503, "ARTIFACT_OCI_METADATA_UNAVAILABLE") from None
        data = getattr(loaded, "data", None)
        size = getattr(loaded, "size", None)
        digest = getattr(loaded, "digest", None)
        if loaded is None:
            raise ProviderError(404, "ARTIFACT_METADATA_NOT_FOUND")
        if (
            not isinstance(data, bytes)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size != len(data)
            or not 0 <= size <= MAX_PROVIDER_ARTIFACT_BYTES
            or digest != request.target_ref
            or "sha256:" + hashlib.sha256(data).hexdigest() != request.target_ref
        ):
            raise ProviderError(503, "ARTIFACT_OCI_METADATA_INVALID")
        if size > request.max_result_bytes:
            raise ProviderError(413, "ARTIFACT_RESULT_BYTE_LIMIT_EXCEEDED")
        try:
            return ArtifactMetadataResponse(
                **self._pod_identity(binding, request),
                byte_count=size,
            )
        except ValidationError:
            raise ProviderError(503, "ARTIFACT_METADATA_INVALID") from None

    @staticmethod
    def _validate_unwired_action(action: str, request: PodActionRequest) -> None:
        if action in {"read_status", "read_logs"} and (
            request.target_ref != request.job_id
            or request.arguments_digest != EMPTY_DIGEST
        ):
            raise ProviderError(403, "POD_STATUS_LOG_DIGEST_BINDING_REQUIRED")
        if action == "send_agent_message" and (
            request.target_ref != request.job_id
            or request.arguments_digest == EMPTY_DIGEST
        ):
            raise ProviderError(403, "POD_MESSAGE_DIGEST_BINDING_REQUIRED")

    @staticmethod
    def _unwired() -> NoReturn:
        raise ProviderError(503, "POD_PLATFORM_DATA_SOURCE_UNWIRED")

    async def read_status(
        self, *, claims: PluginClaims, request: PodActionRequest
    ) -> StatusProviderResponse:
        self._validate_unwired_action("read_status", request)
        await self._pod_binding(claims, request)
        self._unwired()

    async def read_logs(
        self, *, claims: PluginClaims, request: PodActionRequest
    ) -> LogsProviderResponse:
        self._validate_unwired_action("read_logs", request)
        await self._pod_binding(claims, request)
        self._unwired()

    async def send_agent_message(
        self, *, claims: PluginClaims, request: PodActionRequest
    ) -> MessageProviderResponse:
        self._validate_unwired_action("send_agent_message", request)
        await self._pod_binding(claims, request)
        self._unwired()

    async def secret_metadata(
        self,
        *,
        claims: PluginClaims,
        request: SecretMetadataRequest,
    ) -> SecretMetadataResponse:
        binding = await self._bind(
            claims,
            backend_digest=request.backend_digest,
            request_id=request.request_id,
            capability="secrets",
        )
        try:
            allowed_refs = frozenset(secret_delivery_allowlist(binding.backend.manifest))
        except ValueError:
            raise ProviderError(403, "SECRET_DELIVERY_DECLARATION_INVALID") from None
        if request.secret_ref not in allowed_refs:
            raise ProviderError(403, "SECRET_REF_NOT_ALLOWLISTED")
        envelope = CapabilityEnvelope(
            request_id=request.request_id,
            company_id=claims.company_id,
            cabinet_id=claims.cabinet_id,
            project_id=claims.project_id,
            subject_id=claims.sub,
            plugin_instance_id=claims.plugin_instance_id,
            artifact_digest=claims.artifact_digest,
            grant_id=claims.grant_id,
        )
        try:
            result = await self._secret_reader.read(
                SecretsReadRequest(secret_ref=request.secret_ref, envelope=envelope)
            )
        except CapabilityAccessDenied:
            raise ProviderError(403, "SECRET_REF_SCOPE_DENIED") from None
        except (CapabilityConfigurationError, CapabilityBackendError):
            raise ProviderError(503, "SECRET_METADATA_UNAVAILABLE") from None
        except Exception:
            raise ProviderError(503, "SECRET_METADATA_UNAVAILABLE") from None
        if (
            not isinstance(result, SecretsReadResponse)
            or result.request_id != request.request_id
            or result.secret_ref != request.secret_ref
            or result.version != result.value_digest
        ):
            raise ProviderError(503, "SECRET_METADATA_INVALID")
        try:
            return SecretMetadataResponse(
                **self._identity(binding, request_id=request.request_id),
                secret_ref=result.secret_ref,
                value_digest=result.value_digest,
                metadata_digest=result.digest,
            )
        except ValidationError:
            raise ProviderError(503, "SECRET_METADATA_INVALID") from None


def build_scoped_runtime_provider(platform: Any) -> ScopedRuntimeProvider:
    """Build once from the production platform created by the installation saga."""
    try:
        pod_adapter = platform.registry.resolve("pods")
        network_adapter = platform.registry.resolve("network")
        secret_reader = platform.registry.resolve("secrets")
        executor = pod_adapter._executor
        resolver = executor._resolver
        declarations = network_adapter._transport._declarations
        return ScopedRuntimeProvider(
            resolver=resolver,
            declarations=declarations,
            pod_executor=executor,
            secret_reader=secret_reader,
        )
    except ProviderError:
        raise
    except Exception:
        raise ProviderError(503, "RUNTIME_PROVIDER_NOT_WIRED") from None


def get_scoped_runtime_provider() -> ScopedRuntimeProvider:
    """Zero-arg dependency key; production installs a per-app override.

    There is deliberately no request or app.state lookup here.  An application
    that did not complete trusted composition fails closed instead of FastAPI
    interpreting a request annotation as a tenant-controlled query parameter.
    """
    raise ProviderError(503, "RUNTIME_PROVIDER_NOT_WIRED")


def install_scoped_runtime_provider(app: Any, provider: ScopedRuntimeProvider) -> None:
    """Register the already-composed provider on exactly one FastAPI app."""
    if not isinstance(provider, ScopedRuntimeProvider):
        raise ProviderError(503, "RUNTIME_PROVIDER_NOT_WIRED")
    overrides = getattr(app, "dependency_overrides", None)
    if not isinstance(overrides, dict):
        raise ProviderError(503, "RUNTIME_PROVIDER_NOT_WIRED")
    existing = overrides.get(get_scoped_runtime_provider)
    if existing is not None:
        try:
            current = existing()
        except Exception:
            current = None
        if current is provider:
            return
        raise ProviderError(503, "RUNTIME_PROVIDER_ALREADY_INSTALLED")
    overrides[get_scoped_runtime_provider] = lambda: provider


__all__ = [
    "ScopedRuntimeProvider",
    "build_scoped_runtime_provider",
    "get_scoped_runtime_provider",
    "install_scoped_runtime_provider",
]
