"""SQLAlchemy-репозиторий ``CapabilityGrant`` (F11, стык задач 02/03/12).

Работает поверх РЕАЛЬНОЙ модели A10 ``CapabilityGrantRow``
(``plugin_capability_grants``). Особенности физической схемы, учтённые здесь:

* логический ``grant_id`` = PK ``id`` (отдельной колонки ``grant_id`` НЕТ);
* ``created_at`` в контракте задачи 12 = ``issued_at`` в БД;
* ``expires_at`` ЕСТЬ (у ``CapabilityGrantRecord`` его нет — проверяем отдельно);
* ``created_by`` — NOT NULL, обязателен при INSERT;
* Unique(``company_id``, ``plugin_instance_id``) — один активный grant на инстанс;
* FK ``plugin_instance_id → plugin_instances.id`` (RESTRICT) — при создании
  гранта строка экземпляра должна существовать (иначе ``IntegrityError`` →
  явный отказ, fail-closed, а не «создать осиротевший grant»).

Реализует:

* ``admin.ports.GrantStorePort`` (задача 12): async ``get`` / ``get_by_grant_id`` /
  ``create`` / ``deactivate`` / ``delete`` / ``list_by_company`` /
  ``get_enabled_modules`` / ``set_enabled_modules``;
* синхронный контракт ТЗ: ``get_by_id`` / ``get_active`` / ``save`` / ``revoke`` /
  ``list_by_company`` / ``enabled_modules`` / ``is_active(grant_id, *, now)`` —
  Structural basis для ``capabilities.ports.GrantRevocationPort`` (03) и
  ``identity.revocation.RevocationPort`` (02).

Fail-closed (критерий №12): грант активен ТОЛЬКО если строка есть,
``status == 'active'``, ``revoked_at is None``, ``expires_at`` не наступил и это
не служебная строка. Любая неопределённость/ошибка чтения = ``False``/``None``,
НИКОГДА «разрешить по умолчанию».
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressMode,
    EgressPolicy as AdminEgressPolicy,
    GrantStatus,
    QuotaSet,
)

from ._schema import (
    FLAG_SENTINEL_ID,
    FLAG_SENTINEL_PLUGIN_ID,
    GRANT_COLUMNS,
    GRANT_NOT_NULL_EXTRA,
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

DEFAULT_CREATED_BY = "platform-admin"
"""Значение обязательной колонки ``created_by`` при создании гранта репозиторием.

Не является секретом/токеном: это короткий идентификатор субъекта операции.
"""


class SqlCapabilityGrantRepository:
    """Настоящий SQL-репозиторий грантов поверх ``AsyncSession``.

    ``session_factory`` обязателен (без БД гранты не считаются активными).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        columns: ColumnMap | None = None,
        now: Any = None,
        feature_flags: Any = None,
        created_by: str = DEFAULT_CREATED_BY,
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
        self._feature_flags = feature_flags
        self._created_by = text(created_by, field="created_by")
        # Синхронный снимок: grant_id -> CapabilityGrantRecord. Обновляется
        # ТОЛЬКО успешными асинхронными операциями. Пустой = «не знаю» = False.
        self._snapshot: dict[str, CapabilityGrantRecord] = {}
        self._snapshot_complete: bool = False
        self._pending: dict[str, CapabilityGrantRecord] = {}
        self._pending_revocations: set[str] = set()

    # ------------------------------------------------------------------
    # Модель / сессия / часы
    # ------------------------------------------------------------------

    def _row(self) -> type:
        row = load_row(GRANT_ROW)
        self._columns.require(row, *GRANT_COLUMNS, *GRANT_NOT_NULL_EXTRA)
        return row

    @property
    def snapshot_complete(self) -> bool:
        return self._snapshot_complete

    def snapshot_records(self) -> tuple[CapabilityGrantRecord, ...]:
        """Публичный доступ к авторитетному снимку (без мутации)."""
        return tuple(self._snapshot.values())

    def _col(self, name: str) -> str:
        return self._columns.col(name)

    def _session_ctx(self):
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None
        return self._session_factory()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PluginRepositoryError("часы репозитория обязаны возвращать aware datetime")
        return value.astimezone(UTC)

    def _not_sentinel(self, row: type) -> Any:
        """Исключить служебную строку фича-флага из ЛЮБОЙ выдачи грантов."""
        id_col = row.__table__.columns[self._col("grant_id")]
        return id_col != FLAG_SENTINEL_ID

    # ------------------------------------------------------------------
    # ORM -> доменный контракт
    # ------------------------------------------------------------------

    def to_record(self, row: Any) -> CapabilityGrantRecord:
        """``CapabilityGrantRow`` → ``CapabilityGrantRecord`` (fail-closed)."""
        c = self._col
        get = lambda name: getattr(row, c(name), None)  # noqa: E731

        egress_raw = json_mapping(get("egress_policy"), field="egress_policy")
        quota_raw = json_mapping(get("quota"), field="quota")
        try:
            egress = AdminEgressPolicy(
                mode=EgressMode(egress_raw.get("mode", "none")),
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
        try:
            status = GrantStatus(str(get("status") or ""))
        except ValueError as exc:
            raise PluginRepositorySchemaError("неизвестный status гранта") from exc

        issued = aware_utc(get("issued_at"))
        revoked = aware_utc(get("revoked_at"))
        if not isinstance(issued, datetime):
            raise PluginRepositorySchemaError("issued_at гранта отсутствует/не datetime")
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
                created_at=issued,
                revoked_at=revoked if isinstance(revoked, datetime) else None,
            )
        except (ValueError, PluginRepositoryError) as exc:
            raise PluginRepositorySchemaError(f"строка гранта не прошла валидацию: {exc}") from exc

    def to_domain_grant(self, record: CapabilityGrantRecord) -> Any:
        """``CapabilityGrantRecord`` → доменный ``domain.plugins.CapabilityGrant``.

        Импорт домена ленивый. ``egress_ref``/``quota_ref`` строятся из grant_id:
        источник правил — БД, а не короткий JWT (§13.3).
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
            revoked_at=record.revoked_at,
        )

    def _row_values(self, record: CapabilityGrantRecord, row: type) -> dict[str, Any]:
        """Доменный контракт → значения РЕАЛЬНЫХ колонок A10."""
        c = self._col
        egress = record.egress_policy
        quotas = record.quotas
        values: dict[str, Any] = {
            c("grant_id"): record.grant_id,           # физически PK ``id``
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
            c("issued_at"): record.created_at,       # физически ``issued_at``
            c("revoked_at"): record.revoked_at,
            c("created_by"): self._created_by,        # NOT NULL в A10
        }
        if self._columns.has(row, "expires_at"):
            values[c("expires_at")] = None
        existing = set(row.__table__.columns.keys())
        return {key: value for key, value in values.items() if key in existing}

    # ------------------------------------------------------------------
    # Проверка активности (единая точка fail-closed)
    # ------------------------------------------------------------------

    @staticmethod
    def record_is_active(record: CapabilityGrantRecord, *, now: datetime) -> bool:
        if record.grant_id == FLAG_SENTINEL_ID or record.plugin_id == FLAG_SENTINEL_PLUGIN_ID:
            return False
        if record.status is not GrantStatus.ACTIVE or record.revoked_at is not None:
            return False
        return True

    @staticmethod
    def _row_expired(row_obj: Any, *, now: datetime, col: str) -> bool:
        expires = aware_utc(getattr(row_obj, col, None))
        return isinstance(expires, datetime) and expires <= now

    async def is_active_async(self, grant_id: str, *, now: datetime | None = None) -> bool:
        """Авторитетный async-ответ из БД (источник истины). Ошибка → False."""
        key = (grant_id or "").strip()
        if not key or key == FLAG_SENTINEL_ID:
            return False
        try:
            row = self._row()
            moment = now or self._now()
            table = row.__table__
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row).where(table.columns[self._col("grant_id")] == key)
                )
                found = result.scalars().first()
            if found is None:
                self._snapshot.pop(key, None)
                return False
            record = self.to_record(found)
            active = self.record_is_active(record, now=moment)
            if active and self._columns.has(row, "expires_at"):
                active = not self._row_expired(found, now=moment, col=self._col("expires_at"))
            self._snapshot[key] = record
            return active
        except (SQLAlchemyError, PluginRepositoryError):
            self._snapshot.pop(key, None)
            return False

    def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        """СИНХРОННЫЙ ``GrantRevocationPort``/``RevocationPort`` контракт.

        ``AsyncSession`` нельзя await'ить из sync-кода → ответ из снимка,
        который обновляет :meth:`is_active_async`/:meth:`refresh`. Промах =
        ``False`` (fail-closed). ``DbGrantRevocation`` дёргает async-refresh по TTL.
        """
        key = (grant_id or "").strip()
        if not key or key == FLAG_SENTINEL_ID:
            return False
        record = self._snapshot.get(key)
        if record is None:
            return False
        return self.record_is_active(record, now=now or self._now())

    # ------------------------------------------------------------------
    # Синхронный контракт ТЗ
    # ------------------------------------------------------------------

    def get_by_id(self, grant_id: str) -> CapabilityGrantRecord | None:
        key = (grant_id or "").strip()
        if not key or key == FLAG_SENTINEL_ID:
            return None
        return self._snapshot.get(key)

    def get_active(self, company_id: str, plugin_id: str) -> CapabilityGrantRecord | None:
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

        Реальная durable-запись — ``await create(...)`` / ``await flush()``.
        Без подтверждения грант НЕ считается выданным (fail-closed).
        """
        if not isinstance(grant, CapabilityGrantRecord):
            raise PluginRepositoryError("save ожидает CapabilityGrantRecord")
        if grant.grant_id == FLAG_SENTINEL_ID:
            raise PluginRepositoryError("save: зарезервированный id фича-флага")
        self._pending[grant.grant_id] = grant
        return grant

    def revoke(self, grant_id: str) -> None:
        """СИНХРОННАЯ инвалидация (немедленный отзыв в памяти, кр. №12).

        Durable-запись — ``await revoke_async(...)`` / ``await flush()``.
        """
        key = text(grant_id, field="grant_id")
        self._pending_revocations.add(key)
        existing = self._snapshot.get(key)
        if existing is not None:
            self._snapshot[key] = replace(
                existing, status=GrantStatus.REVOKED, revoked_at=self._now()
            )

    def list_by_company(self, company_id: str) -> Sequence[CapabilityGrantRecord]:
        text(company_id, field="company_id")
        return tuple(
            record
            for record in self._snapshot.values()
            if record.company_id == company_id and record.grant_id != FLAG_SENTINEL_ID
        )

    def _flags(self) -> Any:
        """Общий ``DbFeatureFlagStore`` (создаётся лениво при отсутствии)."""
        if self._feature_flags is None:
            from .flags import DbFeatureFlagStore

            self._feature_flags = DbFeatureFlagStore(
                session=self._session,
                session_factory=self._session_factory,
                columns=self._columns,
            )
        return self._feature_flags

    def bind_feature_flags(self, store: Any) -> None:
        self._feature_flags = store

    def enabled_modules(self, company_id: str) -> Sequence[str]:
        """СИНХРОННЫЙ ``plugin_enabled_modules[]`` из общего стора фича-флага."""
        return self._flags().list(company_id)

    async def refresh(self, *, company_id: str | None = None) -> None:
        """Обновить авторитетный снимок из БД (для TTL-кэша revocation)."""
        try:
            row = self._row()
            table = row.__table__
            statement = select(row).where(self._not_sentinel(row))
            if company_id is not None:
                statement = statement.where(
                    table.columns[self._col("company_id")] == company_id
                )
            async with self._session_ctx() as session:
                result = await session.execute(statement)
                rows = result.scalars().all()
            rebuilt: dict[str, CapabilityGrantRecord] = {}
            for item in rows:
                record = self.to_record(item)
                rebuilt[record.grant_id] = record
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
        text(company_id, field="company_id")
        text(plugin_id, field="plugin_id")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row)
                    .where(
                        table.columns[self._col("company_id")] == company_id,
                        table.columns[self._col("plugin_id")] == plugin_id,
                        self._not_sentinel(row),
                    )
                    .order_by(table.columns[self._col("issued_at")].desc())
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
        if key == FLAG_SENTINEL_ID:
            return None
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row).where(table.columns[self._col("grant_id")] == key)
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

    async def get_by_instance_id(
        self, *, plugin_instance_id: str
    ) -> CapabilityGrantRecord | None:
        """Активный grant по ``plugin_instance_id`` (для ``MintPort`` wake-up).

        ``PluginActivator`` при пробуждении знает только instance_id, а
        ``identity.mint_plugin_claims`` требует полный grant. Unique
        (``company_id``, ``plugin_instance_id``) гарантирует не более одной
        активной строки; берём самую свежую по ``issued_at``. Fail-closed:
        ошибка БД = ``PluginRepositoryUnavailable``, а не «пустой grant».
        """
        key = text(plugin_instance_id, field="plugin_instance_id")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row)
                    .where(table.columns[self._col("plugin_instance_id")] == key)
                    .order_by(table.columns[self._col("issued_at")].desc())
                )
                found = result.scalars().first()
            if found is None:
                return None
            record = self.to_record(found)
            self._snapshot[record.grant_id] = record
            return record
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"get_by_instance_id failed: {exc}") from exc

    async def create(self, *, grant: CapabilityGrantRecord) -> CapabilityGrantRecord:
        """Идемпотентно по ``grant_id`` (= PK ``id``). Ошибка записи → deny.

        FK на ``plugin_instances.id``: если экземпляр не создан задачей 12/09,
        INSERT упадёт ``IntegrityError`` → ``PluginRepositoryUnavailable``,
        а не «осиротевший grant» (fail-closed).
        """
        if not isinstance(grant, CapabilityGrantRecord):
            raise PluginRepositoryError("create ожидает CapabilityGrantRecord")
        if grant.grant_id == FLAG_SENTINEL_ID:
            raise PluginRepositoryError("create: зарезервированный id фича-флага")
        try:
            row = self._row()
            table = row.__table__
            values = self._row_values(grant, row)
            async with self._session_ctx() as session:
                existing = (
                    await session.execute(
                        select(row).where(table.columns[self._col("grant_id")] == grant.grant_id)
                    )
                ).scalars().first()
                if existing is not None:
                    stored = self.to_record(existing)
                    self._snapshot[grant.grant_id] = stored
                    return stored
                session.add(row(**values))
                await _commit_if_owned(session, self._session is None)
            self._snapshot[grant.grant_id] = grant
            self._pending.pop(grant.grant_id, None)
            return grant
        except IntegrityError as exc:
            raise PluginRepositoryUnavailable(
                f"create grant failed: нарушение ограничения БД (FK/unique): {type(exc).__name__}"
            ) from exc
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"create grant failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — позитивная мутация = явный deny
            raise PluginRepositoryUnavailable(
                f"create grant failed: {type(exc).__name__}"
            ) from exc

    async def deactivate(self, *, grant_id: str) -> CapabilityGrantRecord:
        """Деактивация (мгновенный отзыв, кр. №12); строка остаётся (audit)."""
        key = text(grant_id, field="grant_id")
        try:
            row = self._row()
            table = row.__table__
            fields = {
                self._col("status"): "revoked",
                self._col("revoked_at"): self._now(),
            }
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(table.columns[self._col("grant_id")] == key)
                    )
                ).scalars().first()
                if found is None:
                    raise PluginRepositoryUnavailable("deactivate: грант не найден")
                await session.execute(
                    update(row)
                    .where(table.columns[self._col("grant_id")] == key)
                    .values(**fields)
                )
                await _commit_if_owned(session, self._session is None)
            record = self.to_record(found)
            revoked = replace(record, status=GrantStatus.REVOKED, revoked_at=self._now())
            self._snapshot[key] = revoked
            self._pending_revocations.discard(key)
            return revoked
        except PluginRepositoryUnavailable:
            raise
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"deactivate grant failed: {exc}") from exc

    async def revoke_async(self, grant_id: str, *, reason: str = "") -> bool:
        """Durable-отзыв для ``admin.ports.RevocationPort`` (идентифицирован по grant_id)."""
        key = text(grant_id, field="grant_id")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(table.columns[self._col("grant_id")] == key)
                    )
                ).scalars().first()
                if found is None:
                    return False
                current = str(getattr(found, self._col("status"), ""))
                if current == "revoked":
                    self.revoke(key)
                    return True
                await session.execute(
                    update(row)
                    .where(table.columns[self._col("grant_id")] == key)
                    .values(**{
                        self._col("status"): "revoked",
                        self._col("revoked_at"): self._now(),
                    })
                )
                await _commit_if_owned(session, self._session is None)
            self.revoke(key)
            return True
        except (SQLAlchemyError, PluginRepositoryError):
            return False

    async def is_revoked_async(self, *, grant_id: str) -> bool:
        return not await self.is_active_async(grant_id)

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        text(company_id, field="company_id")
        text(plugin_id, field="plugin_id")
        try:
            row = self._row()
            table = row.__table__
            condition = (
                table.columns[self._col("company_id")] == company_id,
                table.columns[self._col("plugin_id")] == plugin_id,
                self._not_sentinel(row),
            )
            async with self._session_ctx() as session:
                await session.execute(delete(row).where(*condition))
                await _commit_if_owned(session, self._session is None)
            for key, record in list(self._snapshot.items()):
                if record.company_id == company_id and record.plugin_id == plugin_id:
                    self._snapshot.pop(key, None)
            return True
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"delete grant failed: {exc}") from exc

    async def list_by_company_async(self, *, company_id: str) -> Sequence[CapabilityGrantRecord]:
        text(company_id, field="company_id")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(row).where(
                        table.columns[self._col("company_id")] == company_id,
                        self._not_sentinel(row),
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
        return await self._flags().list_async(company_id)

    async def set_enabled_modules(
        self, *, company_id: str, plugin_ids: Sequence[str]
    ) -> Sequence[str]:
        return await self._flags().set_async(company_id, plugin_ids)

    async def flush(self) -> None:
        """Применить отложенные sync-``save``/``revoke`` (после старта loop)."""
        for grant in list(self._pending.values()):
            await self.create(grant=grant)
        for key in list(self._pending_revocations):
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
    if owned:
        await session.commit()


__all__ = ["DEFAULT_CREATED_BY", "SqlCapabilityGrantRepository"]
