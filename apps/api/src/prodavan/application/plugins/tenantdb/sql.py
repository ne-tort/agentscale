"""Генератор DDL для tenant-БД плагинов (F3, §2.1/§2.2/§5.5).

Контракт модуля
---------------
Каждая функция возвращает ``DdlStatement(text, params)``:

* ``text``  — SQL с **параметрами** (``:name``) там, где можно параметризовать;
* ``params`` — dict значений.

Значения НИКОГДА не подставляются в SQL через f-строку. Исключение —
**идентификаторы** (имя БД/роли/схемы): PostgreSQL не умеет биндить их
в DDL, поэтому они вставляются как ``"quoted ident"``, но ТОЛЬКО после
``assert_safe_identifier`` (правило ``^[a-z0-9_]+$``, ≤63 байта). Это и
есть защита от инъекций: ``co_123; DROP DATABASE prodavan`` будет
отвергнут ещё в ``naming.py``, а ``sql.py`` перепроверяет повторно.

Пароли и DSN в DDL
------------------
``CREATE ROLE ... PASSWORD`` оставляем текстовым литералом в ``text``,
но экранируем одинарные кавычки (удвоение) — параметризация пароля
в ``CREATE ROLE`` невозможна. Пароль приходит из генератора сервиса,
а НЕ от пользователя. ``text`` НЕ должен логироваться: ``DdlStatement.__repr__``
и ``safe_repr`` намеренно скрывают ``params`` и вырезают литерал пароля.

Админский порт
--------------
``CREATE DATABASE`` / ``DROP DATABASE`` / ``CREATE ROLE`` / ``DROP ROLE``
выполняются через **отдельный админский порт** (``AdminConnectionPort``),
не через соединение плагина и не через соединение к ``prodavan``.
``CREATE DATABASE`` идемпотентен через
``SELECT 1 FROM pg_database WHERE datname = :name`` (образец —
``infra/k3s/overlays/dev/userdb-init.yaml``).

Что НЕ является границей (явно зафиксировано, §2.4 / §13.1)
-----------------------------------------------------------
* **RLS не используется как граница изоляции.** При БД-на-компанию RLS
  по ``tenant_id`` защищал бы от той же компании — бессмысленно;
  ``ENABLE ROW LEVEL SECURITY`` без ``FORCE`` не действует на владельца,
  а владелец схемы здесь — сам плагин. Межтенантовая граница =
  отдельная БД + роль + ``CONNECT`` + ``pg_hba.conf`` + NetworkPolicy.
  Ни одного ``ENABLE ROW LEVEL SECURITY`` / ``CREATE POLICY`` здесь нет.
* **GUC ``app.tenant_id`` не применяется.** Плагин является владельцем
  сессии и может пере-set'ить любой ``app.*`` GUC, поэтому доверять ему
  нельзя. Ни ``SET app.tenant_id``, ни ``ALTER ROLE ... SET app.*`` здесь нет.
* ``pgaudit`` — это **наблюдение** (append-only лог DDL/DML, §11 критерий 5),
  а НЕ замена привилегий и НЕ средство запрета. Ни один ``GRANT``/``REVOKE``
  здесь не подменяется «аудитом». DDL фиксируется на tenant-кластере
  инфраструктурой (задача 14), а не этим модулем.
* ``statement_timeout='30s'`` / ``lock_timeout='10s'`` — предохранители
  сессии, а НЕ rate limit и НЕ защита (§13.1).
* ``CONNECTION LIMIT 10`` — лимит сессий, а не размер БД (§13.1).

PUBLIC-привилегии (важно, см. переписку с родителем)
-----------------------------------------------------
PostgreSQL по умолчанию даёт ``PUBLIC`` права ``CONNECT, TEMPORARY`` на
каждую БД (``acldefault('d', ...)``). Поэтому одного
``GRANT CONNECT ON DATABASE tenant_x TO role`` НЕДОСТАТОЧНО для §11.2:
роль сможет подключиться и к ``prodavan``, и к чужой ``tenant_*``.
Функция ``revoke_public_connect`` ниже — **заготовка для bootstrap
tenant-кластера (задача 14), НЕ вызывается сервисом**: до её применения
надо выдать ``CONNECT`` админской роли, иначе платформа потеряет доступ
к БД компании для ``CREATE SCHEMA``. Мгновенный отзыв доступа в сервисе
делается через ``ALTER ROLE ... NOLOGIN`` (не зависит от PUBLIC).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from prodavan.application.plugins.tenantdb.naming import assert_safe_identifier

# region Константы DDL (не пользовательские данные)
_CONNECTION_LIMIT = 10
"""§2.2: CONNECTION LIMIT 10. Лимит СЕССИЙ, не размер БД (§13.1)."""

_STATEMENT_TIMEOUT = "30s"
"""Предохранитель statement, не rate limit (§13.1)."""

_LOCK_TIMEOUT = "10s"
"""Предохранитель ожидания блокировки."""

# Пароль в CREATE ROLE приходит от генератора сервиса; одинарные кавычки
# удваиваются по правилу SQL-литерала. Параметризовать PASSWORD нельзя.
_PASSWORD_LITERAL_RE = re.compile(r"'")

# ``safe_repr`` вырезает литерал пароля, даже если кто-то залогировал statement целиком.
_CREATE_ROLE_PASSWORD_RE = re.compile(r"(PASSWORD\s+')(?:[^']|'')*(')", re.IGNORECASE)
# endregion


@dataclass(frozen=True, slots=True)
class DdlStatement:
    """Одна DDL-команда: текст + параметры.

    ``text`` можно отдавать в ``sqlalchemy.text(...)`` / asyncpg.
    ``params`` содержит только скаляры (str/int), НЕ сырые объекты.

    ``__repr__`` намеренно скрывает ``params``: логи вида
    ``logger.info("ddl=%s", stmt)`` не должны печатать пароли/DSN.
    """

    text: str
    params: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - защитный контракт
        return f"DdlStatement(text={self.safe_text()!r}, params=<redacted>)"

    __str__ = __repr__

    def safe_text(self) -> str:
        """Текст без литерала пароля — безопасно для логов/аудита."""
        return _CREATE_ROLE_PASSWORD_RE.sub(r"\1***\2", self.text)

    def safe_repr(self) -> str:
        """Полная безопасная строка для аудита: SQL без пароля + имена params.

        Значения параметров НЕ печатаются. Ключи параметров — это фиксированные
        внутренние имена (``name``, ``rolname``, ...), они не секретны и
        полезны для расследования.
        """
        return (
            f"DdlStatement(text={self.safe_text()!r}, "
            f"param_keys={sorted(self.params.keys())!r})"
        )


def _qi(identifier: object, *, operation: str) -> str:
    """Quote identifier: ``foo`` → ``"foo"``, только после строгой проверки.

    Это единственное место во всём модуле, где идентификатор попадает в
    текст SQL. f-строка здесь содержит ТОЛЬКО результат ``assert_safe_identifier``
    (набор ``[a-z0-9_]``), а не пользовательский ввод.
    """
    safe = assert_safe_identifier(identifier, operation=operation)
    # Двойные кавычки внутри невозможны: ascii + [a-z0-9_] их не содержит.
    return f'"{safe}"'


def _quote_literal(value: str) -> str:
    """Экранировать строковый литерал (для PASSWORD, search_path, таймаутов)."""
    return "'" + _PASSWORD_LITERAL_RE.sub("''", value) + "'"


# region Проверки существования (идемпотентность)
def database_exists(name: object) -> DdlStatement:
    """``SELECT 1 FROM pg_database WHERE datname = :name`` (образец userdb-init.yaml).

    Выполняется на админском соединении к служебной БД кластера.
    """
    safe = assert_safe_identifier(name, operation="database_exists")
    return DdlStatement(
        text="SELECT 1 FROM pg_database WHERE datname = :name",
        params={"name": safe},
    )


def role_exists(name: object) -> DdlStatement:
    """``SELECT 1 FROM pg_roles WHERE rolname = :rolname``."""
    safe = assert_safe_identifier(name, operation="role_exists")
    return DdlStatement(
        text="SELECT 1 FROM pg_roles WHERE rolname = :rolname",
        params={"rolname": safe},
    )


def role_login_enabled(name: object) -> DdlStatement:
    """Post-state probe for ALTER ROLE LOGIN/NOLOGIN."""
    safe = assert_safe_identifier(name, operation="role_login_enabled")
    return DdlStatement(
        text="SELECT 1 FROM pg_roles WHERE rolname = :rolname AND rolcanlogin",
        params={"rolname": safe},
    )


def database_connect_granted(database: object, role: object) -> DdlStatement:
    """Confirm an explicit role CONNECT ACL, excluding PUBLIC-only access."""
    db = assert_safe_identifier(database, operation="database_connect_granted")
    role_name = assert_safe_identifier(role, operation="database_connect_granted")
    return DdlStatement(
        text=(
            "SELECT 1 FROM pg_database AS db WHERE db.datname = :name AND EXISTS ("
            "SELECT 1 FROM aclexplode(COALESCE(db.datacl, acldefault('d', db.datdba))) AS acl "
            "JOIN pg_roles AS grantee ON grantee.oid = acl.grantee "
            "WHERE grantee.rolname = :role AND acl.privilege_type = 'CONNECT'"
            ")"
        ),
        params={"name": db, "role": role_name},
    )


def schema_exists(schema: object) -> DdlStatement:
    """``SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema``.

    Выполняется на админском соединении **к БД компании** (схемы локальны
    для БД, в ``pg_namespace`` кластера их не видно).
    """
    safe = assert_safe_identifier(schema, operation="schema_exists")
    return DdlStatement(
        text=(
            "SELECT 1 FROM information_schema.schemata "
            "WHERE schema_name = :schema"
        ),
        params={"schema": safe},
    )
# endregion


# region CREATE — ensure_company_database / grant_plugin
def create_database(name: object, *, owner: object | None = None) -> DdlStatement:
    """``CREATE DATABASE`` через отдельный админский порт (§2.1, §13.2).

    НЕ ``IF NOT EXISTS``: у ``CREATE DATABASE`` такого синтаксиса нет.
    Идемпотентность обеспечивает вызывающий код через ``database_exists``.
    ``owner`` — админская/платформенная роль (не роль плагина), если задана.
    """
    qi = _qi(name, operation="create_database")
    owner_clause = ""
    if owner is not None:
        owner_clause = f" OWNER {_qi(owner, operation='create_database')}"
    return DdlStatement(text=f"CREATE DATABASE {qi}{owner_clause}")


def create_role(name: object, *, password: str) -> DdlStatement:
    """``CREATE ROLE ... LOGIN PASSWORD ...`` — минимальные привилегии (§2.2).

    Обязательный набор (§11 критерий 1, проверяется тестом):
    ``NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 10``.

    Роль НЕ получает ``CREATEDB``/``CREATEROLE``, не может выдать себе
    права и не может обойти RLS (которая здесь и не применяется).
    """
    qi = _qi(name, operation="create_role")
    if not isinstance(password, str) or not password:
        # Пароль обязателен: роль без пароля = неконтролируемый доступ.
        raise ValueError("create_role: password must be a non-empty string")
    pwd_literal = _quote_literal(password)
    return DdlStatement(
        text=(
            f"CREATE ROLE {qi} LOGIN PASSWORD {pwd_literal} "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
            f"CONNECTION LIMIT {_CONNECTION_LIMIT}"
        )
    )


def grant_connect(database: object, role: object) -> DdlStatement:
    """``GRANT CONNECT ON DATABASE ... TO role`` (§2.2).

    ВНИМАНИЕ: это НЕ полная граница изоляции, см. докстринг модуля про
    ``PUBLIC``. Реальная граница = REVOKE ... FROM PUBLIC + pg_hba.conf +
    NetworkPolicy + NOLOGIN при revoke.
    """
    db_qi = _qi(database, operation="grant_connect")
    role_qi = _qi(role, operation="grant_connect")
    return DdlStatement(text=f"GRANT CONNECT ON DATABASE {db_qi} TO {role_qi}")


def create_schema(schema: object, *, owner: object) -> DdlStatement:
    """``CREATE SCHEMA ... AUTHORIZATION role`` — владелец = роль плагина (§2.2).

    Выполняется на админском соединении к БД компании. Плагин затем сам
    делает ``CREATE TABLE``/индексы/FK/миграции под своей ролью — без
    ``_ALLOWED_TYPES`` и без парсера SQL (§2.5, §3).
    """
    schema_qi = _qi(schema, operation="create_schema")
    owner_qi = _qi(owner, operation="create_schema")
    return DdlStatement(text=f"CREATE SCHEMA {schema_qi} AUTHORIZATION {owner_qi}")


def alter_role_search_path(role: object, *, schema: object) -> DdlStatement:
    """``ALTER ROLE ... SET search_path = 'module_<plugin>'`` (§2.2, §3.3).

    Значение **без версии плагина** и без ``public``: чужие схемы компании
    не видны (нет ``USAGE``), CREATE идёт только в свою схему.
    """
    role_qi = _qi(role, operation="alter_role_search_path")
    safe_schema = assert_safe_identifier(schema, operation="alter_role_search_path")
    # search_path — строковый литерал, не идентификатор: параметризовать
    # нельзя в ALTER ROLE .. SET, поэтому экранируем как литерал.
    return DdlStatement(
        text=f"ALTER ROLE {role_qi} SET search_path = {_quote_literal(safe_schema)}"
    )


def alter_role_statement_timeout(role: object) -> DdlStatement:
    """``SET statement_timeout='30s'`` — предохранитель, НЕ rate limit (§13.1)."""
    role_qi = _qi(role, operation="alter_role_statement_timeout")
    return DdlStatement(
        text=(
            f"ALTER ROLE {role_qi} SET statement_timeout = "
            f"{_quote_literal(_STATEMENT_TIMEOUT)}"
        )
    )


def alter_role_lock_timeout(role: object) -> DdlStatement:
    """``SET lock_timeout='10s'`` — предохранитель ожидания блокировки."""
    role_qi = _qi(role, operation="alter_role_lock_timeout")
    return DdlStatement(
        text=f"ALTER ROLE {role_qi} SET lock_timeout = {_quote_literal(_LOCK_TIMEOUT)}"
    )
# endregion


# region REVOKE / DROP — revoke_plugin / uninstall_plugin / drop_company_database
def alter_role_no_login(role: object) -> DdlStatement:
    """``ALTER ROLE ... NOLOGIN`` — мгновенный отзыв доступа (§5.5 revoke).

    Надёжнее, чем снятие одного ``GRANT CONNECT``: ``PUBLIC`` всё ещё
    имеет CONNECT на БД, но роль физически не может открыть сессию.
    БД и схема при revoke СОХРАНЯЮТСЯ (§5.5).
    """
    role_qi = _qi(role, operation="alter_role_no_login")
    return DdlStatement(text=f"ALTER ROLE {role_qi} NOLOGIN")


def alter_role_login(role: object) -> DdlStatement:
    """``ALTER ROLE ... LOGIN`` — восстановление доступа (re-grant после revoke)."""
    role_qi = _qi(role, operation="alter_role_login")
    return DdlStatement(text=f"ALTER ROLE {role_qi} LOGIN")


def alter_role_password(role: object, *, password: str) -> DdlStatement:
    """``ALTER ROLE ... PASSWORD ...`` — ротация пароля при повторном grant.

    ``grant_plugin`` повторный вызов НЕ должен слать ``CREATE ROLE`` второй
    раз (идемпотентность), но обязан ротировать DSN (§5.5). Поэтому новая
    роль создаётся через ``create_role``, а существующая получает новый
    пароль через эту команду. Пароль генерируется сервисом, не пользователем.
    """
    qi = _qi(role, operation="alter_role_password")
    if not isinstance(password, str) or not password:
        raise ValueError("alter_role_password: password must be a non-empty string")
    pwd_literal = _quote_literal(password)
    return DdlStatement(text=f"ALTER ROLE {qi} PASSWORD {pwd_literal}")


def revoke_connect(database: object, role: object) -> DdlStatement:
    """``REVOKE CONNECT ON DATABASE ... FROM role`` — снятие явного grant."""
    db_qi = _qi(database, operation="revoke_connect")
    role_qi = _qi(role, operation="revoke_connect")
    return DdlStatement(text=f"REVOKE CONNECT ON DATABASE {db_qi} FROM {role_qi}")


def revoke_all_on_schema(schema: object, role: object) -> DdlStatement:
    """``REVOKE ALL ON SCHEMA ... FROM role`` — перед DROP ROLE (§5.5 uninstall).

    DROP ROLE не пройдёт, пока на роли висят зависимости в БД компании.
    """
    schema_qi = _qi(schema, operation="revoke_all_on_schema")
    role_qi = _qi(role, operation="revoke_all_on_schema")
    return DdlStatement(text=f"REVOKE ALL ON SCHEMA {schema_qi} FROM {role_qi}")


def revoke_public_connect(database: object) -> DdlStatement:
    """ЗАГОТОВКА для bootstrap tenant-кластера (задача 14). НЕ вызывать из сервиса!

    ``REVOKE CONNECT, TEMPORARY ON DATABASE ... FROM PUBLIC`` закрывает
    дефолтный доступ «любой роли к любой БД» (``acldefault('d', ...)``),
    без которого §11.2 не выполняется.

    ОПАСНО: перед применением надо выдать ``CONNECT`` админской/
    платформенной роли, иначе платформа потеряет доступ к БД компании
    для ``CREATE SCHEMA`` и последующего DDL. Поэтому сервис её НЕ
    применяет; это ответственность bootstrap-манифестов tenant-кластера.
    """
    db_qi = _qi(database, operation="revoke_public_connect")
    return DdlStatement(text=f"REVOKE CONNECT, TEMPORARY ON DATABASE {db_qi} FROM PUBLIC")


def grant_connect_admin(database: object, admin_role: object) -> DdlStatement:
    """Заготовка-компаньон: выдать CONNECT админской роли ДО ``revoke_public_connect``.

    Тоже НЕ вызывается сервисом — используется bootstrap tenant-кластера
    (задача 14) в паре с ``revoke_public_connect``.
    """
    return grant_connect(database, admin_role)


def drop_schema(schema: object, *, cascade: bool = True) -> DdlStatement:
    """``DROP SCHEMA IF EXISTS ... [CASCADE]`` — uninstall (§5.5, §11.15).

    Данные dev эфемерны, snapshot/бэкап НЕ требуется (§5.5, §13.1).
    ``CASCADE`` удаляет таблицы плагина; чужие схемы компании не трогаются,
    потому что имя схемы вычисляется строго из ``plugin_id``.
    """
    qi = _qi(schema, operation="drop_schema")
    suffix = " CASCADE" if cascade else ""
    return DdlStatement(text=f"DROP SCHEMA IF EXISTS {qi}{suffix}")


def drop_role(name: object) -> DdlStatement:
    """``DROP ROLE IF EXISTS ...`` — uninstall (§5.5).

    Роль дропается только ПОСЛЕ ``REVOKE ALL ON SCHEMA`` и ``DROP SCHEMA``,
    иначе PostgreSQL вернёт ``2BP01`` (dependent objects still exist).
    """
    qi = _qi(name, operation="drop_role")
    return DdlStatement(text=f"DROP ROLE IF EXISTS {qi}")


def drop_database(name: object) -> DdlStatement:
    """``DROP DATABASE IF EXISTS ...`` — удаление компании (через админский порт).

    ``DROP DATABASE`` не работает внутри транзакции и требует, чтобы к БД
    не было активных подключений. Для этого существуют ``terminate_backend``.
    """
    qi = _qi(name, operation="drop_database")
    return DdlStatement(text=f"DROP DATABASE IF EXISTS {qi}")


def terminate_role_sessions(role: object) -> DdlStatement:
    """Завершить существующие сессии роли после NOLOGIN/REVOKE."""
    safe = assert_safe_identifier(role, operation="terminate_role_sessions")
    return DdlStatement(
        text=(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE usename = :role AND pid <> pg_backend_pid()"
        ),
        params={"role": safe},
    )


def terminate_backend(database: object) -> DdlStatement:
    """``pg_terminate_backend`` для всех сессий БД перед ``DROP DATABASE``.

    Параметризовано по ``datname``; ``pg_stat_activity`` доступен админской
    роли. Исключаем собственный backend, чтобы не порвать админское
    соединение, если оно вдруг указывает на ту же БД.
    """
    safe = assert_safe_identifier(database, operation="terminate_backend")
    return DdlStatement(
        text=(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :name AND pid <> pg_backend_pid()"
        ),
        params={"name": safe},
    )
# endregion


# region Вспомогательное: безопасная передача в адаптер
def as_execute_args(stmt: DdlStatement) -> tuple[str, Mapping[str, Any]]:
    """``(text, params)`` для ``AdminConnectionPort.execute``.

    Явно напоминает: в лог идёт ``stmt.safe_repr()``, а НЕ ``stmt.text``
    и НЕ ``stmt.params``.
    """
    return stmt.text, dict(stmt.params)
# endregion
