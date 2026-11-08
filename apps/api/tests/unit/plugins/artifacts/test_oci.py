"""Unit-тесты OCI-ссылки: pin по digest, запрет :latest, формат digest.

Тесты написаны, но НЕ запускаются (verification freeze, правило задачи 07).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.artifacts import (
    FORBIDDEN_TAGS,
    OciArtifactRef,
    is_valid_digest,
    parse_oci_ref,
)
from .conftest import DIGEST_A

REGISTRY = "ghcr.io"
REPOSITORY = "ne-tort/plugins/mod_equipment"


def test_image_ref_uses_digest_not_tag() -> None:
    ref = OciArtifactRef(registry=REGISTRY, repository=REPOSITORY, digest=DIGEST_A)
    assert ref.image_ref() == f"{REGISTRY}/{REPOSITORY}@{DIGEST_A}"
    assert ":latest" not in ref.image_ref()
    assert ref.image_ref().endswith(DIGEST_A)


def test_artifact_ref_requires_valid_media_type() -> None:
    ref = OciArtifactRef(registry=REGISTRY, repository=REPOSITORY, digest=DIGEST_A)
    assert ref.artifact_ref("application/vnd.agentscale.plugin.frontend") == ref.image_ref()
    with pytest.raises(ValueError):
        ref.artifact_ref("")
    with pytest.raises(ValueError):
        ref.artifact_ref("NOT A MEDIA TYPE")


def test_parse_oci_ref_accepts_digest_pinned() -> None:
    ref = parse_oci_ref(f"{REGISTRY}/{REPOSITORY}@{DIGEST_A}")
    assert ref.registry == REGISTRY
    assert ref.repository == REPOSITORY
    assert ref.digest == DIGEST_A


@pytest.mark.parametrize(
    "value",
    [
        f"{REGISTRY}/{REPOSITORY}:latest",
        f"{REGISTRY}/{REPOSITORY}:1.2.0",
        f"{REGISTRY}/{REPOSITORY}@sha256:latest",
        f"{REGISTRY}/{REPOSITORY}",  # нет digest
        f"{REGISTRY}/{REPOSITORY}@sha256:{'A' * 64}",  # верхний регистр
        f"{REGISTRY}/{REPOSITORY}@sha256:{'a' * 63}",  # 63, а не 64
        f"{REGISTRY}/{REPOSITORY}@md5:{'a' * 64}",  # не sha256
        f"{REGISTRY}/{REPOSITORY}@sha256:{'a' * 64}:latest",
        "",
    ],
)
def test_parse_oci_ref_rejects_tags_and_invalid_digest(value: str) -> None:
    """Критерий 18: деплой по digest, не :latest; невалидный digest — отказ."""
    with pytest.raises(ValueError):
        parse_oci_ref(value)


def test_latest_explicitly_forbidden() -> None:
    assert "latest" in FORBIDDEN_TAGS
    with pytest.raises(ValueError, match="latest"):
        OciArtifactRef(registry=REGISTRY, repository="ne-tort/plugins/latest", digest=DIGEST_A)
    with pytest.raises(ValueError):
        OciArtifactRef(registry=REGISTRY, repository=f"{REPOSITORY}:latest", digest=DIGEST_A)


@pytest.mark.parametrize(
    "digest",
    [
        "sha256:" + "a" * 64,
        "sha256:" + "0123456789abcdef" * 4,
    ],
)
def test_valid_digest(digest: str) -> None:
    assert is_valid_digest(digest) is True


@pytest.mark.parametrize(
    "digest",
    [
        "",
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "sha256:" + "A" * 64,
        "sha256:" + "g" * 64,
        "md5:" + "a" * 64,
        None,
        123,
    ],
)
def test_invalid_digest_rejected(digest: object) -> None:
    assert is_valid_digest(digest) is False  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OciArtifactRef(registry=REGISTRY, repository=REPOSITORY, digest=digest)  # type: ignore[arg-type]


def test_from_parts_is_strict() -> None:
    assert OciArtifactRef.from_parts(REGISTRY, REPOSITORY, DIGEST_A).digest == DIGEST_A
    with pytest.raises(ValueError):
        OciArtifactRef.from_parts("", REPOSITORY, DIGEST_A)
