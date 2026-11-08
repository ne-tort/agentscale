"""Адаптер Admin API → объектное хранилище плагинов (задача 06).

Стык (обе стороны прочитаны):

==============================================  ==========================================
admin (``admin/ports.py::ObjectStorePort``)      objects (``service.py`` / ``ports.py``)
==============================================  ==========================================
``ensure_bucket(*, company_id, plugin_id)        ``PluginObjectStore.grant(company_id,
  -> ObjectStoreBinding``                         plugin_id) -> dict`` (позиционный)
``drop_prefix(*, company_id, plugin_id) -> bool``  ``BucketAdminPort.delete_prefix(
                                                   bucket, prefix) -> int``
``drop_bucket_if_empty(*, company_id) -> bool``   ``BucketAdminPort.delete_bucket(
                                                   bucket) -> None``
==============================================  ==========================================

Дополнительные расхождения, которые закрывает адаптер:

* ``BucketAdminPort.ensure_bucket(bucket, *, policy, quota_bytes)`` у реального
  порта и ``admin.ObjectStorePort.ensure_bucket(*, company_id, plugin_id)`` у
  админского — разные сигнатуры. Политику/квоту генерирует сам
  ``PluginObjectStore.grant`` через ``policy.bucket_policy`` и
  ``policy.session_policy``; адаптер НЕ переизобретает IAM-правила и НЕ
  подставляет ``policy``/``quota_bytes`` вручную (это была бы вторая, легко
  расходящаяся копия генератора).
* ``drop_*`` → ``delete_*``: админский ``drop_prefix`` удаляет ТОЛЬКО свой
  ``module-<plugin>/``, а ``drop_bucket_if_empty`` — ТОЛЬКО свой
  ``tenant-<company>`` и ТОЛЬКО когда других префиксов не осталось.
* ``False`` от ``drop_bucket_if_empty`` — НОРМАЛЬНЫЙ исход (бакет ещё используется
  другими плагинами компании), а не ошибка: см. ``PluginAdminService.uninstall``.
* Имя бакета/префикса всегда выводится из авторитетной пары
  ``(company_id, plugin_id)`` генератором ``objects.policy``; чужая компания
  физически не может попасть в вызов (критерий №15).

Fail-closed: отсутствие ``BucketAdminPort``, ошибка адаптера хранилища,
некорректный ответ ``grant``/``uninstall`` → явный отказ
``PluginAdminError``/``PluginObjectError``, а не «тихо вернули True».
"""

from __future__ import annotations

from typing import Any

from prodavan.application.plugins.admin.contracts import (
    ObjectStoreBinding,
)
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.objects import errors as object_errors
from prodavan.application.plugins.objects import policy as object_policy
from prodavan.application.plugins.objects.ports import (
    BucketAdminPort,
    ObjectInfo,
)
from prodavan.application.plugins.objects.service import PluginObjectStore

from .errors import require_wired


class ObjectStoreAdapter:
    """``admin.ObjectStorePort`` поверх ``PluginObjectStore`` + ``BucketAdminPort``.

    ``PluginObjectStore`` уже содержит аудит, IAM-политику, квоту и проверку
    ключей. Адаптер — ТОНКИЙ слой трансляции вызовов и их результатов; он не
    обходит сервис и не дублирует генератор политик.
    """

    def __init__(
        self,
        *,
        store: PluginObjectStore,
        buckets: BucketAdminPort,
    ) -> None:
        self._store = require_wired("object_store.store", store)
        self._buckets = require_wired("object_store.buckets", buckets)
        if not isinstance(store, PluginObjectStore):
            raise TypeError("ObjectStoreAdapter: ожидался PluginObjectStore")
        if not isinstance(buckets, BucketAdminPort):
            raise TypeError("ObjectStoreAdapter: ожидался BucketAdminPort")

    @property
    def store(self) -> PluginObjectStore:
        return self._store

    @property
    def buckets(self) -> BucketAdminPort:
        return self._buckets

    # ------------------------------------------------------------- ensure_bucket

    async def ensure_bucket(self, *, company_id: str, plugin_id: str) -> ObjectStoreBinding:
        """Создать бакет компании и префикс плагина через реальный сервис.

        ``policy``/``quota_bytes`` → ``bucket_policy``/``session_policy``
        формируются ВНУТРИ ``PluginObjectStore.grant`` (один генератор на обе
        политики, session ⊂ parent). Здесь проверяем, что ответ сервиса
        указывает ровно на наш бакет/префикс, и только тогда возвращаем
        ``ObjectStoreBinding``.
        """

        bucket = self._bucket(company_id=company_id)
        prefix = self._prefix(plugin_id=plugin_id)
        try:
            result: dict[str, Any] = await self._store.grant(company_id, plugin_id)
        except object_errors.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001 - адаптер MinIO мог упасть как угодно
            raise object_errors.BucketUnavailable(
                f"ensure_bucket({bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc

        if not isinstance(result, dict) or result.get("bucket") != bucket:
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="PluginObjectStore.grant вернул чужой bucket",
                status=503,
                extra={"adapter_operation": "ensure_bucket"},
            )
        raw_prefix = result.get("prefix")
        # Реальный генератор даёт ``module-<plugin>/``; админский контракт
        # исторически содержит ``/*`` (fakes задачи 12, api/v1/plugin_admin.py).
        # Допускаем РОВНО эти две формы СВОЕГО префикса — всё остальное отказ.
        allowed_prefixes = (prefix, f"{prefix}*")
        if raw_prefix not in allowed_prefixes:
            raise PluginAdminError(
                code="GRANT_EXISTS",
                detail="PluginObjectStore.grant вернул чужой prefix",
                status=503,
                extra={"adapter_operation": "ensure_bucket"},
            )
        reported_prefix = str(raw_prefix)

        return ObjectStoreBinding(
            company_id=company_id,
            plugin_id=plugin_id,
            bucket=bucket,
            prefix=reported_prefix,
            created=True,
        )

    # -------------------------------------------------------------- drop_prefix

    async def drop_prefix(self, *, company_id: str, plugin_id: str) -> bool:
        """Удалить ТОЛЬКО ``module-<plugin>/`` в ``tenant-<company>``.

        ``delete_prefix`` возвращает число удалённых объектов (метаданные, не
        payload). ``True`` = вызов подтверждён; ``0`` удалённых объектов тоже
        успех (префикс уже пуст/отсутствует) — идемпотентность uninstall.
        """

        bucket = self._bucket(company_id=company_id)
        prefix = self._prefix(plugin_id=plugin_id)
        deleter = getattr(self._buckets, "delete_prefix", None)
        if not callable(deleter):
            # В режиме bucket-per-tenant адаптер может не реализовать метод,
            # но admin-порт требует ``drop_prefix``: отсутствие = явный отказ.
            raise object_errors.BucketUnavailable(
                "BucketAdminPort.delete_prefix не реализован: drop_prefix запрещён"
            )
        try:
            removed = await deleter(bucket, prefix)
        except object_errors.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise object_errors.BucketUnavailable(
                f"удаление префикса {prefix!r} в {bucket!r} недоступно: "
                f"{type(exc).__name__}"
            ) from exc
        if not isinstance(removed, int) or isinstance(removed, bool) or removed < 0:
            raise object_errors.BucketUnavailable(
                "delete_prefix вернул некорректное число удалённых объектов"
            )
        return True

    # --------------------------------------------------- drop_bucket_if_empty

    async def drop_bucket_if_empty(self, *, company_id: str) -> bool:
        """Удалить бакет компании, ТОЛЬКО если не осталось других префиксов.

        ``False`` — нормальный исход (другие плагины компании ещё используют
        бакет), НЕ ошибка. Чужой бакет не трогается: имя выводится из своего
        ``company_id``.
        """

        bucket = self._bucket(company_id=company_id)
        prefixes = await self._other_prefixes(bucket=bucket)
        if prefixes:
            return False
        try:
            await self._buckets.delete_bucket(bucket)
        except object_errors.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise object_errors.BucketUnavailable(
                f"delete_bucket({bucket!r}) недоступен: {type(exc).__name__}"
            ) from exc
        return True

    # --------------------------------------------------------------- helpers

    async def _other_prefixes(self, *, bucket: str) -> tuple[str, ...]:
        """Список префиксов других плагинов в своём бакете.

        ``list_prefix(bucket, prefix)`` с пустым префиксом — это листинг
        метаданных СВОЕГО бакета (не содержимого). Если адаптер не реализовал
        ``list_prefix``, «пусто» НЕ предполагаем: fail-closed → отказ, иначе
        можно удалить бакет с чужими данными.
        """

        lister = getattr(self._buckets, "list_prefix", None)
        if not callable(lister):
            raise object_errors.BucketUnavailable(
                "BucketAdminPort.list_prefix не реализован: "
                "drop_bucket_if_empty запрещён (нельзя подтвердить пустоту)"
            )
        try:
            raw = await lister(bucket, "")
        except object_errors.PluginObjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise object_errors.BucketUnavailable(
                f"листинг {bucket!r} недоступен: {type(exc).__name__}"
            ) from exc
        keys: list[str] = []
        for item in raw or []:
            if isinstance(item, ObjectInfo):
                keys.append(item.key)
            elif isinstance(item, dict):
                keys.append(str(item.get("key", "")))
            else:
                raise object_errors.BucketUnavailable(
                    f"неожиданный элемент листинга: {type(item).__name__}"
                )
        # Префикс плагина — первый сегмент ключа ``module-<plugin>/...``.
        return tuple({key.split("/", 1)[0] for key in keys if key and "/" in key})

    @staticmethod
    def _bucket(*, company_id: str) -> str:
        """``tenant-<company>`` через генератор задачи 06 (жёсткая валидация)."""

        return object_policy.bucket_name(company_id)

    @staticmethod
    def _prefix(*, plugin_id: str) -> str:
        """``module-<plugin>/`` через генератор задачи 06."""

        return object_policy.module_prefix(plugin_id)


__all__ = ["ObjectStoreAdapter"]
