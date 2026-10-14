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
        # ждёт бэкофф (sqlite хранит naive — сравниваем с naive now)
        now = datetime.now(UTC).replace(tzinfo=None)
        naa = row.next_attempt_at
        naa = naa.replace(tzinfo=None) if naa.tzinfo else naa
        assert naa > now
    finally:
        await session.close()
        await engine.dispose()
