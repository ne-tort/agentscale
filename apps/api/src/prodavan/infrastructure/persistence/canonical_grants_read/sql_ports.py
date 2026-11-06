"""SQL implementations of the two *canonical* ports ``ShadowGatewayPorts`` needs.

Task ``MIG-M06-PORTS-01``.

``application/canonical_grants/ports.py`` declares ``AuthenticatedCompanyPort``
(``company_id`` -> ``tenant_id``) and ``CanonicalGrantReadPort`` (the exact grant
rows of one instance/version).  ``application/canonical_grants/service.py`` and
``m06_grants_gateway/canonical_decision_adapter.py`` consume them, but until this
module existed neither had an implementation in the tree, so the canonical half
of ``ShadowGatewayPorts`` could not be stated.

Tables read (all pre-existing, none invented)
---------------------------------------------
==========================================  ======================================
``dynamic_app_capability_grants``           the canonical grant rows
                                            (``models/canonical_grants.py``)
``companies``                               the tenant a company id resolves to
==========================================  ======================================

Nothing here writes, and no method opens a transaction of its own beyond the
read-only session it is handed by its factory.

Both ports resolve exactly, or not at all
-----------------------------------------
* ``resolve_tenant_id`` answers the company *id* of a live, non-deleted company
  whose status is ``active``.  It never answers a near miss: a soft-deleted or
  paused company is not a tenant any more, and returning its id would let a
  revoked company be compared equal to a live tenant context.  An empty answer is
  a refusal, which every caller already treats as "not authenticated".
* ``list_grants`` answers the rows of exactly one
  ``(tenant, app_version, plugin_instance)`` and filters ``app_id`` in SQL as
  well, because the caller states it and a row of another app must not even be
  read.  A row that cannot be decoded into the ``CanonicalGrant`` contract is a
  **refusal** (``CanonicalGrantReadRefused``), never a silently skipped row: a
  grant the reader dropped is indistinguishable from a grant that was never
  issued, and the shadow comparison would then report "grant missing" for a
  partition that actually holds malformed authority.

Why ``revoked`` rows are still returned
---------------------------------------
``CanonicalGrant.check`` decides ``revoked``/``expired``/``not_yet_issued``
itself.  Filtering them out here would move a policy decision into the adapter
and turn a revocation into "nothing there", which is exactly the rewrite the
facade's quarantine evidence exists to prevent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.canonical_grants.contracts import CanonicalGrant
from prodavan.infrastructure.persistence.models.canonical_grants import CanonicalGrantRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow

#: The only ``companies.status`` value a tenant binding may be issued for.
ACTIVE_COMPANY_STATUS: Final = "active"


class CanonicalGrantReadRefused(RuntimeError):
    """A stored row cannot be expressed as the contract the caller reads.

    Raised instead of skipping the row or returning a partial list.  Both
    consumers (``CanonicalGrantShadowAdapter.decide_shadow`` and
    ``CanonicalGrantGatewayService.authorize``) wrap any exception from the port
    into a quarantine/failed read, which is the correct answer for authority that
    exists in the database but cannot be proved.
    """

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _aware_utc(value: datetime | None) -> datetime | None:
    """Interpret a naive timestamp as UTC (the SQLite mirror drops ``tzinfo``)."""

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise CanonicalGrantReadRefused(f"canonical_grant_{field}_not_a_non_empty_array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CanonicalGrantReadRefused(f"canonical_grant_{field}_entry_invalid")
        result.append(item)
    return tuple(result)


class SqlAuthenticatedCompanyPort:
    """``AuthenticatedCompanyPort``: a live company id, or ``None``.

    The port is deliberately narrow.  It answers *only* "is this company a tenant
    right now", because every other question (is the subject a member, does the
    scope belong to the tenant) is answered by the M04/M05 ports, and a second,
    divergent notion of tenant authority would be worse than none.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve_tenant_id(self, *, company_id: str) -> str | None:
        if not isinstance(company_id, str) or not company_id.strip():
            return None
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(CompanyRow.id, CompanyRow.status, CompanyRow.deleted_at).where(
                        CompanyRow.id == company_id
                    )
                )
            ).first()
        if row is None:
            return None
        resolved_id, status, deleted_at = row
        if deleted_at is not None or str(status) != ACTIVE_COMPANY_STATUS:
            return None
        resolved = str(resolved_id)
        return resolved if resolved.strip() else None


class SqlCanonicalGrantReadPort:
    """``CanonicalGrantReadPort`` over ``dynamic_app_capability_grants``."""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_grants(
        self,
        *,
        tenant_id: str,
        app_id: str,
        app_version_id: str,
        plugin_instance_id: str,
    ) -> tuple[CanonicalGrant, ...]:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (tenant_id, app_id, app_version_id, plugin_instance_id)
        ):
            return ()
        statement = (
            select(CanonicalGrantRow)
            .where(
                CanonicalGrantRow.tenant_id == tenant_id,
                CanonicalGrantRow.app_id == app_id,
                CanonicalGrantRow.app_version_id == app_version_id,
                CanonicalGrantRow.plugin_instance_id == plugin_instance_id,
            )
            .order_by(CanonicalGrantRow.id)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return tuple(self._grant(row) for row in rows)

    # -- row -> contract ---------------------------------------------------

    @staticmethod
    def _grant(row: CanonicalGrantRow) -> CanonicalGrant:
        """One row as the decision contract, or a refusal.

        ``CanonicalGrant.__post_init__`` is strict on purpose (sha256 artifact
        digest, timezone-aware timestamps, non-empty non-wildcard sets).  Its
        ``ValueError`` is converted into a named read refusal so a consumer cannot
        mistake "this row is undecodable" for "this row allows".
        """

        try:
            return CanonicalGrant(
                grant_id=str(row.id),
                tenant_id=str(row.tenant_id),
                app_definition_id=str(row.app_definition_id),
                app_id=str(row.app_id),
                app_version_id=str(row.app_version_id),
                app_artifact_digest=str(row.app_artifact_digest),
                scope_kind=str(row.scope_kind),
                scope_id=str(row.scope_id),
                plugin_instance_id=str(row.plugin_instance_id or ""),
                subject_id=str(row.subject_id),
                session_id=str(row.session_id or ""),
                capability_key=str(row.capability_key),
                operations=frozenset(_projects(row.operations, field="operations")),
                resources=frozenset(_projects(row.resources, field="resources")),
                domains=frozenset(_projects(row.domains, field="domains")),
                constraints=_constraints(row.constraints),
                issued_at=_required_instant(row.issued_at, field="issued_at"),
                expires_at=_aware_utc(row.expires_at),
                revoked_at=_aware_utc(row.revoked_at),
                issued_by=str(row.issued_by or ""),
            )
        except ValueError as exc:
            raise CanonicalGrantReadRefused(
                f"canonical_grant_row_undecodable:{_safe(exc)}"
            ) from exc


def _projects(value: Any, *, field: str) -> tuple[str, ...]:
    return _string_tuple(value, field=field)


def _constraints(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    raise CanonicalGrantReadRefused("canonical_grant_constraints_not_an_object")


def _required_instant(value: datetime | None, *, field: str) -> datetime:
    aware = _aware_utc(value)
    if aware is None:
        raise CanonicalGrantReadRefused(f"canonical_grant_{field}_missing")
    return aware


def _safe(exc: ValueError) -> str:
    """The contract's message names the field; keep only a bounded token."""

    text = str(exc)
    cleaned = "".join(character if character.isalnum() or character == "_" else "_" for character in text)
    return cleaned[:80] or "invalid"


__all__ = [
    "ACTIVE_COMPANY_STATUS",
    "CanonicalGrantReadRefused",
    "SqlAuthenticatedCompanyPort",
    "SqlCanonicalGrantReadPort",
]
