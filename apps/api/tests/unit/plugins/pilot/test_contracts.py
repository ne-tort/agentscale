"""Backend НЕ импортирует/не вызывает :8001 pod-gateway; конфигурация только
из Secret; SQL — полный, к своим typed-таблицам; frontend handshake проверяет
event.origin; в URL нет токена.

Критерии 16, 17 §11, §4.1, §5.1, §5.2, §6.4 v4. Тесты написаны,
но НЕ запускаются (verification freeze).
"""
from __future__ import annotations

import re

import pytest

from .conftest import (
    APP_JS_PATH,
    APP_PATH,
    DOCKERFILE_PATH,
    ENTRYPOINT_PATH,
    INDEX_HTML_PATH,
    LIQUIBASE_URL_PATH,
    MANIFEST_PATH,
    PROTOCOL,
    README_PATH,
    load_app_module,
    read_text,
)

# Порт agent-surface pod-gateway: запрещён для плагинов (§4.1, §13.3).
FORBIDDEN_AGENT_SURFACE = (
    ":8001",
    "8001",
    "main_pod",
    "prodavan.main_pod",
    "pod_bridge",
    "PodBridgeClaims",
    "/agent/",
    "/internal/pods/",
    "workspace-archive",
)


# --- критерий 17: backend не ходит в :8001 -----------------------------------

@pytest.mark.parametrize("needle", FORBIDDEN_AGENT_SURFACE)
def test_backend_source_has_no_agent_surface(needle: str) -> None:
    raw = read_text(APP_PATH)
    assert needle not in raw, f"backend плагина не должен знать про agent-surface: {needle}"


def test_entrypoint_and_helper_have_no_agent_surface() -> None:
    for path in (ENTRYPOINT_PATH, LIQUIBASE_URL_PATH, DOCKERFILE_PATH):
        raw = read_text(path)
        assert ":8001" not in raw, f"{path.name}: обращение к pod-gateway"
        assert "main_pod" not in raw


def test_backend_uses_gateway_url_from_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """GATEWAY_URL/PLUGIN_ID/TENANT_DB_DSN/PLUGIN_CLAIMS — всё из env (Secret)."""
    raw = read_text(APP_PATH)
    for name in ("GATEWAY_URL", "PLUGIN_ID", "TENANT_DB_DSN", "PLUGIN_CLAIMS", "PLUGIN_WEBHOOK_SECRET"):
        assert f'os.environ.get("{name}"' in raw, f"{name} должен читаться из окружения/Secret"
    assert "TENANT_DB_DSN" in raw
    assert "postgresql://" in raw or "DSN" in raw
    # Никаких dev-фолбэков с секретами.
    assert 'os.environ.get("TENANT_DB_DSN", "postgresql://' not in raw
    assert 'os.environ.get("PLUGIN_CLAIMS", "ey' not in raw


def test_missing_secret_env_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed: пустой обязательный env => RuntimeError, а не тихий успех."""
    module = load_app_module(monkeypatch)
    monkeypatch.setattr(module, "GATEWAY_URL", "")
    with pytest.raises(RuntimeError, match="не заданы обязательные переменные"):
        module._require_secret_env()


def test_plugin_claims_are_verified_before_gateway_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """§7.2: backend обязан проверить JWT, а не доверять Gateway «по умолчанию»."""
    module = load_app_module(monkeypatch)
    module._authorized_gateway_call()  # валидный claims -> без исключения

    monkeypatch.setattr(module, "PLUGIN_CLAIMS_RAW", "")
    with pytest.raises(ValueError):
        module._authorized_gateway_call()


def test_plugin_claims_rejects_wrong_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    from .conftest import fake_plugin_claims

    module = load_app_module(monkeypatch)
    with pytest.raises(ValueError, match="не совпадает"):
        module.verify_plugin_claims(fake_plugin_claims(plugin_id="чужой_плагин"))


def test_plugin_claims_rejects_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    from .conftest import fake_plugin_claims

    module = load_app_module(monkeypatch)
    with pytest.raises(ValueError, match="просрочен"):
        module.verify_plugin_claims(fake_plugin_claims(exp=1.0))


# --- SQL: полный, к своим typed-таблицам -------------------------------------

def test_backend_uses_full_sql_against_own_tables() -> None:
    raw = read_text(APP_PATH)
    assert "INSERT INTO greetings" in raw
    assert "SELECT" in raw and "FROM greetings" in raw
    assert "greeting_stats" in raw
    assert "ON CONFLICT" in raw, "UPSERT счётчика идемпотентен к повторам"
    assert "$1" in raw and "$2" in raw, "параметризованные запросы, а не конкатенация"
    assert "jsonb" not in raw.lower()
    assert "module_instance_data_rows" not in raw.lower()
    # Платформенная БД недоступна плагину даже по имени.
    assert "FROM prodavan" not in raw
    assert "prodavan.public" not in raw


def test_backend_uses_only_tenant_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch)
    assert module.TENANT_DB_DSN.startswith("postgresql://")
    assert "prodavan" not in module.TENANT_DB_DSN.split("@")[-1]


def test_liquibase_url_helper_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """DSN -> JDBC: пустой/битый DSN = ValueError, credentials не текут в логи."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pilot_liquibase_url", LIQUIBASE_URL_PATH)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    url = helper.jdbc_url("postgresql://plugin:s3cr3t@tenant-postgres:5432/tenant_co_test")
    assert url.startswith("jdbc:postgresql://")
    assert "tenant_co_test" in url
    assert "user=plugin" in url and "password=s3cr3t" in url

    for bad in ("", "mysql://u:p@h/db", "postgresql://h/db", "postgresql://u@h/db"):
        with pytest.raises(helper.DsnError):
            helper.jdbc_url(bad)


def test_entrypoint_has_no_infrastructure_shell_logic() -> None:
    """AGENTS.md: .sh НЕ для infra; это контейнерный entrypoint."""
    raw = read_text(ENTRYPOINT_PATH)
    for forbidden in ("kubectl", "docker ", "helm ", "argocd", "psql ", "alembic ", "git "):
        assert forbidden not in raw, f"хостовая инфраструктурная команда в entrypoint: {forbidden}"
    assert "liquibase" in raw
    assert "exec" in raw
    assert "liquibase_url" in raw, "разбор DSN — Python-логика, а не bash-парсинг"


def test_dockerfile_nonroot_distroless_practices() -> None:
    raw = read_text(DOCKERFILE_PATH)
    assert "FROM python:3.13-slim" in raw
    assert "USER 10001:10001" in raw
    assert "HEALTHCHECK" in raw
    assert "COPY --from=builder" in raw
    assert "chmod" in raw and "chown -R plugin:plugin" in raw
    # Политика задаётся в k8s Deployment (§7.4), а не директивой Dockerfile;
    # упоминание в поясняющем комментарии допустимо.
    assert not re.search(r"^\s*(?:ENV|LABEL)\s+imagePullPolicy", raw, re.MULTILINE)
    assert not re.search(r"(?:FROM|ARG|image:)\s+\S*:latest\b", raw)
    assert "LIQUIBASE_ALLOW_EXECUTE_COMMAND=false" in raw


# --- frontend: handshake, event.origin, без токена в URL ---------------------

def test_frontend_handshake_declares_plugin_rpc_1() -> None:
    js = read_text(APP_JS_PATH)
    html = read_text(INDEX_HTML_PATH)
    assert f'"{PROTOCOL}"' in js
    assert "plugin.ready" in js
    assert "host.ready" in js
    assert "grant.ready" in js
    assert "plugin.rpc.request" in js
    assert 'data-plugin-id="hello_plugin"' in html


def test_frontend_checks_event_origin() -> None:
    """§5.2: проверка event.origin ОБЯЗАТЕЛЬНА, включая ответы."""
    js = read_text(APP_JS_PATH)
    assert "event.origin" in js
    assert "event.source" in js
    assert re.search(r"event\.origin\s*===\s*HOST_ORIGIN", js), (
        "сверка с явно заданным доверенным origin"
    )
    assert '"null"' in js, "opaque origin не принимается"
    assert "function isTrusted(event)" in js
    assert "if (!isTrusted(event)) return" in js, (
        "проверка применяется к КАЖДОМУ сообщению, а не только к handshake"
    )


def test_frontend_rejects_foreign_origin_semantics() -> None:
    js = read_text(APP_JS_PATH)
    # postMessage ТОЛЬКО на доверенный origin, без '*'.
    assert "window.parent.postMessage(message, HOST_ORIGIN)" in js
    assert "postMessage(message, '*')" not in js
    assert "postMessage(message, \"*\")" not in js


def test_frontend_has_no_token_in_url() -> None:
    """§5.2: capability-токен живёт в backend, в URL query не попадает."""
    for path in (INDEX_HTML_PATH, APP_JS_PATH):
        raw = read_text(path)
        # Проверяем именно конструкции URL/query, а не упоминания в комментариях.
        assert not re.search(r"[?&]token\s*=", raw, re.IGNORECASE), (
            f"{path.name}: токен в query-параметре URL"
        )
        assert "location.search" not in raw
        assert "URLSearchParams" not in raw
        assert "window.location" not in raw
        assert "document.cookie" not in raw


def test_frontend_has_no_allow_same_origin() -> None:
    """Хост задачи 13 задаёт sandbox БЕЗ allow-same-origin; плагин его не просит."""
    raw = read_text(INDEX_HTML_PATH)
    assert "allow-same-origin" not in raw
    assert "sandbox=" not in raw


def test_frontend_has_no_shared_api_calls() -> None:
    """CSP connect-src 'none'; frontend не ходит в shared API напрямую."""
    html = read_text(INDEX_HTML_PATH)
    js = read_text(APP_JS_PATH)
    assert "connect-src 'none'" in html
    assert "frame-ancestors" in html
    assert "no-referrer" in html
    assert "<script src=\"app.js\" defer>" in html
    assert "unsafe-inline" not in html
    assert "unsafe-eval" not in html
    for forbidden in ("fetch(", "XMLHttpRequest", "/api/v1/", "$.ajax", "axios", "shared"):
        assert forbidden not in js, f"прямой вызов shared API: {forbidden}"


def test_manifest_has_no_artifact_digest_in_frontend_or_backend() -> None:
    """Запрет самореференции: digest приходит от хоста, не хранится в коде."""
    import json

    manifest = json.loads(read_text(MANIFEST_PATH))
    assert "artifact_digest" not in manifest
    assert "signature" not in manifest
    # В app.js digest — поле host.ready, а не захардкоженная константа плагина.
    js = read_text(APP_JS_PATH)
    assert "data.artifact_digest" not in js
    assert "data.digest" in js


def test_readme_explains_all_four_integration_points() -> None:
    raw = read_text(README_PATH).lower()
    for topic in ("oci", "dsn", "wake", "подпис", "strangler", "эфемерн"):
        assert topic in raw, f"README не раскрывает тему: {topic}"
