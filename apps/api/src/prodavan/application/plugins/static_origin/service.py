"""Static evidence service: digest-verified plugin frontend bundle readiness.

Scope B1 (cluster-internal evidence, NOT public browser static hosting):

* Verify one immutable plugin bundle by ``sha256`` digest directly from the
  quarantine GHCR repository, falling back to the release repository ONLY
  after a typed not-found signal (proven registry 404), using the existing
  ``HttpxOciArtifactLoader`` (digest-verified fetches, ``ghcr.io`` allowlist)
  and the existing ``GhcrRegistryAuthorizer`` composition owned by
  ``main_plugin_static``.
* Re-validate the full OCI manifest contract (schema/mediaType/artifactType,
  ``plugin_id``/``manifest_version`` annotations, config descriptor, exactly
  one ``frontend-dist`` layer), the canonical config JSON bytes and the
  frontend ``tar.gz`` layer (iterate only, never ``extractall``).
* Cache verified results in a bounded in-memory LRU with per-digest
  singleflight and a short negative cache. No disk cache, no blob storage,
  no tenant MinIO/DB/RBAC access: this zone owns nothing but process memory.

The service is dependency-injectable: any object exposing ``load_manifest``
/ ``load_blob`` returning ``LoadedOciObject``-shaped results can replace the
HTTP loader in offline tests.
"""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

from prodavan.application.plugins.artifacts.oci import (
    FRONTEND_MEDIA_TYPE,
    is_valid_digest,
    parse_oci_ref,
)
from prodavan.domain.plugins.types import PLUGIN_ID_PATTERN, SEMVER_PATTERN

#: OCI image manifest grammar shared with the CI publication verifier.
_OCI_MANIFEST_MEDIA_TYPE: Final = "application/vnd.oci.image.manifest.v1+json"
#: As-built CI publication artifact type (publication.py).
_PLUGIN_ARTIFACT_TYPE: Final = "application/vnd.agentscale.plugin.oci"
#: The canonical source manifest travels as the OCI config blob.
_PLUGIN_MANIFEST_MEDIA_TYPE: Final = "application/vnd.agentscale.plugin.manifest.v1+json"
#: OCI layer-role annotation contract of the plugin CI.
_LAYER_ROLE_ANNOTATION: Final = "com.agentscale.plugin.layer.role"
_FRONTEND_LAYER_ROLE: Final = "frontend-dist"
_FRONTEND_ENTRY_ANNOTATION: Final = "agentscale.plugin.frontend.entry"
#: Binding between the OCI config descriptor and the signed canonical manifest.
_MANIFEST_DIGEST_ANNOTATION: Final = "com.agentscale.plugin.manifest_digest"
_ANNOTATION_PLUGIN_ID: Final = "plugin_id"
_ANNOTATION_VERSION: Final = "manifest_version"
#: Repository leaf name of both quarantine and release bundle repositories.
_BUNDLE_REPOSITORY_LEAF: Final = "bundle"
#: Quarantine namespace is fixed by the CI publication contract (not a setting).
_QUARANTINE_NAMESPACE: Final = "plugins-quarantine"
_ALLOWED_REGISTRY_HOST: Final = "ghcr.io"
_ZERO_DIGEST: Final = "sha256:" + "0" * 64
#: Self-reference / signature keys can never appear inside an evidence manifest.
_FORBIDDEN_KEYS: Final = frozenset({"artifact_digest", "signature"})
#: Loader errors carrying these publication codes mean the registry served
#: different bytes for the pinned digest: a verified negative, not an outage.
_LOADER_DIGEST_MISMATCH_CODES: Final = frozenset(
    {"OCI_MANIFEST_MISMATCH", "CANONICAL_MANIFEST_INVALID"}
)
_FRONTEND_ENTRY_MAX_LENGTH: Final = 512
_MEMBER_SEGMENT_MAX_LENGTH: Final = 255
_SAFE_NAME_CHARACTERS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/"
)


class StaticEvidenceErrorCode(StrEnum):
    """Sanitized evidence outcomes; never expose upstream details."""

    #: Typed 404: invalid request identity, proven registry 404 in both
    #: contours, digest mismatch, failed contract validation, or an
    #: incomplete bundle (missing tar entry/blob).
    BUNDLE_NOT_READY = "BUNDLE_NOT_READY"
    #: Typed 503: every non-not-found loader failure (transport outage,
    #: auth rejection, unexpected loader error). Never falls back and is
    #: never negative-cached because it may be transient.
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    #: Manifest/frontend/tar caps exceeded (typed 413).
    BUNDLE_TOO_LARGE = "BUNDLE_TOO_LARGE"


class StaticEvidenceError(RuntimeError):
    """Typed sanitized failure. ``reason`` is a fixed diagnostic constant."""

    def __init__(self, code: StaticEvidenceErrorCode, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(code.value)


class StaticEvidenceConfigError(ValueError):
    """Composition-time configuration failure (no secrets in the message)."""


@dataclass(frozen=True, slots=True)
class StaticEvidenceConfig:
    """Typed caps, prefixes and cache budgets for the evidence service.

    Repository prefixes point at digest-pinned bundle repositories:
    ``<prefix>/<plugin_id>/bundle@sha256:...``. Quarantine first, release
    fallback. The quarantine namespace is derived from the existing
    ``plugin_oci_registry`` setting, never from a new settings default.
    """

    release_repository_prefix: str
    quarantine_repository_prefix: str
    max_manifest_bytes: int = 4 * 1024 * 1024
    max_config_bytes: int = 1024 * 1024
    max_frontend_layer_bytes: int = 64 * 1024 * 1024
    max_tar_members: int = 4096
    max_tar_member_bytes: int = 32 * 1024 * 1024
    max_tar_total_bytes: int = 256 * 1024 * 1024
    success_cache_seconds: float = 300.0
    negative_cache_seconds: float = 30.0
    success_cache_size: int = 1024
    negative_cache_size: int = 4096
    plugin_id_max_length: int = 128
    version_max_length: int = 128

    def __post_init__(self) -> None:
        for name in (
            "max_manifest_bytes",
            "max_config_bytes",
            "max_frontend_layer_bytes",
            "max_tar_members",
            "max_tar_member_bytes",
            "max_tar_total_bytes",
            "success_cache_size",
            "negative_cache_size",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise StaticEvidenceConfigError(f"{name} must be a positive integer")
        for name in ("success_cache_seconds", "negative_cache_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise StaticEvidenceConfigError(f"{name} must be a positive number")
        for name in ("plugin_id_max_length", "version_max_length"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise StaticEvidenceConfigError(f"{name} must be a positive integer")
        if not isinstance(self.release_repository_prefix, str) or not isinstance(
            self.quarantine_repository_prefix, str
        ):
            raise StaticEvidenceConfigError("repository prefixes must be strings")
        if self.release_repository_prefix == self.quarantine_repository_prefix:
            raise StaticEvidenceConfigError(
                "quarantine and release prefixes must differ"
            )
        release_host = self.release_repository_prefix.split("/", 1)[0]
        quarantine_host = self.quarantine_repository_prefix.split("/", 1)[0]
        if (
            release_host != _ALLOWED_REGISTRY_HOST
            or quarantine_host != _ALLOWED_REGISTRY_HOST
        ):
            raise StaticEvidenceConfigError(
                f"only the {_ALLOWED_REGISTRY_HOST} registry is allowed"
            )

    @classmethod
    def from_settings(cls, settings: Any) -> StaticEvidenceConfig:
        """Build the config from the existing ``plugin_oci_registry`` setting."""
        raw = getattr(settings, "plugin_oci_registry", None)
        if not isinstance(raw, str) or not raw.strip():
            raise StaticEvidenceConfigError("PLUGIN_OCI_REGISTRY must be configured")
        release = _canonical_repository_prefix(raw.strip())
        host, _, remainder = release.partition("/")
        owner = remainder.split("/", 1)[0]
        quarantine = _canonical_repository_prefix(
            f"{host}/{owner}/{_QUARANTINE_NAMESPACE}"
        )
        return cls(
            release_repository_prefix=release,
            quarantine_repository_prefix=quarantine,
        )


def _canonical_repository_prefix(prefix: str) -> str:
    """Validate ``host/<path>`` against the strict OCI repository grammar."""
    probe = f"{prefix}/probe0/{_BUNDLE_REPOSITORY_LEAF}@{_ZERO_DIGEST}"
    try:
        parsed = parse_oci_ref(probe)
    except (TypeError, ValueError) as exc:
        raise StaticEvidenceConfigError(
            "OCI registry prefix must be a lowercase canonical repository path"
        ) from exc
    repository_segments = parsed.repository.split("/")
    # Strip the probe segments; at least host + owner must remain.
    prefix_segments = repository_segments[:-2]
    if len(prefix_segments) < 1:
        raise StaticEvidenceConfigError("OCI registry prefix must include an owner")
    canonical = f"{parsed.registry}/{'/'.join(prefix_segments)}"
    if canonical != prefix:
        raise StaticEvidenceConfigError(
            "OCI registry prefix is not canonical lowercase text"
        )
    return canonical


class StaticEvidenceLoadedObject(Protocol):
    """Structural view of ``LoadedOciObject`` (``data`` is the only contract)."""

    @property
    def data(self) -> bytes: ...


class StaticEvidenceArtifactLoader(Protocol):
    """Structural loader contract satisfied by ``HttpxOciArtifactLoader``."""

    async def load_manifest(self, ref: object) -> StaticEvidenceLoadedObject: ...

    async def load_blob(
        self,
        ref: object,
        *,
        digest: str,
        expected_size: int | None = None,
    ) -> StaticEvidenceLoadedObject: ...


@dataclass(frozen=True, slots=True)
class BundleEvidence:
    """Minimal verified-bundle metadata; never carries blobs or secrets."""

    plugin_id: str
    version: str
    bundle_digest: str
    config_digest: str
    frontend_digest: str
    frontend_entry: str
    tar_member_count: int
    tar_total_bytes: int
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.plugin_id, str) or not self.plugin_id:
            raise ValueError("plugin_id must be a non-empty string")
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("version must be a non-empty string")
        if (
            not is_valid_digest(self.bundle_digest)
            or not is_valid_digest(self.config_digest)
            or not is_valid_digest(self.frontend_digest)
        ):
            raise ValueError("digests must match sha256:<64 lowercase hex>")
        if not isinstance(self.frontend_entry, str) or not self.frontend_entry:
            raise ValueError("frontend_entry must be a non-empty string")
        for name in ("tar_member_count", "tar_total_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.source not in ('quarantine', 'release'):
            raise ValueError("source must be 'quarantine' or 'release'")


def _strict_json(data: bytes) -> Any:
    """Strict JSON: UTF-8, no duplicate keys, no non-finite constants."""

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
        raise StaticEvidenceError(
            StaticEvidenceErrorCode.BUNDLE_NOT_READY,
            "registry object is not strict JSON",
        ) from exc


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """The canonical byte representation CI signs (sort_keys, tight separators)."""
    try:
        return (
            json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StaticEvidenceError(
            StaticEvidenceErrorCode.BUNDLE_NOT_READY,
            "canonical manifest is not strict JSON",
        ) from exc


def _reject_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        if _FORBIDDEN_KEYS & set(value):
            raise StaticEvidenceError(
                StaticEvidenceErrorCode.BUNDLE_NOT_READY,
                "self-reference or signature key is forbidden",
            )
        for nested in value.values():
            _reject_forbidden_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_forbidden_keys(nested)


def _not_ready(reason: str) -> StaticEvidenceError:
    return StaticEvidenceError(StaticEvidenceErrorCode.BUNDLE_NOT_READY, reason)


def _too_large(reason: str) -> StaticEvidenceError:
    return StaticEvidenceError(StaticEvidenceErrorCode.BUNDLE_TOO_LARGE, reason)


def _unavailable(reason: str) -> StaticEvidenceError:
    return StaticEvidenceError(StaticEvidenceErrorCode.EVIDENCE_UNAVAILABLE, reason)


def _is_loader_digest_mismatch(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    name = getattr(code, "name", None) or str(code)
    return name in _LOADER_DIGEST_MISMATCH_CODES


def _is_registry_absence(exc: BaseException) -> bool:
    """Only a typed not-found signal allows the release fallback.

    FileNotFoundError is the port-level absence signal: raised for a
    proven registry 404 (see the composition adapter in
    ``prodavan.main_plugin_static``) or by an injected test loader. Every
    other failure - transport outage, auth rejection, malformed loader
    error - fails closed with typed 503 and never masks the quarantine
    contour with a silent release read.
    """
    return isinstance(exc, FileNotFoundError)


def _relative_artifact_path(value: object) -> str | None:
    """Return the value when it is a safe relative artifact path, else None."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _FRONTEND_ENTRY_MAX_LENGTH
    ):
        return None
    if value.startswith("/") or "\\" in value or "\x00" in value:
        return None
    if any(character not in _SAFE_NAME_CHARACTERS for character in value):
        return None
    segments = value.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return None
    if any(len(segment) > _MEMBER_SEGMENT_MAX_LENGTH for segment in segments):
        return None
    return value


def _annotation_string(annotations: object, name: str) -> str:
    if not isinstance(annotations, Mapping):
        raise _not_ready("manifest annotations are missing")
    value = annotations.get(name)
    if not isinstance(value, str) or not value:
        raise _not_ready(f"manifest annotation {name} is missing")
    return value


@dataclass(frozen=True, slots=True)
class _ManifestContract:
    """Digest/size/entry facts extracted from one verified manifest structure."""

    config_digest: str
    config_size: int
    frontend_digest: str
    frontend_size: int
    # New CI bundles carry this annotation; canonical bundles produced before
    # the annotation existed are still trustworthy because the canonical
    # config is digest-verified and supplies the same entry below.
    frontend_entry: str | None


def _validate_manifest_contract(
    manifest: object,
    *,
    plugin_id: str,
    version: str,
    config: StaticEvidenceConfig,
) -> _ManifestContract:
    """Enforce the OCI evidence contract; every failure is a typed 404/413."""
    if not isinstance(manifest, Mapping):
        raise _not_ready("OCI manifest must be a JSON object")
    _reject_forbidden_keys(manifest)
    schema_version = manifest.get("schemaVersion")
    if isinstance(schema_version, bool) or schema_version != 2:
        raise _not_ready("unsupported OCI schemaVersion")
    if manifest.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE:
        raise _not_ready("unsupported OCI manifest media type")
    if manifest.get("artifactType") != _PLUGIN_ARTIFACT_TYPE:
        raise _not_ready("unsupported plugin artifact type")

    annotations = manifest.get("annotations")
    if not isinstance(annotations, Mapping):
        raise _not_ready("OCI manifest annotations are missing")
    if annotations.get(_ANNOTATION_PLUGIN_ID) != plugin_id:
        raise _not_ready("plugin_id annotation mismatch")
    if annotations.get(_ANNOTATION_VERSION) != version:
        raise _not_ready("version annotation mismatch")

    config_descriptor = manifest.get("config")
    if not isinstance(config_descriptor, Mapping):
        raise _not_ready("OCI config descriptor is missing")
    if config_descriptor.get("mediaType") != _PLUGIN_MANIFEST_MEDIA_TYPE:
        raise _not_ready("canonical manifest is not the OCI config")
    config_digest = config_descriptor.get("digest")
    config_size = config_descriptor.get("size")
    if not is_valid_digest(config_digest):
        raise _not_ready("config digest is invalid")
    if (
        isinstance(config_size, bool)
        or not isinstance(config_size, int)
        or config_size < 1
    ):
        raise _not_ready("config size is invalid")
    if config_size > config.max_config_bytes:
        raise _too_large("canonical manifest exceeds the config size cap")
    if annotations.get(_MANIFEST_DIGEST_ANNOTATION) != config_digest:
        raise _not_ready("canonical manifest digest annotation mismatch")

    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise _not_ready("OCI manifest has no layers")
    seen_roles: set[str] = set()
    frontend: Mapping[str, Any] | None = None
    for layer in layers:
        if not isinstance(layer, Mapping):
            raise _not_ready("OCI layer descriptor is malformed")
        digest = layer.get("digest")
        size = layer.get("size")
        if not is_valid_digest(digest):
            raise _not_ready("OCI layer digest is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise _not_ready("OCI layer size is invalid")
        role = _annotation_string(
            layer.get("annotations"), _LAYER_ROLE_ANNOTATION
        )
        if role in seen_roles:
            raise _not_ready("duplicate OCI layer role")
        seen_roles.add(role)
        if role == _FRONTEND_LAYER_ROLE:
            if frontend is not None:
                raise _not_ready("more than one frontend-dist layer")
            if layer.get("mediaType") != FRONTEND_MEDIA_TYPE:
                raise _not_ready("frontend layer media type mismatch")
            if size > config.max_frontend_layer_bytes:
                raise _too_large("frontend layer exceeds the size cap")
            frontend = layer
    if frontend is None:
        raise _not_ready("exactly one frontend-dist layer is required")
    frontend_annotations = frontend.get("annotations")
    frontend_entry = None
    if isinstance(frontend_annotations, Mapping) and frontend_annotations.get(
        _FRONTEND_ENTRY_ANNOTATION
    ) is not None:
        frontend_entry = _annotation_string(
            frontend_annotations, _FRONTEND_ENTRY_ANNOTATION
        )
        if _relative_artifact_path(frontend_entry) is None:
            raise _not_ready("frontend entry annotation is not a safe relative path")
    return _ManifestContract(
        config_digest=config_digest,
        config_size=config_size,
        frontend_digest=frontend["digest"],
        frontend_size=frontend["size"],
        frontend_entry=frontend_entry,
    )


def _validate_canonical_config(
    canonical: object,
    raw_bytes: bytes,
    *,
    plugin_id: str,
    version: str,
    contract: _ManifestContract,
) -> str:
    """Verify canonical bytes, identity binding and the frontend entry."""
    if not isinstance(canonical, dict):
        raise _not_ready("canonical config must be a JSON object")
    if _canonical_json_bytes(canonical) != raw_bytes:
        raise _not_ready("canonical config bytes are not canonical")
    if canonical.get("plugin_id") != plugin_id:
        raise _not_ready("canonical plugin_id mismatch")
    if canonical.get("version") != version:
        raise _not_ready("canonical version mismatch")
    frontend = canonical.get("frontend")
    if not isinstance(frontend, Mapping):
        raise _not_ready("canonical frontend section is missing")
    entry = frontend.get("entry")
    if _relative_artifact_path(entry) is None:
        raise _not_ready("canonical frontend.entry is not a safe relative path")
    if contract.frontend_entry is not None and entry != contract.frontend_entry:
        raise _not_ready("frontend entry annotation and canonical config disagree")
    return entry


def _iter_tar_members(data: bytes) -> Iterator[Any]:
    """Iterate tar.gz members without ever extracting payloads."""
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise _not_ready("frontend tar archive is unreadable") from exc
    with archive:
        try:
            while True:
                member = archive.next()
                if member is None:
                    return
                yield member
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise _not_ready(
                "frontend tar archive is truncated or corrupt"
            ) from exc


def _validate_frontend_tar(
    data: bytes,
    *,
    entry: str,
    config: StaticEvidenceConfig,
) -> tuple[int, int]:
    """Scan the frontend tar.gz: regular files only, strict names, caps."""
    seen: set[str] = set()
    total = 0
    count = 0
    for member in _iter_tar_members(data):
        count += 1
        if count > config.max_tar_members:
            raise _too_large("frontend tar member count cap exceeded")
        if not member.isreg():
            raise _not_ready("frontend tar must contain regular files only")
        name = member.name
        if _relative_artifact_path(name) is None:
            raise _not_ready(
                "frontend tar member name is not a safe relative path"
            )
        if name in seen:
            raise _not_ready("duplicate frontend tar member")
        seen.add(name)
        size = member.size
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise _not_ready("frontend tar member size is invalid")
        if size > config.max_tar_member_bytes:
            raise _too_large("frontend tar member exceeds the size cap")
        total += size
        if total > config.max_tar_total_bytes:
            raise _too_large("frontend tar total size cap exceeded")
    if entry not in seen:
        raise _not_ready("frontend entry is missing from the tar archive")
    return count, total


class PluginStaticEvidenceService:
    """Verify one plugin bundle digest and cache the evidence result.

    The artifact loader is borrowed and injectable; the service never performs
    network I/O itself and never persists anything outside process memory.
    """

    def __init__(
        self,
        *,
        artifact_loader: StaticEvidenceArtifactLoader,
        config: StaticEvidenceConfig,
        clock: Callable[[], float] | None = None,
    ) -> None:
        for method in ("load_manifest", "load_blob"):
            if not callable(getattr(artifact_loader, method, None)):
                raise StaticEvidenceConfigError(
                    f"artifact_loader must implement {method}()"
                )
        if not isinstance(config, StaticEvidenceConfig):
            raise StaticEvidenceConfigError("config must be StaticEvidenceConfig")
        self._loader = artifact_loader
        self._config = config
        self._clock = clock or time.monotonic
        self._success: OrderedDict[
            tuple[str, str, str], tuple[float, BundleEvidence]
        ] = OrderedDict()
        self._negative: OrderedDict[
            tuple[str, str, str], tuple[float, StaticEvidenceErrorCode]
        ] = OrderedDict()
        self._inflight: dict[
            tuple[str, str, str], asyncio.Future[tuple[str, Any]]
        ] = {}

    async def verify_bundle(
        self,
        *,
        plugin_id: str,
        version: str,
        bundle_digest: str,
    ) -> BundleEvidence:
        """Return verified evidence or raise a typed sanitized error."""
        self._validate_identity(
            plugin_id=plugin_id, version=version, bundle_digest=bundle_digest
        )
        key = (plugin_id, version, bundle_digest)
        now = self._clock()

        success = self._success.get(key)
        if success is not None and success[0] > now:
            self._success.move_to_end(key)
            return success[1]
        if success is not None:
            del self._success[key]
        negative = self._negative.get(key)
        if negative is not None and negative[0] > now:
            self._negative.move_to_end(key)
            raise StaticEvidenceError(
                negative[1], "cached negative evidence result"
            )
        if negative is not None:
            del self._negative[key]

        inflight = self._inflight.get(key)
        if inflight is not None:
            status, payload = await inflight
            if status == "ok":
                return payload
            raise payload

        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[str, Any]] = loop.create_future()
        self._inflight[key] = future
        try:
            evidence = await self._verify_bundle_uncached(
                plugin_id=plugin_id, version=version, bundle_digest=bundle_digest
            )
            self._success[key] = (now + self._config.success_cache_seconds, evidence)
            self._trim(self._success, self._config.success_cache_size)
            future.set_result(("ok", evidence))
            return evidence
        except StaticEvidenceError as exc:
            if exc.code is not StaticEvidenceErrorCode.EVIDENCE_UNAVAILABLE:
                self._negative[key] = (
                    now + self._config.negative_cache_seconds,
                    exc.code,
                )
                self._trim(self._negative, self._config.negative_cache_size)
            future.set_result(("error", exc))
            raise
        except BaseException:
            future.set_result(("error", _unavailable("verification crashed")))
            raise
        finally:
            self._inflight.pop(key, None)

    def _trim(self, cache: OrderedDict[Any, Any], limit: int) -> None:
        while len(cache) > limit:
            cache.popitem(last=False)

    def _validate_identity(
        self, *, plugin_id: str, version: str, bundle_digest: str
    ) -> None:
        config = self._config
        if (
            not isinstance(plugin_id, str)
            or len(plugin_id) > config.plugin_id_max_length
            or PLUGIN_ID_PATTERN.fullmatch(plugin_id) is None
        ):
            raise _not_ready("plugin_id has invalid canonical grammar")
        if (
            not isinstance(version, str)
            or len(version) > config.version_max_length
            or SEMVER_PATTERN.fullmatch(version) is None
        ):
            raise _not_ready("version must be SemVer 2.0.0")
        if not is_valid_digest(bundle_digest):
            raise _not_ready("bundle_digest must be sha256:<64 lowercase hex>")

    async def _verify_bundle_uncached(
        self, *, plugin_id: str, version: str, bundle_digest: str
    ) -> BundleEvidence:
        quarantine_repository = (
            f"{self._config.quarantine_repository_prefix}/{plugin_id}/"
            f"{_BUNDLE_REPOSITORY_LEAF}"
        )
        release_repository = (
            f"{self._config.release_repository_prefix}/{plugin_id}/"
            f"{_BUNDLE_REPOSITORY_LEAF}"
        )
        try:
            manifest_object = await self._loader.load_manifest(
                f"{quarantine_repository}@{bundle_digest}"
            )
            repository = quarantine_repository
            source = "quarantine"
        except StaticEvidenceError:
            raise
        except Exception as quarantine_error:
            if _is_loader_digest_mismatch(quarantine_error):
                # The pinned digest holds different bytes in quarantine: a
                # verified negative, never masked by a release read.
                raise _not_ready(
                    "registry content digest mismatch"
                ) from None
            if not _is_registry_absence(quarantine_error):
                # Quarantine outage/auth failure: fail closed with typed 503
                # instead of silently answering from release.
                raise _unavailable("bundle registry read failed") from None
            # Explicit quarantine absence: the bundle may already be
            # promoted, so probe the release repository exactly once.
            try:
                manifest_object = await self._loader.load_manifest(
                    f"{release_repository}@{bundle_digest}"
                )
                repository = release_repository
                source = "release"
            except StaticEvidenceError:
                raise
            except FileNotFoundError as release_error:
                # Explicit absence in both contours: a verified negative.
                raise _not_ready(
                    "bundle absent from quarantine and release"
                ) from release_error
            except Exception as release_error:
                if _is_loader_digest_mismatch(release_error):
                    raise _not_ready(
                        "registry content digest mismatch"
                    ) from None
                raise _unavailable("bundle registry read failed") from None
        ref = f"{repository}@{bundle_digest}"
        return await self._verify_manifest_chain(
            ref,
            manifest_object=manifest_object,
            plugin_id=plugin_id,
            version=version,
            bundle_digest=bundle_digest,
            source=source,
        )

    async def _verify_manifest_chain(
        self,
        ref: str,
        *,
        manifest_object: StaticEvidenceLoadedObject,
        plugin_id: str,
        version: str,
        bundle_digest: str,
        source: str,
    ) -> BundleEvidence:
        config = self._config
        manifest_bytes = manifest_object.data
        if not isinstance(manifest_bytes, (bytes, bytearray)):
            raise _not_ready("loader returned a non-bytes manifest")
        if len(manifest_bytes) > config.max_manifest_bytes:
            raise _too_large("OCI manifest exceeds the size cap")
        manifest = _strict_json(manifest_bytes)
        contract = _validate_manifest_contract(
            manifest, plugin_id=plugin_id, version=version, config=config
        )

        config_object = await self._load_blob(
            ref,
            digest=contract.config_digest,
            expected_size=contract.config_size,
            cap=config.max_config_bytes,
            cap_reason="canonical manifest exceeds the config size cap",
        )
        canonical = _strict_json(config_object.data)
        frontend_entry = _validate_canonical_config(
            canonical,
            bytes(config_object.data),
            plugin_id=plugin_id,
            version=version,
            contract=contract,
        )

        frontend_object = await self._load_blob(
            ref,
            digest=contract.frontend_digest,
            expected_size=contract.frontend_size,
            cap=config.max_frontend_layer_bytes,
            cap_reason="frontend layer exceeds the size cap",
        )
        member_count, total_bytes = _validate_frontend_tar(
            bytes(frontend_object.data),
            entry=frontend_entry,
            config=config,
        )
        return BundleEvidence(
            plugin_id=plugin_id,
            version=version,
            bundle_digest=bundle_digest,
            config_digest=contract.config_digest,
            frontend_digest=contract.frontend_digest,
            frontend_entry=frontend_entry,
            tar_member_count=member_count,
            tar_total_bytes=total_bytes,
            source=source,
        )

    async def _load_blob(
        self,
        ref: str,
        *,
        digest: str,
        expected_size: int,
        cap: int,
        cap_reason: str,
    ) -> StaticEvidenceLoadedObject:
        try:
            blob = await self._loader.load_blob(
                ref, digest=digest, expected_size=expected_size
            )
        except StaticEvidenceError:
            raise
        except FileNotFoundError as exc:
            # The manifest exists but its blob does not: an incomplete
            # bundle is a verified negative, not a registry outage.
            raise _not_ready(
                "bundle blob is absent from its repository"
            ) from exc
        except Exception as exc:
            if _is_loader_digest_mismatch(exc):
                raise _not_ready("registry content digest mismatch") from None
            raise _unavailable("bundle registry read failed") from None
        data = blob.data
        if not isinstance(data, (bytes, bytearray)):
            raise _not_ready("loader returned a non-bytes blob")
        if len(data) > cap:
            raise _too_large(cap_reason)
        return blob


__all__ = [
    "BundleEvidence",
    "PluginStaticEvidenceService",
    "StaticEvidenceArtifactLoader",
    "StaticEvidenceConfig",
    "StaticEvidenceConfigError",
    "StaticEvidenceError",
    "StaticEvidenceErrorCode",
]
