"""Unit-тесты ``GatewayAuth`` (§7.2 / §7.5, критерии §11 №6/7).

Проверяются: обязательность сессии и ``employee ∈ company``, урезанный
anon-scope для ``public_pages`` и ОТДЕЛЬНЫЙ rate limit, HMAC по raw_body,
обязательные заголовки, идемпотентность ``X-Prodavan-Delivery`` и отказ при
replay с другим телом. Тесты НЕ запускаются в рамках миграции.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from prodavan.application.plugins.gateway.auth import (
    HEADER_DELIVERY,
    HEADER_EVENT,
    HEADER_SIGNATURE,
    Sha256HmacVerifier,
)
from prodavan.application.plugins.gateway.errors import (
    GatewayError,
    GatewayErrorCode,
)
from prodavan.application.plugins.gateway.ports import (
    ANON_SCOPES,
    SESSION_SCOPES,
    WEBHOOK_SCOPES,
    DeliveryStatus,
    DeliveryVerdict,
    GatewayClaims,
    GatewayGrant,
    GatewaySession,
)

from .conftest import WEBHOOK_SECRET, build_harness


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _webhook_headers(body: bytes, *, delivery: str = "del-1") -> dict[str, str]:
    return {
        HEADER_SIGNATURE: _signature(WEBHOOK_SECRET, body),
        HEADER_EVENT: "chat.message.completed",
        HEADER_DELIVERY: delivery,
        "Content-Type": "application/json",
    }


# --------------------------------------------------------------------- HMAC


def test_hmac_verifier_accepts_valid_signature() -> None:
    verifier = Sha256HmacVerifier()
    assert verifier.verify(
        secret=WEBHOOK_SECRET,
        signing_bytes=b'{"a":1}',
        signature_header=_signature(WEBHOOK_SECRET, b'{"a":1}'),
    )


@pytest.mark.parametrize(
    "header",
    [None, "", "sha1=abc", "deadbeef", "sha256=xyz", "sha256=" + "0" * 63],
)
def test_hmac_verifier_rejects_malformed(header) -> None:
    verifier = Sha256HmacVerifier()
    assert not verifier.verify(
        secret=WEBHOOK_SECRET, signing_bytes=b"body", signature_header=header
    )


def test_hmac_verifier_is_bound_to_raw_body() -> None:
    verifier = Sha256HmacVerifier()
    signature = _signature(WEBHOOK_SECRET, b"original")
    assert not verifier.verify(
        secret=WEBHOOK_SECRET, signing_bytes=b"tampered", signature_header=signature
    )


# ----------------------------------------------------------- /pluginpage auth


@pytest.mark.asyncio
async def test_page_requires_session_for_api_path() -> None:
    """КРИТЕРИЙ №6: анонимный ``/pluginpage/.../api/*`` → 401."""
    h = build_harness()
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"X-Company-ID": "evil"},
        )
    assert response.status_code == 401
    assert response.json()["code"] == GatewayErrorCode.SESSION_REQUIRED.value
    # Ни wake, ни forward не должны были произойти.
    assert h.activation.wake_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_page_requires_employee_in_company() -> None:
    h = build_harness()
    h.sessions.memberships = {"emp-1": {"co-OTHER"}}
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == GatewayErrorCode.EMPLOYEE_NOT_IN_COMPANY.value
    assert h.sessions.membership_calls == [("emp-1", "co-123")]


@pytest.mark.asyncio
async def test_page_with_valid_session_passes_auth_and_wakes_backend() -> None:
    h = build_harness()
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 200
    assert h.activation.wake_calls == [("mod_equipment", "co-123", "inst-alpha-1")]
    assert len(h.backend_http.requests) == 1


@pytest.mark.asyncio
async def test_inactive_grant_is_fail_closed() -> None:
    h = build_harness(grant_active=False)
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items",
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == GatewayErrorCode.GRANT_INACTIVE.value


# --------------------------------------------------------------- public_pages


@pytest.mark.asyncio
async def test_public_page_allows_anonymous_with_reduced_scope() -> None:
    """Публичная страница пускает без сессии, но scope урезан."""
    h = build_harness(public_pages=frozenset({"/public/info"}))
    result = await h.auth.authorize_page(
        plugin_id="mod_equipment",
        company_id="co-123",
        path="public/info",
        headers={},
    )
    assert result.public is True
    assert result.session is None
    assert result.claims.anonymous is True
    assert result.claims.scopes == ANON_SCOPES
    assert result.claims.project_id is None
    assert result.claims.artifact_digest == h.grants.artifact_digest
    assert result.grant.artifact_digest == h.grants.artifact_digest
    assert result.grant.published_artifact_digest == h.grants.published_artifact_digest
    assert result.grant.capability_token == ""
    assert result.grant.operations == frozenset()
    # Проверяем, что урезанный scope действительно «урезан»: он не содержит
    # ни одного capability-scope обычной страницы и меньше по мощности.
    assert not (result.claims.scopes & SESSION_SCOPES)
    assert len(result.claims.scopes) < len(SESSION_SCOPES)
    assert "pluginpage:api" not in result.claims.scopes


@pytest.mark.asyncio
async def test_public_page_uses_separate_rate_limit_bucket() -> None:
    h = build_harness(public_pages=frozenset({"/public/info"}))
    await h.auth.authorize_page(
        plugin_id="mod_equipment",
        company_id="co-123",
        path="public/info",
        headers={},
    )
    buckets = [c["bucket"] for c in h.rate_limit.allow_calls]
    assert any(":public_page:" in b for b in buckets)
    assert not any(":pluginpage:" in b for b in buckets)
    public_call = next(c for c in h.rate_limit.allow_calls if ":public_page:" in c["bucket"])
    assert public_call["limit"] == h.auth.limits.public_page_per_minute
    assert public_call["limit"] < h.auth.limits.pluginpage_per_minute


@pytest.mark.asyncio
async def test_public_pages_exception_does_not_cover_api_paths() -> None:
    """Исключение public_pages НЕ распространяется на /api/* (иначе 401 обходится)."""
    h = build_harness(public_pages=frozenset({"/api"}))
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/api/items"
        )
    assert response.status_code == 401
    assert response.json()["code"] == GatewayErrorCode.SESSION_REQUIRED.value


@pytest.mark.asyncio
async def test_non_declared_page_is_not_public() -> None:
    """Публичность — ТОЛЬКО по явному списку из манифеста."""
    h = build_harness(public_pages=frozenset())
    with pytest.raises(GatewayError) as ei:
        await h.auth.authorize_page(
            plugin_id="mod_equipment",
            company_id="co-123",
            path="private/secret",
            headers={},
        )
    assert ei.value.gateway_code is GatewayErrorCode.SESSION_REQUIRED


# ------------------------------------------------------------- webhook HMAC


@pytest.mark.asyncio
async def test_webhook_without_signature_is_401() -> None:
    h = build_harness()
    body = b'{"event":"x"}'
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=body,
            headers={
                HEADER_EVENT: "chat.message.completed",
                HEADER_DELIVERY: "del-1",
            },
        )
    assert response.status_code == 401
    assert response.json()["code"] == GatewayErrorCode.WEBHOOK_HEADERS_MISSING.value
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_webhook_with_wrong_signature_is_401() -> None:
    h = build_harness()
    body = b'{"event":"x"}'
    headers = _webhook_headers(body)
    headers[HEADER_SIGNATURE] = "sha256=" + "0" * 64
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=body,
            headers=headers,
        )
    assert response.status_code == 401
    assert response.json()["code"] == GatewayErrorCode.INVALID_SIGNATURE.value
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_webhook_missing_event_or_delivery_is_401() -> None:
    """Отсутствие ``X-Prodavan-Event`` или ``X-Prodavan-Delivery`` → 401.

    На КАЖДУЮ итерацию — свежий harness: ``AsyncClient`` нельзя открыть
    повторно после выхода из ``async with`` (``Cannot reopen a client
    instance``). Переиспользование одного ``h.client`` в цикле было ошибкой
    самого теста, а не шлюза; инвариант fail-closed проверяется для обоих
    заголовков на независимом приложении.
    """

    body = b"{}"
    for missing in (HEADER_EVENT, HEADER_DELIVERY):
        h = build_harness()
        headers = _webhook_headers(body)
        headers.pop(missing)
        async with h.client as client:
            response = await client.post(
                "/webhook/mod_equipment/co-123/events",
                content=body,
                headers=headers,
            )
        assert response.status_code == 401
        assert response.json()["code"] == GatewayErrorCode.WEBHOOK_HEADERS_MISSING.value
        # Отказ не дошёл до backend.
        assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_webhook_secret_comes_from_secret_port_not_body() -> None:
    """Секрет берётся из SecretPort; подпись считается по raw_body, не по JSON."""
    h = build_harness()
    body = b'{"a": 1, "b": 2}'
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=body,
            headers=_webhook_headers(body),
        )
    assert response.status_code == 200
    assert h.secrets.calls == [("mod_equipment", "co-123")]
    assert h.backend_http.requests[0].body == body


@pytest.mark.asyncio
async def test_webhook_missing_secret_is_fail_closed_503() -> None:
    h = build_harness(secret=None)
    body = b"{}"
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=body,
            headers=_webhook_headers(body),
        )
    assert response.status_code == 503
    assert response.json()["code"] == GatewayErrorCode.SECRET_UNAVAILABLE.value


# ------------------------------------------------- идемпотентность / replay


@pytest.mark.asyncio
async def test_duplicate_delivery_returns_200_without_reprocessing() -> None:
    """КРИТЕРИЙ №7: повтор ``X-Prodavan-Delivery`` → 200 без повторной обработки."""
    h = build_harness()
    body = b'{"event":"x"}'
    headers = _webhook_headers(body, delivery="del-42")
    async with h.client as client:
        first = await client.post(
            "/webhook/mod_equipment/co-123/events", content=body, headers=headers
        )
        second = await client.post(
            "/webhook/mod_equipment/co-123/events", content=body, headers=headers
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.headers.get("Idempotent-Replayed") == "true"
    # Backend вызван РОВНО ОДИН раз; wake — тоже один.
    assert len(h.backend_http.requests) == 1
    assert len(h.activation.wake_calls) == 1
    assert len(h.rate_limit.claim_calls) == 2


@pytest.mark.asyncio
async def test_replay_same_delivery_with_different_body_is_401() -> None:
    """Защита от replay: тот же delivery-id, другое тело → отказ, не 200."""
    h = build_harness()
    headers_first = _webhook_headers(b"first-body", delivery="del-42")
    headers_replay = _webhook_headers(b"tampered-body", delivery="del-42")
    async with h.client as client:
        await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=b"first-body",
            headers=headers_first,
        )
        replay = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=b"tampered-body",
            headers=headers_replay,
        )
    assert replay.status_code == 401
    assert replay.json()["code"] == GatewayErrorCode.REPLAY_DETECTED.value
    assert len(h.backend_http.requests) == 1


@pytest.mark.asyncio
async def test_webhook_rate_limit_is_per_plugin_company() -> None:
    h = build_harness(rate_deny_bucket=":webhook:mod_equipment:co-123")
    body = b"{}"
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=body,
            headers=_webhook_headers(body),
        )
    assert response.status_code == 429
    assert response.json()["code"] == GatewayErrorCode.RATE_LIMITED.value
    assert response.headers.get("Retry-After") is not None
    buckets = [c["bucket"] for c in h.rate_limit.allow_calls]
    assert any("webhook_preauth_ip" in b for b in buckets)
    assert any("webhook_preauth_global" in b for b in buckets)
    assert buckets[-1].endswith(":webhook:mod_equipment:co-123")
    # Pre-HMAC buckets пропустили запрос; отклонён именно installation bucket.
    assert all(
        ":webhook:mod_equipment:co-123" not in c["bucket"]
        for c in h.rate_limit.allow_calls[:-1]
    )


@pytest.mark.asyncio
async def test_rate_limit_backend_failure_is_typed_not_500() -> None:
    """Недоступность Redis = типизированный 502/503, НЕ 500 и НЕ allow.

    Реальный дефект F11: RedisRateLimitAdapter бросал GatewayAdapterError
    (RuntimeError), который не являлся GatewayError и не ловился роутером —
    /webhook/* отдавал 500 Internal Server Error. Обёртка в auth.py обязана
    превратить любой отказ порта в GatewayError(UPSTREAM_ERROR).
    """

    class BrokenRateLimit:
        async def allow(self, **_kwargs: object) -> bool:
            raise RuntimeError("redis down")

    h = build_harness()
    h.auth.rate_limit = BrokenRateLimit()  # type: ignore[assignment]
    with pytest.raises(GatewayError) as exc:
        await h.auth._enforce_rate_limit(
            bucket="webhook:p:c",
            limit=10,
            window_seconds=60,
            event_kind="webhook",
            plugin_id="p",
            company_id="c",
        )
    assert exc.value.code == GatewayErrorCode.UPSTREAM_ERROR
    assert "rate limit backend unavailable" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_webhook_claims_have_reduced_webhook_scope() -> None:
    h = build_harness()
    body = b"{}"
    result = await h.auth.authorize_webhook(
        plugin_id="mod_equipment",
        company_id="co-123",
        path="events",
        method="POST",
        raw_body=body,
        headers=_webhook_headers(body),
    )
    assert result.claims.scopes == WEBHOOK_SCOPES
    assert result.claims.subject_id == "webhook"
    assert result.duplicate is False


# ------------------------------------------------------------ /webhook/ ≠ SPA


@pytest.mark.asyncio
async def test_webhook_root_never_returns_index_html() -> None:
    """КРИТЕРИЙ №9: ``/webhook/`` не уезжает в SPA и не отдаёт index.html."""
    h = build_harness()
    async with h.client as client:
        targets = ["/webhook/", "/webhook", "/webhook/mod_equipment/co-123/"]
        for method in ("get", "post", "put", "delete"):
            for target in targets:
                response = await getattr(client, method)(target)
                assert response.status_code == 400, (method, target)
                assert response.json()["code"] == GatewayErrorCode.INVALID_PATH.value
                assert "SPA fallback is forbidden" in response.json()["detail"]
                body = response.content.decode("utf-8", errors="replace").lower()
                assert "<html" not in body and "index.html" not in body
    # Ни один запрос не дошёл до backend/static.
    assert h.backend_http.requests == []
    assert h.static_http.requests == []


@pytest.mark.asyncio
async def test_pluginpage_root_never_returns_index_html() -> None:
    h = build_harness()
    async with h.client as client:
        response = await client.get("/pluginpage/")
    assert response.status_code == 400
    assert "<html" not in response.text.lower()


@pytest.mark.asyncio
async def test_health_and_runtime_not_installed() -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from prodavan.api.v1.plugin_gateway import router

    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        webhook = await client.post("/webhook/mod_equipment/co-123/events", content=b"{}")
    assert health.status_code == 503
    assert webhook.status_code == 503
    assert health.json()["code"] == GatewayErrorCode.RUNTIME_NOT_INSTALLED.value


# ------------------------------------------------------------- прочее


def test_claims_reject_control_characters() -> None:
    """Защита от header injection через доверенные значения."""
    with pytest.raises(ValueError):
        GatewayClaims(
            plugin_id="mod_equipment\r\nX-Evil: 1",
            company_id="co-123",
            subject_id="s",
        )


def test_anon_claims_reject_employee_id() -> None:
    with pytest.raises(ValueError):
        GatewayClaims(
            plugin_id="p",
            company_id="c",
            subject_id="anon",
            anonymous=True,
            employee_id="emp-1",
        )


def test_duplicate_verdict_flag() -> None:
    assert DeliveryVerdict(status=DeliveryStatus.DUPLICATE).is_duplicate
    assert not DeliveryVerdict(status=DeliveryStatus.FIRST).is_duplicate


def test_gateway_session_and_grant_shapes() -> None:
    session = GatewaySession(
        session_id="s", employee_id="e", subject_id="employee:e",
        company_ids=frozenset({"co-123"}),
    )
    grant = GatewayGrant(grant_id="g", active=True, capability_token="t")
    assert "co-123" in session.company_ids
    assert grant.active and grant.capability_token == "t"


# ------------------------------------------------------------- аудит (§11 №11)


@pytest.mark.asyncio
async def test_audit_never_contains_raw_body_tokens_or_cookies() -> None:
    """Критерий №11: аудит не содержит raw payload / токенов / секретов."""
    h = build_harness()
    secret_body = b'{"token":"SUPER-SECRET-DO-NOT-LOG","password":"hunter2"}'
    headers = _webhook_headers(secret_body, delivery="del-audit-1")
    headers["Cookie"] = "session=SUPER-SECRET-COOKIE"
    async with h.client as client:
        await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=secret_body,
            headers=headers,
        )
    # ``GatewayAuditEvent`` — slots-датакласс: перебираем поля явно.
    serialized = " ".join(
        f"{f}={getattr(e, f, '')!r}"
        for e in h.audit.events
        for f in (
            "event", "plugin_id", "company_id", "decision", "code",
            "subject_id", "grant_id", "path_digest", "payload_digest",
            "reason", "request_id", "metadata",
        )
    )
    for forbidden in ("SUPER-SECRET-DO-NOT-LOG", "hunter2", "SUPER-SECRET-COOKIE"):
        assert forbidden not in serialized
    # raw body не попал ни в одно поле; есть только digest.
    assert all(
        b"SUPER-SECRET" not in str(e.payload_digest).encode() for e in h.audit.events
    )
    assert any(e.payload_digest for e in h.audit.events)


@pytest.mark.asyncio
async def test_audit_failure_is_fail_closed() -> None:
    """Ошибка append-only аудита = отказ, а не «запрос всё равно разрешён»."""
    h = build_harness(audit_fail=True)
    body = b"{}"
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=body,
            headers=_webhook_headers(body),
        )
    assert response.status_code == 503
    assert response.json()["code"] == GatewayErrorCode.AUDIT_APPEND_FAILED.value
    assert h.backend_http.requests == []
