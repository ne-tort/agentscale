"""Bridge regressions between trusted CI builder output and static evidence."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from prodavan.application.plugins.static_origin.service import StaticEvidenceErrorCode
from tests.unit.plugins.static_origin.test_service import (
    APP_JS,
    INDEX_HTML,
    PLUGIN_ID,
    QUARANTINE_REPOSITORY,
    VERSION,
    Bundle,
    ExactByteLoader,
    StaticEvidenceError,
    json_bytes,
    make_service,
    sha256,
    verify,
)

FRONTEND_ENTRY_ANNOTATION = "agentscale.plugin.frontend.entry"


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "scripts" / "plugin_manifest_builder.py").is_file():
            return parent
    raise RuntimeError("plugin CI repository root not found")


def _load_builder():
    path = _repo_root() / ".github" / "scripts" / "plugin_manifest_builder.py"
    spec = importlib.util.spec_from_file_location(
        "plugin_manifest_builder_static_origin_test",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load plugin_manifest_builder.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def _source_manifest() -> dict[str, Any]:
    return {
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


class BuilderBundle:
    """Real prepare_package/build_bundle_manifest bytes without subprocesses."""

    def __init__(self, *, manifest: dict[str, Any], bundle: Bundle) -> None:
        self.manifest = manifest
        self.bundle = bundle

    @property
    def frontend_layer(self) -> dict[str, Any]:
        return next(
            layer
            for layer in self.manifest["layers"]
            if layer["annotations"][builder.A_LAYER_ROLE] == builder.ROLE_FRONTEND
        )

    def with_serialized_manifest(self, value: dict[str, Any]) -> Bundle:
        """Replace only the signed manifest bytes while keeping blobs exact."""

        return Bundle(
            manifest=json_bytes(value),
            config=self.bundle.config,
            frontend=self.bundle.frontend,
            migrations=self.bundle.migrations,
        )


def _real_builder_bundle() -> BuilderBundle:
    with tempfile.TemporaryDirectory() as raw_directory:
        root = Path(raw_directory)
        source_root = root / "source"
        package = source_root / "packages" / "plugins" / "hello-plugin"
        frontend = package / "frontend" / "dist"
        frontend.mkdir(parents=True)
        (frontend / "index.html").write_bytes(INDEX_HTML)
        (frontend / "assets").mkdir()
        (frontend / "assets" / "app.js").write_bytes(APP_JS)
        migrations = package / "migrations"
        migrations.mkdir(parents=True)
        (migrations / "changelog.xml").write_bytes(
            b"<databaseChangeLog/>"
        )
        backend = package / "backend"
        backend.mkdir()
        (backend / "Dockerfile").write_text(
            "FROM ghcr.io/ne-tort/base@sha256:" + "a" * 64 + "\n",
            encoding="utf-8",
        )
        source = _source_manifest()
        (package / "manifest.json").write_bytes(
            json.dumps(source, separators=(",", ":")).encode("utf-8")
        )

        first = builder.prepare_package(
            str(source_root),
            "packages/plugins/hello-plugin",
            "",
            str(root / "prepared-first"),
        )
        second = builder.prepare_package(
            str(source_root),
            "packages/plugins/hello-plugin",
            "",
            str(root / "prepared-second"),
        )
        # CI requires byte-identical layer blobs across repeated preparation.
        for key in ("frontend_archive", "migrations_archive", "canonical_manifest"):
            assert Path(first[key]).read_bytes() == Path(second[key]).read_bytes()

        backend_digest = "sha256:" + "b" * 64
        manifest = builder.build_bundle_manifest(
            source,
            "ghcr.io/ne-tort/plugins-quarantine/hello_plugin/backend@"
            + backend_digest,
            first["canonical_manifest"],
            first["frontend_archive"],
            first["migrations_archive"],
            "ne-tort/agentscale",
            "c" * 40,
        )
        bundle = Bundle(
            manifest=json_bytes(manifest),
            config=Path(first["canonical_manifest"]).read_bytes(),
            frontend=Path(first["frontend_archive"]).read_bytes(),
            migrations=Path(first["migrations_archive"]).read_bytes(),
        )
        assert bundle.digest == sha256(bundle.manifest)
        assert bundle.config_digest == sha256(bundle.config)
        assert bundle.frontend_digest == sha256(bundle.frontend)
        return BuilderBundle(manifest=manifest, bundle=bundle)


@pytest.mark.asyncio
async def test_real_builder_bundle_is_quarantine_ready_with_exact_entry() -> None:
    artifact = _real_builder_bundle()
    loader = ExactByteLoader()
    loader.add(artifact.bundle)
    service = make_service(loader)

    evidence = await verify(service, artifact.bundle)

    assert evidence.source == "quarantine"
    assert evidence.plugin_id == PLUGIN_ID
    assert evidence.version == VERSION
    assert evidence.bundle_digest == artifact.bundle.digest
    assert evidence.config_digest == artifact.bundle.config_digest
    assert evidence.frontend_digest == artifact.bundle.frontend_digest
    assert evidence.frontend_entry == "index.html"
    assert evidence.tar_member_count == 2
    assert evidence.tar_total_bytes == len(INDEX_HTML) + len(APP_JS)
    assert artifact.frontend_layer["annotations"][FRONTEND_ENTRY_ANNOTATION] == (
        "index.html"
    )
    assert loader.calls == [
        ("manifest", artifact.bundle.ref(QUARANTINE_REPOSITORY), None),
        ("blob", artifact.bundle.ref(QUARANTINE_REPOSITORY), artifact.bundle.config_digest),
        ("blob", artifact.bundle.ref(QUARANTINE_REPOSITORY), artifact.bundle.frontend_digest),
    ]


@pytest.mark.asyncio
async def test_legacy_manifest_without_frontend_entry_annotation_stays_ready() -> None:
    artifact = _real_builder_bundle()
    assert FRONTEND_ENTRY_ANNOTATION in artifact.frontend_layer["annotations"]
    legacy = json.loads(artifact.bundle.manifest)
    legacy_layer = next(
        layer
        for layer in legacy["layers"]
        if layer["annotations"][builder.A_LAYER_ROLE] == builder.ROLE_FRONTEND
    )
    del legacy_layer["annotations"][FRONTEND_ENTRY_ANNOTATION]
    legacy_bundle = artifact.with_serialized_manifest(legacy)

    loader = ExactByteLoader()
    loader.add(legacy_bundle)
    evidence = await verify(service=make_service(loader), bundle=legacy_bundle)

    assert evidence.source == "quarantine"
    assert evidence.bundle_digest == legacy_bundle.digest
    assert evidence.frontend_entry == "index.html"
    assert loader.count("blob") == 2


@pytest.mark.asyncio
async def test_present_but_mismatched_frontend_entry_annotation_fails_closed() -> None:
    artifact = _real_builder_bundle()
    mismatched = json.loads(artifact.bundle.manifest)
    mismatched_layer = next(
        layer
        for layer in mismatched["layers"]
        if layer["annotations"][builder.A_LAYER_ROLE] == builder.ROLE_FRONTEND
    )
    mismatched_layer["annotations"][FRONTEND_ENTRY_ANNOTATION] = "assets/app.js"
    mismatched_bundle = artifact.with_serialized_manifest(mismatched)

    loader = ExactByteLoader()
    loader.add(mismatched_bundle)
    service = make_service(loader)
    with pytest.raises(StaticEvidenceError) as caught:
        await verify(service, mismatched_bundle)

    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY
    assert not service._success
    assert loader.count("manifest") == 1
    assert loader.count("blob") == 1  # canonical config was checked before the tar


@pytest.mark.asyncio
async def test_builder_and_oras_annotation_file_carry_the_exact_frontend_entry() -> None:
    artifact = _real_builder_bundle()
    layer_annotations = artifact.frontend_layer["annotations"]
    assert layer_annotations[builder.A_LAYER_ROLE] == builder.ROLE_FRONTEND
    assert layer_annotations[FRONTEND_ENTRY_ANNOTATION] == "index.html"

    annotations = builder.annotation_file(artifact.manifest)
    assert annotations["frontend-dist.tar.gz"][FRONTEND_ENTRY_ANNOTATION] == (
        "index.html"
    )
    assert annotations["$manifest"][builder.A_PLUGIN_ID] == PLUGIN_ID
