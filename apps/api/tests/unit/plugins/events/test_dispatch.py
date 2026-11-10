"""Unit-тесты PluginEventDispatcher (F7, задача 10, acceptance №20).

Покрытие требований задачи:

* событие НЕ теряется при «простое» консьюмера: offset не коммитится до успеха;
* 3 retry -> DLQ, коммит только после записи в DLQ;
* повтор ``source_event_id`` не вызывает обработчик второй раз;
* ключ партиции = ``company_id``;
* ``auto_offset_reset=earliest`` и ``enable_auto_commit=False`` зафиксированы;
* ``wake_hook`` вызывается ДО handler;
* DLQ-событие сохраняет ``event_id`` и ``run_id``;
* outbox ``webhook_deliveries`` НЕ используется как шина (нет импорта).

Тесты написаны, НО НЕ ЗАПУСКАЮТСЯ (verification freeze, §9 дизайна v4).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from prodavan.application.plugins.events import (
    AUDIT_PLUGIN_EVENT_DLQ,
    AUDIT_PLUGIN_EVENT_RECEIVED,
    CONSUMER_AUTO_OFFSET_RESET,
    CONSUMER_ENABLE_AUTO_COMMIT,
    PLUGIN_EVENTS_TOPIC,
    DeliveryOutcome,
    IdempotencyKey,
    PluginEventError,
    PluginEventErrorCode,
    consumer_config,
    ensure_topic,
    partition_key,
)
from prodavan.application.plugins.events.topics import dlq_topic

from .conftest import (
    FakeHandler,
    FakeWake,
    RecordingWake,
    build_harness,
    make_event,
    make_subscription,
    record_of,
)


# ---------------------------------------------------------------------------
# Простой консьюмера / ручной commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_consumer_does_not_commit_anything() -> None:
    """Простой: poll -> None, commit НЕ вызывается (событие не теряется).

    В as-built ``kick`` + ``enable_auto_commit=True`` offset коммитится самим
    клиентом до обработки и при рестарте с ``latest`` событие пропадает. Здесь
    нет записи => нет коммита; при следующем старте ``earliest`` вернёт позицию.
    """

    h = build_harness(records=[])
    outcome = await h.dispatcher.consume(make_subscription())
    assert outcome is None
    assert h.consumer.polls == 1
    assert h.consumer.commits == []
    assert h.consumer.seeks == []
    assert h.handler.calls == []


@pytest.mark.asyncio
async def test_retry_does_not_commit_offset_and_restart_replays_event() -> None:
    """Ключевой criterion 20: до успеха commit не вызывается.

    Диспечер делает до 3 попыток ВНУТРИ одного ``consume``: падение на 1-й и
    успех на 2-й дают итоговый ACK с ОДНИМ коммитом и ``seek`` на каждой
    неудачной попытке. Отдельно проверяем «простой/рестарт»: пока успеха нет,
    коммита нет вообще, а повторная доставка той же записи после рестарта
    обрабатывается и коммитится (``earliest`` + ручной commit).
    """

    event = make_event(event_id="evt-idle", source_event_id="src-idle")
    # Ретраи внутри одного consume: падение на 1-й попытке, успех на 2-й.
    h = build_harness(
        records=[record_of(event, offset=101)],
        handler=FakeHandler(fails_at={1}),
    )
    outcome = await h.dispatcher.consume(make_subscription())
    assert outcome is DeliveryOutcome.ACK
    assert h.consumer.seeks == [101], "неудачная попытка -> seek(offset)"
    assert h.consumer.commits == [101], "ровно один коммит ПОСЛЕ успеха"
    assert len(h.handler.calls) == 2
    assert h.dlq.records == []

    # Сценарий «простой»: запись есть, но коммит упал (эмуляция останова
    # консьюмера/брокера). Успешная обработка не должна «проскочить» offset.
    h_restart = build_harness(records=[record_of(event, offset=202)])
    h_restart.consumer.fail_commit = True
    with pytest.raises(Exception):
        await h_restart.dispatcher.consume(make_subscription())
    assert h_restart.consumer.commits == [], "сбой коммита не теряет событие"
    assert h_restart.handler.calls, "обработчик был вызван"

    # После снятия простоя та же запись приходит снова и коммитится.
    h_restart.consumer.fail_commit = False
    h_restart.consumer.records.append(record_of(event, offset=202))
    recovery = await h_restart.dispatcher.consume(make_subscription())
    assert recovery is DeliveryOutcome.ACK
    assert h_restart.consumer.commits == [202]


@pytest.mark.asyncio
async def test_successful_delivery_commits_only_after_handler_and_audit() -> None:
    """ACK: commit строго после handler + wake + audit + remember."""

    event = make_event()
    h = build_harness(records=[record_of(event, offset=7)])
    outcome = await h.dispatcher.consume(make_subscription())
    assert outcome is DeliveryOutcome.ACK
    assert h.consumer.commits == [7]
    assert len(h.handler.calls) == 1
    assert len(h.wake.wakes) == 1
    assert [name for name, _ in h.audit.entries] == [AUDIT_PLUGIN_EVENT_RECEIVED]
    assert h.idempotency.remembered == [IdempotencyKey("sub_1", "evt-1")]


@pytest.mark.asyncio
async def test_audit_received_has_no_raw_payload_or_url() -> None:
    """criterion 11: в аудите только id/digest, без тел/токенов/URL."""

    event = make_event()
    h = build_harness(records=[record_of(event)])
    await h.dispatcher.consume(make_subscription())
    details = h.audit.entries[0][1]
    assert details["event_id"] == "evt-1"
    assert details["run_id"] == "run_77"
    assert details["payload_digest"].startswith("sha256:")
    for forbidden in ("payload", "raw", "url", "token", "secret", "body", "sql"):
        assert forbidden not in details


# ---------------------------------------------------------------------------
# 3 retry -> DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_retries_then_dlq_then_commit() -> None:
    """Ровно 3 попытки handler, затем DLQ и commit (без коммита на ретраях)."""

    event = make_event(event_id="evt-fail", run_id="run-fail", source_event_id="src-fail")
    h = build_harness(
        records=[record_of(event, offset=55)],
        handler=FakeHandler(fails_at={1, 2, 3}),
    )
    outcome = await h.dispatcher.consume(make_subscription())
    assert outcome is DeliveryOutcome.DLQ
    assert len(h.handler.calls) == 3
    assert len(h.wake.wakes) == 3  # wake перед КАЖДОЙ попыткой
    assert h.consumer.seeks == [55, 55], "два RETRY -> два seek, коммита нет"
    assert h.consumer.commits == [55], "коммит ТОЛЬКО после DLQ"

    assert len(h.dlq.records) == 1
    dlq_wire = h.dlq.records[0].to_wire()
    assert dlq_wire["event_id"] == "evt-fail"
    assert dlq_wire["run_id"] == "run-fail"
    assert dlq_wire["attempts"] == 3
    assert dlq_wire["error_kind"] == "RuntimeError"
    assert dlq_wire["dlq_topic"] == "agentscale.plugin.events.dlq"
    assert "payload" not in dlq_wire

    names = [name for name, _ in h.audit.entries]
    assert AUDIT_PLUGIN_EVENT_DLQ in names
    dlq_audit = next(details for name, details in h.audit.entries if name == AUDIT_PLUGIN_EVENT_DLQ)
    assert dlq_audit["event_id"] == "evt-fail"
    assert dlq_audit["run_id"] == "run-fail"
    assert h.idempotency.remembered == [IdempotencyKey("sub_1", "src-fail")]


@pytest.mark.asyncio
async def test_dlq_failure_does_not_commit_offset() -> None:
    """Fail-closed: DLQ недоступен -> commit НЕ выполняется, событие не потеряно."""

    event = make_event()
    h = build_harness(
        records=[record_of(event, offset=9)],
        handler=FakeHandler(fails_at={1, 2, 3}),
    )
    h.dlq.fail = True
    with pytest.raises(PluginEventError) as exc:
        await h.dispatcher.consume(make_subscription())
    assert exc.value.code is PluginEventErrorCode.DLQ_FAILED
    assert h.consumer.commits == []
    assert AUDIT_PLUGIN_EVENT_DLQ not in [name for name, _ in h.audit.entries]


@pytest.mark.asyncio
async def test_missing_audit_port_fails_before_handler() -> None:
    """Fail-closed: нет AuditPort -> отказ до вызова обработчика и коммита."""

    from prodavan.application.plugins.events.dispatch import PluginEventDispatcher

    h = build_harness(records=[record_of(make_event(), offset=3)])
    dispatcher = PluginEventDispatcher(
        producer=h.producer,
        consumer=h.consumer,
        topic_admin=h.admin,
        dlq=h.dlq,
        subscriptions=h.subscriptions,
        published_events=h.published,
        idempotency=h.idempotency,
        audit=None,
        handler=h.handler,
        wake_hook=h.wake,
    )
    with pytest.raises(PluginEventError) as exc:
        await dispatcher.consume(make_subscription())
    assert exc.value.code is PluginEventErrorCode.MISSING_DEPENDENCY
    assert h.handler.calls == []
    assert h.consumer.commits == []


@pytest.mark.asyncio
async def test_wake_failure_exhausts_retries_without_commit() -> None:
    """Ошибка wake (backend не Ready) считается ошибкой доставки.

    Wake вызывается перед КАЖДОЙ из 3 попыток: handler ни разу не вызывается,
    offset не коммитится, событие уходит в DLQ только после 3 неудач.
    """

    event = make_event()
    h = build_harness(records=[record_of(event, offset=4)], wake=FakeWake(fail=True))
    outcome = await h.dispatcher.consume(make_subscription())
    assert outcome is DeliveryOutcome.DLQ
    assert h.handler.calls == []
    assert len(h.wake.wakes) == 0
    assert h.consumer.commits == [4], "коммит только после DLQ"
    assert h.consumer.seeks == [4, 4]
    assert len(h.dlq.records) == 1
    assert h.dlq.records[0].error_kind == "PluginEventError"


# ---------------------------------------------------------------------------
# Идемпотентность консьюмера
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_source_event_id_does_not_call_handler_twice() -> None:
    """Повтор source_event_id -> ACK без второго вызова handler (по (sub, src))."""

    event = make_event(event_id="evt-a", source_event_id="src-stable")
    repeated = make_event(event_id="evt-b", source_event_id="src-stable")
    h = build_harness(records=[record_of(event, offset=1), record_of(repeated, offset=2)])

    first = await h.dispatcher.consume(make_subscription())
    assert first is DeliveryOutcome.ACK
    assert len(h.handler.calls) == 1
    assert h.idempotency.seen_keys == {IdempotencyKey("sub_1", "src-stable")}

    second = await h.dispatcher.consume(make_subscription())
    assert second is DeliveryOutcome.ACK
    assert len(h.handler.calls) == 1, "обработчик НЕ должен вызываться второй раз"
    assert h.wake.wakes == [event], "wake тоже не повторяется для дубликата"
    assert h.consumer.commits == [1, 2]
    assert h.audit.entries[-1][1]["duplicate"] is True


@pytest.mark.asyncio
async def test_same_source_event_id_in_another_subscription_is_not_a_duplicate() -> None:
    """Ключ включает subscription_id: другая подписка обрабатывает событие."""

    event = make_event(source_event_id="src-shared")
    sub_a = make_subscription(subscription_id="sub_a", consumer_group="g-a")
    sub_b = make_subscription(subscription_id="sub_b", consumer_group="g-b")
    h = build_harness(records=[record_of(event, offset=1), record_of(event, offset=2)],
                      subscription=sub_a)
    await h.dispatcher.consume(sub_a)
    # FakeSubscriptions хранит одну active запись; sub_b тоже должен быть
    # зарегистрирован до consume, иначе strict identity guard корректно откажет.
    h.subscriptions.subscription = sub_b
    await h.dispatcher.consume(sub_b)
    assert len(h.handler.calls) == 2
    assert h.idempotency.seen_keys == {
        IdempotencyKey("sub_a", "src-shared"),
        IdempotencyKey("sub_b", "src-shared"),
    }


@pytest.mark.asyncio
async def test_event_type_filter_acks_without_handler() -> None:
    """Чужой тип: ACK без вызова handler, но со своим коммитом offset'а."""

    event = make_event(event_type="price.updated")
    h = build_harness(records=[record_of(event, offset=8)],
                      subscription=make_subscription(event_types=("offer.created",)))
    outcome = await h.dispatcher.consume(make_subscription(event_types=("offer.created",)))
    assert outcome is DeliveryOutcome.ACK
    assert h.handler.calls == []
    assert h.consumer.commits == [8]


# ---------------------------------------------------------------------------
# Публикация / ключ партиции / ensure_topic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_uses_company_id_as_partition_key() -> None:
    """§7.3: ключ партиции = company_id, в wire только payload_digest."""

    event = make_event(company_id="co_partition_42")
    h = build_harness()
    assert await h.dispatcher.publish(event) is True
    sent = h.producer.sent[0]
    assert sent["key"] == "co_partition_42"
    assert sent["key"] == partition_key(event.company_id)
    assert sent["topic"] == "agentscale.plugin.events"
    assert sent["value"]["payload_digest"].startswith("sha256:")
    assert "payload" not in sent["value"]
    assert h.producer.flushes == 1
    assert h.published.remembered == ["evt-1"]


@pytest.mark.asyncio
async def test_publish_ensures_topic_and_dlq_before_send() -> None:
    """auto_create=false: оба топика подготовлены ДО первой отправки."""

    h = build_harness()
    await h.dispatcher.publish(make_event())
    assert [name for name, _, _ in h.admin.created] == [
        PLUGIN_EVENTS_TOPIC,
        "agentscale.plugin.events.dlq",
    ]
    assert h.producer.sent[0]["key"] == "co_42"


@pytest.mark.asyncio
async def test_publish_idempotent_by_event_id() -> None:
    """Повтор event_id -> вторая отправка подавляется."""

    h = build_harness()
    event = make_event(event_id="evt-once")
    assert await h.dispatcher.publish(event) is True
    assert await h.dispatcher.publish(event) is False
    assert len(h.producer.sent) == 1
    assert h.published.remembered == ["evt-once"]


@pytest.mark.asyncio
async def test_publish_failure_does_not_remember_event_id() -> None:
    """Fail-closed: сбой продюсера не 'съедает' событие (remember после flush)."""

    h = build_harness()
    h.producer.fail = True
    with pytest.raises(PluginEventError) as exc:
        await h.dispatcher.publish(make_event())
    assert exc.value.code is PluginEventErrorCode.PUBLISH_FAILED
    assert h.published.remembered == []


@pytest.mark.asyncio
async def test_publish_without_topic_admin_fails_closed() -> None:
    """Нет TopicAdminPort -> отказ, а не надежда на auto-create."""

    from prodavan.application.plugins.events.dispatch import PluginEventDispatcher

    h = build_harness()
    dispatcher = PluginEventDispatcher(
        producer=h.producer,
        consumer=h.consumer,
        topic_admin=None,
        dlq=h.dlq,
        subscriptions=h.subscriptions,
        published_events=h.published,
        idempotency=h.idempotency,
        audit=h.audit,
        handler=h.handler,
        wake_hook=h.wake,
    )
    with pytest.raises(PluginEventError) as exc:
        await dispatcher.publish(make_event())
    assert exc.value.code is PluginEventErrorCode.MISSING_DEPENDENCY
    assert h.producer.sent == []


# ---------------------------------------------------------------------------
# wake_hook до handler + конфиг консьюмера
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wake_hook_is_called_before_handler() -> None:
    """Задача 09: wake ДО доставки. Проверяем порядок в общем timeline."""

    timeline: list[str] = []
    wake = RecordingWake(timeline)

    class TimelineHandler(FakeHandler):
        async def handle(self, event, *, subscription):  # type: ignore[override]
            timeline.append("handler")
            await super().handle(event, subscription=subscription)

    h = build_harness(
        records=[record_of(make_event(), offset=1)],
        handler=TimelineHandler(),
        wake=wake,
    )
    outcome = await h.dispatcher.consume(make_subscription())
    assert outcome is DeliveryOutcome.ACK
    assert timeline == ["wake", "handler"]
    assert len(wake.wakes) == 1


@pytest.mark.asyncio
async def test_wake_hook_runs_before_every_retry() -> None:
    """Перед каждой из 3 попыток backend будится заново."""

    event = make_event()
    h = build_harness(
        records=[record_of(event, offset=1)],
        handler=FakeHandler(fails_at={1, 2, 3}),
    )
    await h.dispatcher.consume(make_subscription())
    assert len(h.wake.wakes) == 3
    assert all(w.event_id == event.event_id for w in h.wake.wakes)
    assert h.dlq.records[0].error_kind == "RuntimeError"


def test_consumer_config_is_earliest_and_manual_commit() -> None:
    """Acceptance №20: конфиг зафиксирован в контракте и в диспетчере."""

    assert CONSUMER_AUTO_OFFSET_RESET == "earliest"
    assert CONSUMER_ENABLE_AUTO_COMMIT is False
    cfg = consumer_config()
    assert cfg["auto_offset_reset"] == "earliest"
    assert cfg["enable_auto_commit"] is False
    assert cfg["consumer_mode"] == "dispatch"
    assert "kick" not in cfg["consumer_mode"]

    h = build_harness()
    settings = h.dispatcher.consumer_settings
    assert settings["auto_offset_reset"] == "earliest"
    assert settings["enable_auto_commit"] is False
    assert settings["topic"] == PLUGIN_EVENTS_TOPIC
    assert settings["max_delivery_attempts"] == 3


@pytest.mark.asyncio
async def test_unknown_subscription_fails_closed() -> None:
    """Отозванная подписка -> отказ без чтения/коммита."""

    h = build_harness(records=[record_of(make_event(), offset=1)])
    h.subscriptions.active = False
    with pytest.raises(PluginEventError) as exc:
        await h.dispatcher.consume(make_subscription())
    assert exc.value.code is PluginEventErrorCode.UNKNOWN_SUBSCRIPTION
    assert h.consumer.polls == 0
    assert h.consumer.commits == []


@pytest.mark.asyncio
async def test_malformed_record_without_raw_dlq_fails_closed() -> None:
    """Нет durable RawDlqPort -> seek + MISSING_DEPENDENCY, не in-memory fallback."""

    from prodavan.application.plugins.events.dispatch import PluginEventDispatcher

    from .conftest import FakeRecord

    h = build_harness(records=[FakeRecord(value={"event_id": "only"}, offset=12)])
    dispatcher = PluginEventDispatcher(
        producer=h.producer,
        consumer=h.consumer,
        topic_admin=h.admin,
        dlq=h.dlq,
        subscriptions=h.subscriptions,
        published_events=h.published,
        idempotency=h.idempotency,
        delivery_claims=h.claims,
        audit=h.audit,
        handler=h.handler,
        wake_hook=h.wake,
    )
    with pytest.raises(PluginEventError) as exc:
        await dispatcher.consume(make_subscription())
    assert exc.value.code is PluginEventErrorCode.MISSING_DEPENDENCY
    assert h.consumer.seeks == [12]
    assert h.consumer.commits == []
    assert h.handler.calls == []


# ---------------------------------------------------------------------------
# Разделение транспортов: outbox НЕ шина событий
# ---------------------------------------------------------------------------


def _source_files() -> list[Path]:
    """Все .py исходники зоны events (без тестов)."""

    root = Path(__file__).resolve()
    # tests/unit/plugins/events -> apps/api/src/prodavan/application/plugins/events
    src = root.parents[4] / "src" / "prodavan" / "application" / "plugins" / "events"
    return sorted(src.glob("*.py"))


def test_events_zone_never_imports_webhook_outbox() -> None:
    """§13.3: outbox webhook_deliveries остаётся ТОЛЬКО для исходящих вебхуков.

    Проверяем по AST всех модулей зоны: нет импорта моделей/сервиса outbox,
    нет ``KafkaManager`` (as-built kick-режим), нет ``celery``/``prodavan.jobs``.
    Docstring-константы исключены: в них outbox упоминается лишь как ЗАПРЕЩЁННЫЙ
    механизм (это комментарий, а не использование шины).
    """

    files = _source_files()
    assert files, "не найдены исходники зоны events"
    forbidden_modules = (
        "webhook_deliveries",
        "WebhookDeliveryRow",
        "WebhookSubscriptionRow",
        "WebhookService",
        "application.external",
        "core.infra.kafka_manager",
        "KafkaManager",
        "celery",
        "prodavan.jobs",
    )
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
                imported.extend(alias.name for alias in node.names)
        for name in imported:
            for forbidden in forbidden_modules:
                assert forbidden not in name, (
                    f"{path.name}: запрещённый импорт {name!r} (outbox/kick/celery)"
                )

        # Строковые константы КОДА (не докстринги) не должны называть outbox
        # транспортом: ищем только литералы внутри функций/присваиваний.
        docstring_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    first = body[0]
                    docstring_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.lineno not in docstring_lines
                and "webhook_deliveries" in node.value
            ):
                pytest.fail(f"{path.name}: outbox используется как строка в коде")


def test_events_zone_does_not_depend_on_sqlalchemy_or_outbox_models() -> None:
    """Дополнительно: зона не тянет ORM/БД — только порты (F11 подключит адаптеры)."""

    files = _source_files()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(("sqlalchemy", "prodavan.infrastructure")), (
                    f"{path.name}: неожидаемая зависимость {node.module!r}"
                )


def test_topics_constants_are_canonical() -> None:
    """Ещё раз фиксируем канон: топик и DLQ (используется адаптерами F11)."""

    assert PLUGIN_EVENTS_TOPIC == "agentscale.plugin.events"
    assert dlq_topic(PLUGIN_EVENTS_TOPIC) == "agentscale.plugin.events.dlq"
    # ensure_topic обязателен: функция существует и требует TopicAdminPort.
    assert callable(ensure_topic)
