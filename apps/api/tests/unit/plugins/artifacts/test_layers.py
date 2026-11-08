"""Unit-тесты OCI-манифеста: ровно 3 слоя, запрет самореференции."""

from __future__ import annotations

import pytest

from prodavan.application.plugins.artifacts import (
    BACKEND_MEDIA_TYPE,
    EXPECTED_LAYER_COUNT,
    FRONTEND_MEDIA_TYPE,
    MIGRATIONS_MEDIA_TYPE,
    SELF_REFERENCE_KEY,
    ArtifactError,
    ArtifactErrorCode,
    build_oci_manifest,
)
from .conftest import backend_layer, config_descriptor, frontend_layer, migrations_layer, ref, valid_manifest


def _build(**overrides):
    kwargs = {
        "ref": ref(),
        "plugin_manifest": valid_manifest(),
        "backend_layer": backend_layer(),
        "frontend_layer": frontend_layer(),
        "migrations_layer": migrations_layer(),
        "config": config_descriptor(),
    }
    kwargs.update(overrides)
    return build_oci_manifest(**kwargs)


def test_exactly_three_layers_with_expected_media_types() -> None:
    manifest = _build()
    assert len(manifest["layers"]) == EXPECTED_LAYER_COUNT == 3
    media_types = [layer["mediaType"] for layer in manifest["layers"]]
    assert media_types == [BACKEND_MEDIA_TYPE, FRONTEND_MEDIA_TYPE, MIGRATIONS_MEDIA_TYPE]
    assert manifest["config"]["mediaType"] == "application/vnd.oci.image.config.v1+json"


def test_layer_paths_and_annotations() -> None:
    manifest = _build()
    titles = [layer["annotations"]["org.opencontainers.image.title"] for layer in manifest["layers"]]
    assert titles == [
        "backend/image.tar",
        "frontend/dist",
        "migrations/liquibase/changelog.xml",
    ]
    annotations = manifest["annotations"]
    assert annotations["agentscale.plugin.plugin_id"] == "mod_equipment"
    assert annotations["agentscale.plugin.manifest_version"] == "1.2.0"
    assert annotations["agentscale.plugin.protocol"] == "plugin-rpc/1"
    assert annotations["agentscale.plugin.requestedCapabilities"] == "cache,objects,docs"
    assert annotations["agentscale.plugin.min_platform_version"] == "4.0.0"


def test_no_artifact_digest_inside_signed_manifest() -> None:
    """Самореференция запрещена: artifact_digest не может быть подписан."""
    manifest = _build()

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key != SELF_REFERENCE_KEY, f"найден artifact_digest по пути {path}.{key}"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(manifest)
    assert "artifact_digest" not in manifest["annotations"]
    assert "layers" in manifest  # структура OCI, исходный plugin-manifest не вложен


def test_build_rejects_self_reference_in_plugin_manifest() -> None:
    with pytest.raises(ArtifactError) as exc:
        _build(plugin_manifest=valid_manifest(artifact_digest="sha256:" + "a" * 64))
    assert exc.value.code is ArtifactErrorCode.INCOMPATIBLE


def test_build_rejects_wrong_protocol() -> None:
    with pytest.raises(ArtifactError) as exc:
        _build(plugin_manifest=valid_manifest(protocol="plugin-rpc/2"))
    assert exc.value.code is ArtifactErrorCode.INCOMPATIBLE


def test_build_requires_scanned_backend_and_csp() -> None:
    backend = backend_layer()
    backend["scanned"] = False
    with pytest.raises(ArtifactError):
        _build(backend_layer=backend)

    frontend = frontend_layer()
    frontend["csp"] = ""
    with pytest.raises(ArtifactError):
        _build(frontend_layer=frontend)


def test_build_requires_valid_layer_digests() -> None:
    backend = backend_layer()
    backend["digest"] = "sha256:XYZ"
    with pytest.raises(ArtifactError) as exc:
        _build(backend_layer=backend)
    assert exc.value.code is ArtifactErrorCode.DIGEST_CONFLICT
