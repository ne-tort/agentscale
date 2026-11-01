"""External Chat API: app keys + outgoing webhooks (service-level, sqlite in-mem)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from prodavan.application.external.app_keys import ApiAppKeyService
from prodavan.application.external.webhooks import WebhookService
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.external_api import (
    ApiAppKeyRow,
    WebhookDeliveryRow,
    WebhookSubscriptionRow,
)
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


_TABLES = (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
    CabinetInstanceRow,
    ProjectRow,
    ApiAppKeyRow,
    WebhookSubscriptionRow,
    WebhookDeliveryRow,
)


async def _session() -> tuple[AsyncSession, Any]:
    engine = create_async_engine("sqlite+aiosqlite://")

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: [t.__table__.create(sync, checkfirst=True) for t in _TABLES]
        )
    session = AsyncSession(engine)
    session.add(CompanyRow(id="co_1", name="Co", login_slug="co"))
    session.add(
        EmployeeRow(
            id="emp_1",
            keycloak_sub="kc_1",
            login="test@co.local",
            email="test@co.local",
            status="active",
        )
    )
    session.add(MembershipRow(company_id="co_1", employee_id="emp_1", role="member"))
    session.add(
        CabinetInstanceRow(
            id="cab_1",
            company_id="co_1",
            name="Кабинет",
            schema_name="cab_1",
            status="active",
        )
    )
    session.add(
        ProjectRow(
            id="prj_1",
            company_id="co_1",
            cabinet_id="cab_1",
            owner_employee_id="emp_1",
            name="Проект",
            slug="prj",
            status="active",
            visibility_mode="cabinet_shared",
            workspace_key="wk_1",
            container_ref="claim-1",
        )
    )
    await session.commit()
    return session, engine


# ------------------------------------------------------------------ app keys


@pytest.mark.asyncio
async def test_app_key_create_and_resolve() -> None:
    session, engine = await _session()
    try:
        svc = ApiAppKeyService(session)
        row, token = await svc.create(
            label="Интеграция", company_id="co_1", employee_id="emp_1"
        )
        assert row.id.startswith("ak_")
        assert token.startswith("ak_")
        # секрет хранится только хэшем
        assert row.secret_hash == hashlib.sha256(token.encode()).hexdigest()
        assert row.secret_hash != token

        ctx = await svc.resolve(token)
        assert ctx.employee_id == "emp_1"
        assert ctx.company_id == "co_1"
        assert "chat:write" in ctx.scopes

        # last_used_at проставился
        await session.refresh(row)
        assert row.last_used_at is not None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_app_key_rejects_wrong_secret_and_revoked() -> None:
    session, engine = await _session()
    try:
        svc = ApiAppKeyService(session)
        row, token = await svc.create(label="k", company_id="co_1", employee_id="emp_1")
        with pytest.raises(AppError):
            await svc.resolve("ak_wrong-secret")
        await svc.revoke(row.id, company_id="co_1")
        with pytest.raises(AppError):
            await svc.resolve(token)
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_app_key_expired_rejected() -> None:
    session, engine = await _session()
    try:
        svc = ApiAppKeyService(session)
        _, token = await svc.create(
            label="k",
            company_id="co_1",
            employee_id="emp_1",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        with pytest.raises(AppError):
            await svc.resolve(token)
    finally:
        await session.close()
        await engine.dispose()


# ------------------------------------------------------------------ webhooks


def _mock_secret_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}

    class _Store:
        def put(self, key_id, secret):
            ref = f"mem://{key_id}"
            store[ref] = secret
            return ref

        def get(self, ref):
            return store[ref]

        def delete(self, ref):
            store.pop(ref, None)

    monkeypatch.setattr(
        "prodavan.application.external.webhooks.get_secret_store", lambda: _Store()
    )
    return store


@pytest.mark.asyncio
async def test_webhook_subscribe_emit_and_deliver(monkeypatch: pytest.MonkeyPatch) -> None:
    session, engine = await _session()
    try:
        secrets = _mock_secret_store(monkeypatch)
        svc = WebhookService(session)
        sub, secret = await svc.subscribe(
            company_id="co_1",
            url="https://example.com/hook",
            events=["chat.message.completed"],
            project_id="prj_1",
        )
        assert sub.id.startswith("whk_")
        assert secrets[sub.secret_ref] == secret

        # эмит создаёт delivery только для подходящих подписок
        n = await svc.emit(
            company_id="co_1",
            project_id="prj_1",
            event_type="chat.message.completed",
            payload={"session_id": "s1"},
        )
        assert n == 1
        # другой проект не матчится
        n0 = await svc.emit(
            company_id="co_1",
            project_id="prj_other",
            event_type="chat.message.completed",
            payload={},
        )
        assert n0 == 0

        # доставка: подписанный POST
        posted: dict = {}

        async def fake_post(url, body, *, headers):
            posted["url"] = url
            posted["body"] = body
            posted["headers"] = headers
            return 200

        monkeypatch.setattr(
            "prodavan.application.external.webhooks._post", fake_post
        )
        stats = await svc.deliver_pending()
        assert stats["delivered"] == 1
        assert posted["url"] == "https://example.com/hook"
        # HMAC-подпись тела
        from prodavan.domain.projects.webhook_hmac import verify_webhook_signature

        assert verify_webhook_signature(
            secret=secret, body=posted["body"], header=posted["headers"]["X-Prodavan-Signature"]
        )
        assert posted["headers"]["X-Prodavan-Event"] == "chat.message.completed"
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_delivery_retry_and_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    session, engine = await _session()
    try:
        _mock_secret_store(monkeypatch)
        svc = WebhookService(session)
        await svc.subscribe(
            company_id="co_1",
            url="https://example.com/hook",
            events=["chat.message.completed"],
        )
        await svc.emit(
            company_id="co_1",
            project_id="prj_1",
            event_type="chat.message.completed",
            payload={},
        )
        # доставка падает (500)
        async def fail_post(url, body, *, headers):
            return 500

        monkeypatch.setattr("prodavan.application.external.webhooks._post", fail_post)
        stats = await svc.deliver_pending()
        assert stats["failed"] == 1
        # delivery ждёт бэкофф, не exhausted сразу
        row = (
            await session.execute(
                __import__("sqlalchemy").select(WebhookDeliveryRow)
            )
        ).scalars().first()
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.retry_history == [{"attempt": 1, "response_status": 500, "error": "http 500", "at": row.retry_history[0]["at"]}]
        # ждёт бэкофф (sqlite хранит naive — сравниваем с naive now)
        now = datetime.now(UTC).replace(tzinfo=None)
        naa = row.next_attempt_at
        naa = naa.replace(tzinfo=None) if naa.tzinfo else naa
        assert naa > now
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_retry_history_is_bounded_and_persisted_after_failed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, engine = await _session()
    try:
        _mock_secret_store(monkeypatch)
        svc = WebhookService(session)
        await svc.subscribe(
            company_id="co_1",
            url="https://example.com/hook",
            events=["chat.message.completed"],
        )
        await svc.emit(
            company_id="co_1",
            project_id="prj_1",
            event_type="chat.message.completed",
            payload={},
        )
        delivery = (
            await session.execute(__import__("sqlalchemy").select(WebhookDeliveryRow))
        ).scalars().first()
        delivery.retry_history = [
            {"attempt": attempt, "response_status": 500}
            for attempt in range(1, 106)
        ]
        await session.flush()

        async def fail_post(url, body, *, headers):
            return 500

        monkeypatch.setattr("prodavan.application.external.webhooks._post", fail_post)
        assert (await svc.deliver_pending())["failed"] == 1

        await session.refresh(delivery)
        assert delivery.status == "pending"
        assert delivery.attempts == 1
        assert len(delivery.retry_history) == 100
        assert delivery.retry_history[0]["attempt"] == 7
        assert delivery.retry_history[-1]["attempt"] == 1
        assert delivery.retry_history[-1]["error"] == "http 500"
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_retry_history_bounds_json_bytes_and_keeps_manual_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, engine = await _session()
    try:
        _mock_secret_store(monkeypatch)
        svc = WebhookService(session)
        await svc.subscribe(
            company_id="co_1",
            url="https://example.com/hook",
            events=["chat.message.completed"],
        )
        await svc.emit(
            company_id="co_1", project_id="prj_1",
            event_type="chat.message.completed", payload={},
        )
        delivery = (
            await session.execute(__import__("sqlalchemy").select(WebhookDeliveryRow))
        ).scalars().first()
        delivery.retry_history = [
            {"kind": "manual_retry", "at": f"2026-01-{index:02d}"}
            for index in range(1, 4)
        ] + [
            {"attempt": index, "error": "x" * 40_000}
            for index in range(1, 4)
        ]
        await session.flush()

        async def fail_post(url, body, *, headers):
            return 500

        monkeypatch.setattr("prodavan.application.external.webhooks._post", fail_post)
        assert (await svc.deliver_pending())["failed"] == 1

        await session.refresh(delivery)
        serialized = __import__("json").dumps(
            delivery.retry_history, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        assert len(serialized) <= 64 * 1024
        assert sum(
            item.get("kind") == "manual_retry" for item in delivery.retry_history
        ) == 3

        from prodavan.api.v1.external import list_webhook_deliveries

        response = await list_webhook_deliveries(
            principal=None,
            session=session,
            employee=await session.get(EmployeeRow, "emp_1"),
        )
        assert len(response["items"]) == 1
        assert len(response["items"][0]["retry_history"]) == len(delivery.retry_history)
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_worker_reclaims_delivery_after_lease_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance: an abandoned lease becomes claimable after its TTL."""
    session, engine = await _session()
    try:
        _mock_secret_store(monkeypatch)
        svc = WebhookService(session)
        await svc.subscribe(
            company_id="co_1",
            url="https://example.com/hook",
            events=["chat.message.completed"],
        )
        await svc.emit(
            company_id="co_1",
            project_id="prj_1",
            event_type="chat.message.completed",
            payload={"session_id": "s1"},
        )
        delivery = (
            await session.execute(__import__("sqlalchemy").select(WebhookDeliveryRow))
        ).scalars().first()
        delivery.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        delivery.leased_by = "dead-worker"
        await session.flush()

        async def succeed_post(url, body, *, headers):
            return 204

        monkeypatch.setattr("prodavan.application.external.webhooks._post", succeed_post)
        stats = await svc.deliver_pending()

        assert stats == {"claimed": 1, "delivered": 1, "failed": 0}
        await session.refresh(delivery)
        assert delivery.status == "delivered"
        assert delivery.leased_by is None
        assert delivery.lease_until is None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_worker_releases_lease_when_subscription_paused_after_claim() -> None:
    """Acceptance: a paused subscription cannot leave a claimed delivery leased."""
    session, engine = await _session()
    try:
        svc = WebhookService(session)
        sub, _ = await svc.subscribe(
            company_id="co_1",
            url="https://example.com/hook",
            events=["chat.message.completed"],
        )
        await svc.emit(
            company_id="co_1",
            project_id="prj_1",
            event_type="chat.message.completed",
            payload={"session_id": "s1"},
        )
        delivery = (
            await session.execute(__import__("sqlalchemy").select(WebhookDeliveryRow))
        ).scalars().first()

        original_deliver_one = svc._deliver_one

        async def pause_then_deliver(row: WebhookDeliveryRow) -> bool:
            sub.enabled = False
            return await original_deliver_one(row)

        svc._deliver_one = pause_then_deliver  # type: ignore[method-assign]
        stats = await svc.deliver_pending()

        assert stats == {"claimed": 1, "delivered": 0, "failed": 1}
        await session.refresh(delivery)
        assert delivery.status == "exhausted"
        assert delivery.last_error == "subscription removed or disabled"
        assert delivery.leased_by is None
        assert delivery.lease_until is None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_subscription_can_pause_and_resume_without_losing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, engine = await _session()
    try:
        secrets = _mock_secret_store(monkeypatch)
        svc = WebhookService(session)
        sub, secret = await svc.subscribe(
            company_id="co_1", url="https://example.com/hook",
            events=["chat.message.completed"],
        )

        paused = await svc.set_enabled(sub.id, company_id="co_1", enabled=False)
        assert paused is sub
        assert paused.enabled is False
        assert await svc.emit(
            company_id="co_1", project_id="prj_1",
            event_type="chat.message.completed", payload={},
        ) == 0
        assert await svc.deliver_pending() == {"claimed": 0, "delivered": 0, "failed": 0}
        assert secrets[sub.secret_ref] == secret

        resumed = await svc.set_enabled(sub.id, company_id="co_1", enabled=True)
        assert resumed is sub
        assert resumed.enabled is True
        assert await svc.emit(
            company_id="co_1", project_id="prj_1",
            event_type="chat.message.completed", payload={},
        ) == 1
        assert await svc.set_enabled(sub.id, company_id="another-company", enabled=False) is None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_delivery_list_is_company_scoped_and_omits_payload() -> None:
    session, engine = await _session()
    try:
        session.add(CompanyRow(id="co_2", name="Other", login_slug="other"))
        await session.commit()
        svc = WebhookService(session)
        await svc.subscribe(
            company_id="co_1", url="https://one.example/hook",
            events=["chat.message.completed"],
        )
        await svc.subscribe(
            company_id="co_2", url="https://two.example/hook",
            events=["chat.message.completed"],
        )
        await svc.emit(
            company_id="co_1", project_id="prj_1",
            event_type="chat.message.completed", payload={"private": "one"},
        )
        await svc.emit(
            company_id="co_2", project_id="prj_1",
            event_type="chat.message.completed", payload={"private": "two"},
        )

        rows = await svc.list_deliveries("co_1")
        assert len(rows) == 1
        delivery, subscription = rows[0]
        assert subscription.url == "https://one.example/hook"
        assert delivery.payload_json == {"private": "one"}
        delivery.status = "pending"
        delivery.attempts = 3
        delivery.response_status = 503
        delivery.last_error = "http 503"
        delivery.retry_history = [{"attempt": 1, "response_status": 503, "error": "http 503"}]
        await session.flush()

        from prodavan.api.v1.external import list_webhook_deliveries

        response = await list_webhook_deliveries(
            principal=None, session=session, employee=await session.get(EmployeeRow, "emp_1")
        )
        assert len(response["items"]) == 1
        item = response["items"][0]
        assert item["id"] == delivery.id
        assert item["status"] == "pending"
        assert item["attempts"] == 3
        assert item["response_status"] == 503
        assert item["last_error"] == "http 503"
        assert item["retry_history"] == [{"attempt": 1, "response_status": 503, "error": "http 503"}]
        assert "payload_json" not in item
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_manual_retry_resets_only_exhausted_delivery() -> None:
    session, engine = await _session()
    try:
        svc = WebhookService(session)
        sub, _ = await svc.subscribe(
            company_id="co_1", url="https://example.com/hook",
            events=["chat.message.completed"],
        )
        await svc.emit(
            company_id="co_1", project_id="prj_1",
            event_type="chat.message.completed", payload={"session_id": "s1"},
        )
        delivery = (
            await session.execute(__import__("sqlalchemy").select(WebhookDeliveryRow))
        ).scalars().first()
        delivery.status = "exhausted"
        delivery.attempts = 5
        delivery.response_status = 503
        delivery.last_error = "http 503"
        delivery.retry_history = [{"attempt": 5, "response_status": 503, "error": "http 503"}]
        delivery.leased_by = "stale-worker"
        delivery.lease_until = datetime.now(UTC) + timedelta(minutes=1)
        delivery.delivered_at = datetime.now(UTC)
        sub.enabled = False
        sub.fail_count = 10
        await session.flush()

        from prodavan.api.v1.external import retry_webhook_delivery

        response = await retry_webhook_delivery(
            delivery_id=delivery.id,
            principal=None,
            session=session,
            employee=await session.get(EmployeeRow, "emp_1"),
        )
        assert response == {"id": delivery.id, "status": "pending"}
        await session.refresh(delivery)
        await session.refresh(sub)
        assert delivery.status == "pending"
        assert delivery.attempts == 0
        assert delivery.next_attempt_at is not None
        ready_at = delivery.next_attempt_at
        ready_at = ready_at.replace(tzinfo=UTC) if ready_at.tzinfo is None else ready_at
        assert ready_at <= datetime.now(UTC)
        assert delivery.response_status is None
        assert delivery.last_error is None
        assert len(delivery.retry_history) == 2
        assert delivery.retry_history[0] == {"attempt": 5, "response_status": 503, "error": "http 503"}
        assert delivery.retry_history[1]["kind"] == "manual_retry"
        assert delivery.leased_by is None
        assert delivery.lease_until is None
        assert delivery.delivered_at is None
        assert sub.enabled is True
        assert sub.fail_count == 0

        assert await svc.retry_delivery(delivery.id, company_id="co_1") is False
        assert await svc.retry_delivery(delivery.id, company_id="another-company") is False

        from prodavan.api.v1.external import retry_webhook_delivery

        with pytest.raises(AppError) as not_retryable:
            await retry_webhook_delivery(
                delivery_id=delivery.id,
                principal=None,
                session=session,
                employee=await session.get(EmployeeRow, "emp_1"),
            )
        assert not_retryable.value.status == 409
    finally:
        await session.close()
        await engine.dispose()


# ------------------------------------- идемпотентность внешнего импорта тендера
# Повторный POST того же source_ref (ретрай кравлера / падение между push и
# подтверждением) не должен создавать ни второй тендер, ни второй чат:
# один тендер = один чат.


class _FakeIO:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def list(self, table_slug: str) -> list[dict]:
        assert table_slug == "tenders"
        return self.rows


@pytest.mark.asyncio
async def test_find_tender_by_source_ref_matches() -> None:
    from prodavan.api.v1.external import _find_tender_by_source_ref

    io = _FakeIO(
        [
            {"row_id": "row_a", "body": {"source_ref": "b2b-center:111"}},
            {"row_id": "row_b", "body": {"source_ref": "b2b-center:222"}},
        ]
    )
    assert await _find_tender_by_source_ref(io, "b2b-center:222") == "row_b"


@pytest.mark.asyncio
async def test_find_tender_by_source_ref_missing() -> None:
    from prodavan.api.v1.external import _find_tender_by_source_ref

    io = _FakeIO([{"row_id": "row_a", "body": {"source_ref": "b2b-center:111"}}])
    assert await _find_tender_by_source_ref(io, "b2b-center:999") == ""
    # пустой source_ref никогда не совпадает — каждый такой push создаёт строку
    assert await _find_tender_by_source_ref(io, "") == ""


@pytest.mark.asyncio
async def test_find_tender_by_source_ref_flat_row_shape() -> None:
    """Строка без вложенного body (плоская) тоже находится."""
    from prodavan.api.v1.external import _find_tender_by_source_ref

    io = _FakeIO([{"row_id": "row_c", "source_ref": "b2b-center:333"}])
    assert await _find_tender_by_source_ref(io, "b2b-center:333") == "row_c"


@pytest.mark.asyncio
async def test_find_tender_by_source_ref_ignores_blank_refs() -> None:
    """Пустой source_ref в строке не должен совпадать с поиском."""
    from prodavan.api.v1.external import _find_tender_by_source_ref

    io = _FakeIO([{"row_id": "row_d", "body": {"source_ref": ""}}])
    assert await _find_tender_by_source_ref(io, "") == ""


@pytest.mark.asyncio
async def test_find_tender_by_source_ref_exact_match_only() -> None:
    """Сравнение точное: подстрока не должна давать ложное совпадение."""
    from prodavan.api.v1.external import _find_tender_by_source_ref

    io = _FakeIO([{"row_id": "row_a", "body": {"source_ref": "b2b-center:1111"}}])
    assert await _find_tender_by_source_ref(io, "b2b-center:111") == ""
