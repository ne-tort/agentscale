"""Тестовые адаптеры портов activator'а (НЕ запускаются, verification freeze).

Все фейки — детерминированные: ``FakeClock`` двигает виртуальное время только
явно (``advance``) или при ``sleep``, поэтому проверки idle TTL, lease'ов и
таймаута Ready не зависят от реальных задержек и от скорости машины.

Ни один фейк не «подсказывает» activator'у успех: Ready по умолчанию
``False``, replicas = 0, аудит пуст. Это позволяет тестам честно проверять
fail-closed поведение, а не переопределять его настройками по умолчанию.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from prodavan.application.plugins.activation.ports import (
    ActivationAuditEvent,
    MintedClaims,
)


class FakeClock:
    """Виртуальные часы: ``sleep`` сдвигает ``now`` на запрошенный интервал."""

    def __init__(self, *, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 11, 8, 6, 0, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if seconds > 0:
            self.current += timedelta(seconds=seconds)
        # Обязательный yield в event loop: без него «конкурентные» wake в
        # asyncio.gather выполнялись бы строго последовательно и тест
        # проверял бы не ту ветку (не in-flight, а «уже ACTIVE»).
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeScale:
    """Имитация PATCH deployments/scale с полным журналом вызовов."""

    def __init__(self, *, replicas: int = 0) -> None:
        self.replicas = replicas
        self.calls: list[int] = []
        self.fail_on: int | None = None

    async def current_replicas(self, plugin_instance_id: str) -> int:
        return self.replicas

    async def scale_to(self, plugin_instance_id: str, replicas: int) -> None:
        self.calls.append(replicas)
        if self.fail_on is not None and replicas == self.fail_on:
            raise RuntimeError("k8s patch failed")
        self.replicas = replicas

    def scale_ups(self) -> int:
        return self.calls.count(1)


class FakeReadiness:
    """Очередь ответов пробы Ready + счётчик вызовов.

    ``become_ready_after`` задаёт число проверок до перехода в Ready: это и
    есть «удержание запроса до Ready» без реального ожидания.
    """

    def __init__(
        self,
        *,
        ready: bool = False,
        become_ready_after: int = 0,
        raise_error: bool = False,
    ) -> None:
        self.ready = ready
        self.become_ready_after = become_ready_after
        self.raise_error = raise_error
        self.checks = 0

    async def is_ready(self, plugin_instance_id: str) -> bool:
        self.checks += 1
        if self.raise_error:
            raise RuntimeError("readiness probe failed")
        if self.ready:
            return True
        if self.become_ready_after and self.checks >= self.become_ready_after:
            self.ready = True
            return True
        return False


class FakeMint:
    """Каждый вызов выпускает НОВЫЙ короткий токен с уникальным ``jti``."""

    def __init__(self, *, ttl_seconds: float = 300.0, broken: bool = False) -> None:
        self.ttl_seconds = ttl_seconds
        self.broken = broken
        self.calls: list[str] = []
        self.issued: list[MintedClaims] = []
        self._clock: FakeClock | None = None

    def bind_clock(self, clock: FakeClock) -> None:
        self._clock = clock

    async def mint(self, *, plugin_instance_id: str, reason: str) -> MintedClaims:
        self.calls.append(plugin_instance_id)
        if self.broken:
            raise RuntimeError("mint unavailable")
        now = self._clock.now() if self._clock is not None else datetime.now(UTC)
        jti = f"jti_{uuid4().hex}"
        claims = MintedClaims(
            token=f"token.{jti}",
            jti=jti,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            claims={
                "iss": "agentscale-plugins",
                "aud": "capability-gateway",
                "sub": f"plugin:mod_equipment:co_123:{plugin_instance_id}",
                "jti": jti,
                "grant_id": "grant_test",
            },
        )
        self.issued.append(claims)
        return claims


class FakeAudit:
    """Append-only журнал; ``fail`` имитирует недоступный sink (§4.5)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[ActivationAuditEvent] = []
        self.fail = fail

    def record(self, event: ActivationAuditEvent) -> None:
        if self.fail:
            raise RuntimeError("audit sink unavailable")
        self.events.append(event)

    def denied(self) -> list[ActivationAuditEvent]:
        return [e for e in self.events if e.outcome == "denied"]


class FakeProxy:
    """Прокси-адаптер для ``hold_request``: фиксирует факт и токен."""

    def __init__(self) -> None:
        self.forwards: list[dict[str, Any]] = []

    async def forward(
        self,
        *,
        plugin_instance_id: str,
        request: Any,
        claims: MintedClaims,
    ) -> Any:
        self.forwards.append(
            {
                "plugin_instance_id": plugin_instance_id,
                "request": request,
                "jti": claims.jti,
                "token": claims.token,
            }
        )
        return {"status": 200, "proxied": True}


class BlockingProxy(FakeProxy):
    """Прокси, который «держит» запрос до явного ``release``.

    ``started`` выставляется в момент входа в ``forward``: тест ждёт именно
    его, а не «два раза уступи event loop и надеемся, что успело».
    """

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def forward(self, **kwargs: Any) -> Any:
        self.started.set()
        await self.gate.wait()
        return await super().forward(**kwargs)

    def release(self) -> None:
        self.gate.set()


class GateScale(FakeScale):
    """Scale, блокирующий первый ``scale_to(1)`` до ``release``.

    Нужен для теста ``max_concurrent_wakes``: лидер висит на PATCH'е, поэтому
    семафор остаётся занятым и лимит проверяется без гонки по времени.
    """

    def __init__(self, *, replicas: int = 0) -> None:
        super().__init__(replicas=replicas)
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def scale_to(self, plugin_instance_id: str, replicas: int) -> None:
        if replicas == 1 and not self.gate.is_set():
            self.entered.set()
            await self.gate.wait()
        await super().scale_to(plugin_instance_id, replicas)

    def release(self) -> None:
        self.gate.set()


__all__: list[str] = [
    "BlockingProxy",
    "FakeAudit",
    "FakeClock",
    "FakeMint",
    "FakeProxy",
    "FakeReadiness",
    "FakeScale",
    "GateScale",
]
