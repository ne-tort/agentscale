"""Общие фикстуры unit-тестов пилотного hello_plugin.

Тесты написаны, но НЕ запускаются (verification freeze). Сеть, БД, кластер,
build/lint/compile/git не используются. Introspection эмулируется на границе
httpx.AsyncClient, поэтому проверяются реальные handler'ы и HTTP-контракт.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_MARKER = Path("packages") / "plugins" / "hello-plugin" / "manifest.json"


def _find_repo_root() -> Path:
    start = Path(__file__).resolve()
    for base in [start, *start.parents]:
        probe = base / _MARKER
        if probe.is_file():
            return base
    raise RuntimeError("не найден корень репозитория с packages/plugins/hello-plugin/manifest.json")


REPO_ROOT = _find_repo_root()
PLUGIN_ROOT = REPO_ROOT / "packages" / "plugins" / "hello-plugin"
BACKEND_DIR = PLUGIN_ROOT / "backend"
FRONTEND_DIR = PLUGIN_ROOT / "frontend"
MIGRATIONS_DIR = PLUGIN_ROOT / "migrations"

MANIFEST_PATH = PLUGIN_ROOT / "manifest.json"
SCHEMA_PATH = REPO_ROOT / "packages" / "schemas" / "plugin-manifest" / "v1" / "schema.json"
CHANGELOG_PATH = MIGRATIONS_DIR / "changelog.xml"
APP_PATH = BACKEND_DIR / "app.py"
ENTRYPOINT_PATH = BACKEND_DIR / "entrypoint.sh"
DOCKERFILE_PATH = BACKEND_DIR / "Dockerfile"
LIQUIBASE_URL_PATH = BACKEND_DIR / "liquibase_url.py"
CONTRACT_PATH = BACKEND_DIR / "CONTRACT.md"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"
APP_JS_PATH = FRONTEND_DIR / "app.js"
README_PATH = PLUGIN_ROOT / "README.md"

PLUGIN_ID = "hello_plugin"
PLUGIN_VERSION = "0.1.0"
PROTOCOL = "plugin-rpc/1"
CAPABILITY_TOKEN = "capability-token"
CAPABILITY_HEADER = "X-Plugin-Capability-Token"
CAPABILITY_ALLOWLIST = frozenset(
    {"cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"}
)


def read_text(path: Path) -> str:
    assert path.is_file(), f"ожидался файл плагина: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    data = json.loads(read_text(MANIFEST_PATH))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def schema() -> dict[str, Any] | None:
    if not SCHEMA_PATH.is_file():
        return None
    return json.loads(read_text(SCHEMA_PATH))


def fake_introspection(
    *,
    plugin_id: str = PLUGIN_ID,
    company_id: str = "co_test",
    cabinet_id: str | None = "cab_test",
    project_id: str | None = "pr_test",
    exp: float = 4_102_444_800.0,
    **overrides: Any,
) -> dict[str, Any]:
    """Ровно восемь полей согласованного protected health response."""
    payload: dict[str, Any] = {
        "plugin_id": plugin_id,
        "company_id": company_id,
        "cabinet_id": cabinet_id,
        "project_id": project_id,
        "grant_id": "grant:hello_plugin",
        "artifact_digest": "sha256:" + "a" * 64,
        "scopes": sorted(CAPABILITY_ALLOWLIST),
        "exp": exp,
    }
    payload.update(overrides)
    return payload


def load_app_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    migrations_ok: bool = False,
    plugin_id: str = PLUGIN_ID,
    webhook_secret: str = "pilot-webhook-secret",
) -> types.ModuleType:
    """Загрузить app.py с изолированным env, но без сети и статического JWT."""
    monkeypatch.setenv("GATEWAY_URL", "https://plugin-gateway.internal")
    monkeypatch.setenv("PLUGIN_ID", plugin_id)
    monkeypatch.setenv("TENANT_DB_DSN", "postgresql://plugin:pw@tenant-postgres:5432/tenant_co_test")
    monkeypatch.delenv("PLUGIN_CLAIMS", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setenv("LIQUIBASE_UPDATE_OK", "1" if migrations_ok else "0")

    try:
        import asyncpg  # noqa: F401
    except ImportError:
        stub = types.ModuleType("asyncpg")
        stub.Pool = object  # type: ignore[attr-defined]
        stub.create_pool = None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "asyncpg", stub)

    try:
        import httpx  # noqa: F401
    except ImportError:
        stub = types.ModuleType("httpx")
        stub.AsyncClient = None  # type: ignore[attr-defined]
        stub.Timeout = lambda *args, **kwargs: (args, kwargs)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "httpx", stub)

    name = f"hello_plugin_app_{abs(hash((migrations_ok, plugin_id, webhook_secret)))}"
    # Container WORKDIR is /app; mirror that import path for runtime_api.py.
    monkeypatch.syspath_prepend(str(BACKEND_DIR))
    spec = importlib.util.spec_from_file_location(name, APP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass разрешает module через sys.modules во время создания класса.
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class FakeRequest:
    """Request с raw body и headers для прямых вызовов handler."""

    def __init__(
        self,
        body: bytes = b"",
        *,
        token: str | None = CAPABILITY_TOKEN,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.headers = dict(
            headers
            if headers is not None
            else ({CAPABILITY_HEADER: token} if token is not None else {})
        )

    async def body(self) -> bytes:
        return self._body


class FakeIntrospectionResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        *,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class IntrospectionStub:
    """Фабрика fake AsyncClient с записью каждого protected health вызова."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []
        self.client_options: list[dict[str, Any]] = []

    def install(self, module: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(module.httpx, "AsyncClient", self._client)

    def _client(self, **kwargs: Any) -> FakeIntrospectionClient:
        self.client_options.append(dict(kwargs))
        return FakeIntrospectionClient(self, **kwargs)


class FakeIntrospectionClient:
    def __init__(self, owner: IntrospectionStub, **kwargs: Any) -> None:
        self.owner = owner
        self.kwargs = kwargs

    async def __aenter__(self) -> FakeIntrospectionClient:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def get(self, url: str, *, headers: dict[str, str]) -> FakeIntrospectionResponse:
        self.owner.requests.append({"url": url, "headers": dict(headers)})
        authorization = headers.get("Authorization", "")
        token = authorization.removeprefix("Bearer ")
        outcome = self.owner.responses.get(token)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, FakeIntrospectionResponse):
            return outcome
        if isinstance(outcome, dict):
            return FakeIntrospectionResponse(payload=outcome)
        if isinstance(outcome, int):
            return FakeIntrospectionResponse(status_code=outcome)
        return FakeIntrospectionResponse(status_code=401)


def stub_introspection(
    module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, Any] | None = None,
) -> IntrospectionStub:
    stub = IntrospectionStub(
        responses if responses is not None else {CAPABILITY_TOKEN: fake_introspection()}
    )
    stub.install(module, monkeypatch)
    return stub


class FakeAcquire:
    def __init__(self, *, schema_exists: bool = True) -> None:
        self.schema_exists = schema_exists

    async def __aenter__(self) -> FakeAcquire:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def fetchval(self, query: str, *params: Any) -> bool:
        return self.schema_exists


class FakePool:
    def __init__(self, *, schema_exists: bool = True) -> None:
        self.schema_exists = schema_exists

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(schema_exists=self.schema_exists)

    async def close(self) -> None:
        return None


def hmac_signature(secret: str, raw_body: bytes) -> str:
    import hashlib
    import hmac

    return "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


__all__ = [
    "APP_JS_PATH",
    "APP_PATH",
    "BACKEND_DIR",
    "CAPABILITY_ALLOWLIST",
    "CAPABILITY_HEADER",
    "CAPABILITY_TOKEN",
    "CHANGELOG_PATH",
    "CONTRACT_PATH",
    "DOCKERFILE_PATH",
    "ENTRYPOINT_PATH",
    "FRONTEND_DIR",
    "INDEX_HTML_PATH",
    "LIQUIBASE_URL_PATH",
    "MANIFEST_PATH",
    "MIGRATIONS_DIR",
    "PLUGIN_ID",
    "PLUGIN_ROOT",
    "PLUGIN_VERSION",
    "PROTOCOL",
    "README_PATH",
    "REPO_ROOT",
    "SCHEMA_PATH",
    "FakeIntrospectionResponse",
    "FakePool",
    "FakeRequest",
    "IntrospectionStub",
    "fake_introspection",
    "hmac_signature",
    "load_app_module",
    "manifest",
    "read_text",
    "schema",
    "stub_introspection",
]
