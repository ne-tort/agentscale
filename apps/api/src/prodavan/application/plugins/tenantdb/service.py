"""``TenantDatabaseService`` — lifecycle tenant-БД плагинов (F3, §2/§3/§5.5).

Ответственность: создать БД компании, выдать плагину роль/схему/DSN,
отозвать доступ, удалить схему и роль, удалить БД компании.

Архитектурные решения
---------------------
* **Порты (Protocol), а не конкретные драйверы.** Сервис не знает про
  SQLAlchemy/asyncpg/k8s Secret/Vault: всё через ``AdminConnectionPort``,
  ``SecretPort``, ``AuditPort``. Реализации подключаются родителем в F11.
  Ни один порт не имеет «тихого» фолбэка: отсутствие интеграции =
  ``TenantDbError(ADMIN_UNAVAILABLE)``, а не «ок, пропустим» (fail-closed).
* **Две зоны админского соединения.** ``CREATE/DROP DATABASE`` и
  ``CREATE/DROP ROLE`` выполняются на кластерном соединении (``scope="cluster"``),
  а ``CREATE/DROP SCHEMA`` — на соединении к конкретной БД компании
  (``scope="database", database=...``). Админский порт сам решает, как
  это разрулить; сервис явно передаёт намерение.
* **DSN только как ``secret_ref``.** Плагин получает НЕ строку DSN, а
  ``TenantDsn.secret_ref``. Plaintext DSN (с паролем) живёт только в
  ``SecretPort`` и ни разу не попадает в ``AuditPort``, в возвращаемые
  логи или в ``Exception`` (§5.5, §11.11).
* **RLS/GUC не применяются** (§2.4), ``pgaudit`` — наблюдение, а не
  граница (§11.5). Подробности — в докстринге ``sql.py``.
* **Идемпотентность.** ``ensure_company_database`` и ``grant_plugin``
  повторный вызов НЕ шлют ``CREATE DATABASE``/``CREATE ROLE`` второй раз:
  сначала ``SELECT 1 ...``, затем DDL. Проверки и DDL идут через один и
  тот же админский порт, чтобы тесты могли это увидеть (см. тесты).
* **revoke ≠ uninstall.** revoke: ``NOLOGIN`` + ``REVOKE CONNECT``,
  БД/схема сохраняются. uninstall: ``DROP SCHEMA`` + ``DROP ROLE``.
  ``drop_company_database`` — только для удаления компании; он НЕ трогает
  чужие БД, потому что имя выводится строго из ``company_id`` (§11.15).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping, Protocol, runtime_checkable
from urllib.parse import parse_qs, quote, urlsplit

from prodavan.application.plugins.tenantdb import sql
from prodavan.application.plugins.tenantdb.errors import TenantDbError
from prodavan.application.plugins.tenantdb.naming import (
    bucket_name,
    plugin_role_name,
    plugin_schema_name,
    plugin_search_path,
    tenant_db_name,
)

# region Типы подключения
AdminScope = Literal["cluster", "database"]
"""``cluster`` — служебная БД кластера (DDL уровня кластера).
``database`` — соединение к БД компании (DDL схем)."""


@dataclass(frozen=True, slots=True)
class AdminTarget:
    """Куда именно выполнять команду.

    ``database`` обязателен при ``scope="database"`` и должен быть пустым
    при ``scope="cluster"`` (проверяется в ``__post_init__``).
    """

    scope: AdminScope
    database: str = ""

    def __post_init__(self) -> None:
        if self.scope == "database" and not self.database:
            raise ValueError("AdminTarget: database required for scope='database'")
        if self.scope == "cluster" and self.database:
            raise ValueError("AdminTarget: database must be empty for scope='cluster'")


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    """Результат ``AdminConnectionPort.execute``.

    ``rows`` — список кортежей/словарей для SELECT; для DDL пустой.
    Адаптер приводит результат драйвера к этому простому виду.
    """

    rows: tuple[Any, ...] = ()

    @property
    def first_value(self) -> Any | None:
        """Первое значение первой строки (для ``SELECT 1 ...``)."""
        if not self.rows:
            return None
        first = self.rows[0]
        if isinstance(first, Mapping):
            return next(iter(first.values()), None)
        if isinstance(first, (tuple, list)):
            return first[0] if first else None
        return first
# endregion


# region Порты (Protocol) — реализации подключаются родителем в F11
@runtime_checkable
class AdminConnectionPort(Protocol):
    """Асинхронное админское соединение к tenant-кластеру PostgreSQL.

    ВАЖНО: это соединение с привилегиями, ОТДЕЛЬНОЕ от роли плагина и
    ОТДЕЛЬНОЕ от платформенной БД ``prodavan``. ``CREATE DATABASE``
    возможен только здесь.

    Реализация обязана:
      * выполнять ``text`` как SQL с параметрами ``params`` (bind, не
        форматирование);
      * НЕ логировать ``params`` (там может быть пароль);
      * пробрасывать ошибку PostgreSQL с ``sqlstate`` наружу — сервис
        маппит 42501 в ``PRIVILEGE_DENIED``;
      * при недоступности поднимать исключение, а не возвращать «пусто».
    """

    async def execute(
        self,
        text: str,
        params: Mapping[str, Any] | None = None,
        *,
        target: AdminTarget,
    ) -> ExecuteResult: ...


@runtime_checkable
class SecretPort(Protocol):
    """Хранилище короткоживущих ``TENANT_DB_DSN`` (§5.5).

    DSN с паролем сохраняется как ``secret_ref``; наружу отдаётся только
    ссылка. Ensure не ротирует; явный rotate только для repair/re-grant.
    """

    async def get(self, *, key: str) -> StoredSecret | None:
        """Прочитать по stable key; plaintext только в памяти, никогда в БД/логи."""
        ...

    async def put(self, *, key: str, value: str) -> str:
        """Сохранить значение, вернуть ``secret_ref`` (не plaintext)."""
        ...

    async def rotate(self, *, key: str, value: str) -> str:
        """Ротация: новый DSN, новый ``secret_ref``. Старый удаляется."""
        ...

    async def delete(self, *, secret_ref: str) -> None:
        """Удалить секрет при uninstall/удалении компании."""
        ...


@runtime_checkable
class AuditPort(Protocol):
    """Аудит lifecycle-событий (без raw SQL/параметров/DSN, §11.11).

    ``payload`` содержит ТОЛЬКО безопасные поля: операция, нормализованные
    имена БД/роли/схемы, ``secret_ref``. Ни пароля, ни DSN, ни ``text`` SQL.
    """

    async def record(self, *, event: str, company_id: str, plugin_id: str, payload: Mapping[str, Any]) -> None: ...
# endregion


@dataclass(frozen=True, slots=True)
class StoredSecret:
    """Secret value для in-memory проверки DSN; repr не раскрывает plaintext."""

    secret_ref: str
    value: str = field(repr=False)


# region Ответ grant_plugin
@dataclass(frozen=True, slots=True)
class TenantDsn:
    """Результат ``grant_plugin``: ссылка на DSN, а не сам DSN.

    Поля:
      * ``secret_ref`` — то, что кладётся в манифест плагина как
        ``TENANT_DB_DSN`` (§5.5, plaintext в манифесте запрещён);
      * ``database`` / ``role`` / ``schema`` / ``bucket`` — нормализованные
        имена (безопасно для логов);
      * ``search_path`` — без версии плагина (§3.3).

    ``__repr__``/__str__ не содержат DSN и пароля по построению: здесь их нет.
    """

    secret_ref: str
    database: str
    role: str
    schema: str
    bucket: str
    search_path: str
    rotated: bool = False
    """True только после явного rotate, никогда после обычного ensure/replay."""
    created: bool = False
    """True если роль и первичный secret созданы этим вызовом."""

    def as_ref(self) -> dict[str, str]:
        """Безопасный словарь для ``env: {TENANT_DB_DSN: {secret_ref: ...}}``."""
        return {"TENANT_DB_DSN": f"secret_ref:{self.secret_ref}"}
# endregion


# region Константы DSN-конструктора
_DSN_SCHEME = "postgresql"
"""Схема DSN: asyncpg/psql/Liquibase понимают ``postgresql://``."""

_SECRET_KEY_PREFIX = "plugins/tenantdb"
"""Префикс ключа в SecretPort. Не содержит пользовательских данных."""

_PASSWORD_BYTES = 32
"""Пароль роли: 32 случайных байта → urlsafe base64 без ``=``."""

# SQLSTATE, которые маппятся в PRIVILEGE_DENIED. Остальные ошибки драйвера
# считаются недоступностью админского соединения (fail-closed).
_PRIVILEGE_SQLSTATES = frozenset({"42501"})
# endregion


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_password() -> str:
    """Случайный пароль роли. НЕ детерминирован, НЕ логируется, НЕ в аудите."""
    return secrets.token_urlsafe(_PASSWORD_BYTES)


class TenantDatabaseService:
    """Управление lifecycle отдельной БД компании и ролей плагинов.

    Все публичные методы асинхронные, fail-closed и идемпотентные там, где
    это заявлено. Ни один метод не возвращает «успех», если админский порт
    недоступен или отсутствует.

    Параметры конструктора:
      * ``admin`` — обязательный ``AdminConnectionPort``;
      * ``secrets`` — обязательный ``SecretPort``;
      * ``audit`` — обязательный ``AuditPort``;
      * ``dsn_host``/``dsn_port`` — адрес tenant-кластера для DSN плагина.
        Это НЕ платформенный Postgres ``prodavan`` (§2.1). Значения
        приходят из настроек родителя (F11); здесь хранятся как строки,
        без пароля и без чтения ``settings`` напрямую.

    ``dsn_sslmode`` по умолчанию ``require``: fail-closed, даже если
    администратор забыл настроить TLS.
    """

    def __init__(
        self,
        *,
        admin: AdminConnectionPort | None,
        secrets: SecretPort | None,
        audit: AuditPort | None,
        dsn_host: str,
        dsn_port: int = 5432,
        dsn_sslmode: str = "require",
        admin_owner_role: str | None = None,
    ) -> None:
        self._admin = admin
        self._secrets = secrets
        self._audit = audit
        self._dsn_host = dsn_host
        self._dsn_port = dsn_port
        self._dsn_sslmode = dsn_sslmode
        self._admin_owner_role = admin_owner_role

    # region Внутренние helpers — fail-closed
    def _require_admin(self, operation: str) -> AdminConnectionPort:
        """Нет админского соединения → ошибка, НЕ «успех»."""
        if self._admin is None:
            raise TenantDbError.admin_unavailable(operation, reason="not configured")
        return self._admin

    def _require_secrets(self, operation: str) -> SecretPort:
        if self._secrets is None:
            raise TenantDbError.admin_unavailable(operation, reason="secret store not configured")
        return self._secrets

    def _require_audit(self, operation: str) -> AuditPort:
        if self._audit is None:
            raise TenantDbError.admin_unavailable(operation, reason="audit not configured")
        return self._audit

    @staticmethod
    def _names(company_id: object, plugin_id: object | None, *, operation: str) -> tuple[str, str, str, str]:
        """Вычислить и вернуть все нормализованные имена одной пачкой.

        Валидация ``company_id``/``plugin_id`` происходит здесь ДО любого
        обращения к портам — невалидный id не порождает ни одного DDL.
        """
        db = tenant_db_name(company_id)
        if plugin_id is None:
            return db, "", "", ""
        role = plugin_role_name(company_id, plugin_id)
        schema = plugin_schema_name(plugin_id)
        path = plugin_search_path(plugin_id)
        del operation  # имена функций несут операцию в своих ошибках
        return db, role, schema, path

    async def _exec(
        self,
        stmt: sql.DdlStatement,
        *,
        target: AdminTarget,
        operation: str,
        safe_name: str,
    ) -> ExecuteResult:
        """Выполнить DDL с единым маппингом ошибок.

        В аудит/логи НЕ пишем ``stmt.text`` и ``stmt.params``: при ошибке
        в текст исключения попадает только ``safe_name``.
        """
        admin = self._require_admin(operation)
        try:
            return await admin.execute(
                stmt.text,
                dict(stmt.params),
                target=target,
            )
        except TenantDbError:
            raise
        except Exception as exc:  # noqa: BLE001 - маппим любые ошибки драйвера
            sqlstate = str(getattr(exc, "sqlstate", "") or "")
            if sqlstate in _PRIVILEGE_SQLSTATES:
                raise TenantDbError.privilege_denied(operation, safe_name=safe_name) from None
            # Любая другая ошибка соединения/драйвера = fail-closed.
            # Текст исключения НЕ прокидываем: там может быть DSN/пароль.
            raise TenantDbError.admin_unavailable(operation, reason="execute failed") from None

    async def _exists(self, stmt: sql.DdlStatement, *, target: AdminTarget, operation: str, safe_name: str) -> bool:
        """``SELECT 1 ...`` → bool. Ошибка выполнения = ADMIN_UNAVAILABLE."""
        result = await self._exec(stmt, target=target, operation=operation, safe_name=safe_name)
        return result.first_value is not None

    async def _audit_event(
        self,
        *,
        event: str,
        company_id: str,
        plugin_id: str,
        payload: Mapping[str, Any],
        operation: str,
    ) -> None:
        """Записать аудит. Ошибка аудита НЕ роняет операцию, но и НЕ тихая.

        fail-closed здесь означал бы откат уже применённого DDL (невозможно
        для CREATE DATABASE), поэтому аудит логирует предупреждение и
        продолжает. Порт при этом обязателен — ``_require_audit`` выше.
        """
        audit = self._require_audit(operation)
        clean = dict(payload)
        # Защита от случайной утечки: выкидываем любые «секретные» ключи.
        for banned in ("password", "dsn", "params", "sql", "text", "secret"):
            clean.pop(banned, None)
        try:
            await audit.record(
                event=event,
                company_id=company_id,
                plugin_id=plugin_id,
                payload=clean,
            )
        except Exception:  # noqa: BLE001 - аудит не должен ломать DDL
            # Никакого текста исключения: там теоретически может быть DSN.
            # Родительский AuditPort сам обязан быть fail-closed на своей стороне.
            return

    def _build_dsn(self, *, database: str, role: str, password: str) -> str:
        """Собрать DSN плагина. Используется ТОЛЬКО внутри ``grant_plugin``.

        Пароль URL-кодируется через ``quote`` (urlsafe base64 уже безопасен,
        но явное кодирование защищает от будущей смены генератора).
        DSN НЕ возвращается наружу: сразу уходит в ``SecretPort``.
        """
        user = quote(role, safe="")
        pwd = quote(password, safe="")
        return (
            f"{_DSN_SCHEME}://{user}:{pwd}@{self._dsn_host}:{self._dsn_port}"
            f"/{database}?sslmode={quote(self._dsn_sslmode, safe='')}"
        )

    def _secret_key(self, *, company_id: str, plugin_id: str) -> str:
        """Ключ в SecretPort. Строится из ПРОВЕРЕННЫХ id (вызывается после ``_names``)."""
        return f"{_SECRET_KEY_PREFIX}/{quote(company_id, safe='')}/{quote(plugin_id, safe='')}/TENANT_DB_DSN"
    async def _read_secret(self, *, key: str, operation: str) -> StoredSecret | None:
        """Read-only доступ к backend; ошибки не превращаются в отсутствие secret."""
        secrets_port = self._require_secrets(operation)
        try:
            stored = await secrets_port.get(key=key)
        except Exception:  # noqa: BLE001 - backend error может содержать DSN
            raise TenantDbError.admin_unavailable(operation, reason="secret read failed") from None
        if stored is None:
            return None
        if (
            not isinstance(stored, StoredSecret)
            or not stored.secret_ref.strip()
            or not isinstance(stored.value, str)
        ):
            raise TenantDbError.admin_unavailable(operation, reason="secret read returned invalid value")
        return stored

    def _dsn_matches(self, value: str, *, database: str, role: str) -> bool:
        """Проверить область DSN без логирования и без записи plaintext куда-либо."""
        try:
            parsed = urlsplit(value)
            return all(
                (
                    parsed.scheme == _DSN_SCHEME,
                    parsed.username == role,
                    bool(parsed.password),
                    parsed.hostname == self._dsn_host,
                    parsed.port == self._dsn_port,
                    parsed.path == f"/{database}",
                    parse_qs(parsed.query).get("sslmode") == [self._dsn_sslmode],
                )
            )
        except (TypeError, ValueError):
            return False

    async def _cleanup_secret(self, *, key: str, operation: str) -> None:
        """Идемпотентно удалить secret по stable key через текущий backend."""
        stored = await self._read_secret(key=key, operation=operation)
        if stored is None:
            return
        try:
            await self._require_secrets(operation).delete(secret_ref=stored.secret_ref)
        except Exception:  # noqa: BLE001
            raise TenantDbError.admin_unavailable(operation, reason="secret cleanup failed") from None
    # endregion

    # region Публичный API
    async def ensure_company_database(self, company_id: str) -> str:
        """Идемпотентно создать БД компании (§2.1, §5.5 grant).

        Повторный вызов НЕ шлёт ``CREATE DATABASE`` второй раз: сначала
        ``SELECT 1 FROM pg_database WHERE datname=:name``. Возвращает
        нормализованное имя БД.

        Никакого «fallback в память» (в отличие от legacy
        ``TenantUserDbService``): нет админского соединения → ошибка.
        """
        operation = "ensure_company_database"
        db = tenant_db_name(company_id)
        target = AdminTarget(scope="cluster")

        if await self._exists(
            sql.database_exists(db), target=target, operation=operation, safe_name=db
        ):
            # Уже существует — это успех (идемпотентность), CREATE не шлём.
            await self._audit_event(
                event="tenantdb.company_database.ensure",
                company_id=company_id,
                plugin_id="",
                payload={"database": db, "created": False, "occurred_at": _now_iso()},
                operation=operation,
            )
            return db

        await self._exec(
            sql.create_database(db, owner=self._admin_owner_role),
            target=target,
            operation=operation,
            safe_name=db,
        )
        if not await self._exists(
            sql.database_exists(db), target=target, operation=operation, safe_name=db
        ):
            raise TenantDbError.admin_unavailable(operation, reason="database missing after create")
        # Bootstrap PUBLIC-привилегий здесь НЕ делаем: см. sql.revoke_public_connect.
        # Это ответственность манифестов tenant-кластера (задача 14).
        await self._audit_event(
            event="tenantdb.company_database.ensure",
            company_id=company_id,
            plugin_id="",
            payload={"database": db, "created": True, "occurred_at": _now_iso()},
            operation=operation,
        )
        return db

    async def ensure_plugin(self, company_id: str, plugin_id: str) -> TenantDsn:
        """Ensure без password rotation: существующий DSN читается и проверяется.

        Отсутствующий/чужой secret даёт ROTATION_REQUIRED. Скрытой смены пароля нет.
        """
        return await self._ensure_or_rotate(company_id, plugin_id, rotate=False)

    async def grant_plugin(self, company_id: str, plugin_id: str) -> TenantDsn:
        """Совместимый alias ensure; повторный grant НЕ ротирует пароль."""
        return await self.ensure_plugin(company_id, plugin_id)

    async def rotate_plugin(self, company_id: str, plugin_id: str) -> TenantDsn:
        """Явный repair/re-grant: ALTER ROLE PASSWORD + LOGIN + новый secret."""
        return await self._ensure_or_rotate(company_id, plugin_id, rotate=True)

    async def _ensure_or_rotate(
        self, company_id: str, plugin_id: str, *, rotate: bool
    ) -> TenantDsn:
        """Общий DDL ensure; password/secret мутируются только при create/rotate."""
        operation = "rotate_plugin" if rotate else "ensure_plugin"
        db, role, schema, path = self._names(company_id, plugin_id, operation=operation)
        bucket = bucket_name(company_id)
        cluster = AdminTarget(scope="cluster")
        database = AdminTarget(scope="database", database=db)

        await self.ensure_company_database(company_id)

        role_present = await self._exists(
            sql.role_exists(role), target=cluster, operation=operation, safe_name=role
        )
        created = not role_present
        rotated = rotate and role_present
        password = _new_password() if created or rotate else ""
        existing_secret: StoredSecret | None = None
        key = self._secret_key(company_id=company_id, plugin_id=plugin_id)

        if created:
            await self._exec(
                sql.create_role(role, password=password),
                target=cluster,
                operation=operation,
                safe_name=role,
            )
            if not await self._exists(
                sql.role_exists(role), target=cluster, operation=operation, safe_name=role
            ):
                raise TenantDbError.admin_unavailable(operation, reason="role missing after create")
        elif rotate:
            # Единственный путь ALTER ROLE PASSWORD: явный repair/re-grant.
            await self._exec(
                sql.alter_role_password(role, password=password),
                target=cluster,
                operation=operation,
                safe_name=role,
            )
            await self._exec(
                sql.alter_role_login(role),
                target=cluster,
                operation=operation,
                safe_name=role,
            )
            if not await self._exists(
                sql.role_login_enabled(role), target=cluster, operation=operation, safe_name=role
            ):
                raise TenantDbError.admin_unavailable(operation, reason="role LOGIN not active after rotate")
        else:
            # Replay/repair с живым secret: проверяем область DSN и сохраняем ref.
            existing_secret = await self._read_secret(key=key, operation=operation)
            if existing_secret is None or not self._dsn_matches(
                existing_secret.value, database=db, role=role
            ):
                raise TenantDbError.rotation_required(operation, safe_name=role)

        # Явный CONNECT (не единственная граница — см. докстринг модуля).
        await self._exec(
            sql.grant_connect(db, role), target=cluster, operation=operation, safe_name=db
        )
        if not await self._exists(
            sql.database_connect_granted(db, role),
            target=cluster,
            operation=operation,
            safe_name=db,
        ):
            raise TenantDbError.admin_unavailable(operation, reason="CONNECT grant missing after GRANT")

        schema_present = await self._exists(
            sql.schema_exists(schema), target=database, operation=operation, safe_name=schema
        )
        if not schema_present:
            await self._exec(
                sql.create_schema(schema, owner=role),
                target=database,
                operation=operation,
                safe_name=schema,
            )
            if not await self._exists(
                sql.schema_exists(schema), target=database, operation=operation, safe_name=schema
            ):
                raise TenantDbError.admin_unavailable(operation, reason="schema missing after create")

        # search_path БЕЗ версии плагина; таймауты — предохранители.
        for stmt in (
            sql.alter_role_search_path(role, schema=path),
            sql.alter_role_statement_timeout(role),
            sql.alter_role_lock_timeout(role),
        ):
            await self._exec(stmt, target=cluster, operation=operation, safe_name=role)

        if created or rotate:
            # Plaintext существует только в этом блоке и уходит сразу в backend.
            secrets_port = self._require_secrets(operation)
            dsn_value = self._build_dsn(database=db, role=role, password=password)
            try:
                if rotate and role_present:
                    secret_ref = await secrets_port.rotate(key=key, value=dsn_value)
                else:
                    secret_ref = await secrets_port.put(key=key, value=dsn_value)
            except Exception:  # noqa: BLE001 - не прокидываем DSN/пароль в ошибку
                raise TenantDbError.admin_unavailable(operation, reason="secret store failed") from None
            finally:
                del dsn_value, password
        else:
            assert existing_secret is not None
            secret_ref = existing_secret.secret_ref

        if not isinstance(secret_ref, str) or not secret_ref:
            raise TenantDbError.admin_unavailable(operation, reason="secret store returned empty ref")

        await self._audit_event(
            event="tenantdb.plugin.rotate" if rotate else "tenantdb.plugin.grant",
            company_id=company_id,
            plugin_id=plugin_id,
            payload={
                "database": db,
                "role": role,
                "schema": schema,
                "bucket": bucket,
                "search_path": path,
                "secret_ref": secret_ref,
                "rotated": rotated,
                "occurred_at": _now_iso(),
            },
            operation=operation,
        )
        return TenantDsn(
            secret_ref=secret_ref,
            database=db,
            role=role,
            schema=schema,
            bucket=bucket,
            search_path=path,
            rotated=rotated,
            created=created,
        )

    async def revoke_plugin(self, company_id: str, plugin_id: str) -> None:
        """Отозвать доступ: ``NOLOGIN`` + ``REVOKE CONNECT`` (§5.5).

        БД и схема СОХРАНЯЮТСЯ. Мгновенный отзыв обеспечивается ``NOLOGIN``:
        ``PUBLIC`` всё ещё имеет дефолтный CONNECT, поэтому снятие одного
        GRANT не дало бы мгновенного эффекта. Старый plaintext-DSN здесь же
        инвалидируется в SecretPort, чтобы он не остался в хранилище;
        деактивация ``CapabilityGrant`` / старого ``PluginClaims`` — отдельная
        ответственность Admin API (F12, §11.12).
        """
        operation = "revoke_plugin"
        db, role, schema, _path = self._names(company_id, plugin_id, operation=operation)
        cluster = AdminTarget(scope="cluster")

        role_present = await self._exists(
            sql.role_exists(role), target=cluster, operation=operation, safe_name=role
        )
        if role_present:
            await self._exec(
                sql.alter_role_no_login(role), target=cluster, operation=operation, safe_name=role
            )
            if await self._exists(
                sql.role_login_enabled(role), target=cluster, operation=operation, safe_name=role
            ):
                raise TenantDbError.admin_unavailable(operation, reason="role LOGIN remains after revoke")
            await self._exec(
                sql.revoke_connect(db, role), target=cluster, operation=operation, safe_name=db
            )
            if await self._exists(
                sql.database_connect_granted(db, role),
                target=cluster,
                operation=operation,
                safe_name=db,
            ):
                raise TenantDbError.admin_unavailable(operation, reason="CONNECT grant remains after REVOKE")
            # NOLOGIN не завершает уже открытые сессии: рвём их явно по role.
            await self._exec(
                sql.terminate_role_sessions(role),
                target=cluster,
                operation=operation,
                safe_name=role,
            )

        # Отсутствующая роль/secret — идемпотентный успех; повтор продолжает cleanup.
        await self._cleanup_secret(
            key=self._secret_key(company_id=company_id, plugin_id=plugin_id),
            operation=operation,
        )

        await self._audit_event(
            event="tenantdb.plugin.revoke",
            company_id=company_id,
            plugin_id=plugin_id,
            payload={
                "database": db,
                "role": role,
                "schema": schema,
                "kept": True,  # БД/схема сохраняются (§5.5)
                "occurred_at": _now_iso(),
            },
            operation=operation,
        )

    async def uninstall_plugin(self, company_id: str, plugin_id: str) -> None:
        """``DROP SCHEMA`` + ``DROP ROLE`` (§5.5, §11.15).

        Данные dev эфемерны — snapshot/бэкап НЕ требуется (§5.5, §13.1).
        Удаляются ТОЛЬКО объекты, чьи имена выведены из этой пары
        ``(company_id, plugin_id)``; чужие БД и чужие схемы не трогаются.
        Порядок: сначала зависимости (REVOKE ALL ON SCHEMA, DROP SCHEMA),
        затем DROP ROLE — иначе PG вернёт 2BP01.
        """
        operation = "uninstall_plugin"
        db, role, schema, _path = self._names(company_id, plugin_id, operation=operation)
        cluster = AdminTarget(scope="cluster")
        database = AdminTarget(scope="database", database=db)

        role_present = await self._exists(
            sql.role_exists(role), target=cluster, operation=operation, safe_name=role
        )
        database_present = await self._exists(
            sql.database_exists(db), target=cluster, operation=operation, safe_name=db
        )

        if role_present:
            await self._exec(
                sql.alter_role_no_login(role), target=cluster, operation=operation, safe_name=role
            )
            await self._exec(
                sql.terminate_role_sessions(role),
                target=cluster,
                operation=operation,
                safe_name=role,
            )

        schema_present = False
        if database_present:
            schema_present = await self._exists(
                sql.schema_exists(schema), target=database, operation=operation, safe_name=schema
            )
        if schema_present:
            if role_present:
                await self._exec(
                    sql.revoke_all_on_schema(schema, role),
                    target=database,
                    operation=operation,
                    safe_name=schema,
                )
            await self._exec(
                sql.drop_schema(schema), target=database, operation=operation, safe_name=schema
            )
        if role_present:
            await self._exec(
                sql.drop_role(role), target=cluster, operation=operation, safe_name=role
            )

        # Подтверждаем факт, а не только отсутствие исключения от драйвера.
        if await self._exists(
            sql.role_exists(role), target=cluster, operation=operation, safe_name=role
        ):
            raise TenantDbError.admin_unavailable(operation, reason="role still exists after drop")
        if database_present and await self._exists(
            sql.schema_exists(schema), target=database, operation=operation, safe_name=schema
        ):
            raise TenantDbError.admin_unavailable(operation, reason="schema still exists after drop")
        await self._cleanup_secret(
            key=self._secret_key(company_id=company_id, plugin_id=plugin_id),
            operation=operation,
        )

        await self._audit_event(
            event="tenantdb.plugin.uninstall",
            company_id=company_id,
            plugin_id=plugin_id,
            payload={
                "database": db,
                "role": role,
                "schema": schema,
                "snapshot_required": False,
                "other_databases_touched": False,
                "occurred_at": _now_iso(),
            },
            operation=operation,
        )

    async def drop_company_database(self, company_id: str) -> None:
        """Удалить БД компании (для удаления компании), через админский порт.

        Перед ``DROP DATABASE`` рвём активные сессии (иначе PG откажет).
        Удаляется РОВНО одна БД ``tenant_<company>``: имя вычисляется
        ``tenant_db_name`` и перепроверяется ``sql.py``.
        """
        operation = "drop_company_database"
        db = tenant_db_name(company_id)
        cluster = AdminTarget(scope="cluster")

        if not await self._exists(
            sql.database_exists(db), target=cluster, operation=operation, safe_name=db
        ):
            raise TenantDbError.not_found(operation, object_kind="database", safe_name=db)

        await self._exec(
            sql.terminate_backend(db), target=cluster, operation=operation, safe_name=db
        )
        await self._exec(
            sql.drop_database(db), target=cluster, operation=operation, safe_name=db
        )
        if await self._exists(
            sql.database_exists(db), target=cluster, operation=operation, safe_name=db
        ):
            raise TenantDbError.admin_unavailable(operation, reason="database still exists after drop")

        await self._audit_event(
            event="tenantdb.company_database.drop",
            company_id=company_id,
            plugin_id="",
            payload={"database": db, "occurred_at": _now_iso()},
            operation=operation,
        )
    # endregion
