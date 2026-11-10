"""Композиция реальных адаптеров: Gateway Runtime и Event Dispatcher.

Две точки сборки (интегратор / родитель, F11):

* :func:`build_gateway_runtime` → ``PluginGatewayRuntime``
  (``api/v1/plugin_gateway.py``, задача 11);
* :func:`build_event_dispatcher` → ``PluginEventDispatcher``
  (``application/plugins/events/dispatch.py``, задача 10).

ГЛАВНОЕ ПРАВИЛО — **fail-closed при отсутствии ЛЮБОГО порта**
------------------------------------------------------------
Обе функции принимают адаптеры ЯВНО и проверяют каждый из них ДО создания
рантайма. ``None`` (= «интеграцию забыли подключить») даёт
:class:`AdapterCompositionError`, а не «деградацию до разрешения»:

* ``GatewayAuth.__post_init__`` не проверил бы ``None`` у части портов, если бы
  композитор подставил in-memory заглушку «чтобы собралось»;
* ``PluginEventDispatcher`` отложил бы ``MISSING_DEPENDENCY`` до первого
  ``publish()``/``consume()`` — здесь отказ происходит на старте.

Ни один in-memory адаптер из :mod:`stores.py` НЕ подставляется молча: они
помечены как dev/тестовые и требуют ЯВНОЙ передачи (в кластере — БД/Redis,
задача A10 родителя).

КЛЮЧЕВОЙ ФИКС СТЫКА 02/09/11
-----------------------------
:func:`build_gateway_runtime` автоматически оборачивает ``BackendHttpPort`` в
:class:`~gateway_adapters.BearerAuthorizationInjector` (если сам адаптер ещё не
ставит доверенный Bearer). Поэтому при проксировании присутствуют ОБА заголовка:
``X-Plugin-Capability-Token`` и ``Authorization: Bearer <WakeResult.token>``.
Входящий клиентский ``Authorization``/``Cookie`` по-прежнему отбрасывается.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prodavan.api.v1.plugin_gateway import PluginGatewayRuntime
from prodavan.application.plugins.events.dispatch import PluginEventDispatcher
from prodavan.application.plugins.gateway.auth import GatewayAuth, GatewayLimits
from prodavan.application.plugins.gateway.proxy import GatewayProxy
from prodavan.application.plugins.gateway.ports import (
    ANON_SCOPES,
    AuditPort,
    BackendHttpPort,
    HmacPort,
    RateLimitPort,
    SESSION_SCOPES,
    WEBHOOK_SCOPES,
    ActivationPort,
    GrantPort,
    SecretPort,
    SessionPort,
    StaticResponsePolicy,
)

from .gateway_adapters import BackendHttpAdapter, BearerAuthorizationInjector
from .kafka_adapters import (
    AiokafkaConsumerAdapter,
    AiokafkaProducerAdapter,
    KafkaDlqAdapter,
    KafkaTopicAdminAdapter,
)
from .stores import CLUSTER_BACKEND_NOTE

__all__ = [
    "ANON_SCOPES",
    "CLUSTER_BACKEND_NOTE",
    "SESSION_SCOPES",
    "WEBHOOK_SCOPES",
    "AdapterCompositionError",
    "KafkaEventAdapters",
    "build_event_dispatcher",
    "build_gateway_runtime",
    "build_kafka_event_adapters",
]


class AdapterCompositionError(RuntimeError):
    """Ошибка композиции: обязательный порт не подключён (fail-closed).

    Наследник ``RuntimeError``: это ошибка СТАРТА приложения. Родитель обязан
    уронить startup, а не «продолжить без плагинов» (§0.5 плана миграции).
    """

    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            "обязательные порты не подключены (fail-closed, молчаливая "
            f"деградация запрещена): {', '.join(missing)}"
        )


def _require_all(provided: dict[str, Any]) -> None:
    """Проверить, что НИ ОДИН обязательный порт не ``None``."""
    missing = tuple(sorted(name for name, value in provided.items() if value is None))
    if missing:
        raise AdapterCompositionError(missing)


def _protocol_shape_ok(value: Any, *methods: str) -> bool:
    """Структурная проверка: у адаптера есть все нужные callable-методы."""
    return all(callable(getattr(value, name, None)) for name in methods)


# ---------------------------------------------------------------------------
# Plugin Gateway
# ---------------------------------------------------------------------------


def build_gateway_runtime(
    *,
    sessions: SessionPort | None = None,
    grants: GrantPort | None = None,
    secrets: SecretPort | None = None,
    rate_limit: RateLimitPort | None = None,
    audit: AuditPort | None = None,
    hmac_verifier: HmacPort | None = None,
    activation: ActivationPort | None = None,
    backend_http: BackendHttpPort | None = None,
    static_http: BackendHttpPort | None = None,
    limits: GatewayLimits | None = None,
    shell_origin: str | None = None,
    inject_trusted_bearer: bool = True,
) -> PluginGatewayRuntime:
    """Собрать ``PluginGatewayRuntime`` из 9 адаптеров + лимитов.

    Обязательны ВСЕ порты (включая ``static_http``: отдельный static-origin —
    часть контракта §5.2/§7.2, и ``GatewayProxy.forward_static`` всё равно
    отказал бы на первом же запросе, но fail-closed на старте честнее).

    ``inject_trusted_bearer=True`` (по умолчанию) гарантирует доверенный
    ``Authorization: Bearer <WakeResult.token>`` при проксировании: если
    переданный ``backend_http`` сам не ставит Bearer
    (``BackendHttpAdapter.inject_trusted_bearer is False``), он оборачивается в
    :class:`BearerAuthorizationInjector`. Отключать флаг можно только когда
    интегратор ЯВНО обеспечивает оба доверенных заголовка иным способом.
    """
    _require_all(
        {
            "SessionPort": sessions,
            "GrantPort": grants,
            "SecretPort": secrets,
            "RateLimitPort": rate_limit,
            "AuditPort": audit,
            "HmacPort": hmac_verifier,
            "ActivationPort": activation,
            "BackendHttpPort": backend_http,
            "static_http": static_http,
            "GatewayLimits": limits,
            "PLUGIN_SHELL_ORIGIN": shell_origin,
        }
    )

    # Структурная проверка контрактов: «объект есть, а метода нет» — та же
    # ошибка композиции, что и None. Ловим её здесь, а не на первом запросе.
    shapes = (
        (sessions, "SessionPort", ("resolve", "employee_in_company")),
        (grants, "GrantPort", ("active_grant", "public_pages")),
        (secrets, "SecretPort", ("webhook_secret",)),
        (
            rate_limit,
            "RateLimitPort",
            ("allow", "claim_delivery_lease", "commit_delivery", "release_delivery"),
        ),
        (audit, "AuditPort", ("record",)),
        (hmac_verifier, "HmacPort", ("verify",)),
        (activation, "ActivationPort", ("wake", "wait_ready")),
        (backend_http, "BackendHttpPort", ("send",)),
        (static_http, "static_http", ("send",)),
    )
    broken = tuple(name for value, name, methods in shapes if not _protocol_shape_ok(value, *methods))
    if broken:
        raise AdapterCompositionError(tuple(f"{name}: не реализует контракт" for name in broken))

    effective_backend: Any = backend_http
    already_injects = bool(
        getattr(backend_http, "inject_trusted_bearer", False)
        if isinstance(backend_http, BackendHttpAdapter)
        else isinstance(backend_http, BearerAuthorizationInjector)
    )
    if inject_trusted_bearer and not already_injects:
        # ЯВНО добавляем Authorization: Bearer <WakeResult.token> к доверенным
        # заголовкам. X-Plugin-Capability-Token остаётся (оба заголовка).
        effective_backend = BearerAuthorizationInjector(backend_http)  # type: ignore[arg-type]

    auth = GatewayAuth(
        sessions=sessions,  # type: ignore[arg-type]
        grants=grants,  # type: ignore[arg-type]
        secrets=secrets,  # type: ignore[arg-type]
        rate_limit=rate_limit,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        hmac_verifier=hmac_verifier,  # type: ignore[arg-type]
        limits=limits,  # type: ignore[arg-type]
    )
    proxy = GatewayProxy(
        activation=activation,  # type: ignore[arg-type]
        backend_http=effective_backend,
        static_http=static_http,
        audit=audit,  # type: ignore[arg-type]
        limits=limits,  # type: ignore[arg-type]
        static_response_policy=StaticResponsePolicy(
            shell_origin=shell_origin or ""
        ),
    )
    return PluginGatewayRuntime(auth=auth, proxy=proxy)


# ---------------------------------------------------------------------------
# Redpanda dispatch
# ---------------------------------------------------------------------------


def build_event_dispatcher(
    *,
    producer: Any | None = None,
    consumer: Any | None = None,
    topic_admin: Any | None = None,
    dlq: Any | None = None,
    raw_dlq: Any | None = None,
    subscriptions: Any | None = None,
    published_events: Any | None = None,
    idempotency: Any | None = None,
    delivery_claims: Any | None = None,
    audit: Any | None = None,
    handler: Any | None = None,
    wake_hook: Any | None = None,
    topic: str = "agentscale.plugin.events",
) -> PluginEventDispatcher:
    """Собрать ``PluginEventDispatcher`` со ВСЕМИ 10 портами (fail-closed).

    ``PluginEventDispatcher`` сам проверяет порты при ``_require``, но только
    **в момент вызова** ``publish``/``consume``. Здесь проверка перенесена на
    старт: отсутствие любого адаптера = :class:`AdapterCompositionError`.

    In-memory порты из :mod:`stores.py` НЕ подставляются автоматически —
    в кластере это БД/Redis (задача A10).
    """
    _require_all(
        {
            "ProducerPort": producer,
            "ConsumerPort": consumer,
            "TopicAdminPort": topic_admin,
            "DlqPort": dlq,
            "RawDlqPort": raw_dlq,
            "SubscriptionStorePort": subscriptions,
            "PublishedEventStorePort": published_events,
            "IdempotencyStorePort": idempotency,
            "DeliveryClaimPort": delivery_claims,
            "AuditPort": audit,
            "EventHandlerPort": handler,
            "WakeHookPort": wake_hook,
        }
    )
    shapes = (
        (producer, "ProducerPort", ("send", "flush")),
        (consumer, "ConsumerPort", ("poll", "commit", "seek", "close", "delivery_ownership")),
        (topic_admin, "TopicAdminPort", ("topic_exists", "create_topic")),
        (dlq, "DlqPort", ("publish",)),
        (raw_dlq, "RawDlqPort", ("publish",)),
        (subscriptions, "SubscriptionStorePort", ("get", "is_active")),
        (published_events, "PublishedEventStorePort", ("seen", "remember")),
        (idempotency, "IdempotencyStorePort", ("seen", "remember")),
        (delivery_claims, "DeliveryClaimPort", ("acquire", "renew", "complete", "release")),
        (audit, "AuditPort", ("append",)),
        (handler, "EventHandlerPort", ("handle",)),
    )
    broken = tuple(name for value, name, methods in shapes if not _protocol_shape_ok(value, *methods))
    wake_ok = _protocol_shape_ok(wake_hook, "wake") or callable(wake_hook)
    if broken or not wake_ok:
        names = broken if broken else ()
        raise AdapterCompositionError(
            tuple(f"{name}: не реализует контракт" for name in names)
            + (() if wake_ok else ("WakeHookPort: не реализует контракт",))
        )

    return PluginEventDispatcher(
        producer=producer,
        consumer=consumer,
        topic_admin=topic_admin,
        dlq=dlq,
        raw_dlq=raw_dlq,
        subscriptions=subscriptions,
        published_events=published_events,
        idempotency=idempotency,
        delivery_claims=delivery_claims,
        audit=audit,
        handler=handler,
        wake_hook=wake_hook,
        topic=topic,
    )


@dataclass(slots=True)
class KafkaEventAdapters:
    """Живые Kafka-адаптеры, готовые к передаче в ``build_event_dispatcher``.

    Владеет одним ``AIOKafkaProducer`` (общим для событий и DLQ) и lifecycle-
    методами :meth:`start` / :meth:`close`. Хранилища состояния (подписки,
    идемпотентность, аудит, handler, wake_hook) сюда НЕ входят: их обязан
    передать интегратор (в кластере — БД/Redis, задача A10).
    """

    producer: AiokafkaProducerAdapter
    consumer: AiokafkaConsumerAdapter
    topic_admin: KafkaTopicAdminAdapter
    dlq: KafkaDlqAdapter
    raw_dlq: Any
    delivery_claims: Any
    raw_producer: Any = None

    def as_ports(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "consumer": self.consumer,
            "topic_admin": self.topic_admin,
            "dlq": self.dlq,
            "raw_dlq": self.raw_dlq,
            "delivery_claims": self.delivery_claims,
        }

    async def close(self) -> None:
        """Остановить консьюмера, продюсер и админ-клиент (без коммита offset'ов)."""
        await self.consumer.close()
        # raw_producer создан ЗДЕСЬ (передан в адаптер как чужой), поэтому
        # закрываем именно его: AiokafkaProducerAdapter.stop() не владеет им.
        if self.raw_producer is not None:
            try:
                await self.raw_producer.stop()
            except Exception:  # noqa: BLE001 — остановка не маскирует отказ
                pass
        self.producer._producer = None  # снять ссылку на остановленный клиент
        await self.topic_admin.close()


async def build_kafka_event_adapters(
    *,
    bootstrap_servers: str,
    subscription: Any,
    topic: str = "agentscale.plugin.events",
    producer: Any = None,
    consumer: Any = None,
    admin: Any = None,
    raw_dlq: Any = None,
    delivery_claims: Any = None,
) -> KafkaEventAdapters:
    """Поднять реальные aiokafka-адаптеры и **обязательно** подготовить топики.

    async-обёртка над ``build_event_dispatcher`` для родителя/F11:

    1. ``KafkaTopicAdminAdapter.start()``;
    2. ``ensure_plugin_topics()`` → основной топик ``agentscale.plugin.events``
       и ``.dlq`` через ``TopicAdminPort`` — **обязательно**, т.к.
       ``auto_create_topics_enabled=false`` (``kafka.yaml:221``);
    3. старт ``AIOKafkaProducer`` (``acks=all``, ``enable_idempotence``) и
       ``AIOKafkaConsumer`` с конфигом ``consumer_config()`` задачи 10
       (``earliest`` + ручной commit, ``group_id=subscription.consumer_group``);
    4. общий продюсер для событий и DLQ.

    Любая ошибка (брокер недоступен, топик не создан) пробрасывается: сборка
    НЕ возвращает «частично рабочий» dispatcher (fail-closed).
    """
    if not isinstance(bootstrap_servers, str) or not bootstrap_servers.strip():
        raise AdapterCompositionError(("bootstrap_servers",))
    if subscription is None or not isinstance(
        getattr(subscription, "consumer_group", None), str
    ):
        raise AdapterCompositionError(("subscription.consumer_group",))
    if raw_dlq is None:
        raise AdapterCompositionError(("RawDlqPort",))
    if delivery_claims is None:
        raise AdapterCompositionError(("DeliveryClaimPort",))
    if not _protocol_shape_ok(raw_dlq, "publish"):
        raise AdapterCompositionError(("RawDlqPort: не реализует контракт",))
    if not _protocol_shape_ok(delivery_claims, "acquire", "renew", "complete", "release"):
        raise AdapterCompositionError(("DeliveryClaimPort: не реализует контракт",))

    admin_adapter = KafkaTopicAdminAdapter(
        bootstrap_servers=bootstrap_servers, admin=admin
    )
    await admin_adapter.start()
    # ensure_topic ОБЯЗАТЕЛЕН: Redpanda не создаст топик при первом produce.
    await admin_adapter.ensure_plugin_topics(topic)

    raw_producer = producer
    owns_producer = raw_producer is None
    if raw_producer is None:
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:
            raise AdapterCompositionError(("aiokafka.AIOKafkaProducer",)) from exc
        raw_producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers.strip(),
            client_id="prodavan-plugin-events-producer",
            acks="all",
            enable_idempotence=True,
        )
        try:
            await raw_producer.start()
        except Exception as exc:  # noqa: BLE001 — fail-closed
            raise AdapterCompositionError((f"AIOKafkaProducer.start: {type(exc).__name__}",)) from exc

    producer_adapter = AiokafkaProducerAdapter(
        bootstrap_servers=bootstrap_servers, producer=raw_producer
    )
    consumer_adapter = AiokafkaConsumerAdapter(
        bootstrap_servers=bootstrap_servers,
        subscription=subscription,
        topic=topic,
        consumer=consumer,
    )
    await consumer_adapter.start()
    dlq_adapter = KafkaDlqAdapter(producer=raw_producer, topic=topic)
    return KafkaEventAdapters(
        producer=producer_adapter,
        consumer=consumer_adapter,
        topic_admin=admin_adapter,
        dlq=dlq_adapter,
        raw_dlq=raw_dlq,
        delivery_claims=delivery_claims,
        raw_producer=raw_producer if owns_producer else None,
    )
