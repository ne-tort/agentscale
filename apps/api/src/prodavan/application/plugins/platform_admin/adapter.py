"""``BearerPlatformAdminAdapter`` — реализация ``PlatformAdminPort`` (задача 12/F11).

Переиспользует **существующий** auth-механизм репозитория, не дублируя логику
верификации JWT:

* ``prodavan.infrastructure.auth.jwt.get_jwt_validator().validate(token)`` —
  JWKS/RS256 (или test HS256) проверка подписи, ``exp``/``sub``/``aud``/``issuer``;
  возвращает ``domain.identity.Principal`` с ``roles`` (там же добавляется
  ``ROLE_PLATFORM_ADMIN`` из claim ``platform_admin``);
* роль platform-admin проверяется тем же правилом, что и в
  ``prodavan.api.deps.require_platform_admin`` (``Principal.is_platform_admin``),
  и **дополнительно** роутером ``api/v1/plugin_admin.py`` через
  ``AdminIdentity.is_platform_admin`` — защита в глубину.

Адаптер НЕ импортирует ``api.deps`` как FastAPI-зависимость (она требует
``Request`` + Depends-граф и тянет запись auth-сессии в БД), а повторяет только
**решение** по роли над уже верифицированным principal. Сама верификация — ровно
один вызов существующего валидатора; своего разбора JWT, JWKS, подписи,
``exp``/``aud`` здесь нет (проверяется отдельным unit-тестом).

Fail-closed (v4 §7.2, §0.5 плана миграции):

==============================  =========================================
Ситуация                        Результат
==============================  =========================================
нет заголовка / не Bearer       ``AppError`` 401 ``UNAUTHORIZED``
пустой токен после ``Bearer``   ``AppError`` 401 ``UNAUTHORIZED``
невалидный/истёкший JWT         ``AppError`` 401 (пробрасываем как есть)
нет роли ``platform.admin``     ``AppError`` 403 ``FORBIDDEN``
валидатор недоступен/упал       ``AppError`` 401 ``UNAUTHORIZED``
валидный platform-admin         ``AdminIdentity`` с непустым ``actor_id``
==============================  =========================================

Ни одного «пустого успеха»: адаптер никогда не возвращает ``AdminIdentity``
с ``is_platform_admin=False`` или пустым ``actor_id`` — только явный отказ.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from fastapi import Request

from prodavan.application.plugins.admin.contracts import AdminIdentity
from prodavan.application.plugins.admin.ports import PlatformAdminPort
from prodavan.domain.errors import AppError
from prodavan.infrastructure.auth.jwt import get_jwt_validator

from .ports import BEARER_SCHEME, PLATFORM_ADMIN_ROLE, PlatformAdminContext, Principal

logger = logging.getLogger(__name__)

#: Префикс заголовка ``Authorization``; сравниваем со схемой без регистра.
_BEARER_PREFIX = f"{BEARER_SCHEME} "


@runtime_checkable
class TokenValidatorPort(Protocol):
    """Структурный минимум существующего ``JwtValidator`` (для инъекции в тестах)."""

    def validate(self, token: str) -> object: ...


class BearerPlatformAdminAdapter:
    """Извлекает Bearer из ``Request`` и проверяет PlatformAdmin через репозиторий.

    ``validator`` по умолчанию — синглтон ``get_jwt_validator()`` из
    ``infrastructure/auth/jwt.py``. Инъекция нужна только unit-тестам, чтобы
    доказать, что адаптер вызывает **существующую** функцию, а не свой разбор
    токена; в проде всегда используется настоящий validators.

    Экземпляр реализует ``PlatformAdminPort`` (структурный ``Protocol``):
    единственный метод ``require_platform_admin(authorization=...)``.
    """

    def __init__(self, validator: TokenValidatorPort | None = None) -> None:
        self._validator = validator

    # ------------------------------------------------------------------ порт

    async def require_platform_admin(
        self, *, authorization: str | None
    ) -> AdminIdentity:
        """Проверить PlatformAdmin и вернуть ``AdminIdentity`` для аудита.

        Сигнатура совпадает с ``admin/ports.py::PlatformAdminPort``: роутер
        ``plugin_admin.py`` передаёт сырой ``Authorization`` заголовок.
        """

        context = self._verify(authorization=authorization)
        # Explicit assertion: _verify либо бросил AppError, либо вернул
        # полностью верифицированный контекст platform-админа.
        identity = AdminIdentity(
            actor_id=context.subject,
            is_platform_admin=context.is_platform_admin,
            scopes=tuple(sorted(context.scopes)),
        )
        if not identity.is_platform_admin or not identity.actor_id.strip():
            # Защита от регрессии: даже если контекст собран вручную, «пустой
            # успех» наружу не уходит.
            raise _deny_platform_admin()
        return identity

    # ------------------------------------------------------------------ извлечение Bearer

    def bearer_from_request(self, request: Request) -> str:
        """Достать токен из ``Request`` (fail-closed: нет/не Bearer → 401).

        Публичный метод: родитель/тесты могут извлечь Bearer из реального
        ``fastapi.Request`` до вызова порта. ``plugin_admin.py`` передаёт порту
        уже строку заголовка, поэтому дублировать чтение заголовка там не нужно.
        """

        return self._extract_bearer(request.headers.get("Authorization"))

    @staticmethod
    def _extract_bearer(authorization: str | None) -> str:
        """Строгая проверка схемы ``Authorization`` без «мягких» фолбэков.

        * ``None``/пустая строка → 401;
        * другая схема (``Basic``, ``Token`` …) → 401;
        * ``Bearer`` без токена / с пробелами → 401;
        * лишние пробелы вокруг токена обрезаются, но токен не модифицируется.
        """

        if authorization is None:
            raise _unauthorized("Отсутствует заголовок Authorization: Bearer token required")
        raw = authorization.strip()
        if not raw:
            raise _unauthorized("Пустой заголовок Authorization: Bearer token required")
        if len(raw) < len(_BEARER_PREFIX) or raw[: len(_BEARER_PREFIX)].lower() != _BEARER_PREFIX.lower():
            raise _unauthorized("Ожидается схема Authorization: Bearer")
        token = raw[len(_BEARER_PREFIX) :].strip()
        if not token:
            raise _unauthorized("Отсутствует токен после схемы Bearer")
        # Токен — компактная base64url-последовательность; пробелы внутри
        # означали бы склейку нескольких токенов. Отказываем, а не «чиним».
        if any(ch.isspace() for ch in token):
            raise _unauthorized("Некорректный формат Bearer-токена")
        return token

    # ------------------------------------------------------------------ верификация

    def _verify(self, *, authorization: str | None) -> PlatformAdminContext:
        """Извлечь Bearer → вызвать СУЩЕСТВУЮЩИЙ валидатор → проверить роль."""

        token = self._extract_bearer(authorization)

        # Единственная точка верификации JWT. Никакого jwt.decode / PyJWKClient /
        # проверки exp/aud/issuer в этом модуле: всё делает репозиторский
        # JwtValidator.validate (infrastructure/auth/jwt.py).
        validator = self._validator if self._validator is not None else get_jwt_validator()
        try:
            domain_principal = validator.validate(token)
        except AppError:
            # Невалидный/истёкший токен: валидатор уже сформировал 401 c
            # безопасным detail. Пробрасываем как есть, не подменяя на успех.
            raise
        except Exception as exc:  # noqa: BLE001 - недоступная auth ≠ успех
            logger.warning(
                "platform_admin_validator_failed error=%s", type(exc).__name__
            )
            raise _unauthorized("PlatformAdmin-проверка недоступна") from exc

        if domain_principal is None:
            # Валидатор вернул None вместо исключения — трактуем как отказ.
            raise _unauthorized("Валидатор не подтвердил токен")

        try:
            # Проекция верифицированного ``domain.identity.Principal`` в наш
            # доменный тип. Непредвиденная форма результата (нет sub/roles) —
            # тоже отказ, а не «разберём как получится».
            principal = Principal.from_domain(domain_principal)
            context = PlatformAdminContext.verified(principal)
        except (ValueError, TypeError) as exc:
            logger.warning("platform_admin_principal_invalid error=%s", type(exc).__name__)
            raise _unauthorized("Валидатор вернул некорректный Principal") from exc

        # Та же проверка роли, что в api/deps.py::require_platform_admin:
        # ROLE_PLATFORM_ADMIN in principal.roles. Роутер plugin_admin.py
        # дополнительно проверит AdminIdentity.is_platform_admin.
        if PLATFORM_ADMIN_ROLE not in context.scopes or not context.is_platform_admin:
            raise _deny_platform_admin()
        return context


# --------------------------------------------------------------------------- ошибки


def _unauthorized(detail: str) -> AppError:
    """401 в формате ``AppError`` → problem+json через зарегистрированный handler."""

    return AppError(
        code="UNAUTHORIZED",
        title="Unauthorized",
        status=401,
        detail=detail,
    )


def _deny_platform_admin() -> AppError:
    """403: токен валиден, но роли platform.admin нет (не «пустить всех»)."""

    return AppError(
        code="FORBIDDEN",
        title="Forbidden",
        status=403,
        detail="platform.admin required",
    )


__all__ = [
    "BearerPlatformAdminAdapter",
    "TokenValidatorPort",
]
