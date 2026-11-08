"""Общие фикстуры/хелперы unit-тестов ПИЛОТНОГО плагина hello_plugin.

Тесты **написаны, но НЕ запускаются** (verification freeze, §0.2 плана
миграции и §9 архитектуры v4). Никаких сетевых вызовов, кластера, БД,
docker/kubectl/psql/alembic/git/линт/сборок.

Тесты читают РЕАЛЬНЫЕ файлы плагина из ``packages/plugins/hello-plugin``,
поэтому путь ищется вверх по маркеру, а не хардкодится числом parents.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import types
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Поиск корня репозитория по маркеру манифеста (устойчиво к смене глубины).
# ---------------------------------------------------------------------------
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
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"
APP_JS_PATH = FRONTEND_DIR / "app.js"
README_PATH = PLUGIN_ROOT / "README.md"

# Значения из manifest.json (дублируются здесь как ожидаемые константы контракта).
PLUGIN_ID = "hello_plugin"
PLUGIN_VERSION = "0.1.0"
PROTOCOL = "plugin-rpc/1"
CAPABILITY_ALLOWLIST = frozenset(
    {"cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"}
)


def read_text(path: Path) -> str:
    """Прочитать UTF-8 файл. Отсутствие = падение теста, а не тихий пропуск."""
    assert path.is_file(), f"ожидался файл плагина: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    data = json.loads(read_text(MANIFEST_PATH))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def schema() -> dict[str, Any] | None:
    """JSON Schema задачи 01, если файл уже создан параллельным субагентом."""
    if not SCHEMA_PATH.is_file():
        return None
    return json.loads(read_text(SCHEMA_PATH))


def fake_plugin_claims(
    *,
    plugin_id: str = PLUGIN_ID,
    exp: float | None = None,
    **overrides: Any,
) -> str:
    """Структурно-валидный ``PLUGIN_CLAIMS`` (подпись/JWKS — зона задачи 02).

    ``app.verify_plugin_claims`` в пилоте проверяет структуру и скоуп, а не
    криптографию; поэтому для unit-тестов достаточно корректного JWT-каркаса.
    """
    if exp is None:
        exp = 4_102_444_800.0  # 2100-01-01, заведомо в будущем
    payload: dict[str, Any] = {
        "iss": "agentscale-plugins",
        "aud": "capability-gateway",
        "typ": "plugin_capability",
        "sub": f"plugin:{plugin_id}:co_test:inst_pilot",
        "company_id": "co_test",
        "cabinet_id": "cab_test",
        "project_id": None,
        "plugin_id": plugin_id,
        "plugin_instance_id": "inst_pilot",
        "manifest_version": PLUGIN_VERSION,
        "artifact_digest": "sha256:" + "a" * 64,
        "grant_id": "grant:hello_plugin",
        "scopes": sorted(CAPABILITY_ALLOWLIST),
        "quota_ref": "co_test/plan_pilot",
        "jti": "pilot-jti",
        "exp": exp,
    }
    payload.update(overrides)
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","kid":"pilot"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.pilot-signature"


def load_app_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    migrations_ok: bool = False,
    plugin_id: str = PLUGIN_ID,
    webhook_secret: str = "pilot-webhook-secret",
    claims: str | None = None,
) -> types.ModuleType:
    """Загрузить ``backend/app.py`` как изолированный модуль с управляемым env.

    Все секреты подставляются через ``os.environ`` (эквивалент k8s Secret);
    plaintext-фолбэков в коде быть не должно. Имя модуля уникально на тест,
    чтобы глобальные состояния (``_pool``, ``_seen_deliveries``, ``_MIGRATIONS_OK``)
    не протекали между проверками.
    """
    monkeypatch.setenv("GATEWAY_URL", "https://agentscale.dev/pluginpage/hello_plugin")
    monkeypatch.setenv("PLUGIN_ID", plugin_id)
    monkeypatch.setenv("TENANT_DB_DSN", "postgresql://plugin:pw@tenant-postgres:5432/tenant_co_test")
    monkeypatch.setenv("PLUGIN_CLAIMS", claims if claims is not None else fake_plugin_claims(plugin_id=plugin_id))
    monkeypatch.setenv("PLUGIN_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setenv("LIQUIBASE_UPDATE_OK", "1" if migrations_ok else "0")

    # asyncpg/fastapi есть в зависимостях apps/api; если окружение без них —
    # тест всё равно должен уметь проверить контракт. Подменяем asyncpg заглушкой,
    # чтобы импорт модуля не требовал реального драйвера/БД.
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        stub = types.ModuleType("asyncpg")
        stub.Pool = object  # type: ignore[attr-defined]
        stub.create_pool = None  # type: ignore[attr-defined]
        monkeypatch.setitem(__import__("sys").modules, "asyncpg", stub)

    name = f"hello_plugin_app_{abs(hash((migrations_ok, plugin_id, webhook_secret, claims)))}"
    spec = importlib.util.spec_from_file_location(name, APP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRequest:
    """Минимальный Request с ``await request.body()`` для прямого вызова хендлера."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class FakeAcquire:
    """Контекстный менеджер соединения для подмены ``_get_pool``."""

    def __init__(self, *, schema_exists: bool = True) -> None:
        self.schema_exists = schema_exists

    async def __aenter__(self) -> FakeAcquire:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def fetchval(self, query: str, *params: Any) -> bool:
        # health() проверяет to_regclass(...) для greetings и greeting_stats.
        return self.schema_exists


class FakePool:
    def __init__(self, *, schema_exists: bool = True) -> None:
        self.schema_exists = schema_exists

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(schema_exists=self.schema_exists)

    async def close(self) -> None:
        return None


def hmac_signature(secret: str, raw_body: bytes) -> str:
    """``X-Prodavan-Signature: sha256=<hmac>`` по docs/external-api.md."""
    import hashlib
    import hmac

    return "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


__all__ = [
    "APP_JS_PATH",
    "APP_PATH",
    "BACKEND_DIR",
    "CAPABILITY_ALLOWLIST",
    "CHANGELOG_PATH",
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
    "FakePool",
    "FakeRequest",
    "fake_plugin_claims",
    "hmac_signature",
    "load_app_module",
    "manifest",
    "read_text",
    "schema",
]
