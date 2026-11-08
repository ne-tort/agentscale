"""Имена топиков, ключи партиций и подготовка топиков (F7, задача 10).

As-built Redpanda (``infra/k3s/base/platform/kafka.yaml``) работает с
``auto_create_topics_enabled=false``: init-Job создаёт фиксированный список
платформенных топиков, а всё остальное обязано быть создано ЯВНО. Поэтому публикация событий плагинов без вызова :func:`ensure_topic` — это отказ, а не
«Redpanda сам создаст»: ``PluginEventDispatcher`` вызывает ``ensure_topic``
для основного топика и для DLQ до первой отправки (fail-closed).

Почему отдельный dispatch, а не as-built ``KafkaManager``: в
``core/infra/kafka_manager.py`` консьюмер проекта поднят с
``KAFKA_CONSUMER_MODE=kick`` + ``enable_auto_commit=True`` +
``auto_offset_reset="latest"`` — это «будильник», который коммитит offset до
обработки и при простое консьюмера теряет события (§7.3, §13.3, criterion 20).
Здесь зафиксированы обратные настройки: ``earliest`` + ручной ``commit``.

Celery-outbox ``webhook_deliveries`` НЕ используется как шина событий: он
остаётся только для исходящих вебхуков (``application/external/webhooks.py``).
В этом модуле и во всей зоне ``events`` запрещены импорты outbox-моделей —
проверяется отдельным unit-тестом.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .errors import PluginEventError, PluginEventErrorCode

if TYPE_CHECKING:  # только для аннотаций: не создаём цикл ports -> topics -> ports
    from .ports import TopicAdminPort

# Каноническая шина событий плагинов (§7.3 дизайна v4, acceptance №20).
# Один топик + N consumer group по подпискам; ключ партиции = company_id.
PLUGIN_EVENTS_TOPIC = "agentscale.plugin.events"

# Суффикс топика мёртвых сообщений. DLQ создаётся тем же администратором,
# что и основной топик: auto_create_topics_enabled=false (kafka.yaml:221).
PLUGIN_DLQ_SUFFIX = ".dlq"

# RF=1: single-node Redpanda (StatefulSet replicas: 1 в kafka.yaml). Любое
# значение > 1 приведёт к under-replicated топику и остановке продюсера с
# acks=all. Это известный риск, НЕ исправляемый в рамках задачи 10.
DEFAULT_NUM_PARTITIONS = 1
DEFAULT_REPLICATION_FACTOR = 1

# Допустимые символы имени топика Kafka (без пробелов и управляющих символов).
_TOPIC_RE = re.compile(r"^[A-Za-z0-9._-]{1,249}$")
# Идентификатор плагина внутри имени топика: только безопасные символы.
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

__all__ = [
    "DEFAULT_NUM_PARTITIONS",
    "DEFAULT_REPLICATION_FACTOR",
    "PLUGIN_DLQ_SUFFIX",
    "PLUGIN_EVENTS_TOPIC",
    "dlq_topic",
    "dlq_topic_for",
    "ensure_dlq_topic",
    "ensure_topic",
    "partition_key",
    "partition_key_bytes",
    "topic_for",
]


def _require_topic(topic: str, name: str = "topic") -> str:
    """Fail-closed: имя топика должно быть валидным и не содержать инъекций."""

    if not isinstance(topic, str) or not topic.strip():
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            f"{name} обязано быть непустой строкой",
        )
    if _TOPIC_RE.fullmatch(topic) is None:
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            f"{name} содержит недопустимые символы или слишком длинное",
        )
    return topic


def topic_for(plugin_id: str | None = None) -> str:
    """Имя топика событий для плагина.

    * ``plugin_id is None`` → каноническая общая шина :data:`PLUGIN_EVENTS_TOPIC`
      (именно она описана в §7.3: один топик, ключ ``company_id``, N consumer
      group по подпискам);
    * ``plugin_id`` задан → выделенный топик ``<base>.<plugin_id>``. Он нужен,
      когда «шумный» плагин выносят в отдельный топик со своим retention,
      чтобы он не вытеснял события соседей (риск квот из §10).

    fail-closed: пустой/некорректный ``plugin_id`` — ошибка, а не подстановка
    значения по умолчанию. Имя всегда валидно для Kafka и для ``ensure_topic``.
    """

    if plugin_id is None:
        return PLUGIN_EVENTS_TOPIC
    if not isinstance(plugin_id, str) or _PLUGIN_ID_RE.fullmatch(plugin_id) is None:
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "plugin_id обязан быть непустым идентификатором [a-z0-9._-]",
        )
    return _require_topic(f"{PLUGIN_EVENTS_TOPIC}.{plugin_id}")


def dlq_topic(topic: str) -> str:
    """Имя DLQ-топика для данного топика событий.

    fail-closed: двойной суффикс ``.dlq.dlq`` запрещён — иначе DLQ-сообщение
    при ошибке обработки снова уйдёт в DLQ и образуется цикл.
    """

    base = _require_topic(topic)
    if base.endswith(PLUGIN_DLQ_SUFFIX):
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "DLQ-топик не может быть использован как источник событий",
        )
    return _require_topic(f"{base}{PLUGIN_DLQ_SUFFIX}")


def dlq_topic_for(plugin_id: str | None = None) -> str:
    """DLQ для топика плагина (сокращение для ``dlq_topic(topic_for(...))``)."""

    return dlq_topic(topic_for(plugin_id))


def partition_key(company_id: str) -> str:
    """Ключ партиции = ``company_id`` (§7.3).

    Возвращает строку как есть: адаптер ``ProducerPort`` кодирует её в UTF-8
    (см. :func:`partition_key_bytes`). Никакого хэширования URL, тела или
    ``event_id``: все события компании должны идти в одну партицию, сохраняя
    порядок и не давая «шумной» компании перемешать чужие потоки.

    fail-closed: пустой ``company_id`` — ошибка, а не пустой ключ (пустой ключ
    в Kafka допустим, но ломает гарантию привязки к компании).
    """

    if not isinstance(company_id, str) or not company_id.strip():
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "company_id обязан быть непустой строкой — ключом партиции",
        )
    return company_id


def partition_key_bytes(company_id: str) -> bytes:
    """Тот же ключ в wire-формате Kafka (UTF-8) для адаптеров продюсера."""

    return partition_key(company_id).encode("utf-8")


async def ensure_topic(
    admin: TopicAdminPort,
    topic: str,
    *,
    num_partitions: int = DEFAULT_NUM_PARTITIONS,
    replication_factor: int = DEFAULT_REPLICATION_FACTOR,
) -> str:
    """Гарантировать существование топика через порт ``TopicAdminPort``.

    ОБЯЗАТЕЛЬНЫЙ вызов перед публикацией/подпиской: в кластере выставлен
    ``auto_create_topics_enabled=false`` (``kafka.yaml:221``), поэтому топик
    ``agentscale.plugin.events`` и его DLQ не появятся «сами» при первом
    produce. As-built ``KafkaManager._ensure_topics`` делает это best-effort и
    при ошибке молча надеется на auto-create (``kafka_manager.py:606-608``) —
    для плагинов такой режим недопустим.

    Алгоритм (fail-closed):
      1. валидируем имя и параметры;
      2. ``admin.topic_exists`` → если нет, ``admin.create_topic``;
      3. ПОВТОРНО проверяем существование. Нет топика → ``TOPIC_NOT_ENSURED``.

    Никаких ``try/except ... return``: отсутствие администратора или ошибка
    создания пробрасываются как ``MISSING_DEPENDENCY`` / ``TOPIC_NOT_ENSURED``,
    и диспечер не начинает публикацию.
    """

    name = _require_topic(topic)
    if admin is None:
        raise PluginEventError(
            PluginEventErrorCode.MISSING_DEPENDENCY,
            "TopicAdminPort обязателен: auto_create_topics_enabled=false",
        )
    if not isinstance(num_partitions, int) or isinstance(num_partitions, bool) or num_partitions < 1:
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "num_partitions должен быть положительным целым",
        )
    if (
        not isinstance(replication_factor, int)
        or isinstance(replication_factor, bool)
        or replication_factor < 1
    ):
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "replication_factor должен быть положительным целым",
        )
    if replication_factor != DEFAULT_REPLICATION_FACTOR:
        # RF>1 на single-node Redpanda даёт under-replicated топик и останавливает
        # продюсер с acks=all. Явный отказ вместо тихого деградирования.
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "RF=1 single-node Redpanda: replication_factor>1 недопустим",
        )

    try:
        exists = await admin.topic_exists(name)
    except PluginEventError:
        raise
    except Exception as exc:
        raise PluginEventError(
            PluginEventErrorCode.TOPIC_NOT_ENSURED,
            f"не удалось проверить топик {name!r}: {type(exc).__name__}",
        ) from exc

    if not exists:
        try:
            await admin.create_topic(
                name,
                num_partitions=num_partitions,
                replication_factor=replication_factor,
            )
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.TOPIC_NOT_ENSURED,
                f"не удалось создать топик {name!r}: {type(exc).__name__}",
            ) from exc

        # Повторная проверка: create_topic мог «успешно» ничего не сделать
        # (гонка/стабы/очередь админ-операций). Не верим на слово — fail-closed.
        try:
            exists = await admin.topic_exists(name)
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.TOPIC_NOT_ENSURED,
                f"не удалось подтвердить топик {name!r}: {type(exc).__name__}",
            ) from exc

    if not exists:
        raise PluginEventError(
            PluginEventErrorCode.TOPIC_NOT_ENSURED,
            f"топик {name!r} отсутствует после create_topic; публикация запрещена",
        )
    return name


async def ensure_dlq_topic(admin: TopicAdminPort, topic: str) -> str:
    """Подготовить DLQ-топик для данного топика событий (обязательно)."""

    return await ensure_topic(admin, dlq_topic(topic))
