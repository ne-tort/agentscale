"""``DbFeatureFlagStore`` — фича-флаг strangler ``plugin_enabled_modules[]`` (F10, кр. 16).

ЕДИНСТВЕННЫЙ владелец флага (решение родителя, 6-я таблица A10):

    ``PluginFeatureFlagRow`` (``plugin_feature_flags``)
    ``company_id`` PK + ``enabled_modules`` JSONB NOT NULL default ``[]`` + ``updated_at``

Ни ``capabilities``/``egress_policy``/``quota`` гранта, ни служебная строка
в таблице доставки НЕ являются источником истины. Если класс 6-й таблицы ещё
не зарегистрирован в конкретной сессии, репозиторий бросает ЯВНУЮ
:class:`PluginRepositorySchemaError` при любой авторитетной операции —
fail-closed, а не «тихо пустой список». A10 и ревизия ``2026110801`` уже
содержат ``PluginFeatureFlagRow`` (``company_id`` PK + ``enabled_modules``
JSONB + ``updated_at``).

Стык sync/async
---------------
Реализует СИНХРОННЫЙ ``strangler.flags.FeatureFlagStorePort`` ровно тремя
методами (порт запрещает dual-read/parity/snapshot и всё сверх этого):

* ``enabled(company_id, plugin_id) -> bool``;
* ``set(company_id, plugin_ids) -> None``;
* ``list(company_id) -> tuple[str, ...]``.

``EnabledModulesPolicy.is_enabled`` дёргает их на КАЖДОМ запросе → читаем
in-memory кэш (fail-closed: пустой кэш → legacy-путь). Авторитетные
асинхронные операции вынесены в отдельные имена: ``list_async`` /
``set_async`` / ``enabled_async`` / ``prefetch`` / ``flush`` — их вызывают
``GrantStorePort.get_enabled_modules`` / ``set_enabled_modules`` и старт
приложения. Разделение обязательно: ``AsyncSession`` нельзя await'ить из
sync-метода роутера.

Конкурентность: ``set_async`` = UPSERT по PK ``company_id`` с CAS по
``updated_at`` (``UPDATE ... WHERE company_id=:id AND enabled_modules=:old``,
проверка ``rowcount``, ретрай). При исчерпании — ``PluginRepositoryUnavailable``
(deny позитивной мутации), а не «последний победил».

Fail-closed (кр. 16): пустой кэш / ошибка БД / флаг выключен → ``False``/``()``,
то есть **legacy-путь**. Это безопаснее неожиданного переключения на ещё не
готовый plugin-путь. Никакой миграции/переноса данных при переключении нет.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ._schema import (
    FLAG_ROW,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    column_names,
    load_row,
    string_tuple,
    text,
)

_CAS_RETRIES = 5

#: Кандидаты имён JSONB-колонки со списком модулей (на случай переименования).
ENABLED_MODULES_COLUMN_CANDIDATES: tuple[str, ...] = (
    "enabled_modules",
    "plugin_enabled_modules",
    "feature_flags",
    "modules",
)

#: Кандидаты колонки-версии для CAS (необязательна). ``updated_at`` НЕ используем
#: как версию: у неё ``onupdate=func.now()`` и сравнение naive/aware datetime
#: ненадёжно. CAS идёт по значению ``enabled_modules`` (см. ``set_async``).
VERSION_COLUMN_CANDIDATES: tuple[str, ...] = ("version", "revision")


def feature_flag_port() -> Any | None:
    """Вернуть ``FeatureFlagStorePort`` (для ``isinstance`` в тестах/ревью)."""
    import importlib

    try:
        module = importlib.import_module("prodavan.application.plugins.strangler.flags")
    except (ImportError, ModuleNotFoundError):
        return None
    return getattr(module, "FeatureFlagStorePort", None)


class DbFeatureFlagStore:
    """Синхронный ``FeatureFlagStorePort`` + асинхронный авторитетный слой."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        columns: ColumnMap | None = None,
        flags_column: str | None = None,
        flags_row: type | None = None,
        clock: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                "DbFeatureFlagStore требует session_factory или session (fail-closed)"
            )
        self._session_factory = session_factory
        self._session = session
        self._columns = columns or ColumnMap()
        self._explicit_flags_column = flags_column
        self._flags_row = flags_row
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[str, ...]] = {}
        self._pending: dict[str, tuple[str, ...]] = {}

    # ------------------------------------------------------------------
    # FeatureFlagStorePort (СИНХРОННЫЙ, ровно 3 метода)
    # ------------------------------------------------------------------

    def enabled(self, company_id: str, plugin_id: str) -> bool:
        """``True`` только если модуль явно включён у компании в кэше."""
        key = (company_id or "").strip()
        plugin = (plugin_id or "").strip()
        if not key or not plugin:
            return False
        with self._lock:
            return plugin in self._cache.get(key, ())

    def set(self, company_id: str, plugin_ids: Iterable[str]) -> None:
        """Записать список в кэш + поставить durable-задачу (``-> None``).

        Реальная БД-запись — ``await flush()`` / ``await set_async()``; до
        подтверждения флаг не переживёт рестарт (fail-closed).
        """
        key = text(company_id, field="company_id")
        normalized = tuple(dict.fromkeys(str(item) for item in plugin_ids))
        with self._lock:
            self._cache[key] = normalized
            self._pending[key] = normalized

    def list(self, company_id: str) -> tuple[str, ...]:
        key = (company_id or "").strip()
        if not key:
            return ()
        with self._lock:
            return self._cache.get(key, ())

    # ------------------------------------------------------------------
    # Разрешение 6-й таблицы
    # ------------------------------------------------------------------

    def _backend(self) -> tuple[type, str, str | None]:
        """Вернуть ``(row, enabled_modules_col, version_col | None)``.

        ЯВНАЯ ошибка, если 6-й таблицы A10 нет: флаг нельзя «тихо» хранить
        где-то ещё (это был бы обход решения родителя).
        """
        row = self._flags_row or load_row(FLAG_ROW)
        row_name = getattr(row, "__name__", type(row).__name__)
        names = column_names(row)
        require = ["company_id"]
        missing = [name for name in require if name not in names]
        if missing:
            raise PluginRepositorySchemaError(
                f"{row_name}: нет колонки company_id (есть {sorted(names)})"
            )
        candidates = (
            ([self._explicit_flags_column] if self._explicit_flags_column else [])
            + list(ENABLED_MODULES_COLUMN_CANDIDATES)
        )
        modules_col = next((name for name in candidates if name and name in names), None)
        if modules_col is None:
            raise PluginRepositorySchemaError(
                f"{row_name}: нет JSONB-колонки enabled_modules "
                f"(кандидаты {candidates}, есть {sorted(names)}). "
                f"Передайте flags_column=<реальная колонка> или flags_row=."
            )
        version_col = next(
            (name for name in VERSION_COLUMN_CANDIDATES if name in names), None
        )
        return row, modules_col, version_col

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

    @staticmethod
    def _modules(value: Any) -> tuple[str, ...]:
        """JSONB-массив → кортеж; пустой/битый = ``()`` (legacy)."""
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise PluginRepositorySchemaError(
                f"enabled_modules должен быть JSONB-массивом, получено {type(value).__name__}"
            )
        return string_tuple(value, field="enabled_modules")

    # ------------------------------------------------------------------
    # Авторитетные async-операции
    # ------------------------------------------------------------------

    async def list_async(self, company_id: str) -> tuple[str, ...]:
        """Авторитетный async-список (``GrantStorePort.get_enabled_modules``)."""
        text(company_id, field="company_id")
        try:
            row, modules_col, _ = self._backend()
            table = row.__table__
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(table.columns["company_id"] == company_id)
                    )
                ).scalars().first()
            modules = self._modules(getattr(found, modules_col, None)) if found is not None else ()
            with self._lock:
                self._cache[company_id] = modules
            return modules
        except PluginRepositoryError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise PluginRepositoryUnavailable(f"не удалось прочитать фича-флаг: {exc}") from exc

    async def set_async(
        self, company_id: str, plugin_ids: Sequence[str] | Iterable[str]
    ) -> tuple[str, ...]:
        """UPSERT по PK ``company_id`` с CAS (без переноса данных, кр. 16).

        Кэш обновляется ТОЛЬКО после подтверждённой записи. Ошибка/гонка →
        ``PluginRepositoryUnavailable`` (deny позитивной мутации).
        """
        text(company_id, field="company_id")
        normalized = tuple(dict.fromkeys(str(item) for item in plugin_ids))
        for item in normalized:
            text(item, field="plugin_ids[]")
        try:
            row, modules_col, version_col = self._backend()
            table = row.__table__
            for _attempt in range(_CAS_RETRIES):
                async with self._session_ctx() as session:
                    found = (
                        await session.execute(
                            select(row).where(table.columns["company_id"] == company_id)
                        )
                    ).scalars().first()
                    if found is None:
                        values: dict[str, Any] = {
                            "company_id": company_id,
                            modules_col: list(normalized),
                        }
                        if version_col is not None and version_col in column_names(row):
                            values[version_col] = self._now()
                        try:
                            session.add(row(**values))
                            await _commit_if_owned(session, self._session is None)
                        except IntegrityError:
                            # Параллельный INSERT: перечитываем и идём на CAS.
                            await _rollback_if_owned(session, self._session is None)
                            continue
                    else:
                        old_raw = getattr(found, modules_col, None)
                        if self._modules(old_raw) == normalized:
                            # Идемпотентный повтор: запись не нужна.
                            with self._lock:
                                self._cache[company_id] = normalized
                                self._pending.pop(company_id, None)
                            return normalized
                        old_version = getattr(found, version_col, None) if version_col else None
                        stmt = (
                            update(row)
                            .where(table.columns["company_id"] == company_id)
                            .where(table.columns[modules_col] == old_raw)
                        )
                        values = {modules_col: list(normalized)}
                        if version_col is not None:
                            stmt = stmt.where(table.columns[version_col] == old_version)
                            values[version_col] = self._now()
                        result = await session.execute(stmt.values(**values))
                        if result.rowcount != 1:
                            await _rollback_if_owned(session, self._session is None)
                            continue
                        await _commit_if_owned(session, self._session is None)
                with self._lock:
                    self._cache[company_id] = normalized
                    self._pending.pop(company_id, None)
                return normalized
            raise PluginRepositoryUnavailable(
                "set enabled_modules: CAS не подтверждён после нескольких попыток (гонка)"
            )
        except PluginRepositoryError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise PluginRepositoryUnavailable(f"set enabled_modules failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — позитивная мутация = явный deny
            raise PluginRepositoryUnavailable(
                f"set enabled_modules failed: {type(exc).__name__}"
            ) from exc

    async def enabled_async(self, company_id: str, plugin_id: str) -> bool:
        text(company_id, field="company_id")
        text(plugin_id, field="plugin_id")
        return plugin_id in await self.list_async(company_id)

    async def prefetch(self, company_id: str | None = None) -> None:
        """Прогреть синхронный кэш (старт приложения / middleware).

        Ошибка чтения НЕ включает флаги: кэш очищается → legacy (fail-closed).
        """
        try:
            row, modules_col, _ = self._backend()
            table = row.__table__
            statement = select(row)
            if company_id is not None:
                statement = statement.where(table.columns["company_id"] == company_id)
            async with self._session_ctx() as session:
                found = (await session.execute(statement)).scalars().all()
            mapping = {
                str(getattr(item, "company_id")): self._modules(getattr(item, modules_col, None))
                for item in found
            }
            with self._lock:
                if company_id is None:
                    self._cache = mapping
                else:
                    self._cache[company_id] = mapping.get(company_id, ())
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            with self._lock:
                if company_id is None:
                    self._cache.clear()
                else:
                    self._cache.pop(company_id, None)
            raise PluginRepositoryUnavailable(f"prefetch фича-флага не удался: {exc}") from exc

    async def flush(self) -> None:
        """Применить отложенные sync-``set`` (durable UPSERT)."""
        with self._lock:
            pending = dict(self._pending)
            self._pending = {}
        for company_id, plugin_ids in pending.items():
            await self.set_async(company_id, plugin_ids)

    def warm(self, company_id: str, plugin_ids: Iterable[str]) -> None:
        """Засеять кэш без обращения к БД (тесты/старт)."""
        with self._lock:
            self._cache[text(company_id, field="company_id")] = tuple(str(item) for item in plugin_ids)

    # Алиасы для явной sync-семантики (не часть порта).
    def list_sync(self, company_id: str) -> tuple[str, ...]:
        return self.list(company_id)

    def enabled_sync(self, company_id: str, plugin_id: str) -> bool:
        return self.enabled(company_id, plugin_id)

    def set_sync(self, company_id: str, plugin_ids: Iterable[str]) -> None:
        self.set(company_id, plugin_ids)


class SyncFeatureFlagStore:
    """Тонкий sync-адаптер (``DbFeatureFlagStore`` УЖЕ реализует порт напрямую)."""

    def __init__(self, inner: DbFeatureFlagStore) -> None:
        if not isinstance(inner, DbFeatureFlagStore):
            raise PluginRepositoryError("SyncFeatureFlagStore требует DbFeatureFlagStore")
        self._inner = inner

    @property
    def inner(self) -> DbFeatureFlagStore:
        return self._inner

    def enabled(self, company_id: str, plugin_id: str) -> bool:
        return self._inner.enabled(company_id, plugin_id)

    def set(self, company_id: str, plugin_ids: Iterable[str]) -> None:
        self._inner.set(company_id, plugin_ids)

    def list(self, company_id: str) -> tuple[str, ...]:
        return self._inner.list(company_id)

    async def flush(self) -> None:
        await self._inner.flush()


class _BorrowedSession:
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
    "ENABLED_MODULES_COLUMN_CANDIDATES",
    "DbFeatureFlagStore",
    "SyncFeatureFlagStore",
    "feature_flag_port",
]
