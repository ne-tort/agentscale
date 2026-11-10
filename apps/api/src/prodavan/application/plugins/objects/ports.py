"""Порты (Protocol) объектной capability плагинов (v4 §4.3, §5.4).

Плагин — company-scoped principal; он не знает, что за реализацией стоит
адаптер (boto3 / mc / MinIO Admin API). Здесь только typed-контракты.

Главное правило: ни один метод порта НЕ принимает ``bytes`` / payload.
As-built ``TenantObjectsService.put(..., content: bytes)`` и
``get_bytes(...) -> bytes`` гоняют содержимое через API (ещё и base64 в JSON) —
это узкое место: память, размер запроса, логи, отсутствие egress-контроля.
Вместо этого плагин получает short-lived scoped creds или presigned URL и
ходит в MinIO :9000 напрямую (только plugin-namespace, §6.2). Порты оперируют
именами, префиксами, TTL и метаданными — но никогда телами объектов.

``TenantObjectsService`` остаётся фасадом аудита/квот/листинга, но НЕ единственной
границей: граница — IAM-политика + этот typed-контракт.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from prodavan.application.plugins.objects.errors import InvalidTtl, PluginObjectError

# Presigned URL: 60..3600. STS имеет ОТДЕЛЬНЫЙ контракт 900..3600,
# подтверждённый real MinIO matrix (TTL=60 → 400, TTL=900 → 200).
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 3600
MIN_STS_TTL_SECONDS = 900
MAX_STS_TTL_SECONDS = MAX_TTL_SECONDS
DEFAULT_TTL_SECONDS = 900

ObjectMethod = Literal["get", "put"]


def validate_ttl(ttl_seconds: int) -> int:
    """Проверка TTL ТОЛЬКО для presigned URL (60..3600).

    As-built ``minio_store.presign_*`` делает только ``max(60, ...)`` — верхней
    границы нет, значит ``ExpiresIn`` может уйти в «почти бессрочный» URL.
    v4 требует short-lived creds, поэтому отвергаем и слишком малый, и слишком
    большой TTL явной ошибкой, а не тихим приведением.
    """
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise InvalidTtl(f"нечисловой TTL: {ttl_seconds!r}") from exc
    if not MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS:
        raise InvalidTtl(
            f"TTL={ttl} вне диапазона [{MIN_TTL_SECONDS}, {MAX_TTL_SECONDS}]"
        )
    return ttl


def validate_sts_ttl(ttl_seconds: int) -> int:
    """Validate MinIO STS DurationSeconds (900..3600), without clamping."""
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
        raise InvalidTtl("STS DurationSeconds must be an integer")
    if not MIN_STS_TTL_SECONDS <= ttl_seconds <= MAX_STS_TTL_SECONDS:
        raise InvalidTtl(
            f"STS TTL={ttl_seconds} вне диапазона "
            f"[{MIN_STS_TTL_SECONDS}, {MAX_STS_TTL_SECONDS}]"
        )
    return ttl_seconds


@dataclass(frozen=True, slots=True)
class ScopedCredentials:
    """Временные креденшелы STS, ограниченные ``module-<plugin>/``.

    ``policy`` — session policy (JSON-совместимый dict), уже применённая к
    ``AssumeRole``. ``bucket`` / ``prefix`` — ЯВНЫЕ литералы, а не policy
    variables: адаптер обязан сверить, что сессия выдана именно на этот бакет
    и префикс, и никогда не подставлять root ``access_key``/``secret_key``.
    """

    access_key: str
    secret_key: str
    session_token: str
    bucket: str
    prefix: str
    expires_at: float
    policy: dict


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """Метаданные объекта без содержимого (в аудит и листинг — только это)."""

    key: str
    size: int
    etag: str | None = None


@runtime_checkable
class BucketAdminPort(Protocol):
    """Администрирование бакетов доверенной платформой, НЕ плагином."""

    async def ensure_bucket(self, bucket: str, *, policy: dict, quota_bytes: int) -> None:
        """Создать/обновить ``tenant-<company>`` и записать IAM-политику + квоту.

        Fail-closed: любая ошибка → ``BucketUnavailable``, а не «и так сойдёт».
        """
        ...

    async def delete_bucket(self, bucket: str) -> None:
        """Удалить ТОЛЬКО указанный бакет (uninstall компании)."""
        ...

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Удалить ТОЛЬКО ``module-<plugin>/`` в ``tenant-<company>``.

        Используется в режиме «префикс + STS» (v1-компромисс §5.4), когда
        несколько плагинов делят один бакет. В режиме bucket-per-tenant
        адаптер может не реализовывать этот метод: тогда ``owns_bucket=True``
        и удаляется весь СВОЙ бакет. Возвращает число удалённых объектов
        (метаданные, не payload).
        """
        ...

    async def set_quota(self, bucket: str, quota_bytes: int) -> None:
        """Жёсткая квота бакета (биллинг по одному бакету, §5.4)."""
        ...

    async def get_usage(self, bucket: str) -> int:
        """Фактическое число байт в бакете (observability/квота), не payload."""
        ...

    async def list_prefix(self, bucket: str, prefix: str) -> list[ObjectInfo]:
        """Листинг ТОЛЬКО своего префикса, метаданные без содержимого.

        ``prefix`` передаётся доверенным сервисом и равен
        ``module-<plugin>/``. Адаптер не имеет права расширять его; сервис
        дополнительно перепроверяет каждый возвращённый ключ.
        """
        ...

    # Опциональный атрибут адаптера (НЕ часть обязательного набора методов):
    # ``True`` = bucket-per-tenant, весь СВОЙ бакет удаляется при uninstall;
    # ``False`` = режим «префикс + STS», удаляем только delete_prefix.
    # Отсутствие атрибута трактуется как ``True`` (значение по умолчанию v4).
    # Задаётся конфигурацией адаптера, а не пользовательским вводом.



@runtime_checkable
class StsPort(Protocol):
    """MinIO STS ``AssumeRole`` с session policy (v4 §5.4, вопрос §12.8)."""

    async def assume_role(
        self,
        *,
        company_id: str,
        plugin_id: str,
        ttl_seconds: int,
        session_policy: dict,
    ) -> ScopedCredentials:
        """Выдать short-lived creds ТОЛЬКО на ``module-<plugin>/``.

        Контракт fail-closed: при недоступности STS адаптер поднимает
        ``StsUnavailable``. Возврат root/service-кредов или creds без
        ``session_policy`` является нарушением контракта и проверяется в
        ``service.py`` (сервис не доверяет адаптеру вслепую).
        """
        ...


@runtime_checkable
class PresignPort(Protocol):
    """Scoped presigned URL. Ключ уже нормализован сервисом — порт НЕ доверяет
    входу и дополнительно проверяет, что он начинается с жёсткого префикса."""

    async def presign_get(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        ...

    async def presign_put(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        ...

    async def presign_head(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        """HEAD uses the same GetObject IAM permission, never ListBucket."""
        ...


@runtime_checkable
class AuditPort(Protocol):
    """Append-only аудит ``plugin_object_access`` (§4.4, §4.5, критерий 11)."""

    async def record(self, event: dict) -> None:
        """Записать каноническое событие.

        В ``event`` ЗАПРЕЩЕНЫ bytes / payload / токены / секреты / PII: только
        идентификаторы, операция, решение, код, причина, размеры и digest-ы.
        Неудачная запись для позитивной мутации = deny (``AuditUnavailable``).
        """
        ...


__all__ = [
    "MIN_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "ObjectMethod",
    "InvalidTtl",
    "validate_ttl",
    "ScopedCredentials",
    "ObjectInfo",
    "BucketAdminPort",
    "StsPort",
    "PresignPort",
    "AuditPort",
]
