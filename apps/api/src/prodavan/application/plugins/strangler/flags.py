"""Фича-флаг strangler'а ``plugin_enabled_modules[]`` (F10, §8 этап 2, §11 кр. 16).

Это **сторона чтения** флага: политика, которой ``StranglerRouter`` решает, по какому
пути пустить запрос. Запись значения уже реализована в чужом каталоге
``application/plugins/admin/service.py::PluginAdminService.set_enabled_modules``
и ``admin/ports.py::GrantStorePort.set_enabled_modules`` — здесь она НЕ дублируется.
Родитель (F11) подключает ``EnabledModulesPolicy`` к admin-роутеру, передав адаптер
к тому же хранилищу grant'ов.

Контекст v4: «всё под нож» — про КОНЕЧНОЕ СОСТОЯНИЕ (declarative-app удалён
полностью), а не про способ. Способ — strangler по контекстам ``mod_equipment`` за
фича-флагом. Это dev: данные эфемерны, переноса/бэкапов/dual-read/dual-write/parity/
«недель наблюдения» НЕТ. Выходы старого и нового путей НЕ сравниваются.

Fail-closed: если хранилище недоступно (порт отсутствует, метод бросил исключение,
вернул неожиданный тип), модуль считается **НЕ включённым** и запрос остаётся на
старом legacy-пути. Это безопаснее, чем «включить всё»: при сбое инфраструктуры
не происходит неожиданного переключения на ещё не готовый plugin-путь.

ЯВНЫЙ ЗАПРЕТ (§8, §13.1, план миграции §4): в публичном API этого модуля нет и не
может появиться полей/методов ``dual_read``, ``dual_write``, ``parity``, ``snapshot``,
``migrate_data`` (и их синонимов). Порт умеет ровно три операции: ``enabled``,
``set``, ``list``. Тесты проверяют отсутствие запрещённых имён.
"""

from __future__ import annotations

import threading
from typing import Iterable, Protocol, runtime_checkable


@runtime_checkable
class FeatureFlagStorePort(Protocol):
    """Порт хранилища фича-флага (БД / Redis / in-memory).

    Структурный контракт. Реализация обязана возвращать достоверный ответ только
    при точно известном состоянии; любая неопределённость/ошибка обрабатывается
    ``EnabledModulesPolicy`` как fail-closed (``enabled -> False``).

    Три операции и ничего более: чтение одного модуля, запись списка, чтение
    списка. Никаких операций переноса данных, снимков или параллельного чтения —
    это не dual-run, а переключатель пути.
    """

    def enabled(self, company_id: str, plugin_id: str) -> bool:
        """``True`` только если модуль явно включён у компании."""
        ...

    def set(self, company_id: str, plugin_ids: Iterable[str]) -> None:
        """Записать список включённых модулей (для тестов/админского адаптера)."""
        ...

    def list(self, company_id: str) -> tuple[str, ...]:
        """Текущий список ``plugin_enabled_modules[]`` компании."""
        ...


class InMemoryFeatureFlagStore:
    """Простая реализация порта для unit-тестов и однопроцессного dev-стенда.

    Не источник истины в кластере: там родитель подключает адаптер к БД-grant'ам.
    Потокобезопасна (``Lock``), чтобы чтение флага шло параллельно с admin-записью.
    """

    def __init__(self, *, initial: dict[str, Iterable[str]] | None = None) -> None:
        self._by_company: dict[str, frozenset[str]] = {
            company: frozenset(str(item) for item in plugins)
            for company, plugins in (initial or {}).items()
        }
        self._lock = threading.Lock()

    def enabled(self, company_id: str, plugin_id: str) -> bool:
        key = (company_id or "").strip()
        plugin = (plugin_id or "").strip()
        if not key or not plugin:
            # Пустой идентификатор не может быть включён — fail-closed.
            return False
        with self._lock:
            return plugin in self._by_company.get(key, frozenset())

    def set(self, company_id: str, plugin_ids: Iterable[str]) -> None:
        key = (company_id or "").strip()
        if not key:
            raise ValueError("company_id must not be empty")
        normalized = frozenset(str(item) for item in plugin_ids)
        with self._lock:
            self._by_company[key] = normalized

    def list(self, company_id: str) -> tuple[str, ...]:
        key = (company_id or "").strip()
        if not key:
            return ()
        with self._lock:
            return tuple(sorted(self._by_company.get(key, frozenset())))


class EnabledModulesPolicy:
    """Fail-closed политика чтения ``plugin_enabled_modules[]`` для роутера.

    Родитель подключает адаптер к хранилищу grant'ов через ``__init__``/``bind``.
    Пока порт не подключён, ``is_enabled`` возвращает ``False`` — запросы идут по
    legacy-пути, а не «куда-то ещё». Исключение порта и неожиданный тип ответа
    трактуются так же: остаёмся на старом пути (§10 матрица рисков, «Сломанный
    cutover»: откат = выключить флаг).

    ВАЖНО: это НЕ ``GrantStorePort`` из admin-слоя и НЕ ``EnabledModulesReceipt``.
    Там — запись флага и аудит операции; здесь — только решение о маршруте. Никакой
    миграции/переноса/сверки данных здесь нет по §8 / §13.1.
    """

    def __init__(self, store: FeatureFlagStorePort | None = None) -> None:
        self._store = store

    @property
    def store(self) -> FeatureFlagStorePort | None:
        """Подключённый порт (``None`` = fail-closed, store недоступен)."""
        return self._store

    def bind(self, store: FeatureFlagStorePort) -> None:
        """Подключить реализацию порта (вызывает родитель при интеграции F11)."""
        self._store = store

    def is_enabled(self, company_id: str, plugin_id: str) -> bool:
        """``True`` только при подтверждённом включении модуля у компании.

        Fail-closed по всем трём причинам: порт отсутствует, порт бросил исключение,
        порт вернул не ``bool``. В последнем случае «истиноподобное» значение НЕ
        считается включением — только явный ``True``.
        """
        key = (company_id or "").strip()
        plugin = (plugin_id or "").strip()
        if not key or not plugin or self._store is None:
            return False
        try:
            answer = self._store.enabled(key, plugin)
        except Exception:
            # Хранилище недоступно ≠ «включить всё». Остаёмся на legacy-пути.
            return False
        # Строгая проверка типа: 1 / "yes" / объект не являются разрешением.
        return answer is True

    def enabled_modules(self, company_id: str) -> tuple[str, ...]:
        """Список включённых модулей; при недоступном хранилище — пустой.

        Вспомогательный метод для диагностики/админского роутера. Он тоже
        fail-closed: ошибка ``list`` даёт ``()``, а не «всё включено».
        """
        key = (company_id or "").strip()
        if not key or self._store is None:
            return ()
        try:
            value = self._store.list(key)
        except Exception:
            return ()
        if not isinstance(value, (tuple, list, frozenset, set)):
            return ()
        try:
            return tuple(str(item) for item in value)
        except Exception:
            return ()


# Явный перечень запрещённых имён: используется и в документации, и в тестах.
# Ни одно из них не должно появляться в публичном API пакета strangler.
FORBIDDEN_PARITY_NAMES: frozenset[str] = frozenset(
    {
        "dual_read",
        "dual_write",
        "parity",
        "snapshot",
        "migrate_data",
        "migrate_to_plugin",
        "data_migration",
        "compare_results",
        "parity_check",
    }
)


__all__ = [
    "FORBIDDEN_PARITY_NAMES",
    "EnabledModulesPolicy",
    "FeatureFlagStorePort",
    "InMemoryFeatureFlagStore",
]
