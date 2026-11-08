"""``SqlArtifactRegistryStore``: ``set_current`` не даёт гонки публикации.

Риск из отчёта задачи 07. Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ.
"""

from __future__ import annotations

import pytest

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import (
    ConcurrentPublishError,
    SqlArtifactRegistryStore,
)

from .conftest import DIGEST, NOW, OTHER_DIGEST, FakeSession, FakeSessionFactory, _Row


def _artifact_row(**overrides: object) -> object:
    data: dict[str, object] = {
        "id": "part_01",
        "plugin_id": "mod_equipment",
        "version": "1.0.0",
        "oci_digest": DIGEST,
        "image_ref": "ghcr.io/example@" + DIGEST,
        "manifest": {"plugin_id": "mod_equipment", "version": "1.0.0"},
        "app_version_projection": {"protocol": "1.0"},
        "cosign_verified": True,
        "status": "published",
        "quarantine_reason": None,
        "lifecycle_version": 7,
        "is_current": False,
        "published_at": NOW,
        "created_at": NOW,
    }
    data.update(overrides)
    return _Row(data)


def _store(*, rows: list | None = None, rowcount: int = 1) -> SqlArtifactRegistryStore:
    session = FakeSession(rows=rows, rowcount=rowcount)
    return SqlArtifactRegistryStore(
        session_factory=FakeSessionFactory(session), clock=lambda: NOW
    )


def test_sql_artifact_store_implements_registry_port() -> None:
    """Структурно реализует ``RegistryStorePort`` (Protocol НЕ runtime_checkable)."""
    store = _store()
    for method in (
        "add", "replace", "by_digest", "versions", "get", "current",
        "set_current", "append_quarantine", "append_lifecycle",
    ):
        assert callable(getattr(store, method, None)), f"нет метода {method}"


def test_artifact_row_maps_to_real_a10_columns() -> None:
    """Маппинг реальной A10: ``oci_digest`` → digest, status → AppLifecycleStatus."""
    store = _store(rows=[_artifact_row()])
    record = store.by_digest(DIGEST)
    assert record is not None
    assert record.digest == DIGEST
    assert record.status is AppLifecycleStatus.PUBLISHED
    assert record.published is True
    # Формат задачи 07: голый hex без префикса (совпадает с manifest_fingerprint()).
    assert len(record.manifest_fingerprint) == 64


def test_set_current_uses_cas_version_and_row_lock() -> None:
    """set_current: FOR UPDATE + CAS по lifecycle_version, commit при rowcount=1."""
    session = FakeSession(rows=[_artifact_row()], rowcount=1)
    factory = FakeSessionFactory(session)
    store = SqlArtifactRegistryStore(session_factory=factory, clock=lambda: NOW)

    store.set_current("mod_equipment", "1.0.0")
    assert session.commits == 1
    # В транзакции было >= 2 UPDATE (снять is_current + поставить с CAS).
    assert len(session.statements) >= 2


def test_set_current_cas_conflict_denies_second_publish() -> None:
    """ГЛАВНОЕ: rowcount=0 (параллельная публикация) → ConcurrentPublishError."""
    session = FakeSession(rows=[_artifact_row()], rowcount=0)
    store = SqlArtifactRegistryStore(
        session_factory=FakeSessionFactory(session), clock=lambda: NOW
    )
    with pytest.raises(ConcurrentPublishError):
        store.set_current("mod_equipment", "1.0.0")
    assert session.rollbacks == 1
    # Ни один коммит не подтверждён: второй публикации нет.
    assert session.commits == 0


def test_set_current_rejects_unpublished_artifact() -> None:
    """QUARANTINED/неопубликованный digest не может стать current (кр. 18)."""
    from prodavan.infrastructure.persistence.repositories.plugins._schema import (
        PluginRepositoryUnavailable,
    )

    store = _store(rows=[_artifact_row(status="quarantined")])
    with pytest.raises(PluginRepositoryUnavailable):
        store.set_current("mod_equipment", "1.0.0")


def test_set_current_rejects_unsigned_artifact() -> None:
    """Неподписанный digest (cosign_verified=False) → отказ публикации."""
    from prodavan.infrastructure.persistence.repositories.plugins._schema import (
        PluginRepositoryUnavailable,
    )

    store = _store(rows=[_artifact_row(cosign_verified=False)])
    with pytest.raises(PluginRepositoryUnavailable):
        store.set_current("mod_equipment", "1.0.0")


def test_add_duplicate_version_is_concurrent_publish_error() -> None:
    """Параллельная вставка того же (plugin_id, version) → явный отказ."""
    from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord

    store = _store(rows=[_artifact_row()])
    record = store.by_digest(DIGEST)
    assert record is not None
    with pytest.raises(ConcurrentPublishError):
        store.add(record)


def test_add_requires_published_projection_difference() -> None:
    """CHECK ``manifest <> app_version_projection`` соблюдён при INSERT."""
    from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord

    store = _store(rows=[])
    record = PluginArtifactRecord(
        plugin_id="mod_equipment",
        version="2.0.0",
        digest=OTHER_DIGEST,
        image_ref="",
        manifest={"plugin_id": "mod_equipment", "version": "2.0.0"},
        manifest_fingerprint="sha256:" + "c" * 64,
        status=AppLifecycleStatus.PUBLISHED,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        published_at=NOW,
        created_at=NOW,
    )
    # outcome не участвует в _values; проверяем, что INSERT прошёл и проекция
    # отличается от manifest (иначе CHECK в БД отклонил бы строку).
    store.add(record)
    session = store._session_factory.session  # type: ignore[union-attr]
    inserted = session.added[-1]
    # Реальная ORM-строка A10: атрибуты = колонки.
    assert inserted.manifest != inserted.app_version_projection
    assert inserted.oci_digest == OTHER_DIGEST


def test_current_reads_is_current_column_only() -> None:
    """Указатель «текущей» версии — строго ``is_current``, не «последняя по дате»."""
    store = _store(rows=[_artifact_row(is_current=True)])
    record = store.current("mod_equipment")
    assert record is not None and record.digest == DIGEST


def test_append_lifecycle_and_quarantine_are_append_only() -> None:
    store = _store()
    evidence = QuarantineEvidence(
        reason=RegistryReason.INVALID_TRANSITION,
        source_kind="test",
        source_id="mod_equipment@1.0.0",
        source_digest=DIGEST,
        observed_at=NOW,
        metadata={},
    )
    store.append_quarantine(evidence)
    store.append_lifecycle(command=None, actor="admin", detail="publish")
    store.append_lifecycle(command=None, actor="admin", detail="rollback")
    assert len(store.quarantine_log) == 1
    assert len(store.lifecycle_log) == 2  # ничего не перезаписано
