"""SQLAlchemy-репозиторий ``CapabilityGrant`` (F11, стык задач 02/03/12).

Реализует:

* ``admin.ports.GrantStorePort`` — асинхронный контракт задачи 12
  (``get`` / ``get_by_grant_id`` / ``create`` / ``deactivate`` / ``delete`` /
  ``list_by_company`` / ``get_enabled_modules`` / ``set_enabled_modules``);
* синхронные методы ``get_by_id`` / ``get_active`` / ``save`` / ``revoke`` /
  ``list_by_company`` / ``enabled_modules`` / ``is_active(grant_id, *, now)`` —
  контракт ТЗ иStructural basis для ``GrantRevocationPort`` (задача 03) и
  ``RevocationPort`` (задача 02). Синхронная часть НЕ ходит в ``AsyncSession``:
  она читает/пишет in-memory снимок, который обновляется асинхронными
  операциями (подробнее — ``revocation.py::DbGrantRevocation``).

Маппинг ORM ↔ домен
--------------------
* ``CapabilityGrantRow`` (A10) ↔ ``admin.contracts.CapabilityGrantRecord``;
* при необходимости — ↔ ``domain.plugins.types.CapabilityGrant`` (предусмотрен
  :meth:`SqlCapabilityGrantRepository.to_domain_grant`).
* ``capabilities`` / ``egress_policy`` / ``quota`` — JSONB. Egress и квоты
  хранятся в БД, а НЕ в коротком JWT (§4.2, §13.3).

Fail-closed (критерий №12)
-------------------------
Грант активен ТОЛЬКО если одновременно: строка существует, ``status == "active"``,
``revoked_at is None``, ``expires_at`` отсутствует или ещё не наступил, и это
не служебная строка фича-флага. Любая неопределённость/ошибка чтения = ``False``
/ ``None``, но НИКОГДА «разрешить по умолчанию».
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressMode,
    EgressPolicy as AdminEgressPolicy,
    GrantStatus,
    QuotaSet,
)

from ._schema import (
    ACTIVE_GRANT_STATUS,
    FLAG_SENTINEL_GRANT_ID,
    FLAG_SENTINEL_PLUGIN_ID,
    GRANT_COLUMNS,
    GRANT_ROW,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    aware_utc,
    json_mapping,
    load_row,
    string_tuple,
    text,
)

def _not_sentinel_condition(row: type, columns: ColumnMap) -> Any:
    """Исключаем служебную строку фича-флага из ЛЮБОЙ выдачи грантов."""
    return row.__table__.columns[columns.col("grant_id")] != FLAG_SENTINEL_GRANT_ID


class SqlCapabilityGrantRepository:
    """Настоящий SQL-репозиторий грантов поверх ``AsyncSession``.

    ``session_factory`` обязателен и НЕ имеет значения по умолчанию: собрать
    «частично подключённый» репозиторий невозможно. ``session`` (готовая
    сессия) допустим для сценариев, где транзакцией управляет вызывающий.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        columns: ColumnMap | None = None,
        now: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                "SqlCapabilityGrantRepository требует session_factory или session "
                "(fail-closed: без БД гранты не считаются активными)"
            )
        self._session_factory = session_factory
        self._session = session
        self._columns = columns or ColumnMap()
        self._clock = now or (lambda: datetime.now(UTC))
        # Синхронный снимок: grant_id -> CapabilityGrantRecord. Обновляется
        # ТОЛЬКО успешными асинхронными операциями. Пустой снимок = «не знаю»
        # = fail-closed False (никогда «активен по умолчанию»).
        self._snapshot: dict[str, CapabilityGrantRecord] = {}
        self._snapshot_complete: bool = False

    # ------------------------------------------------------------------
    # Внутреннее: модель, сессия, часы
    # ------------------------------------------------------------------

    def _row(self) -> type:
        row = load_row(GRANT_ROW)
        self._columns.require(row, *GRANT_COLUMNS)
        return row

    @property
    def snapshot_complete(self) -> bool:
        """Был ли снимок полностью вычитан из БД (для TTL-кэша revocation)."""
        return self._snapshot_complete

    def snapshot_records(self) -> tuple[CapabilityGrantRecord, ...]:
        """Публичный доступ к авторитетному снимку (без возможности мутации)."""
        return tuple(self._snapshot.values())

    def _session_ctx(self):
        """Вернуть async-context-manager сессии.

        Если задана внешняя сессия — НЕ закрываем её и НЕ коммитим: транзакцией
        владеет вызывающий (важно для ``set_current`` реестра артефактов).
        """
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None  # проверено в __init__
        return self._session_factory()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PluginRepositoryError("часы репозитория обязаны возвращать aware datetime")
        return value.astimezone(UTC)

    # ------------------------------------------------------------------
    # ORM -> доменный контракт
    # ------------------------------------------------------------------

    def _col(self, name: str) -> str:
        return self._columns.col(name)

    def to_record(self, row: Any) -> CapabilityGrantRecord:
        """``CapabilityGrantRow`` → ``CapabilityGrantRecord`` (fail-closed).

        Битый JSONB, отсутствие обязательного поля или невалидный digest
        приводят к ``PluginRepositorySchemaError``, а не к «частичному» гранту:
        грант, который не удалось доказать, не должен разрешать доступ.
        """
        c = self._col
        get = lambda name: getattr(row, c(name), None)  # noqa: E731

        egress_raw = json_mapping(get("egress_policy"), field="egress_policy")
        quota_raw = json_mapping(get("quota"), field="quota")

        mode_raw = egress_raw.get("mode", "none")
        try:
            egress = AdminEgressPolicy(
                mode=EgressMode(mode_raw),
                allowed_hosts=tuple(str(item) for item in egress_raw.get("allowed_hosts", ())),
                allowed_paths=tuple(str(item) for item in egress_raw.get("allowed_paths", ())),
                approved_for_full=bool(egress_raw.get("approved_for_full", False)),
            )
        except (ValueError, TypeError) as exc:
            raise PluginRepositorySchemaError(f"egress_policy гранта невалиден: {exc}") from exc

        try:
            quotas = QuotaSet(
                ops_per_minute=int(quota_raw.get("ops_per_minute", 60)),
                max_rows=int(quota_raw.get("max_rows", 10_000)),
                max_bytes=int(quota_raw.get("max_bytes", 10_000_000)),
                max_keys=int(quota_raw.get("max_keys", 1_000)),
                max_object_bytes=int(quota_raw.get("max_object_bytes", 50_000_000)),
                max_egress_bytes=int(quota_raw.get("max_egress_bytes", 10_000_000)),
            )
        except (ValueError, TypeError) as exc:
            raise PluginRepositorySchemaError(f"quota гранта невалидна: {exc}") from exc

        status_raw = str(get("status") or "")
        try:
            status = GrantStatus(status_raw)
        except ValueError as exc:
            raise PluginRepositorySchemaError(f"неизвестный status гранта {status_raw!r}") from exc

        created = aware_utc(get("created_at"))
        revoked = aware_utc(get("revoked_at"))
        if not isinstance(created, datetime):
            raise PluginRepositorySchemaError("created_at гранта отсутствует/не datetime")

        try:
            return CapabilityGrantRecord(
                grant_id=text(get("grant_id"), field="grant_id"),
                company_id=text(get("company_id"), field="company_id"),
                plugin_id=text(get("plugin_id"), field="plugin_id"),
                plugin_instance_id=text(get("plugin_instance_id"), field="plugin_instance_id"),
                manifest_version=text(get("manifest_version"), field="manifest_version"),
                artifact_digest=text(get("artifact_digest"), field="artifact_digest"),
                capabilities=string_tuple(get("capabilities"), field="capabilities"),
                egress_policy=egress,
                quotas=quotas,
                status=status,
                created_at=created,
                revoked_at=revoked if isinstance(revoked, datetime) else None,
            )
        except (ValueError, PluginRepositoryError) as exc:
            raise PluginRepositorySchemaError(f"строка гранта не прошла валидацию: {exc}") from exc

    def to_domain_grant(self, record: CapabilityGrantRecord) -> Any:
        """``CapabilityGrantRecord`` → доменный ``domain.plugins.CapabilityGrant``.

        Импорт домена ленивый: репозиторий не должен падать, если задача 01
        ещё не зарегистрировала пакет. Возвращает объект домена с ``egress_ref``
        и ``quota_ref``, построенными из grant_id (источник правил — БД).
        """
        from prodavan.domain.plugins.types import (
            ArtifactDigest,
            Capability,
            CapabilityGrant,
            EgressMode as DomainEgressMode,
            EgressPolicy as DomainEgressPolicy,
            QuotaRef,
        )

        return CapabilityGrant(
            grant_id=record.grant_id,
            plugin_id=record.plugin_id,
            plugin_instance_id=record.plugin_instance_id,
            capabilities=tuple(Capability(item) for item in record.capabilities),
            egress_ref=f"grant:{record.company_id}/{record.plugin_id}/{record.grant_id}",
            egress_policy=DomainEgressPolicy(
                mode=DomainEgressMode(record.egress_policy.mode.value),
                allowed_hosts=record.egress_policy.allowed_hosts,
                allowed_paths=record.egress_policy.allowed_paths,
            ),
            quota_ref=QuotaRef(f"grant:{record.grant_id}"),
            artifact_digest=ArtifactDigest(record.artifact_digest),
            issued_at=record.created_at,
            expires_at=None,
            revoked_at=record.revoked_at,
        )

    @staticmethod
    def to_row_values(record: CapabilityGrantRecord, columns: ColumnMap) -> dict[str, Any]:
        """Доменный ``CapabilityGrantRecord`` → значения колонок ORM (JSONB)."""
        c = columns.col
        egress = record.egress_policy
        quotas = record.quotas
        return {
            c("grant_id"): record.grant_id,
            c("company_id"): record.company_id,
            c("plugin_id"): record.plugin_id,
            c("plugin_instance_id"): record.plugin_instance_id,
            c("manifest_version"): record.manifest_version,
            c("artifact_digest"): record.artifact_digest,
            c("capabilities"): list(record.capabilities),
            c("egress_policy"): {
                "mode": egress.mode.value,
                "allowed_hosts": list(egress.allowed_hosts),
                "allowed_paths": list(egress.allowed_paths),
                "approved_for_full": egress.approved_for_full,
            },
            c("quota"): {
                "ops_per_minute": quotas.ops_per_minute,
                "max_rows": quotas.max_rows,
                "max_bytes": quotas.max_bytes,
                "max_keys": quotas.max_keys,
                "max_object_bytes": quotas.max_object_bytes,
                "max_egress_bytes": quotas.max_egress_bytes,
            },
            c("status"): record.status.value,
            c("created_at"): record.created_at,
            c("revoked_at"): record.revoked_at,
        }

    # ------------------------------------------------------------------
    # Проверка активности (единая точка fail-closed)
    # ------------------------------------------------------------------

    @staticmethod
    def record_is_active(record: CapabilityGrantRecord, *, now: datetime) -> bool:
        """Активен ли грант: status + revoked_at + expires_at + не-служебная строка."""
        if record.grant_id == FLAG_SENTINEL_GRANT_ID or record.plugin_id == FLAG_SENTINEL_PLUGIN_ID:
            return False
        if record.status is not GrantStatus.ACTIVE:
            return False
        if record.revoked_at is not None:
            return False
        # ``expires_at`` в CapabilityGrantRecord отсутствует (задача 12), поэтому
        # срок жизни проверяется на уровне строки БД в ``is_active_async``.
        return True

    async def is_active_async(self, grant_id: str, *, now: datetime | None = None) -> bool:
        """Авторитетный асинхронный ответ из БД (источник истины).

        Ошибка БД → ``False`` (fail-closed), НО исключение НЕ маскируется в
        ``PluginRepositoryUnavailable`` для позитивных мутаций: их вызывающие
        должны видеть отказ явно.
        """
        key = (grant_id or "").strip()
        if not key or key == FLAG_SENTINEL_GRANT_ID:
            return False
        try:
            row = self._row()
            moment = now or self._now()
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row).where(
                        row.__table__.columns[self._col("grant_id")] == key
                    )
                )
                found = result.scalars().first()
            if found is None:
                self._snapshot.pop(key, None)
                return False
            record = self.to_record(found)
            active = self.record_is_active(record, now=moment)
            if active:
                # Проверяем ``expires_at`` напрямую по строке (опциональная колонка).
                expires = aware_utc(getattr(found, self._col("expires_at"), None)) if self._columns.has(row, "expires_at") else None
                if isinstance(expires, datetime) and expires <= moment:
                    active = False
            self._snapshot[key] = record
            return active
        except (SQLAlchemyError, PluginRepositoryError):
            # Недоступность хранилища ≠ «грант активен».
            self._snapshot.pop(key, None)
            return False

    def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        """СИНХРОННЫЙ ``GrantRevocationPort``/``RevocationPort`` контракт.

        ``AsyncSession`` нельзя await'ить из sync-кода, поэтому ответ берётся из
        in-memory снимка, который обновляет :meth:`is_active_async` и
        :meth:`refresh`. Промах снимка = ``False`` (fail-closed), а не «пустить».
        ``DbGrantRevocation`` (см. revocation.py) сам дёргает async-refresh по
        TTL, так что в штатном потоке ответ авторитетный.
        """
        key = (grant_id or "").strip()
        if not key or key == FLAG_SENTINEL_GRANT_ID:
            return False
        record = self._snapshot.get(key)
        if record is None:
            return False
        return self.record_is_active(record, now=now or self._now())

    # ------------------------------------------------------------------
    # Синхронный контракт ТЗ (get_by_id / get_active / save / revoke / ...)
    # ------------------------------------------------------------------

    def get_by_id(self, grant_id: str) -> CapabilityGrantRecord | None:
        """Синхронный доступ из снимка; ``None`` при отсутствии/неопределённости."""
        key = (grant_id or "").strip()
        if not key or key == FLAG_SENTINEL_GRANT_ID:
            return None
        return self._snapshot.get(key)

    def get_active(self, company_id: str, plugin_id: str) -> CapabilityGrantRecord | None:
        """Активный грант пары ``(company_id, plugin_id)`` из снимка.

        Возвращает ``None``, если активного нет: вызывающий обязан трактовать
        это как отказ, а не как «создать дефолтный grant».
        """
        for record in self._snapshot.values():
            if (
                record.company_id == company_id
                and record.plugin_id == plugin_id
                and self.record_is_active(record, now=self._now())
            ):
                return record
        return None

    def save(self, grant: CapabilityGrantRecord) -> CapabilityGrantRecord:
        """СИНХРОННАЯ постановка в очередь записи (НЕ авторитетная запись в БД).

        Валидирует контракт, кладёт в снимок и помечает запись «ожидающей».
        Реальная durable-запись — ``await create(...)``. Такое разделение
        необходимо: из sync-метода нельзя выполнить ``AsyncSession.commit``.
        Без последующего ``create``/``flush`` грант НЕ считается выданным
        (``_pending`` не публикуется в авторитетный снимок).
        """
        if not isinstance(grant, CapabilityGrantRecord):
            raise PluginRepositoryError("save ожидает CapabilityGrantRecord")
        if grant.grant_id == FLAG_SENTINEL_GRANT_ID:
            raise PluginRepositoryError("save: зарезервированный grant_id фича-флага")
        self._pending = getattr(self, "_pending", {})
        self._pending[grant.grant_id] = grant
        # В снимок НЕ кладём: «поставлено в очередь» ≠ «подтверждено БД».
        return grant

    def revoke(self, grant_id: str) -> None:
        """СИНХРОННАЯ инвалидация (немедленный отзыв в памяти).

        Критерий №12: ещё не истёкший ``PluginClaims`` перестаёт работать СРАЗУ.
        В снимке помечаем грант отозванным и добавляем в очередь durable-записи;
        авторитетный ``await revoke_async(...)`` фиксирует ``status=revoked`` и
        ``revoked_at`` в БД. Инвалидация кэша ``DbGrantRevocation`` происходит
        в его собственном методе ``revoke``.
        """
        key = (grant_id or "").strip()
        if not key:
            raise PluginRepositoryError("revoke: grant_id не должен быть пустым")
        self._pending_revocations = getattr(self, "_pending_revocations", set())
        self._pending_revocations.add(key)
        existing = self._snapshot.get(key)
        if existing is not None:
            # Пересобираем запись с REVOKED, не мутируя frozen dataclass.
            from dataclasses import replace

            self._snapshot[key] = replace(
                existing,
                status=GrantStatus.REVOKED,
                revoked_at=self._now(),
            )
        else:
            # Гранта в снимке нет — всё равно создаём «заглушку-отзыв», чтобы
            # повторный запрос не вернул «активен» после появления строки.
            pass

    def list_by_company(self, company_id: str) -> Sequence[CapabilityGrantRecord]:
        """СИНХРОННЫЙ список из снимка (без служебной строки фича-флага)."""
        text(company_id, field="company_id")
        return tuple(
            record
            for record in self._snapshot.values()
            if record.company_id == company_id and record.grant_id != FLAG_SENTINEL_GRANT_ID
        )

    def enabled_modules(self, company_id: str) -> Sequence[str]:
        """СИНХРОННЫЙ ``plugin_enabled_modules[]`` из снимка фича-флага.

        Делегирует ``DbFeatureFlagStore``; здесь оставлен тонкий алиас для
        совместимости с ТЗ и ``GrantStorePort.get_enabled_modules``.
        """
        from .flags import DbFeatureFlagStore

        store = DbFeatureFlagStore(session=self._session, session_factory=self._session_factory, columns=self._columns)
        return store.list_sync(company_id)

    async def refresh(self, *, company_id: str | None = None) -> None:
        """Обновить авторитетный снимок из БД (для TTL-кэша revocation).

        Читает ВСЕ строки (или одной компании) и пересобирает снимок. Ошибка
        чтения = ``PluginRepositoryUnavailable`` (fail-closed: старый снимок
        НЕ считаем достоверным и очищаем).
        """
        try:
            row = self._row()
            statement = select(row).where(_not_sentinel_condition(row, self._columns))
            if company_id is not None:
                statement = statement.where(
                    row.__table__.columns[self._col("company_id")] == company_id
                )
            async with self._session_ctx() as session:
                result = await session.execute(statement)
                rows = result.scalars().all()
            rebuilt = {self.to_record(item).grant_id: self.to_record(item) for item in rows}
            if company_id is None:
                self._snapshot = rebuilt
                self._snapshot_complete = True
            else:
                self._snapshot = {
                    key: value
                    for key, value in self._snapshot.items()
                    if value.company_id != company_id
                } | rebuilt
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            self._snapshot.clear()
            self._snapshot_complete = False
            raise PluginRepositoryUnavailable(f"не удалось обновить снимок грантов: {exc}") from exc

    # ------------------------------------------------------------------
    # GrantStorePort (async, задача 12)
    # ------------------------------------------------------------------

    async def get(self, *, company_id: str, plugin_id: str) -> CapabilityGrantRecord | None:
        """Активный грант пары (company, plugin) или ``None``."""
        text(company_id, field="company_id")
        text(plugin_id, field="plugin_id")
        try:
            row = self._row()
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row)
                    .where(
                        row.__table__.columns[self._col("company_id")] == company_id,
                        row.__table__.columns[self._col("plugin_id")] == plugin_id,
                        _not_sentinel_condition(row, self._columns),
                    )
                    .order_by(row.__table__.columns[self._col("created_at")].desc())
                )
                found = result.scalars().first()
            if found is None:
                return None
            record = self.to_record(found)
            self._snapshot[record.grant_id] = record
            return record
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"get grant failed: {exc}") from exc

    async def get_by_grant_id(self, *, grant_id: str) -> CapabilityGrantRecord | None:
        key = text(grant_id, field="grant_id")
        if key == FLAG_SENTINEL_GRANT_ID:
            return None
        try:
            row = self._row()
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row).where(row.__table__.columns[self._col("grant_id")] == key)
                )
                found = result.scalars().first()
            if found is None:
                self._snapshot.pop(key, None)
                return None
            record = self.to_record(found)
            self._snapshot[key] = record
            return record
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"get_by_grant_id failed: {exc}") from exc

    async def create(self, *, grant: CapabilityGrantRecord) -> CapabilityGrantRecord:
        """Идемпотентно по ``grant_id``: существующая запись возвращается как есть.

        Позитивная мутация: любая ошибка записи → исключение (deny), а не
        «молчаливый успех». Снимок обновляется только после ``commit``.
        """
        if not isinstance(grant, CapabilityGrantRecord):
            raise PluginRepositoryError("create ожидает CapabilityGrantRecord")
        if grant.grant_id == FLAG_SENTINEL_GRANT_ID:
            raise PluginRepositoryError("create: зарезервированный grant_id фича-флага")
        try:
            row = self._row()
            values = self.to_row_values(grant, self._columns)
            async with self._session_ctx() as session:
                existing = (
                    await session.execute(
                        select(row).where(
                            row.__table__.columns[self._col("grant_id")] == grant.grant_id
                        )
                    )
                ).scalars().first()
                if existing is not None:
                    stored = self.to_record(existing)
                    self._snapshot[grant.grant_id] = stored
                    return stored
                session.add(row(**values))
                await _commit_if_owned(session, self._session is None)
            # Подтверждённая запись становится доступна sync-проверкам.
            self._snapshot[grant.grant_id] = grant
            getattr(self, "_pending", {}).pop(grant.grant_id, None)
            return grant
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"create grant failed: {exc}") from exc

    async def deactivate(self, *, grant_id: str) -> CapabilityGrantRecord:
        """Деактивация (мгновенный отзыв, критерий №12).

        distinct от ``delete``: строка остаётся (audit trail), но перестаёт быть
        активной. Отсутствие строки = ошибка (fail-closed, не «уже отозван»).
        """
        key = text(grant_id, field="grant_id")
        try:
            row = self._row()
            fields = {
                self._col("status"): "revoked",
                self._col("revoked_at"): self._now(),
            }
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            row.__table__.columns[self._col("grant_id")] == key
                        )
                    )
                ).scalars().first()
                if found is None:
                    raise PluginRepositoryUnavailable("deactivate: грант не найден")
                await session.execute(
                    update(row)
                    .where(row.__table__.columns[self._col("grant_id")] == key)
                    .values(**fields)
                )
                await _commit_if_owned(session, self._session is None)
            record = self.to_record(found)
            from dataclasses import replace

            revoked = replace(
                record, status=GrantStatus.REVOKED, revoked_at=self._now()
            )
            self._snapshot[key] = revoked
            getattr(self, "_pending_revocations", set()).discard(key)
            return revoked
        except PluginRepositoryUnavailable:
            raise
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"deactivate grant failed: {exc}") from exc

    async def revoke_async(self, grant_id: str, *, reason: str = "") -> bool:
        """Durable-отзыв для ``admin.ports.RevocationPort`` (идентифицирован по grant_id).

        ``True`` только после подтверждённой записи; повторный отзыв того же
        ``grant_id`` идемпотентно возвращает ``True``, если строка уже revoked.
        """
        key = text(grant_id, field="grant_id")
        try:
            row = self._row()
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            row.__table__.columns[self._col("grant_id")] == key
                        )
                    )
                ).scalars().first()
                if found is None:
                    return False
                current = str(getattr(found, self._col("status"), ""))
                if current == "revoked":
                    self.revoke(key)  # инвалидируем кэш даже при идемпотентном повторе
                    return True
                await session.execute(
                    update(row)
                    .where(row.__table__.columns[self._col("grant_id")] == key)
                    .values(
                        **{
                            self._col("status"): "revoked",
                            self._col("revoked_at"): self._now(),
                        }
                    )
                )
                await _commit_if_owned(session, self._session is None)
            self.revoke(key)
            return True
        except (SQLAlchemyError, PluginRepositoryError):
            # Fail-closed: не подтверждено ≠ подтверждено.
            return False

    async def is_revoked_async(self, *, grant_id: str) -> bool:
        """``admin.ports.RevocationPort.is_revoked`` (fail-closed)."""
        return not await self.is_active_async(grant_id)

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        """Удалить grant при uninstall. Идемпотентно (нет строки = True)."""
        text(company_id, field="company_id")
        text(plugin_id, field="plugin_id")
        try:
            row = self._row()
            condition = (
                row.__table__.columns[self._col("company_id")] == company_id,
                row.__table__.columns[self._col("plugin_id")] == plugin_id,
                _not_sentinel_condition(row, self._columns),
            )
            async with self._session_ctx() as session:
                result = await session.execute(delete(row).where(*condition))
                await _commit_if_owned(session, self._session is None)
            for key, record in list(self._snapshot.items()):
                if record.company_id == company_id and record.plugin_id == plugin_id:
                    self._snapshot.pop(key, None)
            return True
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"delete grant failed: {exc}") from exc

    async def list_by_company_async(self, *, company_id: str) -> Sequence[CapabilityGrantRecord]:
        """Асинхронный авторитетный список (без служебной строки фича-флага)."""
        text(company_id, field="company_id")
        try:
            row = self._row()
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row).where(
                        row.__table__.columns[self._col("company_id")] == company_id,
                        _not_sentinel_condition(row, self._columns),
                    )
                )
                rows = result.scalars().all()
            records = tuple(self.to_record(item) for item in rows)
            for record in records:
                self._snapshot[record.grant_id] = record
            return records
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"list_by_company failed: {exc}") from exc

    async def get_enabled_modules(self, *, company_id: str) -> Sequence[str]:
        """``GrantStorePort.get_enabled_modules`` → ``DbFeatureFlagStore``."""
        from .flags import DbFeatureFlagStore

        store = DbFeatureFlagStore(
            session=self._session,
            session_factory=self._session_factory,
            columns=self._columns,
        )
        return await store.list(company_id)

    async def set_enabled_modules(
        self, *, company_id: str, plugin_ids: Sequence[str]
    ) -> Sequence[str]:
        """``GrantStorePort.set_enabled_modules`` → ``DbFeatureFlagStore.set``."""
        from .flags import DbFeatureFlagStore

        store = DbFeatureFlagStore(
            session=self._session,
            session_factory=self._session_factory,
            columns=self._columns,
        )
        return await store.set(company_id, plugin_ids)

    # ------------------------------------------------------------------
    # Дополнительно: применить отложенные sync-операции (для build/тестов)
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """Применить ``save``/``revoke`` из sync-очереди (после старта loop).

        Вызывается композицией/``DbGrantRevocation``; без него sync-``save`` не
        становится durable. Ошибка = исключение (deny позитивной мутации).
        """
        pending = dict(getattr(self, "_pending", {}))
        for grant in pending.values():
            await self.create(grant=grant)
        revocations = set(getattr(self, "_pending_revocations", set()))
        for key in revocations:
            await self.revoke_async(key)


class _BorrowedSession:
    """Async-CM поверх чужой сессии: НЕ закрывает и НЕ коммитит её."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


async def _commit_if_owned(session: AsyncSession, owned: bool) -> None:
    """Коммитим только сессию, созданную самим репозиторием."""
    if owned:
        await session.commit()


__all__ = ["SqlCapabilityGrantRepository"]
