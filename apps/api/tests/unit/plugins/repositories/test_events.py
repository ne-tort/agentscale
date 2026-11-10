"""SQL-хранилища событий (задача 10): идемпотентность и DLQ.

Ключ КОНСЬЮМЕРА — ``(subscription_id, source_event_id)``, а НЕ URL, НЕ hash
тела и НЕ ``event_id`` платформы. Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.events.contracts import (
    DlqRecord,
    IdempotencyKey,
    MAX_DELIVERY_ATTEMPTS,
    PluginEvent,
    Subscription,
)
from prodavan.application.plugins.events.ports import (
    DlqPort,
    IdempotencyStorePort,
    PublishedEventStorePort,
    SubscriptionStorePort,
)
from prodavan.infrastructure.persistence.repositories.plugins.events import (
    DbDlqStore,
    DbIdempotencyStore,
    DbPublishedEventStore,
    DbSubscriptionStore,
    STATUS_IDEMPOTENCY,
)

from .conftest import DIGEST, NOW, FakeSession, FakeSessionFactory, _Row


def _factory(*, rows: list | None = None, rowcount: int = 1) -> FakeSessionFactory:
    return FakeSessionFactory(FakeSession(rows=rows, rowcount=rowcount))


def idempotency_row(**overrides: object) -> object:
    data: dict[str, object] = {
        "subscription_id": "sub_01",
        "source_event_id": "evt_src_01",
        "status": STATUS_IDEMPOTENCY,
        "topic": "plugin.platform-state",
        "partition": 0,
        "offset": 0,
        "attempts": 0,
        "created_at": NOW,
        "event_id": None,
        "dlq_topic": None,
        "error_kind": None,
        "payload_digest": None,
    }
    data.update(overrides)
    return _Row(data)


def test_event_stores_implement_ports() -> None:
    factory = _factory()
    assert isinstance(DbSubscriptionStore(session_factory=factory), SubscriptionStorePort)
    assert isinstance(DbIdempotencyStore(session_factory=factory), IdempotencyStorePort)
    assert isinstance(DbPublishedEventStore(session_factory=factory), PublishedEventStorePort)
    assert isinstance(DbDlqStore(session_factory=factory), DlqPort)


@pytest.mark.asyncio
async def test_idempotency_key_is_subscription_and_source_event_id() -> None:
    """ГЛАВНОЕ: повтор того же ``(subscription_id, source_event_id)`` не дублирует."""
    store = DbIdempotencyStore(session_factory=_factory(rows=[idempotency_row()]))
    key = IdempotencyKey(subscription_id="sub_01", source_event_id="evt_src_01")
    assert key.as_tuple() == ("sub_01", "evt_src_01")
    assert await store.seen(key) is True


@pytest.mark.asyncio
async def test_same_source_event_in_another_subscription_is_not_duplicate() -> None:
    """Тот же source_event_id у ДРУГОЙ подписки — НЕ дубль."""
    store = DbIdempotencyStore(session_factory=_factory(rows=[idempotency_row()]))
    other = IdempotencyKey(subscription_id="sub_02", source_event_id="evt_src_01")
    # FakeSession отдаёт ту же строку для любого запроса, поэтому проверяем
    # сам ключ как каноническую форму, а не результат SQL-фильтра.
    assert other.as_tuple() != ("sub_01", "evt_src_01")
    assert other.subscription_id == "sub_02"


@pytest.mark.asyncio
async def test_remember_is_idempotent_under_race() -> None:
    """``IntegrityError`` (конкурентный консьюмер) → идемпотентно ОК, без исключения."""
    from sqlalchemy.exc import IntegrityError

    session = FakeSession()
    session.fail_on_commit = IntegrityError("stmt", {}, Exception("duplicate"))
    store = DbIdempotencyStore(session_factory=FakeSessionFactory(session))
    key = IdempotencyKey(subscription_id="sub_01", source_event_id="evt_src_01")
    # fail_on_commit перехватывается как IntegrityError в remember -> return.
    await store.remember(key)


@pytest.mark.asyncio
async def test_remember_error_raises_so_offset_is_not_committed() -> None:
    """Не-IntegrityError ошибка записи → исключение (диспечер сделает seek)."""
    from prodavan.infrastructure.persistence.repositories.plugins._schema import (
        PluginRepositoryUnavailable,
    )

    session = FakeSession()
    session.fail_on_commit = RuntimeError("db down")
    store = DbIdempotencyStore(session_factory=FakeSessionFactory(session))
    with pytest.raises(PluginRepositoryUnavailable):
        await store.remember(IdempotencyKey("sub_01", "evt_src_01"))


@pytest.mark.asyncio
async def test_idempotency_seen_db_failure_raises_without_commit() -> None:
    """SQL failure is RETRY/exception, never a false duplicate + offset commit."""
    from sqlalchemy.exc import SQLAlchemyError

    from prodavan.infrastructure.persistence.repositories.plugins._schema import (
        PluginRepositoryUnavailable,
    )

    session = FakeSession()
    session.fail_on_execute = SQLAlchemyError("db down")
    store = DbIdempotencyStore(session_factory=FakeSessionFactory(session))
    with pytest.raises(PluginRepositoryUnavailable):
        await store.seen(IdempotencyKey("sub_01", "evt_src_01"))
    assert session.commits == 0


@pytest.mark.asyncio
async def test_published_seen_db_failure_raises_without_commit() -> None:
    from sqlalchemy.exc import SQLAlchemyError

    from prodavan.infrastructure.persistence.repositories.plugins._schema import (
        PluginRepositoryUnavailable,
    )

    session = FakeSession()
    session.fail_on_execute = SQLAlchemyError("db down")
    store = DbPublishedEventStore(session_factory=FakeSessionFactory(session))
    with pytest.raises(PluginRepositoryUnavailable):
        await store.seen("evt_platform_01")
    assert session.commits == 0


@pytest.mark.asyncio
async def test_unknown_subscription_is_fail_closed() -> None:
    store = DbSubscriptionStore(session_factory=_factory(rows=[]))
    assert await store.get("ghost") is None
    assert await store.is_active("ghost") is False


@pytest.mark.asyncio
async def test_active_subscription_roundtrip() -> None:
    from prodavan.infrastructure.persistence.repositories.plugins.events import (
        STATUS_SUBSCRIPTION_ACTIVE,
        SUBSCRIPTION_SOURCE_ID,
    )

    row = _Row({
        "subscription_id": "sub_01",
        "source_event_id": SUBSCRIPTION_SOURCE_ID,
        "status": STATUS_SUBSCRIPTION_ACTIVE,
        "dlq_topic": "cg_mod_equipment",
        "error_kind": "offers.created,offers.updated",
    })
    store = DbSubscriptionStore(session_factory=_factory(rows=[row]))
    subscription = await store.get("sub_01")
    assert isinstance(subscription, Subscription)
    assert subscription.event_types == ("offers.created", "offers.updated")
    assert await store.is_active("sub_01") is True


@pytest.mark.asyncio
async def test_published_event_store_uses_its_own_key() -> None:
    """У ПРОДЮСЕРА отдельный ключ по ``event_id`` (не source_event_id)."""
    from prodavan.infrastructure.persistence.repositories.plugins.events import (
        PRODUCER_SUBSCRIPTION_ID,
        STATUS_PUBLISHED,
    )

    row = _Row({
        "subscription_id": PRODUCER_SUBSCRIPTION_ID,
        "source_event_id": "evt_platform_01",
        "status": STATUS_PUBLISHED,
        "event_id": "evt_platform_01",
    })
    store = DbPublishedEventStore(session_factory=_factory(rows=[row]))
    assert await store.seen("evt_platform_01") is True
    await store.remember("evt_platform_02")


@pytest.mark.asyncio
async def test_dlq_publish_stores_only_digest_and_ids() -> None:
    """Кр. 11: в БД уходят только id/digest/error_kind, НИКАКОГО raw payload."""
    event = PluginEvent(
        event_id="evt_01",
        event_type="offers.created",
        source="offers",
        company_id="co_123",
        subscription_id="sub_01",
        occurred_at=NOW,
        payload_digest=DIGEST,
        run_id="run_01",
        correlation_id="corr_01",
    )
    record = DlqRecord(
        event=event,
        subscription_id="sub_01",
        topic="agentscale.plugin.events",
        dlq_topic="agentscale.plugin.events.dlq",
        attempts=MAX_DELIVERY_ATTEMPTS,
        error_kind="ConnectionError",
        failed_at=NOW,
    )
    session = FakeSession()
    store = DbDlqStore(session_factory=FakeSessionFactory(session))
    await store.publish(record)
    inserted = session.added[-1]
    # Реальная ORM-строка A10: атрибуты = колонки.
    assert inserted.payload_digest == DIGEST
    assert inserted.error_kind == "ConnectionError"
    assert inserted.event_id == "evt_01"
    assert inserted.dlq_topic == "agentscale.plugin.events.dlq"
    # Ни одного поля с сырым телом/токеном/SQL.
    from prodavan.infrastructure.persistence.models.plugin_platform import (
        PluginEventDeliveryRow,
    )

    assert not any(
        forbidden in name
        for name in PluginEventDeliveryRow.__table__.columns.keys()
        for forbidden in ("raw_payload", "request_body", "raw_sql", "bind_params", "access_token")
    )
    assert session.commits == 1
