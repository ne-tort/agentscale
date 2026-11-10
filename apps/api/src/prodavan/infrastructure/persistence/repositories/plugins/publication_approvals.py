"""SQL repository for durable plugin publication approvals.

The repository implements both the admin store and the CI read-only approval
port. It never reads or writes CapabilityGrant or ModuleSource. Exact-binding
insert is idempotent through the database UNIQUE constraint; consumption is an
atomic conditional UPDATE that preserves the first consumed_at timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.plugins.admin.contracts import (
    QUOTA_KEYS,
    EgressMode,
    EgressPolicy,
    PluginPublicationApproval,
    QuotaSet,
)
from prodavan.infrastructure.persistence.models.publication_approvals import (
    PluginPublicationApprovalRow,
)

from ._schema import (
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    aware_utc,
    json_mapping,
    string_tuple,
    text,
)

_BINDING_COLUMNS = ("company_id", "plugin_id", "manifest_version", "artifact_digest")


class SqlPluginPublicationApprovalRepository:
    """Durable exact approvals with idempotent create and atomic consumption."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        now: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                "SqlPluginPublicationApprovalRepository requires session_factory or session"
            )
        self._session_factory = session_factory
        self._session = session
        self._clock = now or (lambda: datetime.now(UTC))

    def _session_ctx(self):
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None
        return self._session_factory()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PluginRepositoryError("approval clock must return aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def to_record(row: PluginPublicationApprovalRow) -> PluginPublicationApproval:
        try:
            egress_raw = json_mapping(row.egress_policy, field="egress_policy")
            if set(egress_raw) - {
                "mode", "allowed_hosts", "allowed_paths", "approved_for_full"
            }:
                raise ValueError("unknown egress_policy keys")
            approved_for_full = egress_raw.get("approved_for_full", False)
            if not isinstance(approved_for_full, bool):
                raise ValueError("approved_for_full must be boolean")
            egress = EgressPolicy(
                mode=EgressMode(egress_raw.get("mode", "none")),
                allowed_hosts=string_tuple(
                    egress_raw.get("allowed_hosts", ()), field="allowed_hosts"
                ),
                allowed_paths=string_tuple(
                    egress_raw.get("allowed_paths", ()), field="allowed_paths"
                ),
                approved_for_full=approved_for_full,
            )
            quota_raw = json_mapping(row.quota, field="quota")
            if set(quota_raw) != set(QUOTA_KEYS):
                raise ValueError("quota must contain exactly the six effective limits")
            values: dict[str, int] = {}
            for name in QUOTA_KEYS:
                value = quota_raw[name]
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise ValueError(f"{name} must be an integer >= 1")
                values[name] = value
            quotas = QuotaSet(**values)
            created_at = aware_utc(row.created_at)
            consumed_at = aware_utc(row.consumed_at)
            if not isinstance(created_at, datetime):
                raise ValueError("created_at is missing")
            if consumed_at is not None and not isinstance(consumed_at, datetime):
                raise ValueError("consumed_at is not datetime")
            return PluginPublicationApproval(
                approval_id=text(row.id, field="approval_id"),
                company_id=text(row.company_id, field="company_id"),
                plugin_id=text(row.plugin_id, field="plugin_id"),
                manifest_version=text(row.manifest_version, field="manifest_version"),
                artifact_digest=text(row.artifact_digest, field="artifact_digest"),
                capabilities=string_tuple(row.capabilities, field="capabilities"),
                egress_policy=egress,
                quotas=quotas,
                created_by=text(row.created_by, field="created_by"),
                created_at=created_at,
                consumed_at=consumed_at,
            )
        except (PluginRepositoryError, TypeError, ValueError) as exc:
            raise PluginRepositorySchemaError(
                f"persisted publication approval is invalid: {exc}"
            ) from exc

    @staticmethod
    def _row_values(approval: PluginPublicationApproval) -> dict[str, Any]:
        egress = approval.egress_policy
        quotas = approval.quotas
        return {
            "id": approval.approval_id,
            "company_id": approval.company_id,
            "plugin_id": approval.plugin_id,
            "manifest_version": approval.manifest_version,
            "artifact_digest": approval.artifact_digest,
            "capabilities": list(approval.capabilities),
            "egress_policy": {
                "mode": egress.mode.value,
                "allowed_hosts": list(egress.allowed_hosts),
                "allowed_paths": list(egress.allowed_paths),
                "approved_for_full": egress.approved_for_full,
            },
            "quota": {name: getattr(quotas, name) for name in QUOTA_KEYS},
            "created_by": approval.created_by,
            "created_at": approval.created_at,
            "consumed_at": approval.consumed_at,
        }

    async def get(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
    ) -> PluginPublicationApproval | None:
        try:
            async with self._session_ctx() as session:
                result = await session.execute(
                    select(PluginPublicationApprovalRow).where(
                        PluginPublicationApprovalRow.company_id == company_id,
                        PluginPublicationApprovalRow.plugin_id == plugin_id,
                        PluginPublicationApprovalRow.manifest_version == manifest_version,
                        PluginPublicationApprovalRow.artifact_digest == artifact_digest,
                    )
                )
                row = result.scalars().first()
            return None if row is None else self.to_record(row)
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(f"get publication approval failed: {exc}") from exc

    async def create(
        self, *, approval: PluginPublicationApproval
    ) -> PluginPublicationApproval:
        if not isinstance(approval, PluginPublicationApproval):
            raise PluginRepositoryError("create expects PluginPublicationApproval")
        try:
            values = self._row_values(approval)
            async with self._session_ctx() as session:
                dialect = session.get_bind().dialect.name
                if dialect in {"postgresql", "sqlite"}:
                    if dialect == "postgresql":
                        from sqlalchemy.dialects.postgresql import insert as dialect_insert
                    else:
                        from sqlalchemy.dialects.sqlite import insert as dialect_insert
                    statement = (
                        dialect_insert(PluginPublicationApprovalRow)
                        .values(**values)
                        .on_conflict_do_nothing(index_elements=list(_BINDING_COLUMNS))
                    )
                    await session.execute(statement)
                else:
                    existing = (
                        await session.execute(
                            select(PluginPublicationApprovalRow).where(
                                *[
                                    getattr(PluginPublicationApprovalRow, name) == values[name]
                                    for name in _BINDING_COLUMNS
                                ]
                            )
                        )
                    ).scalars().first()
                    if existing is None:
                        session.add(PluginPublicationApprovalRow(**values))
                await _commit_if_owned(session, self._session is None)
                found = (
                    await session.execute(
                        select(PluginPublicationApprovalRow).where(
                            *[
                                getattr(PluginPublicationApprovalRow, name) == values[name]
                                for name in _BINDING_COLUMNS
                            ]
                        )
                    )
                ).scalars().first()
            if found is None:
                raise PluginRepositoryUnavailable(
                    "create publication approval returned no durable row"
                )
            return self.to_record(found)
        except IntegrityError as exc:
            raise PluginRepositoryUnavailable(
                "create publication approval violated database integrity"
            ) from exc
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(
                f"create publication approval failed: {exc}"
            ) from exc

    async def mark_consumed(
        self, *, approval_id: str, consumed_at: datetime
    ) -> PluginPublicationApproval | None:
        key = text(approval_id, field="approval_id")
        moment = aware_utc(consumed_at)
        if not isinstance(moment, datetime):
            raise PluginRepositoryError("consumed_at must be aware datetime")
        try:
            async with self._session_ctx() as session:
                # Atomic compare-and-set. A concurrent/repeated call does not
                # overwrite the first successful consumption timestamp.
                await session.execute(
                    update(PluginPublicationApprovalRow)
                    .where(
                        PluginPublicationApprovalRow.id == key,
                        PluginPublicationApprovalRow.consumed_at.is_(None),
                    )
                    .values(consumed_at=moment)
                )
                await _commit_if_owned(session, self._session is None)
                found = (
                    await session.execute(
                        select(PluginPublicationApprovalRow).where(
                            PluginPublicationApprovalRow.id == key
                        )
                    )
                ).scalars().first()
            return None if found is None else self.to_record(found)
        except (SQLAlchemyError, PluginRepositoryError) as exc:
            raise PluginRepositoryUnavailable(
                f"consume publication approval failed: {exc}"
            ) from exc


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


__all__ = ["SqlPluginPublicationApprovalRepository"]
