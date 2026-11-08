"""Маршрутизация strangler'а: ровно ОДИН путь на запрос (F10, §8, §11 кр. 16).

``StranglerRouter.route`` читает фича-флаг через ``EnabledModulesPolicy`` и вызывает
**или** ``legacy_handler`` (declarative-app), **или** ``plugin_handler`` (новый
плагин). Никакого одновременного выполнения, никакого сравнения/сверки результатов,
никакого dual-read/dual-write: старый и новый пути работают независимо, переключается
только путь (§8 этап 2).

Правила:

* ``NotEnabled`` (флаг выключен, хранилище недоступно, пустой идентификатор) →
  вызывается ``legacy_handler``. Fail-closed: сбой инфраструктуры флага НЕ
  переключает на plugin-путь.
* Ошибка plugin-пути **НЕ откатывается на legacy молча**. Иначе баг нового кода
  маскировался бы «работающим» старым путём и до cutover не дошёл бы ни один
  сигнал. Вместо этого пробрасывается типизированная ``PluginRouteError`` с
  исходным исключением в ``__cause__``/``original``.
* Ошибка legacy-пути пробрасывается как есть (это существующее поведение, роутер
  его не меняет и не подменяет plugin-путём).

Обработчики — zero-arg callable; допускаются и синхронные, и корутинные (весь
API-слой асинхронный). Результат возвращается без какой-либо постобработки: роутер
не знает о природе значения и потому физически не может его «сравнить».

ВАЖНО: запись флага — не здесь (см. ``admin/service.py::set_enabled_modules``).
Родитель подключает ``EnabledModulesPolicy`` к admin-роутеру; этот класс — только
сторона чтения/исполнения.
"""

from __future__ import annotations

import inspect
from typing import Awaitable, Callable, TypeVar, Union

from prodavan.domain.errors import AppError

from .flags import EnabledModulesPolicy

T = TypeVar("T")

#: Zero-arg обработчик пути: синхронный или корутинный.
#: ``Union`` (а не ``T | ...``) — выражение вычисляется при импорте, поэтому
#: используем безопасную форму, не зависящую от ``TypeVar.__or__``.
Handler = Callable[[], Union[T, Awaitable[T]]]


class NotEnabled(Exception):
    """Сигнал решения «модуль не включён → выбрать legacy-путь».

    Намеренно отдельный тип: фиксирует причину выбора в коде и тестах, чтобы
    «флаг выключен», «хранилище недоступно» и «пустой id» не растворялись в
    безликом ``if not flag``. Роутер перехватывает этот сигнал внутри ``route``
    и вызывает ``legacy_handler``; наружу он не пробрасывается.
    """

    def __init__(self, *, company_id: str, plugin_id: str, reason: str) -> None:
        self.company_id = company_id
        self.plugin_id = plugin_id
        self.reason = reason
        super().__init__(
            f"strangler: модуль {plugin_id!r} у компании {company_id!r} не включён "
            f"({reason}); выбран legacy-путь"
        )


class PluginRouteError(AppError):
    """Типизированный отказ plugin-пути (§10 «Сломанный cutover»).

    Наследует ``AppError`` → уже зарегистрированный обработчик превращает его в
    RFC 7807 problem+json. Статус 502 выбран сознательно: это отказ вышестоящего
    (ещё не готового) обработчика, а не ошибка валидации запроса клиента (400) и
    не конфликт состояния (409, как у admin lifecycle).

    ``original`` хранит исходное исключение (оно же в ``__cause__``): диагностика
    бага плагина не теряется за обёрткой. Молчаливого fallback на legacy нет.
    """

    def __init__(
        self,
        *,
        company_id: str,
        plugin_id: str,
        original: BaseException,
        status: int = 502,
    ) -> None:
        self.original = original
        code = "PLUGIN_ROUTE_FAILED"
        super().__init__(
            code=code,
            title=code,
            status=status,
            detail=(
                f"Ошибка plugin-пути для {plugin_id!r} у компании {company_id!r}: "
                f"{type(original).__name__}: {original}. "
                "Молчаливый откат на legacy запрещён (§8, §10)."
            ),
            extra={"company_id": company_id, "plugin_id": plugin_id},
        )


class StranglerRouter:
    """Переключатель пути по ``plugin_enabled_modules[]``.

    Не держит состояния, кроме ссылки на ``EnabledModulesPolicy``. Политика
    обязательна: собрать «частично подключённый» роутер нельзя — без неё
    ``is_enabled`` всё равно fail-closed, но явная передача убирает двусмысленность
    при интеграции (план §0.5 «отсутствие интеграции = явная ошибка»).
    """

    def __init__(self, policy: EnabledModulesPolicy) -> None:
        if policy is None:
            raise ValueError(
                "EnabledModulesPolicy обязателен: роутер без политики не маршрутизирует"
            )
        self._policy = policy

    @property
    def policy(self) -> EnabledModulesPolicy:
        return self._policy

    @staticmethod
    def _decide(*, company_id: str, plugin_id: str, policy: EnabledModulesPolicy) -> None:
        """Принять решение; ``NotEnabled`` = выбрать legacy, ``None`` = plugin."""
        key = (company_id or "").strip()
        plugin = (plugin_id or "").strip()
        if not key or not plugin:
            raise NotEnabled(
                company_id=company_id,
                plugin_id=plugin_id,
                reason="пустой company_id/plugin_id",
            )
        # Fail-closed: недоступное хранилище даёт False → NotEnabled → legacy.
        if not policy.is_enabled(key, plugin):
            raise NotEnabled(
                company_id=company_id,
                plugin_id=plugin_id,
                reason="флаг plugin_enabled_modules[] выключен или хранилище недоступно",
            )

    @staticmethod
    async def _call(handler: Handler[T]) -> T:
        """Вызвать ровно один обработчик (синхронный или корутинный)."""
        result = handler()
        if inspect.isawaitable(result):
            return await result  # type: ignore[no-any-return, misc]
        return result  # type: ignore[return-value]

    async def route(
        self,
        company_id: str,
        plugin_id: str,
        *,
        legacy_handler: Handler[T],
        plugin_handler: Handler[T],
    ) -> T:
        """Вызвать ровно ОДИН из двух обработчиков и вернуть его результат.

        Порядок: решение по флагу → один вызов → возврат значения. Ни второй
        обработчик, ни сравнение результатов, ни повторная попытка не допускаются.
        """
        if not callable(legacy_handler) or not callable(plugin_handler):
            raise TypeError("legacy_handler и plugin_handler должны быть callable")

        try:
            self._decide(company_id=company_id, plugin_id=plugin_id, policy=self._policy)
        except NotEnabled:
            # Единственная ветка «выключено»: ТОЛЬКО legacy. Plugin не вызывается.
            return await self._call(legacy_handler)

        # Флаг подтверждён: ТОЛЬКО plugin. Legacy не вызывается (даже при ошибке).
        try:
            return await self._call(plugin_handler)
        except PluginRouteError:
            # Уже типизированный отказ (например, вложенный роутер) — не дублируем.
            raise
        except Exception as error:
            # Молчаливый откат на legacy ЗАПРЕЩЁН: баг плагина должен быть виден.
            raise PluginRouteError(
                company_id=company_id,
                plugin_id=plugin_id,
                original=error,
            ) from error


__all__ = [
    "Handler",
    "NotEnabled",
    "PluginRouteError",
    "StranglerRouter",
]
