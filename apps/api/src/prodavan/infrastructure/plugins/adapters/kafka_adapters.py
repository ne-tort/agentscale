"""Реальные адаптеры Redpanda/Kafka для dispatch-контура плагинов (задача 10).

Реализуют 4 порта ``application/plugins/events/ports.py``:

* :class:`AiokafkaProducerAdapter`   → ``ProducerPort``
* :class:`AiokafkaConsumerAdapter`   → ``ConsumerPort`` (ЯВНЫЕ poll/commit/seek)
* :class:`KafkaTopicAdminAdapter`    → ``TopicAdminPort``
* :class:`KafkaDlqAdapter`           → ``DlqPort``

ПОЧЕМУ НЕ as-built ``KafkaManager`` (§7.3, §13.3, criterion 20)
---------------------------------------------------------------
``core/infra/kafka_manager.py:690-697`` поднимает консьюмер с
``KAFKA_CONSUMER_MODE=kick`` + ``enable_auto_commit=True`` +
``auto_offset_reset="latest"``: это «будильник», который коммитит offset ДО
обработки и теряет события при простое консьюмера. Его ``_ensure_topics``
(``kafka_manager.py:606-608``) при ошибке **молча** надеется на auto-create,
тогда как в кластере выставлено ``auto_create_topics_enabled=false``
(``infra/k3s/base/platform/kafka.yaml:221``).

Здесь конфигурация берётся ТОЛЬКО из ``events/contracts.py::consumer_config()``
(задача 10), а НЕ из as-built ``KAFKA_CONSUMER_MODE``/настроек:

    auto_offset_reset = "earliest"
    enable_auto_commit = False
    consumer_mode = "dispatch"
    max_delivery_attempts = 3
    group_id = subscription.consumer_group

``consumer_config()`` — единственный источник истины: адаптер читает его при
каждом ``start()`` и явно отказывает, если канонические значения изменились
(защита от «тихого» возврата к kick/auto_commit). Ключ партиции = ``company_id``
(``events/topics.py::partition_key_bytes``), RF=1 для single-node Redpanda.

Fail-closed: брокер/админ/продюсер недоступен → исключение, а не «буферизуем в
памяти и забудем». Ни один сбой не приводит к коммиту offset'а.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from prodavan.application.plugins.events.contracts import (
    CONSUMER_AUTO_OFFSET_RESET,
    CONSUMER_ENABLE_AUTO_COMMIT,
    MAX_DELIVERY_ATTEMPTS,
    consumer_config,
)
from prodavan.application.plugins.events.ports import ConsumedRecord
from prodavan.application.plugins.events.topics import (
    DEFAULT_NUM_PARTITIONS,
    DEFAULT_REPLICATION_FACTOR,
    PLUGIN_EVENTS_TOPIC,
    dlq_topic,
    partition_key_bytes,
)

logger = logging.getLogger(__name__)

# Как устроен aiokafka в репозитории: импорт ВНУТРИ методов/start(), а не на
# уровне модуля (``kafka_manager.py:588``, ``:639``, ``:687``). Это позволяет
# поднять пакет адаптеров даже без установленного aiokafka и даёт честный
# отказ именно при старте, а не при импорте всего приложения.
_AIOKAFKA_IMPORT_HINT: Final[str] = "aiokafka>=0.11.0 (см. apps/api/pyproject.toml:29)"


class KafkaAdapterError(RuntimeError):
    """Отказ адаптера Kafka (fail-closed). Не коммитим offset при любом сбое."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def consumer_settings_from_contract() -> dict[str, Any]:
    """Канонический конфиг консьюмера задачи 10 + проверка инвариантов.

    ЯВНО запрещаем as-built ``kick``/``auto_commit``/``latest``: если кто-то
    изменит ``consumer_config()``, адаптер откажется стартовать, а не тихо
    деградирует до «будильника».
    """
    config = consumer_config()
    if config.get("auto_offset_reset") != CONSUMER_AUTO_OFFSET_RESET:
        raise KafkaAdapterError(
            "consumer_config().auto_offset_reset differs from the canonical 'earliest'"
        )
    if config.get("enable_auto_commit") is not CONSUMER_ENABLE_AUTO_COMMIT:
        raise KafkaAdapterError(
            "consumer_config().enable_auto_commit differs from the canonical False"
        )
    if config.get("consumer_mode") != "dispatch":
        raise KafkaAdapterError(
            "consumer_config().consumer_mode must be 'dispatch', not KAFKA_CONSUMER_MODE=kick"
        )
    if config.get("max_delivery_attempts") != MAX_DELIVERY_ATTEMPTS:
        raise KafkaAdapterError(
            "consumer_config().max_delivery_attempts differs from the canonical 3"
        )
    if config.get("enable_auto_commit") or config.get("auto_offset_reset") != "earliest":
        raise KafkaAdapterError("kick + auto_commit + latest теряют события при простое")
    return config


# ---------------------------------------------------------------------------
# ProducerPort
# ---------------------------------------------------------------------------


def _encode(value: Mapping[str, Any]) -> bytes:
    """Канонический JSON без raw payload (в wire уходит только digest, §4.5)."""
    if not isinstance(value, Mapping):
        raise KafkaAdapterError("producer value must be a Mapping")
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _headers(headers: Mapping[str, str] | None) -> list[tuple[str, bytes]]:
    if not headers:
        return []
    return [
        (str(name), str(value).encode("utf-8"))
        for name, value in headers.items()
        if isinstance(name, str) and isinstance(value, str)
    ]


class AiokafkaProducerAdapter:
    """``ProducerPort``: aiokafka ``AIOKafkaProducer`` с ``acks=all``.

    ``enable_idempotence=True`` + ``acks="all"`` повторяют настройку as-built
    ``kafka_manager.py:641-646``, но топик НЕ считается созданным «сам»:
    :class:`KafkaTopicAdminAdapter` обязателен (``auto_create=false``).

    ``send`` НЕ буферизует «на потом»: любая ошибка брокера пробрасывается,
    ``PluginEventDispatcher.publish`` превращает её в ``PUBLISH_FAILED`` и
    НЕ вызывает ``store.remember(event_id)`` → событие не теряется.
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        client_id: str = "prodavan-plugin-events-producer",
        producer: Any = None,
    ) -> None:
        if not isinstance(bootstrap_servers, str) or not bootstrap_servers.strip():
            raise KafkaAdapterError("bootstrap_servers is required (no dev fallback)")
        self._bootstrap = bootstrap_servers.strip()
        self._client_id = client_id
        self._producer = producer  # для тестов / явной подстановки
        self._owns_producer = producer is None
        self.sent: list[dict[str, Any]] = []
        self.flushes: int = 0

    async def start(self) -> None:
        if self._producer is not None:
            return
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:
            raise KafkaAdapterError(f"aiokafka is not installed: {_AIOKAFKA_IMPORT_HINT}") from exc
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
            acks="all",
            enable_idempotence=True,
        )
        try:
            await self._producer.start()
        except Exception as exc:  # noqa: BLE001 — сбой старта = явный отказ
            self._producer = None
            raise KafkaAdapterError(
                f"plugin events producer failed to start: {type(exc).__name__}"
            ) from exc

    async def send(
        self,
        *,
        topic: str,
        key: str,
        value: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> None:
        producer = await self._require_started()
        # Ключ партиции УЖЕ равен company_id (dispatch.py:258), НЕ хэшируем и
        # НЕ подменяем: все события компании идут в одну партицию (§7.3).
        encoded_key = partition_key_bytes(key)
        try:
            await producer.send_and_wait(
                topic,
                key=encoded_key,
                value=_encode(value),
                headers=_headers(headers),
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed, без «и так сойдёт»
            raise KafkaAdapterError(
                f"plugin event publish failed for topic {topic!r}: {type(exc).__name__}"
            ) from exc
        self.sent.append({"topic": topic, "key": key, "value": dict(value)})

    async def flush(self) -> None:
        producer = await self._require_started()
        try:
            await producer.flush()
        except Exception as exc:  # noqa: BLE001 — flush до remember обязателен
            raise KafkaAdapterError(
                f"plugin event producer flush failed: {type(exc).__name__}"
            ) from exc
        self.flushes += 1

    async def _require_started(self) -> Any:
        if self._producer is None:
            # Явный отказ вместо «молча создадим и забудем»: старт продюсера —
            # обязанность композитора (build.py / lifespan родителя).
            raise KafkaAdapterError("AiokafkaProducerAdapter.start() was not called")
        return self._producer

    async def stop(self) -> None:
        if self._producer is not None and self._owns_producer:
            try:
                await self._producer.stop()
            except Exception:  # noqa: BLE001 — остановка не маскирует первичный отказ
                logger.exception("plugin events producer stop failed")
            self._producer = None
        elif not self._owns_producer:
            # Чужой продюсер НЕ закрываем: владелец — интегратор.
            self._producer = None


# ---------------------------------------------------------------------------
# ConsumedRecord: адаптер wire-байтов к структурному Protocol задачи 10
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KafkaConsumedRecord:
    """Обёртка над ``aiokafka.ConsumerRecord`` для ``ConsumedRecord``.

    Протокол задачи 10 требует ``value: Mapping[str, Any]``, а aiokafka отдаёт
    ``bytes``. Декодер применяется ЛЕНИВО (в свойстве ``value``): если запись
    битая, ``dispatch.consume`` получит ``INVALID_EVENT`` → ``seek(offset)`` →
    повтор, а не «тихий пропуск».
    """

    topic: str
    partition: int
    offset: int
    key: bytes | None
    raw_value: bytes
    decoder: Callable[[bytes], Mapping[str, Any]] = field(
        default=lambda raw: json.loads(raw.decode("utf-8"))
    )

    @property
    def value(self) -> Mapping[str, Any]:
        # Битые байты НЕ пробрасываем как KafkaAdapterError: диспечер ловит
        # только PluginEventError от from_wire(), после чего делает seek(offset).
        # Возвращаем пустой/некорректный объект — from_wire сам откажет и
        # позиция будет откачена к этой же записи (без потери события).
        try:
            decoded = self.decoder(self.raw_value)
        except Exception:  # noqa: BLE001 — невалидный JSON = INVALID_EVENT в dispatch
            return {}
        return decoded if isinstance(decoded, Mapping) else {}


def _wrap(record: Any) -> ConsumedRecord:
    """Единая точка адаптации ``ConsumerRecord`` → ``ConsumedRecord``."""
    raw = getattr(record, "value", None)
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise KafkaAdapterError("Kafka record value must be bytes")
    return KafkaConsumedRecord(
        topic=str(getattr(record, "topic", "")),
        partition=int(getattr(record, "partition", 0)),
        offset=int(getattr(record, "offset", 0)),
        key=getattr(record, "key", None),
        raw_value=bytes(raw),
    )


# ---------------------------------------------------------------------------
# ConsumerPort — ЯВНЫЕ poll / commit / seek, без auto_commit
# ---------------------------------------------------------------------------


class AiokafkaConsumerAdapter:
    """``ConsumerPort``: aiokafka ``AIOKafkaConsumer`` с РУЧНЫМ коммитом.

    Обязательная конфигурация (из ``consumer_config()`` задачи 10):

    * ``enable_auto_commit=False`` — коммит ТОЛЬКО после ACK/DLQ;
    * ``auto_offset_reset="earliest"`` — при простое/рестарте не теряем события;
    * ``group_id=subscription.consumer_group`` — отдельная группа на подписку
      (§7.3), каждый плагин коммитит СВОЙ offset;
    * ``consumer_mode="dispatch"`` — НЕ ``KAFKA_CONSUMER_MODE=kick``.

    ``poll`` читает ОДНУ запись (``getone``), ``commit`` фиксирует ровно
    переданный offset (``OffsetAndMetadata(offset + 1, ...)``), ``seek``
    откатывает позицию к этому же offset'у при RETRY/ошибке порта.
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        subscription: Any,
        topic: str = PLUGIN_EVENTS_TOPIC,
        client_id: str = "prodavan-plugin-events-consumer",
        consumer: Any = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(bootstrap_servers, str) or not bootstrap_servers.strip():
            raise KafkaAdapterError("bootstrap_servers is required (no dev fallback)")
        group_id = getattr(subscription, "consumer_group", None)
        if not isinstance(group_id, str) or not group_id.strip():
            raise KafkaAdapterError(
                "subscription.consumer_group is required: separate group per subscription"
            )
        # Конфиг задачи 10, НЕ as-built KAFKA_CONSUMER_MODE=kick.
        self._config = dict(config if config is not None else consumer_settings_from_contract())
        self._bootstrap = bootstrap_servers.strip()
        self._group_id = group_id.strip()
        self._topic = topic
        self._client_id = client_id
        self._consumer = consumer
        self._owns_consumer = consumer is None
        self.start_kwargs: dict[str, Any] = {}
        self.polls: int = 0
        self.commits: list[int] = []
        self.seeks: list[int] = []
        #: TopicPartition последней прочитанной записи. Протокол задачи 10
        #: передаёт в commit/seek ТОЛЬКО offset, поэтому адаптер сам помнит,
        #: к какой партиции он относится (иначе коммит задел бы чужие партиции).
        self._last_tp: Any | None = None

    @property
    def group_id(self) -> str:
        return self._group_id

    @property
    def settings(self) -> dict[str, Any]:
        """Диагностическая проекция (для acceptance-теста №20)."""
        return {**self._config, "topic": self._topic, "group_id": self._group_id}

    async def start(self) -> None:
        # Конфиг задачи 10 проверяется ВСЕГДА, даже если консьюмер подставлен
        # извне (unit-тест/фейк): «чужой» kick/auto_commit/latest запрещён.
        settings = consumer_settings_from_contract()
        merged = {**settings, **self._config}
        if merged.get("enable_auto_commit") is not False or merged.get("auto_offset_reset") != "earliest":
            raise KafkaAdapterError(
                "refusing to start a kick/auto_commit/latest consumer for plugin events"
            )
        if merged.get("consumer_mode") != "dispatch":
            raise KafkaAdapterError("KAFKA_CONSUMER_MODE=kick is forbidden for plugin events")
        self._config = merged
        kwargs: dict[str, Any] = {
            "bootstrap_servers": self._bootstrap,
            "client_id": self._client_id,
            # group_id = subscription.consumer_group (§7.3): отдельная группа
            # на подписку, НЕ общий prodavan-api-triggers из kafka_manager.py.
            "group_id": self._group_id,
            "enable_auto_commit": False,
            "auto_offset_reset": "earliest",
            "value_deserializer": lambda raw: raw,  # decoder применяется в _wrap
        }
        self.start_kwargs = dict(kwargs)
        if self._consumer is not None:
            return  # уже стартован / подставлен интегратором
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError as exc:
            raise KafkaAdapterError(f"aiokafka is not installed: {_AIOKAFKA_IMPORT_HINT}") from exc
        self._consumer = AIOKafkaConsumer(self._topic, **kwargs)
        try:
            await self._consumer.start()
        except Exception as exc:  # noqa: BLE001 — сбой старта = явный отказ
            self._consumer = None
            raise KafkaAdapterError(
                f"plugin events consumer failed to start: {type(exc).__name__}"
            ) from exc

    async def _require_started(self) -> Any:
        if self._consumer is None:
            raise KafkaAdapterError("AiokafkaConsumerAdapter.start() was not called")
        return self._consumer

    async def poll(self, *, timeout_ms: int = 1000) -> ConsumedRecord | None:
        consumer = await self._require_started()
        self.polls += 1
        # Без asyncio.wait_for: aiokafka.getone сам соблюдает timeout_ms.
        # Никакого автокоммита — offset живёт до явного commit().
        try:
            record = await consumer.getone(timeout_ms=int(timeout_ms))
        except asyncio.TimeoutError:
            # Простой консьюмера: НИКАКОГО коммита, НИКАКОЙ потери записи.
            # Это и есть отличие от kick-режима (acceptance №20).
            return None
        except Exception as exc:  # noqa: BLE001 — любая ошибка чтения = отказ
            raise KafkaAdapterError(f"plugin events poll failed: {type(exc).__name__}") from exc
        if record is None:
            return None
        self._last_tp = self._topic_partition_of(record)
        return _wrap(record)

    @staticmethod
    def _topic_partition_of(record: Any) -> Any:
        """``TopicPartition`` записи без жёсткого импорта aiokafka.structs."""
        tp = getattr(record, "partition", None)
        if tp is not None and type(tp).__name__ == "TopicPartition":
            return tp
        try:
            from aiokafka.structs import TopicPartition

            return TopicPartition(str(record.topic), int(record.partition))
        except Exception:  # noqa: BLE001 — фолбэк для duck-typed тестовых записей
            return (str(getattr(record, "topic", "")), int(getattr(record, "partition", 0)))

    async def commit(self, offset: int) -> None:
        consumer = await self._require_started()
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise KafkaAdapterError("commit offset must be a non-negative integer")
        try:
            from aiokafka.structs import OffsetAndMetadata
        except ImportError as exc:
            raise KafkaAdapterError(f"aiokafka.structs unavailable: {_AIOKAFKA_IMPORT_HINT}") from exc
        # Явный параметр offset: коммитим РОВНО обработанную запись (+1 —
        # следующий читаемый offset), и ТОЛЬКО ту партицию, из которой она
        # пришла. Коммит по всем assignment() с одним offset'ом был бы ошибкой:
        # соседние партиции имеют собственные позиции (§7.3, criterion 20).
        if self._last_tp is None:
            raise KafkaAdapterError("no polled record to commit: refusing to commit blindly")
        assignment = {self._last_tp: OffsetAndMetadata(offset + 1, "")}
        try:
            await consumer.commit(assignment)
        except Exception as exc:  # noqa: BLE001 — сбой коммита → seek + COMMIT_FAILED
            raise KafkaAdapterError(
                f"plugin events commit failed for offset={offset}: {type(exc).__name__}"
            ) from exc
        self.commits.append(offset)

    async def seek(self, offset: int) -> None:
        consumer = await self._require_started()
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise KafkaAdapterError("seek offset must be a non-negative integer")
        # Возврат к ЭТОМУ ЖЕ offset'у: событие будет доставлено повторно.
        # Откатываем ТОЛЬКО партицию прочитанной записи (см. commit).
        if self._last_tp is not None:
            try:
                consumer.seek(self._last_tp, offset)
            except Exception as exc:  # noqa: BLE001 — откат позиции обязателен
                raise KafkaAdapterError(
                    f"plugin events seek failed for offset={offset}: {type(exc).__name__}"
                ) from exc
        self.seeks.append(offset)

    async def close(self) -> None:
        """Остановить консьюмер; НЕ коммитит необработанные offset'ы."""
        consumer = self._consumer
        if consumer is None:
            return
        try:
            if self._owns_consumer:
                await consumer.stop()
        except Exception:  # noqa: BLE001 — остановка не маскирует отказ
            logger.exception("plugin events consumer stop failed")
        finally:
            # Ссылку снимаем в любом случае (закрываем только свой консьюмер).
            self._consumer = None


# ---------------------------------------------------------------------------
# TopicAdminPort — ОБЯЗАТЕЛЕН при auto_create_topics_enabled=false
# ---------------------------------------------------------------------------


class KafkaTopicAdminAdapter:
    """``TopicAdminPort``: явное управление топиками через ``AIOKafkaAdminClient``.

    В кластере ``rpk cluster config set auto_create_topics_enabled false``
    (``kafka.yaml:221``): топик ``agentscale.plugin.events`` и его DLQ
    ``agentscale.plugin.events.dlq`` НЕ появятся «сами» при первом produce.
    Поэтому ``ensure_topic`` / ``ensure_dlq_topic`` (``events/topics.py``)
    обязательны до любой публикации/чтения, а этот адаптер — единственный
    легальный путь их создания.

    Отличие от as-built ``KafkaManager._ensure_topics`` (``:606-608``): там
    ошибка админ-клиента молча логируется и «надеется на auto-create». Здесь
    любая ошибка = ``KafkaAdapterError`` → ``TOPIC_NOT_ENSURED`` → публикация
    запрещена (fail-closed).

    RF=1: single-node Redpanda; RF>1 даёт under-replicated топик и останавливает
    продюсер с ``acks=all`` (``topics.py:41-45``).
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        client_id: str = "prodavan-plugin-events-admin",
        admin: Any = None,
    ) -> None:
        if not isinstance(bootstrap_servers, str) or not bootstrap_servers.strip():
            raise KafkaAdapterError("bootstrap_servers is required (no dev fallback)")
        self._bootstrap = bootstrap_servers.strip()
        self._client_id = client_id
        self._admin = admin
        self._owns_admin = admin is None
        self.created: list[tuple[str, int, int]] = []
        self.exists_calls: list[str] = []

    async def start(self) -> None:
        if self._admin is not None:
            return
        try:
            from aiokafka.admin import AIOKafkaAdminClient
        except ImportError as exc:
            raise KafkaAdapterError(f"aiokafka.admin is not installed: {_AIOKAFKA_IMPORT_HINT}") from exc
        client = AIOKafkaAdminClient(
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
        )
        try:
            await client.start()
        except Exception as exc:  # noqa: BLE001 — авто-создание НЕ фолбэк
            raise KafkaAdapterError(
                f"plugin events admin client failed to start: {type(exc).__name__}"
            ) from exc
        self._admin = client

    async def _require_started(self) -> Any:
        if self._admin is None:
            raise KafkaAdapterError("KafkaTopicAdminAdapter.start() was not called")
        return self._admin

    async def topic_exists(self, topic: str) -> bool:
        if not isinstance(topic, str) or not topic.strip():
            return False
        admin = await self._require_started()
        self.exists_calls.append(topic)
        try:
            existing = await admin.list_topics()
        except Exception as exc:  # noqa: BLE001 — НЕ «наверное существует»
            raise KafkaAdapterError(
                f"plugin events list_topics failed: {type(exc).__name__}"
            ) from exc
        return topic in set(existing)

    async def create_topic(
        self,
        topic: str,
        *,
        num_partitions: int,
        replication_factor: int,
    ) -> None:
        admin = await self._require_started()
        if replication_factor != DEFAULT_REPLICATION_FACTOR:
            raise KafkaAdapterError(
                "RF=1 for single-node Redpanda; replication_factor>1 is forbidden"
            )
        if num_partitions < DEFAULT_NUM_PARTITIONS:
            raise KafkaAdapterError("num_partitions must be >= 1")
        try:
            from aiokafka.admin import NewTopic
        except ImportError as exc:
            raise KafkaAdapterError(f"aiokafka.admin.NewTopic unavailable: {_AIOKAFKA_IMPORT_HINT}") from exc
        try:
            await admin.create_topics(
                [
                    NewTopic(
                        name=topic,
                        num_partitions=num_partitions,
                        replication_factor=replication_factor,
                    )
                ]
            )
        except Exception as exc:  # noqa: BLE001 — ошибка создания ≠ успех
            raise KafkaAdapterError(
                f"plugin events create_topic({topic!r}) failed: {type(exc).__name__}"
            ) from exc
        self.created.append((topic, num_partitions, replication_factor))

    async def ensure_plugin_topics(self, topic: str = PLUGIN_EVENTS_TOPIC) -> tuple[str, ...]:
        """Явно подготовить ОБА топика: основной и ``.dlq``.

        Удобная точка для ``build.py``/lifespan: вызывает те же
        ``ensure_topic`` / ``ensure_dlq_topic`` задачи 10, что и диспечер,
        но ДО старта продюсера/консьюмера (acceptance №20).
        """
        from prodavan.application.plugins.events.topics import (
            ensure_dlq_topic,
            ensure_topic,
        )

        await self.start()
        main = await ensure_topic(self, topic)
        dead = await ensure_dlq_topic(self, topic)
        return (main, dead)

    async def close(self) -> None:
        admin = self._admin
        if admin is None:
            return
        try:
            if self._owns_admin:
                await admin.close()
        except Exception:  # noqa: BLE001
            logger.exception("plugin events admin close failed")
        finally:
            self._admin = None


# ---------------------------------------------------------------------------
# DlqPort
# ---------------------------------------------------------------------------


class KafkaDlqAdapter:
    """``DlqPort``: подтверждённая запись ``DlqRecord`` в DLQ-топик.

    Топик — ``dlq_topic(PLUGIN_EVENTS_TOPIC)`` = ``agentscale.plugin.events.dlq``.
    Ключ партиции = ``company_id`` исходного события (сохраняем порядок, §7.3).
    В wire уходит ``DlqRecord.to_wire()``: **без raw payload**, только
    ``payload_digest`` + ``event_id`` + ``run_id`` (§4.5, criterion 11).

    Запись ОБЯЗАНА быть подтверждённой (``send_and_wait`` + ``flush``): коммит
    offset'а исходного топика допустим только после того, как событие реально
    появилось в DLQ (``ports.py::DlqPort``, ``dispatch.py::_send_to_dlq``).
    """

    def __init__(
        self,
        *,
        producer: Any,
        topic: str = PLUGIN_EVENTS_TOPIC,
    ) -> None:
        if producer is None or not callable(getattr(producer, "send_and_wait", None)):
            raise KafkaAdapterError("KafkaDlqAdapter requires AIOKafkaProducer.send_and_wait")
        self._producer = producer
        self._dlq_topic = dlq_topic(topic)
        self.published: list[dict[str, Any]] = []

    @property
    def dlq_topic_name(self) -> str:
        return self._dlq_topic

    async def publish(self, record: Any) -> None:
        to_wire = getattr(record, "to_wire", None)
        if not callable(to_wire):
            raise KafkaAdapterError("DlqPort.publish expects a DlqRecord")
        wire = to_wire()
        if not isinstance(wire, Mapping):
            raise KafkaAdapterError("DlqRecord.to_wire() must return a Mapping")
        event = getattr(record, "event", None)
        company_id = getattr(event, "company_id", None)
        if not isinstance(company_id, str) or not company_id.strip():
            raise KafkaAdapterError("DLQ record has no company_id partition key")
        try:
            await self._producer.send_and_wait(
                self._dlq_topic,
                key=partition_key_bytes(company_id),
                value=_encode(wire),
            )
            await self._producer.flush()
        except Exception as exc:  # noqa: BLE001 — DLQ_FAILED, коммит запрещён
            raise KafkaAdapterError(
                f"DLQ publish failed for topic {self._dlq_topic!r}: {type(exc).__name__}"
            ) from exc
        self.published.append(dict(wire))


# Топики, обязательные при auto_create_topics_enabled=false.
REQUIRED_PLUGIN_TOPICS: Final[tuple[str, ...]] = (
    PLUGIN_EVENTS_TOPIC,
    dlq_topic(PLUGIN_EVENTS_TOPIC),
)

__all__ = [
    "MAX_DELIVERY_ATTEMPTS",
    "REQUIRED_PLUGIN_TOPICS",
    "AiokafkaConsumerAdapter",
    "AiokafkaProducerAdapter",
    "KafkaAdapterError",
    "KafkaConsumedRecord",
    "KafkaDlqAdapter",
    "KafkaTopicAdminAdapter",
    "consumer_settings_from_contract",
]
