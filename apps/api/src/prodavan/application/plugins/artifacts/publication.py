"""Authenticated GitHub Actions CI -> platform plugin publication service.

Order is fail-closed: source + OIDC binding first, then the actual signed OCI
manifest is fetched from the pinned registry ref and compared to the submitted
manifest, then the canonical source-manifest blob is fetched and digest-verified.
Only after existing cosign/evidence verification and compatibility checks does
the current artifact registry publish. This module reads a durable administrative
PluginPublicationApproval, never creates CapabilityGrant, never performs
install/deploy, and never trusts caller
capability claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.artifacts.oci import is_valid_digest, parse_oci_ref
from prodavan.application.plugins.artifacts.oidc import GITHUB_ACTIONS_ISSUER
from prodavan.application.plugins.artifacts.oidc import GitHubActionsClaims
from prodavan.application.plugins.artifacts.oidc import OidcValidationError
from prodavan.application.plugins.artifacts.registry import PluginArtifactRegistry
from prodavan.application.plugins.artifacts.registry import PublicationMetadata
from prodavan.application.plugins.artifacts.verification import ArtifactVerifier
from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    PluginInstanceRecord,
    PluginPublicationApproval,
)
from prodavan.application.plugins.admin.ports import (
    GrantStorePort,
    InstanceStorePort,
    PublicationApprovalReaderPort,
)
from prodavan.application.plugins.sources.ports import ModuleSourceRepositoryPort
from prodavan.application.plugins.sources.repository import ModuleSourceRepositoryError
from prodavan.application.plugins.sources.types import ModuleSource
from prodavan.application.plugins.sources.types import ModuleSourceStatus
from prodavan.domain.plugins.helpers import plugin_instance_id_for
from prodavan.domain.plugins.scopes import validate_plugin_instance_scopes
from prodavan.domain.plugins.types import (
    POD_SCOPE_MAX_LENGTH,
    POD_SCOPE_PATTERN,
    PLUGIN_ID_PATTERN,
    SEMVER_PATTERN,
    PluginManifest,
)

_OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_PLUGIN_ARTIFACT_TYPE = "application/vnd.agentscale.plugin.oci"
_PLUGIN_MANIFEST_MEDIA_TYPE = "application/vnd.agentscale.plugin.manifest.v1+json"
_LAYER_ROLE = "com.agentscale.plugin.layer.role"
_A_BACKEND_DIGEST = "backend_digest"
_A_BACKEND_IMAGE_REF = "com.agentscale.plugin.backend_image_ref"
_A_SOURCE_REPOSITORY = "com.agentscale.plugin.source_repository"
_A_SOURCE_SHA = "com.agentscale.plugin.source_sha"
_A_MANIFEST_DIGEST = "com.agentscale.plugin.manifest_digest"
_A_PLUGIN_ID = "plugin_id"
_A_VERSION = "manifest_version"
_A_PROTOCOL = "protocol"
_A_CAPABILITIES = "requestedCapabilities"
_A_MIN_PLATFORM = "min_platform_version"
_ROLE_MANIFEST = "canonical-manifest"
_ROLE_FRONTEND = "frontend-dist"
_ROLE_MIGRATIONS = "migrations"
_FORBIDDEN_KEYS = frozenset({"artifact_digest", "signature"})
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_URL_RE = re.compile(
    r"^/(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/"
    r"actions/runs/(?P<run_id>[0-9]+)(?:/attempts/(?P<attempt>[0-9]+))?$"
)


class PublicationErrorCode(StrEnum):
    SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
    SOURCE_NOT_READY = "SOURCE_NOT_READY"
    SOURCE_PROVIDER_UNSUPPORTED = "SOURCE_PROVIDER_UNSUPPORTED"
    SOURCE_PLUGIN_MISMATCH = "SOURCE_PLUGIN_MISMATCH"
    SOURCE_REPOSITORY_UNAVAILABLE = "SOURCE_REPOSITORY_UNAVAILABLE"
    INPUT_INVALID = "INPUT_INVALID"
    OCI_REF_INVALID = "OCI_REF_INVALID"
    BACKEND_BUNDLE_COLLISION = "BACKEND_BUNDLE_COLLISION"
    RUN_URL_MISMATCH = "RUN_URL_MISMATCH"
    OCI_MANIFEST_MISMATCH = "OCI_MANIFEST_MISMATCH"
    CANONICAL_MANIFEST_INVALID = "CANONICAL_MANIFEST_INVALID"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    SCOPE_RESOLUTION_UNAVAILABLE = "SCOPE_RESOLUTION_UNAVAILABLE"
    ARTIFACT_LOADER_UNAVAILABLE = "ARTIFACT_LOADER_UNAVAILABLE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_BINDING_MISMATCH = "APPROVAL_BINDING_MISMATCH"
    APPROVAL_CONSUMED = "APPROVAL_CONSUMED"
    APPROVAL_UNAVAILABLE = "APPROVAL_UNAVAILABLE"
    SIGNER_IDENTITY_MISMATCH = "SIGNER_IDENTITY_MISMATCH"
    REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    REGISTRY_CONFLICT = "REGISTRY_CONFLICT"
    OIDC_VALIDATION_UNAVAILABLE = "OIDC_VALIDATION_UNAVAILABLE"


class PluginInstanceScopeResolutionError(RuntimeError):
    """Durable scope identity is unavailable; publication must fail closed."""

    code = PublicationErrorCode.SCOPE_RESOLUTION_UNAVAILABLE


class PluginInstanceScopeMismatchError(PluginInstanceScopeResolutionError):
    """Persisted grant/instance identity is partial, foreign, or contradictory."""

    code = PublicationErrorCode.SCOPE_MISMATCH


class PluginInstanceScopeResolver(Protocol):
    """Trusted async CI-only installation identity resolver."""

    async def resolve(self, *, company_id: str, plugin_id: str) -> str: ...


class DurablePluginInstanceScopeResolver:
    """Resolve scope from the shared durable grant+instance pair, never CI input."""

    def __init__(
        self,
        *,
        grant_store: GrantStorePort,
        instance_store: InstanceStorePort,
    ) -> None:
        if grant_store is None or instance_store is None:
            raise ValueError("durable grant_store and instance_store are required")
        self._grant_store = grant_store
        self._instance_store = instance_store

    async def resolve(self, *, company_id: str, plugin_id: str) -> str:
        try:
            grant = await self._grant_store.get(
                company_id=company_id,
                plugin_id=plugin_id,
            )
            instance = await self._instance_store.get(
                company_id=company_id,
                plugin_id=plugin_id,
            )
        except Exception as exc:
            raise PluginInstanceScopeResolutionError(
                "durable plugin instance scope read failed"
            ) from exc

        if grant is None and instance is None:
            return plugin_instance_id_for(company_id, plugin_id)
        if grant is None or instance is None:
            raise PluginInstanceScopeMismatchError(
                "persisted grant/instance pair is incomplete"
            )
        if not isinstance(grant, CapabilityGrantRecord) or not isinstance(
            instance,
            PluginInstanceRecord,
        ):
            raise PluginInstanceScopeMismatchError(
                "persisted grant/instance records have invalid types"
            )
        if (
            (grant.company_id, grant.plugin_id) != (company_id, plugin_id)
            or (instance.company_id, instance.plugin_id) != (company_id, plugin_id)
            or grant.plugin_instance_id != instance.plugin_instance_id
        ):
            raise PluginInstanceScopeMismatchError(
                "persisted grant/instance identity mismatch"
            )

        # Revoked records deliberately remain authoritative for a future re-grant.
        # No ACTIVE check or PublicationApproval read belongs in this resolver.
        resolved = instance.plugin_instance_id
        if (
            not isinstance(resolved, str)
            or len(resolved) > POD_SCOPE_MAX_LENGTH
            or POD_SCOPE_PATTERN.fullmatch(resolved) is None
        ):
            raise PluginInstanceScopeMismatchError(
                "persisted plugin_instance_id is invalid"
            )
        return resolved


@dataclass(frozen=True, slots=True)
class PublicationEvidence:
    """Only two external CI/static-origin confirmations plus safe digest links.

    Caller-provided signer identity, issuer, capability grant and raw evidence
    are ignored. Cosign evidence is produced by the injected verifier.
    """

    scan_verified: bool
    csp_verified: bool
    sbom_digest: str | None = None
    provenance_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scan_verified, bool) or not isinstance(self.csp_verified, bool):
            raise ValueError("scan_verified and csp_verified must be booleans")
        for name, value in (
            ("sbom_digest", self.sbom_digest),
            ("provenance_digest", self.provenance_digest),
        ):
            if value is not None and not is_valid_digest(value):
                raise ValueError(f"{name} must be a sha256 digest")


@dataclass(frozen=True, slots=True)
class ArtifactPublicationCommand:
    source_id: str
    plugin_id: str
    version: str
    bundle_ref: str
    bundle_digest: str
    backend_ref: str
    backend_digest: str
    manifest: Mapping[str, Any]
    commit_sha: str
    ref: str
    run_url: str
    evidence: PublicationEvidence

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "plugin_id",
            "version",
            "bundle_ref",
            "backend_ref",
            "commit_sha",
            "ref",
            "run_url",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be a non-empty stripped string")
        if not isinstance(self.manifest, Mapping):
            raise ValueError("manifest must be an object")
        if PLUGIN_ID_PATTERN.fullmatch(self.plugin_id) is None:
            raise ValueError("plugin_id has invalid canonical grammar")
        if SEMVER_PATTERN.fullmatch(self.version) is None:
            raise ValueError("version must be SemVer 2.0.0")
        if _SHA1_RE.fullmatch(self.commit_sha) is None:
            raise ValueError("commit_sha must be a 40 lowercase hex Git SHA")
        if not is_valid_digest(self.bundle_digest) or not is_valid_digest(self.backend_digest):
            raise ValueError("bundle_digest and backend_digest must be sha256 digests")
        if not isinstance(self.evidence, PublicationEvidence):
            raise ValueError("evidence must be PublicationEvidence")


@dataclass(frozen=True, slots=True)
class PublicationResponse:
    """Typed result without tokens, secrets or internal/upstream URLs."""

    status: str
    code: str
    plugin_id: str
    version: str
    bundle_digest: str
    backend_digest: str
    source_id: str

    @classmethod
    def published(cls, command: ArtifactPublicationCommand) -> PublicationResponse:
        return cls(
            status="PUBLISHED",
            code="PUBLISHED",
            plugin_id=command.plugin_id,
            version=command.version,
            bundle_digest=command.bundle_digest,
            backend_digest=command.backend_digest,
            source_id=command.source_id,
        )

    @classmethod
    def quarantined(
        cls,
        command: ArtifactPublicationCommand,
        code: str,
    ) -> PublicationResponse:
        return cls(
            status="QUARANTINED",
            code=code,
            plugin_id=command.plugin_id,
            version=command.version,
            bundle_digest=command.bundle_digest,
            backend_digest=command.backend_digest,
            source_id=command.source_id,
        )


@dataclass(frozen=True, slots=True)
class LoadedOciObject:
    data: bytes
    digest: str
    size: int


class ArtifactLoaderPort(Protocol):
    async def load_manifest(self, ref: object) -> LoadedOciObject: ...

    async def load_blob(
        self,
        ref: object,
        *,
        digest: str,
        expected_size: int | None = None,
    ) -> LoadedOciObject: ...


class RegistryAuthorizerPort(Protocol):
    """Optional read-only registry token source; implementations never log tokens."""

    async def authorization_for(self, repository: str) -> str | None: ...


class _LoaderError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: PublicationErrorCode = PublicationErrorCode.ARTIFACT_LOADER_UNAVAILABLE,
    ) -> None:
        self.code = code
        super().__init__(message)


class _SourceError(RuntimeError):
    def __init__(self, code: PublicationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class _ApprovalError(RuntimeError):
    def __init__(self, code: PublicationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class HttpxOciArtifactLoader(ArtifactLoaderPort):
    """Small digest-verified OCI registry client.

    trust_env is disabled and redirects are forbidden, so proxy configuration
    cannot silently move a digest fetch. Hosts are allowlisted. Private GHCR
    wiring is an explicit integration task: no token is guessed or logged.
    """

    def __init__(
        self,
        *,
        allowed_registries: Collection[str] = ("ghcr.io",),
        max_manifest_bytes: int = 4 * 1024 * 1024,
        max_blob_bytes: int = 1024 * 1024,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        authorizer: RegistryAuthorizerPort | None = None,
    ) -> None:
        registries = tuple(value.lower() for value in allowed_registries)
        if not registries or any(not value or ":" in value or "/" in value for value in registries):
            raise ValueError("allowed OCI registries must be plain https hosts")
        if max_manifest_bytes <= 0 or max_blob_bytes <= 0:
            raise ValueError("OCI size caps must be positive")
        self._allowed = frozenset(registries)
        self._max_manifest = max_manifest_bytes
        self._max_blob = max_blob_bytes
        self._client_factory = client_factory or self._default_client
        self._authorizer = authorizer

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(10.0),
        )

    async def load_manifest(self, ref: object) -> LoadedOciObject:
        registry, repository, digest = self._parts(ref)
        url = f"https://{registry}/v2/{repository}/manifests/{digest}"
        accept = ", ".join((_OCI_MANIFEST_MEDIA_TYPE, "application/vnd.docker.distribution.manifest.v2+json"))
        data = await self._get(url, accept=accept, max_bytes=self._max_manifest)
        self._verify_digest(
            data,
            digest,
            mismatch_code=PublicationErrorCode.OCI_MANIFEST_MISMATCH,
        )
        return LoadedOciObject(data=data, digest=digest, size=len(data))

    async def load_blob(
        self,
        ref: object,
        *,
        digest: str,
        expected_size: int | None = None,
    ) -> LoadedOciObject:
        registry, repository, _ = self._parts(ref)
        if not is_valid_digest(digest):
            raise _LoaderError("invalid blob digest")
        if expected_size is not None and (
            isinstance(expected_size, bool) or not isinstance(expected_size, int)
            or expected_size < 1 or expected_size > self._max_blob
        ):
            raise _LoaderError("canonical manifest size cap exceeded")
        url = f"https://{registry}/v2/{repository}/blobs/{digest}"
        data = await self._get(url, accept="*/*", max_bytes=self._max_blob)
        self._verify_digest(
            data,
            digest,
            mismatch_code=PublicationErrorCode.CANONICAL_MANIFEST_INVALID,
        )
        if expected_size is not None and len(data) != expected_size:
            raise _LoaderError(
                "blob size does not match descriptor",
                PublicationErrorCode.CANONICAL_MANIFEST_INVALID,
            )
        return LoadedOciObject(data=data, digest=digest, size=len(data))

    def _parts(self, ref: object) -> tuple[str, str, str]:
        image_ref = getattr(ref, "image_ref", None)
        candidate = image_ref() if callable(image_ref) else str(ref)
        try:
            parsed = parse_oci_ref(candidate)
        except (TypeError, ValueError) as exc:
            raise _LoaderError("invalid digest-pinned OCI ref") from exc
        if parsed.registry.lower() not in self._allowed:
            raise _LoaderError("OCI registry is not allowlisted")
        return parsed.registry.lower(), parsed.repository, parsed.digest

    async def _get(self, url: str, *, accept: str, max_bytes: int) -> bytes:
        headers = {"Accept": accept}
        if self._authorizer is not None:
            repository = url.split("/v2/", 1)[1].rsplit("/", 2)[0]
            token = await self._authorizer.authorization_for(repository)
            if token:
                headers["Authorization"] = token
        try:
            async with self._client_factory() as client:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code != 200:
                        raise _LoaderError("OCI registry request was not accepted")
                    encoding = response.headers.get("Content-Encoding", "identity").lower()
                    if encoding not in ("identity", ""):
                        raise _LoaderError("unexpected OCI content encoding")
                    declared = response.headers.get("Content-Length")
                    if declared is not None:
                        try:
                            if int(declared) > max_bytes:
                                raise _LoaderError("OCI object size cap exceeded")
                        except ValueError as exc:
                            raise _LoaderError("invalid OCI Content-Length") from exc
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise _LoaderError("OCI object size cap exceeded")
                        chunks.append(chunk)
        except _LoaderError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise _LoaderError("OCI registry transport failed") from exc
        return b"".join(chunks)

    @staticmethod
    def _verify_digest(
        data: bytes,
        digest: str,
        *,
        mismatch_code: PublicationErrorCode,
    ) -> None:
        actual = "sha256:" + hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise _LoaderError("OCI object digest mismatch", mismatch_code)


def _strict_json(data: bytes) -> Any:
    def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value}")

    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _LoaderError("OCI object is not strict JSON") from exc


def _canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """The exact byte representation built and signed by trusted plugin CI."""

    try:
        return (
            json.dumps(
                dict(manifest),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _LoaderError("canonical manifest is not strict JSON") from exc


@dataclass(frozen=True, slots=True)
class _Bundle:
    ref: object
    backend_ref: object
    config_digest: str
    config_size: int
    capabilities: tuple[str, ...]
    min_platform_version: str


def _reject_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        if _FORBIDDEN_KEYS & set(value):
            raise _LoaderError("self-reference or signature key is forbidden")
        for nested in value.values():
            _reject_forbidden_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_forbidden_keys(nested)


def _annotation_string(manifest: Mapping[str, Any], name: str) -> str:
    annotations = manifest.get("annotations")
    if not isinstance(annotations, Mapping):
        raise _LoaderError("OCI manifest annotations are missing")
    value = annotations.get(name)
    if not isinstance(value, str) or not value:
        raise _LoaderError(f"OCI annotation {name} is missing")
    return value


def _validate_bundle_manifest(
    manifest: Mapping[str, Any],
    *,
    bundle_ref: object,
    backend_ref: object,
    backend_digest: str,
    plugin_id: str,
    version: str,
    source: ModuleSource,
    commit_sha: str,
) -> _Bundle:
    if not isinstance(manifest, Mapping):
        raise _LoaderError("forbidden or malformed OCI manifest")
    _reject_forbidden_keys(manifest)
    if manifest.get("schemaVersion") != 2:
        raise _LoaderError("unsupported OCI schemaVersion")
    if manifest.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE:
        raise _LoaderError("unsupported OCI manifest media type")
    if manifest.get("artifactType") != _PLUGIN_ARTIFACT_TYPE:
        raise _LoaderError("unsupported plugin artifact type")

    annotations = manifest.get("annotations")
    if not isinstance(annotations, Mapping):
        raise _LoaderError("OCI manifest annotations are missing")
    expected_annotations = {
        _A_PLUGIN_ID: plugin_id,
        _A_VERSION: version,
        _A_PROTOCOL: "plugin-rpc/1",
        _A_BACKEND_DIGEST: backend_digest,
        _A_SOURCE_REPOSITORY: source.full_name,
        _A_SOURCE_SHA: commit_sha,
    }
    for name, expected in expected_annotations.items():
        if annotations.get(name) != expected:
            raise _LoaderError(f"OCI annotation {name} does not match trusted source")

    linked_backend_digest = _annotation_string(manifest, _A_BACKEND_DIGEST)
    try:
        linked_backend_ref = parse_oci_ref(_annotation_string(manifest, _A_BACKEND_IMAGE_REF))
    except ValueError as exc:
        raise _LoaderError("backend annotation is not digest pinned") from exc
    if linked_backend_digest != backend_digest or linked_backend_ref.digest != backend_digest:
        raise _LoaderError("backend digest annotation mismatch")
    if linked_backend_ref.image_ref() != backend_ref.image_ref():
        raise _LoaderError("backend image_ref annotation mismatch")
    if linked_backend_ref.registry.lower() != bundle_ref.registry.lower():
        raise _LoaderError("backend and bundle registries differ")

    config = manifest.get("config")
    if not isinstance(config, Mapping) or config.get("mediaType") != _PLUGIN_MANIFEST_MEDIA_TYPE:
        raise _LoaderError("canonical source manifest is not the OCI config")
    config_digest = config.get("digest")
    config_size = config.get("size")
    max_canonical = 1024 * 1024
    if (
        not is_valid_digest(config_digest)
        or isinstance(config_size, bool)
        or not isinstance(config_size, int)
        or config_size < 1
        or config_size > max_canonical
    ):
        raise _LoaderError("canonical manifest descriptor is invalid or too large")
    if annotations.get(_A_MANIFEST_DIGEST) != config_digest:
        raise _LoaderError("canonical manifest digest annotation mismatch")

    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise _LoaderError("OCI manifest has no layers")
    roles: dict[str, int] = {}
    canonical_found = False
    for layer in layers:
        if not isinstance(layer, Mapping):
            raise _LoaderError("OCI layer descriptor is malformed")
        digest = layer.get("digest")
        size = layer.get("size")
        if not is_valid_digest(digest) or isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise _LoaderError("OCI layer descriptor has invalid digest/size")
        layer_annotations = layer.get("annotations")
        if not isinstance(layer_annotations, Mapping):
            raise _LoaderError("OCI layer role annotations are missing")
        role = layer_annotations.get(_LAYER_ROLE)
        if not isinstance(role, str):
            raise _LoaderError("OCI layer role is missing")
        if role not in {_ROLE_MANIFEST, _ROLE_FRONTEND, _ROLE_MIGRATIONS} and not role.startswith("extension/"):
            raise _LoaderError("unknown OCI layer role")
        roles[role] = roles.get(role, 0) + 1
        if roles[role] > 1:
            raise _LoaderError("duplicate OCI layer role")
        if role == _ROLE_MANIFEST:
            canonical_found = True
            if layer.get("mediaType") != _PLUGIN_MANIFEST_MEDIA_TYPE:
                raise _LoaderError("canonical manifest layer media type mismatch")
            if digest != config_digest or size != config_size:
                raise _LoaderError("canonical layer is not the identical config blob")
    if not canonical_found or roles.get(_ROLE_FRONTEND, 0) != 1 or roles.get(_ROLE_MIGRATIONS, 0) != 1:
        raise _LoaderError("required canonical/frontend/migrations layers are missing")
    if any(roles.get(role, 0) > 1 for role in (_ROLE_MANIFEST, _ROLE_FRONTEND, _ROLE_MIGRATIONS)):
        raise _LoaderError("required OCI layer roles must be unique")

    raw_capabilities = _annotation_string(manifest, _A_CAPABILITIES)
    capabilities = tuple(item.strip() for item in raw_capabilities.split(",") if item.strip())
    min_platform = _annotation_string(manifest, _A_MIN_PLATFORM)
    if SEMVER_PATTERN.fullmatch(min_platform) is None:
        raise _LoaderError("minimum platform annotation is not SemVer")
    return _Bundle(
        ref=bundle_ref,
        backend_ref=backend_ref,
        config_digest=config_digest,
        config_size=config_size,
        capabilities=capabilities,
        min_platform_version=min_platform,
    )


class PluginCiPublicationService:
    """Application service binding trusted CI to the existing artifact registry."""

    def __init__(
        self,
        *,
        source_repository: ModuleSourceRepositoryPort,
        oidc_validator: object,
        artifact_loader: ArtifactLoaderPort,
        verifier: ArtifactVerifier,
        artifact_registry: PluginArtifactRegistry,
        approval_reader: PublicationApprovalReaderPort,
        instance_scope_resolver: PluginInstanceScopeResolver,
        manifest_validator: Callable[[dict[str, Any]], object] | None = None,
    ) -> None:
        self._sources = source_repository
        self._oidc = oidc_validator
        self._loader = artifact_loader
        self._verifier = verifier
        self._registry = artifact_registry
        self._approvals = approval_reader
        if not callable(getattr(instance_scope_resolver, "resolve", None)):
            raise TypeError("instance_scope_resolver must implement async resolve()")
        self._instance_scopes = instance_scope_resolver
        self._manifest_validator = manifest_validator

    async def publish(
        self,
        command: ArtifactPublicationCommand,
        *,
        oidc_token: str,
    ) -> PublicationResponse:
        try:
            source = await self._source(command)
        except _SourceError as exc:
            return PublicationResponse.quarantined(command, exc.code.value)
        try:
            claims: GitHubActionsClaims = await self._oidc.validate_for_source(
                oidc_token,
                source=source,
                commit_sha=command.commit_sha,
                ref=command.ref,
            )
        except OidcValidationError as exc:
            return PublicationResponse.quarantined(command, exc.code.value)
        except Exception:  # noqa: BLE001 — validator integration must fail closed
            return PublicationResponse.quarantined(
                command,
                PublicationErrorCode.OIDC_VALIDATION_UNAVAILABLE,
            )

        if not self._run_url_matches(command, source, claims):
            return PublicationResponse.quarantined(command, PublicationErrorCode.RUN_URL_MISMATCH)

        try:
            bundle_ref = parse_oci_ref(command.bundle_ref)
            backend_ref = parse_oci_ref(command.backend_ref)
        except ValueError:
            return PublicationResponse.quarantined(command, PublicationErrorCode.OCI_REF_INVALID)
        if bundle_ref.digest != command.bundle_digest or backend_ref.digest != command.backend_digest:
            return PublicationResponse.quarantined(command, PublicationErrorCode.OCI_REF_INVALID)
        if (
            backend_ref.digest == bundle_ref.digest
            or backend_ref.image_ref() == bundle_ref.image_ref()
        ):
            return PublicationResponse.quarantined(command, PublicationErrorCode.BACKEND_BUNDLE_COLLISION)

        try:
            remote = await self._loader.load_manifest(bundle_ref)
            submitted = _strict_json(remote.data)
            if submitted != dict(command.manifest):
                return PublicationResponse.quarantined(
                    command,
                    PublicationErrorCode.OCI_MANIFEST_MISMATCH,
                )
            try:
                bundle = _validate_bundle_manifest(
                    command.manifest,
                    bundle_ref=bundle_ref,
                    backend_ref=backend_ref,
                    backend_digest=command.backend_digest,
                    plugin_id=command.plugin_id,
                    version=command.version,
                    source=source,
                    commit_sha=command.commit_sha,
                )
            except _LoaderError:
                return PublicationResponse.quarantined(
                    command,
                    PublicationErrorCode.OCI_MANIFEST_MISMATCH,
                )
            canonical = await self._loader.load_blob(
                bundle_ref,
                digest=bundle.config_digest,
                expected_size=bundle.config_size,
            )
            canonical_manifest = _strict_json(canonical.data)
        except _LoaderError as exc:
            return PublicationResponse.quarantined(command, exc.code.value)
        except Exception:  # noqa: BLE001 — loader integration must fail closed
            return PublicationResponse.quarantined(
                command,
                PublicationErrorCode.ARTIFACT_LOADER_UNAVAILABLE,
            )

        try:
            if not isinstance(canonical_manifest, dict):
                raise _LoaderError("canonical OCI blob must contain a JSON object")
            validator = self._manifest_validator
            if validator is None:
                from prodavan.domain.plugins.validation import validate_manifest as validator
            typed_manifest = validator(dict(canonical_manifest))
            if not isinstance(typed_manifest, PluginManifest):
                raise _LoaderError("manifest validator must return PluginManifest")
            expected_bytes = _canonical_manifest_bytes(canonical_manifest)
            if expected_bytes != canonical.data:
                raise _LoaderError("canonical source manifest bytes are not canonical")
            if canonical_manifest.get("plugin_id") != command.plugin_id:
                raise _LoaderError("canonical plugin_id mismatch")
            if canonical_manifest.get("version") != command.version:
                raise _LoaderError("canonical version mismatch")
            if tuple(canonical_manifest.get("capabilities", ())) != bundle.capabilities:
                raise _LoaderError("canonical capabilities annotation mismatch")
            if canonical_manifest.get("min_platform_version") != bundle.min_platform_version:
                raise _LoaderError("canonical platform annotation mismatch")
        except (ValueError, TypeError, _LoaderError):
            return PublicationResponse.quarantined(
                command,
                PublicationErrorCode.CANONICAL_MANIFEST_INVALID,
            )

        try:
            expected_instance_id = await self._instance_scopes.resolve(
                company_id=source.company_id,
                plugin_id=source.plugin_id,
            )
        except PluginInstanceScopeResolutionError as exc:
            return PublicationResponse.quarantined(command, exc.code.value)
        except Exception:
            return PublicationResponse.quarantined(
                command,
                PublicationErrorCode.SCOPE_RESOLUTION_UNAVAILABLE.value,
            )

        try:
            from prodavan.application.plugins.capabilities.adapters.secrets import (
                validate_secret_delivery_ref,
            )
            validate_plugin_instance_scopes(
                typed_manifest,
                company_id=source.company_id,
                plugin_id=source.plugin_id,
                secret_ref_validator=validate_secret_delivery_ref,
                expected_instance_id=expected_instance_id,
            )
        except (ValueError, TypeError):
            # This gate precedes approval consumption, verification and every
            # registry write. A foreign signed scope can never become PUBLISHED.
            return PublicationResponse.quarantined(
                command,
                PublicationErrorCode.SCOPE_MISMATCH.value,
            )

        try:
            approval = await self._approval(
                source,
                command.version,
                command.bundle_digest,
                tuple(canonical_manifest["capabilities"]),
            )
        except _ApprovalError as exc:
            return PublicationResponse.quarantined(command, exc.code.value)

        try:
            outcome = self._verifier.verify(
                bundle_ref,
                canonical_manifest,
                grant=approval,
                scan_verified=command.evidence.scan_verified,
                csp_verified=command.evidence.csp_verified,
            )
        except Exception:
            return PublicationResponse.quarantined(
                command,
                PublicationErrorCode.VERIFICATION_FAILED,
            )
        if outcome.outcome is not RegistryOutcome.APPLIED:
            return PublicationResponse.quarantined(command, PublicationErrorCode.VERIFICATION_FAILED)
        evidence = outcome.evidence
        if evidence.identity != source.expected_signer_identity or evidence.issuer != GITHUB_ACTIONS_ISSUER:
            return PublicationResponse.quarantined(command, PublicationErrorCode.SIGNER_IDENTITY_MISMATCH)

        metadata = PublicationMetadata(
            source_id=source.id,
            commit_sha=command.commit_sha,
            backend_digest=command.backend_digest,
            backend_image_ref=backend_ref.image_ref(),
            run_url=command.run_url,
        )
        try:
            result = await self._registry.publish_async(
                bundle_ref,
                canonical_manifest,
                outcome,
                actor=f"github-actions:{source.full_name}:{command.commit_sha}",
                grant=approval,
                scan_verified=command.evidence.scan_verified,
                csp_verified=command.evidence.csp_verified,
                metadata=metadata,
            )
        except Exception:
            return PublicationResponse.quarantined(
                command,
                PublicationErrorCode.REGISTRY_UNAVAILABLE,
            )
        if result.outcome is RegistryOutcome.APPLIED:
            return PublicationResponse.published(command)
        code = PublicationErrorCode.REGISTRY_CONFLICT
        if result.quarantine is not None:
            code = str(
                result.quarantine.metadata.get(
                    "artifact_error_code",
                    result.quarantine.reason.value,
                )
            )
        return PublicationResponse.quarantined(command, code)

    async def _source(self, command: ArtifactPublicationCommand) -> ModuleSource:
        try:
            source = await self._sources.get_by_id(command.source_id)
        except ModuleSourceRepositoryError as exc:
            raise _SourceError(PublicationErrorCode.SOURCE_REPOSITORY_UNAVAILABLE) from exc
        except Exception as exc:  # noqa: BLE001 — source reads never bypass fail-closed
            raise _SourceError(PublicationErrorCode.SOURCE_REPOSITORY_UNAVAILABLE) from exc
        if source is None:
            raise _SourceError(PublicationErrorCode.SOURCE_UNKNOWN)
        if source.provider != "github_app":
            raise _SourceError(PublicationErrorCode.SOURCE_PROVIDER_UNSUPPORTED)
        if source.status is not ModuleSourceStatus.READY:
            raise _SourceError(PublicationErrorCode.SOURCE_NOT_READY)
        if source.plugin_id != command.plugin_id:
            raise _SourceError(PublicationErrorCode.SOURCE_PLUGIN_MISMATCH)
        return source

    async def _approval(
        self,
        source: ModuleSource,
        version: str,
        bundle_digest: str,
        manifest_capabilities: tuple[str, ...],
    ) -> PluginPublicationApproval:
        try:
            record = await self._approvals.get(
                company_id=source.company_id,
                plugin_id=source.plugin_id,
                manifest_version=version,
                artifact_digest=bundle_digest,
            )
        except Exception as exc:
            raise _ApprovalError(PublicationErrorCode.APPROVAL_UNAVAILABLE) from exc
        if record is None:
            raise _ApprovalError(PublicationErrorCode.APPROVAL_REQUIRED)
        if not isinstance(record, PluginPublicationApproval):
            raise _ApprovalError(PublicationErrorCode.APPROVAL_BINDING_MISMATCH)
        if (
            record.company_id,
            record.plugin_id,
            record.manifest_version,
            record.artifact_digest,
        ) != (source.company_id, source.plugin_id, version, bundle_digest):
            raise _ApprovalError(PublicationErrorCode.APPROVAL_BINDING_MISMATCH)
        if record.consumed_at is not None:
            raise _ApprovalError(PublicationErrorCode.APPROVAL_CONSUMED)
        # Exact capability binding, not merely verifier's subset check: an
        # approval for another manifest cannot silently widen this publication.
        if record.capabilities != manifest_capabilities:
            raise _ApprovalError(PublicationErrorCode.APPROVAL_BINDING_MISMATCH)
        return record

    @staticmethod
    def _run_url_matches(
        command: ArtifactPublicationCommand,
        source: ModuleSource,
        claims: GitHubActionsClaims,
    ) -> bool:
        try:
            parsed = urlsplit(command.run_url)
            port = parsed.port
        except ValueError:
            return False
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return False
        match = _RUN_URL_RE.fullmatch(parsed.path)
        if match is None or match.group("repository") != source.full_name:
            return False
        attempt = match.group("attempt")
        # run_id/run_attempt are optional in small validator fakes and older
        # GitHub claim sets; whenever present they must match exactly.
        if claims.run_id and (
            not claims.run_id.isdigit() or claims.run_id != match.group("run_id")
        ):
            return False
        # GitHub's ordinary HTML run URL has no attempts suffix; when the
        # suffix is present, bind it to the OIDC run_attempt when available.
        if attempt is not None and claims.run_attempt:
            return claims.run_attempt.isdigit() and claims.run_attempt == attempt
        return True


__all__ = [
    "ArtifactLoaderPort",
    "ArtifactPublicationCommand",
    "PublicationApprovalReaderPort",
    "DurablePluginInstanceScopeResolver",
    "HttpxOciArtifactLoader",
    "LoadedOciObject",
    "PluginCiPublicationService",
    "PluginInstanceScopeMismatchError",
    "PluginInstanceScopeResolutionError",
    "PluginInstanceScopeResolver",
    "PublicationErrorCode",
    "PublicationEvidence",
    "PublicationResponse",
    "RegistryAuthorizerPort",
]
