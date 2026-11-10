"""/health: 503 до миграции, 200 только после успешного Liquibase update.

Критерий 4 §11, §3.1/§5.1 v4. Тесты написаны, но НЕ запускаются
(verification freeze).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from .conftest import (
    APP_PATH,
    DOCKERFILE_PATH,
    ENTRYPOINT_PATH,
    FakePool,
    load_app_module,
    read_text,
)


def _health(module: Any) -> Any:
    """Синхронный вызов асинхронного хендлера без pytest-asyncio-фикстур."""
    return asyncio.run(module.health())


def _ready(module: Any) -> Any:
    return asyncio.run(module.health_ready())


def _live(module: Any) -> Any:
    return asyncio.run(module.health_live())


def _flatten_routes(routes: Any):
    for route in routes:
        effective_candidates = getattr(route, "effective_candidates", None)
        if callable(effective_candidates):
            yield from _flatten_routes(effective_candidates())
        else:
            yield route


def test_probe_routes_match_deployment_admin_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_app_module(monkeypatch, migrations_ok=False)
    expected = {"/health/live", "/health/ready", "/health"}
    methods = {
        route.path: route.methods
        for route in _flatten_routes(module.app.routes)
        if getattr(route, "path", None) in expected
    }
    assert set(methods) == expected
    assert all("GET" in methods[path] for path in expected)


def test_liveness_200_before_ready_without_backend_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Liveness не трогает DB/introspection и не выдаёт себя за Ready."""
    module = load_app_module(monkeypatch, migrations_ok=False)

    async def forbidden_dependency(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("liveness не должен вызывать backend dependencies")

    monkeypatch.setattr(module, "_get_pool", forbidden_dependency)
    monkeypatch.setattr(module, "_introspect_capability", forbidden_dependency)
    response = _live(module)
    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "alive"}
    assert b"ready" not in response.body


def test_ready_503_before_migration_success_without_db_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_app_module(monkeypatch, migrations_ok=False)

    async def forbidden_pool() -> None:
        raise AssertionError("без migration marker readiness не должен идти в DB")

    monkeypatch.setattr(module, "_get_pool", forbidden_pool)
    response = _ready(module)
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "migrations_pending"}


def test_ready_200_after_migrations_with_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_app_module(monkeypatch, migrations_ok=True)

    async def fake_pool() -> FakePool:
        return FakePool(schema_exists=True)

    monkeypatch.setattr(module, "_get_pool", fake_pool)
    response = _ready(module)
    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "ready", "plugin_id": "hello_plugin"}


@pytest.mark.parametrize(
    ("migrations_ok", "schema_exists", "expected_status"),
    (
        (False, True, 503),
        (True, False, 503),
        (True, True, 200),
    ),
)
def test_legacy_health_is_exact_readiness_alias(
    monkeypatch: pytest.MonkeyPatch,
    migrations_ok: bool,
    schema_exists: bool,
    expected_status: int,
) -> None:
    module = load_app_module(monkeypatch, migrations_ok=migrations_ok)

    async def fake_pool() -> FakePool:
        return FakePool(schema_exists=schema_exists)

    monkeypatch.setattr(module, "_get_pool", fake_pool)
    ready = _ready(module)
    alias = _health(module)
    assert ready.status_code == alias.status_code == expected_status
    assert ready.body == alias.body


def test_health_responses_do_not_leak_token_secret_dsn_or_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_app_module(monkeypatch, migrations_ok=True)

    async def fake_pool() -> FakePool:
        return FakePool(schema_exists=True)

    monkeypatch.setattr(module, "_get_pool", fake_pool)
    for response in (_live(module), _ready(module), _health(module)):
        body = response.body.decode("utf-8")
        for forbidden in (
            "capability-token",
            "pilot-webhook-secret",
            "postgresql://",
            "plugin:pw",
            "tenant-postgres",
            "plugin-gateway.internal",
            "Authorization",
        ):
            assert forbidden not in body


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


def test_non_root_entrypoint_gates_readiness_on_liquibase_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`set -eu` + LIQUIBASE_UPDATE_OK=1 ТОЛЬКО после update (§11 кр. 4)."""
    dockerfile = read_text(DOCKERFILE_PATH)
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile

    raw = read_text(ENTRYPOINT_PATH)
    url_pos = raw.index("export LIQUIBASE_COMMAND_URL")
    update_pos = raw.index("\nliquibase update\n")
    ok_pos = raw.index("\nexport LIQUIBASE_UPDATE_OK=1\n")
    exec_pos = raw.index("exec python -m uvicorn")
    assert url_pos < update_pos < ok_pos < exec_pos, (
        "порядок: DSN через env -> update -> флаг готовности -> приложение"
    )
    assert "--url=" not in raw, "credentials не должны попадать в argv"
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
