"""In-memory реализация ``StateStorePort`` (задача 09, §7.4 v4).

Годится для одного процесса Plugin Gateway, dev-стенда и unit-тестов.
Для нескольких реплик Gateway родитель (задача 11) обязан подключить
реализацию на БД/ConfigMap с настоящим CAS: локальный ``asyncio.Lock`` не
защищает от гонки между процессами, и ``wake_in_flight`` в БД остаётся
единственным межпроцессным барьером.
"""

from __future__ import annotations

import asyncio
from typing import Mapping

from prodavan.application.plugins.activation.state import ActivationState


class InMemoryStateStore:
    """Хранилище состояний с идентификационным CAS.

    ``compare_and_swap`` сравнивает **объект**, а не поля: ``load`` возвращает
    ровно тот frozen-объект, который лежит в словаре, а любые изменения
    создают новый через ``dataclasses.replace``. Поэтому два конкурентных
    читателя не могут оба пройти CAS — второй получит ``False``.
    """

    def __init__(
        self,
        *,
        initial: Mapping[str, ActivationState] | None = None,
    ) -> None:
        self._states: dict[str, ActivationState] = dict(initial or {})
        self._lock = asyncio.Lock()

    async def load(self, plugin_instance_id: str) -> ActivationState:
        async with self._lock:
            state = self._states.get(plugin_instance_id)
            if state is None:
                # Отсутствие записи = «плагин ещё не будили», а не ошибка.
                # Fail-closed это не нарушает: desired=SUSPENDED, TTL ещё не
                # начался, усыпление без last_active_at запрещено.
                state = ActivationState(plugin_instance_id=plugin_instance_id)
                self._states[plugin_instance_id] = state
            return state

    async def save(self, state: ActivationState) -> None:
        async with self._lock:
            self._states[state.plugin_instance_id] = state

    async def compare_and_swap(
        self,
        *,
        expected: ActivationState,
        new: ActivationState,
    ) -> bool:
        if expected.plugin_instance_id != new.plugin_instance_id:
            return False
        async with self._lock:
            current = self._states.get(expected.plugin_instance_id)
            if current is not expected:
                return False
            self._states[new.plugin_instance_id] = new
            return True

    async def snapshot(self) -> dict[str, ActivationState]:
        """Только для тестов/диагностики: копия без возможности мутации."""
        async with self._lock:
            return dict(self._states)


__all__ = ["InMemoryStateStore"]
