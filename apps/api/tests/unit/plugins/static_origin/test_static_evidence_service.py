"""Offline focused tests for the static evidence service (no network)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from types import SimpleNamespace
from typing import Any

import pytest
from prodavan.application.plugins.artifacts.oci import (
    FRONTEND_MEDIA_TYPE,
    MIGRATIONS_MEDIA_TYPE,
)
from prodavan.application.plugins.static_origin import (
    BundleEvidence,
    PluginStaticEvidenceService,
    StaticEvidenceConfig,
    StaticEvidenceConfigError,
    StaticEvidenceError,
    StaticEvidenceErrorCode,
)

PLUGIN_ID = "demo_plugin"
VERSION = "1.2.3"
ENTRY = "index.html"
RELEASE_PREFIX = "ghcr.io/ne-tort/plugins"
QUARANTINE_PREFIX = "ghcr.io/ne-tort/plugins-quarantine"


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")


def _build_tar(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class _Bundle:
    def __init__(self, manifest: dict[str, Any], blobs: dict[str, bytes]) -> None:
        self.manifest = manifest
        self.blobs = blobs
        self.manifest_bytes = _canonical_bytes(manifest)
        self.bundle_digest = _sha256(self.manifest_bytes)


def _build_bundle(
    *,
    plugin_id: str = PLUGIN_ID,
    version: str = VERSION,
    entry: str = ENTRY,
    tar_members: list[tuple[str, bytes]] | None = None,
    frontend_tar: bytes | None = None,
    canonical_override: bytes | None = None,
    annotations: dict[str, str] | None = None,
    frontend_layer_count: int = 1,
) -> _Bundle:
    canonical = {
        "plugin_id": plugin_id,
        "version": version,
        "protocol": "plugin-rpc/1",
        "capabilities": ["cache"],
        "min_platform_version": "4.0.0",
        "frontend": {"entry": entry, "base_path": "/"},
    }
    canonical_bytes = (
        canonical_override
        if canonical_override is not None
        else _canonical_bytes(canonical)
    )
    frontend_bytes = (
        frontend_tar
        if frontend_tar is not None
        else _build_tar(
            tar_members or [(ENTRY, b"<html></html>"), ("assets/app.js", b"js")],
        )
    )
    migrations_bytes = b"<changelog/>"
    config_digest = _sha256(canonical_bytes)
    frontend_digest = _sha256(frontend_bytes)
    migrations_digest = _sha256(migrations_bytes)
    manifest_annotations = {
        "plugin_id": plugin_id,
        "manifest_version": version,
        "com.agentscale.plugin.manifest_digest": config_digest,
    }
    if annotations:
        manifest_annotations.update(annotations)
    layers: list[dict[str, Any]] = [
        {
            "mediaType": "application/vnd.agentscale.plugin.manifest.v1+json",
            "digest": config_digest,
            "size": len(canonical_bytes),
            "annotations": {
                "com.agentscale.plugin.layer.role": "canonical-manifest",
            },
        },
    ]
    for _ in range(frontend_layer_count):
        layers.append(
            {
                "mediaType": FRONTEND_MEDIA_TYPE,
                "digest": frontend_digest,
                "size": len(frontend_bytes),
                "annotations": {
                    "com.agentscale.plugin.layer.role": "frontend-dist",
                    "agentscale.plugin.frontend.entry": entry,
                },
            }
        )
    layers.append(
        {
            "mediaType": MIGRATIONS_MEDIA_TYPE,
            "digest": migrations_digest,
            "size": len(migrations_bytes),
            "annotations": {
                "com.agentscale.plugin.layer.role": "migrations",
            },
        },
    )
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "artifactType": "application/vnd.agentscale.plugin.oci",
        "annotations": manifest_annotations,
        "config": {
            "mediaType": "application/vnd.agentscale.plugin.manifest.v1+json",
            "digest": config_digest,
            "size": len(canonical_bytes),
        },
        "layers": layers,
    }
    blobs = {
        config_digest: canonical_bytes,
        frontend_digest: frontend_bytes,
    }
    return _Bundle(manifest, blobs)


class _DigestMismatch(RuntimeError):
    def __init__(self) -> None:
        self.code = SimpleNamespace(name="OCI_MANIFEST_MISMATCH")
        super().__init__("digest mismatch")


class FakeLoader:
    """Offline structural loader: strict sha256+size, per-ref stores."""

    def __init__(
        self,
        *,
        manifests: dict[str, bytes] | None = None,
        blobs: dict[str, bytes] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.manifests = manifests or {}
        self.blobs = blobs or {}
        self.delay = delay
        self.manifest_calls: list[str] = []
        self.blob_calls: list[tuple[str, str]] = []

    async def load_manifest(self, ref: object) -> Any:
        key = str(ref)
        self.manifest_calls.append(key)
        if self.delay:
            await asyncio.sleep(self.delay)
        data = self.manifests.get(key)
        if data is None:
            raise FileNotFoundError(key)
        if _sha256(data) != key.rsplit("@", 1)[1]:
            raise _DigestMismatch()
        return SimpleNamespace(data=data, digest=key.rsplit("@", 1)[1], size=len(data))

    async def load_blob(
        self, ref: object, *, digest: str, expected_size: int | None = None
    ) -> Any:
        self.blob_calls.append((str(ref), digest))
        data = self.blobs.get(digest)
        if data is None:
            raise FileNotFoundError(digest)
        if _sha256(data) != digest:
            raise _DigestMismatch()
        if expected_size is not None and len(data) != expected_size:
            raise _DigestMismatch()
        return SimpleNamespace(data=data, digest=digest, size=len(data))


def _service(
    loader: FakeLoader,
    **overrides: Any,
) -> PluginStaticEvidenceService:
    config = StaticEvidenceConfig(
        release_repository_prefix=RELEASE_PREFIX,
        quarantine_repository_prefix=QUARANTINE_PREFIX,
        **overrides,
    )
    return PluginStaticEvidenceService(artifact_loader=loader, config=config)


def _register(bundle: _Bundle, loader: FakeLoader, *, source: str) -> None:
    prefix = (
        QUARANTINE_PREFIX if source == "quarantine" else RELEASE_PREFIX
    )
    ref = f"{prefix}/{PLUGIN_ID}/bundle@{bundle.bundle_digest}"
    loader.manifests[ref] = bundle.manifest_bytes
    loader.blobs.update(bundle.blobs)


@pytest.mark.asyncio
async def test_serves_verified_bundle_from_quarantine_first() -> None:
    bundle = _build_bundle()
    loader = FakeLoader()
    _register(bundle, loader, source="quarantine")
    evidence = await _service(loader).verify_bundle(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
    )
    assert evidence.source == "quarantine"
    assert evidence.frontend_entry == ENTRY
    assert evidence.tar_member_count == 2
    assert evidence.config_digest in loader.blobs
    assert loader.manifest_calls[0].startswith(
        f"{QUARANTINE_PREFIX}/{PLUGIN_ID}/bundle@"
    )


@pytest.mark.asyncio
async def test_release_fallback_when_quarantine_is_missing() -> None:
    bundle = _build_bundle()
    loader = FakeLoader()
    _register(bundle, loader, source="release")
    evidence = await _service(loader).verify_bundle(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
    )
    assert evidence.source == "release"
    assert loader.manifest_calls == [
        f"{QUARANTINE_PREFIX}/{PLUGIN_ID}/bundle@{bundle.bundle_digest}",
        f"{RELEASE_PREFIX}/{PLUGIN_ID}/bundle@{bundle.bundle_digest}",
    ]


@pytest.mark.asyncio
async def test_release_digest_mismatch_is_typed_not_ready() -> None:
    bundle = _build_bundle()
    loader = FakeLoader()
    wrong_digest = "sha256:" + "a" * 64
    # The registry serves different bytes under the pinned digest: the shared
    # loader reports a digest mismatch, which is a verified negative (404).
    loader.manifests[f"{RELEASE_PREFIX}/{PLUGIN_ID}/bundle@{wrong_digest}"] = (
        bundle.manifest_bytes
    )
    loader.blobs.update(bundle.blobs)
    service = _service(loader)
    with pytest.raises(StaticEvidenceError) as caught:
        await service.verify_bundle(
            plugin_id=PLUGIN_ID,
            version=VERSION,
            bundle_digest=wrong_digest,
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY


@pytest.mark.asyncio
async def test_double_registry_failure_is_typed_unavailable() -> None:

    class _DownLoader:
        async def load_manifest(self, ref: object) -> Any:
            raise RuntimeError("registry down")

        async def load_blob(
            self, ref: object, *, digest: str, expected_size: int | None = None
        ) -> Any:
            raise RuntimeError("registry down")

    service = _service(_DownLoader())
    with pytest.raises(StaticEvidenceError) as caught:
        await service.verify_bundle(
            plugin_id=PLUGIN_ID,
            version=VERSION,
            bundle_digest="sha256:" + "b" * 64,
        )
    assert caught.value.code is StaticEvidenceErrorCode.EVIDENCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_annotation_mismatch_is_not_ready() -> None:
    bundle = _build_bundle(annotations={"plugin_id": "other_plugin"})
    loader = FakeLoader()
    _register(bundle, loader, source="quarantine")
    with pytest.raises(StaticEvidenceError) as caught:
        await _service(loader).verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY


@pytest.mark.asyncio
async def test_two_frontend_layers_are_rejected() -> None:
    bundle = _build_bundle(frontend_layer_count=2)
    loader = FakeLoader()
    _register(bundle, loader, source="quarantine")
    with pytest.raises(StaticEvidenceError) as caught:
        await _service(loader).verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY


@pytest.mark.asyncio
async def test_non_canonical_config_bytes_are_rejected() -> None:
    non_canonical = json.dumps(
        {
            "version": VERSION,
            "plugin_id": PLUGIN_ID,
            "frontend": {"entry": ENTRY, "base_path": "/"},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    bundle = _build_bundle(canonical_override=non_canonical)
    loader = FakeLoader()
    _register(bundle, loader, source="quarantine")
    with pytest.raises(StaticEvidenceError) as caught:
        await _service(loader).verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY


@pytest.mark.asyncio
async def test_entry_annotation_config_disagreement_is_rejected() -> None:
    bundle = _build_bundle(entry=ENTRY)
    # Rebuild with an entry annotation that disagrees with the config JSON.
    bundle = _build_bundle()
    manifest = dict(bundle.manifest)
    layers = [dict(layer) for layer in manifest["layers"]]
    for layer in layers:
        annotations = dict(layer.get("annotations", {}))
        if annotations.get("com.agentscale.plugin.layer.role") == "frontend-dist":
            annotations["agentscale.plugin.frontend.entry"] = "other.html"
            layer["annotations"] = annotations
    manifest["layers"] = layers
    rewritten = _Bundle(manifest, dict(bundle.blobs))
    loader = FakeLoader()
    _register(rewritten, loader, source="quarantine")
    with pytest.raises(StaticEvidenceError) as caught:
        await _service(loader).verify_bundle(
            plugin_id=PLUGIN_ID,
            version=VERSION,
            bundle_digest=rewritten.bundle_digest,
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY


async def _expect_not_ready(bundle: _Bundle) -> None:
    loader = FakeLoader()
    _register(bundle, loader, source="quarantine")
    with pytest.raises(StaticEvidenceError) as caught:
        await _service(loader).verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY


@pytest.mark.asyncio
async def test_tar_traversal_member_is_rejected() -> None:
    await _expect_not_ready(
        _build_bundle(
            frontend_tar=_build_tar(
                [(ENTRY, b"x"), ("../evil.js", b"y")],
            ),
        ),
    )


@pytest.mark.asyncio
async def test_tar_absolute_and_backslash_members_are_rejected() -> None:
    await _expect_not_ready(
        _build_bundle(
            frontend_tar=_build_tar([(ENTRY, b"x"), ("/etc/passwd", b"y")]),
        ),
    )
    await _expect_not_ready(
        _build_bundle(
            frontend_tar=_build_tar([(ENTRY, b"x"), ("a\\b.js", b"y")]),
        ),
    )


@pytest.mark.asyncio
async def test_tar_duplicate_and_non_regular_members_are_rejected() -> None:
    await _expect_not_ready(
        _build_bundle(
            frontend_tar=_build_tar(
                [(ENTRY, b"x"), ("assets/app.js", b"y"), ("assets/app.js", b"z")],
            ),
        ),
    )
    symlink_tar = io.BytesIO()
    with tarfile.open(fileobj=symlink_tar, mode="w:gz") as archive:
        info = tarfile.TarInfo(ENTRY)
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    await _expect_not_ready(
        _build_bundle(frontend_tar=symlink_tar.getvalue()),
    )


@pytest.mark.asyncio
async def test_missing_entry_is_rejected() -> None:
    await _expect_not_ready(
        _build_bundle(
            frontend_tar=_build_tar([("assets/app.js", b"y")]),
        ),
    )


@pytest.mark.asyncio
async def test_frontend_and_tar_caps_are_typed_too_large() -> None:
    bundle = _build_bundle()
    loader = FakeLoader()
    _register(bundle, loader, source="quarantine")
    small = _service(loader, max_frontend_layer_bytes=8)
    with pytest.raises(StaticEvidenceError) as caught:
        await small.verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_TOO_LARGE

    loader2 = FakeLoader()
    bundle2 = _build_bundle(
        frontend_tar=_build_tar([(ENTRY, b"x"), ("assets/app.js", b"y")]),
    )
    _register(bundle2, loader2, source="quarantine")
    capped = _service(loader2, max_tar_members=1)
    with pytest.raises(StaticEvidenceError) as caught:
        await capped.verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle2.bundle_digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_TOO_LARGE


@pytest.mark.asyncio
async def test_invalid_identity_never_touches_the_loader() -> None:
    loader = FakeLoader()
    service = _service(loader)
    for plugin_id, version, digest in (
        ("Demo", VERSION, "sha256:" + "a" * 64),
        (PLUGIN_ID, "1.2", "sha256:" + "a" * 64),
        (PLUGIN_ID, VERSION, "sha256:" + "A" * 64),
    ):
        with pytest.raises(StaticEvidenceError) as caught:
            await service.verify_bundle(
                plugin_id=plugin_id, version=version, bundle_digest=digest
            )
        assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY
    assert loader.manifest_calls == []
    assert loader.blob_calls == []


@pytest.mark.asyncio
async def test_success_cache_and_expiry() -> None:
    bundle = _build_bundle()
    loader = FakeLoader()
    _register(bundle, loader, source="quarantine")
    now = 1_000.0

    def clock() -> float:
        return now

    service = PluginStaticEvidenceService(
        artifact_loader=loader,
        config=StaticEvidenceConfig(
            release_repository_prefix=RELEASE_PREFIX,
            quarantine_repository_prefix=QUARANTINE_PREFIX,
            success_cache_seconds=100.0,
        ),
        clock=clock,
    )
    first = await service.verify_bundle(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
    )
    second = await service.verify_bundle(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
    )
    assert first == second
    assert len(loader.manifest_calls) == 1
    now += 200.0
    await service.verify_bundle(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
    )
    assert len(loader.manifest_calls) == 2


@pytest.mark.asyncio
async def test_negative_cache_is_short() -> None:
    loader = FakeLoader()
    now = 1_000.0

    def clock() -> float:
        return now

    service = PluginStaticEvidenceService(
        artifact_loader=loader,
        config=StaticEvidenceConfig(
            release_repository_prefix=RELEASE_PREFIX,
            quarantine_repository_prefix=QUARANTINE_PREFIX,
            negative_cache_seconds=30.0,
        ),
        clock=clock,
    )
    bundle = _build_bundle()
    loader.blobs.update(bundle.blobs)
    digest = "sha256:" + "c" * 64
    loader.manifests[f"{RELEASE_PREFIX}/{PLUGIN_ID}/bundle@{digest}"] = (
        bundle.manifest_bytes
    )
    for _ in range(2):
        with pytest.raises(StaticEvidenceError):
            await service.verify_bundle(
                plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=digest
            )
    assert len(loader.manifest_calls) == 2  # quarantine + release, once
    now += 60.0
    with pytest.raises(StaticEvidenceError):
        await service.verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=digest
        )
    assert len(loader.manifest_calls) == 4


@pytest.mark.asyncio
async def test_singleflight_collapses_concurrent_verifications() -> None:
    bundle = _build_bundle()
    loader = FakeLoader(delay=0.05)
    _register(bundle, loader, source="quarantine")
    service = _service(loader)
    results = await asyncio.gather(
        *(
            service.verify_bundle(
                plugin_id=PLUGIN_ID,
                version=VERSION,
                bundle_digest=bundle.bundle_digest,
            )
            for _ in range(5)
        )
    )
    assert len(results) == 5
    assert len(loader.manifest_calls) == 1


def test_loader_without_blob_method_is_rejected() -> None:
    class _HalfLoader:
        async def load_manifest(self, ref: object) -> Any:
            raise AssertionError

    with pytest.raises(StaticEvidenceConfigError):
        PluginStaticEvidenceService(
            artifact_loader=_HalfLoader(),
            config=StaticEvidenceConfig(
                release_repository_prefix=RELEASE_PREFIX,
                quarantine_repository_prefix=QUARANTINE_PREFIX,
            ),
        )


def test_quarantine_prefix_is_derived_from_settings() -> None:
    stub = SimpleNamespace(plugin_oci_registry=RELEASE_PREFIX)
    config = StaticEvidenceConfig.from_settings(stub)
    assert config.release_repository_prefix == RELEASE_PREFIX
    assert config.quarantine_repository_prefix == QUARANTINE_PREFIX


def test_bundle_evidence_rejects_foreign_source() -> None:
    with pytest.raises(ValueError):
        BundleEvidence(
            plugin_id=PLUGIN_ID,
            version=VERSION,
            bundle_digest="sha256:" + "d" * 64,
            config_digest="sha256:" + "e" * 64,
            frontend_digest="sha256:" + "f" * 64,
            frontend_entry=ENTRY,
            tar_member_count=1,
            tar_total_bytes=1,
            source="cdn",
        )


@pytest.mark.asyncio
async def test_real_httpx_loader_offline_roundtrip() -> None:
    """End-to-end with the real HttpxOciArtifactLoader over a mock transport."""
    import httpx
    from prodavan.application.plugins.artifacts.publication import (
        HttpxOciArtifactLoader,
    )

    bundle = _build_bundle()
    repo = f"{QUARANTINE_PREFIX.split('/', 1)[1]}/{PLUGIN_ID}/bundle"
    manifest_by_path = {
        f"/v2/{repo}/manifests/{bundle.bundle_digest}": bundle.manifest_bytes,
    }
    blob_by_path = {
        f"/v2/{repo}/blobs/{digest}": data
        for digest, data in bundle.blobs.items()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in manifest_by_path:
            return httpx.Response(200, content=manifest_by_path[path])
        if path in blob_by_path:
            return httpx.Response(200, content=blob_by_path[path])
        return httpx.Response(404)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        )

    loader = HttpxOciArtifactLoader(
        allowed_registries=("ghcr.io",),
        max_manifest_bytes=4 * 1024 * 1024,
        max_blob_bytes=64 * 1024 * 1024,
        client_factory=factory,
    )
    service = PluginStaticEvidenceService(
        artifact_loader=loader,
        config=StaticEvidenceConfig(
            release_repository_prefix=RELEASE_PREFIX,
            quarantine_repository_prefix=QUARANTINE_PREFIX,
        ),
    )
    evidence = await service.verify_bundle(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
    )
    assert evidence.source == "quarantine"
    assert evidence.frontend_entry == ENTRY


@pytest.mark.asyncio
async def test_quarantine_outage_never_falls_back_to_release() -> None:
    bundle = _build_bundle()
    loader = FakeLoader()
    _register(bundle, loader, source="release")

    class _Outage:
        def __init__(self, inner: FakeLoader) -> None:
            self._inner = inner

        async def load_manifest(self, ref: object) -> Any:
            if str(ref).startswith(QUARANTINE_PREFIX):
                raise RuntimeError("quarantine outage")
            return await self._inner.load_manifest(ref)

        async def load_blob(
            self, ref: object, *, digest: str, expected_size: int | None = None
        ) -> Any:
            return await self._inner.load_blob(
                ref, digest=digest, expected_size=expected_size
            )

    outage = _Outage(loader)
    service = _service(outage)
    with pytest.raises(StaticEvidenceError) as caught:
        await service.verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.EVIDENCE_UNAVAILABLE
    assert loader.manifest_calls == []


@pytest.mark.asyncio
async def test_quarantine_digest_mismatch_never_falls_back_to_release() -> None:
    bundle = _build_bundle()
    wrong_digest = "sha256:" + "e" * 64
    loader = FakeLoader()
    loader.manifests[f"{QUARANTINE_PREFIX}/{PLUGIN_ID}/bundle@{wrong_digest}"] = (
        bundle.manifest_bytes
    )
    _register(bundle, loader, source="release")
    service = _service(loader)
    with pytest.raises(StaticEvidenceError) as caught:
        await service.verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=wrong_digest
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY
    assert loader.manifest_calls == [
        f"{QUARANTINE_PREFIX}/{PLUGIN_ID}/bundle@{wrong_digest}",
    ]
