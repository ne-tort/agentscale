"""Unit-тесты activator'а плагинов (задача 09, критерии 13 и 14 v4).

Покрытие по ТЗ:
* 0→1 с ожиданием Ready;
* конкурентные ``wake`` не создают второй scale;
* Ready не наступил → ``WakeTimeout`` + возврат в 0 + аудит, НЕ «успех»;
* активный ``job_id`` блокирует ``should_suspend``;
* после ``complete_job`` и истечения TTL → suspend;
* просроченный lease job'а не блокирует suspend вечно;
* wake выдаёт НОВЫЙ токен (не истёкший из Secret);
* ``max_concurrent_wakes`` ограничивает число одновременных пробуждений.

Дополнительно: fail-closed на ошибках scale/mint/аудита, ``hold_request``
удерживает до Ready и проксирует (интерфейс задачи 11), состояние frozen,
валидация политики.

ВАЖНО (verification freeze, §9 v4 / §0 плана): тесты **написаны, но не
запускались** — ни pytest, ни сборки, ни линт, ни компиляция, ни git.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta

import pytest

from prodavan.application.plugins.activation import (
    ActivationConflict,
    ActivationError,
    ActivationErrorCode,
    ActivationState,
    DesiredState,
    IdlePolicy,
    InMemoryStateStore,
    LeaseExpired,
    NotReady,
    PluginActivator,
    ScaleUnavailable,
    WakeTimeout,
)
from prodavan.application.plugins.activation.ports import MintedClaims
from .fakes import (
    BlockingProxy,
    FakeAudit,
    FakeClock,
    FakeMint,
    FakeProxy,
    FakeReadiness,
    FakeScale,
    GateScale,
)

INSTANCE = "inst_equipment_001"


def _policy(**over: float | int) -> IdlePolicy:
    base = dict(
        idle_ttl_seconds=60.0,
        ready_timeout_seconds=5.0,
        max_concurrent_wakes=2,
        ready_poll_interval_seconds=0.5,
        job_lease_ttl_seconds=30.0,
    )
    base.update(over)
    return IdlePolicy(**base)  # type: ignore[arg-type]


class Harness:
    """Сборка activator'а с детерминированными фейками."""

    def __init__(
        self,
        *,
        policy: IdlePolicy | None = None,
        scale: FakeScale | None = None,
        readiness: FakeReadiness | None = None,
        mint: FakeMint | None = None,
        audit: FakeAudit | None = None,
        proxy: FakeProxy | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.scale = scale or FakeScale()
        self.readiness = readiness or FakeReadiness()
        self.mint = mint or FakeMint()
        self.mint.bind_clock(self.clock)
        self.audit = audit or FakeAudit()
        self.store = InMemoryStateStore()
        self.proxy = proxy
        self.activator = PluginActivator(
            scale=self.scale,
            readiness=self.readiness,
            clock=self.clock,
            mint=self.mint,
            audit=self.audit,
            policy=policy or _policy(),
            state_store=self.store,
            proxy=proxy,
        )

    async def state(self) -> ActivationState:
        return await self.store.load(INSTANCE)


# ---------------------------------------------------------------------------
# Обязательный state_store: молчаливого InMemory production default больше нет
# ---------------------------------------------------------------------------


def test_state_store_is_required_without_in_memory_default() -> None:
    """Без явного state_store activator не собирается (typed failure до service use)."""
    with pytest.raises(TypeError, match="state_store"):
        PluginActivator(  # type: ignore[call-arg]
            scale=FakeScale(),
            readiness=FakeReadiness(),
            clock=FakeClock(),
            mint=FakeMint(),
            audit=FakeAudit(),
            policy=_policy(),
        )


def test_explicit_in_memory_state_store_is_used_without_duplicate() -> None:
    """Явный test/dev double используется как есть, второй store не создаётся."""
    h = Harness()
    assert h.activator._store is h.store


# ---------------------------------------------------------------------------
# 0→1 с ожиданием Ready (критерий 13)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wake_from_zero_holds_until_ready() -> None:
    """0→1: запрос удерживается до Ready, затем выдаётся новый токен."""
    h = Harness(readiness=FakeReadiness(become_ready_after=3))

    result = await h.activator.wake(INSTANCE)

    assert h.scale.calls == [1], "ровно один PATCH replicas: 1"
    assert h.readiness.checks == 3, "polling Ready продолжался до факта готовности"
    assert h.clock.sleeps, "между пробами было ожидание, не busy loop"
    assert result.previous_replicas == 0
    assert result.waited_seconds > 0
    assert result.idempotent_hit is False

    state = await h.state()
    assert state.desired is DesiredState.ACTIVE
    assert state.wake_in_flight is False
    assert state.last_active_at is not None

    # Wake succeeded → в аудит ушло намерение и факт, без токенов.
    wake_events = [e for e in h.audit.events if e.event_type == "plugin_wake_up"]
    assert len(wake_events) == 2
    assert all(e.outcome == "allowed" for e in wake_events)
    assert all(result.token not in repr(e) for e in wake_events)


@pytest.mark.asyncio
async def test_hold_request_forwards_only_after_ready() -> None:
    """hold_request держит HTTP-запрос до Ready и только потом проксирует."""
    proxy = FakeProxy()
    h = Harness(
        readiness=FakeReadiness(become_ready_after=2),
        proxy=proxy,
    )

    response = await h.activator.hold_request(INSTANCE, {"method": "GET", "path": "/api/v1/items"})

    assert response == {"status": 200, "proxied": True}
    assert len(proxy.forwards) == 1
    assert h.readiness.checks == 2, "проксирование началось после факта Ready"
    assert proxy.forwards[0]["jti"] == h.mint.issued[0].jti
    assert proxy.forwards[0]["request"] == {"method": "GET", "path": "/api/v1/items"}
    # touch до и после forward: idle-счётчик от завершения запроса.
    state = await h.state()
    assert state.last_active_at == h.clock.now()


@pytest.mark.asyncio
async def test_hold_request_without_proxy_fails_closed() -> None:
    """Реальный proxy делает задача 11: его отсутствие — явная ошибка, не 200."""
    h = Harness(readiness=FakeReadiness(ready=True))
    with pytest.raises(ActivationError) as exc:
        await h.activator.hold_request(INSTANCE, {})
    assert exc.value.code == ActivationErrorCode.PROXY_MISSING.value


# ---------------------------------------------------------------------------
# Конкурентные wake не создают второй scale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_wakes_do_not_scale_twice() -> None:
    """Два параллельных wake: один лидер, второй ждёт, PATCH 1 ровно один."""
    h = Harness(readiness=FakeReadiness(become_ready_after=2))

    first, second = await asyncio.gather(
        h.activator.wake(INSTANCE),
        h.activator.wake(INSTANCE),
    )

    assert h.scale.calls == [1], "второй wake НЕ должен делать scale_to(1)"
    assert h.scale.scale_ups() == 1
    assert {first.idempotent_hit, second.idempotent_hit} == {False, True}
    # Каждый запрос получает СВОЙ свежий jti, даже при одном PATCH.
    assert first.jti != second.jti
    assert len(h.mint.calls) == 2
    assert (await h.state()).wake_in_flight is False


@pytest.mark.asyncio
async def test_sequential_wake_on_active_plugin_reuses_scale() -> None:
    """Повторный wake на ACTIVE плагине не дёргает scale, но обновляет токен."""
    h = Harness(readiness=FakeReadiness(ready=True))

    first = await h.activator.wake(INSTANCE)
    second = await h.activator.wake(INSTANCE)

    assert h.scale.calls == [1]
    assert second.idempotent_hit is True
    assert first.jti != second.jti
    assert second.token != first.token


# ---------------------------------------------------------------------------
# Ready не наступил → WakeTimeout + возврат в 0 + аудит, НЕ «успех»
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wake_timeout_returns_to_zero_and_audits_denial() -> None:
    """Таймаут Ready = отказ, откат replicas и append-only denial."""
    h = Harness(
        policy=_policy(ready_timeout_seconds=2.0, ready_poll_interval_seconds=0.5),
        readiness=FakeReadiness(ready=False),
    )

    with pytest.raises(WakeTimeout) as exc:
        await h.activator.wake(INSTANCE)

    assert exc.value.code == ActivationErrorCode.WAKE_TIMEOUT.value
    assert h.scale.calls == [1, 0], "после неуспеха обязателен возврат в replicas: 0"
    assert (await h.state()).desired is DesiredState.SUSPENDED
    assert (await h.state()).wake_in_flight is False

    denied = h.audit.denied()
    assert len(denied) == 1
    assert denied[0].code == ActivationErrorCode.WAKE_TIMEOUT.value
    assert denied[0].event_type == "plugin_wake_up"


@pytest.mark.asyncio
async def test_readiness_probe_error_is_not_success() -> None:
    """Упавшая проба Ready — это отказ с откатом, а не «считаем готовым»."""
    h = Harness(readiness=FakeReadiness(raise_error=True))

    with pytest.raises(WakeTimeout):
        await h.activator.wake(INSTANCE)

    assert h.scale.calls == [1, 0]
    assert h.audit.denied()[0].code == ActivationErrorCode.WAKE_TIMEOUT.value


@pytest.mark.asyncio
async def test_scale_unavailable_is_explicit_error() -> None:
    """Недоступный k8s scale → ScaleUnavailable, без молчаливого успеха."""
    scale = FakeScale()
    scale.fail_on = 1
    h = Harness(scale=scale, readiness=FakeReadiness(ready=True))

    with pytest.raises(ScaleUnavailable) as exc:
        await h.activator.wake(INSTANCE)
    assert exc.value.code == ActivationErrorCode.SCALE_UNAVAILABLE.value
    assert (await h.state()).desired is DesiredState.SUSPENDED


@pytest.mark.asyncio
async def test_mint_failure_rolls_back_to_zero() -> None:
    """Срыв выпуска PluginClaims = system failure, Deployment не остаётся живым."""
    h = Harness(mint=FakeMint(broken=True), readiness=FakeReadiness(ready=True))

    with pytest.raises(ActivationError) as exc:
        await h.activator.wake(INSTANCE)
    assert exc.value.code == ActivationErrorCode.MINT_INVALID.value
    assert h.scale.calls == [1, 0]


@pytest.mark.asyncio
async def test_audit_failure_blocks_positive_mutation() -> None:
    """Append failure для позитивной мутации = deny, PATCH 0→1 не выполняется."""
    h = Harness(audit=FakeAudit(fail=True), readiness=FakeReadiness(ready=True))

    with pytest.raises(ActivationError) as exc:
        await h.activator.wake(INSTANCE)
    assert exc.value.code == ActivationErrorCode.AUDIT_UNAVAILABLE.value
    assert h.scale.calls == [], "до масштабирования дело не дошло"


@pytest.mark.asyncio
async def test_not_ready_on_active_plugin_is_explicit() -> None:
    """Плагин помечен ACTIVE, но pod потерял Ready → NotReady, не успех по флагу."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)

    h.readiness.ready = False
    with pytest.raises(NotReady):
        await h.activator.wake(INSTANCE)


# ---------------------------------------------------------------------------
# Критерий 14: активный job_id блокирует усыпление
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_job_blocks_suspend() -> None:
    """Idle TTL истёк, но активный job с живым lease запрещает should_suspend."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)
    await h.activator.register_job(INSTANCE, "job_xlsx_export", lease_ttl_seconds=300.0)

    h.clock.advance(120.0)  # TTL (60 с) давно истёк

    assert await h.activator.should_suspend(INSTANCE) is False
    assert await h.activator.suspend(INSTANCE) is False
    assert h.scale.calls == [1], "scale_to(0) при активном job не вызывался"
    denied = h.audit.denied()
    assert any(e.code == ActivationErrorCode.ACTIVATION_CONFLICT.value for e in denied)


@pytest.mark.asyncio
async def test_complete_job_then_idle_ttl_suspends() -> None:
    """После complete_job и истечения TTL → should_suspend=True и scale_to(0)."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)
    await h.activator.register_job(INSTANCE, "job_xlsx_export", lease_ttl_seconds=300.0)

    h.clock.advance(30.0)
    assert await h.activator.should_suspend(INSTANCE) is False  # lease ещё жив

    await h.activator.complete_job(INSTANCE, "job_xlsx_export")
    h.clock.advance(70.0)  # от последней активности > 60 с

    assert await h.activator.should_suspend(INSTANCE) is True
    assert await h.activator.suspend(INSTANCE) is True
    assert h.scale.calls == [1, 0]
    assert (await h.state()).desired is DesiredState.SUSPENDED
    assert (await h.state()).active_job_ids == frozenset()
    assert await h.activator.suspend(INSTANCE) is False, "повторное усыпление идемпотентно"


@pytest.mark.asyncio
async def test_expired_job_lease_does_not_block_suspend_forever() -> None:
    """Просроченный lease снимается: плагин не «залипает» в ACTIVE навсегда."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)
    await h.activator.register_job(INSTANCE, "job_orphan", lease_ttl_seconds=10.0)

    h.clock.advance(70.0)  # и TTL, и lease истекли; complete_job не вызывался

    assert await h.activator.should_suspend(INSTANCE) is True
    assert await h.activator.suspend(INSTANCE) is True
    state = await h.state()
    assert state.active_job_ids == frozenset()
    assert state.job_leases == {}
    assert h.scale.calls == [1, 0]


@pytest.mark.asyncio
async def test_renew_expired_lease_raises_and_prunes() -> None:
    """Heartbeat по истёкшему lease → LeaseExpired, запись удалена."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)
    await h.activator.register_job(INSTANCE, "job_orphan", lease_ttl_seconds=10.0)

    h.clock.advance(11.0)
    with pytest.raises(LeaseExpired) as exc:
        await h.activator.renew_job_lease(INSTANCE, "job_orphan")
    assert exc.value.code == ActivationErrorCode.LEASE_EXPIRED.value
    assert (await h.state()).active_job_ids == frozenset()


@pytest.mark.asyncio
async def test_renew_live_lease_extends_it() -> None:
    """Продление живого lease не даёт job'у «выпасть» из реестра."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)
    await h.activator.register_job(INSTANCE, "job_long", lease_ttl_seconds=20.0)

    h.clock.advance(15.0)
    renewed = await h.activator.renew_job_lease(INSTANCE, "job_long", lease_ttl_seconds=30.0)
    assert renewed.expires_at == h.clock.now() + timedelta(seconds=30.0)
    assert await h.activator.should_suspend(INSTANCE) is False


@pytest.mark.asyncio
async def test_register_job_heartbeat_refreshes_lease() -> None:
    """Повторная регистрация того же job_id продлевает аренду, а не дублирует."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)
    await h.activator.register_job(INSTANCE, "job_long", lease_ttl_seconds=20.0)

    h.clock.advance(15.0)
    await h.activator.register_job(INSTANCE, "job_long", lease_ttl_seconds=20.0)

    state = await h.state()
    assert state.active_job_ids == frozenset({"job_long"})
    assert len(state.job_leases) == 1
    assert state.job_leases["job_long"].expires_at > h.clock.now()


@pytest.mark.asyncio
async def test_complete_job_is_idempotent() -> None:
    """Двойное завершение job'а не падает и не ломает состояние."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)
    await h.activator.register_job(INSTANCE, "job_x")
    await h.activator.complete_job(INSTANCE, "job_x")
    await h.activator.complete_job(INSTANCE, "job_x")
    assert (await h.state()).active_job_ids == frozenset()


# ---------------------------------------------------------------------------
# Wake выдаёт НОВЫЙ токен (не истёкший из Secret)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wake_mints_fresh_token_not_expired_secret_one() -> None:
    """Токен из Secret истёк задолго до wake; результат — новый короткий жетон."""
    h = Harness(readiness=FakeReadiness(ready=True), mint=FakeMint(ttl_seconds=300.0))
    # Имитируем «истёкший токен в Secret»: он выпущен 10 минутами раньше и
    # уже невалиден на момент пробуждения. Activator его НЕ читает вообще.
    stale = MintedClaims(
        token="token.stale_from_secret",
        jti="jti_stale",
        issued_at=h.clock.now() - timedelta(seconds=900),
        expires_at=h.clock.now() - timedelta(seconds=600),
    )
    assert stale.expired_at(now=h.clock.now())

    result = await h.activator.wake(INSTANCE)

    assert result.token == h.mint.issued[0].token
    assert result.jti == h.mint.issued[0].jti
    assert result.token != stale.token
    assert result.jti != stale.jti
    assert result.expires_at > result.issued_at
    assert (result.expires_at - result.issued_at).total_seconds() == 300.0
    assert stale.token not in repr(h.audit.events), "старый токен не утекает в аудит"


@pytest.mark.asyncio
async def test_expired_mint_result_is_rejected() -> None:
    """MintPort вернул уже истёкший токен → отказ + откат, не проксирование."""
    class ExpiredMint(FakeMint):
        async def mint(self, *, plugin_instance_id: str, reason: str) -> MintedClaims:
            claims = await super().mint(plugin_instance_id=plugin_instance_id, reason=reason)
            return replace(
                claims,
                expires_at=claims.issued_at - timedelta(seconds=1),
            )

    h = Harness(mint=ExpiredMint(), readiness=FakeReadiness(ready=True))
    with pytest.raises(ActivationError) as exc:
        await h.activator.wake(INSTANCE)
    assert exc.value.code == ActivationErrorCode.MINT_INVALID.value
    assert h.scale.calls == [1, 0]


# ---------------------------------------------------------------------------
# max_concurrent_wakes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_concurrent_wakes_limits_parallel_wakeups() -> None:
    """Одновременно будится не более N плагинов; лишние получают конфликт."""
    scale = GateScale()
    h = Harness(
        # interval должен быть <= timeout: иначе IdlePolicy обоснованно
        # отвергает конфигурацию (ни одной пробы Ready до таймаута).
        policy=_policy(
            max_concurrent_wakes=1,
            ready_timeout_seconds=0.2,
            ready_poll_interval_seconds=0.1,
        ),
        scale=scale,
        readiness=FakeReadiness(ready=True),
    )

    # Лидер занимает единственный слот и висит на PATCH scale_to(1).
    first = asyncio.create_task(h.activator.wake("inst_a"))
    await asyncio.wait_for(scale.entered.wait(), timeout=1.0)

    # Второй плагин не может получить слот: семафор занят лидером, бюджет
    # ожидания слота ограничен серверным ready_timeout.
    with pytest.raises(ActivationConflict) as exc:
        await h.activator.wake("inst_b")
    assert exc.value.code == ActivationErrorCode.ACTIVATION_CONFLICT.value
    assert any(
        e.code == ActivationErrorCode.ACTIVATION_CONFLICT.value for e in h.audit.denied()
    )

    scale.release()
    result = await asyncio.wait_for(first, timeout=2.0)
    assert result.previous_replicas == 0


@pytest.mark.asyncio
async def test_two_parallel_wakes_with_limit_two_both_succeed() -> None:
    """Лимит 2 разрешает ровно два одновременных 0→1 (граница не занижена)."""
    h = Harness(
        policy=_policy(max_concurrent_wakes=2, ready_timeout_seconds=2.0),
        readiness=FakeReadiness(ready=True),
    )
    results = await asyncio.gather(
        h.activator.wake("inst_1"),
        h.activator.wake("inst_2"),
    )
    assert [r.previous_replicas for r in results] == [0, 0]
    assert len(h.mint.calls) == 2
    assert h.audit.denied() == []


# ---------------------------------------------------------------------------
# Состояние, политика, fail-closed валидация
# ---------------------------------------------------------------------------


def test_activation_state_is_frozen_and_immutable() -> None:
    """ActivationState — frozen dataclass: прямое присваивание запрещено."""
    state = ActivationState(plugin_instance_id=INSTANCE)
    with pytest.raises(FrozenInstanceError):
        state.desired = DesiredState.ACTIVE  # type: ignore[misc]
    assert state.active_job_ids == frozenset()
    assert state.desired is DesiredState.SUSPENDED


def test_activation_state_rejects_naive_datetime() -> None:
    """Naive datetime — молчаливый баг idle TTL: отвергаем на конструкторе."""
    with pytest.raises(ActivationError) as exc:
        ActivationState(
            plugin_instance_id=INSTANCE,
            last_active_at=datetime(2026, 11, 8, 6, 0, 0),  # без tzinfo
        )
    assert exc.value.code == ActivationErrorCode.STATE_INVALID.value


def test_activation_state_rejects_job_registry_mismatch() -> None:
    """Рассинхрон active_job_ids и job_leases — ошибка, а не тихое исправление."""
    with pytest.raises(ActivationError):
        ActivationState(
            plugin_instance_id=INSTANCE,
            active_job_ids=frozenset({"job_1"}),
        )


@pytest.mark.parametrize(
    "over",
    [
        {"idle_ttl_seconds": 0},
        {"ready_timeout_seconds": -1},
        {"max_concurrent_wakes": 0},
        {"ready_poll_interval_seconds": 0},
        {"job_lease_ttl_seconds": 0},
        {"ready_poll_interval_seconds": 10.0, "ready_timeout_seconds": 1.0},
    ],
)
def test_idle_policy_rejects_invalid_values(over: dict) -> None:
    """Невалидная политика = отказ конфигурации, а не усыпление посреди запроса."""
    with pytest.raises(ActivationError) as exc:
        _policy(**over)  # type: ignore[arg-type]
    assert exc.value.code == ActivationErrorCode.STATE_INVALID.value


@pytest.mark.asyncio
async def test_should_suspend_false_without_activity_data() -> None:
    """Нет last_active_at → не усыпляем (нет достоверных данных, fail-closed)."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.register_job(INSTANCE, "job_1")
    assert await h.activator.should_suspend(INSTANCE) is False


@pytest.mark.asyncio
async def test_suspend_respects_fresh_activity() -> None:
    """touch обновляет last_active_at и отодвигает усыпление."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)

    h.clock.advance(40.0)
    await h.activator.touch(INSTANCE)
    h.clock.advance(30.0)  # от wake 70 с, но от touch всего 30 (< TTL 60)

    assert await h.activator.should_suspend(INSTANCE) is False
    h.clock.advance(31.0)
    assert await h.activator.should_suspend(INSTANCE) is True


@pytest.mark.asyncio
async def test_active_jobs_helper_filters_expired_leases() -> None:
    """active_jobs() отдаёт только живые lease'ы для родительского idle-sweep."""
    h = Harness(readiness=FakeReadiness(ready=True))
    await h.activator.wake(INSTANCE)
    await h.activator.register_job(INSTANCE, "job_a", lease_ttl_seconds=100.0)
    await h.activator.register_job(INSTANCE, "job_b", lease_ttl_seconds=10.0)

    h.clock.advance(20.0)
    state = await h.state()
    assert h.activator.active_jobs(state, now=h.clock.now()) == frozenset({"job_a"})


@pytest.mark.asyncio
async def test_wake_timeout_cannot_be_extended_by_caller() -> None:
    """Серверный потолок ready_timeout: клиентский timeout=1000 не растягивает."""
    h = Harness(
        policy=_policy(ready_timeout_seconds=1.0, ready_poll_interval_seconds=0.5),
        readiness=FakeReadiness(ready=False),
    )
    started = h.clock.now()
    with pytest.raises(WakeTimeout):
        await h.activator.wake(INSTANCE, timeout=1000.0)
    assert h.clock.now() - started <= timedelta(seconds=1.5), (
        "клиент не может растянуть серверный ready_timeout"
    )


@pytest.mark.asyncio
async def test_wake_in_flight_cleared_after_failure() -> None:
    """После WakeTimeout плагин не залипает: повторный wake возможен."""
    h = Harness(
        policy=_policy(ready_timeout_seconds=1.0, ready_poll_interval_seconds=0.5),
        readiness=FakeReadiness(ready=False),
    )
    with pytest.raises(WakeTimeout):
        await h.activator.wake(INSTANCE)
    assert (await h.state()).wake_in_flight is False

    h.readiness.ready = True
    result = await h.activator.wake(INSTANCE)
    assert result.previous_replicas == 0
    # Первая попытка: 1 → откат 0; вторая: 1. Лишних PATCH быть не должно.
    assert h.scale.calls == [1, 0, 1]


@pytest.mark.asyncio
async def test_hold_request_blocks_until_proxy_releases() -> None:
    """hold_request реально удерживает ответ, пока proxy не отдал результат."""
    proxy = BlockingProxy()
    h = Harness(readiness=FakeReadiness(ready=True), proxy=proxy)

    task = asyncio.create_task(h.activator.hold_request(INSTANCE, {"path": "/export"}))
    await asyncio.wait_for(proxy.started.wait(), timeout=1.0)
    assert task.done() is False, "запрос удерживается до ответа backend'а"

    proxy.release()
    assert await asyncio.wait_for(task, timeout=1.0) == {"status": 200, "proxied": True}
