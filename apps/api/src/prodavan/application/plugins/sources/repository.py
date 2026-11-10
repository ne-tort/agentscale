"""Async SQLAlchemy persistence for ModuleSource and provisioning reservations.

Ordinary request-session operations retain the caller-owned commit contract.
Distributed provisioning operations always use an owned short session: the
reservation is committed before any provider HTTP call, so no database
transaction remains open while GitHub is creating or protecting a repository.
Immutable repository/company identities are never overwritten by generic updates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.infrastructure.persistence.models.plugin_platform import (
    ModuleSourceRow,
    PluginSourceReservationRow,
)

from .ports import ModuleSourceProvisioningRepositoryPort
from .types import (
    ModuleSource,
    ModuleSourceProvisioningReservation,
    ModuleSourceReservationResult,
    ModuleSourceReservationStatus,
    ModuleSourceStatus,
)


class ModuleSourceRepositoryError(RuntimeError):
    """Типизированный fail-closed отказ source repository."""


class ModuleSourceConflictError(ModuleSourceRepositoryError):
    """Нарушена уникальность source или pre-provider reservation."""


class SqlModuleSourceRepository(ModuleSourceProvisioningRepositoryPort):
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        if (session is None) == (session_factory is None):
            raise ValueError("provide exactly one AsyncSession or async_sessionmaker")
        if session is not None and not isinstance(session, AsyncSession):
            raise TypeError("SqlModuleSourceRepository requires AsyncSession")
        self._session = session
        self._session_factory = session_factory
        # A direct request session is never committed by distributed
        # provisioning. Production composition supplies an owned factory; a
        # request-only repository fails closed before provider side effects.
        self._reservation_session_factory = session_factory

    @asynccontextmanager
    async def _session_ctx(self) -> AsyncIterator[AsyncSession]:
        """Borrow a request session or own/commit a short factory session."""
        if self._session is not None:
            yield self._session
            return
        assert self._session_factory is not None
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def _reservation_session_ctx(self) -> AsyncIterator[AsyncSession]:
        """Own every distributed provisioning transaction, including commit."""
        factory = self._reservation_session_factory
        if factory is None:
            raise ModuleSourceRepositoryError(
                "distributed provisioning requires an independent AsyncSession factory"
            )
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def _completion_session_ctx(self) -> AsyncIterator[AsyncSession]:
        """Use owned completion for reservations, legacy caller commit otherwise."""
        if self._reservation_session_factory is None:
            async with self._session_ctx() as session:
                yield session
            return
        async with self._reservation_session_ctx() as session:
            yield session

    async def reserve_provisioning(
        self, reservation: ModuleSourceProvisioningReservation
    ) -> ModuleSourceReservationResult:
        """Atomically insert a distributed reservation or return its winner."""
        if not isinstance(reservation, ModuleSourceProvisioningReservation):
            raise TypeError("reserve_provisioning requires a provisioning reservation")
        statement = (
            pg_insert(PluginSourceReservationRow)
            .values(**self._reservation_values(reservation))
            .on_conflict_do_nothing()
            .returning(PluginSourceReservationRow.id)
        )
        try:
            async with self._reservation_session_ctx() as session:
                inserted_id = (
                    await session.execute(statement)
                ).scalar_one_or_none()
                if inserted_id is not None:
                    row = await session.get(PluginSourceReservationRow, inserted_id)
                    if row is None:
                        raise ModuleSourceRepositoryError(
                            "inserted provisioning reservation could not be read"
                        )
                    return ModuleSourceReservationResult(
                        reservation=self._to_reservation(row), acquired=True
                    )
                existing = await self._find_conflicting_reservation(
                    session, reservation
                )
                return ModuleSourceReservationResult(
                    reservation=self._to_reservation(existing), acquired=False
                )
        except ModuleSourceRepositoryError:
            raise
        except IntegrityError as exc:
            raise ModuleSourceConflictError(
                "ModuleSource reservation violates a unique identity"
            ) from exc
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError(
                "could not reserve ModuleSource provisioning"
            ) from exc

    async def retry_provisioning(
        self,
        reservation: ModuleSourceProvisioningReservation,
        *,
        attempt_id: str,
    ) -> ModuleSourceReservationResult:
        """CAS one idle failed attempt; competing retry callers cannot both win."""
        if not isinstance(reservation, ModuleSourceProvisioningReservation):
            raise TypeError("retry_provisioning requires a provisioning reservation")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("attempt_id must be a non-empty fencing value")
        statement = (
            update(PluginSourceReservationRow)
            .where(
                PluginSourceReservationRow.id == reservation.id,
                PluginSourceReservationRow.attempt_id == reservation.attempt_id,
                PluginSourceReservationRow.status
                == ModuleSourceReservationStatus.PROVISIONING.value,
                PluginSourceReservationRow.create_in_progress.is_(False),
            )
            .values(
                attempt_id=attempt_id,
                create_in_progress=True,
                updated_at=func.now(),
            )
            .returning(PluginSourceReservationRow.id)
        )
        try:
            async with self._reservation_session_ctx() as session:
                acquired_id = (
                    await session.execute(statement)
                ).scalar_one_or_none()
                row = await session.get(PluginSourceReservationRow, reservation.id)
                if row is None:
                    raise ModuleSourceConflictError(
                        "provisioning reservation disappeared during retry"
                    )
                return ModuleSourceReservationResult(
                    reservation=self._to_reservation(row),
                    acquired=acquired_id is not None,
                )
        except ModuleSourceRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError(
                "could not retry ModuleSource provisioning"
            ) from exc

    async def release_provisioning_attempt(
        self, reservation_id: str, *, attempt_id: str
    ) -> None:
        """Record a failed create as retryable, fenced by its attempt id."""
        statement = (
            update(PluginSourceReservationRow)
            .where(
                PluginSourceReservationRow.id == reservation_id,
                PluginSourceReservationRow.attempt_id == attempt_id,
                PluginSourceReservationRow.status
                == ModuleSourceReservationStatus.PROVISIONING.value,
                PluginSourceReservationRow.create_in_progress.is_(True),
            )
            .values(create_in_progress=False, updated_at=func.now())
        )
        try:
            async with self._reservation_session_ctx() as session:
                # A zero row count is intentional: a newer attempt or completion
                # owns the row and must never be changed by an old worker.
                await session.execute(statement)
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError(
                "could not record the failed ModuleSource create attempt"
            ) from exc

    async def create(self, source: ModuleSource) -> ModuleSource:
        if not isinstance(source, ModuleSource):
            raise TypeError("create requires ModuleSource")
        row = ModuleSourceRow(
            id=source.id,
            company_id=source.company_id,
            plugin_id=source.plugin_id,
            cabinet_id=source.cabinet_id,
            project_id=source.project_id,
            provider=source.provider,
            repo_id=source.repo_id,
            full_name=source.full_name,
            private=source.private,
            default_branch=source.default_branch,
            installation_credential_ref=source.installation_credential_ref,
            trusted_workflow_ref=source.trusted_workflow_ref,
            status=source.status.value,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )
        try:
            async with self._session_ctx() as session:
                session.add(row)
                await session.flush()
                return self._to_domain(row)
        except IntegrityError as exc:
            raise ModuleSourceConflictError(
                "ModuleSource violates company+plugin or provider+repo_id uniqueness"
            ) from exc
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError("could not create ModuleSource") from exc

    async def bind_scope(self, source: ModuleSource) -> ModuleSource:
        """Bind paired scope once; exact replay is allowed, changes are not."""
        if source.cabinet_id is None or source.project_id is None:
            raise ValueError("bind_scope requires paired cabinet_id/project_id")
        try:
            async with self._session_ctx() as session:
                bound_id = (
                    await session.execute(
                        update(ModuleSourceRow)
                        .where(
                            ModuleSourceRow.id == source.id,
                            ModuleSourceRow.company_id == source.company_id,
                            ModuleSourceRow.plugin_id == source.plugin_id,
                            ModuleSourceRow.status != ModuleSourceStatus.DISABLED.value,
                            or_(
                                ModuleSourceRow.cabinet_id.is_(None),
                                ModuleSourceRow.cabinet_id == source.cabinet_id,
                            ),
                            or_(
                                ModuleSourceRow.project_id.is_(None),
                                ModuleSourceRow.project_id == source.project_id,
                            ),
                        )
                        .values(
                            cabinet_id=source.cabinet_id,
                            project_id=source.project_id,
                            updated_at=func.now(),
                        )
                        .returning(ModuleSourceRow.id)
                    )
                ).scalar_one_or_none()
                if bound_id is None:
                    raise ModuleSourceConflictError(
                        "ModuleSource scope is missing, disabled, or already bound differently"
                    )
                reservation = (
                    await session.execute(
                        select(PluginSourceReservationRow).where(
                            PluginSourceReservationRow.company_id == source.company_id,
                            PluginSourceReservationRow.plugin_id == source.plugin_id,
                        )
                    )
                ).scalar_one_or_none()
                if reservation is not None:
                    if (
                        reservation.cabinet_id not in {None, source.cabinet_id}
                        or reservation.project_id not in {None, source.project_id}
                    ):
                        raise ModuleSourceConflictError(
                            "provisioning reservation has a different scope binding"
                        )
                    reservation.cabinet_id = source.cabinet_id
                    reservation.project_id = source.project_id
                    reservation.updated_at = func.now()
                await session.flush()
                row = await session.get(ModuleSourceRow, source.id)
                if row is None:
                    raise ModuleSourceConflictError("bound ModuleSource disappeared")
                return self._to_domain(row)
        except ModuleSourceRepositoryError:
            raise
        except IntegrityError as exc:
            raise ModuleSourceConflictError(
                "ModuleSource scope violates ProjectRow constraints"
            ) from exc
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError(
                "could not bind ModuleSource installation scope"
            ) from exc

    async def get_by_id(self, source_id: str) -> ModuleSource | None:
        if not isinstance(source_id, str) or not source_id.strip():
            return None
        return await self._get_one(ModuleSourceRow.id == source_id)

    async def get_by_company_plugin(
        self, *, company_id: str, plugin_id: str
    ) -> ModuleSource | None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (company_id, plugin_id)
        ):
            return None
        return await self._get_one(
            ModuleSourceRow.company_id == company_id,
            ModuleSourceRow.plugin_id == plugin_id,
        )

    async def get_by_provider_repo(
        self, *, provider: str, repo_id: int
    ) -> ModuleSource | None:
        if (
            provider != "github_app"
            or isinstance(repo_id, bool)
            or not isinstance(repo_id, int)
            or repo_id <= 0
        ):
            return None
        return await self._get_one(
            ModuleSourceRow.provider == provider,
            ModuleSourceRow.repo_id == repo_id,
        )

    async def _get_one(self, *conditions: object) -> ModuleSource | None:
        try:
            async with self._session_ctx() as session:
                found = (
                    await session.execute(select(ModuleSourceRow).where(*conditions))
                ).scalar_one_or_none()
                return self._to_domain(found) if found is not None else None
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError("could not read ModuleSource") from exc

    async def set_status(
        self, source_id: str, status: ModuleSourceStatus
    ) -> ModuleSource | None:
        if not isinstance(status, ModuleSourceStatus):
            raise ValueError("unknown ModuleSource status")
        try:
            async with self._session_ctx() as session:
                row = await session.get(ModuleSourceRow, source_id)
                if row is None:
                    return None
                row.status = status.value
                await session.flush()
                return self._to_domain(row)
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError(
                "could not update ModuleSource status"
            ) from exc

    async def complete_provisioning(self, source_id: str) -> ModuleSource | None:
        """Commit source READY and reservation READY in one short transaction."""
        try:
            async with self._completion_session_ctx() as session:
                row = await session.get(ModuleSourceRow, source_id)
                if row is None:
                    return None
                if row.status == ModuleSourceStatus.DISABLED.value:
                    raise ModuleSourceConflictError(
                        "a disabled ModuleSource cannot complete provisioning"
                    )
                row.status = ModuleSourceStatus.READY.value
                reservation = (
                    await session.execute(
                        select(PluginSourceReservationRow).where(
                            PluginSourceReservationRow.company_id == row.company_id,
                            PluginSourceReservationRow.plugin_id == row.plugin_id,
                        )
                    )
                ).scalar_one_or_none()
                if reservation is not None:
                    if reservation.status == ModuleSourceReservationStatus.DISABLED.value:
                        raise ModuleSourceConflictError(
                            "a disabled reservation cannot complete provisioning"
                        )
                    reservation.status = ModuleSourceReservationStatus.READY.value
                    reservation.create_in_progress = False
                    reservation.updated_at = func.now()
                await session.flush()
                return self._to_domain(row)
        except ModuleSourceRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError(
                "could not complete ModuleSource provisioning"
            ) from exc

    async def delete(self, source_id: str) -> bool:
        """Удалить только provisioning; ready/disabled сохраняются как audit."""
        try:
            async with self._session_ctx() as session:
                row = await session.get(ModuleSourceRow, source_id)
                if row is None:
                    return False
                if row.status != ModuleSourceStatus.PROVISIONING.value:
                    raise ModuleSourceRepositoryError(
                        "only a provisioning ModuleSource may be deleted; disable it instead"
                    )
                await session.delete(row)
                await session.flush()
                return True
        except SQLAlchemyError as exc:
            raise ModuleSourceRepositoryError("could not delete ModuleSource") from exc

    @staticmethod
    def _reservation_values(
        reservation: ModuleSourceProvisioningReservation,
    ) -> dict[str, object]:
        return {
            "id": reservation.id,
            "company_id": reservation.company_id,
            "plugin_id": reservation.plugin_id,
            "cabinet_id": reservation.cabinet_id,
            "project_id": reservation.project_id,
            "provider": reservation.provider,
            "expected_full_name": reservation.full_name,
            "expected_private": reservation.private,
            "expected_default_branch": reservation.default_branch,
            "installation_credential_ref": reservation.installation_credential_ref,
            "trusted_workflow_ref": reservation.trusted_workflow_ref,
            "attempt_id": reservation.attempt_id,
            "create_in_progress": reservation.create_in_progress,
            "status": reservation.status.value,
            "created_at": reservation.created_at,
            "updated_at": reservation.updated_at,
        }

    @staticmethod
    async def _find_conflicting_reservation(
        session: AsyncSession,
        reservation: ModuleSourceProvisioningReservation,
    ) -> PluginSourceReservationRow:
        rows = list(
            (
                await session.execute(
                    select(PluginSourceReservationRow).where(
                        or_(
                            (
                                PluginSourceReservationRow.company_id
                                == reservation.company_id
                            )
                            & (
                                PluginSourceReservationRow.plugin_id
                                == reservation.plugin_id
                            ),
                            (
                                PluginSourceReservationRow.provider
                                == reservation.provider
                            )
                            & (
                                PluginSourceReservationRow.expected_full_name
                                == reservation.full_name
                            ),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != 1:
            raise ModuleSourceConflictError(
                "company/plugin or provider/full_name reservation identity is already bound"
            )
        return rows[0]

    @staticmethod
    def _to_reservation(
        row: PluginSourceReservationRow,
    ) -> ModuleSourceProvisioningReservation:
        try:
            return ModuleSourceProvisioningReservation(
                id=row.id,
                company_id=row.company_id,
                plugin_id=row.plugin_id,
                cabinet_id=row.cabinet_id,
                project_id=row.project_id,
                provider=row.provider,
                full_name=row.expected_full_name,
                private=row.expected_private,
                default_branch=row.expected_default_branch,
                installation_credential_ref=row.installation_credential_ref,
                trusted_workflow_ref=row.trusted_workflow_ref,
                attempt_id=row.attempt_id,
                create_in_progress=row.create_in_progress,
                status=ModuleSourceReservationStatus(row.status),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        except (TypeError, ValueError) as exc:
            raise ModuleSourceRepositoryError(
                "stored provisioning reservation is invalid; refusing to use it"
            ) from exc

    @staticmethod
    def _to_domain(row: ModuleSourceRow) -> ModuleSource:
        try:
            return ModuleSource(
                id=row.id,
                company_id=row.company_id,
                plugin_id=row.plugin_id,
                cabinet_id=row.cabinet_id,
                project_id=row.project_id,
                provider=row.provider,
                repo_id=row.repo_id,
                full_name=row.full_name,
                private=row.private,
                default_branch=row.default_branch,
                installation_credential_ref=row.installation_credential_ref,
                trusted_workflow_ref=row.trusted_workflow_ref,
                status=ModuleSourceStatus(row.status),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        except (TypeError, ValueError) as exc:
            raise ModuleSourceRepositoryError(
                "stored ModuleSource is invalid; refusing to use it"
            ) from exc
