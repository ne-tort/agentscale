"""API пилота: per-request introspection и строгая tenant isolation.

Тесты написаны, но НЕ запускаются (verification freeze). Все вызовы protected
health и tenant PostgreSQL заменены recording-фейками; сети и БД нет.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from fastapi import HTTPException

from .conftest import (
    APP_PATH,
    CAPABILITY_HEADER,
    CAPABILITY_TOKEN,
    CHANGELOG_PATH,
    MANIFEST_PATH,
    PLUGIN_VERSION,
    FakeIntrospectionResponse,
    FakeRequest,
    IntrospectionStub,
    fake_introspection,
    load_app_module,
    read_text,
    stub_introspection,
)


class Record:
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
                cabinet_id=params[1] if len(params) > 2 else None,
                project_id=params[2] if len(params) > 3 else None,
                message="Привет от hello_plugin",
                created_at="2026-11-08T09:00:00+00:00",
            )
        ]

    async def execute(self, query: str, *params: Any) -> str:
        self.sink.append((query, params))
        return "OK"

    def transaction(self) -> FakeTransactionCtx:
        return FakeTransactionCtx(self)


class FakeTransactionCtx:
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
def wired(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, RecordingPool, IntrospectionStub]:
    module = load_app_module(monkeypatch, migrations_ok=True)
    gateway = stub_introspection(module, monkeypatch)
    recording = RecordingPool()

    async def fake_pool() -> RecordingPool:
        return recording

    monkeypatch.setattr(module, "_get_pool", fake_pool)
    return module, recording, gateway


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "company_id": "co_test",
        "cabinet_id": "cab_test",
        "project_id": "pr_test",
        "message": "Привет",
    }
    payload.update(overrides)
    return payload


# --- GET /api/greetings: claims, не client selectors -------------------------


def test_list_greetings_selects_full_sql_from_introspected_scope(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    rows = asyncio.run(module.list_greetings(FakeRequest(), company_id="co_test"))

    assert len(rows) == 1
    assert rows[0]["message"] == "Привет от hello_plugin"
    query, params = pool.sink[0]
    assert "SELECT" in query and "FROM greetings" in query
    assert "company_id = $1" in query
    assert "cabinet_id = $2" in query
    assert "project_id = $3" in query
    assert "LIMIT $4" in query
    assert params == ("co_test", "cab_test", "pr_test", 50)
    assert gateway.requests[0]["url"] == (
        "https://plugin-gateway.internal/capabilities/v1/health"
    )
    assert gateway.requests[0]["headers"]["Authorization"] == (
        f"Bearer {CAPABILITY_TOKEN}"
    )


def test_list_greetings_matching_project_assertion_is_allowed(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    asyncio.run(
        module.list_greetings(
            FakeRequest(), company_id="co_test", project_id="pr_test"
        )
    )
    query, params = pool.sink[0]
    assert "project_id = $3" in query
    assert params[:3] == ("co_test", "cab_test", "pr_test")


def test_cross_tenant_read_denied_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.list_greetings(FakeRequest(), company_id="co_other"))
    assert exc.value.status_code == 403
    assert pool.sink == []


def test_introspected_other_company_does_not_override_requested_route(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    gateway.responses[CAPABILITY_TOKEN] = fake_introspection(company_id="co_other")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.list_greetings(FakeRequest(), company_id="co_test"))
    assert exc.value.status_code == 403
    assert pool.sink == []


def test_cross_project_read_denied_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            module.list_greetings(
                FakeRequest(), company_id="co_test", project_id="pr_other"
            )
        )
    assert exc.value.status_code == 403
    assert pool.sink == []


def test_x_company_id_is_ignored_not_trusted(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    request = FakeRequest(
        headers={
            CAPABILITY_HEADER: CAPABILITY_TOKEN,
            "X-Company-ID": "co_evil",
            "X-Project-ID": "pr_evil",
        }
    )
    asyncio.run(module.list_greetings(request, company_id="co_test"))
    _, params = pool.sink[0]
    assert params[:3] == ("co_test", "cab_test", "pr_test")
    assert "co_evil" not in params and "pr_evil" not in params


def test_missing_capability_token_is_401_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            module.list_greetings(FakeRequest(token=None), company_id="co_test")
        )
    assert exc.value.status_code == 401
    assert pool.sink == [] and gateway.requests == []


def test_forged_signature_rejected_by_introspection_is_401(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    forged = "forged-token-with-invalid-signature"
    gateway.responses[forged] = FakeIntrospectionResponse(
        status_code=401,
        payload={"detail": "invalid signature", "debug": "do-not-leak"},
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            module.list_greetings(FakeRequest(token=forged), company_id="co_test")
        )
    assert exc.value.status_code == 401
    assert "do-not-leak" not in str(exc.value.detail)
    assert forged not in str(exc.value.detail)
    assert pool.sink == []


def test_expired_introspection_is_401_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    gateway.responses[CAPABILITY_TOKEN] = fake_introspection(exp=1.0)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.list_greetings(FakeRequest(), company_id="co_test"))
    assert exc.value.status_code == 401
    assert "expired" in str(exc.value.detail)
    assert pool.sink == []


def test_introspection_unavailable_is_503_without_internal_leaks(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    gateway.responses[CAPABILITY_TOKEN] = RuntimeError(
        "https://plugin-gateway.internal failed; token=capability-token"
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.list_greetings(FakeRequest(), company_id="co_test"))
    assert exc.value.status_code == 503
    assert "plugin-gateway.internal" not in str(exc.value.detail)
    assert CAPABILITY_TOKEN not in str(exc.value.detail)
    assert pool.sink == []


def test_extra_introspection_payload_is_fail_closed_503(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    gateway.responses[CAPABILITY_TOKEN] = fake_introspection(
        status="ok", surface="plugin-capabilities"
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.list_greetings(FakeRequest(), company_id="co_test"))
    assert exc.value.status_code == 503
    assert pool.sink == []


def test_other_plugin_introspection_is_403_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    gateway.responses[CAPABILITY_TOKEN] = fake_introspection(plugin_id="other_plugin")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.list_greetings(FakeRequest(), company_id="co_test"))
    assert exc.value.status_code == 403
    assert pool.sink == []


# --- Свежий token на КАЖДЫЙ request, старый TTL не закеширован ----------------


def test_fresh_per_request_token_works_after_old_token_expired(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    gateway.responses.update(
        {
            "old-expired-token": fake_introspection(exp=1.0),
            "fresh-token": fake_introspection(exp=4_102_444_801.0),
        }
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            module.list_greetings(
                FakeRequest(token="old-expired-token"), company_id="co_test"
            )
        )
    assert exc.value.status_code == 401
    assert pool.sink == []

    response = asyncio.run(
        module.create_greeting(FakeRequest(token="fresh-token"), _create_payload())
    )
    assert response.status_code == 201
    assert [call["headers"]["Authorization"] for call in gateway.requests] == [
        "Bearer old-expired-token",
        "Bearer fresh-token",
    ]
    assert len(gateway.client_options) == 2, "introspection не кэшируется"
    for options in gateway.client_options:
        assert options["trust_env"] is False
        assert options["follow_redirects"] is False
        assert "timeout" in options


# --- POST /api/greetings ------------------------------------------------------


def test_create_greeting_uses_only_introspected_tenant_values(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    response = asyncio.run(module.create_greeting(FakeRequest(), _create_payload()))
    assert response.status_code == 201

    insert = next((q, p) for q, p in pool.sink if "INSERT INTO greetings" in q)
    assert insert[1][1:5] == ("co_test", "cab_test", "pr_test", "Привет")
    upsert = next((q, p) for q, p in pool.sink if "ON CONFLICT" in q)
    assert upsert[1] == (module._stats_id(module._parse_introspection(fake_introspection())),)


def test_cross_tenant_write_denied_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            module.create_greeting(
                FakeRequest(), _create_payload(company_id="co_other")
            )
        )
    assert exc.value.status_code == 403
    assert pool.sink == []


def test_cross_project_write_denied_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            module.create_greeting(
                FakeRequest(), _create_payload(project_id="pr_other")
            )
        )
    assert exc.value.status_code == 403
    assert pool.sink == []


def test_create_greeting_validates_input_after_authentication(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, gateway = wired
    invalid_payloads = (
        {},
        {"company_id": "co_test"},
        _create_payload(message=""),
        _create_payload(message="   "),
        _create_payload(company_id=""),
    )
    for payload in invalid_payloads:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(module.create_greeting(FakeRequest(), payload))
        assert exc.value.status_code == 422
    assert pool.sink == []
    assert len(gateway.requests) == len(invalid_payloads)


# --- GET /api/stats -----------------------------------------------------------


def test_stats_reads_scoped_typed_table(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    result = asyncio.run(module.stats(FakeRequest(), company_id="co_test"))
    assert result == {"company_id": "co_test", "greetings_count": 1}
    query, params = pool.sink[0]
    assert "greeting_stats" in query and "greetings_count" in query
    expected_id = module._stats_id(module._parse_introspection(fake_introspection()))
    assert params == (expected_id,)


def test_cross_tenant_stats_denied_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.stats(FakeRequest(), company_id="co_other"))
    assert exc.value.status_code == 403
    assert pool.sink == []


def test_cross_project_stats_denied_before_sql(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, pool, _ = wired
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            module.stats(
                FakeRequest(), company_id="co_test", project_id="pr_other"
            )
        )
    assert exc.value.status_code == 403
    assert pool.sink == []


def test_stats_keys_are_isolated_by_project_claims(
    wired: tuple[Any, RecordingPool, IntrospectionStub],
) -> None:
    module, _, _ = wired
    first = module._parse_introspection(fake_introspection(project_id="pr_one"))
    second = module._parse_introspection(fake_introspection(project_id="pr_two"))
    assert module._stats_id(first) != module._stats_id(second)


# --- Версия плагина не попадает в идентификаторы БД ---------------------------


def test_db_schema_does_not_contain_plugin_version() -> None:
    changelog = read_text(CHANGELOG_PATH)
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
        assert forbidden not in app_source
    assert not re.search(
        r"(?:TABLE|INDEX|SCHEMA|INTO|FROM|UPDATE)\s+\S*" + re.escape(PLUGIN_VERSION),
        app_source,
        re.IGNORECASE,
    )
    assert 'FastAPI(title="hello_plugin", version="' in app_source


def test_manifest_version_lives_in_manifest_only() -> None:
    import json

    manifest = json.loads(read_text(MANIFEST_PATH))
    assert manifest["version"] == PLUGIN_VERSION
    raw_changelog = read_text(CHANGELOG_PATH)
    assert "DATABASECHANGELOG" in raw_changelog or "liquibase" in raw_changelog.lower()
