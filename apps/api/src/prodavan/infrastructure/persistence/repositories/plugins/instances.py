"""Durable PluginInstanceRow repository для admin lifecycle saga.

Запись появляется ДО БД/bucket/Deployment и исчезает только после подтверждённой
очистки. CREATE использует PostgreSQL ON CONFLICT DO NOTHING для idempotency.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.plugins.admin.contracts import (
    PluginInstanceRecord,
    PluginInstanceStatus,
)

from ._schema import (
    INSTANCE_ROW,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    aware_utc,
    load_row,
    text,
)

_INSTANCE_COLUMNS = (
    "id", "company_id", "plugin_id", "manifest_version", "artifact_digest",
    "status", "desired_replicas", "last_active_at", "active_job_ids",
    "wake_in_flight", "tenant_db_name", "bucket_name", "created_at", "updated_at",
)


class SqlPluginInstanceRepository:
    """Async InstanceStorePort + InstanceIdResolver поверх A10 ORM."""

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
                "SqlPluginInstanceRepository требует session_factory или session"
            )
        self._session_factory = session_factory
        self._session = session
        self._columns = columns or ColumnMap()
        self._clock = now or (lambda: datetime.now(UTC))

    def _row(self) -> type:
        row = load_row(INSTANCE_ROW)
        self._columns.require(row, *_INSTANCE_COLUMNS)
        return row

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
            raise PluginRepositoryError("часы instance repository должны возвращать aware datetime")
        return value.astimezone(UTC)

    def to_record(self, row: Any) -> PluginInstanceRecord:
        get = lambda name: getattr(row, self._col(name), None)  # noqa: E731
        try:
            status = PluginInstanceStatus(str(get("status") or ""))
            created = aware_utc(get("created_at"))
            updated = aware_utc(get("updated_at"))
            if not isinstance(created, datetime) or not isinstance(updated, datetime):
                raise PluginRepositorySchemaError("instance timestamps отсутствуют")
            return PluginInstanceRecord(
                plugin_instance_id=text(get("id"), field="plugin_instance_id"),
                company_id=text(get("company_id"), field="company_id"),
                plugin_id=text(get("plugin_id"), field="plugin_id"),
                manifest_version=text(get("manifest_version"), field="manifest_version"),
                artifact_digest=text(get("artifact_digest"), field="artifact_digest"),
                status=status,
                tenant_db_name=text(get("tenant_db_name"), field="tenant_db_name"),
                bucket_name=text(get("bucket_name"), field="bucket_name"),
                created_at=created,
                updated_at=updated,
            )
        except (ValueError, PluginRepositoryError) as exc:
            raise PluginRepositorySchemaError(f"некорректная PluginInstanceRow: {exc}") from exc

    def _values(self, instance: PluginInstanceRecord) -> dict[str, Any]:
        return {
            self._col("id"): instance.plugin_instance_id,
            self._col("company_id"): instance.company_id,
            self._col("plugin_id"): instance.plugin_id,
            self._col("manifest_version"): instance.manifest_version,
            self._col("artifact_digest"): instance.artifact_digest,
            self._col("status"): instance.status.value,
            self._col("desired_replicas"): 0,
            self._col("last_active_at"): None,
            self._col("active_job_ids"): [],
            self._col("wake_in_flight"): False,
            self._col("tenant_db_name"): instance.tenant_db_name,
            self._col("bucket_name"): instance.bucket_name,
            self._col("created_at"): instance.created_at,
            self._col("updated_at"): instance.updated_at,
        }

    @staticmethod
    def _assert_compatible(
        requested: PluginInstanceRecord, stored: PluginInstanceRecord
    ) -> None:
        """Повтор с новым generated id возвращает stored id; pin менять нельзя."""
        fields = (
            "company_id", "plugin_id", "manifest_version", "artifact_digest",
            "tenant_db_name", "bucket_name",
        )
        if any(getattr(requested, name) != getattr(stored, name) for name in fields):
            raise PluginRepositoryUnavailable(
                "create instance: конфликт immutable полей для company/plugin"
            )

    async def create(self, *, instance: PluginInstanceRecord) -> PluginInstanceRecord:
        if not isinstance(instance, PluginInstanceRecord):
            raise PluginRepositoryError("create ожидает PluginInstanceRecord")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                existing = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("company_id")] == instance.company_id,
                            table.columns[self._col("plugin_id")] == instance.plugin_id,
                        )
                    )
                ).scalars().first()
                if existing is not None:
                    stored = self.to_record(existing)
                    self._assert_compatible(instance, stored)
                    return stored
                statement = (
                    pg_insert(row)
                    .values(**self._values(instance))
                    .on_conflict_do_nothing()
                )
                result = await session.execute(statement)
                if result.rowcount == 0:
                    existing = (
                        await session.execute(
                            select(row).where(
                                table.columns[self._col("company_id")] == instance.company_id,
                                table.columns[self._col("plugin_id")] == instance.plugin_id,
                            )
                        )
                    ).scalars().first()
                    if existing is None:
                        raise PluginRepositoryUnavailable(
                            "create instance: конфликт PK без company/plugin записи"
                        )
                    stored = self.to_record(existing)
                    self._assert_compatible(instance, stored)
                    return stored
                if self._session is None:
                    await session.commit()
            return instance
        except PluginRepositoryUnavailable:
            raise
        except IntegrityError as exc:
            raise PluginRepositoryUnavailable("create instance: нарушение ограничения БД") from exc
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"create instance failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise PluginRepositoryUnavailable(
                f"create instance failed: {type(exc).__name__}"
            ) from exc

    async def get(
        self, *, company_id: str, plugin_id: str
    ) -> PluginInstanceRecord | None:
        text(company_id, field="company_id")
        text(plugin_id, field="plugin_id")
        return await self._get_one(company_id=company_id, plugin_id=plugin_id)

    async def get_by_id(self, *, plugin_instance_id: str) -> PluginInstanceRecord | None:
        key = text(plugin_instance_id, field="plugin_instance_id")
        return await self._get_one(plugin_instance_id=key)

    async def _get_one(
        self,
        *,
        company_id: str | None = None,
        plugin_id: str | None = None,
        plugin_instance_id: str | None = None,
    ) -> PluginInstanceRecord | None:
        try:
            row = self._row()
            table = row.__table__
            if plugin_instance_id is not None:
                conditions = [table.columns[self._col("id")] == plugin_instance_id]
            else:
                conditions = [
                    table.columns[self._col("company_id")] == company_id,
                    table.columns[self._col("plugin_id")] == plugin_id,
                ]
            async with self._session_ctx() as session:
                found = (
                    await session.execute(select(row).where(*conditions))
                ).scalars().first()
            return self.to_record(found) if found is not None else None
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"get instance failed: {exc}") from exc

    async def update_status(
        self, *, plugin_instance_id: str, status: PluginInstanceStatus
    ) -> PluginInstanceRecord:
        key = text(plugin_instance_id, field="plugin_instance_id")
        if not isinstance(status, PluginInstanceStatus):
            raise PluginRepositoryError("update_status ожидает PluginInstanceStatus")
        try:
            row = self._row()
            table = row.__table__
            changed_at = self._now()
            async with self._session_ctx() as session:
                found = (
                    await session.execute(select(row).where(table.columns[self._col("id")] == key))
                ).scalars().first()
                if found is None:
                    raise PluginRepositoryUnavailable("update_status: instance не найден")
                current = self.to_record(found)
                result = await session.execute(
                    update(row)
                    .where(table.columns[self._col("id")] == key)
                    .values(**{
                        self._col("status"): status.value,
                        self._col("updated_at"): changed_at,
                    })
                )
                if result.rowcount != 1:
                    raise PluginRepositoryUnavailable(
                        "update_status: SQL не подтвердил ровно одну строку"
                    )
                if self._session is None:
                    await session.commit()
            return PluginInstanceRecord(
                plugin_instance_id=current.plugin_instance_id,
                company_id=current.company_id,
                plugin_id=current.plugin_id,
                manifest_version=current.manifest_version,
                artifact_digest=current.artifact_digest,
                status=status,
                tenant_db_name=current.tenant_db_name,
                bucket_name=current.bucket_name,
                created_at=current.created_at,
                updated_at=changed_at,
            )
        except PluginRepositoryUnavailable:
            raise
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"update instance status failed: {exc}") from exc

    async def update_artifact_pin(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
        manifest_version: str,
        artifact_digest: str,
        expected_status: PluginInstanceStatus,
        status: PluginInstanceStatus,
    ) -> PluginInstanceRecord:
        """Compare-and-swap an artifact pin with an exact company/plugin fence."""

        for value, field in (
            (company_id, "company_id"),
            (plugin_id, "plugin_id"),
            (plugin_instance_id, "plugin_instance_id"),
            (expected_manifest_version, "expected_manifest_version"),
            (expected_artifact_digest, "expected_artifact_digest"),
            (manifest_version, "manifest_version"),
            (artifact_digest, "artifact_digest"),
        ):
            text(value, field=field)
        if not isinstance(expected_status, PluginInstanceStatus) or not isinstance(
            status, PluginInstanceStatus
        ):
            raise PluginRepositoryError("update_artifact_pin ожидает PluginInstanceStatus")
        try:
            row = self._row()
            table = row.__table__
            changed_at = self._now()
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(table.columns[self._col("id")] == plugin_instance_id)
                    )
                ).scalars().first()
                if found is None:
                    raise PluginRepositoryUnavailable("artifact CAS: instance не найден")
                current = self.to_record(found)
                if (current.company_id, current.plugin_id) != (company_id, plugin_id):
                    raise PluginRepositoryUnavailable(
                        "artifact CAS: company/plugin binding mismatch"
                    )
                if (
                    current.manifest_version == manifest_version
                    and current.artifact_digest == artifact_digest
                    and current.status is status
                ):
                    return current
                if (
                    current.manifest_version != expected_manifest_version
                    or current.artifact_digest != expected_artifact_digest
                    or current.status is not expected_status
                ):
                    raise PluginRepositoryUnavailable("artifact CAS: old pin fence mismatch")
                result = await session.execute(
                    update(row)
                    .where(
                        table.columns[self._col("id")] == plugin_instance_id,
                        table.columns[self._col("company_id")] == company_id,
                        table.columns[self._col("plugin_id")] == plugin_id,
                        table.columns[self._col("manifest_version")]
                        == expected_manifest_version,
                        table.columns[self._col("artifact_digest")] == expected_artifact_digest,
                        table.columns[self._col("status")] == expected_status.value,
                    )
                    .values(
                        **{
                            self._col("manifest_version"): manifest_version,
                            self._col("artifact_digest"): artifact_digest,
                            self._col("status"): status.value,
                            self._col("updated_at"): changed_at,
                        }
                    )
                    .returning(row)
                )
                returned = result.scalars().first()
                if result.rowcount != 1 or returned is None:
                    raise PluginRepositoryUnavailable(
                        "artifact CAS: UPDATE RETURNING не подтвердил одну строку"
                    )
                updated = self.to_record(returned)
                if (
                    updated.plugin_instance_id != plugin_instance_id
                    or updated.company_id != company_id
                    or updated.plugin_id != plugin_id
                    or updated.manifest_version != manifest_version
                    or updated.artifact_digest != artifact_digest
                    or updated.status is not status
                    or updated.tenant_db_name != current.tenant_db_name
                    or updated.bucket_name != current.bucket_name
                    or updated.created_at != current.created_at
                ):
                    raise PluginRepositoryUnavailable(
                        "artifact CAS: post-state binding/pin mismatch"
                    )
                if self._session is None:
                    await session.commit()
                return updated
        except PluginRepositoryUnavailable:
            raise
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"artifact CAS failed: {exc}") from exc

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        text(company_id, field="company_id")
        text(plugin_id, field="plugin_id")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                await session.execute(
                    delete(row).where(
                        table.columns[self._col("company_id")] == company_id,
                        table.columns[self._col("plugin_id")] == plugin_id,
                    )
                )
                if self._session is None:
                    await session.commit()
            return True
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"delete instance failed: {exc}") from exc

    async def resolve(self, *, company_id: str, plugin_id: str) -> str | None:
        found = await self.get(company_id=company_id, plugin_id=plugin_id)
        return found.plugin_instance_id if found is not None else None


class _BorrowedSession:
    """Не закрывать и не коммитить сессию владельца."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


__all__ = ["SqlPluginInstanceRepository"]
