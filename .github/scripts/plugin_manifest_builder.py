#!/usr/bin/env python3
"""Построение подписываемого OCI-манифеста плагина (§5.1 дизайна v4, F4).

Pure-python, только stdlib. CI-скрипт намеренно НЕ импортирует код приложения:
константы продублированы и должны совпадать с
``apps/api/src/prodavan/application/plugins/artifacts/oci.py``
(``PLUGIN_PROTOCOL``, mediaType слоёв, формат digest).

Единица поставки плагина — OCI-артефакт в GHCR, закреплённый по digest и
подписанный cosign keyless. Данный манифест — логическое описание РОВНО ТРЁХ
слоёв (backend-образ по digest, frontend/dist, migrations) и аннотаций.
Физические blobs пушит ``oras push``; целостность всей связки обеспечивается
подписью уже собранного артефакта по его собственному digest, который
вычисляется ТОЛЬКО ПОСЛЕ push. Поэтому digest самого артефакта сюда не пишется.

Fail-closed: любая невалидность (semver, protocol, формат digest, capability,
самореференция, количество слоёв) -> ``ManifestError`` с кодом, а не «почти
валидный» манифест. Самореференция запрещена: ключи ``artifact_digest`` /
``signature`` внутри подписываемой полезной нагрузки делают подпись
невычислимой (ошибка v2, см. §13.1 v4). Digest артефакта публикуется только
в output/step-summary CI, уже вне подписываемого тела.

CLI печатает JSON в stdout; при ошибке пишет ``КОД: причина`` в stderr и
завершается с ненулевым кодом (fail-closed для shell-пайплайна).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable, Sequence
from typing import Any

# ---------------------------------------------------------------------------
# Константы (зеркало apps/api/.../application/plugins/artifacts/oci.py)
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_EMPTY_CONFIG_MEDIA_TYPE = "application/vnd.oci.empty.v1+json"
# sha256 от байт `{}` — стандартный пустой OCI-config (2 байта).
OCI_EMPTY_CONFIG_DIGEST = (
    "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
)
OCI_EMPTY_CONFIG_SIZE = 2

PLUGIN_ARTIFACT_TYPE = "application/vnd.agentscale.plugin.oci"
BACKEND_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
FRONTEND_MEDIA_TYPE = "application/vnd.agentscale.plugin.frontend"
MIGRATIONS_MEDIA_TYPE = "application/vnd.agentscale.plugin.liquibase+xml"

PLUGIN_PROTOCOL = "plugin-rpc/1"

# Allowlist gateway-адаптеров из §4.4 / §5.1. `db.direct` и `db.migrations`
# сюда НЕ входят: это границы PostgreSQL, а не capability gateway.
CAPABILITY_ALLOWLIST: frozenset[str] = frozenset(
    {"cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"}
)
NON_GATEWAY_CAPABILITIES: frozenset[str] = frozenset({"db.direct", "db.migrations"})

# Ключи аннотаций манифеста (имена из §5.1).
A_PLUGIN_ID = "plugin_id"
A_MANIFEST_VERSION = "manifest_version"
A_PROTOCOL = "protocol"
A_CAPABILITIES = "requestedCapabilities"
A_MIN_PLATFORM = "min_platform_version"
A_BACKEND_DIGEST = "backend_digest"

# Роль слоя фиксируется аннотацией: verify проверяет ровно эти три роли.
A_LAYER_ROLE = "com.agentscale.plugin.layer.role"
ROLE_BACKEND = "backend-image-ref"
ROLE_FRONTEND = "frontend-dist"
ROLE_MIGRATIONS = "migrations"
EXPECTED_LAYER_ROLES: tuple[str, ...] = (ROLE_BACKEND, ROLE_FRONTEND, ROLE_MIGRATIONS)

# Запрещённые ключи внутри подписываемого манифеста (самореференция).
FORBIDDEN_SELF_REFERENCE_KEYS: frozenset[str] = frozenset(
    {"artifact_digest", "signature"}
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_MEDIA_TYPE_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*/[a-z0-9]+(?:[.+-][a-z0-9]+)*$")

# ---------------------------------------------------------------------------
# Коды ошибок. Стабильные строки: CI-gate и QuarantineEvidence.metadata.
# Значения совпадают с ArtifactErrorCode из artifacts/errors.py.
# ---------------------------------------------------------------------------


class ManifestErrorCode:
    """Строковые коды отказов построения/валидации манифеста."""

    INVALID_PLUGIN_ID = "INVALID_PLUGIN_ID"
    INVALID_SEMVER = "INVALID_SEMVER"
    INVALID_PROTOCOL = "INVALID_PROTOCOL"
    INVALID_DIGEST = "INVALID_DIGEST"
    LATEST_FORBIDDEN = "LATEST_FORBIDDEN"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
    INVALID_CAPABILITIES = "INVALID_CAPABILITIES"
    INVALID_MIN_PLATFORM = "INVALID_MIN_PLATFORM"
    SELF_REFERENCE = "SELF_REFERENCE"
    INVALID_LAYERS = "INVALID_LAYERS"
    MISSING_FILE = "MISSING_FILE"
    INVALID_MANIFEST = "INVALID_MANIFEST"


class ManifestError(Exception):
    """Отказ построения манифеста. Несёт код и непустую причину.

    Fail-closed: отсутствие обязательных данных трактуется как ошибка,
    а не как «можно подписать и опубликовать».
    """

    def __init__(self, code: str, reason: str) -> None:
        if not isinstance(code, str) or not code:
            raise TypeError("code must be a non-empty string")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("ManifestError requires a non-empty reason")
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


# ---------------------------------------------------------------------------
# Валидаторы (все — fail-closed)
# ---------------------------------------------------------------------------


def _require_digest(value: str, field: str) -> str:
    """Digest должен быть ``^sha256:[0-9a-f]{64}$``; ``:latest`` запрещён."""
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(ManifestErrorCode.INVALID_DIGEST, f"{field}: пустой digest")
    candidate = value.strip()
    if ":latest" in candidate or candidate.endswith("/latest"):
        raise ManifestError(
            ManifestErrorCode.LATEST_FORBIDDEN,
            f"{field}: ':latest' для плагинов запрещён (§5.1, критерий 18)",
        )
    if _DIGEST_RE.fullmatch(candidate) is None:
        raise ManifestError(
            ManifestErrorCode.INVALID_DIGEST,
            f"{field}: ожидался ^sha256:[0-9a-f]{{64}}$, получено {candidate!r}",
        )
    return candidate


def _require_semver(value: str, field: str, code: str) -> str:
    """Строгий semver 2.0.0 без ведущих нулей; 'latest' не является версией."""
    if not isinstance(value, str) or _SEMVER_RE.fullmatch(value.strip()) is None:
        raise ManifestError(code, f"{field}: невалидный semver {value!r}")
    if "latest" in value.strip().lower():
        raise ManifestError(
            ManifestErrorCode.LATEST_FORBIDDEN,
            f"{field}: тег 'latest' как версия запрещён",
        )
    return value.strip()


def _require_plugin_id(value: str) -> str:
    """Идентификатор плагина — нижний регистр, без пути и без тегов."""
    if not isinstance(value, str) or _PLUGIN_ID_RE.fullmatch(value.strip()) is None:
        raise ManifestError(
            ManifestErrorCode.INVALID_PLUGIN_ID,
            f"plugin_id должен соответствовать ^[a-z0-9][a-z0-9_-]{{0,62}}$: {value!r}",
        )
    if ":latest" in value or "/" in value:
        raise ManifestError(
            ManifestErrorCode.INVALID_PLUGIN_ID,
            "plugin_id не должен содержать '/' или ':latest'",
        )
    return value.strip()


def _require_protocol(value: str) -> str:
    """Единственный допустимый протокол handshake — ``plugin-rpc/1`` (§5.2)."""
    if not isinstance(value, str) or value != PLUGIN_PROTOCOL:
        raise ManifestError(
            ManifestErrorCode.INVALID_PROTOCOL,
            f"protocol должен быть ровно {PLUGIN_PROTOCOL!r}, получено {value!r}",
        )
    return value


def _normalise_capabilities(values: Iterable[str]) -> tuple[str, ...]:
    """Allowlist capability + запрет дублей и не-gateway capability."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ManifestError(
            ManifestErrorCode.INVALID_CAPABILITIES,
            "requestedCapabilities должен быть итерируемым набора строк, не строкой",
        )
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str) or not raw.strip():
            raise ManifestError(
                ManifestErrorCode.INVALID_CAPABILITIES,
                f"capability должна быть непустой строкой: {raw!r}",
            )
        cap = raw.strip()
        if cap in NON_GATEWAY_CAPABILITIES:
            raise ManifestError(
                ManifestErrorCode.FORBIDDEN_CAPABILITY,
                f"{cap!r} не является capability gateway (граница PostgreSQL, §4.4)",
            )
        if cap not in CAPABILITY_ALLOWLIST:
            raise ManifestError(
                ManifestErrorCode.UNKNOWN_CAPABILITY,
                f"неизвестная capability {cap!r}; allowlist: {sorted(CAPABILITY_ALLOWLIST)}",
            )
        if cap in result:
            raise ManifestError(
                ManifestErrorCode.INVALID_CAPABILITIES,
                f"дублирующаяся capability {cap!r}",
            )
        if ":latest" in cap:
            raise ManifestError(
                ManifestErrorCode.LATEST_FORBIDDEN, f"capability содержит ':latest': {cap!r}"
            )
        result.append(cap)
    return tuple(result)


def _assert_no_self_reference(node: Any, path: str = "manifest") -> None:
    """Рекурсивно запрещает ключи ``artifact_digest`` / ``signature``.

    Проверяется ВСЯ полезная нагрузка, включая аннотации слоёв: подпись не
    может содержать собственный digest или собственную подпись.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise ManifestError(
                    ManifestErrorCode.INVALID_MANIFEST, f"{path}: ключ должен быть строкой"
                )
            if key in FORBIDDEN_SELF_REFERENCE_KEYS:
                raise ManifestError(
                    ManifestErrorCode.SELF_REFERENCE,
                    f"{path}.{key}: самореференция в подписываемом манифесте запрещена",
                )
            _assert_no_self_reference(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _assert_no_self_reference(value, f"{path}[{index}]")


def _assert_no_latest(node: Any, path: str = "manifest") -> None:
    """Явный запрет мутабельного тега ``:latest`` где угодно в манифесте."""
    if isinstance(node, dict):
        for key, value in node.items():
            _assert_no_latest(key, f"{path}.{key}")
            _assert_no_latest(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _assert_no_latest(value, f"{path}[{index}]")
    elif isinstance(node, str) and ":latest" in node:
        raise ManifestError(
            ManifestErrorCode.LATEST_FORBIDDEN,
            f"{path}: ':latest' запрещён для плагинов (пин только по digest)",
        )


# ---------------------------------------------------------------------------
# Формирование слоёв
# ---------------------------------------------------------------------------


def _collect_dir_files(directory: str) -> list[str]:
    """Детерминированный обход каталога (для CLI ``--frontend-dir`` и т.п.)."""
    if not isinstance(directory, str) or not os.path.isdir(directory):
        raise ManifestError(
            ManifestErrorCode.MISSING_FILE, f"каталог слоя не найден: {directory!r}"
        )
    files: list[str] = []
    for root, dirs, names in os.walk(directory):
        dirs.sort()
        for name in sorted(names):
            files.append(os.path.join(root, name))
    if not files:
        raise ManifestError(
            ManifestErrorCode.INVALID_LAYERS, f"каталог слоя пуст: {directory!r}"
        )
    return files


def _layer_payload(files: Sequence[str], role: str) -> tuple[str, int, list[str]]:
    """Считает стабильный digest/размер слоя из содержимого файлов.

    Digest = sha256 по отсортированным парам ``путь\\0sha256(байты)\\0``.
    Это логический digest подписываемого описания слоя; физический blob пушит
    ``oras`` (его descriptor может отличаться, целостность связки даёт подпись
    всего артефакта по digest). Пустой список — fail-closed.
    """
    if isinstance(files, (str, bytes)) or not isinstance(files, Sequence):
        raise ManifestError(
            ManifestErrorCode.INVALID_LAYERS, f"{role}: ожидалась последовательность путей"
        )
    normalised: list[str] = []
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            raise ManifestError(
                ManifestErrorCode.INVALID_LAYERS, f"{role}: пустой путь файла"
            )
        path = os.path.normpath(raw.strip())
        if not os.path.isfile(path):
            raise ManifestError(
                ManifestErrorCode.MISSING_FILE, f"{role}: файл не найден: {path!r}"
            )
        normalised.append(path)
    normalised = sorted(set(normalised))
    if not normalised:
        raise ManifestError(ManifestErrorCode.INVALID_LAYERS, f"{role}: слой без файлов")

    hasher = hashlib.sha256()
    total_size = 0
    for path in normalised:
        with open(path, "rb") as handle:
            data = handle.read()
        rel = path.replace(os.sep, "/")
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(hashlib.sha256(data).hexdigest().encode("ascii"))
        hasher.update(b"\0")
        total_size += len(data)
    return "sha256:" + hasher.hexdigest(), total_size, [
        path.replace(os.sep, "/") for path in normalised
    ]


def _make_layer(
    *,
    media_type: str,
    digest: str,
    size: int,
    role: str,
    annotations: dict[str, str],
) -> dict[str, Any]:
    """OCI-descriptor слоя. Роль и mediaType фиксируются аннотациями."""
    if _MEDIA_TYPE_RE.fullmatch(media_type) is None:
        raise ManifestError(
            ManifestErrorCode.INVALID_LAYERS, f"некорректный mediaType слоя: {media_type!r}"
        )
    layer_annotations = {A_LAYER_ROLE: role, **annotations}
    return {
        "mediaType": media_type,
        "digest": digest,
        "size": size,
        "annotations": layer_annotations,
    }


# ---------------------------------------------------------------------------
# Публичный контракт
# ---------------------------------------------------------------------------


def build_oci_manifest(
    plugin_id: str,
    version: str,
    backend_digest: str,
    frontend_files: Sequence[str],
    migrations_files: Sequence[str],
    requested_capabilities: Iterable[str],
    min_platform_version: str,
) -> dict[str, Any]:
    """Собрать подписываемый OCI-манифест плагина (ровно 3 слоя).

    Args:
        plugin_id: идентификатор плагина (нижний регистр, без пути/тегов).
        version: semver версия плагина.
        backend_digest: digest собранного backend-образа ``sha256:<64 hex>``;
            тег ``:latest`` категорически запрещён.
        frontend_files: пути файлов ``frontend/dist``.
        migrations_files: пути файлов ``migrations`` (Liquibase changelog и др.).
        requested_capabilities: capability из allowlist gateway-адаптеров.
        min_platform_version: минимальная semver-версия платформы.

    Returns:
        Словарь OCI image manifest (schemaVersion 2) с config, ровно тремя
        layers и annotations. Внутри НЕТ ключей ``artifact_digest`` /
        ``signature`` и нет ``:latest``.

    Raises:
        ManifestError: любая невалидность (код в ``exc.code``). Fail-closed.
    """
    pid = _require_plugin_id(plugin_id)
    ver = _require_semver(version, "version", ManifestErrorCode.INVALID_SEMVER)
    digest = _require_digest(backend_digest, "backend_digest")
    min_platform = _require_semver(
        min_platform_version, "min_platform_version", ManifestErrorCode.INVALID_MIN_PLATFORM
    )
    capabilities = _normalise_capabilities(requested_capabilities)
    # protocol — константа; проверка «== plugin-rpc/1» выполняется и здесь,
    # и повторно в validate_signed_manifest() на стороне verify.
    protocol = _require_protocol(PLUGIN_PROTOCOL)

    frontend_digest, frontend_size, frontend_list = _layer_payload(
        frontend_files, ROLE_FRONTEND
    )
    migrations_digest, migrations_size, migrations_list = _layer_payload(
        migrations_files, ROLE_MIGRATIONS
    )

    backend_layer = _make_layer(
        media_type=BACKEND_MEDIA_TYPE,
        digest=digest,
        # Размер внешнего образа этому дескриптору неизвестен до push;
        # авторитетен только digest. Явно помечаем слой как внешнюю ссылку.
        size=0,
        role=ROLE_BACKEND,
        annotations={
            "org.opencontainers.image.title": "backend image (digest-pinned)",
            "org.opencontainers.image.ref.name": f"plugins/{pid}/backend@{digest}",
            "com.agentscale.plugin.layer.external": "true",
            "com.agentscale.plugin.backend_digest": digest,
        },
    )
    frontend_layer = _make_layer(
        media_type=FRONTEND_MEDIA_TYPE,
        digest=frontend_digest,
        size=frontend_size,
        role=ROLE_FRONTEND,
        annotations={
            "org.opencontainers.image.title": "frontend/dist",
            "com.agentscale.plugin.layer.file_count": str(len(frontend_list)),
            "com.agentscale.plugin.layer.files": json.dumps(
                frontend_list, ensure_ascii=False, separators=(",", ":")
            ),
        },
    )
    migrations_layer = _make_layer(
        media_type=MIGRATIONS_MEDIA_TYPE,
        digest=migrations_digest,
        size=migrations_size,
        role=ROLE_MIGRATIONS,
        annotations={
            "org.opencontainers.image.title": "migrations",
            "com.agentscale.plugin.layer.file_count": str(len(migrations_list)),
            "com.agentscale.plugin.layer.files": json.dumps(
                migrations_list, ensure_ascii=False, separators=(",", ":")
            ),
        },
    )

    manifest: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": PLUGIN_ARTIFACT_TYPE,
        "config": {
            "mediaType": OCI_EMPTY_CONFIG_MEDIA_TYPE,
            "digest": OCI_EMPTY_CONFIG_DIGEST,
            "size": OCI_EMPTY_CONFIG_SIZE,
        },
        "layers": [backend_layer, frontend_layer, migrations_layer],
        # Аннотации из §5.1. `artifact_digest` НАМЕРЕННО отсутствует:
        # это самореференция (§13.1), digest артефакта известен только после push.
        "annotations": {
            "org.opencontainers.image.title": pid,
            "org.opencontainers.image.version": ver,
            "org.opencontainers.image.vendor": "agentscale",
            A_PLUGIN_ID: pid,
            A_MANIFEST_VERSION: ver,
            A_PROTOCOL: protocol,
            A_CAPABILITIES: ",".join(capabilities),
            A_MIN_PLATFORM: min_platform,
            A_BACKEND_DIGEST: digest,
        },
    }

    # Двойная проверка результата: даже при будущей правке кода подписать
    # невалидный манифест нельзя.
    _assert_no_self_reference(manifest)
    _assert_no_latest(manifest)
    validate_signed_manifest(manifest, backend_digest=digest)
    return manifest


def validate_signed_manifest(
    manifest: dict[str, Any], *, backend_digest: str | None = None
) -> dict[str, Any]:
    """Проверить уже собранный/прочитанный подписываемый манифест.

    Используется и построителем, и ``plugin_verify.py`` (тот содержит
    автономную копию проверок, чтобы не зависеть от sys.path). Возвращает
    нормализованную сводку полей для compatibility gate.

    Raises:
        ManifestError: при любом нарушении. Fail-closed.
    """
    if not isinstance(manifest, dict):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST, "манифест должен быть dict")
    _assert_no_self_reference(manifest)
    _assert_no_latest(manifest)

    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise ManifestError(
            ManifestErrorCode.INVALID_MANIFEST, "schemaVersion должен быть равен 2"
        )
    if manifest.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE:
        raise ManifestError(
            ManifestErrorCode.INVALID_MANIFEST, "некорректный mediaType манифеста"
        )
    config = manifest.get("config")
    if not isinstance(config, dict) or not isinstance(config.get("digest"), str):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST, "отсутствует OCI config")

    annotations = manifest.get("annotations")
    if not isinstance(annotations, dict):
        raise ManifestError(ManifestErrorCode.INVALID_MANIFEST, "отсутствуют annotations")

    pid = _require_plugin_id(str(annotations.get(A_PLUGIN_ID, "")))
    ver = _require_semver(
        str(annotations.get(A_MANIFEST_VERSION, "")),
        A_MANIFEST_VERSION,
        ManifestErrorCode.INVALID_SEMVER,
    )
    protocol = _require_protocol(str(annotations.get(A_PROTOCOL, "")))
    min_platform = _require_semver(
        str(annotations.get(A_MIN_PLATFORM, "")),
        A_MIN_PLATFORM,
        ManifestErrorCode.INVALID_MIN_PLATFORM,
    )
    raw_caps = annotations.get(A_CAPABILITIES, "")
    if not isinstance(raw_caps, str):
        raise ManifestError(
            ManifestErrorCode.INVALID_CAPABILITIES, "requestedCapabilities должно быть строкой"
        )
    capabilities = _normalise_capabilities(
        [part for part in raw_caps.split(",") if part.strip()]
    )
    manifest_backend_digest = _require_digest(
        str(annotations.get(A_BACKEND_DIGEST, "")), A_BACKEND_DIGEST
    )

    layers = manifest.get("layers")
    if not isinstance(layers, list) or len(layers) != len(EXPECTED_LAYER_ROLES):
        raise ManifestError(
            ManifestErrorCode.INVALID_LAYERS,
            f"манифест должен содержать ровно {len(EXPECTED_LAYER_ROLES)} слоя, "
            f"получено {len(layers) if isinstance(layers, list) else 'не список'}",
        )
    roles: list[str] = []
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise ManifestError(ManifestErrorCode.INVALID_LAYERS, f"layers[{index}] не dict")
        layer_annotations = layer.get("annotations")
        if not isinstance(layer_annotations, dict):
            raise ManifestError(
                ManifestErrorCode.INVALID_LAYERS, f"layers[{index}] без annotations"
            )
        role = layer_annotations.get(A_LAYER_ROLE)
        if not isinstance(role, str):
            raise ManifestError(
                ManifestErrorCode.INVALID_LAYERS, f"layers[{index}] без роли"
            )
        roles.append(role)
        _require_digest(str(layer.get("digest", "")), f"layers[{index}].digest")
        media_type = layer.get("mediaType")
        if not isinstance(media_type, str) or _MEDIA_TYPE_RE.fullmatch(media_type) is None:
            raise ManifestError(
                ManifestErrorCode.INVALID_LAYERS, f"layers[{index}].mediaType некорректен"
            )
        size = layer.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestError(
                ManifestErrorCode.INVALID_LAYERS, f"layers[{index}].size должен быть >= 0"
            )
    if tuple(roles) != EXPECTED_LAYER_ROLES:
        raise ManifestError(
            ManifestErrorCode.INVALID_LAYERS,
            f"порядок/набор ролей слоёв должен быть {EXPECTED_LAYER_ROLES}, получено {tuple(roles)}",
        )
    if str(layers[0].get("digest")) != manifest_backend_digest:
        raise ManifestError(
            ManifestErrorCode.INVALID_DIGEST,
            "digest backend-слоя не совпадает с аннотацией backend_digest",
        )
    if backend_digest is not None:
        expected = _require_digest(backend_digest, "backend_digest (аргумент)")
        if manifest_backend_digest != expected:
            raise ManifestError(
                ManifestErrorCode.INVALID_DIGEST,
                "backend_digest манифеста не совпадает с digestom из CI (DIGEST_CONFLICT)",
            )

    return {
        "plugin_id": pid,
        "version": ver,
        "protocol": protocol,
        "capabilities": capabilities,
        "min_platform_version": min_platform,
        "backend_digest": manifest_backend_digest,
        "layer_count": len(layers),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _split_csv(value: str) -> list[str]:
    """Разбор ``a,b,c`` в список; пустая строка -> пустой список."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Собрать подписываемый OCI-манифест плагина (§5.1, fail-closed)."
    )
    parser.add_argument("--plugin-id", required=True, help="идентификатор плагина")
    parser.add_argument("--version", required=True, help="semver версия плагина")
    parser.add_argument(
        "--backend-digest", required=True, help="sha256:<64 hex> backend-образа (НЕ :latest)"
    )
    parser.add_argument(
        "--frontend-files", default="", help="пути файлов frontend/dist через запятую"
    )
    parser.add_argument(
        "--frontend-dir", default="", help="каталог frontend/dist (рекурсивный обход)"
    )
    parser.add_argument(
        "--migrations-files", default="", help="пути файлов migrations через запятую"
    )
    parser.add_argument(
        "--migrations-dir", default="", help="каталог migrations (рекурсивный обход)"
    )
    parser.add_argument(
        "--capabilities",
        default="",
        help=f"capability через запятую; allowlist: {','.join(sorted(CAPABILITY_ALLOWLIST))}",
    )
    parser.add_argument(
        "--min-platform-version", required=True, help="минимальная semver-версия платформы"
    )
    parser.add_argument(
        "--protocol",
        default=PLUGIN_PROTOCOL,
        help="протокол handshake; допустимо только plugin-rpc/1",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: JSON-манифест в stdout, ненулевой exit при любой ошибке."""
    args = _build_cli_parser().parse_args(argv)
    try:
        # Явная проверка protocol из CLI: подменить его нельзя.
        _require_protocol(args.protocol)
        frontend_files = _split_csv(args.frontend_files)
        migrations_files = _split_csv(args.migrations_files)
        if args.frontend_dir:
            frontend_files = _collect_dir_files(args.frontend_dir)
        if args.migrations_dir:
            migrations_files = _collect_dir_files(args.migrations_dir)
        manifest = build_oci_manifest(
            plugin_id=args.plugin_id,
            version=args.version,
            backend_digest=args.backend_digest,
            frontend_files=frontend_files,
            migrations_files=migrations_files,
            requested_capabilities=_split_csv(args.capabilities),
            min_platform_version=args.min_platform_version,
        )
    except ManifestError as exc:
        print(f"{exc.code}: {exc.reason}", file=sys.stderr)
        return 2
    except Exception as exc:  # fail-closed: любая непредвиденная ошибка -> отказ
        print(f"{ManifestErrorCode.INVALID_MANIFEST}: {exc!r}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# Unit-тесты (НЕ запускаются в этой сессии; родитель вынесет в pytest)
# ---------------------------------------------------------------------------


def _selftest() -> None:
    """Покрытие: :latest, невалидный digest, самореференция, capability, 3 слоя.

    Функция определена, но намеренно не вызывается из ``__main__``: CLI всегда
    печатает манифест, а тесты забираются родителем в pytest. Только stdlib.
    """
    import tempfile

    good_digest = "sha256:" + "a" * 64
    frontend_file = ""
    migrations_file = ""

    with tempfile.TemporaryDirectory() as tmp:
        frontend_file = os.path.join(tmp, "index.html")
        migrations_file = os.path.join(tmp, "changelog.xml")
        with open(frontend_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        with open(migrations_file, "w", encoding="utf-8") as handle:
            handle.write("<databaseChangeLog/>")

        def build(**overrides: Any) -> dict[str, Any]:
            params: dict[str, Any] = {
                "plugin_id": "mod_equipment",
                "version": "1.2.0",
                "backend_digest": good_digest,
                "frontend_files": [frontend_file],
                "migrations_files": [migrations_file],
                "requested_capabilities": ("cache", "objects"),
                "min_platform_version": "1.0.0",
            }
            params.update(overrides)
            return build_oci_manifest(**params)

        def expect(code: str, **overrides: Any) -> None:
            try:
                build(**overrides)
            except ManifestError as exc:
                assert exc.code == code, (code, exc.code, exc.reason)
            else:
                raise AssertionError(f"ожидался ManifestError({code})")

        # 1) :latest отклоняется (и как digest, и как версия, и в capabilities).
        expect(ManifestErrorCode.LATEST_FORBIDDEN, backend_digest="ghcr.io/x/p:latest")
        expect(ManifestErrorCode.INVALID_SEMVER, version="latest")
        expect(ManifestErrorCode.INVALID_PLUGIN_ID, plugin_id="mod:latest")

        # 2) Невалидный digest: верхний регистр, короткий, произвольный префикс.
        expect(ManifestErrorCode.INVALID_DIGEST, backend_digest="sha256:" + "A" * 64)
        expect(ManifestErrorCode.INVALID_DIGEST, backend_digest="sha256:abc")
        expect(ManifestErrorCode.INVALID_DIGEST, backend_digest="md5:" + "a" * 64)
        expect(ManifestErrorCode.INVALID_DIGEST, backend_digest="")

        # 3) Самореференция: artifact_digest / signature на любом уровне.
        valid = build()
        assert "artifact_digest" not in json.dumps(valid)
        assert "signature" not in json.dumps(valid)
        bad_top = dict(valid)
        bad_top["artifact_digest"] = good_digest
        try:
            validate_signed_manifest(bad_top)
        except ManifestError as exc:
            assert exc.code == ManifestErrorCode.SELF_REFERENCE
        else:
            raise AssertionError("artifact_digest должен отклоняться")
        bad_nested = json.loads(json.dumps(valid))
        bad_nested["layers"][1]["annotations"]["signature"] = "MEUCI..."
        try:
            validate_signed_manifest(bad_nested)
        except ManifestError as exc:
            assert exc.code == ManifestErrorCode.SELF_REFERENCE
        else:
            raise AssertionError("вложенная signature должна отклоняться")

        # 4) Неизвестная capability и не-gateway capability.
        expect(ManifestErrorCode.UNKNOWN_CAPABILITY, requested_capabilities=("cache", "root"))
        expect(
            ManifestErrorCode.FORBIDDEN_CAPABILITY,
            requested_capabilities=("db.direct",),
        )
        expect(ManifestErrorCode.INVALID_CAPABILITIES, requested_capabilities=("cache", "cache"))
        expect(ManifestErrorCode.INVALID_CAPABILITIES, requested_capabilities="cache")

        # 5) Ровно 3 слоя: валидный манифест, затем 2 и 4 слоя, затем битая роль.
        assert len(valid["layers"]) == 3
        summary = validate_signed_manifest(valid, backend_digest=good_digest)
        assert summary["layer_count"] == 3
        assert summary["protocol"] == PLUGIN_PROTOCOL
        two_layers = json.loads(json.dumps(valid))
        two_layers["layers"].pop()
        try:
            validate_signed_manifest(two_layers)
        except ManifestError as exc:
            assert exc.code == ManifestErrorCode.INVALID_LAYERS
        else:
            raise AssertionError("манифест с 2 слоями должен отклоняться")
        four_layers = json.loads(json.dumps(valid))
        four_layers["layers"].append(dict(valid["layers"][2]))
        try:
            validate_signed_manifest(four_layers)
        except ManifestError as exc:
            assert exc.code == ManifestErrorCode.INVALID_LAYERS
        else:
            raise AssertionError("манифест с 4 слоями должен отклоняться")
        bad_role = json.loads(json.dumps(valid))
        bad_role["layers"][2]["annotations"][A_LAYER_ROLE] = "backend-image-ref"
        try:
            validate_signed_manifest(bad_role)
        except ManifestError as exc:
            assert exc.code == ManifestErrorCode.INVALID_LAYERS
        else:
            raise AssertionError("нарушенный порядок ролей должен отклоняться")

        # 6) semver / protocol / min_platform / несовпавший digest.
        expect(ManifestErrorCode.INVALID_SEMVER, version="1.2")
        expect(ManifestErrorCode.INVALID_SEMVER, version="01.2.0")
        expect(ManifestErrorCode.INVALID_MIN_PLATFORM, min_platform_version="dev")
        try:
            validate_signed_manifest(valid, backend_digest="sha256:" + "b" * 64)
        except ManifestError as exc:
            assert exc.code == ManifestErrorCode.INVALID_DIGEST
        else:
            raise AssertionError("несовпавший backend_digest должен отклоняться")
        bad_protocol = json.loads(json.dumps(valid))
        bad_protocol["annotations"][A_PROTOCOL] = "plugin-rpc/2"
        try:
            validate_signed_manifest(bad_protocol)
        except ManifestError as exc:
            assert exc.code == ManifestErrorCode.INVALID_PROTOCOL
        else:
            raise AssertionError("protocol != plugin-rpc/1 должен отклоняться")

        # 7) Пустой слой и отсутствующий файл — fail-closed.
        expect(ManifestErrorCode.INVALID_LAYERS, frontend_files=[])
        expect(ManifestErrorCode.MISSING_FILE, migrations_files=[os.path.join(tmp, "no.xml")])


if __name__ == "__main__":
    raise SystemExit(main())
