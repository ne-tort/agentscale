"""Адаптер Admin API → реестр OCI-артефактов плагинов (задача 07).

Стык (обе стороны прочитаны):

=====================================================  ===========================================
admin (``admin/ports.py::ArtifactRegistryPort``)        artifacts (``registry.py``)
=====================================================  ===========================================
``get(*, plugin_id, manifest_version,                  ``PluginArtifactRegistry.get(
  artifact_digest) -> ArtifactRecord | None``            plugin_id, version)
`` -> PluginArtifactRecord | None`` (SYNC, по паре)
``unlink_installation(*, company_id, plugin_id,         НЕТ прямого метода: реестр
  artifact_digest) -> bool``                             хранит ГЛОБАЛЬНЫЙ immutable
                                                        digest, а не tenant-установку.
``ArtifactPublicationStatus.PUBLISHED/QUARANTINED``      ``AppLifecycleStatus.PUBLISHED/
                                                        DRAFT/DISABLED/REVOKED`` +
                                                        ``RegistryOutcome``
=====================================================  ===========================================

**ГЛАВНОЕ ПРАВИЛО (по заданию, §11 критерий 18): ``PUBLISHED`` маппится
ТОЛЬКО из ПРОВЕРЕННОГО digest, НИКОГДА из тега.** Поэтому:

1. Запрос без валидного ``sha256:<64 lowercase hex>`` → ``None`` (отказ), а не
   «поищем по версии/тегу». Теги (``:latest`` в особенности) запрещены
   ``artifacts/oci.py`` и не являются границей доверия.
2. Поиск ведётся **по digest** через ``RegistryStorePort.by_digest`` — это
   единственная точка, где хранится подписанный digest. ``registry.get(
   plugin_id, version)`` по паре (plugin, semver) НЕ достаточно: версия/тег
   мутабельны и могут указывать на другой digest.
3. После нахождения записи перепроверяется ВСЁ: ``plugin_id``, ``version``,
   ``digest``, ``status is AppLifecycleStatus.PUBLISHED``,
   ``outcome is RegistryOutcome.APPLIED``, ``quarantine is None``,
   ``published_at is not None``. Любое расхождение → ``QUARANTINED``/``None``.
   «Метка PUBLISHED без подтверждённого cosign-evidence» не принимается:
   публикация в реестре возможна только через ``publish`` с проверенным
   ``VerificationEvidence`` (см. ``registry.py``), и адаптер дополнительно
   требует наличия ``published_at``.
4. ``manifest_version`` у admin и ``record.version`` у реестра — одно и то же
   semver-значение; сверяем его явно.

``unlink_installation`` (tenant-scoped регистрация) в чужом каталоге НЕ
реализована: ``PluginArtifactRegistry`` — глобальный immutable реестр, и
удалять из него digest при uninstall компании было бы нарушением §5.5.
Адаптер держит СВОЙ in-memory реестр установок ``(company, plugin, digest)``
и удаляет только tenant-запись; глобальный digest не трогается. Для кластера
этот реестр ДОЛЖЕН быть поверх БД (задача A10) — см. RISKS в отчёте.

Fail-closed: ``False`` = адаптер не подтвердил удаление → ``UNINSTALL_PARTIAL``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import (
    ArtifactPublicationStatus,
    ArtifactRecord,
)
from prodavan.application.plugins.artifacts.oci import is_valid_digest
from prodavan.application.plugins.artifacts.registry import (
    PluginArtifactRecord,
    PluginArtifactRegistry,
    RegistryStorePort,
)
from prodavan.domain.dynamic_apps.types import AppLifecycleStatus

from .errors import require_wired


@runtime_checkable
class DigestLookupPort(Protocol):
    """Минимальный контракт поиска записи ПО DIGEST (не по тегу/версии).

    ``RegistryStorePort.by_digest`` реализует ``InMemoryRegistryStore`` и
    будущая БД-реализация. Отдельный Protocol здесь нужен, чтобы адаптер не
    требовал весь ``RegistryStorePort`` (add/replace/versions/...) и мог
    работать с тонким БД-репозиторием, ищущим запись по digest.
    """

    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None: ...
    async def get_async(self, plugin_id: str, version: str) -> PluginArtifactRecord | None: ...


class ArtifactRegistryAdapter:
    """``admin.ArtifactRegistryPort`` поверх ``PluginArtifactRegistry``.

    Принимает и сам реестр (для публичной семантики ``get``/``current``), и
    ``store`` для поиска по digest. Если ``store`` не передан, используется
    ``registry._store`` — но явная передача предпочтительнее (не завязываемся
    на приватный атрибут в проде).
    """

    def __init__(
        self,
        *,
        registry: PluginArtifactRegistry,
        store: DigestLookupPort | RegistryStorePort | None = None,
    ) -> None:
        self._registry = require_wired("artifact_registry.registry", registry)
        if not isinstance(registry, PluginArtifactRegistry):
            raise TypeError("ArtifactRegistryAdapter: ожидался PluginArtifactRegistry")
        resolved: Any = store if store is not None else getattr(registry, "_store", None)
        self._store = require_wired("artifact_registry.store", resolved)
        for method in ("by_digest_async", "get_async"):
            if not callable(getattr(self._store, method, None)):
                raise TypeError(
                    "ArtifactRegistryAdapter: store must implement by_digest_async and get_async"
                )
        # Tenant-scoped установки: глобальный immutable digest здесь НЕ хранится.
        # In-memory только для dev/тестов; кластеру нужна БД (задача A10).
        self._installations: set[tuple[str, str, str]] = set()

    @property
    def registry(self) -> PluginArtifactRegistry:
        return self._registry

    # ---------------------------------------------------------------- get

    async def get(
        self, *, plugin_id: str, manifest_version: str, artifact_digest: str
    ) -> ArtifactRecord | None:
        """Вернуть ``ArtifactRecord`` ТОЛЬКО для проверенного PUBLISHED-digest.

        Порядок проверок (все — ДО возврата положительного статуса):
        валидный digest → поиск ПО DIGEST → сверка plugin/version/digest →
        статус PUBLISHED + APPLIED + нет карантина + есть published_at.
        """

        digest = (artifact_digest or "").strip()
        if not is_valid_digest(digest):
            # Тег, пустая строка, ':latest', верхний регистр — отказ, не поиск.
            return None

        record = await self._store.by_digest_async(digest)
        if not isinstance(record, PluginArtifactRecord):
            return None
        if not self._is_verified_published(
            record,
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=digest,
        ):
            return None

        # Дополнительная сверка с публичным API реестра: запись по паре
        # (plugin, version) должна быть тем же самым digest. Расхождение
        # означает, что «текущая версия» указывает в другое место — отказ.
        by_version = await self._store.get_async(plugin_id, manifest_version)
        if by_version is None or by_version.digest != digest:
            return None

        return ArtifactRecord(
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=digest,
            status=ArtifactPublicationStatus.PUBLISHED,
            published_at=record.published_at,
        )

    # ------------------------------------------------- unlink_installation

    async def unlink_installation(
        self, *, company_id: str, plugin_id: str, artifact_digest: str
    ) -> bool:
        """Удалить tenant-scoped запись установки (НЕ глобальный digest).

        Идемпотентно: отсутствие записи возвращает ``True`` (уже удалено).
        ``False`` означает, что адаптер не подтвердил удаление → сервис
        трактует uninstall как ``UNINSTALL_PARTIAL``.
        """

        digest = (artifact_digest or "").strip()
        if not is_valid_digest(digest):
            # Невалидный digest не «удаляем на всякий случай»: это провал шага.
            return False
        key = (company_id, plugin_id, digest)
        self._installations.discard(key)
        return key not in self._installations

    def register_installation(
        self, *, company_id: str, plugin_id: str, artifact_digest: str
    ) -> None:
        """Зафиксировать установку (вызывает родитель/HTTP-слой после grant).

        Не часть ``ArtifactRegistryPort``: это служебный метод сборки, чтобы
        ``unlink_installation`` было что удалять. Глобальный реестр при этом
        не изменяется. В кластере заменяется БД-репозиторием (задача A10).
        """

        digest = (artifact_digest or "").strip()
        if not is_valid_digest(digest):
            raise ValueError("register_installation: нужен проверенный sha256-digest")
        self._installations.add((company_id, plugin_id, digest))

    @property
    def installations(self) -> frozenset[tuple[str, str, str]]:
        """Только для тестов/диагностики: снимок tenant-установок."""

        return frozenset(self._installations)

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _is_verified_published(
        record: PluginArtifactRecord,
        *,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
    ) -> bool:
        """Строгая проверка: статус реестра → PUBLISHED ТОЛЬКО из digest.

        Намеренно НЕ смотрим на ``manifest``/теги/``image_ref``: решение
        принимается по неизменяемому digest и по результату проверенного
        ``publish`` (status/outcome/quarantine/published_at).
        """

        if record.digest != artifact_digest or not is_valid_digest(record.digest):
            return False
        if record.plugin_id != plugin_id or record.version != manifest_version:
            return False
        if record.status is not AppLifecycleStatus.PUBLISHED:
            return False
        if record.outcome is not RegistryOutcome.APPLIED:
            return False
        if record.quarantine is not None:
            return False
        if record.published_at is None:
            return False
        if record.publication is None:
            return False
        evidence = record.verification
        if evidence is None or not evidence.verified:
            return False
        if not evidence.identity.strip() or not evidence.issuer.strip():
            return False
        if evidence.artifact_digest != record.digest or evidence.image_ref != record.image_ref:
            return False
        if evidence.manifest_fingerprint != record.manifest_fingerprint:
            return False
        if not all((
            evidence.sbom_verified,
            evidence.provenance_verified,
            evidence.scan_verified,
            evidence.csp_verified,
        )):
            return False
        return True


__all__ = ["ArtifactRegistryAdapter", "DigestLookupPort"]
