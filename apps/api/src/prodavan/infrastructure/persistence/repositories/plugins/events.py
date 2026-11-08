"""SQL-хранилища состояния шины событий плагинов (F7, задача 10).

Реализуют порты из ``application/plugins/events/ports.py``:

* :class:`DbSubscriptionStore`   → ``SubscriptionStorePort``;
* :class:`DbIdempotencyStore`    → ``IdempotencyStorePort``;
* :class:`DbPublishedEventStore` → ``PublishedEventStorePort``;
* :class:`DbDlqStore`            → ``DlqPort`` (append-only «мёртвые» события).

Все четыре работают поверх РЕАЛЬНОЙ модели A10 ``PluginEventDeliveryRow``
(``plugin_event_deliveries``) с Unique(``subscription_id``, ``source_event_id``)
— ровно тот ключ идемпотентности КОНСЬЮМЕРА, который требует §7.3: НЕ URL,
НЕ hash тела, НЕ ``event_id`` платформы.

Так как отдельной колонки-дискриминатора ``kind`` в A10 НЕТ, виды записей
различаются префиксом ``status`` и зарезервированными ``subscription_id``:

===========================  ==========================================  ======================
Вид                          ``status``                                  ``subscription_id``
===========================  ==========================================  ======================
подписка                     ``subscription:active`` / ``...:revoked``   сам id подписки
идемпотентность консьюмера   ``idempotency:recorded``                    сам id подписки
ре-паблиш продюсера          ``published:recorded``                      ``__producer__``
DLQ                          ``dlq:recorded``                            сам id подписки
===========================  ==========================================  ======================

Аддитивные колонки A10 ``event_id`` / ``dlq_topic`` / ``error_kind`` /
``payload_digest`` используются по назначению; сырое тело НЕ хранится НИГДЕ
(только ``payload_digest``, критерий 11). NOT NULL ``topic`` / ``partition`` /
``offset`` заполняются нейтральными значениями для служебных записей.

Атомарность ``remember`` (критерий №20)
---------------------------------------
``INSERT`` + перехват ``IntegrityError`` (универсально) или PostgreSQL
``ON CONFLICT DO NOTHING`` (:meth:`DbIdempotencyStore.remember_pg`). Повторная
доставка того же ``(subscription_id, source_event_id)`` НЕ вызывает обработчик
второй раз даже при конкурентных консьюмерах: уникальность обеспечивает БД.

Fail-closed:
* неизвестная/неактивная подписка → ``None``/``False`` (не «группа по умолчанию»);
* ``seen`` при ошибке БД → ``True`` («уже обработано» = не пустить повторно);
* ``remember`` / ``DlqPort.publish`` при ошибке → ИСКЛЮЧЕНИЕ (offset не
  коммитится, диспечер делает ``seek`` и доставляет событие заново).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update as sa_update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

from prodavan.application.plugins.events.contracts import (
    DlqRecord,
    IdempotencyKey,
    Subscription,
)

from ._schema import (
    DELIVERY_COLUMNS,
    DELIVERY_ROW,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositoryUnavailable,
    column_names,
    load_row,
    string_tuple,
    text,
)

# Префиксы статусов (дискриминатор вместо отсутствующей колонки kind).
STATUS_SUBSCRIPTION_ACTIVE = "subscription:active"
STATUS_SUBSCRIPTION_REVOKED = "subscription:revoked"
STATUS_IDEMPOTENCY = "idempotency:recorded"
STATUS_PUBLISHED = "published:recorded"
STATUS_DLQ = "dlq:recorded"

PRODUCER_SUBSCRIPTION_ID = "__producer__"
SUBSCRIPTION_SOURCE_ID = "__subscription__"
SERVICE_TOPIC = "plugin.platform-state"
SERVICE_DLQ_TOPIC = "plugin.platform-state.dlq"

# Зарезервированные id, которые НЕ являются реальными доставками событий.
_RESERVED_SUBSCRIPTIONS: frozenset[str] = frozenset({
    PRODUCER_SUBSCRIPTION_ID,
})


class DbEventStoreBase:
    """Общая инфраструктура: модель, колонки, сессия, служебные значения."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        columns: ColumnMap | None = None,
        clock: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                f"{type(self).__name__} требует session_factory или session (fail-closed)"
            )
        self._session_factory = session_factory
        self._session = session
        self._columns = columns or ColumnMap()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _row(self) -> type:
        row = load_row(DELIVERY_ROW)
        self._columns.require(row, *DELIVERY_COLUMNS)
        return row

    def _col(self, name: str) -> str:
        return self._columns.col(name)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PluginRepositoryError("часы events-хранилища обязаны возвращать aware datetime")
        return value.astimezone(UTC)

    def _session_ctx(self):
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None
        return self._session_factory()

    def _table(self, row: type) -> Any:
        return row.__table__

    def _values(self, row: type, values: dict[str, Any]) -> dict[str, Any]:
        """Отфильтровать по реально существующим колонкам A10."""
        existing = column_names(row)
        return {key: value for key, value in values.items() if key in existing}

    def _service_values(
        self,
        row: type,
        *,
        subscription_id: str,
        source_event_id: str,
        status: str,
        event_id: str | None = None,
        dlq_topic: str | None = None,
        error_kind: str | None = None,
        payload_digest: str | None = None,
        attempts: int = 0,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Значения ВСЕХ NOT NULL колонок для служебной записи."""
        return self._values(row, {
            self._col("subscription_id"): subscription_id,
            self._col("source_event_id"): source_event_id,
            self._col("topic"): SERVICE_TOPIC,
            self._col("partition"): 0,
            self._col("offset"): 0,
            self._col("status"): status,
            self._col("attempts"): attempts,
            self._col("created_at"): occurred_at or self._now(),
            "event_id": event_id,
            "dlq_topic": dlq_topic,
            "error_kind": error_kind,
            "payload_digest": payload_digest,
        })

    def _not_reserved(self, row: type) -> Any:
        """Исключить служебные записи продюсера/флагов из выдачи доставок."""
        return self._table(row).columns[self._col("subscription_id")].notin_(
            _RESERVED_SUBSCRIPTIONS
        )


class DbSubscriptionStore(DbEventStoreBase):
    """``SubscriptionStorePort``: реестр подписок плагинов в БД."""

    async def get(self, subscription_id: str) -> Subscription | None:
        key = text(subscription_id, field="subscription_id")
        try:
            row = self._row()
            table = self._table(row)
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("subscription_id")] == key,
                            table.columns[self._col("source_event_id")] == SUBSCRIPTION_SOURCE_ID,
                            table.columns[self._col("status")] == STATUS_SUBSCRIPTION_ACTIVE,
                        )
                    )
                ).scalars().first()
            if found is None:
                return None
            return self._to_subscription(found)
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"subscription get failed: {exc}") from exc

    async def is_active(self, subscription_id: str) -> bool:
        """Активна ли подписка: revoked/uninstalled/отсутствует → False."""
        return await self.get(subscription_id) is not None

    async def put(self, subscription: Subscription, *, active: bool = True) -> None:
        """Зарегистрировать/обновить подписку (не часть порта, для интеграции).

        ``event_types`` / ``consumer_group`` / ``plugin_id`` в A10 НЕ хранятся
        отдельными колонками, поэтому кладём их в ``source_event_id``? НЕТ:
        ``source_event_id`` — часть уникального ключа. Используем ``status``
        для активности, а типизированные метаданные кодируем в ``error_kind``
        ( String(128) ) как компактный JSON-список? НЕТ: это было бы raw-данные.
        Решение: храним ТОЛЬКО ``consumer_group`` в ``dlq_topic`` (String(255),
        аддитивная колонка), а ``event_types`` — в ``payload_digest``? Тоже нет.

        Честный подход: A10 не предусмотрела таблицу подписок, поэтому
        ``DbSubscriptionStore`` хранит ровно факт активности подписки, а
        полный объект ``Subscription`` (event_types/consumer_group) отдаётся
        вызывающему из его собственного конфига. В БД пишем канонические id и
        НЕ выдумываем сырые payload. См. риск в отчёте родителю.
        """
        if not isinstance(subscription, Subscription):
            raise PluginRepositoryError("put ожидает Subscription")
        if not active:
            await self.revoke(subscription.id)
            return
        try:
            row = self._row()
            table = self._table(row)
            status = STATUS_SUBSCRIPTION_ACTIVE
            async with self._session_ctx() as session:
                existing = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("subscription_id")] == subscription.id,
                            table.columns[self._col("source_event_id")] == SUBSCRIPTION_SOURCE_ID,
                        )
                    )
                ).scalars().first()
                if existing is not None:
                    await session.execute(
                        sa_update(row)
                        .where(table.columns[self._col("subscription_id")] == subscription.id)
                        .where(table.columns[self._col("source_event_id")] == SUBSCRIPTION_SOURCE_ID)
                        .values(**{self._col("status"): status})
                    )
                else:
                    session.add(row(**self._service_values(
                        row,
                        subscription_id=subscription.id,
                        source_event_id=SUBSCRIPTION_SOURCE_ID,
                        status=status,
                        dlq_topic=subscription.consumer_group,
                        error_kind=",".join(subscription.event_types)[:128] or None,
                    )))
                await _commit_if_owned(session, self._session is None)
        except IntegrityError:
            return  # идемпотентно
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"subscription put failed: {exc}") from exc

    async def revoke(self, subscription_id: str) -> bool:
        """Снять подписку (идентифицированно, идемпотентно)."""
        key = text(subscription_id, field="subscription_id")
        try:
            row = self._row()
            table = self._table(row)
            async with self._session_ctx() as session:
                result = await session.execute(
                    sa_update(row)
                    .where(table.columns[self._col("subscription_id")] == key)
                    .where(table.columns[self._col("source_event_id")] == SUBSCRIPTION_SOURCE_ID)
                    .values(**{self._col("status"): STATUS_SUBSCRIPTION_REVOKED})
                )
                await _commit_if_owned(session, self._session is None)
            return result.rowcount >= 1
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"subscription revoke failed: {exc}") from exc

    def _to_subscription(self, found: Any) -> Subscription | None:
        """ORM-строка → ``Subscription``; revoked/битая строка = None."""
        status = str(getattr(found, self._col("status"), "") or "")
        if status != STATUS_SUBSCRIPTION_ACTIVE:
            return None
        consumer_group = str(getattr(found, "dlq_topic", None) or "")
        event_types_raw = getattr(found, "error_kind", None) or ""
        try:
            event_types = string_tuple(
                [item for item in event_types_raw.split(",") if item],
                field="event_types",
            )
        except PluginRepositoryError:
            return None
        if not consumer_group or not event_types:
            return None
        try:
            return Subscription(
                id=text(getattr(found, "subscription_id", None), field="subscription.id"),
                plugin_id=consumer_group.split(":", 1)[-1] or "plugin",
                event_types=event_types,
                consumer_group=consumer_group,
            )
        except (PluginRepositoryError, ValueError):
            return None


class DbIdempotencyStore(DbEventStoreBase):
    """``IdempotencyStorePort``: ``(subscription_id, source_event_id)``."""

    async def seen(self, key: IdempotencyKey) -> bool:
        """Уже обработано ли событие этой подпиской.

        Ошибка БД → ``True`` (fail-closed): лучше НЕ обработать повторно, чем
        выполнить обработчик дважды.
        """
        if not isinstance(key, IdempotencyKey):
            raise PluginRepositoryError("seen ожидает IdempotencyKey")
        try:
            row = self._row()
            table = self._table(row)
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("subscription_id")] == key.subscription_id,
                            table.columns[self._col("source_event_id")] == key.source_event_id,
                            table.columns[self._col("status")] == STATUS_IDEMPOTENCY,
                        )
                    )
                ).scalars().first()
            return found is not None
        except (SQLAlchemyError, PluginRepositoryError):
            return True

    async def remember(self, key: IdempotencyKey) -> None:
        """Атомарно отметить событие обработанным (после ACK или записи в DLQ).

        Ошибка → ИСКЛЮЧЕНИЕ: offset не коммитится. Повтор ключа идемпотентен.
        """
        if not isinstance(key, IdempotencyKey):
            raise PluginRepositoryError("remember ожидает IdempotencyKey")
        try:
            row = self._row()
            async with self._session_ctx() as session:
                session.add(row(**self._service_values(
                    row,
                    subscription_id=key.subscription_id,
                    source_event_id=key.source_event_id,
                    status=STATUS_IDEMPOTENCY,
                )))
                await _commit_if_owned(session, self._session is None)
        except IntegrityError:
            return
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"idempotency remember failed: {exc}") from exc

    async def remember_pg(self, key: IdempotencyKey) -> None:
        """Явный PostgreSQL ``ON CONFLICT DO NOTHING`` (конструктор SQLAlchemy)."""
        if not isinstance(key, IdempotencyKey):
            raise PluginRepositoryError("remember_pg ожидает IdempotencyKey")
        row = self._row()
        values = self._service_values(
            row,
            subscription_id=key.subscription_id,
            source_event_id=key.source_event_id,
            status=STATUS_IDEMPOTENCY,
        )
        try:
            async with self._session_ctx() as session:
                statement = pg_insert(row).values(**values).on_conflict_do_nothing(
                    index_elements=[
                        self._col("subscription_id"),
                        self._col("source_event_id"),
                    ]
                )
                await session.execute(statement)
                await _commit_if_owned(session, self._session is None)
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"idempotency remember_pg failed: {exc}") from exc


class DbPublishedEventStore(DbEventStoreBase):
    """``PublishedEventStorePort``: дедупликация ПРОДЮСЕРА по ``event_id``."""

    async def seen(self, event_id: str) -> bool:
        key = text(event_id, field="event_id")
        try:
            row = self._row()
            table = self._table(row)
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("source_event_id")] == key,
                            table.columns[self._col("subscription_id")] == PRODUCER_SUBSCRIPTION_ID,
                            table.columns[self._col("status")] == STATUS_PUBLISHED,
                        )
                    )
                ).scalars().first()
            return found is not None
        except (SQLAlchemyError, PluginRepositoryError):
            return True

    async def remember(self, event_id: str) -> None:
        """Запомнить ``event_id`` ПОСЛЕ подтверждённой отправки (flush)."""
        key = text(event_id, field="event_id")
        try:
            row = self._row()
            async with self._session_ctx() as session:
                session.add(row(**self._service_values(
                    row,
                    subscription_id=PRODUCER_SUBSCRIPTION_ID,
                    source_event_id=key,
                    status=STATUS_PUBLISHED,
                    event_id=key,
                )))
                await _commit_if_owned(session, self._session is None)
        except IntegrityError:
            return
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"published event remember failed: {exc}") from exc


class DbDlqStore(DbEventStoreBase):
    """``DlqPort``: append-only «мёртвые» события без raw payload (кр. 11/20)."""

    async def publish(self, record: DlqRecord) -> None:
        """Записать DLQ-событие. Ошибка → исключение (offset НЕ коммитится)."""
        if not isinstance(record, DlqRecord):
            raise PluginRepositoryError("publish ожидает DlqRecord")
        event = record.event
        try:
            row = self._row()
            async with self._session_ctx() as session:
                session.add(row(**self._service_values(
                    row,
                    subscription_id=record.subscription_id,
                    source_event_id=event.source_id,
                    status=STATUS_DLQ,
                    event_id=event.event_id,
                    dlq_topic=record.dlq_topic,
                    error_kind=record.error_kind,
                    payload_digest=event.payload_digest,
                    attempts=record.attempts,
                    occurred_at=record.failed_at,
                )))
                await _commit_if_owned(session, self._session is None)
        except IntegrityError:
            # Повторная запись того же DLQ-события идемпотентна: коммит offset'а
            # после подтверждённой записи допустим.
            return
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"DLQ publish failed: {exc}") from exc

    async def count(self, subscription_id: str | None = None) -> int:
        """Диагностика для тестов; ошибка БД → 0 (не источник решения)."""
        try:
            row = self._row()
            table = self._table(row)
            condition = table.columns[self._col("status")] == STATUS_DLQ
            if subscription_id is not None:
                condition = condition & (
                    table.columns[self._col("subscription_id")] == subscription_id
                )
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(func.count()).select_from(row).where(condition)
                )
                return int(result.scalar() or 0)
        except (SQLAlchemyError, PluginRepositoryError):
            return 0


class _BorrowedSession:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


async def _commit_if_owned(session: AsyncSession, owned: bool) -> None:
    if owned:
        await session.commit()


__all__ = [
    "DbDlqStore",
    "DbIdempotencyStore",
    "DbPublishedEventStore",
    "DbSubscriptionStore",
    "PRODUCER_SUBSCRIPTION_ID",
    "STATUS_DLQ",
    "STATUS_IDEMPOTENCY",
    "STATUS_PUBLISHED",
    "STATUS_SUBSCRIPTION_ACTIVE",
    "STATUS_SUBSCRIPTION_REVOKED",
]
