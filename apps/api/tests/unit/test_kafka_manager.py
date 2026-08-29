"""Unit tests — KafkaManager buffer + event envelopes."""

from __future__ import annotations

import pytest

from prodavan.core.events.bus import publish_platform_event, publish_project_trigger
from prodavan.core.events.envelope import platform_envelope, project_trigger_envelope
from prodavan.core.infra.kafka_manager import KafkaManager, set_kafka_manager


def test_envelope_shapes() -> None:
    pe = platform_envelope(event_id="e1", event_type="project.created", company_id="c1")
    assert pe.bus == "platform"
    assert pe.to_dict()["schema_version"] == 1
    te = project_trigger_envelope(event_id="t1", kind="chat.message", project_id="p1")
    assert te.bus == "project_trigger"
    assert te.event_type == "chat.message"


@pytest.mark.asyncio
async def test_kafka_manager_buffer_only_publish() -> None:
    set_kafka_manager(None)
    mgr = KafkaManager(enabled=False)
    await mgr.startup()
    assert mgr.enabled is False
    assert await mgr.health() is None
    ok = await publish_platform_event(
        event_id="evt_1",
        event_type="company.suspended",
        company_id="co_1",
        payload={"reason": "test"},
    )
    assert ok is True
    recent = mgr.recent_envelopes()
    assert len(recent) == 1
    assert recent[0]["bus"] == "platform"
    assert recent[0]["event_id"] == "evt_1"

    ok2 = await publish_project_trigger(
        event_id="trg_1",
        kind="chat.message",
        project_id="proj_1",
        company_id="co_1",
        payload={"text": "hi"},
    )
    assert ok2 is True
    assert len(mgr.recent_envelopes()) == 2
    assert mgr.recent_envelopes()[-1]["bus"] == "project_trigger"
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_kafka_enabled_without_bootstrap_stays_buffer() -> None:
    set_kafka_manager(None)
    mgr = KafkaManager(enabled=True, bootstrap_servers=None, required=False)
    await mgr.startup()
    assert mgr.enabled is False
    assert await mgr.health() is False
    await mgr.publish(platform_envelope(event_id="x", event_type="project.created"))
    assert len(mgr.recent_envelopes()) == 1
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_kafka_kick_drain_increments_async(monkeypatch: pytest.MonkeyPatch) -> None:
    set_kafka_manager(None)
    mgr = KafkaManager(enabled=False)
    calls: list[dict] = []

    def _fake_enqueue() -> dict:
        calls.append({"ok": True})
        return {"enqueued": False, "reason": "celery_disabled"}

    monkeypatch.setattr(
        "prodavan.core.jobs.enqueue.enqueue_trigger_drain",
        _fake_enqueue,
    )
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: None,
    )
    await mgr._kick_drain()
    assert mgr.drain_kicks == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_kafka_kick_drain_skips_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    set_kafka_manager(None)
    mgr = KafkaManager(enabled=False, drain_debounce_sec=2.0)
    calls: list[dict] = []

    def _fake_enqueue() -> dict:
        calls.append({"ok": True})
        return {"enqueued": True}

    class _Redis:
        enabled = True

    monkeypatch.setattr(
        "prodavan.core.jobs.enqueue.enqueue_trigger_drain",
        _fake_enqueue,
    )
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: _Redis(),
    )

    async def _no_lock(*_a, **_k):
        return None

    monkeypatch.setattr("prodavan.core.infra.cache.acquire_lock", _no_lock)
    await mgr._kick_drain()
    assert mgr.drain_kicks == 0
    assert calls == []


def test_kafka_dispatch_enqueue_increments(monkeypatch: pytest.MonkeyPatch) -> None:
    set_kafka_manager(None)
    mgr = KafkaManager(enabled=False, consumer_mode="dispatch")
    assert mgr.consumer_mode == "dispatch"
    calls: list[str] = []

    def _fake_dispatch(trigger_id: str) -> dict:
        calls.append(trigger_id)
        return {"enqueued": False, "reason": "celery_disabled", "trigger_id": trigger_id}

    monkeypatch.setattr(
        "prodavan.core.jobs.enqueue.enqueue_dispatch_trigger",
        _fake_dispatch,
    )
    mgr._enqueue_dispatch("trg_abc")
    assert mgr.dispatch_enqueues == 1
    assert calls == ["trg_abc"]


def test_kafka_consumer_mode_defaults_to_kick() -> None:
    mgr = KafkaManager(enabled=False, consumer_mode="weird")
    assert mgr.consumer_mode == "kick"


@pytest.mark.asyncio
async def test_kafka_ensure_topics_creates_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = KafkaManager(
        enabled=True,
        bootstrap_servers="localhost:9092",
        topic_platform_events="plat",
        topic_project_triggers="trig",
    )
    created: list[str] = []

    class _Admin:
        async def start(self) -> None:
            return None

        async def list_topics(self) -> set[str]:
            return {"plat"}

        async def create_topics(self, topics: list) -> None:
            created.extend(t.name for t in topics)

        async def close(self) -> None:
            return None

    class _NewTopic:
        def __init__(self, name: str, num_partitions: int, replication_factor: int) -> None:
            self.name = name

    import sys
    from types import ModuleType

    admin_mod = ModuleType("aiokafka.admin")
    admin_mod.AIOKafkaAdminClient = lambda **kwargs: _Admin()  # type: ignore[attr-defined]
    admin_mod.NewTopic = _NewTopic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiokafka.admin", admin_mod)

    await mgr._ensure_topics()
    assert created == [
        "trig",
        "prodavan.auth.commands",
        "prodavan.auth.events",
        "prodavan.relation.events",
        "prodavan.metrics.events",
    ]
