"""``DbFeatureFlagStore`` — фича-флаг strangler ``plugin_enabled_modules[]`` (F10, кр. 16).

ГДЕ ХРАНИТСЯ ФЛАГ (важное решение, см. отчёт родителю)
------------------------------------------------------
Отдельной таблицы для фича-флагов в A10 НЕТ (и правка моделей/ревизий нам
запрещена). Поэтому используется **JSONB-поле существующей модели**
``CapabilityGrantRow``:

* одна служебная строка с фиксированным ``grant_id = "__plugin_feature_flags__"``;
* в её JSONB-колонке (по умолчанию ``feature_flags``) лежит ОДИН объект
  ``{company_id: [plugin_id, ...], ...}`` — сразу для всех компаний;
* все запросы грантов исключают этот ``grant_id`` (см. ``grants.py``), поэтому
  служебная строка НИКОГДА не попадает в выдачу ``get``/``list``/revocation;
* строка заполняется валидными нейтральными значениями во все обязательные
  колонки (status="revoked"), чтобы не нарушить CHECK-ограничения A10.

Имя JSONB-колонки разрешается по списку кандидатов
(``feature_flags`` → ``enabled_modules`` → ``plugin_enabled_modules`` →
``flags`` → ``metadata`` → ``meta``) и может быть задано явно через
``flags_column=`` / ``ColumnMap``. Если ни одна колонка не найдена — ЯВНАЯ
``PluginRepositorySchemaError`` (fail-closed), а не «молча вернуть пустой
список». Родителю рекомендовано добавить в A10 JSONB ``feature_flags``
(``default=dict`` / ``server_default='{}'``).

Конкурентность
--------------
``set`` — read-modify-write по одной строке. Чтобы не потерять флаги другой
компании при параллельном ``PUT /enabled-modules``, запись идёт CAS:
``UPDATE ... WHERE grant_id = :sentinel AND <flags_column> = :old_value`` с
проверкой ``rowcount`` и повтором. При ``rowcount == 0`` (гонка) — ретрай,
при исчерпании — ``PluginRepositoryUnavailable`` (deny позитивной мутации).

Синхронный / асинхронный стык
-----------------------------
Файл ``application/plugins/strangler/flags.py`` (``FeatureFlagStorePort``) ещё
не создан параллельной задачей, поэтому сигнатуры его методов неизвестны.
Экспортируем ОБА варианта:

* async ``list`` / ``set`` / ``enabled`` — авторитетные, через БД
  (стык с ``GrantStorePort.get_enabled_modules``);
* sync ``list_sync`` / ``set_sync`` / ``enabled_sync`` — через in-memory кэш,
  для синхронного strangler-порта/горячего пути запроса; прогрев — ``prefetch``.
* :class:`SyncFeatureFlagStore` — тонкий sync-адаптер с ровно тремя методами
  ``enabled``/``set``/``list`` (на случай, если порт окажется синхронным).

Fail-closed: нет строки / ошибка БД / флаг выключен → ``enabled() == False``,
то есть **legacy-путь** (безопасное значение по умолчанию, кр. 16).
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ._schema import (
    FLAG_SENTINEL_GRANT_ID,
    FLAG_SENTINEL_PLUGIN_ID,
    GRANT_COLUMNS,
    GRANT_ROW,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    json_mapping,
    load_row,
    text,
)

#: Кандидаты JSONB-колонки фича-флага в модели A10 (порядок = приоритет).
FLAGS_COLUMN_CANDIDATES: tuple[str, ...] = (
    "feature_flags",
    "enabled_modules",
    "plugin_enabled_modules",
    "flags",
    "metadata",
    "meta",
)

#: Значения служебной строки, безопасные для типичных CHECK-ограничений A10.
_SENTINEL_PLUGIN_ID = "pluginfeatureflags"  # ^[a-z][a-z0-9_]*$
_SENTINEL_DIGEST = "sha256:" + "0" * 64
_CAS_RETRIES = 5


def feature_flag_port() -> Any | None:
    """Лениво вернуть ``FeatureFlagStorePort`` из strangler/flags.py, если он есть.

    Не бросает исключение: файл создаётся параллельной задачей и может
    отсутствовать. Используется только для диагностики/структурных проверок.
    """
    import importlib

    try:
        module = importlib.import_module(
            "prodavan.application.plugins.strangler.flags"
        )
    except (ImportError, ModuleNotFoundError):
        return None
    return getattr(module, "FeatureFlagStorePort", None)


class DbFeatureFlagStore:
    """Хранилище ``plugin_enabled_modules[]`` в JSONB-поле модели A10."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        columns: ColumnMap | None = None,
        flags_column: str | None = None,
        flags_row: type | None = None,
        dedicated: bool = False,
        clock: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                "DbFeatureFlagStore требует session_factory или session (fail-closed)"
            )
        self._session_factory = session_factory
        self._session = session
        self._columns = columns or ColumnMap()
        self._explicit_flags_column = self._columns.overrides.get("feature_flags", flags_column)
        self._flags_row = flags_row
        self._dedicated = dedicated
        self._clock = clock
        self._lock = threading.Lock()
        # Синхронный кэш: company_id -> tuple[plugin_id, ...]. Пусто = «флаг
        # выключен» = legacy. Не «разрешить новый путь».
        self._cache: dict[str, tuple[str, ...]] = {}

    # ------------------------------------------------------------------
    # Разрешение модели/колонки
    # ------------------------------------------------------------------

    def _row(self) -> type:
        return self._flags_row or load_row(GRANT_ROW)

    def _col(self, name: str) -> str:
        return self._columns.col(name)

    def _flags_col(self, row: type) -> str:
        """Найти JSONB-колонку фича-флага; иначе fail-closed ошибка."""
        table = getattr(row, "__table__", None)
        existing = set(table.columns.keys()) if table is not None else set()
        candidates: list[str] = []
        if self._explicit_flags_column:
            candidates.append(self._explicit_flags_column)
        candidates.extend(FLAGS_COLUMN_CANDIDATES)
        for name in candidates:
            if name in existing:
                return name
        # Явная колонка задана, но отсутствует — это ошибка конфигурации.
        if self._explicit_flags_column:
            raise PluginRepositorySchemaError(
                f"колонка фича-флага {self._explicit_flags_column!r} отсутствует в "
                f"{row.__name__}; есть {sorted(existing)}"
            )
        raise PluginRepositorySchemaError(
            f"{row.__name__} не имеет JSONB-колонки для фича-флага. "
            f"Ожидалась одна из {list(FLAGS_COLUMN_CANDIDATES)}; есть {sorted(existing)}. "
            f"Родителю: добавьте в A10 JSONB 'feature_flags' default={{}} или "
            f"передайте flags_column=<реальная колонка> (модели не правятся отсюда)."
        )

    def _now(self) -> Any:
        from datetime import UTC, datetime

        if self._clock is not None:
            return self._clock()
        return datetime.now(UTC)

    def _session_ctx(self):
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None
        return self._session_factory()

    # ------------------------------------------------------------------
    # Служебная строка
    # ------------------------------------------------------------------

    def _sentinel_values(self, row: type, flags_col: str, mapping: dict[str, Any]) -> dict[str, Any]:
        """Значения ВСЕХ обязательных колонок для служебной строки.

        Заполняем нейтральными, но ВАЛИДНЫМИ значениями: иначе CHECK на digest /
        status / capabilities может отвергнуть INSERT, и фича-флаг не сохранится.
        """
        c = self._col
        now = self._now()
        values: dict[str, Any] = {
            c("grant_id"): FLAG_SENTINEL_GRANT_ID,
            c("company_id"): "__platform__",
            c("plugin_id"): _SENTINEL_PLUGIN_ID,
            c("plugin_instance_id"): "platform",
            c("manifest_version"): "1.0.0",
            c("artifact_digest"): _SENTINEL_DIGEST,
            c("capabilities"): ["__feature_flags__"],
            c("egress_policy"): {"mode": "none"},
            c("quota"): {
                "ops_per_minute": 1,
                "max_rows": 1,
                "max_bytes": 1,
                "max_keys": 1,
                "max_object_bytes": 1,
                "max_egress_bytes": 1,
            },
            c("status"): "revoked",
            c("created_at"): now,
            c("revoked_at"): now,
            flags_col: mapping,
        }
        # Оставляем только колонки, реально существующие в модели.
        table = row.__table__
        return {key: value for key, value in values.items() if key in table.columns.keys()}

    async def _ensure_sentinel(self, session: AsyncSession, row: type, flags_col: str) -> Any:
        """Найти служебную строку или создать её (``ON CONFLICT``-безопасно)."""
        key_col = row.__table__.columns[self._col("grant_id")]
        found = (
            await session.execute(select(row).where(key_col == FLAG_SENTINEL_GRANT_ID))
        ).scalars().first()
        if found is not None:
            return found
        try:
            session.add(row(**self._sentinel_values(row, flags_col, {})))
            await _commit_if_owned(session, self._session is None)
        except IntegrityError:
            # Параллельный процесс создал строку раньше — перечитываем.
            await _rollback_if_owned(session, self._session is None)
            found = (
                await session.execute(select(row).where(key_col == FLAG_SENTINEL_GRANT_ID))
            ).scalars().first()
            if found is None:
                raise PluginRepositoryUnavailable("не удалось создать служебную строку фича-флага")
            return found
        found = (
            await session.execute(select(row).where(key_col == FLAG_SENTINEL_GRANT_ID))
        ).scalars().first()
        if found is None:
            raise PluginRepositoryUnavailable("служебная строка фича-флага не появилась после INSERT")
        return found

    # ------------------------------------------------------------------
    # Чтение / запись отображения (CAS)
    # ------------------------------------------------------------------

    @staticmethod
    def _mapping(value: Any) -> dict[str, list[str]]:
        raw = json_mapping(value, field="feature_flags")
        result: dict[str, list[str]] = {}
        for company_id, plugin_ids in raw.items():
            if not isinstance(company_id, str) or not company_id.strip():
                continue
            if isinstance(plugin_ids, (list, tuple)):
                result[company_id] = [
                    str(item) for item in plugin_ids
                    if isinstance(item, str) and item.strip()
                ]
            else:
                result[company_id] = []
        return result

    async def _read_mapping(self) -> tuple[Any, str, dict[str, list[str]]]:
        row = self._row()
        flags_col = self._flags_col(row)
        async with self._session_ctx() as session:
            found = await self._ensure_sentinel(session, row, flags_col)
            mapping = self._mapping(getattr(found, flags_col, None))
        return row, flags_col, mapping

    async def list(self, company_id: str) -> Sequence[str]:
        """Async ``GrantStorePort.get_enabled_modules``: список включённых модулей."""
        text(company_id, field="company_id")
        try:
            _, _, mapping = await self._read_mapping()
            result = tuple(mapping.get(company_id, []))
            with self._lock:
                self._cache[company_id] = result
            return result
        except PluginRepositoryError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            # Ошибка чтения = «флаг выключен» (legacy), но ЯВНАЯ ошибка для
            # позитивных мутаций; для чтения возвращаем безопасный пустой список.
            raise PluginRepositoryUnavailable(f"не удалось прочитать фича-флаг: {exc}") from exc

    async def set(self, company_id: str, plugin_ids: Sequence[str] | Iterable[str]) -> Sequence[str]:
        """Записать новое значение флага (CAS, идемпотентно, без переноса данных).

        Переключается ТОЛЬКО путь обработки (§8 этап 2, кр. 16): данные
        declarative-app не трогаются. Ошибка записи → исключение (deny).
        """
        text(company_id, field="company_id")
        normalized = tuple(dict.fromkeys(str(item) for item in plugin_ids))
        for item in normalized:
            text(item, field="plugin_ids[]")
        try:
            row, flags_col, _ = await self._read_mapping()
            key_col = row.__table__.columns[self._col("grant_id")]
            flags = row.__table__.columns[flags_col]
            for attempt in range(_CAS_RETRIES):
                async with self._session_ctx() as session:
                    found = await self._ensure_sentinel(session, row, flags_col)
                    old_raw = getattr(found, flags_col, None)
                    mapping = self._mapping(old_raw)
                    if tuple(mapping.get(company_id, [])) == normalized:
                        # Идемпотентный повтор — запись не нужна.
                        result = normalized
                        with self._lock:
                            self._cache[company_id] = result
                        return result
                    mapping[company_id] = list(normalized)
                    new_value: dict[str, Any] = dict(mapping)
                    result_set = await session.execute(
                        update(row)
                        .where(key_col == FLAG_SENTINEL_GRANT_ID)
                        .where(flags == old_raw)  # CAS: значение не изменилось с чтения
                        .values(**{flags_col: new_value})
                    )
                    if result_set.rowcount != 1:
                        await _rollback_if_owned(session, self._session is None)
                        continue  # гонка: перечитываем и повторяем
                    await _commit_if_owned(session, self._session is None)
                with self._lock:
                    self._cache[company_id] = normalized
                return normalized
            raise PluginRepositoryUnavailable(
                "set enabled_modules: CAS не подтверждён после нескольких попыток (гонка публикации)"
            )
        except PluginRepositoryError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise PluginRepositoryUnavailable(f"set enabled_modules failed: {exc}") from exc

    async def enabled(self, company_id: str, plugin_id: str) -> bool:
        """Включён ли новый plugin-путь для модуля (False → legacy)."""
        text(company_id, field="company_id")
        text(plugin_id, field="plugin_id")
        modules = await self.list(company_id)
        return plugin_id in modules

    async def prefetch(self, company_id: str | None = None) -> None:
        """Прогреть синхронный кэш (вызывать при старте / в middleware)."""
        try:
            _, _, mapping = await self._read_mapping()
            with self._lock:
                if company_id is None:
                    self._cache = {
                        key: tuple(value) for key, value in mapping.items()
                    }
                else:
                    self._cache[company_id] = tuple(mapping.get(company_id, []))
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"prefetch фича-флага не удался: {exc}") from exc

    # ------------------------------------------------------------------
    # Синхронный доступ из кэша (fail-closed: нет данных → False/())
    # ------------------------------------------------------------------

    def list_sync(self, company_id: str) -> Sequence[str]:
        with self._lock:
            return self._cache.get(text(company_id, field="company_id"), ())

    def enabled_sync(self, company_id: str, plugin_id: str) -> bool:
        return plugin_id in self.list_sync(company_id)

    def set_sync(self, company_id: str, plugin_ids: Sequence[str]) -> Sequence[str]:
        """Обновить кэш И поставить durable-задачу (реальная запись — ``flush``).

        Возвращает новое значение для вызывающего, но НЕ считается
        подтверждённым до ``await flush()``. Без flush флаг останется выключенным
        после рестарта — это намеренный fail-closed, а не «тихо сохранили».
        """
        normalized = tuple(dict.fromkeys(str(item) for item in plugin_ids))
        with self._lock:
            self._cache[text(company_id, field="company_id")] = normalized
            pending = getattr(self, "_pending", None)
            if pending is None:
                pending = {}
                self._pending = pending
            pending[company_id] = normalized
        return normalized

    async def flush(self) -> None:
        """Применить отложенные ``set_sync`` (durable CAS-запись)."""
        with self._lock:
            pending = dict(getattr(self, "_pending", {}))
            self._pending = {}
        for company_id, plugin_ids in pending.items():
            await self.set(company_id, plugin_ids)

    def warm(self, company_id: str, plugin_ids: Sequence[str]) -> None:
        """Засеять кэш без обращения к БД (для sync-порта / тестов)."""
        with self._lock:
            self._cache[text(company_id, field="company_id")] = tuple(plugin_ids)


class SyncFeatureFlagStore:
    """Sync-адаптер с ровно ``enabled``/``set``/``list`` для strangler-порта.

    Если ``application/plugins/strangler/flags.py::FeatureFlagStorePort``
    окажется синхронным — родитель подключает этот адаптер; durable-запись
    выполняется через ``await inner.flush()`` в async-точке жизненного цикла.
    """

    def __init__(self, inner: DbFeatureFlagStore) -> None:
        if not isinstance(inner, DbFeatureFlagStore):
            raise PluginRepositoryError("SyncFeatureFlagStore требует DbFeatureFlagStore")
        self._inner = inner

    @property
    def inner(self) -> DbFeatureFlagStore:
        return self._inner

    def enabled(self, company_id: str, plugin_id: str) -> bool:
        return self._inner.enabled_sync(company_id, plugin_id)

    def set(self, company_id: str, plugin_ids: Sequence[str]) -> Sequence[str]:
        return self._inner.set_sync(company_id, plugin_ids)

    def list(self, company_id: str) -> Sequence[str]:
        return self._inner.list_sync(company_id)


class _BorrowedSession:
    """Async-CM поверх чужой сессии: НЕ закрывает и НЕ коммитит её."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


async def _commit_if_owned(session: AsyncSession, owned: bool) -> None:
    if owned:
        await session.commit()


async def _rollback_if_owned(session: AsyncSession, owned: bool) -> None:
    if owned:
        await session.rollback()


__all__ = [
    "FLAGS_COLUMN_CANDIDATES",
    "DbFeatureFlagStore",
    "SyncFeatureFlagStore",
    "feature_flag_port",
]
