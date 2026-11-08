"""/health: 503 до миграции, 200 только после успешного Liquibase update.

Критерий 4 §11, §3.1/§5.1 v4. Тесты написаны, но НЕ запускаются
(verification freeze).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import (
    APP_PATH,
    ENTRYPOINT_PATH,
    FakePool,
    load_app_module,
    read_text,
)


def _health(module: Any) -> Any:
    """Синхронный вызов асинхронного хендлера без pytest-asyncio-фикстур."""
    return asyncio.run(module.health())


def test_health_503_before_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    """До успешного `liquibase update` readinessProbe НЕ получает Ready."""
    module = load_app_module(monkeypatch, migrations_ok=False)
    response = _health(module)
    assert response.status_code == 503
    assert b"migrations_pending" in response.body


def test_health_200_after_migrations_with_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, migrations_ok=True)

    async def fake_pool() -> FakePool:
        return FakePool(schema_exists=True)

    monkeypatch.setattr(module, "_get_pool", fake_pool)
    response = _health(module)
    assert response.status_code == 200
    assert b'"ready"' in response.body
    assert b"hello_plugin" in response.body


def test_health_503_after_migrations_but_schema_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Флаг env мало: проверяем, что таблицы РЕАЛЬНО применены (§3.1)."""
    module = load_app_module(monkeypatch, migrations_ok=True)

    async def fake_pool() -> FakePool:
        return FakePool(schema_exists=False)

    monkeypatch.setattr(module, "_get_pool", fake_pool)
    response = _health(module)
    assert response.status_code == 503
    assert b"schema_missing" in response.body


def test_health_503_when_db_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed: любая ошибка БД = не Ready, а не молчаливый 200."""
    module = load_app_module(monkeypatch, migrations_ok=True)

    async def broken_pool() -> FakePool:
        raise RuntimeError("tenant-postgres недоступен")

    monkeypatch.setattr(module, "_get_pool", broken_pool)
    response = _health(module)
    assert response.status_code == 503
    assert b"db_unavailable" in response.body


def test_health_checks_both_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """to_regclass для greetings И greeting_stats — не одна таблица из двух."""
    raw = read_text(APP_PATH)
    assert "to_regclass('greetings')" in raw
    assert "to_regclass('greeting_stats')" in raw


def test_entrypoint_gates_health_on_liquibase_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """`set -eu` + LIQUIBASE_UPDATE_OK=1 ТОЛЬКО после update (§11 кр. 4)."""
    raw = read_text(ENTRYPOINT_PATH)
    update_pos = raw.index("liquibase \\")
    url_pos = raw.index("--url=")
    ok_pos = raw.index("LIQUIBASE_UPDATE_OK=1")
    exec_pos = raw.index("exec python -m uvicorn")
    assert update_pos < url_pos < ok_pos < exec_pos, (
        "порядок: update (с DSN) -> флаг готовности -> запуск приложения"
    )
    assert "set -eu" in raw
    assert "checksum mismatch" in raw.lower()


def test_health_does_not_use_pod_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Критерий 17: /health и весь backend не обращаются к :8001."""
    raw = read_text(APP_PATH)
    assert ":8001" not in raw
    assert "8001" not in raw
    assert "main_pod" not in raw
    assert "/agent/" not in raw
    assert "/internal/pods/" not in raw


def test_migrations_ok_flag_is_not_hardcoded_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """В исходнике нет `LIQUIBASE_UPDATE_OK` по умолчанию = 1 (fail-closed)."""
    raw = read_text(APP_PATH)
    assert 'get("LIQUIBASE_UPDATE_OK", "") == "1"' in raw
    assert 'get("LIQUIBASE_UPDATE_OK", "1")' not in raw
