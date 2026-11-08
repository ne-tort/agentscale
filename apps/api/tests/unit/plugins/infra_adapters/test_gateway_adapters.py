"""Тесты gateway-адаптеров (задача 11) — ПИШЕМ, НЕ ЗАПУСКАЕМ (verification freeze).

Покрытие по ТЗ:
* HMAC верный / неверный / повтор с другим телом (replay);
* ``claim_delivery`` хранит отпечаток первой доставки;
* SSE не буферизуется;
* ``Authorization`` выставляется из свежего ``WakeResult.token``;
* аудит без raw body/токенов (redaction);
* сборка без порта падает (см. ``test_build.py``);
* WakeHook вызывается до handler (см. ``test_kafka_adapters.py``).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest

from prodavan.application.plugins.gateway.errors import GatewayErrorCode
from prodavan.application.plugins.gateway.headers import (
    HEADER_CAPABILITY_TOKEN,
    build_forward_headers,
)
from prodavan.application.plugins.gateway.ports import (
    DeliveryStatus,
    GatewayAuditEvent,
    GatewayClaims,
    GatewayGrant,
    GatewaySession,
)
from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
    HEADER_AUTHORIZATION,
    ActivationAdapter,
    AuditAdapter,
    BackendHttpAdapter,
    BearerAuthorizationInjector,
    GatewayAdapterError,
    GrantAdapter,
    GrantRecord,
    InMemoryGrantStore,
    InMemoryWebhookSecretRefs,
    MappingInstanceResolver,
    RedisRateLimitAdapter,
    SecretAdapter,
    SecretKeyRing,
    SessionAdapter,
    Sha256HmacAdapter,
    with_trusted_bearer,
)

from .conftest import (
    WEBHOOK_SECRET,
    WEBHOOK_SECRET_OLD,
    FakeActivator,
    FakeCookieSessions,
    FakeDirectory,
    FakeHttpxClient,
    FakeHttpxResponse,
    FakeJwtValidator,
    FakeRedis,
    FakeSecretStore,
    ReadyProbe,
    make_backend_request,
    signature,
)


# ---------------------------------------------------------------------------
# HMAC: верный / неверный / 64 hex / ротация / повтор с другим телом
# ---------------------------------------------------------------------------


def test_hmac_valid_signature() -> None:
    body = b'{"offer": 42}'
    verifier = Sha256HmacAdapter()
    assert verifier.verify(
        secret=WEBHOOK_SECRET,
        signing_bytes=body,
        signature_header=signature(WEBHOOK_SECRET, body),
    )


def test_hmac_invalid_signature_and_wrong_body() -> None:
    body = b'{"offer": 42}'
    verifier = Sha256HmacAdapter()
    # Неверная подпись (другое тело).
    assert not verifier.verify(
        secret=WEBHOOK_SECRET,
        signing_bytes=body,
        signature_header=signature(WEBHOOK_SECRET, b"other body"),
    )
    # Неверный секрет.
    assert not verifier.verify(
        secret=WEBHOOK_SECRET,
        signing_bytes=body,
        signature_header=signature("wrong-secret", body),
    )
    # Отсутствующий/битый заголовок.
    assert not verifier.verify(secret=WEBHOOK_SECRET, signing_bytes=body, signature_header=None)
    assert not verifier.verify(secret=WEBHOOK_SECRET, signing_bytes=body, signature_header="sha256=ABC")
    # Ровно 64 hex: 63 и 65 символов, uppercase, не-hex — отказ.
    assert not verifier.verify(
        secret=WEBHOOK_SECRET, signing_bytes=body, signature_header="sha256=" + "a" * 63
    )
    assert not verifier.verify(
        secret=WEBHOOK_SECRET, signing_bytes=body, signature_header="sha256=" + "a" * 65
    )
    assert not verifier.verify(
        secret=WEBHOOK_SECRET, signing_bytes=body, signature_header="sha256=" + "A" * 64
    )
    assert not verifier.verify(
        secret=WEBHOOK_SECRET, signing_bytes=body, signature_header="sha256=" + "z" * 64
    )


def test_hmac_compare_digest_is_used() -> None:
    """Сравнение константное по времени: подмена ``compare_digest`` видна."""
    body = b"payload"
    calls: list[tuple[str, str]] = []
    original = hmac.compare_digest

    def _spy(a: Any, b: Any) -> bool:
        calls.append((str(a), str(b)))
        return original(a, b)

    hmac.compare_digest = _spy  # type: ignore[assignment]
    try:
        Sha256HmacAdapter().verify(
            secret=WEBHOOK_SECRET,
            signing_bytes=body,
            signature_header=signature(WEBHOOK_SECRET, body),
        )
    finally:
        hmac.compare_digest = original  # type: ignore[assignment]
    assert calls, "HMAC должен сравниваться через hmac.compare_digest"


@pytest.mark.asyncio
async def test_secret_rotation_pair_old_key_alive_until_drained() -> None:
    """Ротация: старый ключ живёт до исчерпания in-flight (§7.5)."""
    store = FakeSecretStore(
        {"ref-active": WEBHOOK_SECRET, "ref-old": WEBHOOK_SECRET_OLD}
    )
    ring = SecretKeyRing(active_ref="ref-active", previous_refs=("ref-old",))
    refs = InMemoryWebhookSecretRefs({("mod_equipment", "co-123"): ring})
    adapter = SecretAdapter(refs=refs, secret_reader=store.get)
    verifier = Sha256HmacAdapter()
    body = b'{"delivery": 1}'

    active = await adapter.webhook_secret(plugin_id="mod_equipment", company_id="co-123")
    assert active == WEBHOOK_SECRET
    # Подпись НОВЫМ ключом проходит.
    assert verifier.verify(
        secret=active or "", signing_bytes=body, signature_header=signature(WEBHOOK_SECRET, body)
    )
    # Подпись СТАРЫМ ключом (in-flight доставка) тоже проходит до retire.
    assert verifier.verify(
        secret=active or "",
        signing_bytes=body,
        signature_header=signature(WEBHOOK_SECRET_OLD, body),
    )

    # Пока in-flight > 0, retire НЕ удаляет старый ключ.
    await adapter.begin_in_flight(plugin_id="mod_equipment", company_id="co-123", ref="ref-old")
    assert await adapter.retire_drained(plugin_id="mod_equipment", company_id="co-123") == ()
    await adapter.end_in_flight(plugin_id="mod_equipment", company_id="co-123", ref="ref-old")
    assert await adapter.retire_drained(plugin_id="mod_equipment", company_id="co-123") == ("ref-old",)

    await adapter.webhook_secret(plugin_id="mod_equipment", company_id="co-123")
    assert not verifier.verify(
        secret=WEBHOOK_SECRET,
        signing_bytes=body,
        signature_header=signature(WEBHOOK_SECRET_OLD, body),
    )


@pytest.mark.asyncio
async def test_secret_missing_is_fail_closed() -> None:
    refs = InMemoryWebhookSecretRefs()  # канал не настроен
    adapter = SecretAdapter(refs=refs, secret_reader=FakeSecretStore().get)
    assert await adapter.webhook_secret(plugin_id="p", company_id="c") is None


# ---------------------------------------------------------------------------
# claim_delivery: хранит ОТПЕЧАТОК первой доставки (иначе replay с чужим
# телом прошёл бы как «уже обработано»)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_delivery_stores_first_fingerprint() -> None:
    redis = FakeRedis()
    rl = RedisRateLimitAdapter(client=redis)
    first = await rl.claim_delivery(key="delivery:1", ttl_seconds=60, token="digest-of-body-A")
    assert first.status is DeliveryStatus.FIRST
    assert redis.store["delivery:1"] == "digest-of-body-A"
    assert redis.ttl["delivery:1"] == 60

    # Повтор с ТЕМ ЖЕ телом: DUPLICATE и тот же отпечаток.
    same = await rl.claim_delivery(key="delivery:1", ttl_seconds=60, token="digest-of-body-A")
    assert same.status is DeliveryStatus.DUPLICATE
    assert same.previous_token == "digest-of-body-A"

    # Повтор с ДРУГИМ телом: отпечаток ПЕРВОЙ доставки сохранён, шлюз
    # (GatewayAuth) сравнит его и даст 401 REPLAY_DETECTED, а не 200.
    other = await rl.claim_delivery(key="delivery:1", ttl_seconds=60, token="digest-of-body-B")
    assert other.status is DeliveryStatus.DUPLICATE
    assert other.previous_token == "digest-of-body-A"
    assert other.previous_token != "digest-of-body-B"
    assert hmac.compare_digest(other.previous_token or "", "digest-of-body-B") is False


@pytest.mark.asyncio
async def test_replay_with_other_body_is_denied_by_gateway_auth() -> None:
    """Интеграция claim_delivery + GatewayAuth: повтор с чужим телом → 401."""
    from prodavan.application.plugins.gateway.auth import GatewayAuth, GatewayLimits
    from prodavan.application.plugins.gateway.errors import GatewayError

    redis = FakeRedis()
    audit = _AuditSink()
    auth = GatewayAuth(
        sessions=_NoSession(),
        grants=_ActiveGrant(),
        secrets=_StaticSecret(),
        rate_limit=RedisRateLimitAdapter(client=redis),
        audit=audit,
        hmac_verifier=Sha256HmacAdapter(),
        limits=GatewayLimits(),
    )
    body_a = b'{"a":1}'
    headers_a = {
        "X-Prodavan-Signature": signature(WEBHOOK_SECRET, body_a),
        "X-Prodavan-Event": "offer.created",
        "X-Prodavan-Delivery": "del-1",
    }
    first = await auth.authorize_webhook(
        plugin_id="p", company_id="c", path="hook", method="POST",
        raw_body=body_a, headers=headers_a,
    )
    assert first.duplicate is False

    body_b = b'{"a":2}'  # ДРУГОЕ тело, тот же delivery-id
    headers_b = dict(headers_a)
    headers_b["X-Prodavan-Signature"] = signature(WEBHOOK_SECRET, body_b)
    with pytest.raises(GatewayError) as exc:
        await auth.authorize_webhook(
            plugin_id="p", company_id="c", path="hook", method="POST",
            raw_body=body_b, headers=headers_b,
        )
    assert exc.value.gateway_code is GatewayErrorCode.REPLAY_DETECTED


@pytest.mark.asyncio
async def test_rate_limit_separate_anon_bucket_and_fail_closed_redis() -> None:
    redis = FakeRedis()
    rl = RedisRateLimitAdapter(client=redis)
    # Отдельный namespace для анонимных публичных страниц (§7.2).
    assert await rl.allow(bucket="rl:public_page:p:c", limit=2, window_seconds=60)
    assert await rl.allow(bucket="rl:public_page:p:c", limit=2, window_seconds=60)
    assert not await rl.allow(bucket="rl:public_page:p:c", limit=2, window_seconds=60)
    # Авторизованный bucket не «съеден» анонимным.
    assert await rl.allow(bucket="rl:pluginpage:p:c", limit=2, window_seconds=60)
    assert await rl.allow_anon(subject="1.2.3.4", plugin_id="p", company_id="c", limit=5, window_seconds=60)

    down = RedisRateLimitAdapter(client=FakeRedis(fail=True))
    with pytest.raises(GatewayAdapterError):
        await down.allow(bucket="rl:webhook:p:c", limit=10, window_seconds=60)
    with pytest.raises(GatewayAdapterError):
        await down.claim_delivery(key="d", ttl_seconds=10, token="t")


# ---------------------------------------------------------------------------
# Authorization: Bearer <WakeResult.token> — ЯВНО, оба заголовка
# ---------------------------------------------------------------------------


def test_with_trusted_bearer_adds_authorization_and_keeps_capability_header() -> None:
    incoming = {
        "X-Plugin-Capability-Token": "fresh-wake-token",
        "Authorization": "Bearer client-forged",
        "Cookie": "session=client",
        "Host": "evil.example",
        "Content-Length": "999",
        "Accept": "application/json",
    }
    out = build_forward_headers(incoming, _claims(), _grant("fresh-wake-token"))
    safe = with_trusted_bearer(out, capability_token="fresh-wake-token")
    assert safe[HEADER_CAPABILITY_TOKEN] == "fresh-wake-token"
    assert safe[HEADER_AUTHORIZATION] == "Bearer fresh-wake-token"
    assert "Cookie" not in safe
    assert "Host" not in safe
    assert "Content-Length" not in safe
    assert safe["Accept"] == "application/json"


def test_with_trusted_bearer_no_token_for_anon_and_static() -> None:
    out = with_trusted_bearer(
        {"Authorization": "Bearer client", "X-Plugin-Capability-Token": "client"},
        capability_token="",
    )
    assert HEADER_AUTHORIZATION not in out
    assert HEADER_CAPABILITY_TOKEN not in out
    assert "Authorization" not in out


@pytest.mark.asyncio
async def test_activation_adapter_maps_wake_result_token_to_lease() -> None:
    """Мост к задаче 09: (plugin, company) → instance_id, WakeResult.token → WakeLease."""
    activator = FakeActivator(token="FRESH-JWT-FROM-WAKE")
    adapter = ActivationAdapter(
        activator=activator,
        instance_resolver=MappingInstanceResolver({("mod_equipment", "co-123"): "inst_abc"}),
        readiness=ReadyProbe(ready=True),
    )
    lease = await adapter.wake(plugin_id="mod_equipment", company_id="co-123")
    assert activator.wake_calls == [("inst_abc", None)]
    assert lease.capability_token == "FRESH-JWT-FROM-WAKE"
    assert lease.ready is True
    assert await adapter.wait_ready(lease, timeout_seconds=5.0) is True
    assert adapter.wait_calls[0][0] == "mod_equipment"


@pytest.mark.asyncio
async def test_activation_wait_ready_repolls_readiness_port() -> None:
    """``wait_ready`` — ПОВТОРНЫЙ опрос ReadinessPort, а не «всегда True»."""
    probe = ReadyProbe(ready=False)
    adapter = ActivationAdapter(
        activator=FakeActivator(),
        instance_resolver=MappingInstanceResolver({("p", "c"): "inst_1"}),
        readiness=probe,
    )
    lease = await adapter.wake(plugin_id="p", company_id="c")
    assert await adapter.wait_ready(lease, timeout_seconds=5.0) is False
    assert probe.calls == ["inst_1"]


@pytest.mark.asyncio
async def test_activation_unknown_instance_is_fail_closed() -> None:
    adapter = ActivationAdapter(
        activator=FakeActivator(),
        instance_resolver=MappingInstanceResolver({}),
    )
    with pytest.raises(GatewayAdapterError):
        await adapter.wake(plugin_id="unknown", company_id="c")


@pytest.mark.asyncio
async def test_authorization_is_set_from_fresh_wake_result_token() -> None:
    """КЛЮЧЕВОЕ: Authorization берётся из СВЕЖЕГО WakeResult.token, а не из grant'а/клиента."""
    activator = FakeActivator(token="NEW-FRESH-TOKEN")
    activation = ActivationAdapter(
        activator=activator,
        instance_resolver=MappingInstanceResolver({("mod_equipment", "co-123"): "inst_abc"}),
    )
    lease = await activation.wake(plugin_id="mod_equipment", company_id="co-123")

    claims = _claims()
    # Имитируем proxy.py: grant получает НОВЫЙ токен из lease.
    effective_grant = GatewayGrant(
        grant_id="grant-1", active=True,
        capability_token=lease.capability_token, operations=frozenset(),
    )
    trusted = build_forward_headers(
        {"Authorization": "Bearer client-forged", "Cookie": "session=stale"},
        claims,
        effective_grant,
    )
    # Адаптер ЯВНО добавляет доверенный Bearer.
    http = FakeHttpxClient()
    backend = BackendHttpAdapter(base_url="http://plugin:8080", client=http)
    await backend.send(make_backend_request(headers=trusted, stream=False))
    sent = http.requests[0]["headers"]
    assert sent[HEADER_CAPABILITY_TOKEN] == "NEW-FRESH-TOKEN"
    assert sent[HEADER_AUTHORIZATION] == "Bearer NEW-FRESH-TOKEN"
    assert "Cookie" not in sent
    assert sent["X-Plugin-ID"] == "mod_equipment"


@pytest.mark.asyncio
async def test_injector_wraps_any_backend_http_port() -> None:
    """build_gateway_runtime оборачивает чужой BackendHttpPort в injector."""
    class _Recording:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def send(self, request: Any) -> Any:
            self.requests.append(request)
            from prodavan.application.plugins.gateway.ports import BackendResponse

            return BackendResponse(status_code=200, body=b"ok")

    inner = _Recording()
    injector = BearerAuthorizationInjector(inner)
    await injector.send(make_backend_request())
    headers = inner.requests[0].headers
    assert headers[HEADER_AUTHORIZATION] == "Bearer fresh-wake-token"
    assert headers[HEADER_CAPABILITY_TOKEN] == "fresh-wake-token"


# ---------------------------------------------------------------------------
# SSE без буферизации + X-Accel-Buffering: no
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_is_not_buffered() -> None:
    chunks = [b"data: one\n\n", b"data: two\n\n", b"data: three\n\n"]
    response = FakeHttpxResponse(chunks=chunks, headers={"Content-Type": "text/event-stream", "X-Secret": "no"})
    http = FakeHttpxClient(response)
    backend = BackendHttpAdapter(base_url="http://plugin:8080", client=http)
    result = await backend.send(make_backend_request(stream=True))

    assert result.is_streaming
    assert result.body is None  # тело НЕ собрано в bytes
    assert http.send_kwargs[-1]["stream"] is True
    assert result.headers["X-Accel-Buffering"] == "no"
    assert result.headers["Cache-Control"] == "no-cache, no-transform"
    assert "X-Secret" in result.headers  # безобидный заголовок пробрасывается

    received: list[bytes] = []
    async for chunk in result.stream or _empty():
        received.append(chunk)
    # Чанки пришли ПО ОДНОМУ, без склейки/буферизации.
    assert received == chunks
    assert response.read_calls == 0, "aread() не должен вызываться для SSE"
    assert response.closed is True


@pytest.mark.asyncio
async def test_plain_response_is_read_fully_and_closed() -> None:
    response = FakeHttpxResponse(chunks=None, body=b'{"ok":true}')
    http = FakeHttpxClient(response)
    backend = BackendHttpAdapter(base_url="http://plugin:8080", client=http)
    result = await backend.send(make_backend_request(stream=False))
    assert result.body == b'{"ok":true}'
    assert result.stream is None
    assert response.closed is True


@pytest.mark.asyncio
async def test_server_timeout_is_not_from_body() -> None:
    http = FakeHttpxClient()
    backend = BackendHttpAdapter(base_url="http://plugin:8080", client=http, max_timeout_seconds=5.0)
    await backend.send(make_backend_request(timeout_seconds=999.0, body=b'{"timeout_ms": 999999}'))
    # Серверный потолок адаптера ограничивает даже «попросили больше».
    assert http.requests[0]["timeout"] == 5.0


# ---------------------------------------------------------------------------
# Аудит: §4.5 единая схема, redaction, без raw body/токенов
# ---------------------------------------------------------------------------


class _RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[dict[str, Any]] = []
        self.fail = fail

    async def write(self, record: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("append-only sink down")
        self.records.append(dict(record))


@pytest.mark.asyncio
async def test_audit_maps_to_canonical_schema_and_redacts() -> None:
    sink = _RecordingSink()
    audit = AuditAdapter(sink)
    event = GatewayAuditEvent(
        event="plugin_webhook_received",
        plugin_id="mod_equipment",
        company_id="co-123",
        decision="allow",
        code="ALLOWED",
        subject_id="employee:emp-1",
        grant_id="grant-1",
        path_digest=hashlib.sha256(b"/api/items").hexdigest(),
        payload_digest=hashlib.sha256(b'{"secret":"s3cr3t"}').hexdigest(),
        reason="ok",
        request_id="req-1",
        metadata={
            "run_id": "run_77",
            "correlation_id": "corr_77",
            "capability": "cache",
            "operation": "get",
            "raw_body": b'{"password":"hunter2"}',
            "Authorization": "Bearer JWT.TOKEN.SIG",
            "capability_token": "JWT.TOKEN.SIG",
            "cookie": "session=abc",
            "password": "hunter2",
            "payload": "raw payload must never be stored",
            "note": "just a short technical note",
        },
    )
    await audit.record(event)
    record = sink.records[0]

    # Единая схема §4.5.
    for field in (
        "request_id", "run_id", "correlation_id", "company_id", "cabinet_id",
        "project_id", "subject_id", "capability", "operation", "resource",
        "decision", "code", "grant_id", "reason", "event_type", "occurred_at",
        "schema_version", "redaction", "payload_digest", "path_digest",
    ):
        assert field in record, field
    assert record["event_type"] == "plugin_webhook_received"
    assert record["company_id"] == "co-123"
    assert record["decision"] == "allow"
    assert record["redaction"] == "canonical"
    assert record["run_id"] == "run_77"

    # Канонические digest-ы, НЕ сами данные.
    assert record["payload_digest"] == hashlib.sha256(b'{"secret":"s3cr3t"}').hexdigest()
    assert record["path_digest"] == hashlib.sha256(b"/api/items").hexdigest()

    serialized = repr(record).lower()
    for forbidden in ("hunter2", "s3cr3t", "raw payload", "jwt.token.sig", "bearer ", "password"):
        assert forbidden not in serialized, f"утечка {forbidden!r} в аудите"


@pytest.mark.asyncio
async def test_audit_drops_non_canonical_digest() -> None:
    sink = _RecordingSink()
    audit = AuditAdapter(sink)
    await audit.record(
        GatewayAuditEvent(
            event="plugin_page_opened", plugin_id="p", company_id="c", decision="allow",
            payload_digest="это-сырое-тело-а-не-digest", path_digest="not-a-digest",
        )
    )
    assert sink.records[0]["payload_digest"] == ""
    assert sink.records[0]["path_digest"] == ""


@pytest.mark.asyncio
async def test_audit_append_failure_propagates_fail_closed() -> None:
    audit = AuditAdapter(_RecordingSink(fail=True))
    with pytest.raises(RuntimeError):
        await audit.record(
            GatewayAuditEvent(event="plugin_page_opened", plugin_id="p", company_id="c", decision="allow")
        )


# ---------------------------------------------------------------------------
# Session / Grant adapters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_bearer_and_cookie_resolution_and_membership() -> None:
    validator = FakeJwtValidator(tokens={"jwt-1": "oidc-sub-1"})
    cookie = FakeCookieSessions(
        {"opaque-1": GatewaySession(session_id="s", employee_id="emp-1", subject_id="employee:emp-1")}
    )
    directory = FakeDirectory()
    adapter = SessionAdapter(validator=validator, directory=directory, cookie_sessions=cookie)

    bearer = await adapter.resolve(session_token="jwt-1")
    assert bearer is not None and bearer.employee_id == "emp-1"
    assert bearer.company_ids == frozenset({"co-123"})
    assert bearer.session_id != "jwt-1"  # токен не уходит в аудит/логи
    assert await adapter.employee_in_company(employee_id="emp-1", company_id="co-123") is True
    assert await adapter.employee_in_company(employee_id="emp-1", company_id="co-other") is False

    opaque = await adapter.resolve(session_token="opaque-1")
    assert opaque is not None and opaque.employee_id == "emp-1"
    assert await adapter.resolve(session_token="unknown") is None
    assert await adapter.resolve(session_token=None) is None


@pytest.mark.asyncio
async def test_session_without_cookie_store_is_fail_closed() -> None:
    adapter = SessionAdapter(validator=FakeJwtValidator(tokens={}), directory=FakeDirectory())
    # Валидатор отверг токен, cookie-хранилище НЕ подключено → отказ, не «пустим».
    assert await adapter.resolve(session_token="opaque-cookie") is None


@pytest.mark.asyncio
async def test_grant_active_public_pages_and_egress_from_store() -> None:
    store = InMemoryGrantStore(
        [
            GrantRecord(
                grant_id="grant-1", plugin_id="p", company_id="c",
                operations=frozenset({"read"}),
                public_pages=frozenset({"/public", "/docs/"}),
                egress_mode="webhooks-only",
                egress_allowed_hosts=("hooks.example.com",),
            )
        ]
    )
    adapter = GrantAdapter(store)
    grant = await adapter.active_grant(plugin_id="p", company_id="c")
    assert grant is not None and grant.active and grant.grant_id == "grant-1"
    # Свежий capability-токен приходит из WakeLease, НЕ из БД.
    assert grant.capability_token == ""
    assert await adapter.public_pages(plugin_id="p", company_id="c") == frozenset({"/public", "/docs/"})
    policy = await adapter.egress_policy(plugin_id="p", company_id="c")
    assert policy["mode"] == "webhooks-only"
    assert policy["allowed_hosts"] == ("hooks.example.com",)

    store.revoke(plugin_id="p", company_id="c")
    assert await adapter.active_grant(plugin_id="p", company_id="c") is None
    assert await adapter.public_pages(plugin_id="p", company_id="c") == frozenset()
    assert (await adapter.egress_policy(plugin_id="p", company_id="c"))["mode"] == "none"


# ---------------------------------------------------------------------------
# Вспомогательные двойники
# ---------------------------------------------------------------------------


def _claims() -> GatewayClaims:
    return GatewayClaims(plugin_id="mod_equipment", company_id="co-123", subject_id="employee:emp-1")


def _grant(token: str) -> GatewayGrant:
    return GatewayGrant(grant_id="grant-1", active=True, capability_token=token)


class _AuditSink:
    def __init__(self) -> None:
        self.events: list[GatewayAuditEvent] = []

    async def record(self, event: GatewayAuditEvent) -> None:
        self.events.append(event)


class _NoSession:
    async def resolve(self, *, session_token: str | None) -> GatewaySession | None:
        return None

    async def employee_in_company(self, *, employee_id: str, company_id: str) -> bool:
        return False


class _ActiveGrant:
    async def active_grant(self, *, plugin_id: str, company_id: str) -> GatewayGrant:
        return GatewayGrant(grant_id="grant-1", active=True)

    async def public_pages(self, *, plugin_id: str, company_id: str) -> frozenset[str]:
        return frozenset()


class _StaticSecret:
    async def webhook_secret(self, *, plugin_id: str, company_id: str) -> str | None:
        return WEBHOOK_SECRET


async def _empty():  # type: ignore[no-untyped-def]
    if False:
        yield b""
