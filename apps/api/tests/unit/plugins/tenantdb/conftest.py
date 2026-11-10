"""In-memory fakes портов ``tenantdb`` для unit-тестов.

НИКАКИХ реальных соединений: fake админского порта «исполняет» разбор
SELECT/DDL из ``sql.py`` в памяти и записывает все команды в ``statements``.
Это позволяет проверять идемпотентность, порядок DDL и отсутствие утечек
пароля без PostgreSQL (тесты не запускаются до верификации, но обязаны
быть самодостаточными).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prodavan.application.plugins.tenantdb.service import AdminTarget, ExecuteResult, TenantDatabaseService


@dataclass
class FakeAdminConnection:
    """Эмуляция tenant-кластера в памяти.

    ``databases`` — множество БД кластера; ``roles`` — роли;
    ``schemas`` — отображение ``database -> set[schema]``.
    ``statements`` — лог ВСЕХ выполненных команд (текст, параметры, target)
    для проверок «CREATE DATABASE не послан второй раз» и «чужая БД не
    тронута». Пароли в лог НЕ попадают: тесты проверяют это отдельно через
    ``DdlStatement.safe_repr``, но и здесь ``params`` копируются как есть.
    """

    databases: set[str] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    schemas: dict[str, set[str]] = field(default_factory=dict)
    role_login: dict[str, bool] = field(default_factory=dict)
    connect_grants: set[tuple[str, str]] = field(default_factory=set)
    statements: list[tuple[str, dict[str, Any], AdminTarget]] = field(default_factory=list)
    # Иногда нужно сымитировать отказ драйвера (fail-closed тесты).
    fail_next: bool = False
    # Сымитировать SQLSTATE 42501 (privilege denied).
    privilege_denied_next: bool = False

    async def execute(
        self,
        text: str,
        params: dict[str, Any] | None = None,
        *,
        target: AdminTarget,
    ) -> ExecuteResult:
        self.statements.append((text, dict(params or {}), target))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated driver failure (must NOT be logged)")
        if self.privilege_denied_next:
            self.privilege_denied_next = False
            exc = RuntimeError("permission denied")
            setattr(exc, "sqlstate", "42501")
            raise exc

        upper = text.strip().upper()

        # --- SELECT 1 ... проверки существования и post-state ---
        if "ACLEXPLODE" in upper and "ACL.PRIVILEGE_TYPE = 'CONNECT'" in upper:
            key = (params["name"], params["role"])
            return ExecuteResult(rows=((1,),) if key in self.connect_grants else ())
        if upper.startswith("SELECT 1 FROM PG_ROLES") and "ROLCANLOGIN" in upper:
            name = params["rolname"]
            return ExecuteResult(rows=((1,),) if self.role_login.get(name, False) else ())
        if upper.startswith("SELECT 1 FROM PG_DATABASE"):
            name = params["name"]
            return ExecuteResult(rows=((1,),) if name in self.databases else ())
        if upper.startswith("SELECT 1 FROM PG_ROLES"):
            name = params["rolname"]
            return ExecuteResult(rows=((1,),) if name in self.roles else ())
        if upper.startswith("SELECT 1 FROM INFORMATION_SCHEMA.SCHEMATA"):
            name = params["schema"]
            bucket = self.schemas.setdefault(target.database, set())
            return ExecuteResult(rows=((1,),) if name in bucket else ())

        # --- DDL уровня кластера ---
        if upper.startswith("CREATE DATABASE"):
            # ``CREATE DATABASE "tenant_x"`` → имя без кавычек.
            name = _unquote_ident(text, "CREATE DATABASE")
            self.databases.add(name)
            return ExecuteResult()
        if upper.startswith("DROP DATABASE"):
            name = _unquote_ident(text, "DROP DATABASE")
            self.databases.discard(name)
            self.schemas.pop(name, None)
            self.connect_grants = {item for item in self.connect_grants if item[0] != name}
            return ExecuteResult()
        if upper.startswith("CREATE ROLE"):
            name = _unquote_ident(text, "CREATE ROLE")
            self.roles.add(name)
            self.role_login[name] = True
            return ExecuteResult()
        if upper.startswith("DROP ROLE"):
            name = _unquote_ident(text, "DROP ROLE")
            self.roles.discard(name)
            self.role_login.pop(name, None)
            self.connect_grants = {item for item in self.connect_grants if item[1] != name}
            return ExecuteResult()

        # --- DDL в БД компании ---
        if upper.startswith("CREATE SCHEMA"):
            name = _unquote_ident(text, "CREATE SCHEMA")
            self.schemas.setdefault(target.database, set()).add(name)
            return ExecuteResult()
        if upper.startswith("DROP SCHEMA"):
            name = _unquote_ident(text, "DROP SCHEMA")
            self.schemas.setdefault(target.database, set()).discard(name)
            return ExecuteResult()

        if upper.startswith("GRANT CONNECT ON DATABASE"):
            parts = text.split('"')
            self.connect_grants.add((parts[1], parts[3]))
            return ExecuteResult()
        if upper.startswith("REVOKE CONNECT ON DATABASE"):
            parts = text.split('"')
            self.connect_grants.discard((parts[1], parts[3]))
            return ExecuteResult()
        if upper.startswith("ALTER ROLE"):
            name = _unquote_ident(text, "ALTER ROLE")
            if upper.endswith("NOLOGIN"):
                self.role_login[name] = False
            elif upper.endswith("LOGIN"):
                self.role_login[name] = True
            return ExecuteResult()

        # SELECT pg_terminate_backend and role settings do not change this fake.
        return ExecuteResult()

    def texts(self, *, prefix: str | None = None) -> list[str]:
        """Все тексты команд (опционально только с заданным префиксом)."""
        out = [s[0] for s in self.statements]
        if prefix:
            p = prefix.upper()
            out = [t for t in out if t.strip().upper().startswith(p)]
        return out

    def count(self, prefix: str) -> int:
        return len(self.texts(prefix=prefix))


def _unquote_ident(text: str, statement: str) -> str:
    """Вынуть ПЕРВЫЙ ``"name"`` из DDL (имена всегда [a-z0-9_]).

    Устойчиво к ``IF EXISTS``/``OWNER``/``AUTHORIZATION``: ищем первую
    пару двойных кавычек, а не «первое слово после statement». Все
    идентификаторы в ``sql.py`` всегда заключены в двойные кавычки,
    строковые литералы (пароль, search_path) — в одинарные, поэтому
    конфликта нет.
    """
    del statement  # сохранён для наглядности сигнатуры
    if '"' not in text:
        return text.strip().split()[0]
    return text.split('"', 2)[1]


@dataclass
class FakeSecretStore:
    """Хранит значения по key, отдаёт только ``secret_ref``."""

    values: dict[str, str] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    puts: list[str] = field(default_factory=list)
    rotates: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    _counter: int = 0

    async def get(self, *, key: str) -> Any | None:
        from prodavan.application.plugins.tenantdb.service import StoredSecret

        if key not in self.values:
            return None
        return StoredSecret(secret_ref=self.refs[key], value=self.values[key])

    async def put(self, *, key: str, value: str) -> str:
        self.puts.append(key)
        return self._store(key, value)

    async def rotate(self, *, key: str, value: str) -> str:
        self.rotates.append(key)
        return self._store(key, value)

    async def delete(self, *, secret_ref: str) -> None:
        self.deleted.append(secret_ref)
        for key, ref in list(self.refs.items()):
            if ref == secret_ref:
                self.values.pop(key, None)
                self.refs.pop(key, None)

    def _store(self, key: str, value: str) -> str:
        self._counter += 1
        ref = f"vault://plugins/tenantdb/{self._counter}"
        self.values[key] = value
        self.refs[key] = ref
        return ref


@dataclass
class FakeAudit:
    """Собирает события аудита для проверки redaction (§11.11)."""

    events: list[dict[str, Any]] = field(default_factory=list)

    async def record(self, *, event: str, company_id: str, plugin_id: str, payload: dict[str, Any]) -> None:
        self.events.append(
            {"event": event, "company_id": company_id, "plugin_id": plugin_id, "payload": dict(payload)}
        )

    def all_payload_text(self) -> str:
        """Плоский текст всех payload — для поиска утечки пароля/DSN."""
        import json

        return json.dumps([e["payload"] for e in self.events], ensure_ascii=False)


def make_service(
    *,
    admin: FakeAdminConnection | None = None,
    secrets: FakeSecretStore | None = None,
    audit: FakeAudit | None = None,
    dsn_host: str = "tenant-postgres.plugins.svc",
    dsn_port: int = 5432,
) -> tuple[TenantDatabaseService, FakeAdminConnection, FakeSecretStore, FakeAudit]:
    """Собрать сервис с полным набором fakes. Возвращает (service, + fakes)."""
    a = admin if admin is not None else FakeAdminConnection()
    s = secrets if secrets is not None else FakeSecretStore()
    au = audit if audit is not None else FakeAudit()
    service = TenantDatabaseService(
        admin=a,
        secrets=s,
        audit=au,
        dsn_host=dsn_host,
        dsn_port=dsn_port,
    )
    return service, a, s, au
