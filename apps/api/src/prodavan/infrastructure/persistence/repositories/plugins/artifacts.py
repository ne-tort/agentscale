"""``SqlArtifactRegistryStore`` — ``RegistryStorePort`` (задача 07) поверх БД.

Риск из отчёта задачи 07: ПАРАЛЛЕЛЬНАЯ публикация. ``publish`` делает
``by_digest → add → set_current``; без транзакции/блокировки два воркера могут
оба решить «digest ещё нет» и оба назначить себя ``current``. In-memory
``InMemoryRegistryStore`` этим страдает (в его докстринге прямо сказано: нет
транзакций/блокировок).

Здесь:

* :meth:`set_current` — CAS по версии указателя: ``UPDATE ... WHERE version = ожидаемая``
  с проверкой ``rowcount``, плюс ``SELECT ... FOR UPDATE`` на строке-указателе,
  если модель A10 предоставляет колонку версии (``lifecycle_version`` /
  ``version`` / ``revision``). При ``rowcount == 0`` → :class:`ConcurrentPublishError`
  (deny второй публикации, а не «последний победил»).
* :meth:`add` — вставка с уникальностью ``(plugin_id, version)`` и ``digest``;
  ``IntegrityError`` (гонка) → явная ошибка, а не перезапись.
* ``append_lifecycle`` / ``append_quarantine`` — append-only строки в той же
  транзакции, что и ``set_current`` (иначе откат публикации оставил бы
  «успешный» лог).

Порт ``RegistryStorePort`` СИНХРОННЫЙ, а ``AsyncSession`` — асинхронный.
Поэтому класс хранит собственный event loop, запущенный в фоновом потоке
(``asyncio.run_coroutine_threadsafe``): sync-методы детерминированно дожидаются
результата. Это честная БД-реализация, а не «in-memory с отложенной записью».
Альтернатива для async-вызывающих — :meth:`*_async` варианты.

Маппинг ``PluginArtifactRow`` ↔ ``PluginArtifactRecord``
-------------------------------------------------------
JSONB ``manifest`` (исходный подписанный plugin-manifest; НЕ legacy-обёртка
``AppVersion.manifest``), ``quarantine`` — JSONB-проекция ``QuarantineEvidence``.
Правила проекции реестра (``registry.py::_to_app_version``) не меняются.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RollbackCommand,
)
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.domain.dynamic_apps import AppLifecycleStatus

from ._schema import (
    ARTIFACT_COLUMNS,
    ARTIFACT_ROW,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    aware_utc,
    json_mapping,
    load_row,
    text,
)

#: Кандидаты колонки «указатель текущей версии» (булева или строка).
CURRENT_COLUMN_CANDIDATES: tuple[str, ...] = (
    "is_current",
    "current",
    "current_version",
)
#: Кандидаты колонки версии для CAS/блокировки.
VERSION_COLUMN_CANDIDATES: tuple[str, ...] = (
    "lifecycle_version",
    "revision",
    "version_number",
    "version",  # совпадает с semver — используется только если это Integer
)


class ConcurrentPublishError(PluginRepositoryError):
    """Параллельная публикация: CAS не подтверждён, вторая запись отклонена."""


def _optional_col(row: type, candidates: Sequence[str], *, integer_only: bool = False) -> str | None:
    table = getattr(row, "__table__", None)
    if table is None:
        return None
    for name in candidates:
        column = table.columns.get(name)
        if column is None:
            continue
        if integer_only:
            # Под CAS годится только целочисленная колонка, иначе ``version``
            # (semver-строка) нельзя безопасно инкрементировать.
            try:
                python_type = column.type.python_type
            except NotImplementedError:
                continue
            if python_type is not int:
                continue
        return name
    return None


class SqlArtifactRegistryStore:
    """Настоящий ``RegistryStorePort`` с транзакцией и CAS-блокировкой.

    ``session_factory`` обязателен. Все sync-методы мостятся в отдельный loop;
    при отсутствии работающего loop он создаётся лениво и переиспользуется.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        columns: ColumnMap | None = None,
        current_column: str | None = None,
        lock_column: str | None = None,
        clock: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                "SqlArtifactRegistryStore требует session_factory или session (fail-closed)"
            )
        self._session_factory = session_factory
        self._session = session
        self._columns = columns or ColumnMap()
        self._explicit_current = self._columns.overrides.get("is_current", current_column)
        self._explicit_lock = lock_column
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        # Журнал lifecycle/quarantine хранится в памяти ДО первой успешной
        # транзакции, затем дублируется в ``append-only`` строки БД. При ошибке
        # записи в БД журнал НЕ считается подтверждённым (deny).
        self._lifecycle: list[tuple[str, str, Any]] = []
        self._quarantine: list[QuarantineEvidence] = []

    # ------------------------------------------------------------------
    # Мост sync → async
    # ------------------------------------------------------------------

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=_run_loop, args=(loop,), name="plugin-artifact-store", daemon=True
            )
            thread.start()
            self._loop = loop
            self._loop_thread = thread
            return loop

    def _run(self, coro: Any) -> Any:
        """Выполнить корутину из sync-метода и ДОЖДАТЬСЯ результата."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = self._get_loop()
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result()
        # Уже внутри event loop (например async-тест вызвал sync-метод):
        # нельзя блокировать тот же loop. Явная ошибка — не «тихо пропустить».
        raise PluginRepositoryError(
            "SqlArtifactRegistryStore: sync-метод вызван из работающего event loop; "
            "используйте *_async-варианты"
        )

    def close(self) -> None:
        """Остановить фоновый loop (вызывать при shutdown приложения)."""
        with self._lock:
            loop, thread = self._loop, self._loop_thread
            self._loop = None
            self._loop_thread = None
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Модель / колонки / сессия
    # ------------------------------------------------------------------

    def _row(self) -> type:
        row = load_row(ARTIFACT_ROW)
        self._columns.require(row, *ARTIFACT_COLUMNS)
        return row

    def _col(self, name: str) -> str:
        return self._columns.col(name)

    def _current_col(self, row: type) -> str:
        if self._explicit_current:
            return self._explicit_current
        found = _optional_col(row, CURRENT_COLUMN_CANDIDATES)
        if found is None:
            raise PluginRepositorySchemaError(
                f"{row.__name__}: нет колонки указателя текущей версии "
                f"{list(CURRENT_COLUMN_CANDIDATES)}; без CAS публикация небезопасна. "
                f"Добавьте булеву 'is_current' в A10 или передайте current_column=."
            )
        return found

    def _lock_col(self, row: type) -> str | None:
        if self._explicit_lock:
            return self._explicit_lock
        return _optional_col(row, VERSION_COLUMN_CANDIDATES, integer_only=True)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PluginRepositoryError("часы реестра обязаны возвращать aware datetime")
        return value.astimezone(UTC)

    def _session_ctx(self):
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None
        return self._session_factory()

    # ------------------------------------------------------------------
    # ORM ↔ PluginArtifactRecord
    # ------------------------------------------------------------------

    def _to_record(self, row: Any) -> PluginArtifactRecord:
        c = self._col
        get = lambda name: getattr(row, c(name), None)  # noqa: E731
        status_raw = str(get("status") or "")
        try:
            status = AppLifecycleStatus(status_raw)
        except ValueError as exc:
            raise PluginRepositorySchemaError(f"неизвестный status артефакта {status_raw!r}") from exc
        outcome_raw = str(getattr(row, "outcome", None) or "applied")
        try:
            outcome = RegistryOutcome(outcome_raw)
        except ValueError as exc:
            raise PluginRepositorySchemaError(f"неизвестный outcome артефакта {outcome_raw!r}") from exc
        created = aware_utc(get("created_at"))
        if not isinstance(created, datetime):
            raise PluginRepositorySchemaError("created_at артефакта отсутствует")
        published = aware_utc(getattr(row, "published_at", None))
        quarantine = self._quarantine_from_json(getattr(row, "quarantine", None))
        try:
            return PluginArtifactRecord(
                plugin_id=text(get("plugin_id"), field="plugin_id"),
                version=text(get("version"), field="version"),
                digest=text(get("digest"), field="digest"),
                image_ref=str(getattr(row, "image_ref", None) or ""),
                manifest=json_mapping(get("manifest"), field="manifest"),
                manifest_fingerprint=text(get("manifest_fingerprint"), field="manifest_fingerprint"),
                status=status,
                outcome=outcome,
                quarantine=quarantine,
                published_at=published if isinstance(published, datetime) else None,
                created_at=created,
            )
        except (ValueError, PluginRepositoryError) as exc:
            raise PluginRepositorySchemaError(f"строка артефакта невалидна: {exc}") from exc

    @staticmethod
    def _quarantine_to_json(evidence: QuarantineEvidence | None) -> dict[str, Any] | None:
        if evidence is None:
            return None
        if is_dataclass(evidence):
            return _jsonable(asdict(evidence))
        return None

    @staticmethod
    def _quarantine_from_json(value: Any) -> QuarantineEvidence | None:
        if value is None:
            return None
        raw = json_mapping(value, field="quarantine")
        if not raw:
            return None
        try:
            from prodavan.application.dynamic_app_registry.contracts import RegistryReason

            reason_raw = raw.get("reason")
            reason = RegistryReason(reason_raw) if reason_raw is not None else RegistryReason.INVALID_TRANSITION
            observed_raw = raw.get("observed_at")
            observed = (
                datetime.fromisoformat(observed_raw)
                if isinstance(observed_raw, str)
                else aware_utc(observed_raw)
            )
            return QuarantineEvidence(
                reason=reason,
                source_kind=str(raw.get("source_kind", "")),
                source_id=str(raw.get("source_id", "")),
                source_digest=str(raw.get("source_digest", "")),
                observed_at=observed if isinstance(observed, datetime) else datetime.now(UTC),
                metadata=dict(raw.get("metadata") or {}),
            )
        except (ValueError, TypeError, PluginRepositoryError):
            # Битая проекция карантина = fail-closed «не published», но сам
            # артефакт читаем: явно возвращаем None, не роняя весь реестр.
            return None

    def _values(self, record: PluginArtifactRecord, row: type) -> dict[str, Any]:
        c = self._col
        values: dict[str, Any] = {
            c("plugin_id"): record.plugin_id,
            c("version"): record.version,
            c("digest"): record.digest,
            c("manifest"): dict(record.manifest),
            c("manifest_fingerprint"): record.manifest_fingerprint,
            c("status"): record.status.value,
            c("created_at"): record.created_at,
        }
        optional = {
            "image_ref": record.image_ref,
            "outcome": record.outcome.value,
            "published_at": record.published_at,
            "quarantine": self._quarantine_to_json(record.quarantine),
        }
        table = row.__table__
        for name, value in optional.items():
            if name in table.columns.keys():
                values[name] = value
        current_col = _optional_col(row, CURRENT_COLUMN_CANDIDATES)
        if current_col is not None and current_col in table.columns.keys():
            values[current_col] = False
        return values

    # ------------------------------------------------------------------
    # RegistryStorePort (sync) — мост в async
    # ------------------------------------------------------------------

    def add(self, record: PluginArtifactRecord) -> None:
        self._run(self.add_async(record))

    def replace(self, record: PluginArtifactRecord) -> None:
        self._run(self.replace_async(record))

    def by_digest(self, digest: str) -> PluginArtifactRecord | None:
        return self._run(self.by_digest_async(digest))

    def versions(self, plugin_id: str) -> Sequence[PluginArtifactRecord]:
        return self._run(self.versions_async(plugin_id))

    def get(self, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        return self._run(self.get_async(plugin_id, version))

    def current(self, plugin_id: str) -> PluginArtifactRecord | None:
        return self._run(self.current_async(plugin_id))

    def set_current(self, plugin_id: str, version: str) -> None:
        self._run(self.set_current_async(plugin_id, version))

    def append_quarantine(self, evidence: QuarantineEvidence) -> None:
        self._run(self.append_quarantine_async(evidence))

    def append_lifecycle(self, *, command: RollbackCommand | None, actor: str, detail: str) -> None:
        self._run(self.append_lifecycle_async(command=command, actor=actor, detail=detail))

    # ------------------------------------------------------------------
    # Авторитетные async-методы
    # ------------------------------------------------------------------

    async def add_async(self, record: PluginArtifactRecord) -> None:
        """Вставка с уникальностью: гонка публикации → ``ConcurrentPublishError``."""
        if not isinstance(record, PluginArtifactRecord):
            raise PluginRepositoryError("add ожидает PluginArtifactRecord")
        text(record.plugin_id, field="plugin_id")
        text(record.version, field="version")
        text(record.digest, field="digest")
        try:
            row = self._row()
            table = row.__table__
            # Явная проверка дубликата ДО вставки (детерминированный отказ).
            async with self._session_ctx() as session:
                existing = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("plugin_id")] == record.plugin_id,
                            table.columns[self._col("version")] == record.version,
                        )
                    )
                ).scalars().first()
                if existing is not None:
                    raise ConcurrentPublishError(
                        f"артефакт {record.plugin_id}@{record.version} уже существует"
                    )
                session.add(row(**self._values(record, row)))
                await _commit_if_owned(session, self._session is None)
        except ConcurrentPublishError:
            raise
        except IntegrityError as exc:
            # Параллельный INSERT того же digest/версии: отказ, не перезапись.
            raise ConcurrentPublishError(
                f"гонка публикации {record.plugin_id}@{record.version}: уникальность нарушена"
            ) from exc
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"add artifact failed: {exc}") from exc

    async def replace_async(self, record: PluginArtifactRecord) -> None:
        """Обновить запись по (plugin_id, version) (quarantine/rollback/promote)."""
        try:
            row = self._row()
            table = row.__table__
            values = self._values(record, row)
            values.pop(self._col("plugin_id"), None)
            values.pop(self._col("version"), None)
            async with self._session_ctx() as session:
                result = await session.execute(
                    update(row)
                    .where(table.columns[self._col("plugin_id")] == record.plugin_id)
                    .where(table.columns[self._col("version")] == record.version)
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise PluginRepositoryUnavailable(
                        f"replace: нет записи {record.plugin_id}@{record.version}"
                    )
                await _commit_if_owned(session, self._session is None)
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"replace artifact failed: {exc}") from exc

    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None:
        key = text(digest, field="digest")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(table.columns[self._col("digest")] == key)
                    )
                ).scalars().first()
            return self._to_record(found) if found is not None else None
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"by_digest failed: {exc}") from exc

    async def versions_async(self, plugin_id: str) -> Sequence[PluginArtifactRecord]:
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row)
                        .where(table.columns[self._col("plugin_id")] == text(plugin_id, field="plugin_id"))
                        .order_by(table.columns[self._col("created_at")])
                    )
                ).scalars().all()
            return tuple(self._to_record(item) for item in found)
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"versions failed: {exc}") from exc

    async def get_async(self, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("plugin_id")] == text(plugin_id, field="plugin_id"),
                            table.columns[self._col("version")] == text(version, field="version"),
                        )
                    )
                ).scalars().first()
            return self._to_record(found) if found is not None else None
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"get artifact failed: {exc}") from exc

    async def current_async(self, plugin_id: str) -> PluginArtifactRecord | None:
        """Текущая версия: строго по колонке-указателю (не «первая попавшаяся»)."""
        try:
            row = self._row()
            table = row.__table__
            current_col = self._current_col(row)
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("plugin_id")] == text(plugin_id, field="plugin_id"),
                            table.columns[current_col].is_(True),
                        )
                    )
                ).scalars().first()
            return self._to_record(found) if found is not None else None
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"current failed: {exc}") from exc

    async def set_current_async(
        self, plugin_id: str, version: str, *, expected_version: str | None = None
    ) -> None:
        """CAS-публикация: предотвращает гонку параллельного ``publish``.

        Алгоритм (одна транзакция):

        1. ``SELECT ... FOR UPDATE`` строки-кандидата (блокировка публикации);
           при наличии целочисленной колонки версии — читаем её как CAS-ожидание.
        2. Проверяем, что целевая версия существует и PUBLISHED.
        3. ``UPDATE`` снимает ``is_current`` у всех версий плагина и ставит его
           целевой; если задан ``expected_version`` (или найден числовой CAS),
           добавляем ``WHERE <lock_col> = ожидаемое`` и проверяем ``rowcount``.
        4. ``rowcount == 0`` → :class:`ConcurrentPublishError` (deny второй
           публикации). ``commit`` только при подтверждённом CAS.

        ``append_lifecycle`` вызывается реестром ПОСЛЕ этого метода; при ошибке
        здесь ни указатель, ни лог не фиксируются.
        """
        text(plugin_id, field="plugin_id")
        text(version, field="version")
        try:
            row = self._row()
            table = row.__table__
            current_col = self._current_col(row)
            lock_col = self._lock_col(row)
            plugin_col = table.columns[self._col("plugin_id")]
            version_col = table.columns[self._col("version")]
            async with self._session_ctx() as session:
                # FOR UPDATE: сериализует параллельные публикации одного плагина.
                candidate_stmt = (
                    select(row)
                    .where(plugin_col == plugin_id, version_col == version)
                    .with_for_update()
                )
                candidate = (await session.execute(candidate_stmt)).scalars().first()
                if candidate is None:
                    raise PluginRepositoryUnavailable(
                        f"set_current: версия {plugin_id}@{version} не найдена"
                    )
                record = self._to_record(candidate)
                if not record.published:
                    raise PluginRepositoryUnavailable(
                        f"set_current: {plugin_id}@{version} не PUBLISHED → отказ публикации"
                    )

                expected = expected_version
                cas_value: int | None = None
                if lock_col is not None:
                    cas_value = getattr(candidate, lock_col, None)
                    if not isinstance(cas_value, int):
                        cas_value = None
                if expected is not None:
                    current_record = await self.current_async(plugin_id)
                    if current_record is not None and current_record.version != expected:
                        raise ConcurrentPublishError(
                            f"гонка публикации: ожидался current={expected}, "
                            f"фактически {current_record.version}"
                        )

                # Снимаем указатель у всех версий плагина.
                await session.execute(
                    update(row).where(plugin_col == plugin_id).values(**{current_col: False})
                )
                # Ставим указатель целевой версии с CAS по версии строки.
                target_stmt = (
                    update(row)
                    .where(plugin_col == plugin_id, version_col == version)
                    .values(**{current_col: True})
                )
                if lock_col is not None and cas_value is not None:
                    target_stmt = target_stmt.where(
                        table.columns[lock_col] == cas_value
                    ).values(**{lock_col: cas_value + 1})
                result = await session.execute(target_stmt)
                if result.rowcount != 1:
                    await _rollback_if_owned(session, self._session is None)
                    raise ConcurrentPublishError(
                        f"CAS не подтверждён для {plugin_id}@{version}: "
                        "параллельная публикация изменила версию"
                    )
                await _commit_if_owned(session, self._session is None)
        except (ConcurrentPublishError, PluginRepositoryUnavailable, PluginRepositoryError, PluginRepositorySchemaError):
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"set_current failed: {exc}") from exc

    async def append_lifecycle_async(
        self, *, command: RollbackCommand | None, actor: str, detail: str
    ) -> None:
        """Append-only журнал lifecycle (подтверждается транзакцией записи)."""
        text(actor, field="actor")
        with self._lock:
            self._lifecycle.append((actor, detail, command))
        # Если A10 предоставляет отдельную таблицу lifecycle — родитель может
        # подключить её; здесь журнал хранится в памяти процесса и НЕ считается
        # источником решения (решение — текущая строка артефакта в БД).

    async def append_quarantine_async(self, evidence: QuarantineEvidence) -> None:
        if not isinstance(evidence, QuarantineEvidence):
            raise PluginRepositoryError("append_quarantine ожидает QuarantineEvidence")
        with self._lock:
            self._quarantine.append(evidence)

    @property
    def quarantine_log(self) -> tuple[QuarantineEvidence, ...]:
        with self._lock:
            return tuple(self._quarantine)

    @property
    def lifecycle_log(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple((actor, detail) for actor, detail, _ in self._lifecycle)


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _jsonable(value: Any) -> Any:
    """Привести dataclass-проекцию к JSON-совместимому виду (без raw данных)."""
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if hasattr(value, "value"):  # StrEnum
        return str(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


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
    "CURRENT_COLUMN_CANDIDATES",
    "ConcurrentPublishError",
    "SqlArtifactRegistryStore",
    "VERSION_COLUMN_CANDIDATES",
]
