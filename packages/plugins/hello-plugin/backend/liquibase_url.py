"""Преобразование DSN плагина в JDBC-URL для Liquibase.

Контейнерная Python-логика, а не shell-парсинг (AGENTS.md, принцип 2):
состояние/подключение готовит код внутри образа, а не хостовый .sh-скрипт.
Секрет читается только из окружения (k8s Secret); в логи и в вывод URL
с паролем НЕ попадает — наружу отдаётся строка, но вызывающая сторона
передаёт её Liquibase через переменную окружения процесса, без echo.

Пилот: поддерживается libpq/asyncpg-форма ``postgresql://user:pass@host:port/db``.
"""
from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Префикс JDBC. DSN плагина — обычный PostgreSQL-URI, поэтому «jdbc:» + URI.
_JDBC_PREFIX = "jdbc:"
_ALLOWED_SCHEMES = frozenset({"postgresql", "postgres"})


class DsnError(ValueError):
    """Отказ fail-closed: DSN отсутствует или имеет неподдерживаемую форму."""


def jdbc_url(dsn: str | None = None) -> str:
    """Вернуть JDBC-URL Liquibase из ``TENANT_DB_DSN``.

    PostgreSQL JDBC не принимает ``user:pass@`` в authority, поэтому credentials
    переносятся в query-параметры ``user``/``password``. Всё, что нельзя
    разобрать однозначно, считается ошибкой (fail-closed), а не «пропустить как есть».
    """
    raw = dsn if dsn is not None else os.environ.get("TENANT_DB_DSN", "")
    if not raw:
        raise DsnError("TENANT_DB_DSN не задан в Secret")

    parts = urlsplit(raw)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise DsnError("ожидается postgresql:// DSN")
    if not parts.hostname or not parts.path or parts.path == "/":
        raise DsnError("в DSN отсутствуют хост или имя базы данных")
    if parts.username is None or parts.password is None:
        raise DsnError("в DSN отсутствуют credentials роли плагина")

    # Явно формируем query заново: ничего лишнего из DSN не протекает в JDBC-URL.
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["user"] = parts.username
    query["password"] = parts.password

    port = f":{parts.port}" if parts.port else ""
    netloc = f"{parts.hostname}{port}"
    uri = urlunsplit(("postgresql", netloc, parts.path, urlencode(query), ""))
    return f"{_JDBC_PREFIX}{uri}"
