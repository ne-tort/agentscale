"""Доменные типы PlatformAdmin-адаптера (задача: два недостающих адаптера).

Модуль **не зависит** от Pydantic, FastAPI, Starlette и SQLAlchemy: это чистый
доменный слой, который можно использовать и в application-сервисах, и в
инфраструктурном адаптере, и в unit-тестах без поднятия приложения.

Связь с чужими контрактами (их НЕ редактируем):

* ``application.plugins.admin.ports.PlatformAdminPort`` — порт, который ждёт
  ``AdminIdentity`` (frozen dataclass с ``actor_id``/``is_platform_admin``/
  ``scopes``) от ``require_platform_admin(authorization=...)``;
* ``domain.identity.Principal`` — результат верификации JWKS/JWT (репозиторий,
  ``infrastructure/auth/jwt.py``): ``sub`` + ``frozenset[str] roles``;
* ``api.v1.plugin_admin.require_platform_admin`` — HTTP-зависимость, которая дополнительно
  проверяет ``AdminIdentity.is_platform_admin`` и непустой ``actor_id``.

Fail-closed: ``PlatformAdminContext.authenticated`` по умолчанию ``False``;
создать «успешный» контекст без явного указания признака нельзя, ``scopes`` и
``company_id`` по умолчанию пусты/отсутствуют — никаких «разрешить всё»
значений по умолчанию.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

#: Роль PlatformAdmin в репозитории (``domain.identity.ROLE_PLATFORM_ADMIN``).
#: Дублируем строковый литерал намеренно: доменный слой этого адаптера не должен
#: импортировать чужие модули ради одной константы, а значение зафиксировано
#: контрактом Keycloak realm roles. Сверка с ``ROLE_PLATFORM_ADMIN`` есть в тесте.
PLATFORM_ADMIN_ROLE: Final[str] = "platform.admin"

#: Схема ``Authorization`` — единственная поддерживаемая (v4 §7.2, Bearer).
BEARER_SCHEME: Final[str] = "Bearer"


@dataclass(frozen=True, slots=True)
class Principal:
    """Верифицированный субъект в домене адаптера (проекция ``domain.identity.Principal``).

    ``subject`` — авторитетный ``sub`` из проверенного JWT (не из query/body);
    ``scopes`` — неизменяемый набор ролей/скоупов верифицированного токена;
    ``company_id`` опционален: PlatformAdmin работает в контуре платформы и не
    обязан принадлежать какой-либо компании. Если значение пришло из доверенного
    источника (например, ``preferred_username`` company-принципала), оно здесь;
    иначе ``None`` — «нет компании», а не «все компании».

    ``authenticated`` выставляется **только** адаптером после успешного вызова
    существующего ``get_jwt_validator().validate``. Никакой «пустой успех»:
    объект без этого флага не является аутентифицированным.
    """

    subject: str
    company_id: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    authenticated: bool = False

    def __post_init__(self) -> None:
        # Пустой subject не имеет смысла даже в deny-контексте: аудит и роутер
        # требуют непустой actor_id, а «анонимный успех» запрещён.
        if not self.subject.strip():
            raise ValueError("subject верифицированного Principal не должен быть пустым")
        # Защита от случайной подмены изменяемым множеством: frozen dataclass
        # защищает ссылку, но не тип значения. Нормализуем к frozenset.
        if not isinstance(self.scopes, frozenset):
            object.__setattr__(self, "scopes", frozenset(self.scopes))

    @property
    def is_platform_admin(self) -> bool:
        """Есть ли роль platform.admin среди верифицированных scopes.

        Дополнительная (защита в глубину) проверка поверх той, что делает
        ``api/deps.py::require_platform_admin`` и сам роутер ``plugin_admin.py``.
        """

        return self.authenticated and PLATFORM_ADMIN_ROLE in self.scopes

    @classmethod
    def anonymous(cls) -> Principal:
        """Явный НЕаутентифицированный контекст для отказов (fail-closed).

        Используется только в диагностических сообщениях/тестах: наружу адаптер
        такой контекст не возвращает — он бросает ``AppError`` (401/403).
        """

        return cls(subject="anonymous", authenticated=False)

    @classmethod
    def from_domain(
        cls, principal: object, *, company_id: str | None = None
    ) -> Principal:
        """Спроецировать ``domain.identity.Principal`` в домен адаптера.

        Принимаем ``object`` намеренно: доменный слой не импортирует чужой тип,
        а читает только ``sub``/``roles`` уже верифицированного объекта. Вызов
        допустим **только** после ``get_jwt_validator().validate``; сам адаптер
        никогда не разбирает JWT здесь.
        """

        subject = getattr(principal, "sub", None)
        roles = getattr(principal, "roles", ())
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("верифицированный Principal должен иметь непустой sub")
        return cls(
            subject=subject,
            company_id=company_id,
            scopes=frozenset(str(role) for role in roles),
            authenticated=True,
        )


@dataclass(frozen=True, slots=True)
class PlatformAdminContext:
    """Контекст принятия решения адаптером (frozen, без Pydantic/FastAPI).

    ``principal`` — верифицированный субъект (или ``anonymous()`` при отказе);
    ``company_id`` — явная граница компании, если она задана доверенным
    вызывающим (для admin API компания приходит в теле/query, а не отсюда);
    ``scopes`` — скоупы проверенного токена; ``authenticated`` — итог проверки.

    Все поля имеют «закрытые» значения по умолчанию: собрать контекст со
    случайным «всё разрешено» невозможно. ``require_platform_admin`` в
    ``adapter.py`` строит контекст только после успешной верификации.
    """

    principal: Principal = field(default_factory=Principal.anonymous)
    company_id: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    authenticated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.scopes, frozenset):
            object.__setattr__(self, "scopes", frozenset(self.scopes))
        # Консистентность: authenticated=True без аутентифицированного principal
        # означало бы «пустой успех» — запрещаем на уровне типа.
        if self.authenticated and not self.principal.authenticated:
            raise ValueError("authenticated=True требует верифицированного principal")
        if self.company_id is not None and not self.company_id.strip():
            raise ValueError("company_id не должен быть пустой строкой")

    @classmethod
    def verified(
        cls, principal: Principal, *, company_id: str | None = None
    ) -> PlatformAdminContext:
        """Построить контекст **только** из верифицированного principal."""

        if not principal.authenticated:
            raise ValueError("verified() требует principal.authenticated=True")
        return cls(
            principal=principal,
            company_id=company_id,
            scopes=principal.scopes,
            authenticated=True,
        )

    @property
    def is_platform_admin(self) -> bool:
        return self.authenticated and self.principal.is_platform_admin

    @property
    def subject(self) -> str:
        """Удобный доступ к ``sub`` для ``AdminIdentity.actor_id``."""

        return self.principal.subject


__all__ = [
    "BEARER_SCHEME",
    "PLATFORM_ADMIN_ROLE",
    "PlatformAdminContext",
    "Principal",
]
