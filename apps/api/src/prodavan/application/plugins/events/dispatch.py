"""PluginEventDispatcher — доставка событий плагинов без потерь (F7, задача 10).

Реализует «Разделение транспортов» из §7.3 дизайна v4 и acceptance №20 (§11):

* ``publish`` — продюсер с ключом партиции ``company_id``, идемпотентность по
  ``event_id``, обязательная подготовка топика и DLQ через ``TopicAdminPort``
  (в кластере ``auto_create_topics_enabled=false``, ``kafka.yaml:221``);
* ``consume`` — ``auto_offset_reset="earliest"`` + ``enable_auto_commit=False``
  (зафиксировано в :func:`contracts.consumer_config`), коммит offset'а ТОЛЬКО
  после успеха; ошибка → до 3 ретраев → DLQ → коммит. Никакого ``kick``-режима
  и ``auto_commit``: as-built ``KafkaManager`` теряет события при простое
  консьюмера (``kafka_manager.py:695-696``, §13.3);
* ``wake_hook`` — колбэк, который будит backend плагина ДО доставки (задача 09);
* идемпотентность консьюмера по ``(subscription_id, source_event_id)``, а НЕ
  по URL/hash тела;
* аудит plugin_event_received / plugin_event_dlq без raw payload;
* malformed raw record → отдельный RawDlqRecord с digest и source-координатами,
  без фиктивного PluginEvent; raw DLQ подтверждён/dedup'нут до offset commit.

Fail-closed: отсутствие любого порта/адаптера, ошибка wake, аудита, DLQ,
хранилища или коммита — НЕ приводит к коммиту offset'а. Позиция возвращается
через ``seek``, событие будет прочитано снова (``earliest`` + ручной commit).

Celery-outbox ``webhook_deliveries`` НЕ импортируется и НЕ используется как
шина: он остаётся только для исходящих вебхуков (``application/external``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .contracts import (
    AUDIT_PLUGIN_EVENT_DLQ,
    AUDIT_PLUGIN_EVENT_RECEIVED,
    MAX_DELIVERY_ATTEMPTS,
    ConsumerAssignment,
    DeliveryAttempt,
    DeliveryClaim,
    DeliveryClaimError,
    DeliveryOutcome,
    DeliveryOutcomeUncertainError,
    DlqRecord,
    IdempotencyKey,
    PluginEvent,
    RawDlqRecord,
    Subscription,
    backend_idempotency_key,
    consumer_config,
    delivery_outcome_for_attempt,
)
from .errors import PluginEventError, PluginEventErrorCode
from .ports import (
    AuditPort,
    ConsumedRecord,
    ConsumerPort,
    DeliveryClaimPort,
    DlqPort,
    EventHandlerPort,
    IdempotencyStorePort,
    ProducerPort,
    PublishedEventStorePort,
    RawDlqPort,
    SubscriptionStorePort,
    TopicAdminPort,
    WakeHookPort,
)
from .topics import (
    PLUGIN_EVENTS_TOPIC,
    dlq_topic,
    ensure_dlq_topic,
    ensure_topic,
    partition_key,
)

logger = logging.getLogger(__name__)

# wake_hook может быть как объектом WakeHookPort, так и обычным async-колбэком
# (задача 09 ещё не создана — не завязываемся на конкретный класс).
WakeHook = WakeHookPort | Callable[[PluginEvent, Subscription], Awaitable[None]]

EVENT_SIGNATURE_FIELD = "event_signature"
EVENT_SIGNATURE_VERSION_FIELD = "event_signature_version"
EVENT_SIGNATURE_VERSION = "hmac-sha256-v1"


def _event_signature_bytes(event: PluginEvent, *, topic: str) -> bytes:
    """Domain-separated canonical bytes; the topic is part of the signature."""

    if not isinstance(topic, str) or not topic.strip():
        raise PluginEventError(
            PluginEventErrorCode.INVALID_CONTRACT,
            "signed event topic must be non-empty",
        )
    wire = json.dumps(
        event.to_wire(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    encoded_topic = topic.encode("utf-8")
    return (
        b"agentscale.plugin.event.v1\x00"
        + len(encoded_topic).to_bytes(4, "big")
        + encoded_topic
        + wire
    )


def _valid_signature(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256=")
        and len(value) == len("sha256=") + 64
        and all(char in "0123456789abcdef" for char in value[7:])
    )


class HmacPluginEventSigner:
    """HMAC-SHA256 using only an explicit SecretStore reference.

    No plaintext environment variable, claims-key reuse, direct credential-file
    read or default development key is accepted. Kubernetes secrets are resolved
    only through the read-only SecretStore. Resolution failures are configuration
    failures, not poison events and never become an ACK/DLQ.
    """

    def __init__(self, *, secret_store: Any, secret_ref: str) -> None:
        if not callable(getattr(secret_store, "get", None)):
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                "event signature SecretStore is required",
            )
        k8s_parts = (
            secret_ref[len("k8s-secret://"):].split("/")
            if isinstance(secret_ref, str) and secret_ref.startswith("k8s-secret://")
            else []
        )
        valid_scheme = (
            isinstance(secret_ref, str)
            and (
                (
                    secret_ref.startswith(("vault://", "file://"))
                    and bool(secret_ref.split("://", 1)[1])
                )
                or (len(k8s_parts) == 2 and all(k8s_parts))
            )
        )
        if (
            not valid_scheme
            or len(secret_ref) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in secret_ref)
        ):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "event signature secret must be an explicit SecretStore reference",
            )
        self._store = secret_store
        self._ref = secret_ref

    def _key(self) -> bytes:
        try:
            value = self._store.get(self._ref)
        except Exception:
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                "event signature secret resolution failed",
            ) from None
        if not isinstance(value, str) or len(value.encode("utf-8")) < 32:
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                "event signature secret is invalid",
            )
        return value.encode("utf-8")

    def validate(self) -> None:
        """Resolve and validate the configured key at composition time."""

        self._key()

    def sign(self, event: PluginEvent, *, topic: str) -> str:
        digest = hmac.new(
            self._key(), _event_signature_bytes(event, topic=topic), hashlib.sha256
        ).hexdigest()
        return "sha256=" + digest

    def verify(self, event: PluginEvent, *, topic: str, signature: str) -> bool:
        if not _valid_signature(signature):
            return False
        expected = self.sign(event, topic=topic)
        return hmac.compare_digest(expected, signature)

    def signed_wire(self, event: PluginEvent, *, topic: str) -> dict[str, Any]:
        return {
            **event.to_wire(),
            EVENT_SIGNATURE_VERSION_FIELD: EVENT_SIGNATURE_VERSION,
            EVENT_SIGNATURE_FIELD: self.sign(event, topic=topic),
        }


class SignedEventHandler:
    """Attach the required bus signer to an explicitly supplied event handler."""

    def __init__(self, *, handler: EventHandlerPort, signer: Any) -> None:
        if not callable(getattr(handler, "handle", None)):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "event handler must implement handle()",
            )
        if not all(callable(getattr(signer, method, None)) for method in ("sign", "verify")):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "event signer must implement sign() and verify()",
            )
        self._handler = handler
        self.event_signer = signer

    async def handle(self, event: PluginEvent, *, subscription: Subscription) -> None:
        await self._handler.handle(event, subscription=subscription)


@dataclass(frozen=True, slots=True)
class _EventBinding:
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    artifact_digest: str
    grant_id: str


@dataclass(frozen=True, slots=True)
class _PendingDelivery:
    event_id: str
    subscription_id: str
    binding: _EventBinding
    wake_result: Any


class ActivatingPluginEventHandler:
    """One exact wake+deliver unit shared as event_handler and event_wake_hook.

    The durable grant binds company/plugin/instance/artifact digest. Subscription
    identity and the claims returned by the real PluginActivator are checked
    independently. The handler only consumes the wake result produced earlier
    in the same dispatcher attempt; it never skips wake or creates a worker loop.
    """

    def __init__(
        self,
        *,
        activator: Any,
        proxy: Any,
        grant_store: Any,
        signer: Any,
        topic: str = PLUGIN_EVENTS_TOPIC,
        timeout_seconds: float = 30.0,
        wake_timeout: float | None = None,
    ) -> None:
        if not callable(getattr(activator, "wake", None)):
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                "PluginActivator.wake is required",
            )
        if not callable(getattr(proxy, "forward", None)):
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                "plugin backend ProxyPort.forward is required",
            )
        if not callable(getattr(grant_store, "get", None)):
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                "durable grant lookup is required for event wake binding",
            )
        if not all(callable(getattr(signer, method, None)) for method in ("sign", "verify")):
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                "event signer is required",
            )
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "event backend timeout must be positive",
            )
        self._activator = activator
        self._proxy = proxy
        self._grants = grant_store
        self.event_signer = signer
        self._topic = topic
        self._timeout_seconds = float(timeout_seconds)
        self._wake_timeout = wake_timeout
        self._pending: ContextVar[_PendingDelivery | None] = ContextVar(
            f"plugin_event_delivery_{id(self)}", default=None
        )

    async def _binding(
        self, event: PluginEvent, subscription: Subscription
    ) -> _EventBinding:
        plugin_id = event.plugin_id or subscription.plugin_id
        if event.plugin_id is not None and event.plugin_id != subscription.plugin_id:
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                "event target plugin differs from subscription",
            )
        try:
            grant = await self._grants.get(
                company_id=event.company_id, plugin_id=plugin_id
            )
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                f"event grant lookup failed: {type(exc).__name__}",
            ) from None
        if (
            grant is None
            or not bool(getattr(grant, "is_active", False))
            or getattr(grant, "revoked_at", None) is not None
            or getattr(grant, "company_id", None) != event.company_id
            or getattr(grant, "plugin_id", None) != plugin_id
            or subscription.plugin_id != plugin_id
        ):
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                "event company/plugin grant binding mismatch",
            )
        instance = str(getattr(grant, "plugin_instance_id", "") or "")
        digest = str(getattr(grant, "artifact_digest", "") or "")
        grant_id = str(getattr(grant, "grant_id", "") or "")
        subscription_prefix = f"{event.company_id}:{instance}:"
        group_prefix = (
            f"tenant:{event.company_id}:plugin:{plugin_id}:{instance}:group:"
        )
        if (
            not instance
            or len(digest) != 71
            or not digest.startswith("sha256:")
            or not grant_id
            or not subscription.id.startswith(subscription_prefix)
            or len(subscription.id) == len(subscription_prefix)
            or not subscription.consumer_group.startswith(group_prefix)
            or len(subscription.consumer_group) == len(group_prefix)
        ):
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                "subscription company/plugin/instance binding mismatch",
            )
        return _EventBinding(
            company_id=event.company_id,
            plugin_id=plugin_id,
            plugin_instance_id=instance,
            artifact_digest=digest,
            grant_id=grant_id,
        )

    @staticmethod
    def _verify_wake_identity(binding: _EventBinding, result: Any) -> None:
        claims = getattr(result, "claims", None)
        expected_subject = (
            f"plugin:{binding.plugin_id}:{binding.company_id}:"
            f"{binding.plugin_instance_id}"
        )
        expected = {
            "company_id": binding.company_id,
            "plugin_id": binding.plugin_id,
            "plugin_instance_id": binding.plugin_instance_id,
            "artifact_digest": binding.artifact_digest,
            "grant_id": binding.grant_id,
            "sub": expected_subject,
        }
        if (
            getattr(result, "plugin_instance_id", None) != binding.plugin_instance_id
            or not isinstance(claims, Mapping)
            or any(claims.get(name) != value for name, value in expected.items())
        ):
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                "PluginActivator wake identity or artifact digest mismatch",
            )

    async def wake(self, event: PluginEvent, *, subscription: Subscription) -> None:
        binding = await self._binding(event, subscription)
        result = await self._activator.wake(
            binding.plugin_instance_id, timeout=self._wake_timeout
        )
        self._verify_wake_identity(binding, result)
        if self._pending.get() is not None:
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                "event delivery wake context is already active",
            )
        self._pending.set(
            _PendingDelivery(
                event_id=event.event_id,
                subscription_id=subscription.id,
                binding=binding,
                wake_result=result,
            )
        )

    def abort(self, event: PluginEvent, *, subscription: Subscription) -> None:
        """Clear only this exact event/subscription wake context.

        Ownership loss between wake and handle must not leave a stale pending
        binding in the long-lived subscription runtime task. Another event's
        pending context is never cleared.
        """

        pending = self._pending.get()
        if (
            pending is not None
            and pending.event_id == event.event_id
            and pending.subscription_id == subscription.id
        ):
            self._pending.set(None)

    def cancel(self, event: PluginEvent, *, subscription: Subscription) -> None:
        """Alias for cancellation-aware dispatcher adapters."""

        self.abort(event, subscription=subscription)

    async def handle(self, event: PluginEvent, *, subscription: Subscription) -> None:
        pending = self._pending.get()
        if (
            pending is None
            or pending.event_id != event.event_id
            or pending.subscription_id != subscription.id
        ):
            raise PluginEventError(
                PluginEventErrorCode.CONSUME_FAILED,
                "event handler called without its exact wake hook",
            )
        try:
            from prodavan.application.plugins.activation.ports import MintedClaims
            from prodavan.application.plugins.gateway.ports import BackendRequest

            result = pending.wake_result
            claims = MintedClaims(
                token=result.token,
                jti=result.jti,
                issued_at=result.issued_at,
                expires_at=result.expires_at,
                claims=result.claims,
            )
            wire = self.event_signer.signed_wire(event, topic=self._topic)
            body = json.dumps(
                wire, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            delivery_key = backend_idempotency_key(
                IdempotencyKey(
                    subscription_id=subscription.id,
                    source_event_id=event.source_id,
                )
            )
            request = BackendRequest(
                path=f"/events/{quote(event.event_type, safe='')}",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    # Derived only from the signed event + trusted subscription.
                    # It is never accepted from a client and is never logged.
                    "Idempotency-Key": delivery_key,
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
                stream=False,
                max_response_bytes=65536,
                plugin_instance_id=pending.binding.plugin_instance_id,
            )
            response = await self._proxy.forward(
                plugin_instance_id=pending.binding.plugin_instance_id,
                request=request,
                claims=claims,
            )
            status = getattr(response, "status_code", None)
            if (
                not isinstance(status, int)
                or isinstance(status, bool)
                or not 200 <= status < 300
                or getattr(response, "stream", None) is not None
            ):
                raise PluginEventError(
                    PluginEventErrorCode.CONSUME_FAILED,
                    "plugin backend did not acknowledge the event with a bounded 2xx response",
                )
        finally:
            self._pending.set(None)


__all__ = [
    "ActivatingPluginEventHandler",
    "EVENT_SIGNATURE_FIELD",
    "EVENT_SIGNATURE_VERSION",
    "EVENT_SIGNATURE_VERSION_FIELD",
    "HmacPluginEventSigner",
    "PluginEventDispatcher",
    "SignedEventHandler",
    "WakeHook",
]


class PluginEventDispatcher:
    """Доставка событий плагинов через Redpanda dispatch (без потерь).

    Все зависимости — порты из :mod:`ports`. Адаптеры передаются снаружи (F11);
    ``None`` вместо обязательного порта — ``MISSING_DEPENDENCY`` при первом же
    вызове, а не «тихий» режим буферизации.

    ``consume`` обрабатывает ОДНУ запись за вызов (одну итерацию dispatch-loop):
    это делает контракт ``poll → commit/seek`` проверяемым в unit-тестах и
    позволяет внешнему воркеру самому решать, когда останавливаться. Событие не
    теряется при «простое»: пока нет ACK, ``commit`` не вызывается вовсе, а при
    следующем старте ``earliest`` вернёт консьюмера к некоммитнутой позиции.
    """

    def __init__(
        self,
        *,
        producer: ProducerPort | None = None,
        consumer: ConsumerPort | None = None,
        topic_admin: TopicAdminPort | None = None,
        dlq: DlqPort | None = None,
        raw_dlq: RawDlqPort | None = None,
        subscriptions: SubscriptionStorePort | None = None,
        published_events: PublishedEventStorePort | None = None,
        idempotency: IdempotencyStorePort | None = None,
        delivery_claims: DeliveryClaimPort | None = None,
        delivery_lease_seconds: float = 30.0,
        audit: AuditPort | None = None,
        handler: EventHandlerPort | None = None,
        wake_hook: WakeHook | None = None,
        signer: Any = None,
        topic: str = PLUGIN_EVENTS_TOPIC,
    ) -> None:
        self._producer = producer
        self._consumer = consumer
        self._topic_admin = topic_admin
        self._dlq = dlq
        self._raw_dlq = raw_dlq
        self._subscriptions = subscriptions
        self._published_events = published_events
        self._idempotency = idempotency
        self._delivery_claims = delivery_claims
        if (
            not isinstance(delivery_lease_seconds, (int, float))
            or isinstance(delivery_lease_seconds, bool)
            or not math.isfinite(delivery_lease_seconds)
            or delivery_lease_seconds <= 0
        ):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "delivery_lease_seconds must be finite and positive",
            )
        self._delivery_lease_seconds = float(delivery_lease_seconds)
        self._audit = audit
        self._handler = handler
        self._wake_hook = wake_hook
        self._signer = signer if signer is not None else getattr(handler, "event_signer", None)
        self._topic = topic
        # Топики подготавливаются один раз: ensure_topic обязателен (auto_create
        # выключен), но не должен дёргать Admin API на каждое событие.
        self._topics_ready = False

    @property
    def topic(self) -> str:
        return self._topic

    @property
    def consumer_settings(self) -> dict[str, Any]:
        """Канонический конфиг консьюмера (для адаптера и acceptance-теста)."""

        return {**consumer_config(), "topic": self._topic}

    # ------------------------------------------------------------------
    # Внутренние проверки (fail-closed)
    # ------------------------------------------------------------------

    @staticmethod
    def _require(dependency: Any, name: str) -> Any:
        if dependency is None:
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                f"{name} обязателен: молчаливый пропуск доставки запрещён",
            )
        return dependency

    def _require_signer(self) -> Any:
        signer = self._require(self._signer, "PluginEventSigner")
        if not all(callable(getattr(signer, method, None)) for method in ("sign", "verify")):
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                "PluginEventSigner must implement sign() and verify()",
            )
        return signer

    def _signed_wire(self, event: PluginEvent) -> dict[str, Any]:
        signer = self._require_signer()
        try:
            signature = signer.sign(event, topic=self._topic)
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.PUBLISH_FAILED,
                f"event signing failed: {type(exc).__name__}",
            ) from None
        if not _valid_signature(signature):
            raise PluginEventError(
                PluginEventErrorCode.PUBLISH_FAILED,
                "event signer returned an invalid signature",
            )
        return {
            **event.to_wire(),
            EVENT_SIGNATURE_VERSION_FIELD: EVENT_SIGNATURE_VERSION,
            EVENT_SIGNATURE_FIELD: signature,
        }

    def _verify_signed_record(
        self, event: PluginEvent, decoded: Mapping[str, Any]
    ) -> None:
        signer = self._require_signer()
        version = decoded.get(EVENT_SIGNATURE_VERSION_FIELD)
        signature = decoded.get(EVENT_SIGNATURE_FIELD)
        if version != EVENT_SIGNATURE_VERSION or not _valid_signature(signature):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                "unsigned event or unsupported event signature",
            )
        try:
            verified = signer.verify(
                event, topic=self._topic, signature=str(signature)
            )
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.MISSING_DEPENDENCY,
                f"event signature verification unavailable: {type(exc).__name__}",
            ) from None
        if verified is not True:
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                "event signature verification failed",
            )

    async def _ensure_topics_ready(self) -> str:
        """Подготовить основной топик и DLQ ДО любой отправки/чтения.

        auto_create_topics_enabled=false (kafka.yaml:221) — топик плагинов и
        DLQ не создаются init-Job'ом, поэтому единственный легальный путь —
        ``TopicAdminPort``. Ошибка пробрасывается, публикация не начинается.
        """

        admin = self._require(self._topic_admin, "TopicAdminPort")
        if not self._topics_ready:
            await ensure_topic(admin, self._topic)
            await ensure_dlq_topic(admin, self._topic)
            self._topics_ready = True
        return self._topic

    @staticmethod
    def _safe_error_kind(exc: BaseException) -> str:
        """Только имя класса исключения: без текста, payload, URL, токенов."""

        return type(exc).__name__

    async def _audit_received(
        self, event: PluginEvent, subscription: Subscription, *, duplicate: bool = False
    ) -> None:
        """``plugin_event_received``: только id/digest, без raw payload (§4.5)."""

        # Аудит уже проверен в consume (fail-closed до чтения); повторяем здесь,
        # так как метод доступен адаптерам и тестам напрямую.
        audit = self._require(self._audit, "AuditPort")
        await audit.append(
            AUDIT_PLUGIN_EVENT_RECEIVED,
            details={
                "event_id": event.event_id,
                "source_event_id": event.source_id,
                "event_type": event.event_type,
                "company_id": event.company_id,
                "plugin_id": event.plugin_id or "",
                "subscription_id": subscription.id,
                "consumer_group": subscription.consumer_group,
                "payload_digest": event.payload_digest,
                "run_id": event.run_id,
                "correlation_id": event.correlation_id,
                "duplicate": duplicate,
                "topic": self._topic,
            },
        )

    async def _audit_dlq(
        self,
        event: PluginEvent,
        subscription: Subscription,
        *,
        attempts: int,
        error_kind: str | None,
    ) -> None:
        """``plugin_event_dlq``: фиксирует event_id и run_id (criterion 11/20)."""

        # Аудит уже проверен в consume (fail-closed до чтения); повторяем здесь,
        # так как метод доступен адаптерам и тестам напрямую.
        audit = self._require(self._audit, "AuditPort")
        await audit.append(
            AUDIT_PLUGIN_EVENT_DLQ,
            details={
                "event_id": event.event_id,
                "source_event_id": event.source_id,
                "event_type": event.event_type,
                "company_id": event.company_id,
                "plugin_id": event.plugin_id or "",
                "subscription_id": subscription.id,
                "consumer_group": subscription.consumer_group,
                "payload_digest": event.payload_digest,
                "run_id": event.run_id,
                "correlation_id": event.correlation_id,
                "attempts": attempts,
                "error_kind": error_kind or "",
                "dlq_topic": dlq_topic(self._topic),
                "topic": self._topic,
            },
        )

    # ------------------------------------------------------------------
    # Публикация
    # ------------------------------------------------------------------

    async def publish(self, event: PluginEvent) -> bool:
        """Опубликовать событие. Возвращает ``True`` при новой отправке.

        * идемпотентность ПРОДЮСЕРА по ``event_id``: повтор → ``False`` БЕЗ
          повторной отправки (дубликат не считается ошибкой);
        * ключ партиции = ``company_id`` (§7.3); в wire уходит ``to_wire()``
          только с ``payload_digest``, без raw payload;
        * топик и DLQ подготовлены заранее (``ensure_topic`` обязателен);
        * ``flush`` до ``remember``: запоминаем ``event_id`` только после
          подтверждённой отправки, иначе сбой продюсера «съел» бы событие.

        Любая ошибка порта → ``PluginEventError`` (fail-closed), без тихого
        «и так сойдёт».
        """

        if not isinstance(event, PluginEvent):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                "publish ожидает PluginEvent",
            )
        producer = self._require(self._producer, "ProducerPort")
        store = self._require(self._published_events, "PublishedEventStorePort")
        self._require_signer()

        await self._ensure_topics_ready()

        if await store.seen(event.event_id):
            logger.info(
                "plugin event: duplicate publish suppressed event_id=%s", event.event_id
            )
            return False

        key = partition_key(event.company_id)
        try:
            signed_wire = self._signed_wire(event)
            await producer.send(
                topic=self._topic,
                key=key,
                value=signed_wire,
                headers={
                    "event_id": event.event_id,
                    "schema_version": str(event.schema_version),
                    EVENT_SIGNATURE_VERSION_FIELD: EVENT_SIGNATURE_VERSION,
                    EVENT_SIGNATURE_FIELD: str(signed_wire[EVENT_SIGNATURE_FIELD]),
                },
            )
            await producer.flush()
            await store.remember(event.event_id)
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.PUBLISH_FAILED,
                f"публикация {event.event_id} не удалась: {self._safe_error_kind(exc)}",
            ) from exc
        return True

    # ------------------------------------------------------------------
    # Потребление (одна запись за вызов)
    # ------------------------------------------------------------------

    async def consume(self, subscription: Subscription) -> DeliveryOutcome | None:
        """Прочитать и обработать ОДНУ запись для подписки.

        Возврат:
          * ``ACK``   — обработчик принял событие, offset ЗАКОММИЧЕН;
          * ``RETRY`` — временная ошибка, offset НЕ коммитится (будет повтор);
          * DLQ   — 3 неудачи valid event либо malformed raw record записаны
            в соответствующий DLQ, offset ЗАКОММИЧЕН после подтверждения;
          * None  — записей нет (простой консьюмера; НИКАКОГО коммита).

        Гарантия «не теряем при простое»: ``poll`` вернул запись → до успеха
        ``commit`` не вызывается. Если воркер уснул/упал в любой точке до
        коммита, при следующем старте ``earliest`` + не-закоммиченный offset
        вернёт ту же запись. ``seek`` откатывает позицию при каждом RETRY и при
        любом сбое инфраструктурного порта.
        """

        if not isinstance(subscription, Subscription):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_CONTRACT,
                "consume ожидает Subscription",
            )
        consumer = self._require(self._consumer, "ConsumerPort")
        self._require(
            getattr(consumer, "delivery_ownership", None),
            "ConsumerOwnershipPort",
        )
        # Required before poll: malformed/raw records must not bypass the
        # production durable-claim dependency merely because they lack identity.
        self._require(self._delivery_claims, "DeliveryClaimPort")
        subscriptions = self._require(self._subscriptions, "SubscriptionStorePort")
        idem = self._require(self._idempotency, "IdempotencyStorePort")
        handler = self._require(self._handler, "EventHandlerPort")
        dlq = self._require(self._dlq, "DlqPort")
        wake_hook = self._require(self._wake_hook, "WakeHookPort/callable")
        self._require_signer()
        # Аудит проверяем ДО чтения: отсутствие sink'а не должно обнаружиться
        # только после успешной обработки (иначе обработчик уже отработал).
        self._require(self._audit, "AuditPort")

        await self._ensure_topics_ready()

        # Fail-closed: подписка должна существовать и быть активной. Иначе
        # revocation/uninstall продолжил бы получать события.
        known = await subscriptions.get(subscription.id)
        if known is None or not await subscriptions.is_active(subscription.id):
            raise PluginEventError(
                PluginEventErrorCode.UNKNOWN_SUBSCRIPTION,
                f"подписка {subscription.id!r} не найдена или отозвана",
            )
        if (
            known.id != subscription.id
            or known.consumer_group != subscription.consumer_group
            or known.event_types != subscription.event_types
        ):
            raise PluginEventError(
                PluginEventErrorCode.UNKNOWN_SUBSCRIPTION,
                "stored subscription identity differs from the delivery request",
            )

        record: ConsumedRecord | None = await consumer.poll()
        if record is None:
            # Простой консьюмера: НЕ коммитим ничего. Это и есть отличие от
            # as-built kick-режима, который коммитил offset до обработки.
            return None

        offset = record.offset
        if record.topic != self._topic:
            # Never promote an unknown topic into a fictitious PluginEvent and
            # never derive a DLQ topic from an untrusted record topic.
            await consumer.seek(offset)
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                "consumed record is outside the plugin event topic",
            )
        # KafkaConsumedRecord уже хранит исходные байты в raw_value. Используем
        # их до ленивого decoder адаптера: так decode-ошибка не превращается в
        # безликий {}, а digest считается от действительно полученной записи.
        raw_value = getattr(record, "raw_value", None)
        wire_value: Any = raw_value if raw_value is not None else record.value
        try:
            decoded = self._decode_record_value(wire_value)
            event = PluginEvent.from_wire(decoded)
            self._verify_signed_record(event, decoded)
        except PluginEventError as exc:
            if exc.code is PluginEventErrorCode.MISSING_DEPENDENCY:
                # A signer/key configuration failure is not poison; do not DLQ
                # the whole stream. Rewind and surface the typed startup error.
                await consumer.seek(offset)
                raise
            # Missing/invalid signatures are untrusted records. A confirmed raw
            # DLQ is the only way this offset may advance.
            await self._send_raw_to_dlq(
                consumer=consumer,
                record=record,
                wire_value=wire_value,
                subscription=subscription,
                error=exc,
            )
            return DeliveryOutcome.DLQ
        except Exception as exc:
            # До появления PluginEvent нет доверенной identity. Отдельный raw
            # DLQ подтверждает/dedup'ит запись; только затем разрешён commit.
            await self._send_raw_to_dlq(
                consumer=consumer,
                record=record,
                wire_value=wire_value,
                subscription=subscription,
                error=exc,
            )
            return DeliveryOutcome.DLQ

        idem_key = IdempotencyKey(
            subscription_id=subscription.id,
            source_event_id=event.source_id,
        )
        # DB failure remains an error, never a false duplicate. A durable claim
        # is nevertheless required even for duplicate completion/commit.
        seen = await idem.seen(idem_key)
        claim = await self._acquire_delivery_claim(
            consumer=consumer, offset=offset, key=idem_key
        )
        if seen or claim.completed:
            # A completed durable claim is authoritative proof that backend 2xx
            # already happened; a later owner only repairs remember/audit/commit.
            await self._audit_received(event, subscription, duplicate=True)
            await self._renew_delivery_claim(
                claim, consumer=consumer, offset=offset, record=record, uncertain=False
            )
            if not seen:
                await idem.remember(idem_key)
            await self._renew_delivery_claim(
                claim, consumer=consumer, offset=offset, record=record, uncertain=False
            )
            await self._commit_after_success(consumer, offset, record=record)
            await self._release_delivery_claim_after_commit(claim)
            return DeliveryOutcome.ACK

        if not subscription.accepts(event.event_type):
            # Не наш тип: ACK без вызова обработчика (свой offset всё равно
            # коммитится, чтобы не блокировать группу).
            await self._audit_received(event, subscription)
            await self._renew_delivery_claim(
                claim, consumer=consumer, offset=offset, record=record, uncertain=False
            )
            await idem.remember(idem_key)
            await self._renew_delivery_claim(
                claim, consumer=consumer, offset=offset, record=record, uncertain=False
            )
            await self._commit_after_success(consumer, offset, record=record)
            await self._release_delivery_claim_after_commit(claim)
            return DeliveryOutcome.ACK

        last_error_kind: str | None = None
        for attempt_no in range(1, MAX_DELIVERY_ATTEMPTS + 1):
            try:
                # Claim is acquired BEFORE wake. Heartbeat covers a long wake,
                # then a separate atomic revalidation happens immediately before
                # handler.handle, closing the wake→backend rebalance window.
                await self._run_with_delivery_lease(
                    claim,
                    self._call_wake(wake_hook, event, subscription),
                    consumer=consumer,
                    offset=offset,
                    uncertain=False,
                    record=record,
                )
                await self._renew_delivery_claim(
                    claim, consumer=consumer, offset=offset, record=record, uncertain=False
                )
                await self._run_with_delivery_lease(
                    claim,
                    handler.handle(event, subscription=subscription),
                    consumer=consumer,
                    offset=offset,
                    uncertain=True,
                    record=record,
                )
                # EventHandlerPort returns only after a real bounded 2xx. Any
                # ownership loss during handler is uncertain, never ACK/DLQ.
                await self._complete_delivery_claim(
                    claim, consumer=consumer, offset=offset, record=record
                )
                attempt = DeliveryAttempt(outcome=DeliveryOutcome.ACK, attempt=attempt_no)
            except (DeliveryClaimError, DeliveryOutcomeUncertainError):
                await self._abort_wake(wake_hook, event, subscription)
                raise
            except asyncio.CancelledError:
                await self._abort_wake(wake_hook, event, subscription)
                raise
            except Exception as exc:
                await self._abort_wake(wake_hook, event, subscription)
                last_error_kind = self._safe_error_kind(exc)
                logger.info(
                    "plugin event: handler failed event_id=%s attempt=%s error=%s",
                    event.event_id,
                    attempt_no,
                    last_error_kind,
                )
                attempt = DeliveryAttempt(
                    outcome=DeliveryOutcome.RETRY,
                    attempt=attempt_no,
                    error_kind=last_error_kind,
                )

            outcome = delivery_outcome_for_attempt(attempt)
            if outcome is DeliveryOutcome.ACK:
                await self._audit_received(event, subscription)
                await self._renew_delivery_claim(
                    claim, consumer=consumer, offset=offset, record=record, uncertain=True
                )
                await idem.remember(idem_key)
                await self._renew_delivery_claim(
                    claim, consumer=consumer, offset=offset, record=record, uncertain=True
                )
                await self._commit_after_success(consumer, offset, record=record)
                # Keep the completed lease until TTL/transfer: this is the
                # durable ownership evidence expected during replay/finalization.
                return DeliveryOutcome.ACK

            if outcome is DeliveryOutcome.RETRY:
                # offset НЕ коммитим; возвращаем позицию к этой же записи.
                await consumer.seek(offset)
                continue

            # Valid-event DLQ semantics are preserved, but every terminal step
            # is still owner-fenced; stale consumers cannot publish/commit DLQ.
            await self._renew_delivery_claim(
                claim, consumer=consumer, offset=offset, record=record, uncertain=True
            )
            await self._send_to_dlq(
                dlq=dlq,
                event=event,
                subscription=subscription,
                error_kind=last_error_kind,
            )
            await self._renew_delivery_claim(
                claim, consumer=consumer, offset=offset, record=record, uncertain=True
            )
            await self._audit_dlq(
                event,
                subscription,
                attempts=MAX_DELIVERY_ATTEMPTS,
                error_kind=last_error_kind,
            )
            await self._renew_delivery_claim(
                claim, consumer=consumer, offset=offset, record=record, uncertain=True
            )
            await idem.remember(idem_key)
            await self._renew_delivery_claim(
                claim, consumer=consumer, offset=offset, record=record, uncertain=True
            )
            # Коммит ТОЛЬКО после подтверждённой записи в DLQ и аудита.
            await self._commit_after_success(consumer, offset, record=record)
            await self._release_delivery_claim_after_commit(claim)
            return DeliveryOutcome.DLQ

        # Недостижимо: цикл всегда заканчивается ACK/RETRY/DLQ. Оставлено как
        # fail-closed предохранитель на случай правки MAX_DELIVERY_ATTEMPTS.
        await consumer.seek(offset)
        raise PluginEventError(
            PluginEventErrorCode.CONSUME_FAILED,
            f"доставка {event.event_id} завершилась без исхода",
        )

    @staticmethod
    def _consumer_owns_record(
        consumer: ConsumerPort, record: ConsumedRecord
    ) -> bool:
        """Use optional public assignment metadata; never private Kafka internals."""

        checker = getattr(consumer, "delivery_ownership", None)
        if not callable(checker):
            # Missing typed metadata is fail-closed. consume() rejects it before
            # poll; this guard also protects direct/raw helper calls.
            return False
        try:
            assignment = checker(record)
        except Exception:
            return False
        return (
            isinstance(assignment, ConsumerAssignment)
            and assignment.topic == record.topic
            and assignment.partition == record.partition
            and (assignment.offset is None or assignment.offset == record.offset)
            and assignment.owned is True
        )

    async def _require_record_ownership(
        self,
        consumer: ConsumerPort,
        record: ConsumedRecord,
        offset: int,
    ) -> None:
        """Fence raw DLQ publication as well as valid-event critical sections."""

        if not self._consumer_owns_record(consumer, record):
            await self._reject_delivery_ownership(
                consumer,
                offset,
                uncertain=False,
                reason="consumer assignment was lost before raw DLQ delivery",
            )

    async def _acquire_delivery_claim(
        self,
        *,
        consumer: ConsumerPort,
        offset: int,
        key: IdempotencyKey,
    ) -> DeliveryClaim:
        """Acquire durable ownership before duplicate handling, wake or delivery."""

        try:
            store = self._require(self._delivery_claims, "DeliveryClaimPort")
            claim = await store.acquire(
                key, lease_seconds=self._delivery_lease_seconds
            )
        except PluginEventError:
            await consumer.seek(offset)
            raise
        except Exception as exc:
            await consumer.seek(offset)
            raise DeliveryClaimError(
                f"delivery claim acquire failed: {self._safe_error_kind(exc)}"
            ) from exc
        if not isinstance(claim, DeliveryClaim) or claim.key != key:
            await consumer.seek(offset)
            raise DeliveryClaimError(
                "delivery claim is held by another owner or is invalid"
            )
        return claim

    @staticmethod
    async def _reject_delivery_ownership(
        consumer: ConsumerPort,
        offset: int,
        *,
        uncertain: bool,
        reason: str,
    ) -> None:
        """Rewind best-effort, preserving the original typed ownership error.

        A revoked partition can reject seek itself. That secondary adapter error
        is logged only as a sanitized class name and must never mask the typed
        retryable/uncertain ownership failure or turn into a commit/DLQ result.
        """

        try:
            await consumer.seek(offset)
        except Exception as exc:
            logger.info(
                "plugin event: ownership seek failed offset=%s error=%s",
                offset,
                type(exc).__name__,
            )
        if uncertain:
            raise DeliveryOutcomeUncertainError(reason)
        raise DeliveryClaimError(reason)

    async def _renew_delivery_claim(
        self,
        claim: DeliveryClaim,
        *,
        consumer: ConsumerPort,
        offset: int,
        uncertain: bool,
        record: ConsumedRecord | None = None,
    ) -> None:
        """Atomically revalidate and renew immediately before a critical step."""

        try:
            store = self._require(self._delivery_claims, "DeliveryClaimPort")
            owned = await store.renew(claim)
        except Exception:
            owned = False
        if owned is not True or (
            record is not None and not self._consumer_owns_record(consumer, record)
        ):
            await self._reject_delivery_ownership(
                consumer,
                offset,
                uncertain=uncertain,
                reason="delivery claim ownership or consumer assignment was lost",
            )

    async def _complete_delivery_claim(
        self,
        claim: DeliveryClaim,
        *,
        consumer: ConsumerPort,
        offset: int,
        record: ConsumedRecord | None = None,
    ) -> None:
        """Owner-only durable completion, called only after real handler 2xx."""

        try:
            store = self._require(self._delivery_claims, "DeliveryClaimPort")
            completed = await store.complete(claim)
        except Exception:
            completed = False
        if completed is not True or (
            record is not None and not self._consumer_owns_record(consumer, record)
        ):
            await self._reject_delivery_ownership(
                consumer,
                offset,
                uncertain=True,
                reason="delivery ownership or assignment was lost after backend delivery",
            )

    async def _release_delivery_claim_after_commit(
        self, claim: DeliveryClaim
    ) -> None:
        """Best-effort lease cleanup; a completed commit is never rewritten."""

        try:
            store = self._require(self._delivery_claims, "DeliveryClaimPort")
            await store.release(claim)
        except Exception:
            # The lease expires naturally. Completion (when applicable) remains
            # durable, and a post-commit cleanup error cannot become a retry.
            return

    async def _delivery_lease_heartbeat(
        self,
        claim: DeliveryClaim,
        parent: asyncio.Task[Any],
        consumer: ConsumerPort | None = None,
        record: ConsumedRecord | None = None,
    ) -> None:
        """Renew through long calls and cancel the same task on ownership loss.

        The operation stays in the caller's context (the exact wake→handler
        ContextVar binding is preserved); no private consumer metadata is used.
        """

        interval = max(claim.lease_seconds / 3.0, 0.001)
        store = self._require(self._delivery_claims, "DeliveryClaimPort")
        while True:
            await asyncio.sleep(interval)
            try:
                owned = await store.renew(claim)
            except Exception:
                owned = False
            if owned is not True or (
                consumer is not None
                and record is not None
                and not self._consumer_owns_record(consumer, record)
            ):
                parent.cancel()
                return

    async def _run_with_delivery_lease(
        self,
        claim: DeliveryClaim,
        operation: Awaitable[Any],
        *,
        consumer: ConsumerPort,
        offset: int,
        uncertain: bool,
        record: ConsumedRecord | None = None,
    ) -> Any:
        """Run wake/handler in the current context with a loss-cancelling lease."""

        parent = asyncio.current_task()
        if parent is None:
            raise DeliveryClaimError("delivery lease requires an asyncio task")
        heartbeat_task = asyncio.create_task(
            self._delivery_lease_heartbeat(
                claim,
                parent,
                consumer=consumer,
                record=record,
            )
        )
        try:
            result = await operation
            if heartbeat_task.done():
                await self._reject_delivery_ownership(
                    consumer,
                    offset,
                    uncertain=uncertain,
                    reason="delivery lease renewal failed during delivery",
                )
            return result
        except asyncio.CancelledError:
            if heartbeat_task.done():
                await self._reject_delivery_ownership(
                    consumer,
                    offset,
                    uncertain=uncertain,
                    reason="delivery lease renewal failed during delivery",
                )
            raise
        except Exception:
            if heartbeat_task.done():
                await self._reject_delivery_ownership(
                    consumer,
                    offset,
                    uncertain=uncertain,
                    reason="delivery lease renewal failed during delivery",
                )
            raise
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    @staticmethod
    def _decode_record_value(value: Any) -> Mapping[str, Any]:
        """Декодировать wire-значение до PluginEvent.from_wire.

        Mapping используется для уже декодированных адаптеров. bytes/str проходят
        строгий UTF-8/JSON decode; JSON обязан быть объектом. Любой отказ здесь
        относится к raw record и обрабатывается отдельным RawDlqPort.
        """

        if isinstance(value, Mapping):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            text = bytes(value).decode("utf-8", errors="strict")
        elif isinstance(value, str):
            text = value
        else:
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                "wire-значение должно быть JSON-объектом или UTF-8 bytes/str",
            )
        decoded = json.loads(text)
        if not isinstance(decoded, Mapping):
            raise PluginEventError(
                PluginEventErrorCode.INVALID_EVENT,
                "JSON записи события должен быть объектом",
            )
        return decoded

    @staticmethod
    def _raw_payload_digest(value: Any) -> str:
        """Односторонний digest wire-значения без сохранения сырого payload.

        Для байтов digest считается от точного исходного содержимого. Mapping из
        уже декодированного адаптера сериализуется канонически. Несериализуемые
        тестовые/сторонние объекты получают digest безопасного type-marker;
        идентичность и дедупликация всё равно определяются координатами записи.
        """

        if isinstance(value, (bytes, bytearray, memoryview)):
            encoded = bytes(value)
        elif isinstance(value, str):
            encoded = value.encode("utf-8")
        else:
            try:
                encoded = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=lambda item: f"<{type(item).__name__}>",
                ).encode("utf-8")
            except Exception:
                encoded = f"<unserializable:{type(value).__name__}>".encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    async def _send_raw_to_dlq(
        self,
        *,
        consumer: ConsumerPort,
        record: ConsumedRecord,
        wire_value: Any,
        subscription: Subscription,
        error: Exception,
    ) -> None:
        """Подтвердить raw DLQ и только потом коммитить source offset.

        RawDlqPort сам атомарно дедуплицирует естественный ключ записи. Поэтому
        повтор после неопределённого результата или commit failure не создаёт
        вторую durable запись. Отсутствие production-адаптера — явная ошибка,
        seek и retry, а не in-memory fallback.
        """

        await self._require_record_ownership(consumer, record, record.offset)
        try:
            raw_dlq = self._require(self._raw_dlq, "RawDlqPort")
            raw_record = RawDlqRecord(
                topic=record.topic,
                partition=record.partition,
                offset=record.offset,
                consumer_group=subscription.consumer_group,
                payload_digest=self._raw_payload_digest(wire_value),
                error_kind=self._safe_error_kind(error),
            )
            published = await raw_dlq.publish(raw_record)
            if not isinstance(published, bool):
                raise PluginEventError(
                    PluginEventErrorCode.DLQ_FAILED,
                    "RawDlqPort.publish обязан вернуть bool после подтверждения",
                )
        except Exception as exc:
            await consumer.seek(record.offset)
            if isinstance(exc, PluginEventError):
                raise
            raise PluginEventError(
                PluginEventErrorCode.DLQ_FAILED,
                f"запись raw DLQ не удалась: {self._safe_error_kind(exc)}",
            ) from exc

        # True = новая запись, False = подтверждённый дубликат; оба исхода
        # разрешают commit. Исключение выше и commit failure его запрещают.
        await self._commit_after_success(consumer, record.offset, record=record)

    async def _commit_after_success(
        self,
        consumer: ConsumerPort,
        offset: int,
        *,
        record: ConsumedRecord | None = None,
    ) -> None:
        """Коммит ТОЛЬКО после успеха; сбой коммита → seek + COMMIT_FAILED.

        Если адаптер не смог зафиксировать offset (брокер недоступен, ребаланс,
        консьюмер остановлен), событие НЕ считается доставленным: возвращаем
        позицию к этой же записи, чтобы следующий старт с ``earliest`` прочитал
        её снова. Именно это отличает dispatch от ``kick`` + ``auto_commit``.
        """

        if record is not None and not self._consumer_owns_record(consumer, record):
            await self._reject_delivery_ownership(
                consumer,
                offset,
                uncertain=True,
                reason="consumer assignment was lost before offset commit",
            )
        try:
            await consumer.commit(offset)
        except Exception as exc:
            await consumer.seek(offset)
            raise PluginEventError(
                PluginEventErrorCode.COMMIT_FAILED,
                f"коммит offset={offset} не удался: {type(exc).__name__}",
            ) from exc

    async def _abort_wake(
        self,
        wake_hook: WakeHook,
        event: PluginEvent,
        subscription: Subscription,
    ) -> None:
        """Best-effort exact wake abort; never clear another event's pending."""

        callback = getattr(wake_hook, "abort", None)
        if not callable(callback):
            callback = getattr(wake_hook, "cancel", None)
        if not callable(callback):
            return
        try:
            result = callback(event, subscription=subscription)
            if hasattr(result, "__await__"):
                await result
        except Exception:
            # The original ownership/handler error remains authoritative.
            return

    async def _call_wake(
        self,
        wake_hook: WakeHook,
        event: PluginEvent,
        subscription: Subscription,
    ) -> None:
        """Вызвать wake-колбэк (объект задачи 09 или async callable).

        Важно: wake выполняется ДО ``handler.handle`` и ДО ``commit``. Ошибка
        wake приравнена к ошибке обработчика → RETRY (offset не коммитится).
        """

        try:
            if hasattr(wake_hook, "wake"):
                await wake_hook.wake(event, subscription=subscription)  # type: ignore[union-attr]
            else:
                await wake_hook(event, subscription)  # type: ignore[operator]
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                f"пробуждение backend не удалось: {self._safe_error_kind(exc)}",
            ) from exc

    async def _send_to_dlq(
        self,
        *,
        dlq: DlqPort,
        event: PluginEvent,
        subscription: Subscription,
        error_kind: str | None,
    ) -> None:
        """Записать DLQ-событие с сохранением ``event_id`` и ``run_id``.

        Никакого raw payload: :class:`DlqRecord` несёт ``payload_digest``.
        Ошибка записи → ``DLQ_FAILED`` и коммит НЕ выполняется.
        """

        record = DlqRecord(
            event=event,
            subscription_id=subscription.id,
            topic=self._topic,
            dlq_topic=dlq_topic(self._topic),
            attempts=MAX_DELIVERY_ATTEMPTS,
            error_kind=error_kind,
        )
        try:
            await dlq.publish(record)
        except PluginEventError:
            raise
        except Exception as exc:
            raise PluginEventError(
                PluginEventErrorCode.DLQ_FAILED,
                f"запись в DLQ не удалась: {self._safe_error_kind(exc)}",
            ) from exc
