"""API пилота: GET/POST /api/greetings, GET /api/stats — полный SQL в СВОЮ БД
через TENANT_DB_DSN, typed-таблицы, без версии плагина в схеме.

Критерии §2/§3.3, §5.1. Тесты написаны, но НЕ запускаются (verification freeze).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from fastapi import HTTPException

from .conftest import (
    APP_PATH,
    CHANGELOG_PATH,
    MANIFEST_PATH,
    PLUGIN_VERSION,
    load_app_module,
    read_text,
)


class Record:
    """Имитация asyncpg.Record: поддерживает dict(row).

    Реальная ``asyncpg.Record`` реализует ``keys()`` и ``Mapping``; ``dict(row)``
    строит пары (ключ, значение). Итерация по ключам (как в dict) приводила бы к
    ``ValueError: dictionary update sequence element #1 has length 10`` — это
    баг фейка, а не ``app.list_greetings``.
    """

    def __init__(self, **values: Any) -> None:
        self._values = values

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def keys(self):
        return self._values.keys()

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class FakeConnection:
    def __init__(self, sink: list[tuple[str, tuple[Any, ...]]]) -> None:
        self.sink = sink

    async def fetchval(self, query: str, *params: Any) -> bool:
        self.sink.append((query, params))
        return True

    async def fetch(self, query: str, *params: Any) -> list[Record]:
        self.sink.append((query, params))
        return [
            Record(
                id="11111111-1111-1111-1111-111111111111",
                company_id=str(params[0]),
                cabinet_id=None,
                project_id=None,
                message="Привет от hello_plugin",
                created_at="2026-11-08T09:00:00+00:00",
            )
        ]

    async def execute(self, query: str, *params: Any) -> str:
        self.sink.append((query, params))
        return "OK"

    def transaction(self) -> FakeTransactionCtx:
        # asyncpg: ``async with conn.transaction():`` — асинхронный контекстный
        # менеджер, а не сам connection. Возврат ``self`` давал TypeError.
        return FakeTransactionCtx(self)


class FakeTransactionCtx:
    """``async with conn.transaction():`` из реального asyncpg."""

    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self.conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


class FakeAcquireCtx:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self.conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


class RecordingPool:
    def __init__(self) -> None:
        self.sink: list[tuple[str, tuple[Any, ...]]] = []
        self.conn = FakeConnection(self.sink)

    def acquire(self) -> FakeAcquireCtx:
        return FakeAcquireCtx(self.conn)

    async def close(self) -> None:
        return None


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, RecordingPool]:
    """Один и тот же экземпляр модуля + пул, чтобы monkeypatch попал куда нужно."""
    module = load_app_module(monkeypatch, migrations_ok=True)
    recording = RecordingPool()

    async def fake_pool() -> RecordingPool:
        return recording

    monkeypatch.setattr(module, "_get_pool", fake_pool)
    return module, recording


# --- GET /api/greetings -------------------------------------------------------

def test_list_greetings_selects_full_sql(wired: tuple[Any, RecordingPool]) -> None:
    module, pool = wired
    rows = asyncio.run(module.list_greetings(company_id="co_test"))
    assert len(rows) == 1
    assert rows[0]["message"] == "Привет от hello_plugin"
    assert any("SELECT" in q and "FROM greetings" in q for q, _ in pool.sink)
    assert any("company_id = $1" in q for q, _ in pool.sink)


def test_list_greetings_filters_by_project(wired: tuple[Any, RecordingPool]) -> None:
    module, pool = wired
    asyncio.run(module.list_greetings(company_id="co_test", project_id="pr_1"))
    assert any("project_id = $2" in q for q, _ in pool.sink), (
        "разделение по project_id — WHERE плагина, а не RLS (§2.4)"
    )


def test_list_greetings_rejects_missing_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed: без валидного PLUGIN_CLAIMS запрос к БД не выполняется."""
    module = load_app_module(monkeypatch, migrations_ok=True)
    monkeypatch.setattr(module, "PLUGIN_CLAIMS_RAW", "")
    with pytest.raises(ValueError):
        asyncio.run(module.list_greetings(company_id="co_test"))


# --- POST /api/greetings ------------------------------------------------------

def test_create_greeting_inserts_into_own_typed_table(wired: tuple[Any, RecordingPool]) -> None:
    module, pool = wired
    response = asyncio.run(
        module.create_greeting({"company_id": "co_test", "message": "Привет"})
    )
    assert response.status_code == 201
    queries = [q for q, _ in pool.sink]
    assert any("INSERT INTO greetings" in q for q in queries), "полный SQL INSERT, не jsonb-строка"
    assert any("greeting_stats" in q and "ON CONFLICT" in q for q in queries)


def test_create_greeting_validates_input(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, migrations_ok=True)
    for payload in ({}, {"company_id": "co_test"}, {"message": "Привет"}, {"company_id": "", "message": "x"}):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(module.create_greeting(payload))  # type: ignore[arg-type]
        assert exc.value.status_code == 422


# --- GET /api/stats -----------------------------------------------------------

def test_stats_reads_typed_table(wired: tuple[Any, RecordingPool]) -> None:
    module, pool = wired
    # fetchval в FakeConnection возвращает True -> int(True) == 1.
    result = asyncio.run(module.stats(company_id="co_test"))
    assert result["company_id"] == "co_test"
    assert isinstance(result["greetings_count"], int)
    assert any("greeting_stats" in q and "greetings_count" in q for q, _ in pool.sink)


# --- схема БД не содержит версии плагина -------------------------------------

def test_db_schema_does_not_contain_plugin_version() -> None:
    """§3.3: версия живёт в DATABASECHANGELOG и в пути статики, не в DDL/именах.

    Строка ``0.1.0`` допустима как версия FastAPI-приложения/манифеста, но
    НЕ как часть имени схемы, таблицы или индекса — проверяем именно это.
    """
    changelog = read_text(CHANGELOG_PATH)
    # В changelog версии нет вообще (кроме XSD-версии Liquibase, не плагина).
    assert PLUGIN_VERSION not in changelog
    assert f"module_{PLUGIN_VERSION}" not in changelog

    app_source = read_text(APP_PATH)
    for forbidden in (
        f"module_{PLUGIN_VERSION}",
        f"schema_{PLUGIN_VERSION}",
        f"greetings_{PLUGIN_VERSION}",
        f"greeting_stats_{PLUGIN_VERSION}",
        f"_{PLUGIN_VERSION}",
        f"v{PLUGIN_VERSION}",
    ):
        assert forbidden not in app_source, f"версия плагина в идентификаторе БД: {forbidden}"
    # Ни одного SQL-идентификатора, содержащего версию.
    assert not re.search(
        r"(?:TABLE|INDEX|SCHEMA|INTO|FROM|UPDATE)\s+\S*" + re.escape(PLUGIN_VERSION),
        app_source,
        re.IGNORECASE,
    )
    assert "FastAPI(title=\"hello_plugin\", version=\"" in app_source, (
        "версия приложения объявлена явно, но не попадает в имена БД"
    )


def test_manifest_version_lives_in_manifest_only() -> None:
    import json

    manifest = json.loads(read_text(MANIFEST_PATH))
    assert manifest["version"] == PLUGIN_VERSION
    raw_changelog = read_text(CHANGELOG_PATH)
    assert "DATABASECHANGELOG" in raw_changelog or "liquibase" in raw_changelog.lower()
