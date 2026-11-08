"""``GrantChangeWatcher`` — подписка на изменения ``CapabilityGrant`` с дебаунсом.

Зачем: массовый grant (например, установка 5 плагинов одной компании за один
admin-запрос) порождает 5 событий. Без дебаунса каждое событие дёргало бы
``PluginInfraController.sync()`` → 5 раз ``pg_reload_conf()``, 5 раз ``RELOAD;``
PgBouncer и 5 раз hot-reload Envoy. Мало того, что это лишняя нагрузка на БД,
так ещё и гонка: пока kubelet не спроецировал ConfigMap, следующий sync читает
старое ``/data`` и может записать промежуточное состояние.

Политика дебаунса (leading-edge НЕ используется, только trailing-edge)
----------------------------------------------------------------------
* событие → пометить «есть изменения» и обновить ``last_event_at``;
* ``sync()`` вызывается ОДИН РАЗ, когда с момента ПОСЛЕДНЕГО события прошло
  ``debounce_seconds`` (окно тишины). 5 событий подряд схлопываются в один sync;
* ``max_wait_seconds`` — защита от бесконечного потока событий: даже если
  изменения идут непрерывно, sync произойдёт не позже этого времени;
* ``sync()`` читает СВЕЖУЮ выборку активных grant'ов из ``GrantStorePort``,
  поэтому сколько бы событий ни схлопнулось, в кластер попадёт финальное
  состояние, а не «пятое из пяти».

Fail-closed
-----------
* ``GrantEventPort`` и ``ClockPort`` обязательны (``None`` → ``SyncError``);
* исключение ``poll()`` НЕ глотается: watcher обязан его записать в аудит и
  пробросить, иначе «тихий пропуск» события оставит grant не применённым;
* ``SyncError`` из ``controller.sync()`` пробрасывается НАРУЖУ без подавления:
  один неудачный sync — это ошибка синхронизации, а не «ну, в следующий раз»;
* «успех» watcher'а НЕ подменяет успех контроллера: ``on_change``/audit пишут
  только канонические поля, без секретов/DSN.

Потокобезопасность: класс намеренно однопоточный (один фоновый поток/задача
вызывает ``tick()``). Никаких ``threading.Lock`` и asyncio-примитивов внутри:
контроллер сам синхронный, а параллельные вызовы ``tick()`` из двух потоков
могли бы дать два reload подряд — родитель при интеграции обязан запускать
ровно один loop на watcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from prodavan.infrastructure.plugins.controllers.errors import (
    STEP_TENANT_HBA_PATCH,
    SyncError,
)
from prodavan.infrastructure.plugins.controllers.ports import (
    ClockPort,
    GrantChangeEvent,
    GrantEventPort,
    SyncReceipt,
)

# Событие аудита watcher'а. НЕ содержит grant_id списком, секретов, DSN.
AUDIT_EVENT_WATCHER = "plugin_infra_grant_change"

#: Значения по умолчанию: 0.5 с тишины и не более 5 с ожидания при непрерывном
#: потоке. Достаточно, чтобы kubelet спроецировал ConfigMap/Secret (обычно <1 с),
#: но не заставляет ждать выданный grant десятки секунд.
DEFAULT_DEBOUNCE_SECONDS = 0.5
DEFAULT_MAX_WAIT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class WatcherOutcome:
    """Результат ОДНОГО ``tick()`` (без секретов/DSN).

    ``events_seen`` — сколько событий поглощено с прошлого успешного sync;
    ``sync`` — квитанция контроллера или ``None``, если окно дебаунса ещё идёт.
    """

    events_seen: int
    synced: bool
    receipt: SyncReceipt | None = None


@dataclass
class GrantChangeWatcher:
    """Дебаунс-обёртка над ``PluginInfraController.sync()``.

    Параметры:
      * ``events``   — порт подписки на изменения grant'ов (обязателен);
      * ``sync``     — callable, обычно ``controller.sync`` (обязателен);
      * ``clock``    — ``ClockPort`` с ``monotonic()``/``sleep()`` (обязателен);
      * ``debounce_seconds``  — окно тишины;
      * ``max_wait_seconds``  — жёсткий предел ожидания при непрерывном потоке;
      * ``audit``    — опциональный append-only приёмник (те же правила
        redaction, что у ``AuditPort`` контроллера);
      * ``on_sync``  — опциональный callback после успешного sync (метрики).

    ``sleep`` используется ТОЛЬКО в ``run_once()`` (блокирующее ожидание окна);
    ``tick()`` никогда не спит и потому детерминирован в unit-тестах с фейковыми
    часами.
    """

    events: GrantEventPort
    sync: Callable[[], SyncReceipt]
    clock: ClockPort
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS
    audit: Any = None
    on_sync: Callable[[SyncReceipt], None] | None = None

    # Внутреннее состояние (НЕ в сигнатуре dataclass-полей выше по смыслу,
    # но объявлено здесь, чтобы ``field(default_factory=...)`` работал).
    _pending: int = field(default=0, init=False, repr=False)
    _first_event_at: float | None = field(default=None, init=False, repr=False)
    _last_event_at: float | None = field(default=None, init=False, repr=False)
    _last_error: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.events is None or self.sync is None or self.clock is None:
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="adapter_missing")
        if self.debounce_seconds <= 0:
            raise ValueError("debounce_seconds must be > 0")
        if self.max_wait_seconds < self.debounce_seconds:
            raise ValueError("max_wait_seconds must be >= debounce_seconds")

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def tick(self) -> WatcherOutcome:
        """Один проход цикла: забрать события, решить, пора ли дёргать sync.

        Возвращает ``WatcherOutcome``. Если окно тишины ещё не истекло —
        ``synced=False`` (но ``events_seen`` уже учтён). Если ``sync()`` поднял
        ``SyncError`` — исключение ПРОБРАСЫВАЕТСЯ, а ``WatcherOutcome`` не
        возвращается: «пропустил неудачу и вернул успех» запрещён.
        """
        now = self.clock.monotonic()
        try:
            batch: Sequence[GrantChangeEvent] | None = self.events.poll()
        except SyncError:
            raise
        except Exception as exc:
            # poll() упал — НЕ тихий пропуск: аудит + проброс. Иначе событие
            # grant'а потеряется и доступ не вступит в силу.
            self._emit_audit("deny", events_seen=0, reason="poll_failed")
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="poll_failed") from exc

        if batch is None:
            # None вместо пустого списка — нарушение контракта порта.
            self._emit_audit("deny", events_seen=0, reason="poll_contract_violation")
            raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="poll_contract_violation")

        new_events = 0
        for event in batch:
            if not isinstance(event, GrantChangeEvent):
                self._emit_audit("deny", events_seen=0, reason="event_contract_violation")
                raise SyncError(step=STEP_TENANT_HBA_PATCH, reason="event_contract_violation")
            new_events += 1
            # В аудит НЕ кладём grant_id/kind содержимое: событие может нести
            # произвольные строки. Только факт и счётчик.
        if new_events:
            self._pending += new_events
            if self._first_event_at is None:
                self._first_event_at = now
            self._last_event_at = now

        if self._pending == 0:
            return WatcherOutcome(events_seen=0, synced=False)

        assert self._last_event_at is not None and self._first_event_at is not None
        quiet_for = now - self._last_event_at
        waited_for = now - self._first_event_at
        due = quiet_for >= self.debounce_seconds or waited_for >= self.max_wait_seconds
        if not due:
            return WatcherOutcome(events_seen=self._pending, synced=False)

        seen = self._pending
        receipt = self.sync()  # SyncError пробрасывается НАРУЖУ без подавления
        self._pending = 0
        self._first_event_at = None
        self._last_event_at = None
        self._last_error = None
        self._emit_audit("allow", events_seen=seen, reason="synced")
        if self.on_sync is not None:
            # Метрики не должны ронять уже подтверждённый sync: ошибка callback
            # НЕ маскируется как SyncError, но и не превращается в «успех без
            # следа» — контроллер уже записал свой аудит.
            try:
                self.on_sync(receipt)
            except Exception:
                pass
        return WatcherOutcome(events_seen=seen, synced=True, receipt=receipt)

    def run_once(self) -> WatcherOutcome:
        """Блокирующе дождаться ближайшего окна тишины и выполнить sync.

        Удобно для простого supervisor-цикла родителя::

            while True:
                watcher.run_once()

        Внутри — ``clock.sleep`` только на ОСТАТОК окна дебаунса; событий может
        не быть вообще, тогда ``tick()`` вернёт ``synced=False`` и цикл повторит
        ожидание. Исключения ``SyncError`` НЕ подавляются.
        """
        outcome = self.tick()
        if outcome.synced:
            return outcome
        if self._last_event_at is not None:
            remaining = self.debounce_seconds - (self.clock.monotonic() - self._last_event_at)
            if remaining > 0:
                self.clock.sleep(remaining)
        return self.tick()

    @property
    def pending(self) -> int:
        """Сколько событий ждёт схлопывания (для метрик/тестов)."""
        return self._pending

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------

    def _emit_audit(self, decision: str, *, events_seen: int, reason: str) -> None:
        """Записать watcher-аудит (best effort, без секретов/DSN).

        Ошибка аудита НЕ маскирует ``SyncError`` контроллера: это диагностика
        фонового цикла, а не часть транзакции синхронизации.
        """
        if self.audit is None or not hasattr(self.audit, "record"):
            return
        event: Mapping[str, Any] = {
            "event": AUDIT_EVENT_WATCHER,
            "decision": decision,
            "events_seen": events_seen,
            "reason": reason,
        }
        try:
            self.audit.record(event)
        except Exception:
            return


__all__ = [
    "AUDIT_EVENT_WATCHER",
    "DEFAULT_DEBOUNCE_SECONDS",
    "DEFAULT_MAX_WAIT_SECONDS",
    "GrantChangeWatcher",
    "WatcherOutcome",
]
