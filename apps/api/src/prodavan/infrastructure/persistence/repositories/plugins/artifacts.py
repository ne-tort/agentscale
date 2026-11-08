"""``SqlArtifactRegistryStore`` — ``RegistryStorePort`` (задача 07) поверх БД A10.

Риск из отчёта задачи 07: ПАРАЛЛЕЛЬНАЯ публикация. ``publish`` делает
``by_digest → add → set_current``; без транзакции/блокировки два воркера могут
оба решить «digest ещё нет» и оба назначить себя ``current``.
``InMemoryRegistryStore`` этим страдает (в его докстринге прямо сказано: нет
транзакций/блокировок).

Здесь (РЕАЛЬНАЯ модель ``PluginArtifactRow``):

* :meth:`set_current` — ``SELECT ... FOR UPDATE`` строки-кандидата +
  CAS по ``lifecycle_version`` (``UPDATE ... WHERE lifecycle_version = ожидаемая``,
  проверка ``rowcount``) + снятие ``is_current`` у остальных версий. Partial
  unique ``UNIQUE(plugin_id) WHERE is_current = true`` в A10 — вторая линия
  защиты: даже при обходе CAS БД не даст двум текущим версиям.
  При ``rowcount == 0`` → :class:`ConcurrentPublishError` (deny второй
  публикации, а не «последний победил»).
* :meth:`add` — уникальность ``(plugin_id, version)`` и ``oci_digest``;
  ``IntegrityError`` (гонка) → явная ошибка, а не перезапись.
* ``append_lifecycle`` / ``append_quarantine`` — append-only журнал в памяти
  процесса (A10 не даёт отдельной таблицы); решение о публикации принимается
  ТОЛЬКО по строке ``PluginArtifactRow`` в БД, журнал не источник решения.

Маппинг ``PluginArtifactRow`` ↔ ``PluginArtifactRecord``
-------------------------------------------------------
* ``digest`` ↔ ``oci_digest``; ``image_ref`` есть в A10 (аддитивно);
* ``manifest`` — исходный подписанный plugin-manifest; ``app_version_projection``
  — legacy-обёртка из ``build_app_version_projection`` (CHECK в БД требует
  ``manifest <> app_version_projection``);
* ``manifest_fingerprint`` НЕ хранится — вычисляется ``manifest_fingerprint()``
  задачи 07 при чтении (детерминированно);
* ``status`` A10 = ``published|quarantined|revoked`` ↔ ``AppLifecycleStatus``
  задачи 07 = ``draft|published|disabled|revoked`` — явный маппинг ниже;
* ``quarantine_reason`` (короткий код) вместо JSONB ``quarantine``;
* ``outcome`` не хранится — восстанавливается из ``status``.

Порт ``RegistryStorePort`` СИНХРОННЫЙ, ``AsyncSession`` — асинхронный. Класс
держит собственный фоновый event loop и мостит sync-методы через
``asyncio.run_coroutine_threadsafe`` (честная БД-реализация, без «отложенной
записи»). Async-вызывающие могут использовать :meth:`*_async` напрямую.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
    RollbackCommand,
)
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.domain.dynamic_apps import AppLifecycleStatus

from ._schema import (
    ARTIFACT_COLUMNS,
    ARTIFACT_ROW,
    ARTIFACT_STATUS_PUBLISHED,
    ARTIFACT_STATUS_QUARANTINED,
    ARTIFACT_STATUS_REVOKED,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    aware_utc,
    column_names,
    json_mapping,
    load_row,
    text,
)

#: Кандидаты колонки-указателя текущей версии.
CURRENT_COLUMN_CANDIDATES: tuple[str, ...] = ("is_current", "current", "current_version")
#: Кандидаты CAS-колонки (целочисленной).
VERSION_COLUMN_CANDIDATES: tuple[str, ...] = (
    "lifecycle_version", "revision", "version_number",
)


class ConcurrentPublishError(PluginRepositoryError):
    """Параллельная публикация: CAS не подтверждён, вторая запись отклонена."""


def _find_col(row: type, candidates: Sequence[str], *, integer_only: bool = False) -> str | None:
    table = getattr(row, "__table__", None)
    if table is None:
        return None
    for name in candidates:
        column = table.columns.get(name)
        if column is None:
            continue
        if integer_only:
            try:
                if column.type.python_type is not int:
                    continue
            except NotImplementedError:
                continue
        return name
    return None


def _status_to_domain(value: str) -> AppLifecycleStatus:
    """A10 status → доменный ``AppLifecycleStatus`` (fail-closed)."""
    mapping = {
        ARTIFACT_STATUS_PUBLISHED: AppLifecycleStatus.PUBLISHED,
        ARTIFACT_STATUS_QUARANTINED: AppLifecycleStatus.DISABLED,
        ARTIFACT_STATUS_REVOKED: AppLifecycleStatus.REVOKED,
    }
    try:
        return mapping[value]
    except KeyError as exc:
        raise PluginRepositorySchemaError(f"неизвестный status артефакта {value!r}") from exc


def _status_to_db(status: AppLifecycleStatus) -> str:
    mapping = {
        AppLifecycleStatus.PUBLISHED: ARTIFACT_STATUS_PUBLISHED,
        AppLifecycleStatus.DISABLED: ARTIFACT_STATUS_QUARANTINED,
        AppLifecycleStatus.DRAFT: ARTIFACT_STATUS_QUARANTINED,
        AppLifecycleStatus.REVOKED: ARTIFACT_STATUS_REVOKED,
    }
    try:
        return mapping[status]
    except KeyError as exc:
        raise PluginRepositorySchemaError(f"неизвестный AppLifecycleStatus {status!r}") from exc


def _fingerprint(manifest: Any) -> str:
    """Детерминированный fingerprint (совпадает с задачей 07 по смыслу)."""
    from prodavan.application.plugins.artifacts.verification import manifest_fingerprint

    try:
        return manifest_fingerprint(manifest)
    except Exception:  # noqa: BLE001 — fallback без external-зависимости
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class SqlArtifactRegistryStore:
    """Настоящий ``RegistryStorePort`` с транзакцией и CAS-блокировкой."""

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
        self._explicit_current = current_column
        self._explicit_lock = lock_column
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
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
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            future = asyncio.run_coroutine_threadsafe(coro, self._get_loop())
            return future.result()
        raise PluginRepositoryError(
            "SqlArtifactRegistryStore: sync-метод вызван из работающего event loop; "
            "используйте *_async-варианты"
        )

    def close(self) -> None:
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
        found = _find_col(row, CURRENT_COLUMN_CANDIDATES)
        if found is None:
            raise PluginRepositorySchemaError(
                f"{row.__name__}: нет колонки is_current {list(CURRENT_COLUMN_CANDIDATES)}; "
                f"без CAS/partial unique публикация небезопасна. Добавьте 'is_current' в A10 "
                f"или передайте current_column=."
            )
        return found

    def _lock_col(self, row: type) -> str | None:
        if self._explicit_lock:
            return self._explicit_lock
        return _find_col(row, VERSION_COLUMN_CANDIDATES, integer_only=True)

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

    def _to_record(self, row_obj: Any) -> PluginArtifactRecord:
        row = type(row_obj)
        c = self._col
        get = lambda name: getattr(row_obj, c(name), None)  # noqa: E731
        status = _status_to_domain(str(get("status") or ""))
        outcome = (
            RegistryOutcome.APPLIED
            if status is AppLifecycleStatus.PUBLISHED
            else RegistryOutcome.QUARANTINED
        )
        created = aware_utc(get("created_at"))
        if not isinstance(created, datetime):
            raise PluginRepositorySchemaError("created_at артефакта отсутствует")
        published = aware_utc(get("published_at"))
        manifest = json_mapping(get("manifest"), field="manifest")
        image_ref = str(get("image_ref") or "") if self._columns.has(row, "image_ref") else ""
        quarantine = None
        reason = get("quarantine_reason")
        if reason and self._columns.has(row, "quarantine_reason"):
            quarantine = QuarantineEvidence(
                reason=RegistryReason.INVALID_TRANSITION,
                source_kind="plugin_artifact_registry",
                source_id=str(get("digest")),
                source_digest=str(get("digest")),
                observed_at=published if isinstance(published, datetime) else created,
                metadata={"detail": str(reason)[:200]},
            )
        try:
            return PluginArtifactRecord(
                plugin_id=text(get("plugin_id"), field="plugin_id"),
                version=text(get("version"), field="version"),
                digest=text(get("digest"), field="digest"),
                image_ref=image_ref,
                manifest=manifest,
                manifest_fingerprint=_fingerprint(manifest),
                status=status,
                outcome=outcome,
                quarantine=quarantine,
                published_at=published if isinstance(published, datetime) else None,
                created_at=created,
            )
        except (ValueError, PluginRepositoryError) as exc:
            raise PluginRepositorySchemaError(f"строка артефакта невалидна: {exc}") from exc

    def _values(self, record: PluginArtifactRecord, row: type) -> dict[str, Any]:
        """Доменный контракт → РЕАЛЬНЫЕ колонки A10."""
        c = self._col
        manifest = dict(record.manifest)
        # CHECK ``manifest <> app_version_projection``: проекция ВСЕГДА отлична.
        from prodavan.infrastructure.persistence.models.plugin_platform import (
            build_app_version_projection,
        )

        values: dict[str, Any] = {
            c("plugin_id"): record.plugin_id,
            c("version"): record.version,
            c("digest"): record.digest,
            c("manifest"): manifest,
            c("app_version_projection"): build_app_version_projection(manifest),
            c("status"): _status_to_db(record.status),
            c("created_at"): record.created_at,
            c("published_at"): record.published_at,
            c("image_ref"): record.image_ref or None,
            c("cosign_verified"): False,
            c("lifecycle_version"): 0,
            c("quarantine_reason"): (
                record.quarantine.reason.value if record.quarantine is not None else None
            ),
            c("is_current"): False,
        }
        existing = column_names(row)
        return {key: value for key, value in values.items() if key in existing}

    # ------------------------------------------------------------------
    # RegistryStorePort (sync)
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
            raise ConcurrentPublishError(
                f"гонка публикации {record.plugin_id}@{record.version}: "
                f"нарушена уникальность (plugin_id,version) или oci_digest"
            ) from exc
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"add artifact failed: {exc}") from exc

    async def replace_async(self, record: PluginArtifactRecord) -> None:
        """Обновить запись (quarantine/rollback/promote) по (plugin_id, version)."""
        try:
            row = self._row()
            table = row.__table__
            values = self._values(record, row)
            values.pop(self._col("plugin_id"), None)
            values.pop(self._col("version"), None)
            values.pop(self._col("is_current"), None)  # указатель меняет только set_current
            values.pop(self._col("lifecycle_version"), None)  # CAS-счётчик не сбрасываем
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
        """Текущая версия: строго ``is_current = true`` (не «первая попавшаяся»)."""
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

        1. ``SELECT ... FOR UPDATE`` строки-кандидата (сериализует публикации
           одного плагина) + читаем ``lifecycle_version`` как CAS-ожидание.
        2. Проверяем: версия есть, ``status='published'``, ``cosign_verified``
           (если колонка есть) — иначе отказ (кр. 18).
        3. ``UPDATE`` снимает ``is_current`` у всех версий плагина.
        4. ``UPDATE`` ставит ``is_current=True`` целевой версии с
           ``WHERE lifecycle_version = ожидаемая`` и инкрементом счётчика;
           ``rowcount != 1`` → :class:`ConcurrentPublishError` (deny).
        5. Partial unique ``UNIQUE(plugin_id) WHERE is_current = true`` в A10 —
           вторая линия защиты на уровне БД.

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
                candidate = (
                    await session.execute(
                        select(row)
                        .where(plugin_col == plugin_id, version_col == version)
                        .with_for_update()
                    )
                ).scalars().first()
                if candidate is None:
                    raise PluginRepositoryUnavailable(
                        f"set_current: версия {plugin_id}@{version} не найдена"
                    )
                record = self._to_record(candidate)
                if not record.published:
                    raise PluginRepositoryUnavailable(
                        f"set_current: {plugin_id}@{version} не PUBLISHED → отказ публикации"
                    )
                if self._columns.has(row, "cosign_verified"):
                    if getattr(candidate, self._col("cosign_verified"), False) is not True:
                        raise PluginRepositoryUnavailable(
                            "set_current: cosign-подпись не подтверждена → QUARANTINED"
                        )

                if expected_version is not None:
                    current_record = await self.current_async(plugin_id)
                    if current_record is not None and current_record.version != expected_version:
                        raise ConcurrentPublishError(
                            f"гонка публикации: ожидался current={expected_version}, "
                            f"фактически {current_record.version}"
                        )

                # Снимаем указатель у всех версий плагина.
                await session.execute(
                    update(row).where(plugin_col == plugin_id).values(**{current_col: False})
                )
                # Ставим указатель целевой версии с CAS по счётчику.
                target_stmt = (
                    update(row)
                    .where(plugin_col == plugin_id, version_col == version)
                    .values(**{current_col: True})
                )
                cas_value: int | None = None
                if lock_col is not None:
                    raw = getattr(candidate, lock_col, None)
                    cas_value = int(raw) if isinstance(raw, int) else None
                if lock_col is not None and cas_value is not None:
                    target_stmt = target_stmt.where(
                        table.columns[lock_col] == cas_value
                    ).values(**{current_col: True, lock_col: cas_value + 1})
                result = await session.execute(target_stmt)
                if result.rowcount != 1:
                    await _rollback_if_owned(session, self._session is None)
                    raise ConcurrentPublishError(
                        f"CAS не подтверждён для {plugin_id}@{version}: "
                        "параллельная публикация изменила lifecycle_version"
                    )
                await _commit_if_owned(session, self._session is None)
        except (ConcurrentPublishError, PluginRepositoryUnavailable, PluginRepositoryError, PluginRepositorySchemaError):
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"set_current failed: {exc}") from exc

    async def append_lifecycle_async(
        self, *, command: RollbackCommand | None, actor: str, detail: str
    ) -> None:
        """Append-only журнал lifecycle (решение — строка артефакта в БД)."""
        text(actor, field="actor")
        with self._lock:
            self._lifecycle.append((actor, detail, command))

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
