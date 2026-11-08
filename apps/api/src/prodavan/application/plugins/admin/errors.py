"""Ошибки Admin API lifecycle плагинов (F3-admin).

Наследуют ``AppError`` из ``prodavan.domain.errors``: тем самым все отказы автоматически
превращаются в RFC 7807 problem+json уже зарегистрированным обработчиком
(``api/exception_handlers.py``), и HTTP-слою не нужен свой exception handler.

Коды фиксированы задачей и являются частью публичного контракта:
  ARTIFACT_NOT_PUBLISHED, GRANT_EXISTS, GRANT_NOT_FOUND, REVOKE_FAILED,
  UNINSTALL_PARTIAL.

Fail-closed: любая неопределённость (артефакт не найден, статус не PUBLISHED,
порт вернул неожиданный ответ, реестр отзывов недоступен) трактуется как отказ,
а не как молчаливое разрешение.
"""

from __future__ import annotations

from enum import StrEnum

from prodavan.domain.errors import AppError


class PluginAdminErrorCode(StrEnum):
    """Стабильные коды отказов админского lifecycle."""

    ARTIFACT_NOT_PUBLISHED = "ARTIFACT_NOT_PUBLISHED"
    GRANT_EXISTS = "GRANT_EXISTS"
    GRANT_NOT_FOUND = "GRANT_NOT_FOUND"
    REVOKE_FAILED = "REVOKE_FAILED"
    UNINSTALL_PARTIAL = "UNINSTALL_PARTIAL"


class PluginAdminError(AppError):
    """Отказ операции lifecycle плагинов.

    ``code`` принимает только значения :class:`PluginAdminErrorCode` (или уже
    строковое значение одного из них). Статус по умолчанию — 409: все перечисленные
    коды описывают конфликт состояния, а не ошибку валидации тела запроса.
    """

    def __init__(
        self,
        *,
        code: PluginAdminErrorCode | str,
        detail: str,
        status: int = 409,
        extra: dict | None = None,
    ) -> None:
        normalized = PluginAdminErrorCode(code).value
        super().__init__(
            code=normalized,
            title=normalized,
            status=status,
            detail=detail,
            extra=extra,
        )


# --------------------------------------------------------------------------- фабрики
# Централизованные точки построения: гарантируют единый русский текст и код для
# каждого сценария, чтобы сервис не мог «случайно» выдать другой код.


def artifact_not_published(*, plugin_id: str, manifest_version: str, artifact_digest: str) -> PluginAdminError:
    """Артефакт отсутствует в реестре или находится в ``QUARANTINED`` (§11 кр. 18)."""

    return PluginAdminError(
        code=PluginAdminErrorCode.ARTIFACT_NOT_PUBLISHED,
        detail=(
            f"Артефакт плагина {plugin_id} версии {manifest_version} "
            f"({artifact_digest}) не опубликован: установка запрещена"
        ),
        status=409,
        extra={"plugin_id": plugin_id, "artifact_digest": artifact_digest},
    )


def grant_exists(*, company_id: str, plugin_id: str) -> PluginAdminError:
    """Повторный grant с другим артефактом/версией — конфликт, а не переустановка.

    Повторный grant с тем же набором параметров идемпотентен и до этой ошибки
    не доходит (см. ``PluginAdminService.grant``).
    """

    return PluginAdminError(
        code=PluginAdminErrorCode.GRANT_EXISTS,
        detail=(
            f"У компании {company_id} уже есть активный grant плагина {plugin_id}; "
            "для смены версии требуется uninstall"
        ),
        status=409,
        extra={"company_id": company_id, "plugin_id": plugin_id},
    )


def grant_not_found(*, company_id: str, plugin_id: str) -> PluginAdminError:
    """Операция над отсутствующим/уже деактивированным grant."""

    return PluginAdminError(
        code=PluginAdminErrorCode.GRANT_NOT_FOUND,
        detail=f"Активный grant плагина {plugin_id} у компании {company_id} не найден",
        status=404,
        extra={"company_id": company_id, "plugin_id": plugin_id},
    )


def revoke_failed(*, grant_id: str, reason: str) -> PluginAdminError:
    """Реестр отзывов не подтвердил запись — токен считается НЕ отозванным."""

    return PluginAdminError(
        code=PluginAdminErrorCode.REVOKE_FAILED,
        detail=f"Не удалось отозвать grant {grant_id}: {reason}",
        status=503,
        extra={"grant_id": grant_id},
    )


def uninstall_partial(*, company_id: str, plugin_id: str, failed_steps: tuple[str, ...]) -> PluginAdminError:
    """Частичное удаление инфраструктуры (§5.5). Состояние требует ручной проверки."""

    return PluginAdminError(
        code=PluginAdminErrorCode.UNINSTALL_PARTIAL,
        detail=(
            f"Удаление плагина {plugin_id} у компании {company_id} выполнено частично: "
            f"не удались шаги {', '.join(failed_steps)}"
        ),
        status=503,
        extra={
            "company_id": company_id,
            "plugin_id": plugin_id,
            "failed_steps": list(failed_steps),
        },
    )


__all__ = [
    "PluginAdminError",
    "PluginAdminErrorCode",
    "artifact_not_published",
    "grant_exists",
    "grant_not_found",
    "revoke_failed",
    "uninstall_partial",
]
