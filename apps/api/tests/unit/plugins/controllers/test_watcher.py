"""Unit-тесты ``GrantChangeWatcher`` (дебаунс массовых grant'ов).

Acceptance-критерии задачи:
  * подписка на изменения ``CapabilityGrant`` → вызов ``sync()``;
  * дебаунс: 5 изменений подряд схлопываются в ОДИН sync (не 5 перезагрузок БД);
  * trailing-edge по окну тишины + ``max_wait`` при непрерывном потоке;
  * ошибка ``poll()``/``sync()`` не глотается (fail-closed);
  * аудит watcher'а не содержит секретов/DSN.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЛИСЬ (verification freeze, план миграции §0.2).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.infrastructure.plugins.controllers.errors import SyncError
from prodavan.infrastructure.plugins.controllers.ports import (
    GrantChangeEvent,
    SyncReceipt,
)
from prodavan.infrastructure.plugins.controllers.watcher import (
    AUDIT_EVENT_WATCHER,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_MAX_WAIT_SECONDS,
    GrantChangeWatcher,
)

from .conftest import FakeAudit, FakeClock, FakeEventPort


def _receipt(grant_count: int = 1) -> SyncReceipt:
    return SyncReceipt(
        grant_count=grant_count,
        changed_steps=("tenant_hba_patch",),
        reloaded_steps=("tenant_pg_pg_reload_conf",),
        occurred_at=datetime(2026, 11, 8, 6, 0, 0, tzinfo=UTC),
    )


class _SyncSpy:
    """Callable-шпион вместо реального контроллера."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def __call__(self) -> SyncReceipt:
        self.calls += 1
        if self.fail:
            raise SyncError(step="tenant_hba_patch", reason="reload_failed")
        return _receipt()


def _watcher(
    events: FakeEventPort,
    sync: _SyncSpy,
    clock: FakeClock,
    **kw: object,
) -> GrantChangeWatcher:
    return GrantChangeWatcher(
        events=events,
        sync=sync,
        clock=clock,
        debounce_seconds=kw.get("debounce_seconds", DEFAULT_DEBOUNCE_SECONDS),
        max_wait_seconds=kw.get("max_wait_seconds", DEFAULT_MAX_WAIT_SECONDS),
        audit=kw.get("audit"),
    )


# --------------------------------------------------------------------------- дебаунс


def test_five_changes_collapse_into_single_sync() -> None:
    """5 событий grant подряд → ОДИН sync, а не пять перезагрузок БД."""
    events = FakeEventPort(
        queue=[GrantChangeEvent(kind="grant", grant_id=f"g{i}") for i in range(5)]
    )
    sync = _SyncSpy()
    clock = FakeClock(t=10.0)
    watcher = _watcher(events, sync, clock)

    first = watcher.tick()  # забрали 5 событий, окно тишины ещё не истекло
    assert first.synced is False
    assert first.events_seen == 5
    assert sync.calls == 0
    assert watcher.pending == 5

    clock.advance(DEFAULT_DEBOUNCE_SECONDS)
    second = watcher.tick()
    assert second.synced is True
    assert second.events_seen == 5
    assert sync.calls == 1
    assert watcher.pending == 0


def test_debounce_uses_trailing_edge_window() -> None:
    """Каждое новое событие ОТДВИГАЕТ окно тишины (классический debounce)."""
    events = FakeEventPort(queue=[GrantChangeEvent(kind="grant", grant_id="g1")])
    sync = _SyncSpy()
    clock = FakeClock(t=0.0)
    watcher = _watcher(events, sync, clock)

    watcher.tick()
    clock.advance(0.4)
    events.queue = [GrantChangeEvent(kind="grant", grant_id="g2")]
    watcher.tick()  # новое событие: last_event_at = 0.4, sync всё ещё не пора
    assert sync.calls == 0

    clock.advance(0.4)
    assert watcher.tick().synced is False  # прошло 0.4 < 0.5
    clock.advance(0.1)
    outcome = watcher.tick()  # 0.5 тишины с последнего события
    assert outcome.synced is True
    assert outcome.events_seen == 2
    assert sync.calls == 1


def test_max_wait_forces_sync_under_continuous_events() -> None:
    """Непрерывный поток не может отложить sync бесконечно (max_wait)."""
    events = FakeEventPort(queue=[GrantChangeEvent(kind="grant", grant_id="g1")])
    sync = _SyncSpy()
    clock = FakeClock(t=0.0)
    watcher = _watcher(events, sync, clock, debounce_seconds=0.5, max_wait_seconds=2.0)

    watcher.tick()
    for step in range(3):
        clock.advance(0.4)
        events.queue = [GrantChangeEvent(kind="grant", grant_id=f"g{step}")]
        watcher.tick()
    # С момента ПЕРВОГО события прошло 1.2 с, до max_wait ещё далеко.
    assert sync.calls == 0
    clock.advance(1.0)
    events.queue = [GrantChangeEvent(kind="grant", grant_id="g_last")]
    outcome = watcher.tick()  # 2.2 с с первого события → max_wait сработал
    assert outcome.synced is True
    assert sync.calls == 1


def test_no_events_means_no_sync() -> None:
    """Пустой poll не дёргает контроллер и не пишет лишний аудит."""
    events = FakeEventPort()
    sync = _SyncSpy()
    clock = FakeClock()
    watcher = _watcher(events, sync, clock)
    outcome = watcher.tick()
    assert outcome.synced is False
    assert sync.calls == 0
    assert watcher.pending == 0


def test_run_once_sleeps_remaining_window() -> None:
    """``run_once`` блокирующе ждёт остаток окна и выполняет sync."""
    events = FakeEventPort(queue=[GrantChangeEvent(kind="grant", grant_id="g1")])
    sync = _SyncSpy()
    clock = FakeClock(t=0.0)
    watcher = _watcher(events, sync, clock)
    outcome = watcher.run_once()
    assert outcome.synced is True
    assert sync.calls == 1
    assert clock.sleeps == [pytest.approx(DEFAULT_DEBOUNCE_SECONDS)]


# --------------------------------------------------------------------------- fail-closed


def test_poll_failure_is_not_swallowed() -> None:
    """Ошибка подписки → SyncError + аудит отказа, а не тихий пропуск grant'а."""
    events = FakeEventPort()
    events.fail = True
    audit = FakeAudit()
    watcher = _watcher(events, _SyncSpy(), FakeClock(), audit=audit)
    with pytest.raises(SyncError) as exc_info:
        watcher.tick()
    assert exc_info.value.reason == "poll_failed"
    assert audit.events
    assert audit.events[0]["event"] == AUDIT_EVENT_WATCHER
    assert audit.events[0]["decision"] == "deny"


def test_sync_error_propagates_from_watcher() -> None:
    """SyncError контроллера пробрасывается, «успех» не возвращается."""
    events = FakeEventPort(queue=[GrantChangeEvent(kind="grant", grant_id="g1")])
    sync = _SyncSpy(fail=True)
    clock = FakeClock(t=0.0)
    audit = FakeAudit()
    watcher = _watcher(events, sync, clock, audit=audit)
    with pytest.raises(SyncError) as exc_info:
        watcher.run_once()
    assert exc_info.value.reason == "reload_failed"
    # Ни одного «allow»-аудита: неудачный sync не считается успехом.
    assert not any(e.get("decision") == "allow" for e in audit.events)
    # События остаются в pending: при следующей попытке sync повторится.
    assert watcher.pending == 1


def test_missing_clock_is_adapter_missing() -> None:
    """Без ClockPort watcher не собирается (fail-closed по конструкции)."""
    with pytest.raises(SyncError):
        GrantChangeWatcher(
            events=FakeEventPort(),
            sync=_SyncSpy(),
            clock=None,  # type: ignore[arg-type]
        )


def test_invalid_debounce_is_rejected() -> None:
    """Нулевое/отрицательное окно дебаунса — явная ошибка конфигурации."""
    for value in (0, -1):
        with pytest.raises(ValueError):
            GrantChangeWatcher(
                events=FakeEventPort(),
                sync=_SyncSpy(),
                clock=FakeClock(),
                debounce_seconds=value,
            )


def test_bad_event_object_is_contract_violation() -> None:
    """Произвольный объект вместо GrantChangeEvent → отказ, а не игнор."""
    events = FakeEventPort(queue=[object()])  # type: ignore[list-item]
    with pytest.raises(SyncError) as exc_info:
        _watcher(events, _SyncSpy(), FakeClock()).tick()
    assert exc_info.value.reason == "event_contract_violation"


def test_watcher_audit_has_no_secrets() -> None:
    """Аудит watcher'а — только канонические поля, без grant_id/секретов/DSN."""
    events = FakeEventPort(queue=[GrantChangeEvent(kind="grant", grant_id="g_secret")])
    audit = FakeAudit()
    clock = FakeClock(t=0.0)
    watcher = _watcher(events, _SyncSpy(), clock, audit=audit)
    watcher.run_once()
    for event in audit.events:
        text = repr(event)
        for forbidden in ("g_secret", "SCRAM-SHA-256", "postgresql://", "dsn", "password"):
            assert forbidden not in text
        assert event["event"] == AUDIT_EVENT_WATCHER
        assert event["decision"] in ("allow", "deny")
