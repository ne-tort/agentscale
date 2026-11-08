"""Чистые функции имён для tenant-БД плагинов (F3, §2.1/§2.2/§5.4/§3.3).

Это ЕДИНСТВЕННОЕ место, где пользовательские ``company_id`` / ``plugin_id``
превращаются в SQL-идентификаторы. Отсюда жёсткие правила:

* Никакой конкатенации непроверенных строк в DDL. ``sql.py`` получает уже
  нормализованные имена и дополнительно перепроверяет их через
  ``assert_safe_identifier`` (defense in depth).
* Разрешены только ``[a-z0-9_]``. Всё остальное — ЯВНАЯ ОШИБКА, а не
  «молча заменим на подчёркивание»: тихая нормализация создаёт коллизии
  (``co_1`` и ``co-1`` → одна БД) и маскирует попытку инъекции.
* Длина результата ≤ 63 байта — лимит идентификатора PostgreSQL (NAMEDATALEN).
  Проверяется в UTF-8, а не в символах: не-ASCII отсекается правилом выше,
  но явная проверка защищает от будущих правок.
* Имена детерминированы: одинаковый ввод → одинаковый результат. Никаких
  хэшей/случайности/состояния, чтобы ``revoke``/``uninstall`` всегда били
  в тот же объект, что и ``grant``.

Схема имени (без версии плагина! §3.3):
  БД       ``tenant_<company_norm>``
  роль     ``tenant_<company_norm>_plugin_<plugin_norm>``
  схема    ``module_<plugin_norm>``
  bucket   ``tenant-<company_norm>``

Версия плагина в имя НЕ входит: иначе при обновлении данные останутся
в старой схеме. Версия живёт в DATABASECHANGELOG и в пути статики (§3.3).
"""

from __future__ import annotations

import re

from prodavan.application.plugins.tenantdb.errors import (
    INVALID_IDENTIFIER,
    TenantDbError,
)

# region Константы
PG_MAX_IDENTIFIER_BYTES = 63
"""Лимит PostgreSQL NAMEDATALEN-1. Действует на БД, роль, схему, таблицу."""

# Строгое разрешающее правило. Намеренно НЕ ``[A-Z]``/``-``/``.``/``;``:
# ``co_123; DROP DATABASE prodavan`` должен быть ОТВЕРГНУТ, а не «почищен».
_IDENT_RE = re.compile(r"^[a-z0-9_]+$")

# Шаблоны-заменители для недопустимых символов НЕ используются — см. докстринг.
# Regex оставлен «полным совпадением» (\A..\Z через ^$ + fullmatch), чтобы
# исключить обход через перевод строки (re.MULTILINE здесь не включён).

_PREFIX_DATABASE = "tenant_"
_PREFIX_SCHEMA = "module_"
_ROLE_INFIX = "_plugin_"
_PREFIX_BUCKET = "tenant-"

# Внутренние константы для ``kind`` в ошибке. Не пользовательский ввод.
_KIND_COMPANY = "company_id"
_KIND_PLUGIN = "plugin_id"
# endregion


def _validate_raw(raw: object, *, kind: str, operation: str) -> str:
    """Проверить исходный id и вернуть его в нижнем регистре.

    fail-closed: пустое/не-строка/не-ASCII/запрещённые символы →
    ``TenantDbError(INVALID_IDENTIFIER)``. Исходное значение НИКОГДА не
    подставляется в текст ошибки (защита от отражения инъекции в логи).
    """
    if not isinstance(raw, str):
        raise TenantDbError.invalid_identifier(operation, kind=kind)
    value = raw.strip().lower()
    if not value:
        raise TenantDbError.invalid_identifier(operation, kind=kind)
    if _IDENT_RE.fullmatch(value) is None:
        raise TenantDbError.invalid_identifier(operation, kind=kind)
    # Дополнительная проверка на случай будущего ослабления regex:
    # идентификатор обязан быть ASCII и не содержать кавычек/точек/пробелов.
    if not value.isascii():
        raise TenantDbError.invalid_identifier(operation, kind=kind)
    return value


def _assert_limit(name: str, *, operation: str, kind: str) -> str:
    """Убедиться, что итоговое имя ≤ 63 байт, иначе ЯВНАЯ ошибка.

    Намеренно НЕ обрезаем: усечение ``[:63]`` (как в legacy
    ``tenant_infra/userdb_service.py::_schema_name``) молча порождает
    коллизии имён — две разные компании могут получить одну БД.
    """
    if len(name.encode("utf-8")) > PG_MAX_IDENTIFIER_BYTES:
        raise TenantDbError.invalid_identifier(operation, kind=kind)
    return name


def normalize_identifier(raw: object, *, operation: str = "naming", kind: str = "identifier") -> str:
    """Провалидировать исходный id и вернуть нормализованное значение.

    Публичная точка входа для внешних вызовов (например, для проверки
    ``search_path`` в тестах). Внутренние функции используют ``_validate_raw``
    с конкретным ``kind``.

    НЕ «санитайзер» в смысле «починить и продолжить»: это валидатор.
    """
    value = _validate_raw(raw, kind=kind, operation=operation)
    return _assert_limit(value, operation=operation, kind=kind)


def assert_safe_identifier(name: object, *, operation: str = "sql") -> str:
    """Перепроверить УЖЕ собранное имя перед подстановкой в DDL (defense in depth).

    ``sql.py`` вызывает это для каждого идентификатора. Если сюда попал
    результат ``re.sub``/f-строки с пользовательскими данными — будет
    ``INVALID_IDENTIFIER``, а не выполненный DDL.
    """
    if not isinstance(name, str) or not name:
        raise TenantDbError.invalid_identifier(operation, kind="sql identifier")
    if not name.isascii() or _IDENT_RE.fullmatch(name) is None:
        raise TenantDbError.invalid_identifier(operation, kind="sql identifier")
    if len(name.encode("utf-8")) > PG_MAX_IDENTIFIER_BYTES:
        raise TenantDbError.invalid_identifier(operation, kind="sql identifier")
    return name


# region Публичные функции имён
def tenant_db_name(company_id: object) -> str:
    """``tenant_<norm>`` — имя отдельной БД компании (§2.1, §13.2)."""
    op = "tenant_db_name"
    norm = _validate_raw(company_id, kind=_KIND_COMPANY, operation=op)
    return _assert_limit(f"{_PREFIX_DATABASE}{norm}", operation=op, kind=_KIND_COMPANY)


def plugin_role_name(company_id: object, plugin_id: object) -> str:
    """``tenant_<company>_plugin_<plugin>`` — LOGIN-роль плагина (§2.2).

    Роль уникальна в паре (компания, плагин): один и тот же плагин у двух
    компаний получает разные роли и разные пароли, поэтому утечка DSN
    одной компании не даёт доступа к БД другой.
    """
    op = "plugin_role_name"
    company = _validate_raw(company_id, kind=_KIND_COMPANY, operation=op)
    plugin = _validate_raw(plugin_id, kind=_KIND_PLUGIN, operation=op)
    return _assert_limit(
        f"{_PREFIX_DATABASE}{company}{_ROLE_INFIX}{plugin}",
        operation=op,
        kind=f"{_KIND_COMPANY}/{_KIND_PLUGIN}",
    )


def plugin_schema_name(plugin_id: object) -> str:
    """``module_<norm>`` — схема плагина БЕЗ версии (§2.1, §3.3).

    ВАЖНО: версия в имени запрещена. При обновлении плагин мигрирует
    ту же схему через DATABASECHANGELOG; данные не остаются в «старой»
    версии. ``plugin_id`` вида ``mod_equipment_v2`` — это валидный
    идентификатор, но это ID плагина, а не номер версии.
    """
    op = "plugin_schema_name"
    plugin = _validate_raw(plugin_id, kind=_KIND_PLUGIN, operation=op)
    return _assert_limit(f"{_PREFIX_SCHEMA}{plugin}", operation=op, kind=_KIND_PLUGIN)


def bucket_name(company_id: object) -> str:
    """``tenant-<norm>`` — bucket-на-компанию (§5.4, bucket-per-tenant).

    Для MinIO/S3 допустим дефис, поэтому префикс здесь ``tenant-``, а не
    ``tenant_``. Правило валидации то же: только ``[a-z0-9_]`` во входных
    данных; дефис добавляется только как константный разделитель.
    Реализация bucket/STS — соседняя задача 06, здесь только имя.
    """
    op = "bucket_name"
    company = _validate_raw(company_id, kind=_KIND_COMPANY, operation=op)
    return _assert_limit(f"{_PREFIX_BUCKET}{company}", operation=op, kind=_KIND_COMPANY)


def plugin_search_path(plugin_id: object) -> str:
    """Значение ``search_path`` роли: имя схемы БЕЗ версии (§2.2).

    Возвращает ровно ``module_<plugin>`` — без запятых, без ``public``,
    без кавычек и без версии. Используется и в ``ALTER ROLE ... SET``,
    и в тесте «search_path без версии плагина».
    """
    return plugin_schema_name(plugin_id)
# endregion


# region Служебные константы для сервиса/тестов
MAX_COMPANY_ID_BYTES = PG_MAX_IDENTIFIER_BYTES - len(_PREFIX_DATABASE.encode("utf-8"))
"""Максимальная длина ``company_id``, при которой имя БД ещё ≤ 63 байт."""

MAX_PLUGIN_ID_BYTES = PG_MAX_IDENTIFIER_BYTES - len(_PREFIX_SCHEMA.encode("utf-8"))
"""Максимальная длина ``plugin_id`` для имени схемы ≤ 63 байт."""

MAX_ROLE_COMPANY_PLUGIN_BYTES = (
    PG_MAX_IDENTIFIER_BYTES
    - len(_PREFIX_DATABASE.encode("utf-8"))
    - len(_ROLE_INFIX.encode("utf-8"))
)
"""Суммарный лимит ``len(company)+len(plugin)`` для имени роли ≤ 63 байт."""

BUCKET_MAX_IDENTIFIER_BYTES = PG_MAX_IDENTIFIER_BYTES
"""Для bucket лимит тот же (63) — запас под S3-совместимые имена."""

ERROR_CODE_INVALID_IDENTIFIER = INVALID_IDENTIFIER
"""Реэкспорт для удобства тестов: ошибок именования ровно одна."""
# endregion
