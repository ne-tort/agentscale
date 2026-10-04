"""Unit tests — UX/reliability fixes: cabinet delete commit, live-models retries, JSON charset."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import prodavan.application.ai_models.live_service as live_service
from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService
from prodavan.core.middleware import JsonCharsetMiddleware

# ---------------------------------------------------------------- cabinet delete


@pytest.mark.asyncio
async def test_cabinet_delete_data_row_commits(monkeypatch) -> None:
    """The flushed DELETE must be committed — otherwise the request teardown
    rolls it back and the row "comes back" on the next list/poll."""
    session = AsyncMock()
    svc = CabinetModuleService(session)
    async def _tables_body(**kwargs):  # noqa: ANN003
        return [{"slug": "catalogs", "scope": {"chats": "all"}}]


    async def _require_access(**kwargs):  # noqa: ANN003
        return None

    async def _require_module_binding(**kwargs):  # noqa: ANN003
        return None

    inst = SimpleNamespace(id="minst_1")

    async def _cabinet_sot(**kwargs):  # noqa: ANN003
        return inst

    async def _delete(**kwargs):  # noqa: ANN003
        return True

    async def _schedule(_session, **kwargs):  # noqa: ANN003
        return {"scheduled": 0, "skipped": True}

    monkeypatch.setattr(svc._access, "require_access", _require_access)
    monkeypatch.setattr(svc, "_require_module_binding", _require_module_binding)
    monkeypatch.setattr(svc, "_cabinet_sot", _cabinet_sot)
    monkeypatch.setattr(svc._instances, "delete_data_row", _delete)
    monkeypatch.setattr(svc._instances, "resolve_tables_body", _tables_body)
    # delete читает строку для каскада mod_equipment (catalogs не каскадируются)
    async def _get_row(**kwargs):  # noqa: ANN003
        return {"row_id": "row_1", "body": {"name": "DB"}, "session_id": None}

    monkeypatch.setattr(svc._instances, "get_data_row", _get_row)
    import prodavan.application.projects.rematerialize_scheduler as remat

    monkeypatch.setattr(
        remat, "schedule_cabinet_rematerialize", _schedule
    )

    out = await svc.delete_data_row(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="catalogs",
        row_id="row_1",
        principal=MagicMock(),
        employee=None,
    )
    assert out["scheduled"] == 0
    # The deletion is persisted in the same request: commit AFTER the row
    # delete, BEFORE the rematerialize scheduling result is returned.
    session.commit.assert_awaited()


# ---------------------------------------------------------------- live models retries


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    responses: list[_FakeResponse] = []
    calls = 0

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):  # noqa: ANN002
        return False

    async def get(self, *args, **kwargs):  # noqa: ANN002, ANN003
        _FakeAsyncClient.calls += 1
        if _FakeAsyncClient.calls <= len(_FakeAsyncClient.responses):
            return _FakeAsyncClient.responses[_FakeAsyncClient.calls - 1]
        return _FakeAsyncClient.responses[-1]


async def _nosleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_live_models_retries_transient_5xx(monkeypatch) -> None:
    svc = live_service.AiModelsLiveService(AsyncMock())
    key_row = SimpleNamespace(id="k1", api_kind="cursor_sdk", provider="cursor")

    monkeypatch.setattr(live_service.settings, "pod_agent_runtime_enabled", True)
    monkeypatch.setattr(live_service.settings, "live_models_fetch_attempts", 3)
    monkeypatch.setattr(live_service.asyncio, "sleep", _nosleep)

    async def _any_project(_self, company_id):  # noqa: ANN001
        return "proj_1"

    monkeypatch.setattr(
        live_service.AiModelsLiveService, "_any_running_project_for_company", _any_project
    )

    async def _resolve(_session, project_id):  # noqa: ANN001
        return SimpleNamespace(base_url="http://bridge", headers={})

    monkeypatch.setattr(live_service, "resolve_runtime_endpoint", _resolve)

    class _Broker:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def push_lease_to_runtime(self, **kwargs):  # noqa: ANN003
            return True

    monkeypatch.setattr(live_service, "AgentCredentialBroker", _Broker)

    # HTTP-ish kinds resolve the provider endpoint before the bridge call.
    async def _secret(_self, _key_id):  # noqa: ANN001
        return None

    monkeypatch.setattr(live_service.AiModelsLiveService, "_resolve_key_secret", _secret)

    class _Resolver:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def resolve(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(live_service, "ProviderResolver", _Resolver)

    _FakeAsyncClient.calls = 0
    _FakeAsyncClient.responses = [
        _FakeResponse(503, {"error": "busy"}),
        _FakeResponse(200, {"models": ["m-one", "m-two"]}),
    ]
    monkeypatch.setattr(live_service.httpx, "AsyncClient", _FakeAsyncClient)

    out = await svc._fetch_live_model_ids(company_id="co1", key_row=key_row)
    assert out == ["m-one", "m-two"]
    assert _FakeAsyncClient.calls == 2


@pytest.mark.asyncio
async def test_live_models_retries_empty_then_gives_up(monkeypatch) -> None:
    svc = live_service.AiModelsLiveService(AsyncMock())
    key_row = SimpleNamespace(id="k1", api_kind="cursor_sdk", provider="cursor")

    monkeypatch.setattr(live_service.settings, "pod_agent_runtime_enabled", True)
    monkeypatch.setattr(live_service.settings, "live_models_fetch_attempts", 3)
    monkeypatch.setattr(live_service.asyncio, "sleep", _nosleep)

    async def _any_project(_self, company_id):  # noqa: ANN001
        return "proj_1"

    monkeypatch.setattr(
        live_service.AiModelsLiveService, "_any_running_project_for_company", _any_project
    )

    async def _resolve(_session, project_id):  # noqa: ANN001
        return SimpleNamespace(base_url="http://bridge", headers={})

    monkeypatch.setattr(live_service, "resolve_runtime_endpoint", _resolve)

    class _Broker:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def push_lease_to_runtime(self, **kwargs):  # noqa: ANN003
            return True

    monkeypatch.setattr(live_service, "AgentCredentialBroker", _Broker)

    # HTTP-ish kinds resolve the provider endpoint before the bridge call.
    async def _secret(_self, _key_id):  # noqa: ANN001
        return None

    monkeypatch.setattr(live_service.AiModelsLiveService, "_resolve_key_secret", _secret)

    class _Resolver:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def resolve(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(live_service, "ProviderResolver", _Resolver)

    _FakeAsyncClient.calls = 0
    _FakeAsyncClient.responses = [_FakeResponse(200, {"models": []})]
    monkeypatch.setattr(live_service.httpx, "AsyncClient", _FakeAsyncClient)

    out = await svc._fetch_live_model_ids(company_id="co1", key_row=key_row)
    assert out == []
    assert _FakeAsyncClient.calls == 3


@pytest.mark.asyncio
async def test_live_models_4xx_fails_fast(monkeypatch) -> None:
    svc = live_service.AiModelsLiveService(AsyncMock())
    key_row = SimpleNamespace(id="k1", api_kind="cursor_sdk", provider="cursor")

    monkeypatch.setattr(live_service.settings, "pod_agent_runtime_enabled", True)
    monkeypatch.setattr(live_service.settings, "live_models_fetch_attempts", 3)
    monkeypatch.setattr(live_service.asyncio, "sleep", _nosleep)

    async def _any_project(_self, company_id):  # noqa: ANN001
        return "proj_1"

    monkeypatch.setattr(
        live_service.AiModelsLiveService, "_any_running_project_for_company", _any_project
    )

    async def _resolve(_session, project_id):  # noqa: ANN001
        return SimpleNamespace(base_url="http://bridge", headers={})

    monkeypatch.setattr(live_service, "resolve_runtime_endpoint", _resolve)

    class _Broker:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def push_lease_to_runtime(self, **kwargs):  # noqa: ANN003
            return True

    monkeypatch.setattr(live_service, "AgentCredentialBroker", _Broker)

    # HTTP-ish kinds resolve the provider endpoint before the bridge call.
    async def _secret(_self, _key_id):  # noqa: ANN001
        return None

    monkeypatch.setattr(live_service.AiModelsLiveService, "_resolve_key_secret", _secret)

    class _Resolver:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def resolve(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(live_service, "ProviderResolver", _Resolver)

    _FakeAsyncClient.calls = 0
    _FakeAsyncClient.responses = [_FakeResponse(401, {"error": "no"})]
    monkeypatch.setattr(live_service.httpx, "AsyncClient", _FakeAsyncClient)

    out = await svc._fetch_live_model_ids(company_id="co1", key_row=key_row)
    assert out == []
    assert _FakeAsyncClient.calls == 1


# ---------------------------------------------------------------- charset middleware


async def _noop_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _run_through_middleware(content_type: bytes) -> bytes:
    captured: dict = {}

    async def app(scope, receive, send):  # noqa: ANN001
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", content_type)],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    async def send_wrapper(message):  # noqa: ANN001
        if message["type"] == "http.response.start":
            captured["headers"] = dict(message["headers"])

    mid = JsonCharsetMiddleware(app)
    scope = {"type": "http", "method": "GET", "path": "/"}
    await mid(scope, _noop_receive, send_wrapper)
    return captured["headers"][b"content-type"]


@pytest.mark.asyncio
async def test_json_charset_middleware_appends_to_json() -> None:
    assert await _run_through_middleware(b"application/json") == b"application/json; charset=utf-8"


@pytest.mark.asyncio
async def test_json_charset_middleware_appends_to_problem_json() -> None:
    assert (
        await _run_through_middleware(b"application/problem+json")
        == b"application/problem+json; charset=utf-8"
    )


@pytest.mark.asyncio
async def test_json_charset_middleware_leaves_other_types_alone() -> None:
    assert await _run_through_middleware(b"text/event-stream") == b"text/event-stream"


@pytest.mark.asyncio
async def test_json_charset_middleware_keeps_existing_charset() -> None:
    assert (
        await _run_through_middleware(b"application/json; charset=utf-8")
        == b"application/json; charset=utf-8"
    )
