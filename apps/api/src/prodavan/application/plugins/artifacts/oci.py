"""Ссылка на OCI-артефакт плагина (§5.1 дизайна v4).

Единица поставки — OCI-артефакт в GHCR, подписанный cosign keyless и
закреплённый по digest. Теги (особенно ``:latest``) для плагинов запрещены:
тег мутабелен и ломает проверку «тот же digest, что подписан».

Формат ссылки: ``<registry>/<repository>@sha256:<64 hex>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
# Полная ссылка «репозиторий@digest»; тег (`...:tag`) не допускается.
_FULL_REF_PATTERN = re.compile(
    r"^(?P<registry>[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?)"
    r"/(?P<repository>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
    r"@(?P<digest>sha256:[0-9a-f]{64})$"
)
_REPOSITORY_PATTERN = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$"
)

FORBIDDEN_TAGS: frozenset[str] = frozenset({"latest"})
PLUGIN_PROTOCOL = "plugin-rpc/1"
FRONTEND_MEDIA_TYPE = "application/vnd.agentscale.plugin.frontend"
BACKEND_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
MIGRATIONS_MEDIA_TYPE = "application/vnd.agentscale.plugin.liquibase+xml"


def is_valid_digest(digest: str) -> bool:
    """Строгая проверка digest: только нижний регистр hex и ровно 64 символа."""
    return isinstance(digest, str) and _DIGEST_PATTERN.fullmatch(digest) is not None


@dataclass(frozen=True, slots=True)
class OciArtifactRef:
    """Immutable ссылка на OCI-артефакт, всегда по digest.

    ``registry`` — хост реестра (например ``ghcr.io``);
    ``repository`` — путь без тега (например ``ne-tort/plugins/mod_equipment``).
    """

    registry: str
    repository: str
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.registry, str) or not self.registry.strip():
            raise ValueError("registry must be a non-empty host")
        if ":" in self.repository or self.repository.endswith("@"):
            raise ValueError("repository must not contain a tag or digest separator")
        if not isinstance(self.repository, str) or _REPOSITORY_PATTERN.fullmatch(self.repository) is None:
            raise ValueError("repository must be a lowercase OCI path without tags")
        if not is_valid_digest(self.digest):
            raise ValueError("digest must match ^sha256:[0-9a-f]{64}$")
        # Явный запрет мутабельных тегов (§5.1, критерий 18).
        if self.repository.split("/")[-1] in FORBIDDEN_TAGS:
            raise ValueError("tag ':latest' is forbidden for plugin artifacts")

    def image_ref(self) -> str:
        """Ссылка по digest, НЕ по тегу: ``registry/repository@sha256:...``."""
        return f"{self.registry}/{self.repository}@{self.digest}"

    def artifact_ref(self, media_type: str) -> str:
        """Ссылка на артефакт с явным mediaType (для cosign/CI).

        mediaType не подставляется в саму ссылку (это метаданные манифеста),
        но проверяется: пустой/некорректный тип — fail-closed.
        Возвращается канонический ``image_ref`` по digest.
        """
        if not isinstance(media_type, str) or not re.fullmatch(
            r"[a-z0-9]+(?:[.-][a-z0-9]+)*/[a-z0-9]+(?:[.+-][a-z0-9]+)*", media_type
        ):
            raise ValueError("media_type must be a valid IANA-style media type")
        return self.image_ref()

    @classmethod
    def from_parts(cls, registry: str, repository: str, digest: str) -> OciArtifactRef:
        """Явная сборка из частей (используется CI и тестами)."""
        return cls(registry=registry, repository=repository, digest=digest)


def parse_oci_ref(value: str) -> OciArtifactRef:
    """Разобрать ``registry/repository@sha256:<64 hex>``.

    Fail-closed: тег (``:latest``, ``:1.2.0``), отсутствие digest, верхний
    регистр в hex, некорректный путь — всё это ``ValueError``, а не «попробуем
    угадать». Публикация без проверенного digest невозможна.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OCI ref must be a non-empty string")
    candidate = value.strip()
    if "@" not in candidate:
        raise ValueError("OCI ref must be pinned by @sha256 digest; tags are forbidden")
    # Явный запрет ':latest' в любой части ссылки.
    if ":latest" in candidate or candidate.endswith("/latest"):
        raise ValueError("tag ':latest' is forbidden for plugin artifacts")
    match = _FULL_REF_PATTERN.fullmatch(candidate)
    if match is None:
        raise ValueError("OCI ref must match <registry>/<repository>@sha256:<64 hex>")
    return OciArtifactRef(
        registry=match.group("registry"),
        repository=match.group("repository"),
        digest=match.group("digest"),
    )


__all__ = [
    "BACKEND_MEDIA_TYPE",
    "FORBIDDEN_TAGS",
    "FRONTEND_MEDIA_TYPE",
    "MIGRATIONS_MEDIA_TYPE",
    "PLUGIN_PROTOCOL",
    "OciArtifactRef",
    "is_valid_digest",
    "parse_oci_ref",
]
