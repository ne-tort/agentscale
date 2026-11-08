"""SQL-хранилища состояния шины событий плагинов (F7, задача 10).

Реализуют порты из ``application/plugins/events/ports.py``:

* :class:`DbSubscriptionStore`      → ``SubscriptionStorePort``;
* :class:`DbIdempotencyStore`       → ``IdempotencyStorePort``;
* :class:`DbPublishedEventStore`    → ``PublishedEventStorePort``;
* :class:`DbDlqStore`               → ``DlqPort`` (append-only «мёртвые» события).

Все четыре работают поверх ОДНОЙ модели ``PluginEventDeliveryRow`` (A10) с
различением по колонке ``kind``/``status`` и уникальностью
``(subscription_id, source_event_id)`` — ровно тот ключ, который требует §7.3:

* НЕ URL, НЕ hash тела, НЕ ``event_id`` платформы;
* ``event_id`` используется ТОЛЬКО продюсером (``DbPublishedEventStore``),
  у консьюмера и продюсера РАЗНЫЕ ключи идемпотентности.

Атомарность ``remember`` (критично для критерия №20)
----------------------------------------------------
``INSERT ... ON CONFLICT DO NOTHING`` (PostgreSQL) / «проверка → INSERT с
перехватом ``IntegrityError``» (универсальный путь). Повторная доставка того же
``(subscription_id, source_event_id)`` НЕ вызывает обработчик второй раз даже
при конкурентных консьюмерах. Уникальность обеспечивается БД, а не Python-lock.

Fail-closed:
* неизвестная/неактивная подписка → ``None``/``False``;
* ``seen`` при ошибке БД → ``True`` («уже обработано» = НЕ пустить повторно),
  а ``remember`` при ошибке → ИСКЛЮЧЕНИЕ (offset не коммитится, диспечер
  сделает ``seek`` и доставит событие заново);
* ``DlqPort.publish`` при ошибке → исключение (иначе коммит offset'а потерял бы
  событие — см. докстринг порта).

Ни одно событие не содержит raw payload: только ``payload_digest`` и короткие
технические метаданные (criterion 11).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
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
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    json_mapping,
    load_row,
    string_tuple,
    text,
)

#: Разделители видов записей в одной таблице (если A10 не даст отдельную колонку
#: ``kind``, используем префикс ``status`` — см. :meth:`_kind_value`).
KIND_SUBSCRIPTION = "subscription"
KIND_IDEMPOTENCY = "idempotency"
KIND_PUBLISHED = "published"
KIND_DLQ = "dlq"

STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
STATUS_RECORDED = "recorded"


class DbEventStoreBase:
    """Общая инфраструктура: модель, колонки, сессия, уникальность."""

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

    def _kind_col(self, row: type) -> str | None:
        """Колонка-дискриминатор, если есть (``kind`` / ``record_kind``)."""
        for name in ("kind", "record_kind", "type"):
            if name in row.__table__.columns.keys():
                return name
        return None

    def _kind_values(self, row: type, kind: str) -> dict[str, Any]:
        """Значения колонок, идентифицирующие вид записи."""
        col = self._kind_col(row)
        if col is not None:
            return {col: kind}
        # Без дискриминатора различаем по префиксу служебного ``status``.
        return {self._col("status"): f"{kind}:{STATUS_RECORDED}"}

    def _kind_condition(self, row: type, kind: str) -> Any:
        table = row.__table__
        col = self._kind_col(row)
        if col is not None:
            return table.columns[col] == kind
        return table.columns[self._col("status")] == f"{kind}:{STATUS_RECORDED}"

    def _optional(self, row: type, name: str) -> str | None:
        return name if name in row.__table__.columns.keys() else None

    def _insert_values(self, row: type, values: dict[str, Any]) -> dict[str, Any]:
        """Отфильтровать значения по реально существующим колонкам."""
        existing = set(row.__table__.columns.keys())
        return {key: value for key, value in values.items() if key in existing}


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
                            self._kind_condition(row, KIND_SUBSCRIPTION),
                        )
                    )
                ).scalars().first()
            if found is None:
                return None
            return self._to_subscription(found)
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            # Неизвестная подписка → None (диспечер отказывает), но не «группа
            # по умолчанию». Ошибку пробрасываем для позитивных мутаций.
            raise PluginRepositoryUnavailable(f"subscription get failed: {exc}") from exc

    async def is_active(self, subscription_id: str) -> bool:
        """Активна ли подписка: revoked/uninstalled/отсутствует → False."""
        subscription = await self.get(subscription_id)
        return subscription is not None

    async def put(self, subscription: Subscription, *, active: bool = True) -> None:
        """Зарегистрировать/обновить подписку (не часть порта, для интеграции)."""
        if not isinstance(subscription, Subscription):
            raise PluginRepositoryError("put ожидает Subscription")
        if not active:
            await self.revoke(subscription.id)
            return
        try:
            row = self._row()
            values = self._insert_values(row, {
                self._col("subscription_id"): subscription.id,
                self._col("source_event_id"): "__subscription__",
                self._col("status"): STATUS_ACTIVE,
                self._col("created_at"): self._now(),
                "subscription_id": subscription.id,
                "plugin_id": subscription.plugin_id,
                "consumer_group": subscription.consumer_group,
                "event_types": list(subscription.event_types),
                "details": {
                    "plugin_id": subscription.plugin_id,
                    "event_types": list(subscription.event_types),
                    "consumer_group": subscription.consumer_group,
                },
                **self._kind_values(row, KIND_SUBSCRIPTION),
            })
            async with self._session_ctx() as session:
                session.add(row(**values))
                await _commit_if_owned(session, self._session is None)
        except IntegrityError:
            # Уже существует — идемпотентно считаем зарегистрированной.
            return
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"subscription put failed: {exc}") from exc

    async def revoke(self, subscription_id: str) -> bool:
        """Снять подписку (идентифицированно, идемпотентно)."""
        from sqlalchemy import update as sa_update

        key = text(subscription_id, field="subscription_id")
        try:
            row = self._row()
            table = self._table(row)
            async with self._session_ctx() as session:
                result = await session.execute(
                    sa_update(row)
                    .where(
                        table.columns[self._col("subscription_id")] == key,
                        self._kind_condition(row, KIND_SUBSCRIPTION),
                    )
                    .values(**{self._col("status"): STATUS_REVOKED})
                )
                await _commit_if_owned(session, self._session is None)
            return result.rowcount >= 1
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"subscription revoke failed: {exc}") from exc

    def _to_subscription(self, found: Any) -> Subscription | None:
        """ORM-строка → ``Subscription``; revoked/битая строка = None."""
        status = str(getattr(found, self._col("status"), "") or "")
        # Если дискриминатор вынесен в ``kind``, статус хранится отдельно.
        if self._kind_col(type(found)) is not None and status == STATUS_REVOKED:
            return None
        details = json_mapping(getattr(found, "details", None), field="subscription.details")
        event_types_raw = getattr(found, "event_types", None) or details.get("event_types")
        try:
            return Subscription(
                id=text(getattr(found, "subscription_id", None), field="subscription.id"),
                plugin_id=text(details.get("plugin_id") or getattr(found, "plugin_id", None), field="plugin_id"),
                event_types=string_tuple(event_types_raw, field="event_types"),
                consumer_group=text(
                    details.get("consumer_group") or getattr(found, "consumer_group", None),
                    field="consumer_group",
                ),
            )
        except (PluginRepositoryError, ValueError):
            # Битая строка подписки = НЕ активна (fail-closed), не исключение
            # на каждом событии.
            return None


class DbIdempotencyStore(DbEventStoreBase):
    """``IdempotencyStorePort``: ``(subscription_id, source_event_id)``.

    Атомарность: уникальный индекс в БД + ``ON CONFLICT DO NOTHING``.
    """

    async def seen(self, key: IdempotencyKey) -> bool:
        """Уже обработано ли событие этой подпиской.

        Ошибка БД → ``True`` (fail-closed): лучше НЕ обработать повторно, чем
        выполнить обработчик дважды. Диспечер при ``seen=True`` коммитит offset,
        но при любом исключении порта делает ``seek`` — см. ``dispatch.py``.
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
                            self._kind_condition(row, KIND_IDEMPOTENCY),
                        )
                    )
                ).scalars().first()
            return found is not None
        except (SQLAlchemyError, PluginRepositoryError):
            return True

    async def remember(self, key: IdempotencyKey) -> None:
        """Атомарно отметить событие обработанным (после ACK или записи в DLQ).

        Ошибка → ИСКЛЮЧЕНИЕ: offset не коммитится, событие будет доставлено
        заново. Повтор того же ключа идемпотентен (``ON CONFLICT DO NOTHING``).
        """
        if not isinstance(key, IdempotencyKey):
            raise PluginRepositoryError("remember ожидает IdempotencyKey")
        try:
            row = self._row()
            values = self._insert_values(row, {
                self._col("subscription_id"): key.subscription_id,
                self._col("source_event_id"): key.source_event_id,
                self._col("status"): STATUS_RECORDED,
                self._col("created_at"): self._now(),
                "event_id": key.source_event_id,
                "attempts": 1,
                "details": {},
                **self._kind_values(row, KIND_IDEMPOTENCY),
            })
            async with self._session_ctx() as session:
                session.add(row(**values))
                await _commit_if_owned(session, self._session is None)
        except IntegrityError:
            # Конкурентный консьюмер уже запомнил ключ — идемпотентно ОК.
            return
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"idempotency remember failed: {exc}") from exc

    async def remember_pg(self, key: IdempotencyKey) -> None:
        """Явный PostgreSQL ``ON CONFLICT DO NOTHING`` (без перехвата ошибки).

        Используется, когда известен уникальный индекс
        ``(subscription_id, source_event_id)``. Запрос идёт через
        ``sqlalchemy.dialects.postgresql.insert`` — это конструктор SQLAlchemy,
        а НЕ raw SQL-строка.
        """
        if not isinstance(key, IdempotencyKey):
            raise PluginRepositoryError("remember_pg ожидает IdempotencyKey")
        row = self._row()
        values = self._insert_values(row, {
            self._col("subscription_id"): key.subscription_id,
            self._col("source_event_id"): key.source_event_id,
            self._col("status"): STATUS_RECORDED,
            self._col("created_at"): self._now(),
            **self._kind_values(row, KIND_IDEMPOTENCY),
        })
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
                            self._kind_condition(row, KIND_PUBLISHED),
                        )
                    )
                ).scalars().first()
            return found is not None
        except (SQLAlchemyError, PluginRepositoryError):
            # Продюсер: «уже публиковали» = не публикуем повторно (fail-closed
            # относительно дублей), но не теряем событие консьюмера.
            return True

    async def remember(self, event_id: str) -> None:
        """Запомнить ``event_id`` ПОСЛЕ подтверждённой отправки (flush)."""
        key = text(event_id, field="event_id")
        try:
            row = self._row()
            values = self._insert_values(row, {
                self._col("subscription_id"): "__producer__",
                self._col("source_event_id"): key,
                self._col("status"): STATUS_RECORDED,
                self._col("created_at"): self._now(),
                "event_id": key,
                "details": {},
                **self._kind_values(row, KIND_PUBLISHED),
            })
            async with self._session_ctx() as session:
                session.add(row(**values))
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
        # Только канонические id и digest-ы. Ни payload, ни URL, ни текста
        # исключения: ``error_kind`` — имя класса (контракт DlqRecord).
        safe_details = {
            "event_id": event.event_id,
            "source_event_id": event.source_id,
            "event_type": event.event_type,
            "company_id": event.company_id,
            "subscription_id": record.subscription_id,
            "topic": record.topic,
            "dlq_topic": record.dlq_topic,
            "attempts": record.attempts,
            "error_kind": record.error_kind,
            "payload_digest": event.payload_digest,
            "run_id": event.run_id,
            "correlation_id": event.correlation_id,
        }
        try:
            row = self._row()
            values = self._insert_values(row, {
                self._col("subscription_id"): record.subscription_id,
                self._col("source_event_id"): event.source_id,
                self._col("status"): "dlq",
                self._col("created_at"): record.failed_at,
                "event_id": event.event_id,
                "topic": record.topic,
                "dlq_topic": record.dlq_topic,
                "attempts": record.attempts,
                "error_kind": record.error_kind,
                "payload_digest": event.payload_digest,
                "details": safe_details,
                **self._kind_values(row, KIND_DLQ),
            })
            async with self._session_ctx() as session:
                session.add(row(**values))
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
        from sqlalchemy import func

        try:
            row = self._row()
            table = self._table(row)
            condition = self._kind_condition(row, KIND_DLQ)
            if subscription_id is not None:
                condition = condition & (table.columns[self._col("subscription_id")] == subscription_id)
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
    "KIND_DLQ",
    "KIND_IDEMPOTENCY",
    "KIND_PUBLISHED",
    "KIND_SUBSCRIPTION",
]
