"""Offline byte-level contract tests for plugin static evidence.

No registry, network, tenant database or object storage is used. The fake
loader returns exact OCI manifest, canonical config and frontend tar.gz bytes.
The ASGI suite reuses these fixtures so headers alone cannot prove readiness.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import tarfile
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from prodavan.application.plugins.artifacts.publication import (
    LoadedOciObject,
    PublicationErrorCode,
    _LoaderError,
)
from prodavan.application.plugins.static_origin.service import (
    PluginStaticEvidenceService,
    StaticEvidenceConfig,
    StaticEvidenceError,
    StaticEvidenceErrorCode,
)

PLUGIN_ID = "hello_plugin"
VERSION = "1.2.0"
INDEX_HTML = b"<!doctype html><title>verified plugin</title>"
APP_JS = b"export const verified = true;\n"
SHELL_ORIGIN = "https://agentscale.example.test"
QUARANTINE_PREFIX = "ghcr.io/ne-tort/plugins-quarantine"
RELEASE_PREFIX = "ghcr.io/ne-tort/plugins"
QUARANTINE_REPOSITORY = QUARANTINE_PREFIX + "/hello_plugin/bundle"
RELEASE_REPOSITORY = RELEASE_PREFIX + "/hello_plugin/bundle"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
PLUGIN_ARTIFACT_TYPE = "application/vnd.agentscale.plugin.oci"
PLUGIN_MANIFEST_MEDIA_TYPE = "application/vnd.agentscale.plugin.manifest.v1+json"
FRONTEND_MEDIA_TYPE = "application/vnd.agentscale.plugin.frontend"
MIGRATIONS_MEDIA_TYPE = "application/vnd.agentscale.plugin.liquibase+xml"
LAYER_ROLE = "com.agentscale.plugin.layer.role"
FRONTEND_ENTRY_ANNOTATION = "agentscale.plugin.frontend.entry"


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    name: str
    data: bytes = b""
    kind: str = "file"
    linkname: str = ""


def frontend_archive(members: Iterable[ArchiveMember]) -> bytes:
    """Build deterministic in-memory tar.gz bytes, including hostile cases."""

    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for member in members:
                info = tarfile.TarInfo(member.name)
                info.mtime = 0
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if member.kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = member.linkname
                    info.size = 0
                    archive.addfile(info)
                elif member.kind == "directory":
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.size = 0
                    archive.addfile(info)
                else:
                    info.size = len(member.data)
                    archive.addfile(info, io.BytesIO(member.data))
    return output.getvalue()


@dataclass(frozen=True, slots=True)
class Bundle:
    manifest: bytes
    config: bytes
    frontend: bytes
    migrations: bytes

    @property
    def digest(self) -> str:
        return sha256(self.manifest)

    @property
    def config_digest(self) -> str:
        return sha256(self.config)

    @property
    def frontend_digest(self) -> str:
        return sha256(self.frontend)

    def ref(self, repository: str = QUARANTINE_REPOSITORY) -> str:
        return f"{repository}@{self.digest}"


def valid_config(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "plugin_id": PLUGIN_ID,
        "version": VERSION,
        "protocol": "plugin-rpc/1",
        "frontend": {"entry": "index.html", "base_path": "/"},
        "backend": {"port": 8080, "health": "/health"},
        "capabilities": [],
        "migrations": {
            "engine": "liquibase",
            "path": "migrations/changelog.xml",
        },
        "egress_ref": "grant:hello_plugin",
        "public_pages": [],
        "webhooks": [],
        "min_platform_version": "4.0.0",
    }
    value.update(overrides)
    return value


def frontend_layer(frontend: bytes) -> dict[str, Any]:
    return {
        "mediaType": FRONTEND_MEDIA_TYPE,
        "digest": sha256(frontend),
        "size": len(frontend),
        "annotations": {
            LAYER_ROLE: "frontend-dist",
            FRONTEND_ENTRY_ANNOTATION: "index.html",
            "org.opencontainers.image.title": "frontend-dist.tar.gz",
        },
    }


def make_bundle(
    *,
    config: Mapping[str, Any] | None = None,
    config_bytes: bytes | None = None,
    frontend: bytes | None = None,
    members: Iterable[ArchiveMember] | None = None,
    manifest_mutator: Callable[[dict[str, Any]], None] | None = None,
    manifest_bytes: bytes | None = None,
) -> Bundle:
    """Create consistent exact bytes unless a test explicitly breaks them."""

    canonical = json_bytes(config or valid_config()) if config_bytes is None else config_bytes
    frontend_bytes = (
        frontend_archive(
            members
            if members is not None
            else (
                ArchiveMember("index.html", INDEX_HTML),
                ArchiveMember("assets/app.js", APP_JS),
            )
        )
        if frontend is None
        else frontend
    )
    migrations = b"not-served-by-the-static-origin"
    parsed_config = json.loads(canonical)
    frontend_entry = parsed_config.get("frontend", {}).get("entry")
    selected_frontend_layer = frontend_layer(frontend_bytes)
    selected_frontend_layer["annotations"][FRONTEND_ENTRY_ANNOTATION] = frontend_entry
    manifest: dict[str, Any] = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": PLUGIN_ARTIFACT_TYPE,
        "config": {
            "mediaType": PLUGIN_MANIFEST_MEDIA_TYPE,
            "digest": sha256(canonical),
            "size": len(canonical),
        },
        "layers": [
            {
                "mediaType": PLUGIN_MANIFEST_MEDIA_TYPE,
                "digest": sha256(canonical),
                "size": len(canonical),
                "annotations": {
                    LAYER_ROLE: "canonical-manifest",
                    "org.opencontainers.image.title": "canonical-manifest.json",
                },
            },
            selected_frontend_layer,
            {
                "mediaType": MIGRATIONS_MEDIA_TYPE,
                "digest": sha256(migrations),
                "size": len(migrations),
                "annotations": {
                    LAYER_ROLE: "migrations",
                    "org.opencontainers.image.title": "migrations.tar.gz",
                },
            },
        ],
        "annotations": {
            "plugin_id": parsed_config.get("plugin_id"),
            "manifest_version": parsed_config.get("version"),
            "protocol": "plugin-rpc/1",
            "com.agentscale.plugin.manifest_digest": sha256(canonical),
        },
    }
    if manifest_mutator is not None:
        manifest_mutator(manifest)
    return Bundle(
        manifest=json_bytes(manifest) if manifest_bytes is None else manifest_bytes,
        config=canonical,
        frontend=frontend_bytes,
        migrations=migrations,
    )


class RegistryNotFound(FileNotFoundError):
    """Explicit registry absence, the only reason release fallback is allowed."""


class FakeLoaderFailure(RuntimeError):
    """A non-not-found loader failure; it must never trigger release fallback."""


class ExactByteLoader:
    """ArtifactLoaderPort fake with real digest checks and call recording."""

    def __init__(self) -> None:
        self.manifests: dict[str, bytes] = {}
        self.blobs: dict[str, dict[str, bytes]] = {}
        self.calls: list[tuple[str, str, str | None]] = []
        self.manifest_errors: dict[str, list[Exception]] = {}
        self.blob_errors: dict[tuple[str, str], list[Exception]] = {}
        self.first_manifest_started = asyncio.Event()
        self.release_first_manifest: asyncio.Event | None = None

    def add(self, bundle: Bundle, repository: str = QUARANTINE_REPOSITORY) -> None:
        self.manifests[bundle.ref(repository)] = bundle.manifest
        blobs = self.blobs.setdefault(repository, {})
        blobs[bundle.config_digest] = bundle.config
        blobs[bundle.frontend_digest] = bundle.frontend
        blobs[sha256(bundle.migrations)] = bundle.migrations

    @staticmethod
    def _image_ref(ref: object) -> str:
        candidate = getattr(ref, "image_ref", None)
        return candidate() if callable(candidate) else str(ref)

    @staticmethod
    def _repository(ref: str) -> str:
        return ref.rsplit("@", 1)[0]

    async def load_manifest(self, ref: object) -> LoadedOciObject:
        image_ref = self._image_ref(ref)
        self.calls.append(("manifest", image_ref, None))
        if self.release_first_manifest is not None:
            self.first_manifest_started.set()
            gate = self.release_first_manifest
            self.release_first_manifest = None
            await gate.wait()
        queued = self.manifest_errors.get(image_ref)
        if queued:
            raise queued.pop(0)
        data = self.manifests.get(image_ref)
        if data is None:
            raise RegistryNotFound(image_ref)
        expected = image_ref.rsplit("@", 1)[1]
        actual = sha256(data)
        if actual != expected:
            raise _LoaderError(
                "manifest digest mismatch",
                PublicationErrorCode.OCI_MANIFEST_MISMATCH,
            )
        return LoadedOciObject(data=data, digest=actual, size=len(data))

    async def load_blob(
        self,
        ref: object,
        *,
        digest: str,
        expected_size: int | None = None,
    ) -> LoadedOciObject:
        image_ref = self._image_ref(ref)
        repository = self._repository(image_ref)
        self.calls.append(("blob", image_ref, digest))
        queued = self.blob_errors.get((repository, digest))
        if queued:
            raise queued.pop(0)
        data = self.blobs.get(repository, {}).get(digest)
        if data is None:
            raise RegistryNotFound(f"{repository}/blobs/{digest}")
        actual = sha256(data)
        if actual != digest:
            raise _LoaderError(
                "blob digest mismatch",
                PublicationErrorCode.CANONICAL_MANIFEST_INVALID,
            )
        if expected_size is not None and len(data) != expected_size:
            raise _LoaderError(
                "blob size mismatch",
                PublicationErrorCode.CANONICAL_MANIFEST_INVALID,
            )
        return LoadedOciObject(data=data, digest=actual, size=len(data))

    def count(self, operation: str, repository: str | None = None) -> int:
        return sum(
            1
            for kind, ref, _ in self.calls
            if kind == operation
            and (repository is None or self._repository(ref) == repository)
        )


def make_service(
    loader: ExactByteLoader,
    *,
    clock: Callable[[], float] | None = None,
    **overrides: Any,
) -> PluginStaticEvidenceService:
    values: dict[str, Any] = {
        "release_repository_prefix": RELEASE_PREFIX,
        "quarantine_repository_prefix": QUARANTINE_PREFIX,
    }
    values.update(overrides)
    return PluginStaticEvidenceService(
        artifact_loader=loader,
        config=StaticEvidenceConfig(**values),
        clock=clock,
    )


async def verify(
    service: PluginStaticEvidenceService,
    bundle: Bundle,
    *,
    plugin_id: str = PLUGIN_ID,
    version: str = VERSION,
    digest: str | None = None,
) -> Any:
    return await service.verify_bundle(
        plugin_id=plugin_id,
        version=version,
        bundle_digest=bundle.digest if digest is None else digest,
    )


async def assert_rejected_without_success_cache(
    service: PluginStaticEvidenceService,
    bundle: Bundle,
    expected: StaticEvidenceErrorCode,
    *,
    digest: str | None = None,
) -> None:
    for _ in range(2):
        with pytest.raises(StaticEvidenceError) as caught:
            await verify(service, bundle, digest=digest)
        assert caught.value.code is expected
        assert str(caught.value) == expected.value
    # A negative cache is allowed, a verified-ready cache entry is not.
    assert not service._success


@pytest.mark.asyncio
async def test_valid_quarantine_fetches_exact_manifest_config_and_frontend() -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle)
    service = make_service(loader)

    evidence = await verify(service, bundle)

    assert evidence.plugin_id == PLUGIN_ID
    assert evidence.version == VERSION
    assert evidence.bundle_digest == bundle.digest
    assert evidence.config_digest == bundle.config_digest
    assert evidence.frontend_digest == bundle.frontend_digest
    assert evidence.frontend_entry == "index.html"
    assert evidence.source == "quarantine"
    assert evidence.tar_member_count == 2
    assert evidence.tar_total_bytes == len(INDEX_HTML) + len(APP_JS)
    assert loader.calls == [
        ("manifest", bundle.ref(QUARANTINE_REPOSITORY), None),
        ("blob", bundle.ref(QUARANTINE_REPOSITORY), bundle.config_digest),
        ("blob", bundle.ref(QUARANTINE_REPOSITORY), bundle.frontend_digest),
    ]
    assert loader.blobs[QUARANTINE_REPOSITORY][bundle.config_digest] == bundle.config
    assert loader.blobs[QUARANTINE_REPOSITORY][bundle.frontend_digest] == bundle.frontend
    assert sha256(bundle.migrations) not in {call[2] for call in loader.calls}


@pytest.mark.asyncio
async def test_release_fallback_only_for_quarantine_not_found() -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle, RELEASE_REPOSITORY)
    service = make_service(loader)

    evidence = await verify(service, bundle)

    assert evidence.source == "release"
    assert loader.count("manifest", QUARANTINE_REPOSITORY) == 1
    assert loader.count("manifest", RELEASE_REPOSITORY) == 1
    assert loader.count("blob", RELEASE_REPOSITORY) == 2


@pytest.mark.asyncio
async def test_quarantine_outage_never_falls_back_to_release_and_is_sanitized() -> None:
    secret = "ghp_secret_token_postgresql://user:password@tenant-db/private"
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle, RELEASE_REPOSITORY)
    loader.manifest_errors[bundle.ref(QUARANTINE_REPOSITORY)] = [
        FakeLoaderFailure(secret)
    ]
    service = make_service(loader)

    with pytest.raises(StaticEvidenceError) as caught:
        await verify(service, bundle)

    assert caught.value.code is StaticEvidenceErrorCode.EVIDENCE_UNAVAILABLE
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in caught.value.reason
    assert loader.count("manifest", QUARANTINE_REPOSITORY) == 1
    assert loader.count("manifest", RELEASE_REPOSITORY) == 0
    assert loader.count("blob") == 0


@pytest.mark.asyncio
async def test_manifest_digest_mismatch_is_not_release_fallback() -> None:
    bundle = make_bundle()
    requested = sha256(b"caller-selected-different-manifest")
    loader = ExactByteLoader()
    loader.manifests[f"{QUARANTINE_REPOSITORY}@{requested}"] = bundle.manifest
    loader.add(bundle, RELEASE_REPOSITORY)
    service = make_service(loader)

    await assert_rejected_without_success_cache(
        service,
        bundle,
        StaticEvidenceErrorCode.BUNDLE_NOT_READY,
        digest=requested,
    )
    assert loader.count("manifest", RELEASE_REPOSITORY) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "wrong"),
    (("plugin_id", "other_plugin"), ("manifest_version", "2.0.0")),
)
async def test_wrong_oci_annotation_identity_fails_closed(field: str, wrong: str) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest["annotations"][field] = wrong

    bundle = make_bundle(manifest_mutator=mutate)
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "wrong"),
    (("plugin_id", "other_plugin"), ("version", "2.0.0")),
)
async def test_wrong_canonical_config_identity_fails_closed(field: str, wrong: str) -> None:
    config = valid_config(**{field: wrong})
    annotation = "plugin_id" if field == "plugin_id" else "manifest_version"

    def keep_requested_annotation(manifest: dict[str, Any]) -> None:
        manifest["annotations"][annotation] = (
            PLUGIN_ID if field == "plugin_id" else VERSION
        )

    bundle = make_bundle(config=config, manifest_mutator=keep_requested_annotation)
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
async def test_noncanonical_config_bytes_fail_closed() -> None:
    raw = json.dumps(valid_config(), indent=2, ensure_ascii=False).encode("utf-8")
    bundle = make_bundle(config_bytes=raw)
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("descriptor", ("config", "frontend"))
async def test_wrong_blob_digest_fails_closed_without_success_cache(descriptor: str) -> None:
    wrong_digest = sha256(b"wrong-descriptor-digest")

    def mutate(manifest: dict[str, Any]) -> None:
        if descriptor == "config":
            manifest["config"]["digest"] = wrong_digest
            manifest["annotations"]["com.agentscale.plugin.manifest_digest"] = wrong_digest
        else:
            layer = next(
                item
                for item in manifest["layers"]
                if item["annotations"][LAYER_ROLE] == "frontend-dist"
            )
            layer["digest"] = wrong_digest

    bundle = make_bundle(manifest_mutator=mutate)
    loader = ExactByteLoader()
    loader.add(bundle)
    original = bundle.config if descriptor == "config" else bundle.frontend
    loader.blobs[QUARANTINE_REPOSITORY][wrong_digest] = original
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("descriptor", ("config", "frontend"))
async def test_wrong_blob_size_fails_closed_without_success_cache(descriptor: str) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        target = manifest["config"]
        if descriptor == "frontend":
            target = next(
                item
                for item in manifest["layers"]
                if item["annotations"][LAYER_ROLE] == "frontend-dist"
            )
        target["size"] += 1

    bundle = make_bundle(manifest_mutator=mutate)
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


def remove_frontend(manifest: dict[str, Any]) -> None:
    manifest["layers"] = [
        layer
        for layer in manifest["layers"]
        if layer["annotations"][LAYER_ROLE] != "frontend-dist"
    ]


def duplicate_frontend(manifest: dict[str, Any]) -> None:
    layer = next(
        item
        for item in manifest["layers"]
        if item["annotations"][LAYER_ROLE] == "frontend-dist"
    )
    manifest["layers"].append(deepcopy(layer))


def wrong_frontend_media_type(manifest: dict[str, Any]) -> None:
    layer = next(
        item
        for item in manifest["layers"]
        if item["annotations"][LAYER_ROLE] == "frontend-dist"
    )
    layer["mediaType"] = "application/octet-stream"


def wrong_config_media_type(manifest: dict[str, Any]) -> None:
    manifest["config"]["mediaType"] = "application/json"


def wrong_oci_media_type(manifest: dict[str, Any]) -> None:
    manifest["mediaType"] = "application/vnd.docker.distribution.manifest.v2+json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutator",
    (
        remove_frontend,
        duplicate_frontend,
        wrong_frontend_media_type,
        wrong_config_media_type,
        wrong_oci_media_type,
    ),
)
async def test_wrong_layer_or_media_type_fails_closed(
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    bundle = make_bundle(manifest_mutator=mutator)
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostile_name",
    ("../secret", "/etc/passwd", "assets/../../secret", "assets\\..\\secret"),
)
async def test_tar_traversal_fails_closed(hostile_name: str) -> None:
    bundle = make_bundle(
        members=(
            ArchiveMember("index.html", INDEX_HTML),
            ArchiveMember(hostile_name, b"must-not-be-served"),
        )
    )
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
async def test_tar_symlink_fails_closed() -> None:
    bundle = make_bundle(
        members=(
            ArchiveMember("index.html", INDEX_HTML),
            ArchiveMember("assets/linked.js", kind="symlink", linkname="../../secret"),
        )
    )
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
async def test_tar_duplicate_member_fails_closed() -> None:
    bundle = make_bundle(
        members=(
            ArchiveMember("index.html", INDEX_HTML),
            ArchiveMember("index.html", b"different-duplicate"),
        )
    )
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
async def test_tar_missing_entry_fails_closed() -> None:
    bundle = make_bundle(members=(ArchiveMember("assets/app.js", APP_JS),))
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader), bundle, StaticEvidenceErrorCode.BUNDLE_NOT_READY
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    (
        {"max_config_bytes": 1},
        {"max_frontend_layer_bytes": 1},
        {"max_tar_members": 1},
        {"max_tar_member_bytes": len(INDEX_HTML) - 1},
        {"max_tar_total_bytes": len(INDEX_HTML) + len(APP_JS) - 1},
    ),
)
async def test_oversize_fails_closed_without_success_cache(overrides: dict[str, int]) -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle)
    await assert_rejected_without_success_cache(
        make_service(loader, **overrides),
        bundle,
        StaticEvidenceErrorCode.BUNDLE_TOO_LARGE,
    )


@pytest.mark.asyncio
async def test_repeated_digest_is_singleflight_and_success_cached() -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle)
    gate = asyncio.Event()
    loader.release_first_manifest = gate
    service = make_service(loader)

    tasks = [asyncio.create_task(verify(service, bundle)) for _ in range(8)]
    await asyncio.wait_for(loader.first_manifest_started.wait(), timeout=1)
    for _ in range(5):
        await asyncio.sleep(0)
    assert loader.count("manifest") == 1
    gate.set()
    results = await asyncio.gather(*tasks)
    again = await verify(service, bundle)

    assert all(item is results[0] for item in results)
    assert again is results[0]
    assert loader.count("manifest") == 1
    assert loader.count("blob") == 2


@pytest.mark.asyncio
async def test_independent_digests_have_independent_fetch_and_cache_entries() -> None:
    first = make_bundle()
    second = make_bundle(config=valid_config(capabilities=["cache"]))
    loader = ExactByteLoader()
    loader.add(first)
    loader.add(second)
    service = make_service(loader)
    assert first.digest != second.digest

    first_result, second_result = await asyncio.gather(
        verify(service, first), verify(service, second)
    )
    assert (await verify(service, first)) is first_result
    assert (await verify(service, second)) is second_result
    assert first_result.bundle_digest == first.digest
    assert second_result.bundle_digest == second.digest
    assert first_result.config_digest != second_result.config_digest
    assert loader.count("manifest") == 2
    assert loader.count("blob") == 4
    assert loader.count("manifest", QUARANTINE_REPOSITORY) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plugin_id", "version", "digest"),
    (
        ("../hello_plugin", VERSION, sha256(b"valid-shape")),
        (PLUGIN_ID, "latest", sha256(b"valid-shape")),
        (PLUGIN_ID, VERSION, "sha256:not-a-digest"),
    ),
)
async def test_invalid_request_identity_fails_before_loader(
    plugin_id: str, version: str, digest: str
) -> None:
    loader = ExactByteLoader()
    service = make_service(loader)
    with pytest.raises(StaticEvidenceError) as caught:
        await service.verify_bundle(
            plugin_id=plugin_id, version=version, bundle_digest=digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY
    assert loader.calls == []
