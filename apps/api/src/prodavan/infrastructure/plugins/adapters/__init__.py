"""Реальные инфраструктурные адаптеры плагинов (F6/F7, задачи 10/11).

Этот пакет — «сторона инфраструктуры» стыков:

* :mod:`gateway_adapters` — 8 реализаций ``Protocol`` Plugin Gateway
  (``application/plugins/gateway/ports.py``, задача 11);
* :mod:`kafka_adapters` — aiokafka-реализации ``ProducerPort`` /
  ``ConsumerPort`` (poll/commit/seek) / ``TopicAdminPort`` / ``DlqPort``
  (``application/plugins/events/ports.py``, задача 10);
* :mod:`stores` — in-memory порты состояния ТОЛЬКО для dev/тестов
  (в кластере — БД/Redis, задача A10 родителя);
* :mod:`build` — fail-closed композиция ``PluginGatewayRuntime`` и
  ``PluginEventDispatcher``.

Ключевые решения (см. шапки модулей):

1. **Доверенный ``Authorization: Bearer <WakeResult.token>``** добавляется ЯВНО
   при проксировании: ``headers.py`` отбрасывает входящий ``Authorization`` и
   кладёт capability-токен в ``X-Plugin-Capability-Token``, а задачи 02/09
   ожидают JWT именно в ``Authorization``. Оба заголовка присутствуют.
2. **НЕ as-built ``KafkaManager``**: конфиг берётся из
   ``events/contracts.py::consumer_config()`` (``earliest`` +
   ``enable_auto_commit=False`` + ``dispatch``), а не из
   ``KAFKA_CONSUMER_MODE=kick``. Топик ``agentscale.plugin.events`` и ``.dlq``
   создаются ЯВНО (``auto_create_topics_enabled=false``, RF=1).
3. **Fail-closed**: отсутствие любого порта, секрета, Redis, grant'а, Ready,
   топика или аудита — явный отказ, а не молчаливое разрешение/деградация.

Импорты тяжёлых зависимостей (``aiokafka``, ``httpx``, ``redis.asyncio``,
``sqlalchemy``) делаются ЛЕНИВО внутри методов: пакет поднимается и без них,
а честный отказ возникает при старте конкретного адаптера. Это повторяет стиль
``core/infra/kafka_manager.py`` (импорт aiokafka внутри ``startup``/``_ensure_topics``).

Тесты написаны, но НЕ запускались (verification freeze миграции).
"""

from __future__ import annotations

from .build import (
    AdapterCompositionError,
    KafkaEventAdapters,
    build_event_dispatcher,
    build_gateway_runtime,
    build_kafka_event_adapters,
)
from .gateway_adapters import (
    HEADER_AUTHORIZATION,
    ActivationAdapter,
    AuditAdapter,
    BackendHttpAdapter,
    BearerAuthorizationInjector,
    GatewayAdapterError,
    GrantAdapter,
    GrantRecord,
    InMemoryCookieSessionStore,
    InMemoryGrantStore,
    InMemoryWebhookSecretRefs,
    MappingInstanceResolver,
    RedisRateLimitAdapter,
    SecretAdapter,
    SecretKeyRing,
    SessionAdapter,
    Sha256HmacAdapter,
    SqlEmployeeDirectory,
    with_trusted_bearer,
)
from .kafka_adapters import (
    REQUIRED_PLUGIN_TOPICS,
    AiokafkaConsumerAdapter,
    AiokafkaProducerAdapter,
    KafkaAdapterError,
    KafkaConsumedRecord,
    KafkaDlqAdapter,
    KafkaTopicAdminAdapter,
    consumer_settings_from_contract,
)
from .stores import (
    CLUSTER_BACKEND_NOTE,
    InMemoryAudit,
    InMemoryIdempotencyStore,
    InMemoryPublishedEventStore,
    InMemorySubscriptionStore,
    InMemoryWakeHook,
    event_instance_resolver,
    in_memory_event_ports,
)

__all__ = [
    # build (fail-closed композиция)
    "AdapterCompositionError",
    "KafkaEventAdapters",
    "build_event_dispatcher",
    "build_gateway_runtime",
    "build_kafka_event_adapters",
    # gateway adapters
    "HEADER_AUTHORIZATION",
    "ActivationAdapter",
    "AuditAdapter",
    "BackendHttpAdapter",
    "BearerAuthorizationInjector",
    "GatewayAdapterError",
    "GrantAdapter",
    "GrantRecord",
    "InMemoryCookieSessionStore",
    "InMemoryGrantStore",
    "InMemoryWebhookSecretRefs",
    "MappingInstanceResolver",
    "RedisRateLimitAdapter",
    "SecretAdapter",
    "SecretKeyRing",
    "SessionAdapter",
    "Sha256HmacAdapter",
    "SqlEmployeeDirectory",
    "with_trusted_bearer",
    # kafka adapters
    "REQUIRED_PLUGIN_TOPICS",
    "AiokafkaConsumerAdapter",
    "AiokafkaProducerAdapter",
    "KafkaAdapterError",
    "KafkaConsumedRecord",
    "KafkaDlqAdapter",
    "KafkaTopicAdminAdapter",
    "consumer_settings_from_contract",
    # in-memory stores (dev/тесты; кластер → БД/Redis, задача A10)
    "CLUSTER_BACKEND_NOTE",
    "InMemoryAudit",
    "InMemoryIdempotencyStore",
    "InMemoryPublishedEventStore",
    "InMemorySubscriptionStore",
    "InMemoryWakeHook",
    "event_instance_resolver",
    "in_memory_event_ports",
]
