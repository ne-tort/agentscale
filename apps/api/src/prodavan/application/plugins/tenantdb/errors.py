"""Ошибки подсистемы tenant-БД плагинов (F3).

Fail-closed: любая нештатная ситуация — это ``TenantDbError``, а не
молчаливый «успех». Коды стабильны и предназначены для маппинга в
problem+json на уровне Admin API (родительская интеграция, F12).

Важно (§11 критерий 11, redaction): в ``detail``/логи НЕ должны попадать
DSN, пароли и сырой SQL с параметрами. Поэтому текст ``detail`` здесь
всегда формируется из *безопасных* частей: кода операции и уже
провалидированного идентификатора. Сырые пользовательские строки,
которые не прошли валидацию, в сообщение не подставляются вообще —
см. ``TenantDbError.invalid_identifier``.
"""

from __future__ import annotations

# region Коды ошибок (стабильный контракт)
# Используются как machine-readable ``code`` в problem+json.

INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
"""Идентификатор компании/плагина пустой, невалидный или превышает лимит PG."""

ADMIN_UNAVAILABLE = "ADMIN_UNAVAILABLE"
"""Нет админского соединения к tenant-кластеру или оно отказало."""

ALREADY_EXISTS = "ALREADY_EXISTS"
"""Объект уже существует, а операция требует его отсутствия."""

NOT_FOUND = "NOT_FOUND"
"""БД/роль/схема не найдены там, где операция требует их наличия."""

PRIVILEGE_DENIED = "PRIVILEGE_DENIED"
"""PostgreSQL отклонил DDL по привилегиям (SQLSTATE 42501 и аналоги)."""

ROTATION_REQUIRED = "ROTATION_REQUIRED"
"""DSN secret отсутствует/не совпадает; ensure не выполняет скрытую ротацию."""
# endregion

ALL_CODES: frozenset[str] = frozenset(
    {
        INVALID_IDENTIFIER,
        ADMIN_UNAVAILABLE,
        ALREADY_EXISTS,
        NOT_FOUND,
        PRIVILEGE_DENIED,
        ROTATION_REQUIRED,
    }
)
"""Полный набор кодов — для проверки контракта в тестах и в CI."""

# Маппинг «код → HTTP-статус». Здесь только подсказка для слоя API;
# сам сервис не знает про HTTP и не импортирует FastAPI.
_HTTP_STATUS: dict[str, int] = {
    INVALID_IDENTIFIER: 422,
    ADMIN_UNAVAILABLE: 503,
    ALREADY_EXISTS: 409,
    NOT_FOUND: 404,
    PRIVILEGE_DENIED: 403,
    ROTATION_REQUIRED: 409,
}

_TITLES: dict[str, str] = {
    INVALID_IDENTIFIER: "Invalid tenant identifier",
    ADMIN_UNAVAILABLE: "Tenant admin connection unavailable",
    ALREADY_EXISTS: "Tenant database object already exists",
    NOT_FOUND: "Tenant database object not found",
    PRIVILEGE_DENIED: "Privilege denied by PostgreSQL",
    ROTATION_REQUIRED: "Explicit tenant DSN rotation required",
}


class TenantDbError(Exception):
    """Типизированная ошибка операций с tenant-БД.

    Наследует ``Exception`` напрямую, чтобы модуль ``application/plugins``
    не зависел от доменного ``AppError`` и от веб-слоя. Родительский Admin API
    (F12) маппит ``code`` → ``AppError``/problem+json по ``http_status``.

    Нарочно НЕ dataclass: ``@dataclass(frozen=True, slots=True)`` поверх
    ``Exception`` ломает установку ``__traceback__``/``__context__`` и
    pickle — это известная ловушка. Обычный класс с ``__slots__``-safe
    атрибутами здесь надёжнее.

    Поля:
      * ``code`` — один из констант выше;
      * ``operation`` — имя операции сервиса (например ``grant_plugin``);
      * ``detail`` — безопасное описание без DSN/пароля/сырого SQL;
      * ``http_status`` — рекомендуемый статус для API-слоя;
      * ``title`` — заголовок для problem+json.
    """

    def __init__(
        self,
        *,
        code: str,
        operation: str,
        detail: str,
        http_status: int = 400,
    ) -> None:
        # Защита от опечатки: код ошибки должен быть из закрытого набора.
        if code not in ALL_CODES:
            raise ValueError(f"unknown TenantDbError code: {code!r}")
        self.code = code
        self.operation = operation
        self.detail = detail
        self.http_status = http_status
        self.title = _TITLES[code]
        # В ``args`` кладём только безопасную строку: при логировании
        # исключения «как есть» туда не утекут DSN/пароль.
        super().__init__(f"[{code}] {operation}: {detail}")

    def __str__(self) -> str:
        # Никаких секретов: только код, операция и безопасный detail.
        return f"[{self.code}] {self.operation}: {self.detail}"

    # region Фабрики — гарантируют безопасный detail
    @classmethod
    def invalid_identifier(cls, operation: str, *, kind: str) -> TenantDbError:
        """Невалидный id. САМ id в сообщение НЕ кладём (защита от инъекции в логи).

        ``kind`` — это внутренняя константа вызывающего кода («company_id»,
        «plugin_id»), а не пользовательская строка.
        """
        return cls(
            code=INVALID_IDENTIFIER,
            operation=operation,
            detail=f"invalid or empty {kind}; допустимо [a-z0-9_], длина <= 63",
            http_status=_HTTP_STATUS[INVALID_IDENTIFIER],
        )

    @classmethod
    def admin_unavailable(cls, operation: str, *, reason: str = "no admin connection") -> TenantDbError:
        """Нет админского соединения — fail-closed, НЕ «успех».

        ``reason`` — короткая внутренняя константа («not configured»,
        «execute failed»), без DSN, хоста и пароля.
        """
        return cls(
            code=ADMIN_UNAVAILABLE,
            operation=operation,
            detail=f"admin connection unavailable ({reason})",
            http_status=_HTTP_STATUS[ADMIN_UNAVAILABLE],
        )

    @classmethod
    def already_exists(cls, operation: str, *, object_kind: str, safe_name: str) -> TenantDbError:
        """Объект уже есть. ``safe_name`` — УЖЕ нормализованный идентификатор."""
        return cls(
            code=ALREADY_EXISTS,
            operation=operation,
            detail=f"{object_kind} already exists: {safe_name}",
            http_status=_HTTP_STATUS[ALREADY_EXISTS],
        )

    @classmethod
    def not_found(cls, operation: str, *, object_kind: str, safe_name: str) -> TenantDbError:
        """Объект отсутствует. ``safe_name`` — УЖЕ нормализованный идентификатор."""
        return cls(
            code=NOT_FOUND,
            operation=operation,
            detail=f"{object_kind} not found: {safe_name}",
            http_status=_HTTP_STATUS[NOT_FOUND],
        )

    @classmethod
    def privilege_denied(cls, operation: str, *, safe_name: str) -> TenantDbError:
        """DDL отклонён по привилегиям (42501). SQLSTATE — безопасная константа."""
        return cls(
            code=PRIVILEGE_DENIED,
            operation=operation,
            detail=f"privilege denied for {safe_name} (SQLSTATE 42501)",
            http_status=_HTTP_STATUS[PRIVILEGE_DENIED],
        )
    @classmethod
    def rotation_required(cls, operation: str, *, safe_name: str) -> TenantDbError:
        """Ensure обнаружил отсутствующий/чужой DSN; rotate должен быть явным."""
        return cls(
            code=ROTATION_REQUIRED,
            operation=operation,
            detail=f"tenant DSN secret missing or mismatched for {safe_name}",
            http_status=_HTTP_STATUS[ROTATION_REQUIRED],
        )
    # endregion
