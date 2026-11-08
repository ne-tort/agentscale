"""Acceptance-тесты HTTP surface ``/capabilities/v1/*`` (задача 04, F2).

Покрытие по ТЗ:
  1. запрос без токена -> 401;
  2. токен с ``typ=pod_bridge`` -> не проходит (401);
  3. запрос к ``/agent/events`` через plugin surface -> 403;
  4. body с полем ``sql`` -> 422;
  5. неизвестный адаптер -> 503;
  6. квота -> 429;
  7. входящий ``X-Company-ID: evil`` игнорируется, компания берётся из claims;
  8. ``/capabilities/v1/cache/get`` с валидным токеном -> 200.

Дополнительно: коды problem+json, RevokedGrant -> 403, отзыв grant_id,
запрет command/args/raw, GET/DELETE typed payload, health защищён.

Тесты используют ``fastapi.testclient.TestClient`` и НЕ ЗАПУСКАЮТСЯ здесь
(Verification freeze: запуск отложен до явного решения о верификации).
"""

from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from prodavan.application.plugins.identity.mint import mint_plugin_claims
from prodavan.application.plugins.identity.revocation import InMemoryRevocation, GrantRevocation
from prodavan.application.plugins.capabilities.errors import (
    RevokedGrant,
    QuotaExceededError,
    ValidationError,
)
from prodavan.application.plugins.capabilities.ports import AdapterMissingError
from tests.unit.plugins.transport.conftest import (
    ALL_SCOPES,
    FakeDecision,
    FakeGrant,
    FakeInstance,
    TEST_COMPANY,
    TEST_DIGEST,
    TEST_SECRET,
    auth,
    peek,
)

CACHE_GET = "/capabilities/v1/cache/get"
CACHE_BODY = {"key": "tenant:co_123:plugin:mod_equipment:inst_abc:k1"}
EMPTY_DIGEST = "sha256:" + "0" * 64


# ---------------------------------------------------------------------------
# 1. Нет токена
# ---------------------------------------------------------------------------


def test_request_without_token_is_401(client: TestClient) -> None:
    resp = client.post(CACHE_GET, json=CACHE_BODY)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] == "UNAUTHORIZED"


def test_non_bearer_authorization_is_401(client: TestClient, token: str) -> None:
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers={"Authorization": f"Basic {token}"})
    assert resp.status_code == 401


def test_empty_bearer_is_401(client: TestClient) -> None:
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers={"Authorization": "Bearer   "})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. typ=pod_bridge не проходит
# ---------------------------------------------------------------------------


def test_pod_bridge_token_is_rejected(client: TestClient) -> None:
    """Агентский trust (:8001) не обслуживается plugin surface (§4.1, кр. 17)."""
    now = 1_700_000_000
    pod_token = jwt.encode(
        {
            "iss": "agentscale-pods",
            "aud": "prodavan-pod-bridge",
            "typ": "pod_bridge",
            "sub": "pod:pod-1",
            "pod_id": "pod-1",
            "company_id": TEST_COMPANY,
            "project_id": "proj-1",
            "grant_id": "grant_01",
            "artifact_digest": TEST_DIGEST,
            "scopes": list(ALL_SCOPES),
            "iat": now,
            "exp": now + 10_000,
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    assert peek(pod_token)["typ"] == "pod_bridge"
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(pod_token))
    assert resp.status_code == 401
    assert resp.json()["code"] == "WRONG_TYP"


def test_plugin_token_with_wrong_typ_claim_is_rejected(client: TestClient, secret: str) -> None:
    """Подписанный тем же ключом токен, но с подменённым typ — отказ."""
    grant = FakeGrant()
    token = mint_plugin_claims(grant=grant, instance=FakeInstance(), secret=secret)
    payload = peek(token)
    payload["typ"] = "pod_bridge"
    forged = jwt.encode(payload, secret, algorithm="HS256")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(forged))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. /agent/events через plugin surface -> 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/agent/events",
        "/api/v1/agent/events",
        "/api/v1/internal/pods/pod-1/workspace-archive",
        "/api/v1/projects/prj_1/infra/cache/k",
        "/webhook/mod_equipment/co_123",
        "/pluginpage/mod_equipment/co_123/api/x",
    ],
)
def test_agent_and_external_surface_are_403(client: TestClient, token: str, path: str) -> None:
    """Критерий №17: плагин с PluginClaims не имеет доступа к agent-surface.

    Роутер смонтирован без внешнего префикса; запрос к чужому пути матчится
    catch-all, но ``assert_plugin_surface`` в ``get_plugin_claims`` всё равно
    закрывает его ДО вызова сервиса (fail-closed по пути из request.url.path).
    """
    resp = client.post(path, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 403
    assert resp.json()["code"] in {"FORBIDDEN_SURFACE", "NOT_FOUND", "FORBIDDEN"}


def test_capabilities_path_with_agent_suffix_is_403(client: TestClient, token: str) -> None:
    """Попытка обхода allowlist вложенным agent-путём."""
    resp = client.post(
        "/capabilities/v1/../agent/events",
        json=CACHE_BODY,
        headers=auth(token),
    )
    assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# 4. body с полем sql -> 422 (запрет generic dispatch, ADR-002)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["sql", "command", "args", "raw", "params", "payload"])
def test_generic_dispatch_fields_are_422(client: TestClient, token: str, field: str) -> None:
    body = {**CACHE_BODY, field: "SELECT * FROM companies"}
    resp = client.post(CACHE_GET, json=body, headers=auth(token))
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] in {"VALIDATION_ERROR", "FORBIDDEN_GENERIC_DISPATCH"}


@pytest.mark.parametrize("field", ["company_id", "grant_id", "artifact_digest", "scopes"])
def test_identity_spoofing_fields_are_422(client: TestClient, token: str, field: str) -> None:
    body = {**CACHE_BODY, field: "evil"}
    resp = client.post(CACHE_GET, json=body, headers=auth(token))
    assert resp.status_code == 422


def test_docs_query_with_raw_filter_is_422(client: TestClient, token: str) -> None:
    body = {
        "namespace": "tenant_co_123",
        "collection": "equipment",
        "predicates": [{"field": "sku", "operator": "eq", "values": ["x"]}],
        "limit": 10,
        "raw": '{"$where": "..."}',
    }
    resp = client.post("/capabilities/v1/docs/query", json=body, headers=auth(token))
    assert resp.status_code == 422


def test_unknown_extra_field_is_422(client: TestClient, token: str) -> None:
    resp = client.post(
        CACHE_GET,
        json={**CACHE_BODY, "unexpected": 1},
        headers=auth(token),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. Неизвестный адаптер -> 503
# ---------------------------------------------------------------------------


def test_adapter_missing_decision_is_503(client: TestClient, token: str, service) -> None:
    service.result = FakeDecision("deny", "ADAPTER_MISSING", reason="adapter_missing")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 503
    assert resp.json()["code"] == "ADAPTER_MISSING"


def test_adapter_missing_exception_is_503(client: TestClient, token: str, service) -> None:
    service.raise_exc = AdapterMissingError("cache")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 503
    assert resp.json()["code"] == "ADAPTER_MISSING"


def test_unknown_capability_is_503(client: TestClient, token: str, service) -> None:
    service.result = FakeDecision("deny", "CAPABILITY_UNKNOWN")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 503


def test_service_not_configured_is_503(app, token: str) -> None:
    """Fail-closed: нет привязанного сервиса = явная ошибка, не allow."""
    del app.state.plugin_capability_service
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 503
    assert resp.json()["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 6. Квота -> 429
# ---------------------------------------------------------------------------


def test_quota_exceeded_decision_is_429(client: TestClient, token: str, service) -> None:
    """Acceptance №10 / §4.4: QUOTA_EXCEEDED -> 429, а не 200/500."""
    service.result = FakeDecision(
        "deny", "QUOTA_EXCEEDED", reason="ops_per_minute exceeded"
    )
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 429
    assert resp.json()["code"] == "QUOTA_EXCEEDED"


def test_quota_exceeded_exception_is_429(client: TestClient, token: str, service) -> None:
    service.raise_exc = QuotaExceededError("limit")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 429
    assert resp.json()["code"] == "QUOTA_EXCEEDED"


def test_revoked_grant_decision_is_403(client: TestClient, token: str, service) -> None:
    """Acceptance №12: отзыв grant_id -> 403."""
    service.result = FakeDecision("deny", "GRANT_REVOKED", reason="grant_revoked")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 403
    assert resp.json()["code"] == "GRANT_REVOKED"


def test_revoked_grant_exception_is_403(client: TestClient, token: str, service) -> None:
    service.raise_exc = RevokedGrant("revoked")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 403


def test_invalid_request_decision_is_422(client: TestClient, token: str, service) -> None:
    service.result = FakeDecision("deny", "INVALID_REQUEST")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 422


def test_identity_denied_is_401(client: TestClient, token: str, service) -> None:
    service.result = FakeDecision("deny", "IDENTITY_DENIED")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 401


def test_validation_exception_is_422(client: TestClient, token: str, service) -> None:
    service.raise_exc = ValidationError("bad typed contract")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 7. Входящий X-Company-ID: evil игнорируется, берётся из claims
# ---------------------------------------------------------------------------


def test_incoming_x_company_id_is_ignored(client: TestClient, token: str, service) -> None:
    headers = {
        **auth(token),
        "X-Company-ID": "evil",
        "X-Plugin-ID": "evil-plugin",
        "X-Project-ID": "evil-project",
        "X-Plugin-Capability-Token": "evil-token",
    }
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=headers)
    assert resp.status_code == 200
    assert service.calls, "сервис должен быть вызван"
    call = service.calls[-1]
    assert call["claims"].company_id == TEST_COMPANY
    # Envelope доменного запроса собран из claims, а не из заголовков.
    assert call["request"].envelope.company_id == TEST_COMPANY
    assert call["request"].envelope.subject_id == call["claims"].sub
    assert call["request"].envelope.grant_id == call["claims"].grant_id


def test_health_uses_company_from_claims(client: TestClient, token: str) -> None:
    resp = client.get(
        "/capabilities/v1/health",
        headers={**auth(token), "X-Company-ID": "evil"},
    )
    assert resp.status_code == 200
    assert resp.json()["company_id"] == TEST_COMPANY
    assert resp.json()["surface"] == "plugin-capabilities"


def test_health_without_token_is_401(client: TestClient) -> None:
    resp = client.get("/capabilities/v1/health")
    assert resp.status_code == 401


def test_revocation_check_blocks_old_token(app, keyring, secret: str) -> None:
    """Отзыв по grant_id: ещё не истёкший токен перестаёт работать (кр. 12)."""
    app.state.plugin_revocation = GrantRevocation(
        InMemoryRevocation(initially_revoked=("grant_01",))
    )
    token = mint_plugin_claims(grant=FakeGrant(), instance=FakeInstance(), secret=secret)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 403
    assert resp.json()["code"] == "GRANT_REVOKED"


def test_missing_identity_config_is_503(app, token: str) -> None:
    """Fail-closed: нет keyring/revocation -> 503, а не пропуск."""
    del app.state.plugin_keyring
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 503
    assert resp.json()["code"] == "IDENTITY_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# 8. Валидный токен -> 200
# ---------------------------------------------------------------------------


def test_cache_get_with_valid_token_is_200(client: TestClient, token: str, service) -> None:
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"decision": "allow", "data": {"ok": True}}
    call = service.calls[-1]
    assert call["capability"] == "cache"
    # Публичный реестр операций §4.4 — точечная форма.
    assert call["operation"] == "cache.get"
    assert call["request"].key == CACHE_BODY["key"]


@pytest.mark.parametrize(
    ("op", "body"),
    [
        ("get", {"key": "k"}),
        ("set", {"key": "k", "value": "v"}),
        ("delete", {"key": "k"}),
        ("incr", {"key": "k", "amount": 2}),
        (
            "publish",
            {"channel": "ch", "message_digest": EMPTY_DIGEST},
        ),
    ],
)
def test_all_cache_operations_with_valid_token(
    client: TestClient, token: str, service, op: str, body: dict
) -> None:
    resp = client.post(f"/capabilities/v1/cache/{op}", json=body, headers=auth(token))
    assert resp.status_code == 200
    assert service.calls[-1]["operation"] == f"cache.{op}"


def test_unknown_cache_operation_is_422(client: TestClient, token: str) -> None:
    resp = client.post("/capabilities/v1/cache/drop_all", json={"key": "k"}, headers=auth(token))
    assert resp.status_code == 422


def test_all_endpoints_reachable_with_valid_token(client: TestClient, token: str, service) -> None:
    """СValидным токеном все 13+ эндпоинтов отдают 200 ( typed контракты )."""
    digest = EMPTY_DIGEST
    cases: list[tuple[str, str, dict | None]] = [
        ("POST", "/capabilities/v1/objects/credentials", {
            "bucket": "tenant-co-123", "key_prefix": "plugin/inst_abc/",
            "permissions": ["read"], "ttl_seconds": 900,
        }),
        ("GET", "/capabilities/v1/objects", {"bucket": "tenant-co-123", "key_prefix": "plugin/", "limit": 10}),
        ("POST", "/capabilities/v1/objects", {"bucket": "tenant-co-123", "key_prefix": "plugin/", "limit": 10}),
        ("DELETE", "/capabilities/v1/objects/k1", {"bucket": "tenant-co-123"}),
        ("POST", "/capabilities/v1/docs/query", {
            "namespace": "tenant_co_123", "collection": "equipment",
            "predicates": [{"field": "sku", "operator": "eq", "values": ["x"]}],
            "limit": 10,
        }),
        ("POST", "/capabilities/v1/events/publish", {
            "topic": "agentscale.plugin.events", "event_type": "equipment.updated",
            "partition_key": "co_123", "payload_digest": digest,
        }),
        ("POST", "/capabilities/v1/jobs/submit", {
            "task_name": "export_xlsx", "run_id": "run_1", "arguments_digest": digest,
        }),
        ("GET", "/capabilities/v1/jobs/job_1", {"run_id": "run_1"}),
        ("POST", "/capabilities/v1/jobs/job_1/cancel", {"run_id": "run_1", "lease_id": "lease_1"}),
        ("POST", "/capabilities/v1/pods/invoke", {
            "pod_id": "pod_1", "action": "read_status", "target_ref": "status",
        }),
        ("POST", "/capabilities/v1/mcp/register", {
            "server_id": "srv_1",
            "tools": [{
                "tool_name": "cache_get", "tool_version": "1.0.0",
                "capability": "cache", "operation": "cache.get",
                "input_schema_digest": digest,
            }],
        }),
        ("POST", "/capabilities/v1/secrets/read", {"secret_ref": "ref_1"}),
        ("POST", "/capabilities/v1/network/request", {
            "host": "api.example.com", "port": 443, "path": "/v1/data",
            "method": "GET", "body_digest": digest,
        }),
    ]
    for method, path, body in cases:
        kwargs: dict = {"headers": auth(token)}
        if method == "GET":
            kwargs["params"] = body
        else:
            kwargs["json"] = body
        resp = client.request(method, path, **kwargs)
        assert resp.status_code == 200, f"{method} {path}: {resp.status_code} {resp.text}"
        assert resp.json()["decision"] == "allow"

    ops = {call["operation"] for call in service.calls}
    assert "objects.scoped_credentials" in ops
    assert "objects.list" in ops
    assert "objects.delete" in ops
    assert "docs.query" in ops
    assert "events.publish" in ops
    assert {"jobs.submit", "jobs.get", "jobs.cancel"} <= ops
    assert "pods.invoke" in ops
    assert "mcp.register" in ops
    assert "secrets.read" in ops
    assert "network.egress" in ops


def test_problem_json_always_has_code(client: TestClient, token: str, service) -> None:
    service.result = FakeDecision("deny", "QUOTA_EXCEEDED")
    resp = client.post(CACHE_GET, json=CACHE_BODY, headers=auth(token))
    payload = resp.json()
    assert {"type", "title", "status", "code", "detail"} <= set(payload)
    assert payload["status"] == 429
