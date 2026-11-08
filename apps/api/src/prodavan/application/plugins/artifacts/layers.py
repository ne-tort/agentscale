"""Описание OCI-манифеста артефакта плагина (§5.1, §13.3 дизайна v4).

Артефакт — один immutable OCI-манифест с ТРЕМЯ слоями:

1. backend image (linux/amd64);
2. ``frontend/dist`` (mediaType ``application/vnd.agentscale.plugin.frontend``);
3. ``migrations/liquibase/changelog.xml``.

Аннотации несут ``plugin_id``, ``manifest_version``, ``protocol``,
``requestedCapabilities``, ``min_platform_version`` и ``artifact_digest``.

ВАЖНО (§5.1 «без самореференции», §13.1): ``artifact_digest`` — это digest
САМОГО OCI-манифеста, поэтому он НЕ может входить в подписываемый манифест
плагина: нельзя подписать значение, которое появляется только после подписи.
Он живёт ТОЛЬКО в annotations OCI-манифеста и вычисляется CI после сборки.
``build_oci_manifest`` явно отбрасывает/запрещает этот ключ в payload.
"""

from __future__ import annotations

from typing import Any, Mapping

from .errors import ArtifactError, ArtifactErrorCode
from .oci import (
    BACKEND_MEDIA_TYPE,
    FRONTEND_MEDIA_TYPE,
    MIGRATIONS_MEDIA_TYPE,
    PLUGIN_PROTOCOL,
    OciArtifactRef,
    is_valid_digest,
)

OCI_MANIFEST_SCHEMA_VERSION = 2
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
PLUGIN_ARTIFACT_MEDIA_TYPE = "application/vnd.agentscale.plugin.artifact.v1+json"

# Ключ, который запрещён внутри подписываемого manifest (самореференция).
SELF_REFERENCE_KEY = "artifact_digest"

EXPECTED_LAYER_COUNT = 3
BACKEND_LAYER_PATH = "backend/image.tar"
FRONTEND_LAYER_PATH = "frontend/dist"
MIGRATIONS_LAYER_PATH = "migrations/liquibase/changelog.xml"


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    """Fail-closed: обязательный объект, иначе отказ, а не молчаливый None."""
    if not isinstance(value, Mapping):
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            f"поле {field!r} манифеста обязано быть объектом",
        )
    return value


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            f"поле {field!r} манифеста обязано быть непустой строкой",
        )
    return value


def build_oci_manifest(
    *,
    ref: OciArtifactRef,
    plugin_manifest: Mapping[str, Any],
    backend_layer: Mapping[str, Any],
    frontend_layer: Mapping[str, Any],
    migrations_layer: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Собрать структуру OCI image manifest (dict) для CI (задача 16).

    Возвращает «подписываемый» каркас: ровно 3 слоя, annotations без
    самореференции, pin по digest. Родитель/CI затем считает digest, добавит
    ``artifact_digest`` в annotations и подпишет cosign keyless.

    ``config`` — дескриптор OCI config-блоба (``digest`` + ``size``); передаётся
    явно, чтобы не подсовывать синтетический digest (fail-closed).

    Проверки (fail-closed):
    - ``ref`` — строго ``OciArtifactRef`` с валидным digest;
    - в ``plugin_manifest`` отсутствует ``artifact_digest`` (самореференция);
    - ``protocol == plugin-rpc/1``;
    - ровно 3 слоя с ожидаемыми mediaType и путями;
    - изображение просканировано (``scanned: true``) — §5.1 compatibility gate;
    - CSP для frontend присутствует (``csp``) — §5.2 / §5.1.
    """
    if not isinstance(ref, OciArtifactRef):
        raise ArtifactError(
            ArtifactErrorCode.DIGEST_CONFLICT,
            "OCI-манифест строится только из OciArtifactRef, закреплённого по digest",
        )
    if not is_valid_digest(ref.digest):
        raise ArtifactError(ArtifactErrorCode.DIGEST_CONFLICT, "невалидный digest артефакта")

    manifest = _require_mapping(plugin_manifest, "plugin_manifest")
    # Запрет самореференции: artifact_digest не может быть внутри подписываемого manifest.
    if SELF_REFERENCE_KEY in manifest:
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            "artifact_digest запрещён внутри подписываемого manifest: самореференция (§5.1)",
        )

    plugin_id = _require_str(manifest.get("plugin_id"), "plugin_id")
    manifest_version = _require_str(
        manifest.get("version") or manifest.get("manifest_version"), "version"
    )
    protocol = _require_str(manifest.get("protocol"), "protocol")
    if protocol != PLUGIN_PROTOCOL:
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            f"protocol должен быть {PLUGIN_PROTOCOL!r}, получено {protocol!r}",
        )

    requested = manifest.get("requestedCapabilities")
    if not isinstance(requested, (list, tuple)) or not requested or not all(
        isinstance(item, str) and item.strip() for item in requested
    ):
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            "requestedCapabilities обязан быть непустым массивом строк",
        )
    min_platform_version = _require_str(manifest.get("min_platform_version"), "min_platform_version")

    backend = _require_mapping(backend_layer, "backend_layer")
    frontend = _require_mapping(frontend_layer, "frontend_layer")
    migrations = _require_mapping(migrations_layer, "migrations_layer")
    config_desc = _require_mapping(config, "config")

    # Образ обязан быть просканирован (compatibility gate §5.1).
    if backend.get("scanned") is not True:
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            "backend-образ не помечен как просканированный (scanned=true)",
        )
    # CSP для frontend обязателен (§5.2, §5.1 gate).
    frontend_csp = frontend.get("csp")
    if not isinstance(frontend_csp, str) or not frontend_csp.strip():
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            "для frontend-слоя обязан быть задан CSP (csp)",
        )

    layers: list[dict[str, Any]] = [
        {
            "mediaType": BACKEND_MEDIA_TYPE,
            "digest": _checked_digest(backend.get("digest"), "backend"),
            "size": _checked_size(backend.get("size"), "backend"),
            "annotations": {
                "org.opencontainers.image.title": BACKEND_LAYER_PATH,
                "org.opencontainers.image.ref.name": ref.image_ref(),
                "agentscale.plugin.backend.scanned": "true",
                "agentscale.plugin.backend.architecture": _require_str(
                    backend.get("architecture"), "backend.architecture"
                ),
            },
        },
        {
            "mediaType": FRONTEND_MEDIA_TYPE,
            "digest": _checked_digest(frontend.get("digest"), "frontend"),
            "size": _checked_size(frontend.get("size"), "frontend"),
            "annotations": {
                "org.opencontainers.image.title": FRONTEND_LAYER_PATH,
                "agentscale.plugin.frontend.csp": frontend_csp,
                "agentscale.plugin.frontend.entry": _require_str(
                    frontend.get("entry"), "frontend.entry"
                ),
            },
        },
        {
            "mediaType": MIGRATIONS_MEDIA_TYPE,
            "digest": _checked_digest(migrations.get("digest"), "migrations"),
            "size": _checked_size(migrations.get("size"), "migrations"),
            "annotations": {
                "org.opencontainers.image.title": MIGRATIONS_LAYER_PATH,
                "agentscale.plugin.migrations.engine": _require_str(
                    migrations.get("engine"), "migrations.engine"
                ),
            },
        },
    ]
    if len(layers) != EXPECTED_LAYER_COUNT:
        # Защита от случайного изменения структуры при доработках.
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE, "OCI-манифест обязан содержать ровно 3 слоя"
        )

    annotations: dict[str, str] = {
        "agentscale.plugin.plugin_id": plugin_id,
        "agentscale.plugin.manifest_version": manifest_version,
        "agentscale.plugin.protocol": protocol,
        "agentscale.plugin.requestedCapabilities": ",".join(str(item) for item in requested),
        "agentscale.plugin.min_platform_version": min_platform_version,
        "org.opencontainers.image.ref.name": ref.image_ref(),
    }

    result: dict[str, Any] = {
        "schemaVersion": OCI_MANIFEST_SCHEMA_VERSION,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": PLUGIN_ARTIFACT_MEDIA_TYPE,
        "config": {
            "mediaType": OCI_CONFIG_MEDIA_TYPE,
            "digest": _checked_digest(config_desc.get("digest"), "config"),
            "size": _checked_size(config_desc.get("size"), "config"),
        },
        "layers": layers,
        "annotations": annotations,
    }
    # Финальная гарантия: ни в payload, ни в annotations нет самореференции.
    _assert_no_self_reference(result)
    return result


def _checked_digest(value: object, layer: str) -> str:
    if not is_valid_digest(value):
        raise ArtifactError(
            ArtifactErrorCode.DIGEST_CONFLICT,
            f"слой {layer!r}: невалидный sha256 digest",
        )
    return str(value)


def _checked_size(value: object, layer: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            f"слой {layer!r}: size обязан быть неотрицательным целым",
        )
    return value


def _assert_no_self_reference(structure: object) -> None:
    """Рекурсивно убедиться, что в структуре нет ключа ``artifact_digest``.

    Fail-closed: самореференция запрещена §5.1/§13.1. Ключ может появиться
    только в annotations ПОСЛЕ подписи, но не в подписываемом манифесте,
    который возвращает эта функция.
    """
    if isinstance(structure, Mapping):
        for key, value in structure.items():
            if str(key) == SELF_REFERENCE_KEY:
                raise ArtifactError(
                    ArtifactErrorCode.INCOMPATIBLE,
                    "artifact_digest обнаружен внутри подписываемой структуры: самореференция",
                )
            _assert_no_self_reference(value)
    elif isinstance(structure, (list, tuple)):
        for item in structure:
            _assert_no_self_reference(item)


__all__ = [
    "BACKEND_LAYER_PATH",
    "EXPECTED_LAYER_COUNT",
    "FRONTEND_LAYER_PATH",
    "MIGRATIONS_LAYER_PATH",
    "OCI_CONFIG_MEDIA_TYPE",
    "OCI_MANIFEST_MEDIA_TYPE",
    "OCI_MANIFEST_SCHEMA_VERSION",
    "PLUGIN_ARTIFACT_MEDIA_TYPE",
    "SELF_REFERENCE_KEY",
    "build_oci_manifest",
]
