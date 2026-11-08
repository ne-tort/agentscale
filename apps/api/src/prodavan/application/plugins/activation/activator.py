"""PluginActivator — lifecycle плагина без SandboxClaim (задача 09, §7.4 v4).

Контекст v4: ``SandboxClaim`` для плагинов ОТВЕРГНУТ — CRD не несёт
image/port, требует ``warmPoolRef``, ``envVarsInjectionPolicy: Disallowed``,
тащит PVC 5Gi, а as-built ``ensure_running`` (``agent_sandbox/runtime.py:336-348``)
**не ждёт Ready**: Gateway получил бы connection-refused на первый запрос.
Решение — обычный Deployment ``replicas: 0`` + activator, который:

* патчит scale 0→1, **удерживает** запрос до Ready и только потом проксирует;
* при не наступившем Ready — ``WakeTimeout`` + возврат в 0 + аудит, НЕ «успех»;
* ведёт idle TTL и не усыпляет при активном ``job_id`` (критерий 14);
* выпускает **новый короткий** ``PluginClaims`` при каждом wake (критерий 13);
* ограничивает число одновременных пробуждений (риск cold start,
  доминируемого pull-ом образа — §7.4 / §10).

Argo: ``ignoreDifferences /spec/replicas``, новый CRD не вводится.

Fail-closed: отсутствие любого порта, истёкший токен, просроченный lease,
недоступный scale, не записанный аудит или не пришедший Ready — явная ошибка.
Никаких «считаем готовым» и «тихо вернёмся без прокси».
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from prodavan.application.plugins.activation.errors import (
    ActivationConflict,
    ActivationError,
    ActivationErrorCode,
    LeaseExpired,
    NotReady,
    ScaleUnavailable,
    WakeTimeout,
)
from prodavan.application.plugins.activation.ports import (
    ActivationAuditEvent,
    AuditPort,
    ClockPort,
    MintPort,
    MintedClaims,
    ProxyPort,
    ReadinessPort,
    ScalePort,
    StateStorePort,
)
from prodavan.application.plugins.activation.state import (
    ActivationState,
    DesiredState,
    IdlePolicy,
    JobLease,
    WakeResult,
)
from prodavan.application.plugins.activation.state_store import InMemoryStateStore

logger = logging.getLogger(__name__)


class PluginActivator:
    """Управляет пробуждением и засыпанием backend'а плагинов.

    Все публичные методы — корутины. Блокировка на ``instance_id`` защищает
    короткие критические секции (чтение/CAS/запись состояния); ``wake_in_flight``
    в состоянии (CAS через ``StateStorePort``) — барьер между процессами
    Gateway, а ``asyncio.Event`` — сигнал завершения wake для последователей
    внутри процесса. Ожидание Ready НЕ держит блокировку: иначе второй wake
    не смог бы ни узнать о пробуждении, ни дождаться его (взаимный дедлок).

    ``state_store`` можно не передавать: тогда используется in-process
    ``InMemoryStateStore`` (dev/тесты). В проде родитель обязан передать
    реализацию на БД/ConfigMap с настоящим CAS — см. ``state_store.py``.
    """

    def __init__(
        self,
        *,
        scale: ScalePort,
        readiness: ReadinessPort,
        clock: ClockPort,
        mint: MintPort,
        audit: AuditPort,
        policy: IdlePolicy,
        state_store: StateStorePort | None = None,
        proxy: ProxyPort | None = None,
    ) -> None:
        self._scale = scale
        self._readiness = readiness
        self._clock = clock
        self._mint = mint
        self._audit = audit
        self._policy = policy
        self._store = state_store or InMemoryStateStore()
        self._proxy = proxy

        self._locks: dict[str, asyncio.Lock] = {}
        self._wake_events: dict[str, asyncio.Event] = {}
        self._wake_failures: dict[str, str] = {}
        # Семафор считает ТОЛЬКО реальные 0→1-пробуждения: повторные вызовы
        # на уже ACTIVE плагине в него не входят, поэтому лимит не мешает
        # обслуживать трафик на прогретом backend'е.
        self._wake_semaphore = asyncio.Semaphore(policy.max_concurrent_wakes)

    # ------------------------------------------------------------------ wake

    async def wake(self, instance_id: str, *, timeout: float | None = None) -> WakeResult:
        """0→1, ожидание Ready, выпуск НОВОГО короткого ``PluginClaims``.

        ``timeout`` (секунды) — сколько **всего** готовы ждать входящего
        запроса; при ``None`` берётся ``policy.ready_timeout_seconds``.
        ``ready_timeout`` политики остаётся серверным потолком: клиент не
        может растянуть ожидание бесконечно (§4.4 «таймаут задаётся сервером»).

        Идемпотентность при конкурентных wake: флаг ``wake_in_flight``
        выставляется через CAS, остальные вызовы **ждут** завершения первого и
        возвращают fresh-токен, но НЕ делают второго ``scale_to(1)``.
        """
        started_at = self._now()
        budget = self._resolve_timeout(timeout)

        # --- короткая критическая секция: выбираем «лидера» пробуждения ---
        async with self._lock_for(instance_id):
            state = await self._store.load(instance_id)

            # Флаг в полёте проверяем ПЕРВЫМ: обработчик wake мог уже пометить
            # desired=ACTIVE, но ещё не снять wake_in_flight. Иначе второй
            # конкурентный вызов прошёл бы по ветке «уже ACTIVE» и сделал бы
            # лишний PATCH scale.
            if state.wake_in_flight:
                waiter = self._wake_events.get(instance_id)
                leader = False
            elif state.desired is DesiredState.ACTIVE:
                # Уже ACTIVE и нет пробуждения в полёте: перевыпускаем токен
                # (Secret почти наверняка хранит истёкший) без повторного PATCH.
                return await self._wake_already_active(state)
            else:
                claimed = state.with_wake_in_flight(True)
                if await self._store.compare_and_swap(expected=state, new=claimed):
                    waiter = asyncio.Event()
                    self._wake_events[instance_id] = waiter
                    self._wake_failures.pop(instance_id, None)
                    leader = True
                else:
                    # CAS проиграл: другой процесс/поток уже поднял флаг.
                    waiter = self._wake_events.get(instance_id)
                    leader = False

        if not leader:
            return await self._await_in_flight_wake(
                instance_id,
                waiter=waiter,
                started_at=started_at,
                budget=budget,
            )

        failure: str | None = None
        try:
            return await self._perform_wake(
                instance_id,
                started_at=started_at,
                budget=budget,
            )
        except Exception as exc:
            failure = type(exc).__name__
            raise
        finally:
            # Флаг снимается в любом исходе, иначе плагин навсегда залипнет
            # в «пробуждении». Сигнал последователям выставляется после
            # фиксации состояния — они увидят консистентную картину.
            async with self._lock_for(instance_id):
                await self._clear_wake_in_flight(instance_id)
                event = self._wake_events.pop(instance_id, None)
                if failure is not None:
                    self._wake_failures[instance_id] = failure
            if event is not None:
                event.set()

    async def _perform_wake(
        self,
        instance_id: str,
        *,
        started_at: datetime,
        budget: float,
    ) -> WakeResult:
        previous_replicas = await self._read_replicas(instance_id)

        # Лимит одновременных 0→1 (cold start доминируется pull-ом образа:
        # десятки параллельных пробуждений вытягивают образ и съедают квоту).
        # wait_for использует монотонное время цикла событий — это страховка
        # от зависшего владельца слота, а не бизнес-бюджет готовности.
        remaining = budget - (self._now() - started_at).total_seconds()
        if remaining <= 0:
            self._record(
                "plugin_wake_up",
                instance_id,
                outcome="denied",
                code=ActivationErrorCode.ACTIVATION_CONFLICT.value,
                reason="бюджет ожидания исчерпан до получения слота пробуждения",
            )
            raise ActivationConflict(plugin_instance_id=instance_id)
        try:
            await asyncio.wait_for(self._wake_semaphore.acquire(), timeout=remaining)
        except TimeoutError as exc:
            self._record(
                "plugin_wake_up",
                instance_id,
                outcome="denied",
                code=ActivationErrorCode.ACTIVATION_CONFLICT.value,
                reason="лимит одновременных пробуждений исчерпан",
            )
            raise ActivationConflict(
                plugin_instance_id=instance_id,
                detail="Превышен max_concurrent_wakes при ожидании слота пробуждения",
            ) from exc

        try:
            # Намерение 0→1 фиксируется ДО самого PATCH scale: если
            # append-only аудит недоступен, до мутации дело не доходит (§4.5).
            await self._record_wake_started(instance_id, previous_replicas=previous_replicas)
            await self._scale_up(instance_id)
            ready_at = await self._await_ready(
                instance_id,
                started_at=started_at,
                budget=budget,
            )
            claims = await self._mint_fresh(instance_id)

            # Аудит успеха — ДО мутации: append failure для позитивной
            # мутации = system failure (§4.5), а не «уже подняли, ладно».
            self._record(
                "plugin_wake_up",
                instance_id,
                outcome="allowed",
                code=ActivationErrorCode.ALLOWED.value,
                resource=f"deployment/{instance_id}",
            )
            await self._mark_active(instance_id, at=ready_at)

            waited = (ready_at - started_at).total_seconds()
            return WakeResult(
                plugin_instance_id=instance_id,
                token=claims.token,
                jti=claims.jti,
                claims=claims.claims,
                issued_at=claims.issued_at,
                expires_at=claims.expires_at,
                ready_at=ready_at,
                waited_seconds=max(0.0, waited),
                previous_replicas=previous_replicas,
            )
        except WakeTimeout:
            # _await_ready уже записал denial и выполнил возврат в 0.
            raise
        except ActivationError:
            await self._restore_zero(instance_id, previous_replicas)
            raise
        except Exception as exc:
            # Любая непредвиденная ошибка позитивной мутации = system failure:
            # откатываем replicas, чтобы не оставить «вечно живой» backend.
            logger.exception("plugin wake failed instance_id=%s", instance_id)
            await self._restore_zero(instance_id, previous_replicas)
            self._record(
                "plugin_wake_up",
                instance_id,
                outcome="denied",
                code=ActivationErrorCode.UNEXPECTED.value,
                reason=type(exc).__name__,
            )
            raise ActivationError(
                code=ActivationErrorCode.UNEXPECTED.value,
                detail=f"Пробуждение плагина {instance_id} прервано: {type(exc).__name__}",
                extra={"plugin_instance_id": instance_id},
            ) from exc
        finally:
            self._wake_semaphore.release()

    # ------------------------------------------------------- hold / proxy

    async def hold_request(self, instance_id: str, request: Any) -> Any:
        """Удержать входящий HTTP-запрос до Ready, затем проксировать.

        Реальный proxy делает задача 11 (Plugin Gateway): здесь зафиксирован
        только контракт. Отсутствие ``ProxyPort`` — fail-closed явная ошибка,
        а не «вернуть 200 без тела».

        Запрос удерживается **до фактического Ready** (критерий 13): сначала
        ``wake`` с готовым результатом, затем ``forward``. Никакого «пустим
        сразу, авось под поднимется».
        """
        if self._proxy is None:
            raise ActivationError(
                code=ActivationErrorCode.PROXY_MISSING.value,
                title="Plugin proxy not configured",
                status=503,
                detail="ProxyPort не подключён: реализуется задачей 11 (Plugin Gateway)",
                extra={"plugin_instance_id": instance_id},
            )
        result = await self.wake(instance_id)
        claims = MintedClaims(
            token=result.token,
            jti=result.jti,
            issued_at=result.issued_at,
            expires_at=result.expires_at,
            claims=result.claims,
        )
        if claims.expired_at(now=self._now()):
            raise ActivationError(
                code=ActivationErrorCode.MINT_INVALID.value,
                title="Plugin claims expired",
                status=503,
                detail="Выпущенный при wake токен уже истёк до проксирования",
                extra={"plugin_instance_id": instance_id},
            )
        # touch до отправки: длинный запрос не должен быть остановлен idle TTL.
        await self.touch(instance_id)
        try:
            return await self._proxy.forward(
                plugin_instance_id=instance_id,
                request=request,
                claims=claims,
            )
        finally:
            # После ответа тоже обновляем активность: idle-счётчик начинается
            # от завершения запроса, а не от его начала.
            await self.touch(instance_id)

    # -------------------------------------------------------- idle / suspend

    async def touch(self, instance_id: str) -> None:
        """Обновить ``last_active_at`` (каждый удержанный/проксируемый запрос)."""
        async with self._lock_for(instance_id):
            await self._mutate_state(
                instance_id,
                lambda st: st.with_activity(at=self._now()),
            )

    async def should_suspend(self, instance_id: str) -> bool:
        """Idle TTL ИСТЁК **И** активных ``job_id`` НЕТ (критерий 14).

        Отличие от as-built ``project_service/idle_pause.py:25-40``: тот считает
        idle только по agent-сессиям/событиям и не видит фоновые job'ы. Здесь
        реестр job'ов с lease — часть состояния плагина.

        Fail-closed: нет ``last_active_at`` → ``False`` (нет достоверных данных),
        просроченные lease'ы снимаются, ``wake_in_flight`` → ``False``,
        ``desired=SUSPENDED`` → ``False`` (не дёргаем scale повторно).
        """
        now = self._now()
        async with self._lock_for(instance_id):
            state = await self._store.load(instance_id)
            pruned = state.pruned_leases(now=now)
            if pruned is not state:
                await self._mutate_state(instance_id, lambda _st: pruned)
            return self._suspend_allowed(pruned, now=now)

    async def suspend(self, instance_id: str) -> bool:
        """``scale_to(0)`` с запретом при активных job'ах.

        Возвращает ``True`` при реально выполненном усыплении, ``False`` когда
        усыпление запрещено/не требуется. Fail-closed: ошибка scale
        пробрасывается как ``ScaleUnavailable``, а не «считаем что уже спит».
        """
        now = self._now()
        async with self._lock_for(instance_id):
            state = await self._store.load(instance_id)
            pruned = state.pruned_leases(now=now)
            if pruned is not state:
                await self._mutate_state(instance_id, lambda _st: pruned)

            if pruned.desired is DesiredState.SUSPENDED:
                return False
            if pruned.wake_in_flight or pruned.has_active_jobs(now=now):
                self._record(
                    "plugin_suspend",
                    instance_id,
                    outcome="denied",
                    code=ActivationErrorCode.ACTIVATION_CONFLICT.value,
                    reason="активные job'ы или пробуждение в полёте",
                )
                return False
            if not self._idle_expired(pruned, now=now):
                return False

            # Решение об усыплении пишется ДО мутации: если append-only sink
            # недоступен, до scale дело вообще не доходит (§4.5).
            self._record(
                "plugin_suspend",
                instance_id,
                outcome="allowed",
                code=ActivationErrorCode.ALLOWED.value,
                reason="решение об усыплении по idle TTL",
                resource=f"deployment/{instance_id}",
            )
            await self._scale_down(instance_id)
            await self._mutate_state(
                instance_id,
                lambda st: st.with_desired(DesiredState.SUSPENDED).with_wake_in_flight(False),
            )
            # Факт исполнения — отдельное событие. Откатывать 0→1 здесь было
            # бы опаснее, чем сообщить о сбое аудита: состояние уже честно
            # SUSPENDED, а сбой записи виден оператору.
            self._record(
                "plugin_suspend",
                instance_id,
                outcome="allowed",
                code=ActivationErrorCode.ALLOWED.value,
                reason="replicas 1->0",
                resource=f"deployment/{instance_id}",
            )
            return True

    def _suspend_allowed(self, state: ActivationState, *, now: datetime) -> bool:
        """Единая проверка для ``should_suspend`` и ``suspend``."""
        if state.desired is not DesiredState.ACTIVE:
            return False
        if state.wake_in_flight or state.has_active_jobs(now=now):
            return False
        return self._idle_expired(state, now=now)

    def _idle_expired(self, state: ActivationState, *, now: datetime) -> bool:
        """Проверка TTL без обращения к store (вызывается под блокировкой).

        Нет ``last_active_at`` → ``False``: усыпление без достоверных данных
        об активности было бы самовольным (fail-closed).
        """
        if state.last_active_at is None:
            return False
        return (now - state.last_active_at).total_seconds() >= self._policy.idle_ttl_seconds

    # ------------------------------------------------------- реестр job'ов

    async def register_job(
        self,
        instance_id: str,
        job_id: str,
        *,
        lease_ttl_seconds: float | None = None,
    ) -> JobLease:
        """Зарегистрировать активный ``job_id`` с lease.

        Повторная регистрация того же job'а **продлевает** lease (heartbeat
        воркера), а не создаёт дубль. Просроченные lease'ы других job'ов
        снимаются здесь же, чтобы реестр не рос бесконечно.
        """
        if not job_id.strip():
            raise ActivationError(
                code=ActivationErrorCode.STATE_INVALID.value,
                title="Invalid job id",
                status=422,
                detail="job_id не может быть пустым",
            )
        ttl = float(lease_ttl_seconds or self._policy.job_lease_ttl_seconds)
        if ttl <= 0 or ttl != ttl:
            raise ActivationError(
                code=ActivationErrorCode.STATE_INVALID.value,
                title="Invalid job lease",
                status=422,
                detail="lease_ttl_seconds должен быть положительным конечным числом",
            )
        now = self._now()
        lease = JobLease(
            job_id=job_id,
            registered_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        async with self._lock_for(instance_id):
            await self._mutate_state(
                instance_id,
                lambda st: st.pruned_leases(now=now).with_job(lease),
            )
            return lease

    async def complete_job(self, instance_id: str, job_id: str) -> None:
        """Снять job из реестра; отсутствующий job — идемпотентный no-op.

        Просроченный lease снимается автоматически: он **не** блокирует
        ``should_suspend`` вечно (требование задачи, риск §10).
        """
        now = self._now()
        async with self._lock_for(instance_id):
            await self._mutate_state(
                instance_id,
                lambda st: st.pruned_leases(now=now).without_job(job_id),
            )

    async def renew_job_lease(
        self,
        instance_id: str,
        job_id: str,
        *,
        lease_ttl_seconds: float | None = None,
    ) -> JobLease:
        """Heartbeat воркера: продлить lease, но **не воскрешать** истёкший.

        Просроченная аренда — ``LeaseExpired``: результат job'а нельзя принять
        молча, он мог быть передан другому воркеру и выполнен повторно.
        """
        ttl = float(lease_ttl_seconds or self._policy.job_lease_ttl_seconds)
        now = self._now()
        async with self._lock_for(instance_id):
            state = await self._store.load(instance_id)
            lease = state.job_leases.get(job_id)
            if lease is None or lease.expired_at(now=now):
                await self._mutate_state(instance_id, lambda st: st.pruned_leases(now=now))
                raise LeaseExpired(plugin_instance_id=instance_id, job_id=job_id)
            renewed = lease.renewed(now=now, ttl_seconds=ttl)
            await self._mutate_state(instance_id, lambda st: st.with_job(renewed))
            return renewed

    def active_jobs(self, state: ActivationState, *, now: datetime | None = None) -> frozenset[str]:
        """Только job'ы с живым lease (для idle-sweep родителя, критерий 14)."""
        checked_at = now or self._now()
        return frozenset(
            job_id
            for job_id, lease in state.job_leases.items()
            if not lease.expired_at(now=checked_at)
        )

    # ------------------------------------------------------------- внутреннее

    def _lock_for(self, instance_id: str) -> asyncio.Lock:
        lock = self._locks.get(instance_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[instance_id] = lock
        return lock

    def _now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ActivationError(
                code=ActivationErrorCode.STATE_INVALID.value,
                title="Invalid clock",
                status=500,
                detail="ClockPort.now() обязан вернуть aware UTC datetime",
            )
        return now

    def _resolve_timeout(self, timeout: float | None) -> float:
        # Потолок серверный: клиент может только УМЕНЬШИТЬ ожидание (§4.4).
        if timeout is None:
            return self._policy.ready_timeout_seconds
        value = min(float(timeout), self._policy.ready_timeout_seconds)
        if value <= 0 or value != value:
            raise ActivationError(
                code=ActivationErrorCode.STATE_INVALID.value,
                title="Invalid wake timeout",
                status=422,
                detail="timeout должен быть положительным конечным числом",
            )
        return value

    async def _wake_already_active(self, state: ActivationState) -> WakeResult:
        """Плагин уже ACTIVE: fresh-токен + проверка Ready без второго PATCH."""
        instance_id = state.plugin_instance_id
        try:
            ready = await self._readiness.is_ready(instance_id)
        except Exception as exc:
            raise NotReady(plugin_instance_id=instance_id, detail=str(exc)) from exc
        if not ready:
            # Потеря Ready у «живого» пода: честный отказ, не «успех по флагу».
            raise NotReady(plugin_instance_id=instance_id)
        claims = await self._mint_fresh(instance_id)
        self._record(
            "plugin_wake_up",
            instance_id,
            outcome="allowed",
            code=ActivationErrorCode.ALLOWED.value,
            resource=f"deployment/{instance_id}",
        )
        await self._mutate_state(instance_id, lambda st: st.with_activity(at=self._now()))
        replicas = await self._read_replicas(instance_id)
        return WakeResult(
            plugin_instance_id=instance_id,
            token=claims.token,
            jti=claims.jti,
            claims=claims.claims,
            issued_at=claims.issued_at,
            expires_at=claims.expires_at,
            ready_at=self._now(),
            waited_seconds=0.0,
            previous_replicas=replicas,
            idempotent_hit=True,
        )

    async def _await_in_flight_wake(
        self,
        instance_id: str,
        *,
        waiter: asyncio.Event | None,
        started_at: datetime,
        budget: float,
    ) -> WakeResult:
        """Второй конкурентный wake: ждём Ready и получаем fresh-токен.

        ВАЖНО: второй ``scale_to(1)`` НЕ делается. По завершении ожидания
        перевыпускаем токен (у каждого запроса свой ``jti``), но Deployment
        уже поднят первым вызовом.

        Внутри процесса ждём ``asyncio.Event`` (без блокировки instance —
        иначе дедлок с обработчиком). Если лидер живёт в другом процессе
        Gateway, ``Event`` отсутствует и используется опрос состояния через
        ``StateStorePort`` + ``ClockPort.sleep``.
        """
        if waiter is not None:
            try:
                await asyncio.wait_for(waiter.wait(), timeout=budget)
            except TimeoutError as exc:
                raise WakeTimeout(
                    plugin_instance_id=instance_id,
                    timeout_seconds=budget,
                    detail="Параллельное пробуждение не завершилось в отведённое время",
                ) from exc
            failure = self._wake_failures.get(instance_id)
            if failure is not None:
                raise WakeTimeout(
                    plugin_instance_id=instance_id,
                    timeout_seconds=budget,
                    detail=f"Параллельное пробуждение завершилось отказом: {failure}",
                )
        else:
            deadline = started_at + timedelta(seconds=budget)
            while True:
                state = await self._store.load(instance_id)
                if not state.wake_in_flight:
                    break
                if self._now() >= deadline:
                    raise WakeTimeout(
                        plugin_instance_id=instance_id,
                        timeout_seconds=budget,
                        detail="Параллельное пробуждение не завершилось в отведённое время",
                    )
                await self._clock.sleep(min(self._policy.ready_poll_interval_seconds, budget))

        # Даже после сигнала лидера Ready проверяется заново: «флаг снят»
        # не равняется «фактически готов» (fail-closed, критерий 13).
        try:
            ready = await self._readiness.is_ready(instance_id)
        except Exception as exc:
            raise NotReady(plugin_instance_id=instance_id, detail=str(exc)) from exc
        if not ready:
            raise NotReady(
                plugin_instance_id=instance_id,
                detail="Параллельное пробуждение завершилось, но backend не Ready",
            )
        claims = await self._mint_fresh(instance_id)
        replicas = await self._read_replicas(instance_id)
        return WakeResult(
            plugin_instance_id=instance_id,
            token=claims.token,
            jti=claims.jti,
            claims=claims.claims,
            issued_at=claims.issued_at,
            expires_at=claims.expires_at,
            ready_at=self._now(),
            waited_seconds=max(0.0, (self._now() - started_at).total_seconds()),
            previous_replicas=replicas,
            idempotent_hit=True,
        )

    async def _await_ready(
        self,
        instance_id: str,
        *,
        started_at: datetime,
        budget: float,
    ) -> datetime:
        """Polling Ready до ``policy.ready_timeout_seconds`` / ``timeout``.

        При истечении — ``WakeTimeout`` + возврат в 0 + аудит (НЕ «успех»).
        """
        deadline = started_at + timedelta(seconds=budget)
        interval = self._policy.ready_poll_interval_seconds
        while True:
            now = self._now()
            try:
                ready = await self._readiness.is_ready(instance_id)
            except Exception as exc:
                # Ошибка пробы ≠ «не готов»: это сбой инфраструктуры, но для
                # входящего запроса результат тот же — откат и явный отказ.
                await self._fail_ready_timeout(
                    instance_id,
                    reason=f"ошибка пробы Ready: {type(exc).__name__}",
                )
                raise WakeTimeout(
                    plugin_instance_id=instance_id,
                    timeout_seconds=budget,
                    detail=f"Проба Ready плагина {instance_id} завершилась ошибкой: {type(exc).__name__}",
                ) from exc
            if ready:
                return now
            if now >= deadline:
                await self._fail_ready_timeout(
                    instance_id,
                    reason="Ready не наступил до истечения таймаута",
                )
                raise WakeTimeout(
                    plugin_instance_id=instance_id,
                    timeout_seconds=budget,
                )
            remaining = (deadline - now).total_seconds()
            await self._clock.sleep(min(interval, max(0.0, remaining), budget))

    async def _fail_ready_timeout(self, instance_id: str, *, reason: str) -> None:
        """Откат 1→0 + append-only аудит отказа. Исключения аудита не глушатся."""
        await self._restore_zero(instance_id, previous_replicas=0)
        self._record(
            "plugin_wake_up",
            instance_id,
            outcome="denied",
            code=ActivationErrorCode.WAKE_TIMEOUT.value,
            reason=reason,
            resource=f"deployment/{instance_id}",
        )

    async def _scale_up(self, instance_id: str) -> None:
        await self._apply_scale(instance_id, replicas=1)

    async def _scale_down(self, instance_id: str) -> None:
        await self._apply_scale(instance_id, replicas=0)

    async def _apply_scale(self, instance_id: str, *, replicas: int) -> None:
        try:
            await self._scale.scale_to(instance_id, replicas)
        except Exception as exc:
            self._record(
                "plugin_scale",
                instance_id,
                outcome="denied",
                code=ActivationErrorCode.SCALE_UNAVAILABLE.value,
                reason=type(exc).__name__,
                resource=f"deployment/{instance_id}",
            )
            raise ScaleUnavailable(
                plugin_instance_id=instance_id,
                detail=f"scale_to({replicas}) недоступен: {type(exc).__name__}",
            ) from exc

    async def _restore_zero(self, instance_id: str, previous_replicas: int) -> None:
        """Вернуть ``replicas: 0`` после неуспешного wake, если до него было 0.

        Если плагин уже был ACTIVE (previous_replicas > 0), откат не делаем:
        это был не 0→1, а повторная попытка на уже живом поде.
        """
        if previous_replicas != 0:
            return
        try:
            await self._scale.scale_to(instance_id, 0)
        except Exception:
            # Уже в ветке отказа: не маскируем первичную ошибку, но явно
            # логируем неудавшийся откат — оператор увидит «залипшую» реплику.
            logger.exception(
                "plugin wake rollback scale_to(0) failed instance_id=%s", instance_id
            )

    async def _read_replicas(self, instance_id: str) -> int:
        try:
            return int(await self._scale.current_replicas(instance_id))
        except Exception as exc:
            raise ScaleUnavailable(
                plugin_instance_id=instance_id,
                detail=f"current_replicas недоступен: {type(exc).__name__}",
            ) from exc

    async def _mint_fresh(self, instance_id: str) -> MintedClaims:
        """Выпустить новый короткий ``PluginClaims`` и проверить его свежесть."""
        try:
            claims = await self._mint.mint(
                plugin_instance_id=instance_id,
                reason="wake",
            )
        except ActivationError:
            raise
        except Exception as exc:
            raise ActivationError(
                code=ActivationErrorCode.MINT_INVALID.value,
                title="Plugin claims mint failed",
                status=503,
                detail=f"Не удалось выпустить PluginClaims: {type(exc).__name__}",
                extra={"plugin_instance_id": instance_id},
            ) from exc
        if not claims.token.strip() or not claims.jti.strip():
            raise ActivationError(
                code=ActivationErrorCode.MINT_INVALID.value,
                title="Plugin claims mint failed",
                status=503,
                detail="MintPort вернул пустой token/jti",
                extra={"plugin_instance_id": instance_id},
            )
        if claims.expired_at(now=self._now()) or claims.ttl_seconds <= 0:
            raise ActivationError(
                code=ActivationErrorCode.MINT_INVALID.value,
                title="Plugin claims mint failed",
                status=503,
                detail="MintPort вернул уже истёкший или нулевой TTL токен",
                extra={"plugin_instance_id": instance_id},
            )
        return claims

    async def _mark_active(self, instance_id: str, *, at: datetime) -> None:
        await self._mutate_state(
            instance_id,
            lambda st: st.with_desired(DesiredState.ACTIVE)
            .with_activity(at=at)
            .with_wake_in_flight(False),
        )

    async def _clear_wake_in_flight(self, instance_id: str) -> None:
        for _ in range(8):
            state = await self._store.load(instance_id)
            if not state.wake_in_flight:
                return
            if await self._store.compare_and_swap(
                expected=state,
                new=state.with_wake_in_flight(False),
            ):
                return
            await self._clock.sleep(0)
        logger.error("не удалось снять wake_in_flight instance_id=%s", instance_id)

    async def _mutate_state(
        self,
        instance_id: str,
        transform: Callable[[ActivationState], ActivationState],
    ) -> None:
        """CAS-цикл под блокировкой вызывающего: не теряем чужие изменения.

        Все мутации состояния идут через этот метод (а не через ``save``),
        иначе параллельный wake/job-реестр перезаписал бы друг друга.
        """
        for _ in range(8):
            state = await self._store.load(instance_id)
            new = transform(state)
            if new is state:
                return
            if await self._store.compare_and_swap(expected=state, new=new):
                return
            await self._clock.sleep(0)  # уступить event loop конкуренту
        raise ActivationConflict(
            plugin_instance_id=instance_id,
            detail="Не удалось атомарно обновить состояние активации",
        )

    async def _record_wake_started(self, instance_id: str, *, previous_replicas: int) -> None:
        """Записать НАМЕРЕНИЕ 0→1 до ожидания Ready (§4.5: решение ≠ факт)."""
        self._record(
            "plugin_wake_up",
            instance_id,
            outcome="allowed",
            code=ActivationErrorCode.ALLOWED.value,
            reason=f"replicas {previous_replicas}->1, ожидание Ready",
            resource=f"deployment/{instance_id}",
        )

    def _record(
        self,
        event_type: str,
        instance_id: str,
        *,
        outcome: str,
        code: str | None = None,
        reason: str | None = None,
        resource: str | None = None,
    ) -> None:
        """Append failure для позитивной мутации = system failure, не allow.

        Событие содержит только канонические идентификаторы: ни токенов, ни
        DSN, ни тел запросов, ни PII (§4.5 v4, критерий 11).
        """
        try:
            self._audit.record(
                ActivationAuditEvent(
                    event_type=event_type,
                    plugin_instance_id=instance_id,
                    occurred_at=self._now(),
                    outcome=outcome,
                    code=code,
                    reason=reason,
                    resource=resource,
                )
            )
        except Exception as exc:
            if outcome == "allowed":
                raise ActivationError(
                    code=ActivationErrorCode.AUDIT_UNAVAILABLE.value,
                    title="Plugin activation audit unavailable",
                    status=503,
                    detail="Аудит позитивного перехода не записан: мутация отклонена",
                    extra={"plugin_instance_id": instance_id},
                ) from exc
            logger.exception("plugin activation audit failed instance_id=%s", instance_id)


# ---------------------------------------------------------------------------
# Вспомогательная реализация часов для production (инжектится родителем)
# ---------------------------------------------------------------------------


class SystemClock:
    """Реальные часы: ``datetime.now(UTC)`` + ``asyncio.sleep``."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


__all__ = ["PluginActivator", "SystemClock"]
