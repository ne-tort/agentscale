"""Тесты ``artifacts_adapter``: PUBLISHED ТОЛЬКО из проверенного digest.

Ключевой тест по заданию: маппинг ``PUBLISHED`` делается из проверенного
``sha256:<64 hex>`` и подтверждённой публикации, а НЕ из тега/версии.
Покрытие:
* digest валиден + запись PUBLISHED/APPLIED/без карантина → PUBLISHED;
* тег, ``:latest``, пустой digest, uppercase hex → ``None``;
* статус QUARANTINED/DRAFT/REVOKED, outcome != APPLIED, quarantine != None,
  отсутствие published_at → ``None``;
* расхождение digest/plugin/version → ``None``;
* «current» реестра указывает на другой digest → ``None``;
* ``unlink_installation`` не трогает глобальный immutable digest, идемпотентен.
"""

from __future__ import annotations

from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import ArtifactPublicationStatus
from prodavan.application.plugins.adapters.artifacts_adapter import (
    ArtifactRegistryAdapter,
)
from prodavan.application.plugins.artifacts.registry import (
    InMemoryRegistryStore,
    PluginArtifactRegistry,
)
from prodavan.application.plugins.artifacts.verification import ArtifactVerifier

from .fakes import COMPANY, DIGEST, DIGEST_OTHER, PLUGIN, make_artifact_record


class _FakeCosign:
    def verify(self, image_ref: str, identity: str, issuer: str) -> Any:
        from prodavan.application.plugins.artifacts.verification import (
            VerificationResult,
        )

        return VerificationResult(
            verified=True,
            signed_digest=image_ref.split("@", 1)[-1],
            sbom_verified=True,
            provenance_verified=True,
        )


def _registry(record: Any | None = None) -> tuple[PluginArtifactRegistry, InMemoryRegistryStore]:
    store = InMemoryRegistryStore()
    if record is not None:
        store.add(record)
    verifier = ArtifactVerifier(
        cosign=_FakeCosign(),
        identity="ci@agentscale",
        issuer="https://token.actions.githubusercontent.com",
    )
    registry = PluginArtifactRegistry(store=store, verifier=verifier)
    if record is not None and record.published:
        store.set_current(record.plugin_id, record.version)
    return registry, store


def _adapter(record: Any | None = None) -> ArtifactRegistryAdapter:
    registry, store = _registry(record)
    return ArtifactRegistryAdapter(registry=registry, store=store)


# --------------------------------------------------------------------------- get


async def test_published_is_mapped_only_from_verified_digest() -> None:
    adapter = _adapter(make_artifact_record())

    result = await adapter.get(
        plugin_id=PLUGIN,
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
    )

    assert result is not None
    assert result.status is ArtifactPublicationStatus.PUBLISHED
    assert result.artifact_digest == DIGEST
    assert result.published_at is not None


@pytest.mark.parametrize(
    "bad_digest",
    [
        "",
        "latest",
        "ghcr.io/plugins/mod_equipment:latest",
        "sha256:" + "A" * 64,  # uppercase hex запрещён
        "sha256:" + "a" * 63,  # короткий digest
        "sha256:" + "a" * 64 + "@latest",
        ":1.2.0",
        DIGEST_OTHER,
    ],
)
async def test_tag_or_invalid_digest_is_never_published(bad_digest: str) -> None:
    """Тег/версия/чужой digest НЕ являются границей доверия (критерий №18)."""

    adapter = _adapter(make_artifact_record())
    assert (
        await adapter.get(
            plugin_id=PLUGIN,
            manifest_version="1.2.0",
            artifact_digest=bad_digest,
        )
        is None
    )


async def test_status_not_published_maps_to_none_not_quarantined_record() -> None:
    """Любой не-PUBLISHED статус реестра = «не опубликовано» (None)."""

    adapter = _adapter(make_artifact_record(published=False))
    assert (
        await adapter.get(
            plugin_id=PLUGIN, manifest_version="1.2.0", artifact_digest=DIGEST
        )
        is None
    )


async def test_applied_outcome_quarantine_and_published_at_are_required() -> None:
    for kwargs in (
        {"applied": False},
        {"quarantine": True},
        {"published_at": False},
    ):
        adapter = _adapter(make_artifact_record(**kwargs))
        assert (
            await adapter.get(
                plugin_id=PLUGIN, manifest_version="1.2.0", artifact_digest=DIGEST
            )
            is None
        ), kwargs


async def test_digest_must_match_plugin_and_version() -> None:
    adapter = _adapter(make_artifact_record())
    assert (
        await adapter.get(
            plugin_id="mod_offers", manifest_version="1.2.0", artifact_digest=DIGEST
        )
        is None
    )
    assert (
        await adapter.get(
            plugin_id=PLUGIN, manifest_version="9.9.9", artifact_digest=DIGEST
        )
        is None
    )


async def test_registry_get_must_point_to_same_digest() -> None:
    """Реестр по паре (plugin, version) должен указывать на тот же digest."""

    registry, store = _registry(make_artifact_record())
    adapter = ArtifactRegistryAdapter(registry=registry, store=store)
    assert (
        await adapter.get(
            plugin_id=PLUGIN, manifest_version="1.2.0", artifact_digest=DIGEST
        )
    ) is not None

    # Добавляем запись с другим digest под тем же semver: поиск по digest
    # по-прежнему находит PUBLISHED, но «текущая версия» уже другая.
    other = make_artifact_record(digest=DIGEST_OTHER, version="1.2.0")
    store.add(other)
    store.set_current(PLUGIN, "1.2.0")
    # get(plugin, version) возвращает ПЕРВУЮ подходящую запись (DIGEST),
    # поэтому дополнительно проверяем, что чужой digest не становится PUBLISHED.
    assert (
        await adapter.get(
            plugin_id=PLUGIN, manifest_version="1.2.0", artifact_digest=DIGEST_OTHER
        )
    ) is None

    # Явная подмена: реестр содержит ТОЛЬКО другой digest под тем же semver —
    # гейт по паре (plugin, version) откажет для исходного digest.
    registry2, store2 = _registry(make_artifact_record(digest=DIGEST_OTHER, version="1.2.0"))
    adapter2 = ArtifactRegistryAdapter(registry=registry2, store=store2)
    assert (
        await adapter2.get(
            plugin_id=PLUGIN, manifest_version="1.2.0", artifact_digest=DIGEST
        )
    ) is None
    assert (
        await adapter2.get(
            plugin_id=PLUGIN, manifest_version="1.2.0", artifact_digest=DIGEST_OTHER
        )
    ) is not None


async def test_missing_record_returns_none() -> None:
    adapter = _adapter(None)
    assert (
        await adapter.get(
            plugin_id=PLUGIN, manifest_version="1.2.0", artifact_digest=DIGEST
        )
        is None
    )


# --------------------------------------------------------------------------- unlink


async def test_unlink_installation_is_idempotent_and_keeps_global_digest() -> None:
    record = make_artifact_record()
    registry, store = _registry(record)
    adapter = ArtifactRegistryAdapter(registry=registry, store=store)
    adapter.register_installation(
        company_id=COMPANY, plugin_id=PLUGIN, artifact_digest=DIGEST
    )

    assert await adapter.unlink_installation(
        company_id=COMPANY, plugin_id=PLUGIN, artifact_digest=DIGEST
    ) is True
    assert adapter.installations == frozenset()
    # Глобальный immutable digest в реестре НЕ удалён. set_current лишь
    # увеличивает lifecycle generation и заменяет immutable dataclass record.
    from dataclasses import replace
    assert store.by_digest(DIGEST) == replace(record, lifecycle_version=1)
    # Повтор — идемпотентный успех.
    assert await adapter.unlink_installation(
        company_id=COMPANY, plugin_id=PLUGIN, artifact_digest=DIGEST
    ) is True


async def test_unlink_rejects_invalid_digest() -> None:
    adapter = _adapter(make_artifact_record())
    assert await adapter.unlink_installation(
        company_id=COMPANY, plugin_id=PLUGIN, artifact_digest=":latest"
    ) is False


def test_register_installation_requires_verified_digest() -> None:
    adapter = _adapter(make_artifact_record())
    with pytest.raises(ValueError, match="sha256"):
        adapter.register_installation(
            company_id=COMPANY, plugin_id=PLUGIN, artifact_digest="latest"
        )


class _AsyncReadStore:
    def __init__(self, store: InMemoryRegistryStore) -> None:
        self.store = store
        self.digest_calls: list[str] = []
        self.version_calls: list[tuple[str, str]] = []

    async def by_digest_async(self, digest: str):
        self.digest_calls.append(digest)
        return self.store.by_digest(digest)

    async def get_async(self, plugin_id: str, version: str):
        self.version_calls.append((plugin_id, version))
        return self.store.get(plugin_id, version)


async def test_get_awaits_both_digest_and_version_reads() -> None:
    registry, store = _registry(make_artifact_record())
    reads = _AsyncReadStore(store)
    adapter = ArtifactRegistryAdapter(registry=registry, store=reads)

    result = await adapter.get(
        plugin_id=PLUGIN, manifest_version="1.2.0", artifact_digest=DIGEST
    )

    assert result is not None
    assert reads.digest_calls == [DIGEST]
    assert reads.version_calls == [(PLUGIN, "1.2.0")]
