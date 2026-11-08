"""Ошибки объектного хранилища плагинов (fail-closed, архитектура v4 §5.4 / §4.5).

Любая неопределённость (нет адаптера, недоступен STS, сломан аудит, ключ вне
префикса) приводит к ЯВНОМУ исключению, а не к молчаливому успеху и не к
возврату root-кредов. Коды стабильны и уходят в problem+json через ``AppError``.
"""

from __future__ import annotations

from prodavan.domain.errors import AppError


class PluginObjectError(AppError):
    """База для всех отказов объектной capability плагинов."""

    CODE = "PLUGIN_OBJECT_ERROR"
    STATUS = 500
    TITLE = "Plugin object storage error"
    MESSAGE = "Ошибка объектного хранилища плагинов"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            code=self.CODE,
            title=self.TITLE,
            status=self.STATUS,
            detail=detail or self.MESSAGE,
        )


class ObjectAccessDenied(PluginObjectError):
    """Попытка выйти за пределы ``tenant-<company>/module-<plugin>/``."""

    CODE = "OBJECT_ACCESS_DENIED"
    STATUS = 403
    TITLE = "Object access denied"
    MESSAGE = "Доступ к объекту плагина запрещён"


class InvalidObjectKey(ObjectAccessDenied):
    """Некорректный ключ объекта или идентификатор области.

    Наследует ``ObjectAccessDenied`` намеренно: ``../`` и чужой префикс — это не
    «плохой ввод», а отказ в доступе. Обработчики и тесты могут ловить базовый
    класс, не различая типы атаки по коду ответа.
    """

    CODE = "INVALID_OBJECT_KEY"
    STATUS = 400
    TITLE = "Invalid object key"
    MESSAGE = "Недопустимый ключ объекта или идентификатор области"


class InvalidObjectMethod(InvalidObjectKey):
    """``method`` presign не ``get``/``put``."""

    CODE = "INVALID_OBJECT_METHOD"
    STATUS = 400
    TITLE = "Invalid object method"
    MESSAGE = "Метод presigned URL должен быть get или put"


class BucketUnavailable(PluginObjectError):
    """``BucketAdminPort`` / ``PresignPort`` недоступен или вернул мусор."""

    CODE = "BUCKET_UNAVAILABLE"
    STATUS = 503
    TITLE = "Bucket unavailable"
    MESSAGE = "Объектный бакет плагина недоступен"


class StsUnavailable(PluginObjectError):
    """MinIO STS недоступен: временные creds не выданы, root-креды запрещены."""

    CODE = "STS_UNAVAILABLE"
    STATUS = 503
    TITLE = "STS unavailable"
    MESSAGE = "STS недоступен: временные ограниченные credentials не выданы"


class AuditUnavailable(PluginObjectError):
    """Append аудита не удался: позитивная мутация отклоняется (§4.5)."""

    CODE = "AUDIT_UNAVAILABLE"
    STATUS = 503
    TITLE = "Audit unavailable"
    MESSAGE = "Не удалось записать событие аудита; операция отклонена"


__all__ = [
    "PluginObjectError",
    "ObjectAccessDenied",
    "InvalidObjectKey",
    "InvalidObjectMethod",
    "BucketUnavailable",
    "StsUnavailable",
    "AuditUnavailable",
]
